import asyncio
import datetime
import logging
import os
import re
import subprocess
import threading
import time
from collections import defaultdict
from pathlib import Path

import pytz
import requests
import uvicorn
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonWebApp, Update, WebAppInfo
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import replays
import slots_service
import week_control
from db import init_db
from slots_service import NotFoundError
from webapp import app as fastapi_app

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

TZ = pytz.timezone("Europe/Moscow")
TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
AI_KEY = os.environ.get("AI_KEY")
THREAD_ID = int(os.environ.get("PLAYABLE_THREAD_ID", "54606"))
WEBAPP_URL = os.environ.get("WEBAPP_URL", "")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "")
WEBAPP_HOST = os.environ.get("WEBAPP_HOST", "127.0.0.1")
WEBAPP_PORT = int(os.environ.get("WEBAPP_PORT", "8000"))
PROJECT_DIR = Path(__file__).resolve().parent
TOKEN_SH = PROJECT_DIR / "token.sh"
CLOUDFLARED_LOG = PROJECT_DIR / "cloudflared.log"
CLOUDFLARED_CMD = ["/usr/bin/cloudflared", "tunnel", "--protocol", "http2", "--no-autoupdate", "--url", f"http://{WEBAPP_HOST}:{WEBAPP_PORT}"]
ADMIN_USER_IDS = {
    int(x.strip())
    for x in os.environ.get("ADMIN_USER_IDS", "").split(",")
    if x.strip()
}
ALLOWED_MEMBER_STATUSES = {"creator", "administrator", "member", "restricted", "owner"}

chat = None
if AI_KEY:
    from google import genai

    client = genai.Client(api_key=AI_KEY)
    generation_config = {
        "temperature": 1,
        "top_p": 0.95,
        "top_k": 40,
        "max_output_tokens": 8192,
        "response_mime_type": "text/plain",
        "system_instruction": "Reply in Russian with sarcastic tone. You are replyingin the in-game squad channel or Arma3. Joke something about the players not being able to kill somebody or that you will never stop defending the trigger(main base). Be playful. Make jokes about the players, be extremely sarcastic, but add a little of cheering up now and then",
    }
    chat = client.aio.chats.create(model="gemini-2.0-flash-001", config=generation_config)


def start_web_server() -> None:
    config = uvicorn.Config(fastapi_app, host=WEBAPP_HOST, port=WEBAPP_PORT, log_level="info")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    logger.info("FastAPI started on %s:%s", WEBAPP_HOST, WEBAPP_PORT)


def get_scheduled_time(hour: int, minute: int) -> datetime.datetime:
    now = datetime.datetime.now(TZ)
    scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now >= scheduled:
        scheduled += datetime.timedelta(days=1)
    return scheduled


def is_admin(user_id: int) -> bool:
    return not ADMIN_USER_IDS or user_id in ADMIN_USER_IDS


async def get_chat_member(bot, user_id: int):
    return await bot.get_chat_member(chat_id=CHAT_ID, user_id=user_id)


async def ensure_chat_member(bot, user_id: int) -> None:
    member = await get_chat_member(bot, user_id)
    if member.status not in ALLOWED_MEMBER_STATUSES:
        raise PermissionError("Not a member of allowed chat")


async def resolve_display_name(bot, user) -> str:
    fallback = user.first_name or user.full_name or (f"@{user.username}" if user.username else str(user.id))
    try:
        member = await get_chat_member(bot, user.id)
        custom_title = getattr(member, "custom_title", None)
        if custom_title:
            return custom_title
    except Exception as exc:
        logger.warning("Could not resolve custom title for %s: %s", user.id, exc)
    return fallback


def _write_token_var(name: str, value: str) -> None:
    lines = TOKEN_SH.read_text().splitlines() if TOKEN_SH.exists() else []
    out = []
    found = False
    for line in lines:
        if line.startswith(f"export {name}="):
            out.append(f"export {name}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"export {name}={value}")
    TOKEN_SH.write_text("\n".join(out) + "\n")


def _extract_tunnel_url() -> str | None:
    if not CLOUDFLARED_LOG.exists():
        return None
    m = re.search(r"https://[-a-z0-9]+\.trycloudflare\.com", CLOUDFLARED_LOG.read_text())
    return m.group(0) if m else None


def _cloudflare_health(url: str | None) -> str:
    if not url:
        return "no-url"
    try:
        r = requests.get(url.rstrip("/") + "/health", timeout=10)
        return f"ok:{r.status_code}" if r.ok else f"bad:{r.status_code}"
    except Exception as exc:
        return f"err:{exc.__class__.__name__}"


def _restart_quick_tunnel_blocking() -> str:
    subprocess.run(["pkill", "-f", " ".join(CLOUDFLARED_CMD)], check=False)
    time.sleep(1)
    with CLOUDFLARED_LOG.open("w") as f:
        proc = subprocess.Popen(CLOUDFLARED_CMD, stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    for _ in range(40):
        url = _extract_tunnel_url()
        if url:
            _write_token_var("WEBAPP_URL", url)
            return url
        if proc.poll() is not None:
            break
        time.sleep(1)
    raise RuntimeError("Не удалось поднять Cloudflare Tunnel")


async def set_webapp_menu_button(bot, url: str) -> None:
    await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="Открыть слоты", web_app=WebAppInfo(url=url)))


async def tunnel_status_text() -> str:
    url = _extract_tunnel_url() or WEBAPP_URL
    return f"Tunnel URL: {url or '-'}\nHealth: {_cloudflare_health(url)}"


async def upsert_week_control_message(bot, force_new: bool = False) -> None:
    await week_control.upsert_week_control_message(bot, force_new=force_new)


async def create_poll(context: ContextTypes.DEFAULT_TYPE) -> None:
    now = datetime.datetime.now(TZ)
    if now.weekday() != 3:
        return
    message = await context.bot.send_poll(
        CHAT_ID,
        "Играю...",
        ["Пт1", "Пт2", "Пт не играю", "Сб1", "Сб2", "Сб не играю"],
        allows_multiple_answers=True,
        is_anonymous=False,
        message_thread_id=THREAD_ID,
    )
    await message.pin()

    message = await context.bot.send_poll(
        CHAT_ID,
        "Готов быть КО...",
        ["Пт1", "Пт2", "Сб1", "Сб2", "Буду пьян, кто КОшит то"],
        allows_multiple_answers=True,
        is_anonymous=False,
        message_thread_id=THREAD_ID,
    )
    await message.pin()


async def close_slots_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    now = datetime.datetime.now(TZ)
    if now.weekday() != 3:
        return
    closed = slots_service.auto_close_open_items()
    if closed:
        await week_control.upsert_week_control_message(context.bot)
        await context.bot.send_message(CHAT_ID, f"Итоги закрыты: {len(closed)} слот(ов)", message_thread_id=THREAD_ID)


async def init_week_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    now = datetime.datetime.now(TZ)
    if now.weekday() != 0:
        return
    await upsert_week_control_message(context.bot)


async def refresh_week_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await upsert_week_control_message(context.bot)
    except Exception as exc:
        logger.warning("Week refresh failed: %s", exc)


async def report_frags(context: ContextTypes.DEFAULT_TYPE) -> None:
    now = datetime.datetime.now(TZ)
    if 21 <= now.hour <= 23:
        return
    new_frags, _parsed_games = replays.collect_new_frags()
    new_frags = [f for f in new_frags if ("[DER]" in f.killer or "[DER_c]" in f.killer)]
    if not new_frags:
        return

    message_lines: list[str] = []
    by_game = defaultdict(list)
    for frag in new_frags:
        by_game[frag.mission].append(frag)

    for mission, frags in by_game.items():
        message_lines.append(f"Игра {mission}:")
        for frag in frags:
            message_lines.append(" " + str(frag))
        message_lines.extend(["", ""])

    payload = "\n".join(message_lines)
    await context.bot.send_message(CHAT_ID, payload)
    if chat is not None:
        try:
            response = (
                await chat.send_message(
                    "Вот фраги с последней игры, прокомментируй. Если там больше 4 - будь позитивен пожалуйста, это хороший результат:" + payload
                )
            ).text
            await context.bot.send_message(CHAT_ID, response)
        except Exception as exc:
            logger.warning("AI commentary failed: %s", exc)


async def command_app(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    try:
        await ensure_chat_member(context.bot, update.effective_user.id)
    except Exception:
        await update.message.reply_text("Только участники целевого чата могут пользоваться меню бота")
        return
    if not WEBAPP_URL:
        await update.message.reply_text("WEBAPP_URL не настроен")
        return
    if update.effective_chat and update.effective_chat.type == "private":
        await update.message.reply_text(
            "Открыть приложение слотов",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Открыть приложение", web_app=WebAppInfo(url=WEBAPP_URL))]]),
        )
        return
    username = BOT_USERNAME or (context.bot.username or "")
    if username:
        await update.message.reply_text(f"Открой бота в личке: https://t.me/{username}")
    else:
        await update.message.reply_text("Открой бота в личке и вызови /app")


def _detail_value(details: str | None) -> str | None:
    if not details:
        return None
    for part in details.split(";"):
        if part.startswith("value="):
            return part.split("=", 1)[1]
    return None


def format_logs(lines: list[dict]) -> str:
    if not lines:
        return "Лог пуст"

    action_map = {
        "add_item": "создал слот",
        "delete_item": "удалил слот",
        "roll": "сделал бросок",
        "undo_roll": "отменил бросок",
        "call_winner": "подвел итог",
        "reopen_item": "переоткрыл розыгрыш",
        "week_init": "обновил неделю",
        "week_reset": "пересоздал неделю",
        "tunnel_restart": "перезапустил tunnel",
        "warning_repeat_roll": "🚨 ВНИМАНИЕ! сделал повторный бросок",
    }

    out = []
    for row in lines:
        who = row.get("display_name") or row.get("username") or "system"
        target = ""
        if row.get("slot_code"):
            target += row["slot_code"]
        if row.get("item_name"):
            target += f"/{row['item_name']}"
        target = f" {target}" if target else ""
        action_text = action_map.get(row["action"], row["action"])
        value = _detail_value(row.get("details"))
        if value and row["action"] in {"roll", "warning_repeat_roll"}:
            action_text = f"{action_text} {value}"
        out.append(f"{row['created_at']} — {who} {action_text}{target}")
    return "\n".join(out)


async def command_week_init(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None:
        return
    try:
        await ensure_chat_member(context.bot, update.effective_user.id)
    except Exception:
        await update.effective_message.reply_text("Только участники целевого чата могут пользоваться меню бота")
        return
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("Только для админов")
        return
    user = {
        "user_id": update.effective_user.id,
        "username": f"@{update.effective_user.username}" if update.effective_user.username else None,
        "display_name": await resolve_display_name(context.bot, update.effective_user),
    }
    await upsert_week_control_message(context.bot)
    slots_service.log_action("week_init", user=user)
    await update.effective_message.reply_text("Неделя обновлена")


async def command_week_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None:
        return
    try:
        await ensure_chat_member(context.bot, update.effective_user.id)
    except Exception:
        await update.effective_message.reply_text("Только участники целевого чата могут пользоваться меню бота")
        return
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("Только для админов")
        return
    user = {
        "user_id": update.effective_user.id,
        "username": f"@{update.effective_user.username}" if update.effective_user.username else None,
        "display_name": await resolve_display_name(context.bot, update.effective_user),
    }
    await upsert_week_control_message(context.bot, force_new=True)
    slots_service.log_action("week_reset", user=user)
    await update.effective_message.reply_text("Неделя пересоздана")


async def command_slots(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None:
        return
    try:
        await ensure_chat_member(context.bot, update.effective_user.id)
    except Exception:
        await update.effective_message.reply_text("Только участники целевого чата могут пользоваться меню бота")
        return
    week = slots_service.create_or_get_active_week()
    await update.effective_message.reply_text(week_control.build_week_text(week))


async def command_undo_roll(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None:
        return
    try:
        await ensure_chat_member(context.bot, update.effective_user.id)
    except Exception:
        await update.effective_message.reply_text("Только участники целевого чата могут пользоваться меню бота")
        return
    user = {
        "user_id": update.effective_user.id,
        "username": f"@{update.effective_user.username}" if update.effective_user.username else None,
        "display_name": await resolve_display_name(context.bot, update.effective_user),
    }
    try:
        item = slots_service.undo_last_roll(user)
        await week_control.upsert_week_control_message(context.bot)
        await update.effective_message.reply_text(f"Бросок отменен: {item['slot_code']}/{item['name']}")
    except (NotFoundError, slots_service.ConflictError) as exc:
        await update.effective_message.reply_text(str(exc))


async def command_logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None:
        return
    try:
        await ensure_chat_member(context.bot, update.effective_user.id)
    except Exception:
        await update.effective_message.reply_text("Только участники целевого чата могут пользоваться меню бота")
        return
    limit = 20
    if context.args and context.args[0].isdigit():
        limit = max(1, min(int(context.args[0]), 100))
    await update.effective_message.reply_text(format_logs(slots_service.get_action_logs(limit)))


async def command_tunnel_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None:
        return
    try:
        await ensure_chat_member(context.bot, update.effective_user.id)
    except Exception:
        await update.effective_message.reply_text("Только участники целевого чата могут пользоваться меню бота")
        return
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("Только для админов")
        return
    await update.effective_message.reply_text(await tunnel_status_text())


async def command_tunnel_restart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global WEBAPP_URL
    if update.effective_user is None:
        return
    try:
        await ensure_chat_member(context.bot, update.effective_user.id)
    except Exception:
        await update.effective_message.reply_text("Только участники целевого чата могут пользоваться меню бота")
        return
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("Только для админов")
        return
    wait_msg = await update.effective_message.reply_text("Перезапускаю Cloudflare Tunnel...")
    try:
        new_url = await asyncio.to_thread(_restart_quick_tunnel_blocking)
        WEBAPP_URL = new_url
        os.environ["WEBAPP_URL"] = new_url
        week_control.WEBAPP_URL = new_url
        await set_webapp_menu_button(context.bot, new_url)
        slots_service.log_action("tunnel_restart", user={
            "user_id": update.effective_user.id,
            "username": f"@{update.effective_user.username}" if update.effective_user.username else None,
            "display_name": await resolve_display_name(context.bot, update.effective_user),
        }, details=new_url)
        await wait_msg.edit_text(f"Tunnel обновлен:\n{new_url}\nHealth: {_cloudflare_health(new_url)}")
    except Exception as exc:
        await wait_msg.edit_text(f"Ошибка перезапуска tunnel: {exc}")


async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    data = query.data or ""
    try:
        if data == "week:refresh":
            await upsert_week_control_message(context.bot)
            await query.answer("Обновлено")
            return
        if data == "week:reset":
            if update.effective_user is None or not is_admin(update.effective_user.id):
                await query.answer("Только для админов", show_alert=True)
                return
            await upsert_week_control_message(context.bot, force_new=True)
            await query.answer("Неделя пересоздана")
            return
        if data.startswith("item:roll:"):
            item_id = int(data.split(":", 2)[2])
            user = {
                "user_id": update.effective_user.id,
                "username": f"@{update.effective_user.username}" if update.effective_user.username else None,
                "display_name": await resolve_display_name(context.bot, update.effective_user),
            }
            item = slots_service.roll_for_item(item_id, user)
            await week_control.upsert_week_control_message(context.bot)
            await query.answer(f"Бросок: {item['last_roll']['value']}", show_alert=False)
            return
    except NotFoundError as exc:
        await query.answer(str(exc), show_alert=True)
    except Exception as exc:
        logger.exception("Callback failed")
        await query.answer(str(exc), show_alert=True)


async def any_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.message.text is None:
        return
    if "der_ai_bot" not in update.message.text.lower():
        return
    if update.message.text.lower().endswith("реплей"):
        await report_frags(context)
        return
    if chat is None:
        await context.bot.send_message(update.message.chat.id, "AI disabled")
        return
    response = (await chat.send_message(update.message.text.lower())).text
    await context.bot.send_message(update.message.chat.id, response)


def main() -> None:
    init_db()
    slots_service.normalize_competitions()
    slots_service.create_or_get_active_week()
    now = datetime.datetime.now(TZ)
    if now.weekday() == 3 and (now.hour, now.minute) >= (9, 30):
        slots_service.auto_close_open_items()
        week_control.refresh_week_control_sync()
    start_web_server()

    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("app", command_app))
    application.add_handler(CommandHandler("week_init", command_week_init))
    application.add_handler(CommandHandler("week_reset", command_week_reset))
    application.add_handler(CommandHandler("slots", command_slots))
    application.add_handler(CommandHandler("undo_roll", command_undo_roll))
    application.add_handler(CommandHandler("logs", command_logs))
    application.add_handler(CommandHandler("tunnel_status", command_tunnel_status))
    application.add_handler(CommandHandler("tunnel_restart", command_tunnel_restart))
    application.add_handler(CallbackQueryHandler(handle_button))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, any_message))

    application.job_queue.run_repeating(create_poll, interval=60 * 60 * 24, first=get_scheduled_time(9, 0))
    application.job_queue.run_repeating(close_slots_job, interval=60 * 60 * 24, first=get_scheduled_time(9, 30))
    application.job_queue.run_repeating(init_week_job, interval=60 * 60 * 24, first=get_scheduled_time(9, 5))
    application.job_queue.run_repeating(refresh_week_job, interval=60 * 5, first=10)
    application.job_queue.run_repeating(report_frags, interval=60 * 60, first=5)
    application.run_polling()


if __name__ == "__main__":
    main()
