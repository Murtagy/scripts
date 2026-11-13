import json
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta
from email import policy
from email.parser import BytesParser
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import httpx
from telegram import Bot
from telegram.error import BadRequest, Forbidden, RetryAfter, TimedOut
from telegram.request import HTTPXRequest

import db
from mission_names import MissionName, MissionScheduleError
from plan_content import (
    Headquarters,
    PlanDetail,
    PlanSummary,
    UniformImage,
    Uniforms,
    digest,
)
from plan_monitor import (
    HeadquartersAPI,
    HeadquartersError,
    ImageDownloadError,
    PlanMonitor,
    PlanStore,
    Topic,
)


class PlanMonitorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        db_path = patch.object(db, "DB_PATH", str(Path(directory.name) / "test.sqlite3"))
        db_path.start()
        self.addCleanup(db_path.stop)
        db.init_db()
        self.now = datetime(2026, 11, 13, 12, tzinfo=ZoneInfo("Europe/Moscow"))
        self.summary = PlanSummary(reference=6057, mission_id=1421, event_datetime=date(2026, 11, 14), event_position=0)
        self.detail = PlanDetail(author_squad="DER", author_username="A", event_datetime=self.summary.event_datetime, event_position=0, is_reserve=False, reversed=True, content="@@@ DER\nДержать метку.\n@@@")
        self.api = AsyncMock(spec=HeadquartersAPI)
        self.api.headquarters.return_value = Headquarters(plans=(self.summary,))
        self.api.plan.return_value = self.detail
        schedule = patch("plan_monitor.fetch_mission_schedule", new=AsyncMock(return_value={}))
        self.schedule = schedule.start()
        self.addCleanup(schedule.stop)
        download = patch("plan_monitor.download_image", new=AsyncMock(return_value=b"image"))
        self.download = download.start()
        self.addCleanup(download.stop)
        self.bot = AsyncMock(spec=Bot)
        self.bot.create_forum_topic.return_value = SimpleNamespace(message_thread_id=101)
        self.sequence = 200
        for method in (self.bot.send_message, self.bot.send_photo, self.bot.send_document):
            method.side_effect = self.sent_message
        self.bot.send_media_group.side_effect = self.sent_album
        self.store = PlanStore()
        self.monitor = PlanMonitor(self.api, self.store, "chat", lambda: self.now)

    def sent_message(self, *args: object, **kwargs: object) -> SimpleNamespace:
        self.sequence += 1
        return SimpleNamespace(message_id=self.sequence)

    def sent_album(self, **kwargs: object) -> tuple[SimpleNamespace, ...]:
        media = kwargs["media"]
        if not isinstance(media, list):
            raise TypeError("Expected album media list")
        group_id = f"album:{self.sequence + 1}"
        return tuple(SimpleNamespace(message_id=self.sent_message().message_id, media_group_id=group_id) for _ in media)

    async def tick(self, seconds: int = 90) -> None:
        self.now += timedelta(seconds=seconds)
        await self.monitor.poll(self.bot)

    async def start(self) -> None:
        await self.tick()
        await self.tick()

    async def test_one_plan_is_edited_not_reposted_across_restart_and_reverts(self) -> None:
        await self.start()
        first_id = self.sequence
        changed = self.detail.model_copy(update={"content": "@@@ DER\nДругая задача.\n@@@"})
        self.api.plan.return_value = changed
        await self.tick()
        self.bot.send_message.assert_awaited_once()
        self.assertEqual(self.bot.edit_message_text.await_args.kwargs["message_id"], first_id)
        self.assertIn("Другая задача", self.bot.edit_message_text.await_args.kwargs["text"])
        self.monitor = PlanMonitor(self.api, PlanStore(), "chat", lambda: self.now)
        await self.tick()
        await self.tick()
        self.assertEqual(self.bot.edit_message_text.await_count, 1)
        self.api.plan.return_value = self.detail
        await self.tick()
        self.assertEqual(self.bot.edit_message_text.await_count, 2)
        self.bot.send_message.assert_awaited_once()

    async def test_comments_non_der_changes_and_slots_do_not_create_notifications(self) -> None:
        await self.start()
        body = self.detail.model_dump()
        body["content"] += "\n@@@ TOR\nСовсем другой план.\n@@@"
        body["comments"] = [{"content": "Комментарий v1", "create_datetime": "irrelevant"}]
        self.api.plan.return_value = PlanDetail.model_validate(body)
        await self.tick()
        body["comments"] = [{"content": "Комментарий v2"}]
        self.api.plan.return_value = PlanDetail.model_validate(body)
        await self.tick()
        self.bot.send_message.assert_awaited_once()
        self.bot.edit_message_text.assert_not_awaited()
        self.bot.send_photo.assert_not_awaited()

    async def test_pending_legacy_comments_roles_and_notifications_are_never_sent(self) -> None:
        with db.get_conn() as conn:
            conn.executemany("INSERT INTO plan_deliveries (chat_id,reference,kind,identity,text) VALUES ('chat',6057,?,'old','SPAM')", ((kind,) for kind in ("initial", "comment", "roles", "der", "arrangement")))
            conn.commit()
        await self.start()
        self.bot.send_message.assert_awaited_once()
        self.assertNotIn("SPAM", self.bot.send_message.await_args.kwargs["text"])

    async def test_transient_edit_failure_retries_same_id_without_new_message(self) -> None:
        await self.start()
        self.api.plan.return_value = self.detail.model_copy(update={"content": "DER - новая задача"})
        self.bot.edit_message_text.side_effect = TimedOut()
        await self.tick()
        self.assertTrue(self.store.pending("chat", 6057))
        self.bot.send_message.assert_awaited_once()
        self.bot.edit_message_text.side_effect = BadRequest("Message is not modified")
        await self.tick()
        self.assertEqual(self.store.pending("chat", 6057), ())
        self.bot.send_message.assert_awaited_once()

    async def test_only_explicitly_deleted_message_is_replaced(self) -> None:
        await self.start()
        self.api.plan.return_value = self.detail.model_copy(update={"content": "DER - новая задача"})
        self.bot.edit_message_text.side_effect = Forbidden("Cannot edit")
        await self.tick()
        self.bot.send_message.assert_awaited_once()
        self.bot.edit_message_text.side_effect = BadRequest("Message to edit not found")
        await self.tick()
        self.bot.edit_message_text.side_effect = None
        await self.tick()
        self.assertEqual(self.bot.send_message.await_count, 2)

    async def test_long_plan_promotes_same_message_to_document_and_keeps_full_text(self) -> None:
        await self.start()
        first_id = self.sequence
        self.api.plan.return_value = self.detail.model_copy(update={"content": "@@@ DER\n" + "🪖 задача " * 1000 + "\n@@@"})
        await self.tick()
        media = self.bot.edit_message_media.await_args.kwargs["media"]
        self.assertIn("🪖 задача ".encode(), media.media.input_file_content)
        self.assertEqual(self.bot.edit_message_media.await_args.kwargs["message_id"], first_id)
        self.bot.send_message.assert_awaited_once()
        self.bot.send_document.assert_not_awaited()
        self.api.plan.return_value = self.detail
        await self.tick()
        self.assertEqual(self.bot.edit_message_media.await_count, 2)
        self.bot.send_message.assert_awaited_once()

    async def test_promotion_timeout_then_not_modified_records_document_across_restart(self) -> None:
        await self.start()
        self.api.plan.return_value = self.detail.model_copy(update={"content": "DER - " + "Длинная задача " * 1000})
        self.bot.edit_message_media.side_effect = TimedOut()
        await self.tick()
        self.assertEqual(self.store.pending("chat", 6057)[0].media_type, "plan_document")
        self.monitor = PlanMonitor(self.api, PlanStore(), "chat", lambda: self.now)
        self.bot.edit_message_media.side_effect = BadRequest("Message is not modified")
        await self.tick()
        self.assertEqual(self.store.pending("chat", 6057), ())
        self.api.plan.return_value = self.detail
        self.bot.edit_message_media.side_effect = None
        await self.tick()
        self.assertEqual(self.bot.edit_message_media.await_count, 3)
        self.bot.edit_message_text.assert_not_awaited()
        self.bot.send_message.assert_awaited_once()

    async def test_promotion_timeout_then_immediate_revert_still_updates_same_document(self) -> None:
        await self.start()
        self.api.plan.return_value = self.detail.model_copy(update={"content": "DER - " + "Длинная задача " * 1000})
        self.bot.edit_message_media.side_effect = TimedOut()
        await self.tick()
        self.store.enqueue("chat", self.summary, self.detail)
        self.assertTrue(self.store.pending("chat", 6057))
        self.api.plan.return_value = self.detail
        self.bot.edit_message_media.side_effect = None
        await self.tick()
        self.assertEqual(self.bot.edit_message_media.await_count, 2)
        self.bot.edit_message_text.assert_not_awaited()
        self.bot.send_message.assert_awaited_once()

    async def test_ambiguous_text_edit_then_revert_does_not_skip_required_edit(self) -> None:
        await self.start()
        self.api.plan.return_value = self.detail.model_copy(update={"content": "DER - другая задача"})
        self.bot.edit_message_text.side_effect = TimedOut()
        await self.tick()
        self.store.enqueue("chat", self.summary, self.detail)
        self.api.plan.return_value = self.detail
        self.bot.edit_message_text.side_effect = None
        await self.tick()
        self.assertEqual(self.bot.edit_message_text.await_count, 2)
        self.bot.send_message.assert_awaited_once()
        self.assertEqual(self.store.pending("chat", 6057), ())

    async def test_all_plan_images_and_uniforms_are_uploaded_separately_once(self) -> None:
        screenshots = Uniforms(ally=(UniformImage(image="https://tsgames.ru/ally.jpg", title="Красные"),), enemy=(UniformImage(image="https://tsgames.ru/enemy.jpg", title="Синие"),))
        self.api.plan.return_value = self.detail.model_copy(update={"content": self.detail.content + "\n@@@ TOR\n![](https://img.tsgames.ru/other-map.png)\n@@@", "screenshots": screenshots})
        await self.start()
        self.assertEqual(self.bot.send_photo.await_count, 3)
        captions = [call.kwargs["caption"] for call in self.bot.send_photo.await_args_list]
        self.assertTrue(any(text.startswith("Наша форма:") for text in captions))
        self.assertTrue(any(text.startswith("Противник:") for text in captions))
        self.assertTrue(any("TOR" in text for text in captions))
        self.download.assert_any_await("https://tsgames.ru/ally.jpg")
        self.assertIsNotNone(self.bot.send_photo.await_args.kwargs["photo"].input_file_content)
        await self.tick()
        self.assertEqual(self.bot.send_photo.await_count, 3)
        self.bot.send_message.assert_awaited_once()
        self.bot.edit_message_text.assert_not_awaited()

    async def test_real_sdk_multipart_media_references_resolve_to_uploaded_files(self) -> None:
        observed: list[str] = []
        sequence = 1000

        def message() -> dict[str, object]:
            nonlocal sequence
            sequence += 1
            return {"message_id": sequence, "date": 0, "chat": {"id": -100123, "type": "supergroup"}}

        def handle(request: httpx.Request) -> httpx.Response:
            method = request.url.path.rsplit("/", 1)[-1]
            if method == "getMe":
                return httpx.Response(200, json={"ok": True, "result": {"id": 123, "is_bot": True, "first_name": "Test", "username": "test_bot"}})
            if method == "createForumTopic":
                return httpx.Response(200, json={"ok": True, "result": {"message_thread_id": 101, "name": "Test", "icon_color": 7322096}})
            if method == "sendMessage":
                return httpx.Response(200, json={"ok": True, "result": message()})
            self.assertIn(method, {"sendMediaGroup", "editMessageMedia"})
            parsed = BytesParser(policy=policy.default).parsebytes(
                b"Content-Type: " + request.headers["content-type"].encode() + b"\r\n\r\n" + request.content,
            )
            fields: dict[str, bytes] = {}
            for part in parsed.iter_parts():
                name = part.get_param("name", header="content-disposition")
                data = part.get_payload(decode=True)
                if isinstance(name, str) and isinstance(data, bytes):
                    fields[name] = data
            payload = json.loads(fields["media"])
            items = payload if isinstance(payload, list) else [payload]
            refs: set[str] = set()
            for item in items:
                uri = item.get("media")
                self.assertIsInstance(uri, str, "InputMedia must contain an attach:// media reference")
                self.assertTrue(uri.startswith("attach://"))
                key = uri.removeprefix("attach://")
                self.assertIn(key, fields)
                self.assertTrue(fields[key])
                refs.add(key)
            self.assertEqual(len(refs), len(items), "Each uploaded image needs its own multipart key")
            if method == "sendMediaGroup":
                observed.append("album")
                return httpx.Response(200, json={"ok": True, "result": [dict(message(), media_group_id="test-album") for _ in items]})
            observed.append(items[0]["type"])
            return httpx.Response(200, json={"ok": True, "result": True})

        request = HTTPXRequest(httpx_kwargs={"transport": httpx.MockTransport(handle), "trust_env": False})
        uniforms = Uniforms(ally=tuple(UniformImage(image=f"https://tsgames.ru/{i}.jpg") for i in range(2)))
        current = self.detail.model_copy(update={"screenshots": uniforms})
        self.api.plan.return_value = current
        async with Bot("123:test", request=request) as bot:
            for _ in range(2):
                self.now += timedelta(seconds=90)
                await self.monitor.poll(bot)
            current = current.model_copy(update={"content": "@@@ DER\n" + "Длинная задача " * 1000 + "\n@@@"})
            self.api.plan.return_value = current
            self.now += timedelta(seconds=90)
            await self.monitor.poll(bot)
            cloud = "https://nitro-n.s3-website.cloud.ru/plans/test/image.webp"
            current = current.model_copy(update={"content": current.content + f"\n![]({cloud})"})
            self.api.plan.return_value = current
            self.store.enqueue("chat", self.summary, current)
            for pending in self.store.pending("chat", 6057):
                if pending.image_url == cloud:
                    self.store.confirm("chat", pending, 555, "text")
                    self.store.prepare_message("chat", replace(pending, message_id=555, media_type="text"))
            self.now += timedelta(seconds=90)
            await self.monitor.poll(bot)
        self.assertEqual(observed, ["album", "document", "photo"])

    async def test_uniforms_are_two_separate_uploaded_albums_and_survive_restart(self) -> None:
        ally = tuple(UniformImage(image=f"https://tsgames.ru/ally{i}.jpg") for i in range(2))
        enemy = tuple(UniformImage(image=f"https://tsgames.ru/enemy{i}.jpg") for i in range(3))
        self.api.plan.return_value = self.detail.model_copy(update={"screenshots": Uniforms(ally=ally, enemy=enemy)})
        await self.start()
        self.assertEqual(self.bot.send_media_group.await_count, 2)
        media = [call.kwargs["media"] for call in self.bot.send_media_group.await_args_list]
        self.assertEqual([len(group) for group in media], [2, 3])
        self.assertTrue(all(item.caption.startswith("Наша форма:") for item in media[0]))
        self.assertTrue(all(item.caption.startswith("Противник:") for item in media[1]))
        self.assertTrue(all(item.media.input_file_content == b"image" for group in media for item in group))
        self.bot.send_photo.assert_not_awaited()
        self.monitor = PlanMonitor(self.api, PlanStore(), "chat", lambda: self.now)
        await self.tick()
        await self.tick()
        self.assertEqual(self.bot.send_media_group.await_count, 2)
        changed = tuple(image.model_copy(update={"title": "Обновлённая подпись"}) for image in ally)
        self.api.plan.return_value = self.detail.model_copy(update={"screenshots": Uniforms(ally=changed, enemy=enemy)})
        await self.tick()
        self.assertEqual(self.bot.edit_message_caption.await_count, 2)
        self.assertEqual(self.bot.send_media_group.await_count, 2)

    async def test_partial_side_changes_split_albums_including_singleton(self) -> None:
        images = tuple(UniformImage(image=f"https://tsgames.ru/{i}.jpg") for i in range(4))
        self.api.plan.return_value = self.detail.model_copy(update={"screenshots": Uniforms(ally=images)})
        await self.start()
        for count in (2, 1):
            self.api.plan.return_value = self.detail.model_copy(update={"screenshots": Uniforms(ally=images[:count], enemy=images[count:])})
            await self.tick()
            with db.get_conn() as conn:
                rows = conn.execute("SELECT media_group_id,album_key FROM plan_messages WHERE kind='image' AND active=1").fetchall()
            groups: dict[str, set[str]] = {}
            for group_id, side in rows:
                if group_id is not None:
                    groups.setdefault(group_id, set()).add(side)
            self.assertTrue(all(len(sides) == 1 for sides in groups.values()))
            self.assertEqual(self.store.pending("chat", 6057), ())
            self.assertEqual(self.store.pending_deletions("chat", 6057), ())
        self.bot.send_photo.assert_awaited_once()
        self.bot.send_message.assert_awaited_once()

    async def test_lost_delete_response_then_reactivation_republishes_instead_of_reusing_missing_id(self) -> None:
        image = UniformImage(image="https://tsgames.ru/form.jpg")
        with_form = self.detail.model_copy(update={"screenshots": Uniforms(ally=(image,))})
        self.api.plan.return_value = with_form
        await self.start()
        old_id = self.sequence
        self.api.plan.return_value = self.detail
        self.bot.delete_message.side_effect = TimedOut()
        await self.tick()
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT attempted FROM plan_message_deletions").fetchone()[0], 1)
            self.assertIsNone(conn.execute("SELECT message_id FROM plan_messages WHERE kind='image'").fetchone()[0])
        self.api.plan.return_value = with_form
        self.bot.delete_message.side_effect = [TimedOut(), BadRequest("Message to delete not found")]
        await self.tick()
        self.assertEqual(self.bot.send_photo.await_count, 2)
        self.assertNotEqual(self.sequence, old_id)
        self.assertEqual(self.store.pending("chat", 6057), ())
        self.assertEqual(self.store.pending_deletions("chat", 6057), ())

    async def test_telegram_flood_limit_pauses_album_delivery_without_partial_singles(self) -> None:
        images = tuple(UniformImage(image=f"https://tsgames.ru/{i}.jpg") for i in range(2))
        self.api.plan.return_value = self.detail.model_copy(update={"screenshots": Uniforms(ally=images)})
        self.bot.send_media_group.side_effect = RetryAfter(35)
        await self.start()
        self.bot.send_photo.assert_not_awaited()
        self.bot.send_media_group.assert_awaited_once()
        self.bot.send_media_group.side_effect = self.sent_album
        await self.tick(30)
        self.bot.send_media_group.assert_awaited_once()
        await self.tick(5)
        self.assertEqual(self.bot.send_media_group.await_count, 2)
        self.assertEqual(self.store.pending("chat", 6057), ())
        self.bot.send_photo.assert_not_awaited()

    async def test_album_limit_is_ten_photos(self) -> None:
        images = tuple(UniformImage(image=f"https://tsgames.ru/{i}.jpg") for i in range(12))
        self.api.plan.return_value = self.detail.model_copy(update={"screenshots": Uniforms(ally=images)})
        await self.start()
        self.assertEqual([len(call.kwargs["media"]) for call in self.bot.send_media_group.await_args_list], [10, 2])
        self.assertEqual(self.store.pending("chat", 6057), ())

    async def test_existing_singles_are_deleted_only_after_album_receipt_and_cleanup_retries(self) -> None:
        images = tuple(UniformImage(image=f"https://tsgames.ru/{i}.jpg") for i in range(2))
        detail = self.detail.model_copy(update={"screenshots": Uniforms(ally=images)})
        self.store.save_topic("chat", Topic(6057, 101, self.summary.event_datetime, self.summary.topic_name()))
        self.store.enqueue("chat", self.summary, detail)
        for number, message in enumerate(self.store.pending("chat", 6057), 900):
            self.store.confirm("chat", message, number, "text" if message.kind == "plan" else "photo")
        # Simulate the previous deployment, which had no album keys/group IDs.
        with db.get_conn() as conn:
            conn.execute("UPDATE plan_messages SET album_key=NULL")
            conn.commit()
        self.api.plan.return_value = detail
        self.bot.send_media_group.side_effect = TimedOut()
        await self.start()
        self.bot.delete_message.assert_not_awaited()
        self.assertEqual(self.store.pending_deletions("chat", 6057), ())
        self.bot.send_media_group.side_effect = self.sent_album
        self.bot.delete_message.side_effect = TimedOut()
        await self.tick()
        self.assertEqual(set(self.store.pending_deletions("chat", 6057)), {901, 902})
        self.bot.delete_message.side_effect = None
        self.monitor = PlanMonitor(self.api, PlanStore(), "chat", lambda: self.now)
        await self.tick()
        self.assertEqual(self.store.pending_deletions("chat", 6057), ())
        self.assertEqual(self.bot.send_media_group.await_count, 2)
        self.assertEqual({call.kwargs["message_id"] for call in self.bot.delete_message.await_args_list}, {901, 902})
        self.bot.send_message.assert_not_awaited()

    async def test_failed_uniform_download_does_not_block_other_side_or_send_partial_singles(self) -> None:
        ally = tuple(UniformImage(image=f"https://tsgames.ru/ally{i}.jpg") for i in range(2))
        enemy = tuple(UniformImage(image=f"https://tsgames.ru/enemy{i}.jpg") for i in range(2))
        self.api.plan.return_value = self.detail.model_copy(update={"screenshots": Uniforms(ally=ally, enemy=enemy)})
        self.download.side_effect = [b"image", ImageDownloadError("404"), b"image", b"image"]
        await self.start()
        self.bot.send_media_group.assert_awaited_once()
        self.assertTrue(self.bot.send_media_group.await_args.kwargs["media"][0].caption.startswith("Противник:"))
        self.bot.send_photo.assert_not_awaited()
        self.bot.send_message.assert_awaited_once()
        self.assertEqual(len(self.store.pending("chat", 6057)), 2)

    async def test_trusted_cloud_link_is_promoted_to_uploaded_photo_in_same_message(self) -> None:
        url = "https://nitro-n.s3-website.cloud.ru/plans/test/image.webp"
        self.api.plan.return_value = self.detail.model_copy(update={"content": self.detail.content + f"\n![]({url})"})
        self.download.side_effect = ImageDownloadError("old origin policy", retryable=False)
        await self.start()
        link_id = self.sequence
        with db.get_conn() as conn:
            conn.execute("UPDATE plan_messages SET sent_text=NULL WHERE kind='image'")
            conn.commit()
        self.download.side_effect = None
        self.bot.edit_message_media.side_effect = TimedOut()
        await self.tick()
        self.bot.edit_message_media.side_effect = BadRequest("Message is not modified")
        await self.tick()
        self.assertEqual(self.bot.edit_message_media.await_args.kwargs["message_id"], link_id)
        self.assertEqual(self.store.pending("chat", 6057), ())
        self.assertEqual(self.bot.send_message.await_count, 2)
        self.bot.send_photo.assert_not_awaited()

    async def test_uniform_side_change_updates_existing_caption_not_photo(self) -> None:
        image = UniformImage(image="https://tsgames.ru/same.jpg")
        self.api.plan.return_value = self.detail.model_copy(update={"screenshots": Uniforms(ally=(image,))})
        await self.start()
        photo_id = self.sequence
        self.api.plan.return_value = self.detail.model_copy(update={"screenshots": Uniforms(enemy=(image,))})
        await self.tick()
        self.bot.send_photo.assert_awaited_once()
        self.bot.edit_message_caption.assert_awaited_once()
        self.assertEqual(self.bot.edit_message_caption.await_args.kwargs["message_id"], photo_id)
        self.assertTrue(self.bot.edit_message_caption.await_args.kwargs["caption"].startswith("Противник:"))

    async def test_migration_does_not_repost_existing_uploaded_images(self) -> None:
        url = "https://img.tsgames.ru/map.png"
        with db.get_conn() as conn:
            conn.execute("INSERT INTO plan_deliveries (chat_id,reference,kind,identity,text,image_url,sent_parts) VALUES ('chat',6057,'image',?,'Old image',?,1)", (digest(url), url))
            conn.commit()
        db.init_db()
        db.init_db()
        self.api.plan.return_value = self.detail.model_copy(update={"content": self.detail.content + f"\n![]({url})"})
        await self.start()
        self.bot.send_photo.assert_not_awaited()
        self.assertEqual(self.store.pending("chat", 6057), ())

    async def test_broken_image_does_not_block_plan_or_other_images_and_removed_image_stops_retrying(self) -> None:
        self.api.plan.return_value = self.detail.model_copy(update={"content": self.detail.content + "\n![](https://img.tsgames.ru/bad.png)\n![](https://img.tsgames.ru/good.png)"})
        self.download.side_effect = [ImageDownloadError("HTTP 404"), b"image"]
        await self.start()
        self.bot.send_message.assert_awaited_once()
        self.bot.send_photo.assert_awaited_once()
        pending, = self.store.pending("chat", 6057)
        self.assertEqual(pending.image_url, "https://img.tsgames.ru/bad.png")
        self.download.side_effect = ImageDownloadError("HTTP 404")
        self.api.plan.return_value = self.detail
        await self.tick()
        self.assertEqual(self.store.pending("chat", 6057), ())

    async def test_invalid_photo_uses_document_and_external_image_link_is_not_retried(self) -> None:
        self.api.plan.return_value = self.detail.model_copy(update={"content": self.detail.content + "\n![](https://img.tsgames.ru/tall.png)"})
        self.bot.send_photo.side_effect = BadRequest("PHOTO_INVALID_DIMENSIONS")
        await self.start()
        self.bot.send_document.assert_awaited_once()
        await self.tick()
        self.bot.send_document.assert_awaited_once()
        self.api.plan.return_value = self.detail.model_copy(update={"content": self.detail.content + "\n![](https://giphy.com/a.gif)"})
        self.download.side_effect = ImageDownloadError("origin", retryable=False)
        await self.tick()
        self.assertIn("Изображение по ссылке:", self.bot.send_message.await_args.kwargs["text"])
        attempts = self.download.await_count
        await self.tick()
        self.assertEqual(self.download.await_count, attempts)

    async def test_legacy_generic_photo_gets_its_new_uniform_label_only_once(self) -> None:
        url = "https://tsgames.ru/form.jpg"
        with db.get_conn() as conn:
            conn.execute("INSERT INTO plan_deliveries (chat_id,reference,kind,identity,text,image_url,sent_parts) VALUES ('chat',6057,'image',?,'Old generic photo',?,1)", (digest(url), url))
            conn.commit()
        db.init_db()
        self.api.plan.return_value = self.detail.model_copy(update={"screenshots": Uniforms(ally=(UniformImage(image=url),))})
        await self.start()
        self.assertTrue(self.bot.send_photo.await_args.kwargs["caption"].startswith("Наша форма:"))
        self.monitor = PlanMonitor(self.api, PlanStore(), "chat", lambda: self.now)
        db.init_db()
        await self.tick()
        await self.tick()
        self.bot.send_photo.assert_awaited_once()

    async def test_quota_uses_actual_time_after_slow_pending_delivery(self) -> None:
        await self.tick()
        self.store.save_topic("chat", Topic(6057, 101, self.summary.event_datetime, self.summary.topic_name()))
        self.store.enqueue("chat", self.summary, self.detail)

        def slow(**kwargs: object) -> SimpleNamespace:
            self.now += timedelta(seconds=40)
            return self.sent_message()

        self.bot.send_message.side_effect = slow
        await self.tick()
        self.api.plan.assert_awaited_once()
        self.assertFalse(self.store.claim_request(self.now + timedelta(seconds=89)))
        self.assertTrue(self.store.claim_request(self.now + timedelta(seconds=90)))

    async def test_request_clock_and_rate_limit_cooldown_survive_restart(self) -> None:
        self.api.headquarters.side_effect = HeadquartersError("Rate limit", cooldown=1200)
        await self.tick()
        self.monitor = PlanMonitor(self.api, PlanStore(), "chat", lambda: self.now)
        await self.tick(1199)
        self.api.headquarters.assert_awaited_once()
        await self.tick(1)
        self.assertEqual(self.api.headquarters.await_count, 2)

    async def test_existing_topic_gets_weekday_label_without_recreation(self) -> None:
        mission = MissionName("Крона", "https://tsgames.ru/missions/NL_Krona")
        self.store.save_mission_names((self.summary,), {(self.summary.event_datetime, 0): mission})
        self.store.save_topic("chat", Topic(6057, 101, self.summary.event_datetime, "11-14-1-Крона"))
        await self.tick()
        self.bot.edit_forum_topic.assert_awaited_once_with(chat_id="chat", message_thread_id=101, name="11-14-сб1-Крона")
        self.bot.create_forum_topic.assert_not_awaited()
        self.assertEqual(self.store.topics("chat")[0].thread_id, 101)
        self.assertEqual(self.store.topics("chat")[0].name, "11-14-сб1-Крона")

    async def test_names_survive_public_site_failure(self) -> None:
        mission = MissionName("Крона", "https://tsgames.ru/missions/NL_Krona")
        self.store.save_mission_names((self.summary,), {(self.summary.event_datetime, 0): mission})
        self.schedule.side_effect = MissionScheduleError("unavailable")
        await self.start()
        self.assertEqual(self.bot.create_forum_topic.await_args.kwargs["name"], "11-14-сб1-Крона")
        self.assertIn("Крона", self.bot.send_message.await_args.kwargs["text"])

    async def test_only_old_tracked_topics_are_deleted(self) -> None:
        self.store.save_topic("chat", Topic(1, 11, date(2026, 11, 8), "old"))
        self.store.save_topic("chat", Topic(2, 12, date(2026, 11, 10), "current"))
        self.store.save_topic("other", Topic(3, 13, date(2026, 11, 1), "other"))
        await self.tick()
        self.bot.delete_forum_topic.assert_awaited_once_with(chat_id="chat", message_thread_id=11)
        self.assertEqual({topic.reference for topic in self.store.topics("chat")}, {2})
        self.assertEqual(len(self.store.topics("other")), 1)

    async def test_one_closed_topic_does_not_block_other_delivery_or_api(self) -> None:
        other = self.summary.model_copy(update={"reference": 6049, "event_position": 1})
        self.api.headquarters.return_value = Headquarters(plans=(self.summary, other))
        for summary, thread in ((self.summary, 101), (other, 102)):
            self.store.save_topic("chat", Topic(summary.reference, thread, summary.event_datetime, summary.topic_name()))
            self.store.enqueue("chat", summary, self.detail)

        def send(**kwargs: object) -> SimpleNamespace:
            if kwargs["message_thread_id"] == 101:
                raise BadRequest("TOPIC_CLOSED")
            return self.sent_message()

        self.bot.send_message.side_effect = send
        await self.tick()
        self.assertEqual(self.store.pending("chat", 6049), ())
        self.assertTrue(self.store.pending("chat", 6057))
        await self.tick()
        self.api.plan.assert_awaited_once()

    async def test_reschedule_outside_window_is_saved_before_cleanup_even_when_rename_fails(self) -> None:
        await self.start()
        moved = self.summary.model_copy(update={"event_datetime": date(2026, 11, 27)})
        self.api.plan.return_value = self.detail.model_copy(update={"event_datetime": moved.event_datetime})
        self.bot.edit_forum_topic.side_effect = Forbidden("rename")
        await self.tick()
        self.assertEqual(self.store.topics("chat")[0].event_date, moved.event_datetime)
        self.api.headquarters.return_value = Headquarters(plans=(moved,))
        self.now = datetime(2026, 11, 16, tzinfo=ZoneInfo("Europe/Moscow"))
        await self.tick(0)
        self.bot.delete_forum_topic.assert_not_awaited()

    async def test_only_nearest_four_game_details_are_fetched(self) -> None:
        plans = tuple(self.summary.model_copy(update={"reference": day * 10 + pos, "event_datetime": date(2026, 11, day), "event_position": pos}) for day in (12, 13, 14, 20, 21, 27) for pos in (0, 1))
        self.api.headquarters.return_value = Headquarters(plans=tuple(reversed(plans)))

        async def plan(reference: int) -> PlanDetail:
            summary = next(item for item in plans if item.reference == reference)
            return self.detail.model_copy(update={"event_datetime": summary.event_datetime, "event_position": summary.event_position})

        self.api.plan.side_effect = plan
        for _ in range(12):
            await self.tick()
        self.assertEqual({call.args[0] for call in self.api.plan.await_args_list}, {130, 131, 140, 141})


class HeadquartersAPITests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_listing_object_drops_private_fields(self) -> None:
        response = httpx.Response(200, json={"headquarters_password": "private", "plans": [{"reference": 6057, "mission_id": 1421, "event_datetime": "2026-11-14", "event_position": 0}]})
        with patch.object(httpx.AsyncClient, "get", new=AsyncMock(return_value=response)):
            result = await HeadquartersAPI("secret").headquarters()
        self.assertNotIn("private", result.model_dump_json())

    async def test_http_and_transport_errors_hide_token(self) -> None:
        for result in (httpx.Response(401), httpx.ConnectError("failed https://tsgames.ru/?access-token=secret")):
            get = AsyncMock(side_effect=result) if isinstance(result, Exception) else AsyncMock(return_value=result)
            with patch.object(httpx.AsyncClient, "get", new=get), self.assertRaises(HeadquartersError) as error:
                await HeadquartersAPI("secret").headquarters()
            self.assertNotIn("secret", str(error.exception))

    async def test_validation_error_names_fields_without_private_input(self) -> None:
        response = httpx.Response(200, json={"content": "PRIVATE PLAN"})
        with patch.object(httpx.AsyncClient, "get", new=AsyncMock(return_value=response)), self.assertRaises(HeadquartersError) as error:
            await HeadquartersAPI("secret").plan(6057)
        self.assertIn("author_squad", str(error.exception))
        self.assertNotIn("PRIVATE PLAN", str(error.exception))

    async def test_rate_limit_honors_retry_after(self) -> None:
        with patch.object(httpx.AsyncClient, "get", new=AsyncMock(return_value=httpx.Response(429, headers={"Retry-After": "1200"}))), self.assertRaises(HeadquartersError) as error:
            await HeadquartersAPI("secret").headquarters()
        self.assertEqual(error.exception.cooldown, 1200)
