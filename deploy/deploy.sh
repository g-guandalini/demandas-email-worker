#!/bin/sh
set -eu

cd /opt/demandas

# Receives the short-lived, read-only GITHUB_TOKEN through SSH stdin. Keep
# registry credentials in a private temporary Docker config and remove them.
IFS= read -r registry_token || { echo "Missing temporary GHCR token" >&2; exit 1; }
IFS= read -r registry_user || { echo "Missing GHCR username" >&2; exit 1; }
case "$registry_user" in
    ""|*[!A-Za-z0-9_.-]*) echo "Invalid GHCR username" >&2; exit 1 ;;
esac
docker_config="$(mktemp -d)"
cleanup() {
    docker --config "$docker_config" logout ghcr.io >/dev/null 2>&1 || true
    rm -f "$docker_config/config.json"
    rmdir "$docker_config" 2>/dev/null || true
}
trap cleanup EXIT HUP INT TERM
printf '%s' "$registry_token" | docker --config "$docker_config" login ghcr.io --username "$registry_user" --password-stdin
unset registry_token registry_user

export DOCKER_CONFIG="$docker_config"
docker compose --env-file .env -f compose.production.yaml pull
docker compose --env-file .env -f compose.production.yaml up -d --wait
docker compose --env-file .env -f compose.production.yaml ps
