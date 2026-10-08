import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram import Bot
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TelegramError, TimedOut

import week_control


class WeekControlTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.contexts = ExitStack()
        self.addCleanup(self.contexts.close)
        self.week = {"id": 1, "week_key": "2026-W41", "slots": []}
        self.bot = AsyncMock(spec=Bot)
        self.bot.send_message.return_value = SimpleNamespace(message_id=99)
        self.contexts.enter_context(patch.object(week_control, "CHAT_ID", "-100123"))
        self.create_week = self.contexts.enter_context(patch.object(
            week_control.slots_service, "create_or_get_active_week", return_value=self.week,
        ))
        self.reset_week = self.contexts.enter_context(patch.object(
            week_control.slots_service, "reset_active_week", return_value=self.week,
        ))
        self.get_message = self.contexts.enter_context(patch.object(
            week_control.slots_service, "get_control_message",
            return_value={"chat_id": "-100123", "message_id": 42},
        ))
        self.save_message = self.contexts.enter_context(patch.object(
            week_control.slots_service, "save_control_message",
        ))

    async def test_successful_edit_keeps_existing_message(self) -> None:
        await week_control.upsert_week_control_message(self.bot)
        self.bot.edit_message_text.assert_awaited_once()
        self.bot.send_message.assert_not_awaited()
        self.save_message.assert_not_called()

    async def test_unchanged_message_needs_no_replacement(self) -> None:
        self.bot.edit_message_text.side_effect = BadRequest("Message is not modified")
        await week_control.upsert_week_control_message(self.bot)
        self.bot.send_message.assert_not_awaited()
        self.save_message.assert_not_called()

    async def test_edit_failures_never_create_duplicates(self) -> None:
        errors = (
            TimedOut(), NetworkError("Connection lost"), RetryAfter(30),
            Forbidden("Not enough rights"), BadRequest("Message can't be edited"),
            BadRequest("Message is too long"), TelegramError("Unknown failure"),
        )
        for error in errors:
            with self.subTest(error=error):
                self.bot.edit_message_text.side_effect = error
                for _ in range(2):
                    with self.assertRaises(TelegramError):
                        await week_control.upsert_week_control_message(self.bot)
                self.bot.send_message.assert_not_awaited()
                self.save_message.assert_not_called()

    async def test_explicitly_missing_message_is_replaced(self) -> None:
        self.bot.edit_message_text.side_effect = BadRequest("Message to edit not found")
        await week_control.upsert_week_control_message(self.bot)
        self.bot.send_message.assert_awaited_once()
        self.save_message.assert_called_once_with(1, "-100123", week_control.THREAD_ID, 99)

    async def test_first_message_is_created(self) -> None:
        self.get_message.return_value = None
        await week_control.upsert_week_control_message(self.bot)
        self.bot.edit_message_text.assert_not_awaited()
        self.bot.send_message.assert_awaited_once()
        self.save_message.assert_called_once_with(1, "-100123", week_control.THREAD_ID, 99)

    async def test_explicit_reset_creates_new_message(self) -> None:
        self.get_message.return_value = None
        await week_control.upsert_week_control_message(self.bot, force_new=True)
        self.reset_week.assert_called_once_with()
        self.create_week.assert_not_called()
        self.bot.send_message.assert_awaited_once()
