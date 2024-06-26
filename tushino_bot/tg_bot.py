import datetime
import os
from telegram import Update
from telegram.ext import Application, MessageHandler, CallbackContext, ContextTypes
import pytz
import random
import logging

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger


now = datetime.datetime.now(pytz.timezone('Europe/Moscow'))
if now.hour < 12:
    scheduled_time = now + datetime.timedelta(hours=15-now.hour)
else:
    scheduled_time = now + datetime.timedelta(days=1)
scheduled_time = now + datetime.timedelta(seconds=10)
logger().info(f'Создам опрос в {scheduled_time}')

TOKEN = os.environ['BOT_TOKEN']
CHAT_ID = os.environ['CHAT_ID']



async def create_poll(context) -> None:
    # По четвергам создаем опрос о посещении СГ
    now = datetime.datetime.now()

    if now.weekday() in [3]:
        await _create_poll(context)


async def _create_poll(context: ContextTypes) -> None:
    message = await context.bot.send_poll(
        CHAT_ID,
        "Играю...",
        ["Пт1", "Пт2", "Пт не играю", "Сб1", "Сб2", "Сб не играю"],
        allows_multiple_answers=True,
        is_anonymous=False,
    )
    await message.pin()

    message = await context.bot.send_poll(
        CHAT_ID,
        "Готов быть КО...",
        ["Пт1", "Пт2", "Сб1", "Сб2", "Буду пьян, кто КОшит то"],
        allows_multiple_answers=True,
        is_anonymous=False,
    )
    await message.pin()


async def any_message(update: Update, context: ContextTypes) -> None:
    if update.message is None:
        return
    CHAT_ID = update.message.chat.id
    print(update)
    if 'der_ai_bot' in update.message.text.lower():
        response = random.choice([
            'Работаю во благо ДЭРов...',
            'Всегда на службе',
            'Нужно больше золота',
            'Жизнь за Нерзулла',
            'Как скажешь друг',
            'Согласен',
            'Воистину',
            'DER-DER-DER!',
        ])
        await context.bot.send_message(CHAT_ID, response)
    else:
        pass


def main():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(MessageHandler(None, any_message))
    application.job_queue.run_repeating(create_poll, interval=60*60*24, first=scheduled_time)
    application.run_polling()


if __name__ == '__main__':
    main()
