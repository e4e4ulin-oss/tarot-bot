#!/usr/bin/env bash
# Ставит nginx перед ботом: https для мини-приложения и его API.
#
# Страница открыта по https, а браузер не выпустит запрос с https-страницы на
# http-адрес. Значит у сервера должен быть сертификат, а для сертификата нужно
# имя: по голому адресу его не выдают. Бесплатного поддомена для этого хватает.
#
# Что получится: по https отдаётся папка miniapp, а всё, что начинается с /api,
# уходит боту на локальный порт. Одно имя на страницу и на API — значит никакого
# CORS и никаких отдельных настроек в браузере.
#
#   sudo bash deploy/web.sh taroautorasklad.duckdns.org you@example.com
#
# Второй аргумент — почта для Let's Encrypt: туда придёт письмо, если
# сертификат вдруг перестанет продлеваться. Можно опустить.
set -euo pipefail

DOMAIN="${1:-}"
EMAIL="${2:-}"
DIR="${TAROT_DIR:-/opt/tarot-bot}"
PORT="${TAROT_WEB_PORT:-8081}"

[ -n "$DOMAIN" ] || { echo "укажите имя: sudo bash deploy/web.sh ИМЯ.duckdns.org"; exit 2; }
[ "$(id -u)" -eq 0 ] || { echo "нужно от root: sudo bash deploy/web.sh $DOMAIN"; exit 2; }

log() { printf '%s %s\n' "$(date '+%H:%M:%S')" "$1"; }

# Проверяем имя до установки: certbot всё равно откажет, но позже и непонятнее
here="$(curl -fsS --max-time 10 https://api.ipify.org || true)"
there="$(getent hosts "$DOMAIN" | awk '{print $1; exit}' || true)"
if [ -n "$here" ] && [ -n "$there" ] && [ "$here" != "$there" ]; then
  echo "«$DOMAIN» ведёт на $there, а сервер — $here."
  echo "Поправьте адрес в DuckDNS и подождите пару минут: сертификат выдают только хозяину имени."
  exit 1
fi
[ -n "$there" ] || { echo "«$DOMAIN» пока никуда не ведёт — создайте поддомен в DuckDNS"; exit 1; }

log "ставлю nginx и certbot"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq nginx certbot python3-certbot-nginx >/dev/null

log "настраиваю сайт $DOMAIN"
cat > /etc/nginx/sites-available/tarot <<CONF
server {
    listen 80;
    server_name $DOMAIN;

    # Колода и её картинки лежат файлами — отдаёт их nginx, бота не трогаем
    root $DIR/miniapp;
    index index.html;

    # Поисковикам тут делать нечего: это приложение, а не сайт
    add_header X-Robots-Tag "noindex" always;

    location /api/ {
        proxy_pass http://127.0.0.1:$PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        # Разбор пишется моделью до минуты: обычные 60 секунд коротковаты
        proxy_read_timeout 120s;
    }

    location / {
        try_files \$uri \$uri/ =404;
    }
}
CONF

ln -sf /etc/nginx/sites-available/tarot /etc/nginx/sites-enabled/tarot
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

log "запрашиваю сертификат"
if [ -n "$EMAIL" ]; then
  certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL" --redirect
else
  certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --register-unsafely-without-email --redirect
fi

# Продление certbot ставит себе сам таймером, но проверить дешевле, чем узнать
# об этом через три месяца по упавшему приложению
systemctl list-timers 'certbot*' --no-pager | head -3 || true

log "готово: https://$DOMAIN"
echo
echo "Дальше:"
echo "  1. впишите в $DIR/.env строки"
echo "       WEB_ENABLED=true"
echo "       WEB_PORT=$PORT"
echo "       MINIAPP_URL=https://$DOMAIN/"
echo "  2. перезапустите бота: systemctl restart tarot-bot"
echo "  3. в BotFather замените адрес мини-приложения на https://$DOMAIN/"
