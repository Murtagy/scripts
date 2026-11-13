import unittest
from datetime import date

from plan_content import (
    PlanDetail,
    PlanSummary,
    der_fragments,
    paragraphs,
    telegram_chunks,
    upcoming_plans,
)


class PlanContentTests(unittest.TestCase):
    def test_nested_der_sections_and_shared_logistics_keep_only_matching_entries(self) -> None:
        content = """@@@ Логистика
DER - забрать машину.
И медика.
TOR - забрать ящик.
GL - принять РПГ у DER.
TT - держать город.
@@@
@@@ Отрядам
@@@ DER
Держать метку.
Pandur - на восток.
@@@
@@@ TOR
Не для нас.
@@@
@@@
ORDER не является упоминанием.
"""
        fragments = der_fragments(content)
        self.assertEqual(len(fragments), 2)
        self.assertIn("Логистика\nDER - забрать машину.\nИ медика.", fragments[0].render())
        self.assertIn("GL - принять РПГ у DER.", fragments[0].text)
        self.assertNotIn("TOR", fragments[0].text)
        self.assertNotIn("TT", fragments[0].text)
        self.assertEqual(fragments[1].render(), "Отрядам-DER\nДержать метку.\nPandur - на восток.")

    def test_plain_squad_lines_are_filtered_and_cyrillic_der_is_recognized(self) -> None:
        fragment, = der_fragments("@@@ Город\nTOR наблюдает.\nDER выдвигается.\nTT прикрывает.\n@@@")
        self.assertEqual(fragment.render(), "Город\nDER выдвигается.")
        self.assertEqual(der_fragments("ДЕР - держать город.")[0].text, "ДЕР - держать город.")

    def test_images_do_not_pollute_plan_text(self) -> None:
        fragment, = der_fragments('@@@ DER\nДержать метку.\n![](https://img.tsgames.ru/a.png "title")\n<img src="https://img.tsgames.ru/b.png">\n@@@')
        self.assertEqual(fragment.text, "Держать метку.")
        self.assertEqual(der_fragments("@@@ DER\n![](https://img.tsgames.ru/only-image.png)\n@@@"), ())

    def test_new_wire_schema_and_uniform_full_resolution_urls(self) -> None:
        detail = PlanDetail.model_validate({
            "author_squad": "DER", "author_username": "A", "event_datetime": "2026-11-14",
            "event_position": 0, "is_reserve": False, "reversed": True, "content": "DER: задача",
            "slots": [{"side_id": 2, "side_name": "Чехия", "side_base_name": "red", "side_color": "red",
                       "squads": [{"callsign": "Alpha-1-2", "units": ["Командир", "Стрелок"]}]}],
            "screenshots": {
                "ally": [{"image": "https://tsgames.ru/ally.jpg", "thumb": "https://tsgames.ru/ally_thumb.jpg", "title": "Красные", "size": "765x510"}],
                "enemy": [{"image": "https://tsgames.ru/enemy.jpg", "thumb": "https://tsgames.ru/enemy_thumb.jpg", "title": "Синие"}],
            },
            "comments": [{"create_datetime": "malformed and irrelevant", "content": "Do not forward"}],
        })
        self.assertNotIn("slots", detail.model_dump())
        media = detail.media_notices()
        self.assertEqual([item.image_url for item in media], ["https://tsgames.ru/ally.jpg", "https://tsgames.ru/enemy.jpg"])
        self.assertTrue(media[0].text.startswith("Наша форма:\nКрасные"))
        self.assertTrue(media[1].text.startswith("Противник:\nСиние"))
        self.assertEqual([item.album_key for item in media], ["ally", "enemy"])
        self.assertNotIn("thumb", "\n".join(item.text for item in media))
        # API already resolves ally/enemy: reversed must not invert them again.
        self.assertEqual(detail.model_copy(update={"reversed": False}).media_notices(), media)

    def test_unused_slots_and_empty_uniform_shapes_cannot_block_plan(self) -> None:
        for slots in ([{"callsign": "Alpha-1-2", "units": [None]}], None, [{"new_slot_schema": True}]):
            for screenshots in (None, [], {"ally": None, "enemy": []}):
                detail = PlanDetail.model_validate({
                    "author_squad": "DER", "author_username": "A", "event_datetime": "2026-11-14", "event_position": 0,
                    "is_reserve": False, "reversed": False, "content": "DER - задача", "slots": slots, "screenshots": screenshots,
                })
                self.assertEqual(detail.media_notices(), ())
                self.assertNotIn("slots", detail.model_dump())

    def test_nullable_uniform_title_is_optional(self) -> None:
        detail = PlanDetail.model_validate({
            "author_squad": "DER", "author_username": "A", "event_datetime": "2026-11-14", "event_position": 0,
            "is_reserve": False, "reversed": False, "content": "", "screenshots": {"ally": [{"image": "https://tsgames.ru/a.jpg", "title": None}]},
        })
        self.assertTrue(detail.media_notices()[0].text.startswith("Наша форма:\nhttps://"))

    def test_plan_uses_only_der_fragments_and_explicit_empty_state(self) -> None:
        detail = PlanDetail(author_squad="DER", author_username="A", event_datetime=date(2026, 11, 14), event_position=0, is_reserve=False, reversed=False, content="TOR - не для нас")
        summary = PlanSummary(reference=1, mission_id=1421, event_datetime=detail.event_datetime, event_position=0)
        self.assertIn("Фрагменты DER пока не опубликованы.", detail.der_message(summary))
        self.assertNotIn("TOR", detail.der_message(summary))
        changed = detail.model_copy(update={"content": "@@@ DER\nДержать метку.\n@@@\n@@@ TOR\nДругой отряд.\n@@@"})
        self.assertIn("DER\nДержать метку.", changed.der_message(summary))
        self.assertNotIn("Другой отряд", changed.der_message(summary))

    def test_uniform_and_plan_duplicate_url_produces_one_labelled_image(self) -> None:
        detail = PlanDetail.model_validate({
            "author_squad": "DER", "author_username": "A", "event_datetime": "2026-11-14", "event_position": 0,
            "is_reserve": False, "reversed": False, "content": "![](https://tsgames.ru/same.jpg)",
            "screenshots": {"ally": [{"image": "https://tsgames.ru/same.jpg"}]},
        })
        notice, = detail.media_notices()
        self.assertTrue(notice.text.startswith("Наша форма:"))

    def test_chunks_respect_utf16_limit(self) -> None:
        text = "План 🪖 " * 1500
        chunks = telegram_chunks(text)
        self.assertTrue(all(len(chunk.encode("utf-16-le")) // 2 <= 4000 for chunk in chunks))
        self.assertEqual("".join(chunks), text.strip())

    def test_only_four_nearest_future_games_this_or_next_week(self) -> None:
        plans = tuple(PlanSummary(reference=index, mission_id=946, event_datetime=date(2026, 11, day), event_position=position)
                      for index, (day, position) in enumerate(((20, 0), (14, 1), (14, 0), (13, 1), (13, 0), (7, 1), (7, 0), (6, 1), (6, 0), (3, 0)), 1))
        selected = upcoming_plans(plans, date(2026, 11, 4))
        self.assertEqual([(item.event_datetime.day, item.event_position) for item in selected], [(6, 0), (6, 1), (7, 0), (7, 1)])
        saturday = upcoming_plans(plans, date(2026, 11, 7))
        self.assertEqual([(item.event_datetime.day, item.event_position) for item in saturday], [(7, 0), (7, 1), (13, 0), (13, 1)])

    def test_multiple_plans_for_one_game_are_all_selected(self) -> None:
        first = PlanSummary(reference=10, mission_id=946, event_datetime=date(2026, 11, 6), event_position=0)
        second = first.model_copy(update={"reference": 11})
        self.assertEqual(upcoming_plans((first, second), date(2026, 11, 4)), (second, first))

    def test_paragraph_paths_and_line_ending_normalization(self) -> None:
        parsed = paragraphs("@@@ План\r\n@@@ DER\r\nОдин  \r\n@@@\r\nДва\r\n@@@\r\nТри")
        self.assertEqual([item.path for item in parsed], [("План", "DER"), ("План",), ()])
        self.assertEqual(parsed[0].text, "Один")
