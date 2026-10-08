import datetime
import os
import unittest
from unittest.mock import Mock, patch

with patch.dict(os.environ, {"BOT_TOKEN": "123456:test", "CHAT_ID": "-100123", "AI_KEY": ""}):
    import tg_bot


class WeekJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.enterContext(patch.object(tg_bot, "init_db"))
        self.enterContext(patch.object(tg_bot.slots_service, "normalize_competitions", return_value=0))
        self.enterContext(patch.object(tg_bot.slots_service, "create_or_get_active_week", return_value={"id": 1}))
        self.get_message = self.enterContext(patch.object(
            tg_bot.slots_service, "get_control_message", return_value={"message_id": 42},
        ))
        self.close_items = self.enterContext(patch.object(
            tg_bot.slots_service, "auto_close_open_items", return_value=[],
        ))
        self.refresh = self.enterContext(patch.object(tg_bot.week_control, "refresh_week_control_sync"))
        self.enterContext(patch.object(tg_bot, "start_web_server"))
        now = datetime.datetime(2026, 10, 8, 10, 0, tzinfo=tg_bot.TZ)
        clock = self.enterContext(patch.object(tg_bot.datetime, "datetime"))
        clock.now.return_value = now
        self.enterContext(patch.object(tg_bot, "get_scheduled_time", return_value=10))
        self.application = Mock()
        builder = self.enterContext(patch.object(tg_bot.Application, "builder"))
        builder.return_value.token.return_value.build.return_value = self.application

    def test_unchanged_restart_does_not_refresh_message(self) -> None:
        tg_bot.main()
        self.refresh.assert_not_called()

    def test_startup_creates_missing_message(self) -> None:
        self.get_message.return_value = None
        tg_bot.main()
        self.refresh.assert_called_once_with()

    def test_startup_refreshes_closed_items(self) -> None:
        self.close_items.return_value = [{"id": 10}]
        tg_bot.main()
        self.refresh.assert_called_once_with()

    def test_no_periodic_week_refresh(self) -> None:
        tg_bot.main()
        jobs = self.application.job_queue.run_repeating.call_args_list
        job_names = [job.args[0].__name__ for job in jobs]
        self.assertEqual(job_names, ["create_poll", "close_slots_job", "init_week_job", "report_frags"])
