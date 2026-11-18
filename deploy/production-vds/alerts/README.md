# Stuck account deletion alert

`pd-deletion-check.sh` runs every 15 minutes from `pd-deletion-check.timer` and
messages a Telegram group when an account deletion job is stuck (see the
script header for the definition). It queries the account DB as the read-only
`account_backup` role through `compose.sh` and opens the app's
`community.db` read-only.

Install (as root):

    install -m 0755 pd-deletion-check.sh /usr/local/sbin/pd-deletion-check
    install -m 0644 pd-deletion-check.service pd-deletion-check.timer /etc/systemd/system/
    install -d -m 0700 /etc/pd-alerts   # telegram.env: TELEGRAM_BOT_TOKEN=..., TELEGRAM_CHAT_ID=...
    systemctl daemon-reload && systemctl enable --now pd-deletion-check.timer

The token file is root-only and never committed. Check a run with
`systemctl start pd-deletion-check.service; journalctl -u pd-deletion-check -n 20`.
