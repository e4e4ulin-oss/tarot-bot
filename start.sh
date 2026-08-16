#!/usr/bin/env bash
# Запуск бота одной командой: ./start.sh
# Создаёт виртуальное окружение, ставит зависимости и стартует бота.
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "→ Создаю виртуальное окружение…"
  python3 -m venv .venv
fi

echo "→ Проверяю зависимости…"
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  echo
  echo "Создан файл .env — впишите в него BOT_TOKEN и запустите ./start.sh снова."
  echo "Файл лежит здесь: $(pwd)/.env"
  exit 1
fi

if grep -qE '^BOT_TOKEN=\s*$|^BOT_TOKEN=123456:AA' .env; then
  echo
  echo "В .env не заполнен BOT_TOKEN. Откройте $(pwd)/.env и вставьте токен от @BotFather."
  exit 1
fi

echo "→ Запускаю бота. Остановить — Ctrl+C."
exec .venv/bin/python -m bot
