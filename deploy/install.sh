#!/usr/bin/env bash
# Установка таро-бота на чистый VPS (Ubuntu/Debian).
#
# Одной командой, прямо на сервере:
#
#   curl -fsSL https://raw.githubusercontent.com/e4e4ulin-oss/tarot-bot/main/deploy/install.sh \
#     | sudo bash -s -- --token ТОКЕН_ОТ_BOTFATHER
#
# Или из уже склонированного репозитория:
#
#   git clone https://github.com/e4e4ulin-oss/tarot-bot && cd tarot-bot
#   sudo ./deploy/install.sh
#
# Скрипт ставит git и Docker (если их нет), скачивает код, создаёт .env,
# поднимает бота и включает автозапуск после перезагрузки сервера.
# Повторный запуск безопасен: код обновится, настройки сохранятся.
#
# Параметры (все необязательные, кроме --token при неинтерактивном запуске):
#   --token ЗНАЧЕНИЕ        токен бота от @BotFather
#   --xai ЗНАЧЕНИЕ          ключ Grok с console.x.ai
#   --admin-chat ЗНАЧЕНИЕ   чат, куда падают заявки на авторский разбор
#   --admin-ids ЗНАЧЕНИЕ    id тех, кто отвечает на заявки (через запятую)
#   --dir ПУТЬ              куда установить (по умолчанию /opt/tarot-bot)
set -euo pipefail

REPO_URL="https://github.com/e4e4ulin-oss/tarot-bot"
TARGET_DIR="${TAROT_DIR:-/opt/tarot-bot}"

ORIG_ARGS=("$@")
ARG_TOKEN=""
ARG_XAI=""
ARG_ADMIN_CHAT=""
ARG_ADMIN_IDS=""

log() { printf '\n\033[1m→ %s\033[0m\n' "$1"; }
fail() { printf '\n\033[31m%s\033[0m\n' "$1" >&2; exit 1; }

usage() {
  sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
  case "$1" in
    -t|--token) ARG_TOKEN="${2:-}"; shift 2 ;;
    --xai|--grok) ARG_XAI="${2:-}"; shift 2 ;;
    --admin-chat) ARG_ADMIN_CHAT="${2:-}"; shift 2 ;;
    --admin-ids) ARG_ADMIN_IDS="${2:-}"; shift 2 ;;
    --dir) TARGET_DIR="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) fail "Неизвестный параметр: $1 (--help — список)" ;;
  esac
done

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  command -v sudo >/dev/null || fail "Нужны права root: запустите через sudo."
  SUDO="sudo"
fi

apt_install() {
  $SUDO apt-get update -qq
  $SUDO apt-get install -y -qq "$@"
}

# --- Где лежит проект ----------------------------------------------------------
# Скрипт может быть запущен и из репозитория, и напрямую через curl | bash.
PROJECT_DIR=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  candidate="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd || true)"
  [ -n "$candidate" ] && [ -f "$candidate/docker-compose.yml" ] && PROJECT_DIR="$candidate"
fi

if [ -z "$PROJECT_DIR" ]; then
  # Запуск через curl: скачиваем код и передаём управление скрипту из репозитория
  command -v git >/dev/null || { log "Ставлю git…"; apt_install git ca-certificates; }

  if [ -d "$TARGET_DIR/.git" ]; then
    log "Обновляю код в $TARGET_DIR…"
    $SUDO git -C "$TARGET_DIR" fetch --depth 1 origin main
    $SUDO git -C "$TARGET_DIR" reset --hard origin/main
  else
    log "Скачиваю код в $TARGET_DIR…"
    $SUDO git clone --depth 1 "$REPO_URL" "$TARGET_DIR"
  fi

  exec $SUDO bash "$TARGET_DIR/deploy/install.sh" \
    ${ORIG_ARGS[@]+"${ORIG_ARGS[@]}"} --dir "$TARGET_DIR"
fi

ENV_FILE="$PROJECT_DIR/.env"

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
  apt_install curl ca-certificates
  curl -fsSL https://get.docker.com | $SUDO sh
else
  log "Docker уже установлен: $(docker --version)"
fi

if ! docker compose version >/dev/null 2>&1; then
  fail "Не найден плагин docker compose. Установите docker-compose-plugin и запустите скрипт снова."
fi

# --- Настройки -----------------------------------------------------------------
[ -f "$ENV_FILE" ] || $SUDO cp "$PROJECT_DIR/.env.example" "$ENV_FILE"
$SUDO chmod 600 "$ENV_FILE"

[ -n "$ARG_TOKEN" ] && set_env BOT_TOKEN "$ARG_TOKEN"
[ -n "$ARG_XAI" ] && set_env XAI_API_KEY "$ARG_XAI"
[ -n "$ARG_ADMIN_CHAT" ] && set_env ADMIN_CHAT_ID "$ARG_ADMIN_CHAT"
[ -n "$ARG_ADMIN_IDS" ] && set_env ADMIN_IDS "$ARG_ADMIN_IDS"

token="$(get_env BOT_TOKEN)"
case "$token" in
  ""|123456:AA*) token="" ;;
esac

if [ -z "$token" ]; then
  if [ -t 0 ]; then
    log "Настройки (Enter — пропустить необязательное)"
    echo "Токен берётся у @BotFather, ключ Grok — на https://console.x.ai"
    echo

    token="$(ask 'Токен бота (BOT_TOKEN)' '')"
    [ -n "$token" ] || fail "Без токена бот не запустится."
    set_env BOT_TOKEN "$token"

    set_env XAI_API_KEY "$(ask 'Ключ Grok (XAI_API_KEY, можно позже)' "$(get_env XAI_API_KEY)")"

    echo
    echo "ADMIN_CHAT_ID и ADMIN_IDS можно заполнить после первого запуска:"
    echo "напишите боту /chatid — он пришлёт оба числа."
    set_env ADMIN_CHAT_ID "$(ask 'ADMIN_CHAT_ID (куда падают заявки)' "$(get_env ADMIN_CHAT_ID)")"
    set_env ADMIN_IDS "$(ask 'ADMIN_IDS (кто отвечает на заявки)' "$(get_env ADMIN_IDS)")"
  else
    fail "Не задан токен бота. Добавьте к команде: --token ТОКЕН_ОТ_BOTFATHER"
  fi
fi

# --- Запуск --------------------------------------------------------------------
log "Собираю и запускаю контейнер…"
cd "$PROJECT_DIR"
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
