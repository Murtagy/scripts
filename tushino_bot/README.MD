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
- `/tunnel_status` — статус Cloudflare Tunnel
- `/tunnel_restart` — перезапустить Cloudflare Tunnel и обновить URL/menu button
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

## Деплой после коммитов

Если код уже крутится на VPS и проект лежит в `/http/scripts/tushino_bot`, после `git push` можно обновить так:

```bash
ssh wg
cd /http/scripts/tushino_bot
git pull --ff-only
pkill -f "python3 tg_bot.py" || true
./run.sh >/dev/null 2>&1 </dev/null &
```

Потом проверить:

```bash
ps aux | grep -E "python3 tg_bot.py" | grep -v grep
ss -ltnp | grep ":8000"
```

Если менялся текст weekly message / кнопки, можно принудительно перерисовать:

```bash
cd /http/scripts/tushino_bot
source environment/bin/activate
source token.sh
python3 - <<'PY'
from db import init_db
import week_control
init_db()
week_control.refresh_week_control_sync()
print("refreshed")
PY
```

Если менялись команды меню бота, можно перевыставить их:

```bash
cd /http/scripts/tushino_bot
source environment/bin/activate
source token.sh
python3 - <<'PY'
import os, requests
url = "https://api.telegram.org/bot" + os.environ["BOT_TOKEN"] + "/setMyCommands"
cmds = {
  "commands": [
    {"command": "app", "description": "Открыть приложение слотов"},
    {"command": "slots", "description": "Показать слоты недели"},
    {"command": "undo_roll", "description": "Отменить последний бросок"},
    {"command": "logs", "description": "Показать журнал действий"},
    {"command": "tunnel_status", "description": "Статус Cloudflare Tunnel"},
    {"command": "tunnel_restart", "description": "Перезапустить Cloudflare Tunnel"},
    {"command": "week_init", "description": "Обновить сообщения недели"},
    {"command": "week_reset", "description": "Пересоздать неделю"}
  ]
}
print(requests.post(url, json=cmds, timeout=30).json())
PY
```

Если miniapp перестал открываться из-за Quick Tunnel:

```text
/app
/tunnel_status
/tunnel_restart
```

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
- При ничьей бот сам выбирает победителя при закрытии итогов
- Итоги по слотам закрываются автоматически через 30 минут после опроса о посещении
- old replay/AI functionality сохранена и работает параллельно
- для локального дебага webapp без Telegram initData можно временно выставить:

```bash
export ALLOW_INSECURE_WEBAPP=1
```

Не включать это в production.
