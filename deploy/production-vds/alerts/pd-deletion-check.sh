#!/bin/bash
# Alert when account deletions get stuck (see README.md in this folder).
#
# A deleted user's public community content is removed by a queued job before
# their private rows are erased. A job that keeps failing leaves that content
# public, so this counts stuck jobs on both sides and messages Telegram:
#   - account DB  account_deletion_jobs  (read-only role account_backup)
#   - app SQLite  community_cleanup_jobs (opened read-only)
# Stuck = state 'retry' with >= 3 attempts, or 'pending'/'retry' older than 1 h.
# Repeats every REPEAT_S while non-zero; sends one "resolved" when it clears.
# Messages carry counts and error codes only, never account or actor ids.
set -uo pipefail

ENV_FILE=/etc/pd-alerts/telegram.env      # TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
STATE_DIR=/var/lib/pd-alerts
COMPOSE=/opt/pd-account-prod/deploy/compose.sh
APP_DB=/var/lib/plantdiseases/community.db
REPEAT_S=$((6 * 3600))

# shellcheck disable=SC1090
. "$ENV_FILE"
mkdir -p "$STATE_DIR"

send() {
    curl -sS -m 20 -o /dev/null -w '%{http_code}' \
        "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
        --data-urlencode "text=$1" | grep -q '^200$' \
        || echo "telegram send failed" >&2
}

problems=()

acct=$("$COMPOSE" exec -T prod-postgres psql -X -q -t -A -F ' ' -U account_backup -d plantdisease \
    -v ON_ERROR_STOP=1 -c "
    SELECT count(*),
           coalesce(string_agg(DISTINCT last_error_code, ','), '-')
    FROM account_deletion_jobs
    WHERE (state = 'retry' AND attempt_count >= 3)
       OR (state IN ('pending','retry') AND created_at < now() - interval '1 hour');" 2>/dev/null)
if [[ $acct =~ ^([0-9]+)\ (.*)$ ]]; then
    acct_n=${BASH_REMATCH[1]}; acct_err=${BASH_REMATCH[2]}
    (( acct_n > 0 )) && problems+=("account service: ${acct_n} stuck deletion job(s), errors: ${acct_err}")
else
    problems+=("account service: check could not query account_deletion_jobs")
fi

app=$(runuser -u plantd -- python3 -I - "$APP_DB" <<'PY' 2>/dev/null
import sqlite3, sys, time
c = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
n, errs = c.execute(
    "SELECT count(*), group_concat(DISTINCT last_error_code) FROM community_cleanup_jobs "
    "WHERE (state = 'retry' AND attempt_count >= 3) "
    "OR (state IN ('pending','retry') AND created_at < ?)",
    (int(time.time()) - 3600,),
).fetchone()
print(n, errs or "-")
PY
)
if [[ $app =~ ^([0-9]+)\ (.*)$ ]]; then
    app_n=${BASH_REMATCH[1]}; app_err=${BASH_REMATCH[2]}
    (( app_n > 0 )) && problems+=("app server: ${app_n} stuck community cleanup job(s), errors: ${app_err}")
else
    problems+=("app server: check could not query community_cleanup_jobs")
fi

now=$(date +%s)
last=$(cat "$STATE_DIR/deletion-alert.last" 2>/dev/null || echo 0)
if (( ${#problems[@]} > 0 )); then
    printf '%s\n' "${problems[@]}" >&2
    if (( now - last >= REPEAT_S )); then
        msg="⚠️ PlantDiseases (pdis.arbuz.lol): account deletion stuck"$'\n'
        msg+=$(printf -- '- %s\n' "${problems[@]}")
        msg+=$'\n'"A deleted user's community content may still be public. Repeats every 6 h until cleared."
        send "$msg"
        echo "$now" > "$STATE_DIR/deletion-alert.last"
    fi
    exit 1
fi
if (( last > 0 )); then
    send "✅ PlantDiseases: stuck account deletions cleared."
    rm -f "$STATE_DIR/deletion-alert.last"
fi
echo "ok: no stuck deletion jobs"
