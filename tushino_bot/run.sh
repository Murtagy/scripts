#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
exec &>> logs.log

if [[ -f env/bin/activate ]]; then
  source env/bin/activate
elif [[ -f environment/bin/activate ]]; then
  source environment/bin/activate
fi

if [[ -f token.sh ]]; then
  source token.sh
fi

python3 tg_bot.py
