cd /http/scripts/tushino_bot
exec &>> logs.log
source environment/bin/activate
source token.sh
python3 tg_bot.py &
