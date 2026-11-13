"""Rate-limited headquarters polling and persistent Telegram topic delivery."""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx
from pydantic import ValidationError
from telegram import (
    Bot,
    InputFile,
    InputMediaDocument,
    InputMediaPhoto,
    LinkPreviewOptions,
)
from telegram.error import BadRequest, RetryAfter, TelegramError

from db import get_conn
from mission_names import MissionName, MissionScheduleError, fetch_mission_schedule
from plan_content import (
    Headquarters,
    PlanDetail,
    PlanSummary,
    telegram_chunks,
    upcoming_plans,
)

logger = logging.getLogger(__name__)


class HeadquartersError(Exception):
    def __init__(self, message: str, cooldown: int = 90) -> None:
        super().__init__(message)
        self.cooldown = cooldown


class HeadquartersAPI:
    def __init__(self, token: str) -> None:
        self._token = token

    async def _get(self, path: str) -> bytes:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(
                    f"https://tsgames.ru/api/v2/{path}",
                    params={"access-token": self._token},
                )
        except httpx.RequestError as exc:
            # Exception URLs contain the credential; never log or propagate them.
            raise HeadquartersError(f"Headquarters transport failure: {type(exc).__name__}") from None
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After", "600")
            cooldown = max(600, int(retry_after)) if retry_after.isdigit() else 600
            raise HeadquartersError("Headquarters rate limit", cooldown=cooldown)
        if response.is_error:
            raise HeadquartersError(f"Headquarters HTTP {response.status_code}")
        return response.content

    async def headquarters(self) -> Headquarters:
        payload = await self._get("headquarters")
        try:
            return Headquarters.model_validate_json(payload)
        except ValidationError:
            raise HeadquartersError("Invalid headquarters response") from None

    async def plan(self, reference: int) -> PlanDetail:
        payload = await self._get(f"headquarters/{reference}")
        try:
            return PlanDetail.model_validate_json(payload)
        except ValidationError as exc:
            fields = ", ".join(".".join(map(str, error["loc"])) for error in exc.errors(include_input=False)[:6])
            raise HeadquartersError(f"Invalid plan response {reference}: {fields}") from None


class ImageDownloadError(Exception):
    def __init__(self, message: str, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


def image_origin_allowed(url: str) -> bool:
    try:
        target = urlsplit(url)
        return (
            target.scheme == "https" and target.hostname in {"tsgames.ru", "img.tsgames.ru", "nitro-n.s3-website.cloud.ru"}
            and target.port in {None, 443} and target.username is None and target.password is None
        )
    except ValueError:
        return False


async def download_image(url: str) -> bytes:
    if not image_origin_allowed(url):
        raise ImageDownloadError("Image origin is not allowed", retryable=False)
    limit = 10_000_000
    try:
        async with (
            httpx.AsyncClient(timeout=30, follow_redirects=False) as client,
            client.stream("GET", url) as response,
        ):
            if response.status_code != 200:
                raise ImageDownloadError(f"Image HTTP {response.status_code}")
            if not response.headers.get("content-type", "").lower().startswith("image/"):
                raise ImageDownloadError("Image endpoint returned non-image content")
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > limit:
                    raise ImageDownloadError("Image exceeds Telegram photo size limit", retryable=False)
    except httpx.RequestError as exc:
        raise ImageDownloadError(f"Image transport failure: {type(exc).__name__}") from None
    if not content:
        raise ImageDownloadError("Image endpoint returned empty content")
    return bytes(content)


@dataclass(frozen=True)
class Topic:
    reference: int
    thread_id: int
    event_date: date
    name: str


MediaType = Literal["text", "photo", "document", "plan_document", "legacy"]


@dataclass(frozen=True)
class PlanMessage:
    reference: int
    kind: Literal["plan", "image"]
    identity: str
    text: str
    image_url: str | None
    message_id: int | None
    sent_text: str | None
    media_type: MediaType | None
    album_key: str | None = None
    media_group_id: str | None = None

    def requires_document(self) -> bool:
        return self.kind == "plan" and (self.media_type == "plan_document" or len(self.text.encode("utf-16-le")) // 2 > 4000)


class PlanStore:
    def topics(self, chat_id: str) -> tuple[Topic, ...]:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT reference, thread_id, event_date, name FROM plan_topics WHERE chat_id=?",
                (chat_id,),
            ).fetchall()
        return tuple(Topic(row["reference"], row["thread_id"], date.fromisoformat(row["event_date"]), row["name"]) for row in rows)

    def save_topic(self, chat_id: str, topic: Topic) -> None:
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO plan_topics (chat_id, reference, thread_id, event_date, name)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(chat_id, reference) DO UPDATE SET
                   thread_id=excluded.thread_id, event_date=excluded.event_date, name=excluded.name""",
                (chat_id, topic.reference, topic.thread_id, topic.event_date.isoformat(), topic.name),
            )
            conn.commit()

    def forget_topic(self, chat_id: str, reference: int) -> None:
        with get_conn() as conn:
            conn.execute("DELETE FROM plan_deliveries WHERE chat_id=? AND reference=?", (chat_id, reference))
            conn.execute("DELETE FROM plan_messages WHERE chat_id=? AND reference=?", (chat_id, reference))
            conn.execute("DELETE FROM plan_message_deletions WHERE chat_id=? AND reference=?", (chat_id, reference))
            conn.execute("DELETE FROM plan_topics WHERE chat_id=? AND reference=?", (chat_id, reference))
            conn.commit()

    def claim_request(self, now: datetime) -> bool:
        with get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT next_request FROM plan_api_clock WHERE id=1").fetchone()
            if row is not None and row["next_request"] > now.timestamp():
                return False
            conn.execute(
                "INSERT OR REPLACE INTO plan_api_clock (id, next_request) VALUES (1, ?)",
                (now.timestamp() + 90,),
            )
            conn.commit()
        return True

    def defer_requests(self, until: datetime) -> None:
        with get_conn() as conn:
            conn.execute("UPDATE plan_api_clock SET next_request=MAX(next_request, ?) WHERE id=1", (until.timestamp(),))
            conn.commit()

    def save_mission_names(self, plans: tuple[PlanSummary, ...], schedule: dict[tuple[date, int], MissionName]) -> None:
        names: dict[int, MissionName] = {}
        for plan in sorted(plans, key=lambda item: (item.event_datetime, item.event_position, item.reference)):
            mission = schedule.get((plan.event_datetime, plan.event_position))
            if mission is not None:
                names[plan.mission_id] = mission
        with get_conn() as conn:
            conn.executemany(
                "INSERT INTO plan_missions (mission_id,name,url) VALUES (?,?,?) ON CONFLICT(mission_id) DO UPDATE SET name=excluded.name,url=excluded.url",
                ((mission_id, mission.name, mission.url) for mission_id, mission in names.items()),
            )
            conn.commit()

    def mission(self, mission_id: int) -> MissionName | None:
        with get_conn() as conn:
            row = conn.execute("SELECT name,url FROM plan_missions WHERE mission_id=?", (mission_id,)).fetchone()
        return MissionName(row["name"], row["url"]) if row is not None else None

    def enqueue(self, chat_id: str, summary: PlanSummary, detail: PlanDetail) -> None:
        text = detail.der_message(summary, self.mission(summary.mission_id))
        with get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """INSERT INTO plan_messages (chat_id,reference,kind,identity,text)
                   VALUES (?,?,'plan','current',?) ON CONFLICT(chat_id,reference,kind,identity)
                   DO UPDATE SET text=excluded.text,active=1""",
                (chat_id, summary.reference, text),
            )
            conn.execute("UPDATE plan_messages SET active=0 WHERE chat_id=? AND reference=? AND kind='image'", (chat_id, summary.reference))
            for notice in detail.media_notices():
                caption = telegram_chunks(notice.text, limit=1024)[0]
                conn.execute(
                    """INSERT INTO plan_messages (chat_id,reference,kind,identity,text,image_url,album_key)
                       VALUES (?,?,'image',?,?,?,?) ON CONFLICT(chat_id,reference,kind,identity)
                       DO UPDATE SET text=excluded.text,image_url=excluded.image_url,album_key=excluded.album_key,active=1""",
                    (chat_id, summary.reference, notice.identity, caption, notice.image_url, notice.album_key),
                )
                if notice.kind == "uniform":
                    # A legacy photo has no editable ID; publish its newly requested uniform label once.
                    conn.execute(
                        """UPDATE plan_messages SET media_type=NULL,sent_text=NULL WHERE
                           chat_id=? AND reference=? AND kind='image' AND identity=? AND media_type='legacy'""",
                        (chat_id, summary.reference, notice.identity),
                    )
            # Cancel a retirement if that exact photo became current again before deletion.
            conn.execute(
                """DELETE FROM plan_message_deletions WHERE chat_id=? AND reference=? AND attempted=0 AND message_id IN
                   (SELECT message_id FROM plan_messages WHERE chat_id=? AND reference=? AND active=1)""",
                (chat_id, summary.reference, chat_id, summary.reference),
            )
            conn.execute(
                """INSERT OR IGNORE INTO plan_message_deletions (chat_id,reference,message_id)
                   SELECT chat_id,reference,message_id FROM plan_messages
                   WHERE chat_id=? AND reference=? AND kind='image' AND album_key IS NOT NULL AND active=0 AND message_id IS NOT NULL""",
                (chat_id, summary.reference),
            )
            conn.commit()

    def pending(self, chat_id: str, reference: int) -> tuple[PlanMessage, ...]:
        with get_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM plan_messages WHERE chat_id=? AND reference=? AND active=1
                   AND (media_type IS NULL OR media_type!='legacy') AND (sent_text IS NULL OR text!=sent_text)
                   ORDER BY CASE kind WHEN 'plan' THEN 0 ELSE 1 END,rowid""",
                (chat_id, reference),
            ).fetchall()
        return tuple(PlanMessage(row["reference"], row["kind"], row["identity"], row["text"], row["image_url"], row["message_id"], row["sent_text"], row["media_type"], row["album_key"], row["media_group_id"]) for row in rows)

    def albums(self, chat_id: str, reference: int) -> tuple[tuple[PlanMessage, ...], ...]:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM plan_messages WHERE chat_id=? AND reference=? AND active=1 AND album_key IS NOT NULL ORDER BY album_key,rowid",
                (chat_id, reference),
            ).fetchall()
        sides: dict[str, list[PlanMessage]] = {}
        for row in rows:
            message = PlanMessage(row["reference"], row["kind"], row["identity"], row["text"], row["image_url"], row["message_id"], row["sent_text"], row["media_type"], row["album_key"], row["media_group_id"])
            sides.setdefault(row["album_key"], []).append(message)
        return tuple(tuple(side[index:index + 10]) for side in sides.values() for index in range(0, len(side), 10))

    def group_members(self, chat_id: str, reference: int, group_id: str) -> set[str]:
        with get_conn() as conn:
            return {row[0] for row in conn.execute(
                "SELECT identity FROM plan_messages WHERE chat_id=? AND reference=? AND media_group_id=? AND message_id IS NOT NULL",
                (chat_id, reference, group_id),
            )}

    def confirm_album(self, chat_id: str, messages: tuple[PlanMessage, ...], ids: tuple[int, ...], group_id: str | None, media_type: MediaType = "photo") -> None:
        if len(messages) != len(ids):
            raise RuntimeError("Telegram album receipt size mismatch")
        with get_conn() as conn:
            for message, message_id in zip(messages, ids):
                if message.message_id is not None and message.message_id != message_id:
                    conn.execute(
                        "INSERT OR IGNORE INTO plan_message_deletions (chat_id,reference,message_id) VALUES (?,?,?)",
                        (chat_id, message.reference, message.message_id),
                    )
                conn.execute(
                    """UPDATE plan_messages SET message_id=?,sent_text=?,media_type=?,media_group_id=?
                       WHERE chat_id=? AND reference=? AND kind='image' AND identity=?""",
                    (message_id, message.text, media_type, group_id, chat_id, message.reference, message.identity),
                )
            conn.commit()

    def pending_deletions(self, chat_id: str, reference: int) -> tuple[int, ...]:
        with get_conn() as conn:
            return tuple(row[0] for row in conn.execute("SELECT message_id FROM plan_message_deletions WHERE chat_id=? AND reference=?", (chat_id, reference)))

    def prepare_deletion(self, chat_id: str, message_id: int) -> None:
        with get_conn() as conn:
            conn.execute("UPDATE plan_message_deletions SET attempted=1 WHERE chat_id=? AND message_id=?", (chat_id, message_id))
            # A timeout may hide a successful deletion. Never treat that ID as confirmed again.
            conn.execute(
                "UPDATE plan_messages SET message_id=NULL,sent_text=NULL,media_type=NULL,media_group_id=NULL WHERE chat_id=? AND kind='image' AND message_id=?",
                (chat_id, message_id),
            )
            conn.commit()

    def confirm_deletion(self, chat_id: str, message_id: int) -> None:
        with get_conn() as conn:
            conn.execute("DELETE FROM plan_message_deletions WHERE chat_id=? AND message_id=?", (chat_id, message_id))
            conn.execute(
                "UPDATE plan_messages SET message_id=NULL,sent_text=NULL,media_type=NULL,media_group_id=NULL WHERE chat_id=? AND kind='image' AND message_id=?",
                (chat_id, message_id),
            )
            conn.commit()

    def prepare_message(self, chat_id: str, message: PlanMessage) -> PlanMessage:
        # An edit can succeed despite a timeout. Its old text is no longer confirmed, even on a revert.
        media_type = "plan_document" if message.requires_document() else message.media_type
        with get_conn() as conn:
            conn.execute(
                "UPDATE plan_messages SET media_type=?,sent_text=NULL WHERE chat_id=? AND reference=? AND kind=? AND identity=?",
                (media_type, chat_id, message.reference, message.kind, message.identity),
            )
            conn.commit()
        return replace(message, media_type=media_type, sent_text=None)

    def confirm(self, chat_id: str, message: PlanMessage, message_id: int, media_type: MediaType) -> None:
        with get_conn() as conn:
            conn.execute(
                """UPDATE plan_messages SET message_id=?,sent_text=?,media_type=?
                   WHERE chat_id=? AND reference=? AND kind=? AND identity=?""",
                (message_id, message.text, media_type, chat_id, message.reference, message.kind, message.identity),
            )
            conn.commit()

    def forget_message(self, chat_id: str, message: PlanMessage) -> None:
        with get_conn() as conn:
            conn.execute(
                """UPDATE plan_messages SET message_id=NULL,sent_text=NULL,media_type=NULL,media_group_id=NULL
                   WHERE chat_id=? AND reference=? AND kind=? AND identity=?""",
                (chat_id, message.reference, message.kind, message.identity),
            )
            conn.commit()


def topic_missing(error: TelegramError) -> bool:
    return isinstance(error, BadRequest) and any(
        text in str(error).lower()
        for text in ("topic_id_invalid", "message thread not found", "topic_deleted")
    )


def photo_rejected(error: BadRequest) -> bool:
    return any(value in str(error).lower() for value in ("photo_invalid", "image_process_failed", "photo_ext_invalid"))


class PlanMonitor:
    def __init__(self, api: HeadquartersAPI, store: PlanStore, chat_id: str, clock: Callable[[], datetime]) -> None:
        self.api = api
        self.store = store
        self.chat_id = chat_id
        self._clock = clock
        self._plans: tuple[PlanSummary, ...] = ()
        self._listed_at: datetime | None = None
        self._checked_at: dict[int, datetime] = {}
        self._lock = asyncio.Lock()
        self._telegram_pause_until: datetime | None = None

    def _telegram_paused(self) -> bool:
        return self._telegram_pause_until is not None and self._clock() < self._telegram_pause_until

    def _pause_telegram(self, error: RetryAfter) -> None:
        delay = error.retry_after
        seconds = delay.total_seconds() if isinstance(delay, timedelta) else float(delay)
        self._telegram_pause_until = self._clock() + timedelta(seconds=seconds)
        logger.info("Telegram rate limit: delivery paused for %.0f seconds", seconds)

    def _defer_api(self, error: HeadquartersError) -> None:
        self.store.defer_requests(self._clock() + timedelta(seconds=error.cooldown))
        logger.warning("%s", error)

    async def _send_image(self, bot: Bot, thread_id: int, url: str, caption: str) -> tuple[int, MediaType]:
        try:
            content = await download_image(url)
        except ImageDownloadError as exc:
            if exc.retryable:
                raise
            sent = await bot.send_message(
                chat_id=self.chat_id, message_thread_id=thread_id,
                text=f"Изображение по ссылке:\n{caption}", link_preview_options=LinkPreviewOptions(is_disabled=True),
                read_timeout=60, write_timeout=60,
            )
            return sent.message_id, "text"
        photo = InputFile(content, filename=urlsplit(url).path.rsplit("/", 1)[-1] or "plan-image.jpg")
        try:
            sent = await bot.send_photo(
                chat_id=self.chat_id, message_thread_id=thread_id,
                photo=photo, caption=caption, read_timeout=60, write_timeout=60,
            )
        except BadRequest as exc:
            if not photo_rejected(exc):
                raise
            sent = await bot.send_document(
                chat_id=self.chat_id, message_thread_id=thread_id,
                document=photo, caption=caption, read_timeout=60, write_timeout=60,
            )
            return sent.message_id, "document"
        return sent.message_id, "photo"

    async def _promote_link(self, bot: Bot, message_id: int, url: str, caption: str) -> tuple[int, MediaType]:
        content = await download_image(url)
        photo = InputFile(content, filename=urlsplit(url).path.rsplit("/", 1)[-1] or "plan-image.jpg", attach=True)
        try:
            await bot.edit_message_media(
                chat_id=self.chat_id, message_id=message_id,
                media=InputMediaPhoto(media=photo, caption=caption), read_timeout=60, write_timeout=60,
            )
        except BadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return message_id, "photo"
            if not photo_rejected(exc):
                raise
            try:
                await bot.edit_message_media(
                    chat_id=self.chat_id, message_id=message_id,
                    media=InputMediaDocument(media=photo, caption=caption), read_timeout=60, write_timeout=60,
                )
            except BadRequest as document_error:
                if "message is not modified" not in str(document_error).lower():
                    raise
            return message_id, "document"
        return message_id, "photo"

    async def _write_plan(self, bot: Bot, topic: Topic, message: PlanMessage) -> tuple[int, MediaType]:
        # Telegram cannot fit arbitrary plans in a text post. Promote that same ID to a text file.
        if message.media_type == "plan_document":
            document = InputFile(message.text.encode(), filename=f"plan-{topic.reference}-DER.txt", attach=message.message_id is not None)
            caption = telegram_chunks(f"{message.text.splitlines()[0]}\nПолный текст — в файле.", limit=1024)[0]
            if message.message_id is not None:
                await bot.edit_message_media(
                    chat_id=self.chat_id, message_id=message.message_id,
                    media=InputMediaDocument(media=document, caption=caption), read_timeout=60, write_timeout=60,
                )
                return message.message_id, "plan_document"
            sent = await bot.send_document(
                chat_id=self.chat_id, message_thread_id=topic.thread_id,
                document=document, caption=caption, read_timeout=60, write_timeout=60,
            )
            return sent.message_id, "plan_document"
        if message.message_id is not None:
            await bot.edit_message_text(
                chat_id=self.chat_id, message_id=message.message_id, text=message.text,
                link_preview_options=LinkPreviewOptions(is_disabled=True), read_timeout=60, write_timeout=60,
            )
            return message.message_id, "text"
        sent = await bot.send_message(
            chat_id=self.chat_id, message_thread_id=topic.thread_id, text=message.text,
            link_preview_options=LinkPreviewOptions(is_disabled=True), read_timeout=60, write_timeout=60,
        )
        return sent.message_id, "text"

    async def _write_image(self, bot: Bot, topic: Topic, message: PlanMessage) -> tuple[int, MediaType]:
        if message.message_id is not None:
            if message.media_type == "text":
                if message.image_url is not None and image_origin_allowed(message.image_url):
                    try:
                        return await self._promote_link(bot, message.message_id, message.image_url, message.text)
                    except ImageDownloadError as exc:
                        if exc.retryable:
                            raise
                await bot.edit_message_text(
                    chat_id=self.chat_id, message_id=message.message_id,
                    text=f"Изображение по ссылке:\n{message.text}",
                    link_preview_options=LinkPreviewOptions(is_disabled=True), read_timeout=60, write_timeout=60,
                )
                return message.message_id, "text"
            await bot.edit_message_caption(
                chat_id=self.chat_id, message_id=message.message_id, caption=message.text,
                read_timeout=60, write_timeout=60,
            )
            return message.message_id, "document" if message.media_type == "document" else "photo"
        if message.image_url is None:
            raise RuntimeError("Image message lacks URL")
        return await self._send_image(bot, topic.thread_id, message.image_url, message.text)

    async def _deliver_one(self, bot: Bot, topic: Topic, message: PlanMessage) -> bool:
        try:
            message = self.store.prepare_message(self.chat_id, message)
            if message.kind == "plan":
                message_id, media_type = await self._write_plan(bot, topic, message)
            else:
                message_id, media_type = await self._write_image(bot, topic, message)
            self.store.confirm(self.chat_id, message, message_id, media_type)
        except ImageDownloadError as exc:
            logger.warning("Plan %s image delivery failed: %s", topic.reference, exc)
        except RetryAfter as exc:
            self._pause_telegram(exc)
            return False
        except TelegramError as exc:
            error = str(exc).lower()
            unchanged = isinstance(exc, BadRequest) and "message is not modified" in error
            missing = isinstance(exc, BadRequest) and "message to edit not found" in error
            if unchanged and message.message_id is not None and message.media_type is not None:
                self.store.confirm(self.chat_id, message, message.message_id, message.media_type)
            elif missing:
                self.store.forget_message(self.chat_id, message)
            elif topic_missing(exc):
                self.store.forget_topic(self.chat_id, topic.reference)
                return False
            else:
                logger.warning("Plan %s message update failed: %s", topic.reference, exc)
        return True

    async def _deliver_albums(self, bot: Bot, topic: Topic) -> set[str]:
        blocked: set[str] = set()
        for batch in self.store.albums(self.chat_id, topic.reference):
            if len(batch) == 1 and batch[0].media_group_id is None:
                continue  # A standalone photo needs no album operation.
            group_ids = {message.media_group_id for message in batch}
            group_id = batch[0].media_group_id
            settled = all(message.message_id is not None for message in batch) and len(group_ids) == 1 and group_id is not None
            if settled and group_id is not None:
                members = self.store.group_members(self.chat_id, topic.reference, group_id)
                if members == {message.identity for message in batch}:
                    continue
            blocked.update(message.identity for message in batch)
            try:
                prepared = tuple(self.store.prepare_message(self.chat_id, message) for message in batch)
                if len(prepared) == 1:
                    single = prepared[0]
                    if single.image_url is None:
                        raise RuntimeError("Uniform image lacks URL")
                    message_id, media_type = await self._send_image(bot, topic.thread_id, single.image_url, single.text)
                    self.store.confirm_album(self.chat_id, prepared, (message_id,), None, media_type)
                    continue
                media: list[InputMediaPhoto] = []
                for message in prepared:
                    if message.image_url is None:
                        raise RuntimeError("Uniform image lacks URL")
                    content = await download_image(message.image_url)
                    photo = InputFile(content, filename=urlsplit(message.image_url).path.rsplit("/", 1)[-1], attach=True)
                    media.append(InputMediaPhoto(media=photo, caption=message.text))
                sent = await bot.send_media_group(
                    chat_id=self.chat_id, message_thread_id=topic.thread_id, media=media,
                    read_timeout=60, write_timeout=120,
                )
                if len(sent) != len(prepared):
                    raise RuntimeError("Telegram album receipt size mismatch")
                group_id = sent[0].media_group_id or f"album:{sent[0].message_id}"
                self.store.confirm_album(self.chat_id, prepared, tuple(item.message_id for item in sent), group_id)
            except ImageDownloadError as exc:
                logger.warning("Plan %s uniform album download failed: %s", topic.reference, exc)
            except RetryAfter as exc:
                self._pause_telegram(exc)
                break
            except TelegramError as exc:
                if topic_missing(exc):
                    raise
                logger.warning("Plan %s uniform album delivery failed: %s", topic.reference, exc)
        return blocked

    async def _delete_superseded(self, bot: Bot, topic: Topic) -> None:
        for message_id in self.store.pending_deletions(self.chat_id, topic.reference):
            try:
                self.store.prepare_deletion(self.chat_id, message_id)
                await bot.delete_message(chat_id=self.chat_id, message_id=message_id, read_timeout=60)
            except RetryAfter as exc:
                self._pause_telegram(exc)
                return
            except TelegramError as exc:
                missing = isinstance(exc, BadRequest) and "message to delete not found" in str(exc).lower()
                if not missing:
                    logger.warning("Old uniform photo %s deletion failed: %s", message_id, exc)
                    continue
            self.store.confirm_deletion(self.chat_id, message_id)

    async def _deliver(self, bot: Bot, topic: Topic) -> None:
        if self._telegram_paused():
            return
        for message in self.store.pending(self.chat_id, topic.reference):
            if message.kind == "plan" and not await self._deliver_one(bot, topic, message):
                return
        try:
            blocked = await self._deliver_albums(bot, topic)
        except TelegramError:
            self.store.forget_topic(self.chat_id, topic.reference)
            return
        if self._telegram_paused():
            return
        for message in self.store.pending(self.chat_id, topic.reference):
            ready_image = message.kind == "image" and message.identity not in blocked
            if not ready_image:
                continue
            if not await self._deliver_one(bot, topic, message):
                return
        await self._delete_superseded(bot, topic)

    async def _remove_old_topics(self, bot: Bot, monday: date) -> None:
        for topic in self.store.topics(self.chat_id):
            if topic.event_date >= monday:
                continue
            try:
                await bot.delete_forum_topic(chat_id=self.chat_id, message_thread_id=topic.thread_id)
            except TelegramError as exc:
                if not topic_missing(exc):
                    logger.warning("Plan %s topic deletion failed: %s", topic.reference, exc)
                    continue
            self.store.forget_topic(self.chat_id, topic.reference)

    async def _update_topic(self, bot: Bot, topic: Topic, summary: PlanSummary) -> Topic | None:
        # Persist reschedules even if renaming fails or this game leaves the polling window.
        dated = Topic(topic.reference, topic.thread_id, summary.event_datetime, topic.name)
        self.store.save_topic(self.chat_id, dated)
        name = summary.topic_name(self.store.mission(summary.mission_id))
        if topic.name == name:
            return dated
        try:
            await bot.edit_forum_topic(chat_id=self.chat_id, message_thread_id=topic.thread_id, name=name)
        except TelegramError as exc:
            if topic_missing(exc):
                self.store.forget_topic(self.chat_id, topic.reference)
                return None
            logger.warning("Plan %s topic rename failed: %s", topic.reference, exc)
            return dated
        updated = Topic(topic.reference, topic.thread_id, summary.event_datetime, name)
        self.store.save_topic(self.chat_id, updated)
        return updated

    async def _poll(self, bot: Bot) -> None:
        now = self._clock()
        today = now.astimezone(ZoneInfo("Europe/Moscow")).date()
        monday = today - timedelta(days=today.weekday())
        listed_today = self._listed_at is not None and self._listed_at.astimezone(ZoneInfo("Europe/Moscow")).date() == today
        refresh_list = not listed_today or self._listed_at is None or now - self._listed_at >= timedelta(minutes=10)
        if refresh_list and self.store.claim_request(self._clock()):
            try:
                headquarters = await self.api.headquarters()
            except HeadquartersError as exc:
                self._defer_api(exc)
            else:
                self._plans = headquarters.plans
                self._listed_at = self._clock()
                listed_today = True
                try:
                    schedule = await fetch_mission_schedule()
                except MissionScheduleError as exc:
                    logger.warning("%s; keeping cached mission names", exc)
                else:
                    self.store.save_mission_names(self._plans, schedule)
                summaries = {plan.reference: plan for plan in self._plans}
                for topic in self.store.topics(self.chat_id):
                    summary = summaries.get(topic.reference)
                    if summary is not None:
                        await self._update_topic(bot, topic, summary)

        # At a week boundary, refresh metadata before destructive cleanup.
        if listed_today:
            await self._remove_old_topics(bot, monday)
        for topic in self.store.topics(self.chat_id):
            if topic.event_date >= monday:
                await self._deliver(bot, topic)
        if refresh_list:
            return

        eligible = upcoming_plans(self._plans, today)
        if not eligible or not self.store.claim_request(self._clock()):
            return
        requested_at = self._clock()
        summary = min(eligible, key=lambda plan: (
            self._checked_at.get(plan.reference, datetime.min.replace(tzinfo=requested_at.tzinfo)),
            plan.event_datetime, plan.event_position, plan.reference,
        ))
        self._checked_at[summary.reference] = requested_at
        detail = await self.api.plan(summary.reference)
        summary = summary.model_copy(update={"event_datetime": detail.event_datetime, "event_position": detail.event_position})
        self._plans = tuple(summary if plan.reference == summary.reference else plan for plan in self._plans)
        current_topic = next((topic for topic in self.store.topics(self.chat_id) if topic.reference == summary.reference), None)
        if current_topic is not None:
            current_topic = await self._update_topic(bot, current_topic, summary)
        selected = {plan.reference for plan in upcoming_plans(self._plans, today)}
        if summary.reference not in selected:
            return
        if current_topic is None:
            name = summary.topic_name(self.store.mission(summary.mission_id))
            created = await bot.create_forum_topic(chat_id=self.chat_id, name=name)
            current_topic = Topic(summary.reference, created.message_thread_id, summary.event_datetime, name)
            self.store.save_topic(self.chat_id, current_topic)
        self.store.enqueue(self.chat_id, summary, detail)
        await self._deliver(bot, current_topic)

    async def poll(self, bot: Bot) -> None:
        async with self._lock:
            try:
                await self._poll(bot)
            except HeadquartersError as exc:
                self._defer_api(exc)
            except TelegramError as exc:
                logger.warning("Plan topic operation failed: %s: %s", type(exc).__name__, exc)
