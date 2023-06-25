import datetime
import os
from telegram import Update
from telegram.ext import Application, MessageHandler, CallbackContext, ContextTypes
import pytz


now = datetime.datetime.now(pytz.timezone('Europe/Moscow'))
if now.hour < 12:
    scheduled_time = now + datetime.timedelta(hours=15-now.hour)
else:
    scheduled_time = now + datetime.timedelta(days=1)

print(scheduled_time)

TOKEN = os.environ['BOT_TOKEN']
CHAT_ID = os.environ['CHAT_ID']



async def create_poll(context) -> None:
    # По пятницам создаем опрос о посещении СГ
    now = datetime.datetime.now()

    if now.weekday() in [4]:
        await _create_poll(context)


async def _create_poll(context: ContextTypes) -> None:
    message = await context.bot.send_poll(
        CHAT_ID,
        "Играю...",
        ["Пт1", "Пт2", "Пт не играю", "Сб1", "Сб2", "Сб не играю"],
        allows_multiple_answers=True
    )
    await message.pin()


async def any_message(update: Update, context: ContextTypes) -> None:
    pass
    # print(update.message)
    # if update.message.text == 'опрос':
    #     await _create_poll(context)


def main():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(MessageHandler(None, any_message))
    application.job_queue.run_repeating(create_poll, interval=60*60*24, first=scheduled_time)
    application.run_polling()


if __name__ == '__main__':
    main()
