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
    lines = [f"Неделя {week['week_key']}", ""]
    for slot in week["slots"]:
        lines.append(f"{slot['code']}:")
        if not slot["items"]:
            lines.append("- пусто")
            lines.append("")
            continue

        for item in slot["items"]:
            latest_roll = item.get("latest_roll")
            leader_text = ""
            if item["scores"]:
                top = item["scores"][0]
                top_name = top["display_name"] or top["username"]
                leader_text = f", лидер - {top_name} {top['best_value']}"

            latest_text = ""
            if latest_roll:
                latest_name = latest_roll["display_name"] or latest_roll["username"]
                latest_text = f", посл бросок - {latest_name} {latest_roll['value']}"

            if item["status"] == "called" and item["scores"]:
                top = item["scores"][0]
                winner = top["display_name"] or top["username"]
                line = f"- {item['name']}={winner}✅{leader_text}{latest_text}"
            elif item["status"] == "called":
                line = f"- {item['name']}=✅"
            elif item["status"] == "tiebreak":
                line = f"- {item['name']}=переброс{leader_text}{latest_text}"
            else:
                line = f"- {item['name']}={len(item['scores'])}🎲{leader_text}{latest_text}"

            lines.append(line)
        lines.append("")
    return "\n".join(lines).strip()


def build_week_keyboard(week: dict) -> InlineKeyboardMarkup:
    rows = []
    if BOT_USERNAME:
        rows.append([InlineKeyboardButton("Открыть бота", url=f"https://t.me/{BOT_USERNAME}")])
    for slot in week["slots"]:
        for item in slot["items"]:
            label = f"{slot['code']}-{item['name']}"
            if len(label) > 24:
                label = label[:24]
            rows.append([
                InlineKeyboardButton(f"🎲 {label}", callback_data=f"item:roll:{item['id']}"),
            ])
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
            tb = f"/{score['tiebreak_value']}" if score['tiebreak_value'] is not None else ""
            lines.append(f"  {idx}) {name} {score['best_value']}{tb}{suffix}")
        if len(item["scores"]) > 3:
            lines.append(f"  … еще {len(item['scores']) - 3}")
    return "\n".join(lines).strip()


async def upsert_week_control_message(bot: Bot, force_new: bool = False) -> None:
    week = slots_service.reset_active_week() if force_new else slots_service.create_or_get_active_week()
    text = build_week_text(week)
    keyboard = build_week_keyboard(week)
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



def refresh_week_control_sync(force_new: bool = False) -> None:
    if not BOT_TOKEN:
        return
    asyncio.run(upsert_week_control_message(Bot(BOT_TOKEN), force_new=force_new))
