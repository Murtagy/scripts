# tushino_bot

Бот для Telegram-чата.

Что умеет сейчас:
- опросы на игры
- парсинг реплеев и фрагов
- weekly playable items для `пт1`, `пт2`, `сб1`, `сб2`
- web app для добавления айтемов, ролла, call winner, reset week

## Установка

```bash
python3 -m venv env
source env/bin/activate
pip install -r requirements.txt
```

## Нужные env vars

```bash
export BOT_TOKEN=...
export CHAT_ID=...
export PLAYABLE_THREAD_ID=54606
export WEBAPP_URL=https://t.me/<bot_username>/<app_short_name>
```

Опционально:

```bash
export AI_KEY=...
export ADMIN_USER_IDS=123,456
export WEBAPP_HOST=127.0.0.1
export WEBAPP_PORT=8000
export BOT_DB_PATH=bot.sqlite3
export ALLOW_INSECURE_WEBAPP=0
```

## Запуск

```bash
python3 tg_bot.py
```

`tg_bot.py` поднимает:
- Telegram bot
- FastAPI app на `WEBAPP_HOST:WEBAPP_PORT`

## Команды

- `/app` — ссылка на app
- `/slots` — summary недели
- `/week_init` — создать/обновить неделю
- `/week_reset` — пересоздать неделю

## Notes

- `WEBAPP_URL` лучше дать как Telegram Mini App link (`t.me/...`), не просто raw HTTPS URL.
- Если нужен локальный дебаг web app без Telegram initData: `ALLOW_INSECURE_WEBAPP=1`.
