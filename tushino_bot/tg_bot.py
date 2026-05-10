import datetime
import logging
import os
import threading
from collections import defaultdict

import pytz
import uvicorn
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
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
ADMIN_USER_IDS = {
    int(x.strip())
    for x in os.environ.get("ADMIN_USER_IDS", "").split(",")
    if x.strip()
}

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


async def resolve_display_name(bot, user) -> str:
    fallback = user.first_name or user.full_name or (f"@{user.username}" if user.username else str(user.id))
    try:
        member = await bot.get_chat_member(chat_id=CHAT_ID, user_id=user.id)
        custom_title = getattr(member, "custom_title", None)
        if custom_title:
            return custom_title
    except Exception as exc:
        logger.warning("Could not resolve custom title for %s: %s", user.id, exc)
    return fallback


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
    if update.message is None:
        return
    if not WEBAPP_URL:
        await update.message.reply_text("WEBAPP_URL not set")
        return
    if update.effective_chat and update.effective_chat.type == "private":
        await update.message.reply_text(
            "Open slots app",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Open app", web_app=WebAppInfo(url=WEBAPP_URL))]]),
        )
        return
    username = BOT_USERNAME or (context.bot.username or "")
    if username:
        await update.message.reply_text(f"Open bot in PM: https://t.me/{username}")
    else:
        await update.message.reply_text("Open bot in private chat and run /app there")


async def command_week_init(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("Admin only")
        return
    await upsert_week_control_message(context.bot)
    await update.effective_message.reply_text("Week ready")


async def command_week_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("Admin only")
        return
    await upsert_week_control_message(context.bot, force_new=True)
    await update.effective_message.reply_text("Week rebuilt")


async def command_slots(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    week = slots_service.create_or_get_active_week()
    await update.effective_message.reply_text(week_control.build_week_text(week))


async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    data = query.data or ""
    try:
        if data == "week:refresh":
            await upsert_week_control_message(context.bot)
            await query.answer("Refreshed")
            return
        if data == "week:reset":
            if update.effective_user is None or not is_admin(update.effective_user.id):
                await query.answer("Admin only", show_alert=True)
                return
            await upsert_week_control_message(context.bot, force_new=True)
            await query.answer("Week rebuilt")
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
        if data.startswith("item:call:"):
            item_id = int(data.split(":", 2)[2])
            user = {
                "user_id": update.effective_user.id,
                "username": f"@{update.effective_user.username}" if update.effective_user.username else None,
                "display_name": await resolve_display_name(context.bot, update.effective_user),
            }
            item = slots_service.call_item(item_id, user)
            await week_control.upsert_week_control_message(context.bot)
            if item["call_result"] == "tiebreak":
                await query.answer("Ничья. Нужен переброс.", show_alert=False)
            else:
                winner = item["scores"][0]["display_name"] or item["scores"][0]["username"]
                await query.answer(f"Победил {winner}", show_alert=False)
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
    slots_service.create_or_get_active_week()
    start_web_server()

    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("app", command_app))
    application.add_handler(CommandHandler("week_init", command_week_init))
    application.add_handler(CommandHandler("week_reset", command_week_reset))
    application.add_handler(CommandHandler("slots", command_slots))
    application.add_handler(CallbackQueryHandler(handle_button))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, any_message))

    application.job_queue.run_repeating(create_poll, interval=60 * 60 * 24, first=get_scheduled_time(9, 0))
    application.job_queue.run_repeating(init_week_job, interval=60 * 60 * 24, first=get_scheduled_time(9, 5))
    application.job_queue.run_repeating(refresh_week_job, interval=60 * 5, first=10)
    application.job_queue.run_repeating(report_frags, interval=60 * 60, first=5)
    application.run_polling()


if __name__ == "__main__":
    main()
