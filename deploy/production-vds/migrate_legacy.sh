#!/usr/bin/env bash
# Final copy of the small legacy data sets into the production volumes (run right before the nginx switch).
# Sources are read with SQLite's online backup API (safe against the live old service, which is not touched).
# Targets are only overwritten while still empty, so re-running can never clobber data created on production.
set -euo pipefail
umask 077
C=/opt/pd-account-prod/deploy/compose.sh
M=/opt/pd-account-prod/migration/final
rm -rf "$M"; install -d -m 0700 "$M"
python3 - <<'PY'
import sqlite3
for name in ("community", "push"):
    src = sqlite3.connect(f"file:/var/lib/plantdiseases/{name}.db?mode=ro", uri=True)
    dst = sqlite3.connect(f"/opt/pd-account-prod/migration/final/{name}.db")
    src.backup(dst); dst.close(); src.close()
print("legacy sources snapshotted")
PY
chown -R 10001:10001 "$M"
"$C" stop prod-app-api >/dev/null
"$C" run --rm --no-deps -T -v "$M":/mig:ro prod-app-api python - <<'PY'
import os, sqlite3
def rows(path, tables):
    if not os.path.exists(path): return 0
    c = sqlite3.connect(path)
    have = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
    n = sum(c.execute(f"select count(*) from {t}").fetchone()[0] for t in tables if t in have)
    c.close(); return n
def copy(src, dst):
    s = sqlite3.connect(f"file:{src}?mode=ro&immutable=1", uri=True); d = sqlite3.connect(dst)
    s.backup(d); d.close(); s.close()
community_tables = ["community_scans", "community_authors", "community_likes",
                    "community_blocks", "community_reports", "community_scan_views"]
target = "/data/community/community.db"
if rows(target, community_tables) == 0:
    copy("/mig/community.db", target); print("community: copied legacy database")
else:
    print("community: target already has data; left untouched")
push = "/data/legacy-push/push.db"
if rows(push, ["push_tokens"]) == 0:
    copy("/mig/push.db", push); print("push: copied legacy database")
else:
    print("push: target already has data; left untouched")
PY
"$C" up -d --no-build prod-app-api >/dev/null
for _ in $(seq 1 60); do
  [ "$(curl -s -m 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:18200/api/health)" = 200 ] && { echo "production app healthy again"; exit 0; }
  sleep 2
done
echo "production app did not become healthy" >&2; exit 1
