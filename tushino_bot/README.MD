# tushino_bot

Telegram-бот для squad/chat.

Что умеет:
- weekly polls на игры
- парсинг реплеев и фрагов
- AI-комментарии к фрагам
- weekly playable items для `пт1`, `пт2`, `сб1`, `сб2`
- броски за айтемы прямо из треда кнопками `🎲`
- private miniapp для управления айтемами и week state

## Архитектура

Один процесс `tg_bot.py` поднимает:
- Telegram bot
- FastAPI app на `127.0.0.1:8000` по умолчанию

Публичный доступ к miniapp лучше давать через:
- Cloudflare Tunnel
- или свой домен + reverse proxy

Сейчас логика такая:
- в треде живет одно основное сообщение недели
- в нем summary + кнопки бросков
- управление (`/app`, `/week_init`, `/week_reset`) — в private chat с ботом
- private menu/webapp доступны только участникам целевого чата

## Установка

Локально:

```bash
python3 -m venv env
source env/bin/activate
pip install -r requirements.txt
```

На сервере уже может использоваться `environment/` вместо `env/` — `run.sh` умеет оба варианта.

## Переменные окружения

Минимум:

```bash
export BOT_TOKEN=...
export CHAT_ID=-100...
export PLAYABLE_THREAD_ID=54606
export BOT_USERNAME=DER_AI_bot
export WEBAPP_URL=https://<public-https-url>
```

Опционально:

```bash
export AI_KEY=...
export ADMIN_USER_IDS=123456789,987654321
export WEBAPP_HOST=127.0.0.1
export WEBAPP_PORT=8000
export BOT_DB_PATH=bot.sqlite3
export ALLOW_INSECURE_WEBAPP=0
```

Где:
- `CHAT_ID` — id целевого supergroup/channel discussion chat
- `PLAYABLE_THREAD_ID` — id topic/thread, куда бот шлет polls и weekly message
- `BOT_USERNAME` — username бота без `https://t.me/`
- `WEBAPP_URL` — публичный HTTPS URL miniapp

## token.sh

Обычно удобно держать env в `token.sh`:

```bash
export BOT_TOKEN=...
export CHAT_ID=-100...
export PLAYABLE_THREAD_ID=54606
export BOT_USERNAME=DER_AI_bot
export WEBAPP_URL=https://<public-https-url>
export AI_KEY=...
export ADMIN_USER_IDS=123456789
```

`token.sh` в git не хранится.

## Запуск

Прямо:

```bash
source env/bin/activate
source token.sh
python3 tg_bot.py
```

Или через helper script:

```bash
bash ./run.sh
```

`run.sh`:
- переходит в директорию проекта
- активирует `env/` или `environment/`
- source `token.sh`, если есть
- пишет в `logs.log`
- запускает `python3 tg_bot.py`

## Команды бота

В private chat с ботом:
- `/app` — открыть miniapp
- `/slots` — summary недели
- `/undo_roll` — отменить свой последний бросок за текущую неделю
- `/logs [N]` — показать последние действия, по умолчанию 20
- `/week_init` — перерисовать/обновить week message
- `/week_reset` — пересоздать неделю

Важно:
- команды menu/private доступны только участникам целевого чата
- `week_init/week_reset` дополнительно ограничены `ADMIN_USER_IDS`

## Cloudflare Quick Tunnel

Если домена нет, для MVP можно поднять временный HTTPS URL так:

```bash
cloudflared tunnel --protocol http2 --no-autoupdate --url http://127.0.0.1:8000
```

В логах будет URL вида:

```bash
https://something.trycloudflare.com
```

Его надо положить в:

```bash
export WEBAPP_URL=https://something.trycloudflare.com
```

Потом перезапустить бота.

Минусы Quick Tunnel:
- URL временный
- может поменяться после рестарта
- не production-grade

Для постоянной схемы лучше:
- свой домен
- named Cloudflare Tunnel
- или обычный reverse proxy + HTTPS

## Проверка

Локальная health-check ручка:

```bash
curl http://127.0.0.1:8000/health
```

Ожидаемо:

```json
{"ok": true}
```

## Поведение miniapp

- WebApp доступен только из private chat/menu бота
- Telegram inline `web_app` button в group thread не работает
- поэтому в треде только кнопки `🎲`
- управление week/item state — через private bot/menu/webapp

## Notes

- Броски сейчас `1..6`
- Один пользователь может бросить один раз на один айтем
- При ничьей после завершения нужен переброс
- old replay/AI functionality сохранена и работает параллельно
- для локального дебага webapp без Telegram initData можно временно выставить:

```bash
export ALLOW_INSECURE_WEBAPP=1
```

Не включать это в production.
