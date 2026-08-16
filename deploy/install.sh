#!/usr/bin/env bash
# Установка таро-бота на чистый VPS (Ubuntu/Debian).
#
#   git clone https://github.com/e4e4ulin-oss/tarot-bot
#   cd tarot-bot
#   sudo ./deploy/install.sh
#
# Скрипт ставит Docker (если его нет), спрашивает токен и ключи, поднимает бота
# и настраивает автозапуск после перезагрузки сервера. Повторный запуск безопасен.
set -euo pipefail

cd "$(dirname "$0")/.."
PROJECT_DIR="$(pwd)"
ENV_FILE="$PROJECT_DIR/.env"

log() { printf '\n\033[1m→ %s\033[0m\n' "$1"; }
fail() { printf '\n\033[31m%s\033[0m\n' "$1" >&2; exit 1; }

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  command -v sudo >/dev/null || fail "Нужны права root: запустите через sudo."
  SUDO="sudo"
fi

# --- .env: значение по ключу, без возни с экранированием -----------------------
set_env() {
  local key="$1" value="$2" tmp
  tmp="$(mktemp)"
  grep -v "^${key}=" "$ENV_FILE" > "$tmp" || true
  printf '%s=%s\n' "$key" "$value" >> "$tmp"
  mv "$tmp" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
}

get_env() {
  grep "^$1=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- || true
}

ask() {
  local prompt="$1" current="$2" answer
  if [ -n "$current" ]; then
    read -rp "$prompt [оставить текущее — Enter]: " answer || true
    printf '%s' "${answer:-$current}"
  else
    read -rp "$prompt: " answer || true
    printf '%s' "$answer"
  fi
}

# --- Docker --------------------------------------------------------------------
if ! command -v docker >/dev/null; then
  log "Ставлю Docker…"
  $SUDO apt-get update -qq
  $SUDO apt-get install -y -qq curl ca-certificates
  curl -fsSL https://get.docker.com | $SUDO sh
else
  log "Docker уже установлен: $(docker --version)"
fi

if ! docker compose version >/dev/null 2>&1; then
  fail "Не найден плагин docker compose. Установите docker-compose-plugin и запустите скрипт снова."
fi

# --- Настройки -----------------------------------------------------------------
[ -f "$ENV_FILE" ] || cp "$PROJECT_DIR/.env.example" "$ENV_FILE"
chmod 600 "$ENV_FILE"

if [ ! -t 0 ]; then
  # Неинтерактивный запуск: проверяем, что токен уже вписан руками
  token="$(get_env BOT_TOKEN)"
  case "$token" in
    ""|123456:AA*) fail "Впишите BOT_TOKEN в $ENV_FILE и запустите скрипт снова." ;;
  esac
else
  log "Настройки (Enter — пропустить необязательное)"
  echo "Токен берётся у @BotFather, ключ Grok — на https://console.x.ai"
  echo

  token="$(ask 'Токен бота (BOT_TOKEN)' "$(get_env BOT_TOKEN)")"
  [ -n "$token" ] || fail "Без токена бот не запустится."
  set_env BOT_TOKEN "$token"

  xai="$(ask 'Ключ Grok (XAI_API_KEY, можно позже)' "$(get_env XAI_API_KEY)")"
  set_env XAI_API_KEY "$xai"

  echo
  echo "ADMIN_CHAT_ID и ADMIN_IDS можно заполнить после первого запуска:"
  echo "напишите боту /chatid — он пришлёт оба числа."
  admin_chat="$(ask 'ADMIN_CHAT_ID (куда падают заявки)' "$(get_env ADMIN_CHAT_ID)")"
  set_env ADMIN_CHAT_ID "$admin_chat"
  admin_ids="$(ask 'ADMIN_IDS (кто отвечает на заявки)' "$(get_env ADMIN_IDS)")"
  set_env ADMIN_IDS "$admin_ids"
fi

# --- Запуск --------------------------------------------------------------------
log "Собираю и запускаю контейнер…"
$SUDO docker compose up -d --build

sleep 3
log "Состояние:"
$SUDO docker compose ps

cat <<INFO

Готово. Бот запущен и поднимется сам после перезагрузки сервера
(restart: unless-stopped в docker-compose.yml).

Что дальше:
  1. Напишите боту /start в Telegram — проверьте, что отвечает.
  2. Отправьте /chatid, впишите числа в $ENV_FILE
     (ADMIN_CHAT_ID и ADMIN_IDS) и выполните: docker compose restart

Полезные команды (из каталога $PROJECT_DIR):
  docker compose logs -f       логи бота
  docker compose restart       перезапустить после правки .env
  docker compose down          остановить
  git pull && docker compose up -d --build    обновить до новой версии

INFO
