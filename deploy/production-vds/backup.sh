#!/usr/bin/env bash
# One coordinated production backup + restore check (operator tool, outside Git).
# Every store is labelled with the same UTC timestamp. Never stops services, never touches
# the live database; the restore check uses a temporary database that is dropped again.
set -euo pipefail
set -o noclobber
umask 077
C=/opt/pd-account-prod/deploy/compose.sh
ENV_FILE=/etc/pd-account-prod/account-prod.env
IDENT=/etc/pd-account-prod/backup-age-identity.txt
OUT=/var/backups/pd-account-prod/db
REC=$(age-keygen -y "$IDENT")
PW=$(grep '^PROD_POSTGRES_BACKUP_PASSWORD=' "$ENV_FILE" | cut -d= -f2-)
export PGPASSWORD="$PW"

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
base="$OUT/plantdisease-prod-$STAMP"

# 1. PostgreSQL (least-privilege backup role, custom format)
"$C" exec -T -e PGPASSWORD prod-postgres pg_dump -h 127.0.0.1 -U account_backup -d plantdisease -Fc --no-owner \
    | age -r "$REC" > "$base.pg.dump.age"
# 2. Community SQLite via the online backup API (never a plain file copy)
# The snapshot is written to the community volume (not the container's small /tmp) and streamed in chunks.
"$C" exec -T prod-app-api python -c "
import os, shutil, sqlite3, sys
snap = '/data/community/.backup-snapshot.db'
try:
    src = sqlite3.connect('/data/community/community.db'); dst = sqlite3.connect(snap)
    src.backup(dst); dst.close(); src.close()
    with open(snap, 'rb') as f: shutil.copyfileobj(f, sys.stdout.buffer, 1 << 20)
finally:
    if os.path.exists(snap): os.remove(snap)" \
    | age -r "$REC" > "$base.community.db.age"

for f in "$base".*.age; do [ -s "$f" ] || { echo "empty artifact: $(basename "$f")" >&2; exit 1; }; done
( cd "$OUT" && sha256sum "$(basename "$base")".*.age > "$(basename "$base").sha256" )

# Restore check: decrypt, restore into a temporary database, verify, drop it again.
work=$(mktemp -d /var/backups/pd-account-prod/.check.XXXXXX); trap 'rm -rf "$work"' EXIT
tmpdb="restore_check_$(date -u +%H%M%S)_$$"
age -d -i "$IDENT" "$base.pg.dump.age" > "$work/pg.dump"
"$C" exec -T prod-postgres psql -U account_bootstrap -d postgres -v ON_ERROR_STOP=1 -q -c "CREATE DATABASE $tmpdb"
cleanup_db() { "$C" exec -T prod-postgres psql -U account_bootstrap -d postgres -q -c "DROP DATABASE IF EXISTS $tmpdb" >/dev/null 2>&1 || true; }
trap 'cleanup_db; rm -rf "$work"' EXIT
"$C" exec -T prod-postgres pg_restore -U account_bootstrap -d "$tmpdb" --no-owner --exit-on-error < "$work/pg.dump"
tables=$("$C" exec -T prod-postgres psql -U account_bootstrap -d "$tmpdb" -At -c "select count(*) from pg_tables where schemaname='public'")
head=$("$C" exec -T prod-postgres psql -U account_bootstrap -d "$tmpdb" -At -c "select version_num from alembic_version")
age -d -i "$IDENT" "$base.community.db.age" > "$work/community.db"
integrity=$(python3 -c "import sqlite3,sys; print(sqlite3.connect(sys.argv[1]).execute('pragma integrity_check').fetchone()[0])" "$work/community.db")
[ "$tables" = 25 ] && [ "$head" = 0010_billing_lifecycle_quota_policy ] && [ "$integrity" = ok ] || { echo "restore check FAILED tables=$tables head=$head community=$integrity" >&2; exit 1; }
find "$OUT" -maxdepth 1 -type f -name 'plantdisease-prod-2*' -mtime +27 -delete
echo "backup OK stamp=$STAMP tables=$tables alembic=$head community_integrity=$integrity"
