#!/usr/bin/env bash
# Switch https://pdis.arbuz.lol/ from the old service (:8000) to the production app (:18200) and
# publish the account API under /id/. Saves a copy of the nginx config, tests, and
# rolls back automatically if any smoke test fails. Run only after an explicit owner "yes".
set -euo pipefail
SITE=/etc/nginx/sites-available/plantdiseases.conf
SN=/etc/nginx/snippets/pd-account-prod.conf
BK=/var/backups/pd-account-prod/nginx-pre-switch-$(date -u +%Y%m%dT%H%M%SZ)
[ ! -e "$SN" ] || { echo "already switched (snippet exists)" >&2; exit 1; }
curl -fsS -m 8 http://127.0.0.1:18200/api/health >/dev/null
curl -fsS -m 8 http://127.0.0.1:18201/ready >/dev/null
curl -fsS -m 8 http://127.0.0.1:8000/api/health >/dev/null   # old service must be healthy: it is the rollback
nginx -t
install -d -m 0700 "$BK"; cp -a "$SITE" "$BK/"; cp -a /etc/nginx/snippets "$BK/snippets"; cp -a /etc/nginx/conf.d "$BK/conf.d"
/opt/pd-account-prod/deploy/migrate_legacy.sh
install -m 0644 /opt/pd-account-prod/nginx/pd-account-prod.conf "$SN"
install -m 0644 /opt/pd-account-prod/nginx/pd-account-prod-zones.conf /etc/nginx/conf.d/pd-account-prod-zones.conf
python3 - <<'PY'
p = "/etc/nginx/sites-available/plantdiseases.conf"
s = open(p).read()
old = "        proxy_pass http://127.0.0.1:8000;\n"
assert s.count(old) == 1, "unexpected site config shape"
s = s.replace(old, '        proxy_pass http://127.0.0.1:18200;\n        proxy_set_header X-Internal-Service-Token "";\n')
inc = "    include /etc/nginx/snippets/pd-account-beta.conf;\n"
assert s.count(inc) == 1
s = s.replace(inc, inc + "    include /etc/nginx/snippets/pd-account-prod.conf;\n")
open(p, "w").write(s)
PY
fail() { echo "SMOKE TEST FAILED: $1 -> rolling back" >&2; /opt/pd-account-prod/nginx/rollback.sh "$BK"; exit 1; }
nginx -t || fail "nginx -t"
systemctl reload nginx
R="--resolve pdis.arbuz.lol:443:127.0.0.1 -s -m 20"
code() { curl $R -o /dev/null -w '%{http_code}' "$@"; }
[ "$(code https://pdis.arbuz.lol/api/health)" = 200 ] || fail "/api/health"
[ "$(code -F image=@/opt/pd-account-prod/releases/305f1ad87fd0/server/static/guide/healthy.png https://pdis.arbuz.lol/api/analyze)" = 200 ] || fail "/api/analyze"
[ "$(code -X POST https://pdis.arbuz.lol/api/internal/account-deletions)" = 404 ] || fail "internal app route exposed"
[ "$(code -X POST https://pdis.arbuz.lol/id/v1/internal/auth/verify)" = 404 ] || fail "internal account route exposed"
[ "$(code https://pdis.arbuz.lol/id/ready)" = 200 ] || fail "/id/ready"
curl $R https://pdis.arbuz.lol/id/.well-known/jwks.json | grep -q plantd-prod-2026-10 || fail "jwks kid"
[ "$(code https://pdis.arbuz.lol/plantdisease-test/api/health)" = 200 ] || fail "beta route"
echo "SWITCHED. Backup of previous nginx config: $BK  (rollback: /opt/pd-account-prod/nginx/rollback.sh)"
