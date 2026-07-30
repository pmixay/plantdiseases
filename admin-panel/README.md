# PlantDiseases Admin Panel

A local control center for a remote PlantDiseases deployment. It starts on
`127.0.0.1`, connects to the server over verified SSH, reads the existing
SQLite stores without installing an agent, and provides guarded Git/restart
operations plus an explicit SSH command console.

## Start it

Prerequisite: Python 3.11 or newer on the administrator's computer.

Linux or macOS:

```bash
chmod +x start.sh
./start.sh
```

Windows:

```bat
start.bat
```

The launcher creates a private `.venv`, installs the small web/SSH dependency
set, starts the panel at `http://127.0.0.1:8787`, and opens a one-time signed
URL in the default browser. Use `--port 8788` to choose another port or
`--no-browser` to print the URL only.

## First connection

The panel ships with the verified production VPS profile:

- host `5.83.140.51`, SSH user `root`;
- repository `/home/plantdiseases/repo`;
- runtime data `/home/plantdiseases/repo/server`;
- remote Python `/usr/bin/python3`;
- systemd unit `plantdiseases.service`;
- health endpoint `http://127.0.0.1:8000/api/health`.

The **Use verified VPS profile** button restores these non-secret settings.
The connection still requires a freshly verified host fingerprint and a local
private key or password. A key is strongly preferred for the privileged root
account.

Enter:

- SSH host/IP, port and username;
- either a password or a private-key path on the admin computer;
- the absolute remote PlantDiseases repository root;
- the remote persistent data directory containing the SQLite databases;
- exactly one restart method: Docker Compose, systemd, or a custom command;
- the server health URL as seen from the remote host.

Select **Check fingerprint** first and compare its `SHA256:` value with the
fingerprint reported by the hosting provider or server console. Connecting
pins that exact key for the running session; a changed key hard-fails.

Passwords are kept only in backend process memory. They are never returned to
the browser, written to config, placed in localStorage, logged, or added to a
remote command. Non-secret connection fields can be remembered by the browser.
Stopping the launcher clears the SSH session and password.

## What “users” means

PlantDiseases has no accounts. Its server knows only random per-install UUIDs.
The panel builds the user list from the union of IDs in:

- `feedback.db`;
- `community.db` scans, opinions, views and opinion requests;
- `push.db` registrations;
- an optional `presence.db`, if a separately consented presence feature is
  added later.

“Recently active” means the latest **persisted server event** falls inside the
chosen window. It is not a claim that a phone is connected right now. The
existing Android app does not send manufacturer or phone model, so the panel
shows **Not collected**. If a future consented `presence.db` includes fields
such as `device_model`, `manufacturer`, `android_api`, or `last_seen_at`, the
remote helper discovers and displays them automatically.

Android Room data—garden entries, local photos, reminders, scan history and
chat transcripts—stays on the phone. SSH access to the server cannot view or
delete it. Error telemetry is not linked to a user ID and is deliberately not
joined by IP address.

The panel never returns raw FCM tokens, passwords, cookies, credentials, IP
hashes, exact coordinates or authorization values from remote databases.

## User deletion

Deletion requires typing the full install UUID. Before mutation, the panel
uses SQLite's online backup API and places consistent snapshots below:

```text
<data-directory>/admin_backups/<UTC timestamp>-delete-<ID prefix>/
```

The cascade covers feedback, issued scan IDs, community scans, related and
authored opinions/views/requests, FCM push registrations, optional presence
rows, and labeling candidates linked through scan IDs owned in either feedback
**or community**. Unreferenced thumbnails/training/feedback images are removed
only after containment and symlink checks. Shared files stay in place.

An audit entry containing the action, host, target ID, counts and backup path
is written locally to `data/audit.jsonl`; it never contains deleted records or
SSH credentials.

## Git and deployment controls

Structured controls:

1. verify the configured path is a Git worktree;
2. fetch and prune `origin`;
3. list only branches actually returned by `origin`;
4. preserve known untracked runtime databases, media, caches and backups while
   refusing source changes or unknown untracked paths;
5. switch without force, creating a tracking branch only when necessary;
6. update with `git merge --ff-only` (never reset/clean/forced checkout);
7. take online database backups before a deploy;
8. rebuild/restart through the selected adapter;
9. wait up to 90 seconds for `/api/health`.

Docker deployments run `docker compose up -d --build --remove-orphans`, because
a simple container restart would not load newly pulled code. systemd uses
`sudo -n`, so configure narrow passwordless permission for only the required
PlantDiseases service. For systemd deployments, `server/venv` (falling back to
repository `.venv`) is updated from `server/requirements.txt` before restart.
The panel also refuses a target branch that tracks any configured runtime-data
path, preventing checkout from overwriting production files. Only one
deploy/restart operation runs at a time.

The **SSH console** intentionally runs arbitrary commands as the configured SSH
account. Use a restricted deployment account. Console output is capped at 512
KiB and commands time out; the structured buttons remain the safer path for
routine work.

## Security boundaries

- loopback-only listener (`127.0.0.1`), with trusted Host checking;
- random launcher/session token and HttpOnly, SameSite=Strict cookie;
- CSRF token plus same-origin checks on mutations;
- strict Content Security Policy; all JS/CSS/assets are local;
- explicit host-key fingerprint pinning;
- no saved password and no remote helper installation;
- shell quoting plus allow-listing against fetched branch names;
- source-tree, runtime-collision and fast-forward-only Git guards;
- online SQLite backups before destructive data/deployment operations.

The panel is an operator tool, not a public web service. Do not reverse-proxy
it or bind it to a LAN/public address.

## Development checks

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```
