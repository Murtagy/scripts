import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

import db
from mission_names import (
    MissionName,
    MissionScheduleError,
    fetch_mission_schedule,
    heading_date,
    parse_mission_schedule,
)
from plan_content import PlanSummary
from plan_monitor import PlanStore


def article(published: str, body: str, category: str = "bg-missions") -> str:
    return f'<div class="panel panel-default panel-news"><span class="{category}"></span><span>{published}, 12:00</span><div>{body}</div></div>'


class MissionScheduleTests(unittest.TestCase):
    def test_dates_order_names_and_links_from_public_announcements(self) -> None:
        html = article("04.11.2026", '''
<h4>Пятница, 06 ноября</h4>
<ul><li><a href="http://tsgames.ru/missions/skt_Autumn_2008">Осень <b>2008</b></a></li>
<li><a href="/user/profile/Skaut">Автор</a></li></ul>
<ul><li><a href="/missions/te_Threat_Deactivation">Деактивация угрозы</a></li></ul>
<h4>Суббота, 07 ноября</h4>
<a href="/missions/Lpt_WinterTale">Зимняя сказка</a>
<a href="/missions/NL_Tide">Прилив &amp; отлив</a>
<h4>Резерв</h4><a href="/missions/Reserve">Резервная</a>
<a href="/missions/list/schedule">График игр</a>''')
        schedule = parse_mission_schedule(html)
        self.assertEqual(len(schedule), 4)
        self.assertEqual(schedule[(date(2026, 11, 6), 0)], MissionName("Осень 2008", "https://tsgames.ru/missions/skt_Autumn_2008"))
        self.assertEqual(schedule[(date(2026, 11, 6), 1)].name, "Деактивация угрозы")
        self.assertEqual(schedule[(date(2026, 11, 7), 1)].name, "Прилив & отлив")

    def test_latest_article_wins_for_same_game(self) -> None:
        latest = article("05.11.2026", '<h4>Пятница, 06 ноября</h4><a href="/missions/New">Новая</a>')
        old = article("04.11.2026", '<h4>Пятница, 06 ноября</h4><a href="/missions/Old">Старая</a>')
        self.assertEqual(parse_mission_schedule(latest + old)[(date(2026, 11, 6), 0)].name, "Новая")

    def test_year_comes_from_publication_including_new_year_boundary(self) -> None:
        html = article("30.12.2026", '<h4>Суббота, 02 января</h4><a href="/missions/NewYear">Новый год</a>')
        self.assertEqual(set(parse_mission_schedule(html)), {(date(2027, 1, 2), 0)})
        self.assertIsNone(heading_date("31 февраля", date(2026, 2, 25)))
        self.assertIsNone(heading_date("06 ноября", None))
        self.assertIsNone(heading_date("06 ноября", date(2026, 4, 1)))

    def test_other_articles_untrusted_links_and_missing_publication_are_ignored(self) -> None:
        body = '<h4>Пятница, 06 ноября</h4><a href="/missions/NotScheduled">Другая новость</a>'
        self.assertEqual(parse_mission_schedule(article("04.11.2026", body, "bg-updates")), {})
        self.assertEqual(parse_mission_schedule(body), {})
        self.assertEqual(parse_mission_schedule(article("", body)), {})
        links = '''<h4>Пятница, 06 ноября</h4>
<a href="https://evil.test/missions/Bad">Чужая</a>
<a href="https://[broken">Сломанная</a>
<a href="/missions/list/schedule">График</a>
<a href="/missions/test/Good">Тестовая</a>'''
        self.assertEqual(parse_mission_schedule(article("04.11.2026", links)), {
            (date(2026, 11, 6), 0): MissionName("Тестовая", "https://tsgames.ru/missions/test/Good"),
        })

    def test_named_topic_fits_telegram_limit(self) -> None:
        summary = PlanSummary(reference=6042, mission_id=946, event_datetime=date(2026, 11, 6), event_position=0)
        self.assertEqual(summary.topic_name(MissionName("Осень 2008", "https://tsgames.ru/missions/skt_Autumn_2008")), "11-06-пт1-Осень 2008")
        long = summary.topic_name(MissionName("🪖" * 200, "https://tsgames.ru/missions/Long"))
        self.assertLessEqual(len(long.encode("utf-16-le")) // 2, 128)

    def test_topic_game_labels_follow_the_event_weekday_and_order(self) -> None:
        mission = MissionName("Миссия", "https://tsgames.ru/missions/Test")
        for day, position, label in ((13, 0, "пт1"), (13, 1, "пт2"), (14, 0, "сб1"), (14, 1, "сб2")):
            with self.subTest(day=day, position=position):
                summary = PlanSummary(reference=1, mission_id=946, event_datetime=date(2026, 11, day), event_position=position)
                self.assertEqual(summary.topic_name(mission), f"11-{day:02d}-{label}-Миссия")
                self.assertEqual(summary.topic_name(), f"11-{day:02d}-{label}-mission-946")

    def test_name_cache_uses_newest_game_and_survives_empty_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.object(db, "DB_PATH", str(Path(directory) / "test.sqlite3")):
            db.init_db()
            newest = PlanSummary(reference=6042, mission_id=946, event_datetime=date(2026, 11, 6), event_position=0)
            older = newest.model_copy(update={"reference": 6008, "event_datetime": date(2026, 10, 23)})
            mission = MissionName("Осень 2008", "https://tsgames.ru/missions/skt_Autumn_2008")
            store = PlanStore()
            store.save_mission_names((newest, older), {
                (older.event_datetime, 0): MissionName("Старое имя", mission.url),
                (newest.event_datetime, 0): mission,
            })
            store.save_mission_names((newest,), {})
            self.assertEqual(PlanStore().mission(946), mission)
            self.assertIsNone(store.mission(12345))


class MissionScheduleFetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_request_has_no_access_token(self) -> None:
        response = httpx.Response(200, text=article("04.11.2026", '<h4>06 ноября</h4><a href="/missions/Test">Тест</a>'))
        with patch.object(httpx.AsyncClient, "get", new=AsyncMock(return_value=response)) as get:
            schedule = await fetch_mission_schedule()
        get.assert_awaited_once_with("https://tsgames.ru/")
        self.assertEqual(len(schedule), 1)

    async def test_redirect_and_empty_page_fail_without_fake_names(self) -> None:
        for response in (httpx.Response(302, headers={"location": "/user/login"}), httpx.Response(200, text="no schedule")):
            with self.subTest(status=response.status_code), patch.object(httpx.AsyncClient, "get", new=AsyncMock(return_value=response)), self.assertRaises(MissionScheduleError):
                await fetch_mission_schedule()
