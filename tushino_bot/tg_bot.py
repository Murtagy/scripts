import datetime
import os
from telegram import Update
from telegram.ext import Application, MessageHandler, CallbackContext, ContextTypes
import pytz
import random
import logging
import replays
from collections import defaultdict

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger

# Set httpx logger level to WARNING (or ERROR)
logging.getLogger("httpx").setLevel(logging.WARNING)


now = datetime.datetime.now(pytz.timezone('Europe/Moscow'))

scheduled_time = now.replace(hour=9, minute=0, second=0, microsecond=0)
if now.hour >= 9:
    scheduled_time += datetime.timedelta(days=1)
logger().info(f'Создам опрос в {scheduled_time}')

TOKEN = os.environ['BOT_TOKEN']
AI_KEY = os.environ['AI_KEY']
CHAT_ID = os.environ['CHAT_ID']



async def create_poll(context) -> None:
    # По четвергам создаем опрос о посещении СГ
    now = datetime.datetime.now()

    if now.weekday() in [3]:
        await _create_poll(context)


async def report_frags(context) -> None:
    # ищем новые фраги и пуляем их в канал
    now = datetime.datetime.now(pytz.timezone('Europe/Moscow'))
    if now.hour >= 21 and now.hour <= 23:
        # до 12 ночи реплеи недоступны
        return None
    new_frags, parsed_games = replays.collect_new_frags()
    new_frags = [f for f in new_frags if f.killer.startswith('[DER]')]
    if len(new_frags) == 0:
        return

    message: list[str] = []

    by_game = defaultdict(list)
    for f in new_frags:
        by_game[f.mission].append(f)

    for mission, frags in by_game.items():
        message.append(f'Игра {mission}:')
        for f in frags:
            message.append(' ' + str(f))
        message.append('')
        message.append('')

    m = '\n'.join(message)
    await context.bot.send_message(CHAT_ID, m)
    response = (await chat.send_message('Вот фраги с последней игры, прокомментируй. Если там больше 4 - будь позитивен пожалуйста, это хороший результат:' + m)).text
    await context.bot.send_message(CHAT_ID, response)


async def _create_poll(context: ContextTypes) -> None:
    message = await context.bot.send_poll(
        CHAT_ID,
        "Играю...",
        ["Пт1", "Пт2", "Пт не играю", "Сб1", "Сб2", "Сб не играю"],
        allows_multiple_answers=True,
        is_anonymous=False,
        message_thread_id=54606
    )
    await message.pin()

    message = await context.bot.send_poll(
        CHAT_ID,
        "Готов быть КО...",
        ["Пт1", "Пт2", "Сб1", "Сб2", "Буду пьян, кто КОшит то"],
        allows_multiple_answers=True,
        is_anonymous=False,
        message_thread_id=54606
    )
    await message.pin()


import os
from google import genai

client = genai.Client(api_key=AI_KEY)

# Create the model
generation_config = {
  "temperature": 1,
  "top_p": 0.95,
  "top_k": 40,
  "max_output_tokens": 8192,
  "response_mime_type": "text/plain",
  "system_instruction": "Reply in Russian with sarcastic tone. You are replyingin the in-game squad channel or Arma3. Joke something about the players not being able to kill somebody or that you will never stop defending the trigger(main base). Be playful. Make jokes about Vaven and spades he uses for trenches. Jolywitz and his ability to die in vehicles. Nunel and Dota2",
}

# model = genai.GenerativeModel(
#   model_name="gemini-2.0-flash",
#   generation_config=generation_config,
#   system_instruction=
# )
chat = client.aio.chats.create(model='gemini-2.0-flash-001', config=generation_config)


async def any_message(update: Update, context: ContextTypes) -> None:
    if update.message is None:
        return
    CHAT_ID = update.message.chat.id
    if 'der_ai_bot' in update.message.text.lower():
        response = (await chat.send_message(update.message.text.lower())).text
        
        # response = random.choice([
        #     'Работаю во благо ДЭРов...',
        #     'Всегда на службе',
        #     'Нужно больше золота',
        #     'Жизнь за Нерзулла',
        #     'Как скажешь друг',
        #     'Согласен',
        #     'Воистину',
        #     'DER-DER-DER!',
        # ])
        await context.bot.send_message(CHAT_ID, response)
    else:
        pass


def main():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(MessageHandler(None, any_message))
    application.job_queue.run_repeating(create_poll, interval=60*60*24, first=scheduled_time)
    application.job_queue.run_repeating(report_frags, interval=60*60*1, first=now + datetime.timedelta(seconds=5))
    application.run_polling()


if __name__ == '__main__':
    main()
