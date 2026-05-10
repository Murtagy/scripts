import asyncio
import os

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

import slots_service

CHAT_ID = os.environ.get("CHAT_ID")
THREAD_ID = int(os.environ.get("PLAYABLE_THREAD_ID", "54606"))
WEBAPP_URL = os.environ.get("WEBAPP_URL", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "")


def item_display_name(score: dict) -> str:
    return score["display_name"] or score["username"] or str(score["user_id"])


def build_week_text(week: dict) -> str:
    lines = [f"Неделя {week['week_key']}"]
    for slot in week["slots"]:
        if not slot["items"]:
            lines.append(f"{slot['code']}: пусто")
            continue
        parts = []
        for item in slot["items"]:
            if item["status"] == "called" and item["scores"]:
                top = item["scores"][0]
                winner = top["display_name"] or top["username"]
                parts.append(f"{item['name']}={winner} {top['best_value']}✅")
            elif item["status"] == "tiebreak":
                parts.append(f"{item['name']}=переброс")
            else:
                parts.append(f"{item['name']}={len(item['scores'])}🎲")
        lines.append(f"{slot['code']}: " + "; ".join(parts))
    return "\n".join(lines).strip()


def build_week_keyboard() -> InlineKeyboardMarkup:
    rows = []
    if BOT_USERNAME:
        rows.append([InlineKeyboardButton("Open bot", url=f"https://t.me/{BOT_USERNAME}")])
    rows.append([InlineKeyboardButton("Refresh", callback_data="week:refresh")])
    rows.append([InlineKeyboardButton("Rebuild", callback_data="week:reset")])
    return InlineKeyboardMarkup(rows)


def build_slot_text(slot: dict) -> str:
    lines = [slot['code']]
    if not slot["items"]:
        return f"{slot['code']}: пусто"
    for item in slot["items"]:
        if item["status"] == "tiebreak" and item["tied_display_names"]:
            status = "переброс: " + ", ".join(item["tied_display_names"])
        elif item["status"] == "called" and item["scores"]:
            top = item["scores"][0]
            status = f"победил {item_display_name(top)} {top['best_value']}✅"
        elif item["scores"]:
            status = f"{len(item['scores'])} брос."
        else:
            status = "без бросков"
        lines.append(f"• {item['name']} — {status}")
        for idx, score in enumerate(item["scores"][:3], start=1):
            name = item_display_name(score)
            suffix = "✅" if item["status"] == "called" and idx == 1 else ""
            tb = f"/{score['tiebreak_value']}" if score["tiebreak_value"] is not None else ""
            lines.append(f"  {idx}) {name} {score['best_value']}{tb}{suffix}")
        if len(item["scores"]) > 3:
            lines.append(f"  … еще {len(item['scores']) - 3}")
    return "\n".join(lines).strip()


def build_slot_keyboard(slot: dict) -> InlineKeyboardMarkup:
    rows = []
    for item in slot["items"]:
        label = item["name"]
        if len(label) > 18:
            label = label[:15] + "..."
        rows.append([
            InlineKeyboardButton(f"🎲 {label}", callback_data=f"item:roll:{item['id']}"),
            InlineKeyboardButton(f"🏁 {label}", callback_data=f"item:call:{item['id']}"),
        ])
    return InlineKeyboardMarkup(rows)


async def upsert_week_control_message(bot: Bot, force_new: bool = False) -> None:
    week = slots_service.reset_active_week() if force_new else slots_service.create_or_get_active_week()
    text = build_week_text(week)
    keyboard = build_week_keyboard()
    existing = slots_service.get_control_message(week["id"])
    if existing:
        try:
            await bot.edit_message_text(
                chat_id=existing["chat_id"],
                message_id=existing["message_id"],
                text=text,
                reply_markup=keyboard,
            )
        except Exception as exc:
            if "message is not modified" not in str(exc).lower() and CHAT_ID:
                message = await bot.send_message(
                    CHAT_ID,
                    text,
                    reply_markup=keyboard,
                    message_thread_id=THREAD_ID,
                )
                slots_service.save_control_message(week["id"], str(CHAT_ID), THREAD_ID, message.message_id)
    elif CHAT_ID:
        message = await bot.send_message(
            CHAT_ID,
            text,
            reply_markup=keyboard,
            message_thread_id=THREAD_ID,
        )
        slots_service.save_control_message(week["id"], str(CHAT_ID), THREAD_ID, message.message_id)

    for slot in week["slots"]:
        await upsert_slot_message(bot, week, slot)


async def upsert_slot_message(bot: Bot, week: dict, slot: dict) -> None:
    text = build_slot_text(slot)
    keyboard = build_slot_keyboard(slot)
    kind = f"slot:{slot['code']}"
    existing = slots_service.get_bot_message(week["id"], kind)
    if existing:
        try:
            await bot.edit_message_text(
                chat_id=existing["chat_id"],
                message_id=existing["message_id"],
                text=text,
                reply_markup=keyboard,
            )
            return
        except Exception as exc:
            if "message is not modified" in str(exc).lower():
                return
    if not CHAT_ID:
        return
    message = await bot.send_message(
        CHAT_ID,
        text,
        reply_markup=keyboard,
        message_thread_id=THREAD_ID,
    )
    slots_service.save_bot_message(week["id"], kind, str(CHAT_ID), THREAD_ID, message.message_id)


def refresh_week_control_sync(force_new: bool = False) -> None:
    if not BOT_TOKEN:
        return
    asyncio.run(upsert_week_control_message(Bot(BOT_TOKEN), force_new=force_new))
