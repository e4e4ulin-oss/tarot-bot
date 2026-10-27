#!/usr/bin/env bash
# Заводит секрет служебного адреса прямо в .env и перезапускает бота.
#
# Служебный адрес (/api/ops/status и /api/ops/logs) открывается секретом из
# OPS_TOKEN. Придумывать его руками, копировать и вставлять — лишний шаг, на
# котором секрет успевает побывать в буфере обмена и в истории команд.
#
#   sudo bash /opt/tarot-bot/deploy/ops-token.sh         # завести, если его нет
#   sudo bash /opt/tarot-bot/deploy/ops-token.sh --show  # показать нынешний
#   sudo bash /opt/tarot-bot/deploy/ops-token.sh --new   # сменить на новый
#
# Повторный запуск без --new ничего не меняет: уже выданный секрет мог быть
# вписан в настройках, и молча его обнулить — значит сломать доступ.
set -euo pipefail

DIR="${TAROT_DIR:-/opt/tarot-bot}"
SERVICE="${TAROT_SERVICE:-tarot-bot}"
ENV_FILE="$DIR/.env"
MODE="keep"

while [ $# -gt 0 ]; do
  case "$1" in
    --new) MODE="new"; shift ;;
    --show) MODE="show"; shift ;;
    -*) printf 'неизвестный параметр: %s\n' "$1" >&2; exit 2 ;;
    *) ENV_FILE="$1"; shift ;;
  esac
done

[ -f "$ENV_FILE" ] || { echo "нет файла $ENV_FILE"; exit 1; }

current="$(sed -n 's/^OPS_TOKEN=//p' "$ENV_FILE" | tail -1)"

show() {
  echo
  echo "OPS_TOKEN=$1"
  echo
  echo "Впишите это значение в настройках окружения Claude:"
  echo "  Network secrets → хост вашего домена, заголовок X-Ops-Token"
  echo
}

if [ "$MODE" = "show" ]; then
  [ -n "$current" ] || { echo "секрет ещё не заведён — запустите без --show"; exit 1; }
  show "$current"
  exit 0
fi

if [ -n "$current" ] && [ "$MODE" = "keep" ]; then
  echo "Секрет уже заведён. Сменить: $0 --new"
  show "$current"
  exit 0
fi

token="$(openssl rand -hex 24)"

# Правим на месте, сохраняя права и владельца: .env читает служебный
# пользователь, и новый файл с чужими правами оставил бы бота без настроек
if grep -q '^OPS_TOKEN=' "$ENV_FILE"; then
  sed -i "s|^OPS_TOKEN=.*|OPS_TOKEN=$token|" "$ENV_FILE"
else
  printf '\n# Служебный адрес: /api/ops/status и /api/ops/logs, только чтение\nOPS_TOKEN=%s\n' \
    "$token" >> "$ENV_FILE"
fi

echo "Секрет записан в $ENV_FILE"

# Неудачный перезапуск не должен прятать секрет: он уже записан, и человеку
# нужно его увидеть, даже если службу придётся поднимать руками
if systemctl restart "$SERVICE" 2>/dev/null; then
  echo "Бот перезапущен — секрет уже действует"
else
  echo "Перезапустить не вышло: сделайте это сами, иначе секрет не подхватится"
  echo "  systemctl restart $SERVICE"
fi

show "$token"
