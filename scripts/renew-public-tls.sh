#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID -ne 0 ]]; then
  printf '%s\n' 'TLS renewal must run as root.' >&2
  exit 1
fi
tls_project_directory="${GASTROGID_DIRECTORY:-/home/ubuntu/gastrogid}"
cd "$tls_project_directory"
exec 9>/run/lock/gastrogid-tls-renew.lock
flock -n 9 || exit 0
tls_marker=/var/lib/gastrogid/tls/work/.renewed
docker compose -f compose.yaml -f compose.public.yaml run --rm --no-deps certbot \
  renew --quiet --no-random-sleep-on-renew --deploy-hook 'touch /var/lib/letsencrypt/.renewed' "$@"
if [[ -f "$tls_marker" ]]; then
  docker compose -f compose.yaml -f compose.public.yaml exec -T edge nginx -t
  docker compose -f compose.yaml -f compose.public.yaml exec -T edge nginx -s reload
  rm -f "$tls_marker"
fi
