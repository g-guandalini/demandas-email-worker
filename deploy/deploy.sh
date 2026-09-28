#!/bin/sh
set -eu

cd /opt/demandas

docker_config=""
release_bundle=""
cleanup() {
    if [ -n "$docker_config" ]; then
        docker --config "$docker_config" logout ghcr.io >/dev/null 2>&1 || true
        rm -f "$docker_config/config.json"
        rmdir "$docker_config" 2>/dev/null || true
    fi
    if [ -n "$release_bundle" ]; then rm -f "$release_bundle"; fi
}
trap cleanup EXIT HUP INT TERM

# The SSH caller sends the registry credentials as two lines, then a tar stream.
IFS= read -r registry_token || { echo "Missing temporary GHCR token" >&2; exit 1; }
IFS= read -r registry_user || { echo "Missing GHCR username" >&2; exit 1; }
case "$registry_user" in
    ""|*[!A-Za-z0-9_.-]*) echo "Invalid GHCR username" >&2; exit 1 ;;
esac

# Receive only the release files allowed by this deployment command.
release_bundle="$(mktemp /tmp/demandas-release.XXXXXX.tar.gz)"
cat > "$release_bundle"
release_members="$(tar -tzf "$release_bundle" | sort)"
expected_members="$(printf 'compose.production.yaml\ndeploy/deploy.sh')"
if [ "$release_members" != "$expected_members" ]; then
    echo "Unexpected release bundle contents" >&2
    exit 1
fi
tar -xzf "$release_bundle" --no-same-owner --no-same-permissions -C /opt/demandas
chmod 0755 /opt/demandas/deploy/deploy.sh

# Receives the short-lived, read-only GITHUB_TOKEN through SSH stdin. Keep
# registry credentials in a private temporary Docker config and remove them.
docker_config="$(mktemp -d)"
printf '%s' "$registry_token" | docker --config "$docker_config" login ghcr.io --username "$registry_user" --password-stdin
unset registry_token registry_user

export DOCKER_CONFIG="$docker_config"
docker compose --env-file .env -f compose.production.yaml pull
docker compose --env-file .env -f compose.production.yaml up -d --wait
docker compose --env-file .env -f compose.production.yaml ps
