#!/usr/bin/env bash
# One coordinated production backup + restore check (operator tool, outside Git).
# Order matters (docs/DELETION_JOURNAL_AND_BACKUP_EPOCH.md): epoch barrier first, then
# every store, all labelled with the same epoch id. Never stops services, never touches
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

EPOCH=$("$C" exec -T prod-account-api python -m app.deletion_replay begin-epoch)
case "$EPOCH" in e[0-9]*Z-*) ;; *) echo "unexpected epoch id" >&2; exit 1 ;; esac
base="$OUT/plantdisease-prod-$EPOCH"

# 1. PostgreSQL (least-privilege backup role, custom format)
"$C" exec -T -e PGPASSWORD prod-postgres pg_dump -h 127.0.0.1 -U account_backup -d plantdisease -Fc --no-owner \
    | age -r "$REC" > "$base.pg.dump.age"
# 2. Community SQLite via the online backup API (never a plain file copy)
"$C" exec -T prod-app-api python -c "
import os, sqlite3, sys
src = sqlite3.connect('/data/community/community.db'); dst = sqlite3.connect('/tmp/community.snap')
src.backup(dst); dst.close(); src.close()
sys.stdout.buffer.write(open('/tmp/community.snap', 'rb').read()); os.remove('/tmp/community.snap')" \
    | age -r "$REC" > "$base.community.db.age"
# 3. Deletion journal
"$C" exec -T prod-account-api cat /run/deletion-journal/journal.jsonl | age -r "$REC" > "$base.journal.age"

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
age -d -i "$IDENT" "$base.journal.age" | grep -c "\"epoch\":\"$EPOCH\"" >/dev/null
[ "$tables" = 25 ] && [ "$head" = 0010_billing_lifecycle_quota_policy ] && [ "$integrity" = ok ] || { echo "restore check FAILED tables=$tables head=$head community=$integrity" >&2; exit 1; }
find "$OUT" -maxdepth 1 -type f -name 'plantdisease-prod-e*' -mtime +27 -delete
echo "backup OK epoch=$EPOCH tables=$tables alembic=$head community_integrity=$integrity journal_epoch_present=yes"
