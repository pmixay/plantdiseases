#!/usr/bin/env bash
# Restore the nginx configuration saved by switch.sh (default: the newest backup) and reload.
# The old service on :8000 was never stopped, so traffic returns to it immediately.
set -euo pipefail
BK=${1:-$(ls -d /var/backups/pd-account-prod/nginx-pre-switch-* | sort | tail -1)}
[ -f "$BK/plantdiseases.conf" ] || { echo "no saved site config in $BK" >&2; exit 1; }
cp -a "$BK/plantdiseases.conf" /etc/nginx/sites-available/plantdiseases.conf
rm -f /etc/nginx/snippets/pd-account-prod.conf /etc/nginx/conf.d/pd-account-prod-zones.conf
nginx -t && systemctl reload nginx
echo "rolled back to the pre-switch nginx config from $BK"
