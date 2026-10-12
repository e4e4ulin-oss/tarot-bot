#!/usr/bin/env bash
# Подтягивает свежий код из GitHub и перезапускает бота, если что-то изменилось.
#
# Нужен для правок с телефона: отправить коммит из приложения можно откуда
# угодно, а зайти по ssh и выполнить установщик — нет. Таймер systemd запускает
# этот скрипт раз в несколько минут, так что правка доезжает до сервера сама.
#
# Запуск вручную — короткая команда на сервере:
#
#   tarot-update
#
# Установщик кладёт копию в /usr/local/bin/tarot-update: таймер запускает именно
# её, чтобы обновление кода не перезаписывало скрипт прямо во время работы.
#
# Переменные окружения: TAROT_DIR, TAROT_SERVICE, TAROT_BRANCH.
set -euo pipefail

DIR="${1:-${TAROT_DIR:-/opt/tarot-bot}}"
SERVICE="${TAROT_SERVICE:-tarot-bot}"
BRANCH="${TAROT_BRANCH:-main}"

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1"; }

[ -d "$DIR/.git" ] || { log "в $DIR нет репозитория — обновлять нечего"; exit 1; }

# Каталог принадлежит служебному пользователю, а git работает от root и без
# этого отказывается его трогать. safe.directory читается только из глобального
# конфига, поэтому -c и переменные окружения здесь не сработают.
if ! git config --global --get-all safe.directory 2>/dev/null | grep -qxF "$DIR"; then
  git config --global --add safe.directory "$DIR"
fi

git -C "$DIR" fetch --quiet --depth 1 origin "$BRANCH"

current="$(git -C "$DIR" rev-parse HEAD)"
fresh="$(git -C "$DIR" rev-parse FETCH_HEAD)"

if [ "$current" = "$fresh" ]; then
  log "уже свежая версия (${current:0:7})"
  exit 0
fi

log "обновление ${current:0:7} → ${fresh:0:7}"

deps_before="$(git -C "$DIR" rev-parse 'HEAD:requirements.txt' 2>/dev/null || true)"
git -C "$DIR" reset --quiet --hard "$fresh"
deps_after="$(git -C "$DIR" rev-parse 'HEAD:requirements.txt' 2>/dev/null || true)"

if [ "$deps_before" != "$deps_after" ] && [ -x "$DIR/.venv/bin/pip" ]; then
  log "список зависимостей изменился — доустанавливаю"
  "$DIR/.venv/bin/pip" install -q -r "$DIR/requirements.txt"
fi

# Правка могла прийти с телефона и не проверяться локально. Синтаксис — самое
# частое, что при этом ломается, и ценой одной секунды мы не роняем рабочего
# бота: при ошибке откатываемся на прежнюю ревизию и не перезапускаем службу.
if [ -x "$DIR/.venv/bin/python" ] && [ -d "$DIR/bot" ]; then
  if ! "$DIR/.venv/bin/python" -m compileall -q "$DIR/bot" >/dev/null; then
    log "код не компилируется — откатываюсь на ${current:0:7}, бот продолжает работать"
    git -C "$DIR" reset --quiet --hard "$current"
    exit 1
  fi
fi

# Файлы после reset принадлежат root — возвращаем службе её каталог.
if id -u tarot >/dev/null 2>&1; then
  chown -R tarot: "$DIR"
fi

systemctl restart "$SERVICE"
log "бот перезапущен на ${fresh:0:7}"
