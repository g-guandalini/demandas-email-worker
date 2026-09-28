#!/bin/sh
set -eu
set +x

: "${DEMANDAS_WEB_USER:?DEMANDAS_WEB_USER is required}"
: "${DEMANDAS_WEB_PASSWORD:?DEMANDAS_WEB_PASSWORD is required}"

case "$DEMANDAS_WEB_USER" in
  ""|*[!A-Za-z0-9_.-]*) echo "Invalid DEMANDAS_WEB_USER" >&2; exit 1 ;;
esac

mkdir -p /etc/nginx/auth
password_hash="$(openssl passwd -apr1 "$DEMANDAS_WEB_PASSWORD")"
umask 077
printf '%s:%s\n' "$DEMANDAS_WEB_USER" "$password_hash" > /etc/nginx/auth/.htpasswd
unset DEMANDAS_WEB_PASSWORD password_hash
