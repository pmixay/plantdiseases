# Deployment Guide

Operator runbook for the PlantDiseases FastAPI server, plus the
Android release build. Architecture and API surface live in
[`README.md`](README.md); model status lives in
[`docs/ROADMAP.md`](docs/ROADMAP.md).

---

## 0. Overview & topology

- One plain Python process: `uvicorn main:app` over a virtualenv on a host.
  **Nothing is containerised** and no deployment asset is checked in — the
  nginx and systemd snippets below are templates to copy onto the host, not
  files in this repo. CI never deploys.
- TLS terminates in nginx; uvicorn binds loopback and speaks plain HTTP.
- State (SQLite stores, image dirs, LLM cache, logs) is on local disk and must
  be pointed outside the code tree — see §3.2 *Storage paths* and §5.
- The Android client's server URL is **compiled into the APK**; there is no
  in-app override. Build against a deployment with
  `./gradlew :app:assembleRelease -PAPI_BASE_URL=https://your.host/` (a
  trailing `/` is appended if missing). Default is `https://pdis.arbuz.lol/`.

## 1. Prerequisites

- **Python 3.14.** That is what CI runs (`.github/workflows/ci.yml`) and the
  only version tested. `server/start.sh` requires ≥ 3.10 (the syntax floor),
  so it will still launch on an untested interpreter — pin the venv to 3.14
  explicitly.
- `pip install -r server/requirements.txt`. Serving is ONNX Runtime only; no
  `torch` import exists anywhere in `server/`, so torch is **not** a runtime
  dependency.
- CPU-only is fine for the `gamma` (DINOv2 ViT-S/14) default. A GPU host is
  only worth it for `frontier_enabled=true` (ViT-B/14).
- The model bundle (§2) must be on disk before first boot.

## 2. Model bundle (`server/models/`)

Model artefacts are **never committed and never auto-downloaded**. Training,
calibration, conversion, and bundle export exist only in
[`server/train_notebook.ipynb`](server/train_notebook.ipynb). Extract one whole
notebook-produced archive into a **fresh** `server/models/` directory, preserving
nested paths. Never assemble a deployment from loose files or different runs.
There is no demo or filename fallback in the API startup path.

| Manifest-bound artefact | Status | Enforcement |
|---|---|---|
| `bundle_manifest.json` | **required** | Canonical bundle identity, run/data lineage, exact classifier choice, serving contract, sizes, and SHA-256 digests are verified before any graph loads. Legacy unbound bundles cannot start the API. |
| `detector.onnx` | **required** | Must be the checksummed detector role. A `.pt` is never loaded at serve time. |
| exactly one selected `classifier_int8.onnx` **or** `classifier.onnx` | **required** | The manifest selects it with `fallback_allowed=false`; an unbound sibling is rejected. A broken selected graph cannot silently fall back to another graph. |
| `classes.json`, `temperature.json`, `conformal.json` | **required** | Taxonomy, preprocessing, model identity, calibration source, and conformal fit must agree with the selected graph and manifest. Missing/malformed files abort startup. |
| `data_manifest.json`, `run_manifest.json` | **required** | Dataset digest/count and run/version/mode/family identity must match the sealed manifest. |
| `ood_embedding.npz` | conditional | Required exactly when `classes.json` declares an embedding bank; a declared-invalid or unbound bank aborts startup. The served OOD overlay remains off unless a matching full-pipeline gate is declared. |
| `aqua/*` | required for `combined` | TFLite classifier, Aqua classes, and retained ONNX student must all be bound together. Publish the TFLite + Aqua classes additionally to `server/static/aqua/`; their endpoint validates both file digests and the combined bundle identity. |
| `analysis/*` | informational | Run logs/cards/metrics may travel in the archive but cannot influence serving. |

`server/static/guide/` contains disease example photos and is not a model
artefact. A `frontier` deployment uses the same complete bundle contract under
`frontier_models_dir`; when it is invalid, gamma remains available and frontier
is reported unavailable.

Validate a bundle before shipping it:

```bash
python server/tools/verify_bundle.py server/models/
```

The verifier checks bytes and metadata only; it does not load or run a model.

## 3. Configuration

### 3.1 Precedence and gotchas

```bash
cp server/config.example.json server/config.json   # gitignored
$EDITOR server/config.json
```

- **Order: `server/config.json` → same-named environment variable → code
  default.** The file wins over the environment.
- A **blank string in the file** counts as unset, so an env var can still win.
  A **blank environment variable does not** — `get_str` returns `""`, not the
  default.
- JSON section names (`server`, `models`, `limits`, `ai`, `plantnet`,
  `security`, `push`) are decorative: nesting is flattened to flat UPPERCASE
  leaf keys. The one exception is `api_keys`, which survives as a dict.
- Config is read **once at import**. There is no hot reload — restart to apply.
- `PLANTDISEASES_CONFIG=/path/to/config.json` relocates the file. It is
  env-only.
- `config.example.json` is a starting point, **not a reference**: around 50 keys the
  server reads are absent from it (notably `rate_limit_burst`,
  `telemetry_enabled`, the community quotas and every storage path). It also
  ships values that differ from the code defaults — `rate_limit_rps: 1.0`
  (code: `2.0`) and `plantnet_timeout: 8.0` (code: `10.0`). If you copied the
  example, those are what you are running.

### 3.2 Keys

Defaults below are the **code** defaults.

**Bind & proxy**

| Key | Default | Notes |
|---|---|---|
| `host` / `port` | `0.0.0.0` / `8000` | Read **only** by `python main.py`. The systemd `ExecStart` and `start.sh` pass uvicorn flags instead. |
| `forwarded_allow_ips` | `127.0.0.1` | uvicorn `--forwarded-allow-ips`. `start.sh` reads it from the *environment* only. |
| `trusted_proxies` | `[]` | IPs/CIDRs whose `X-Forwarded-For` is honoured. See §6.1 — the right value is often not `127.0.0.1`. |
| `cors_origins` | `["http://localhost:3000","http://127.0.0.1:3000"]` | `["*"]` logs a production warning. Methods are fixed to `GET, POST`; headers to `Content-Type, Accept`. |
| `structured_logs` | `false` | JSON log lines instead of text. |

**Limits & concurrency**

| Key | Default | Notes |
|---|---|---|
| `max_file_size` | `10485760` (10 MB) | Image field of a multipart upload. |
| `max_request_bytes` | `max_file_size + 2 MB` | Whole body, every route and content type. Rejected with 413. |
| `max_image_dimension` | `4096` | Per side, on top of Pillow's decompression-bomb guard. |
| `min_image_dimension` | `48` | Below this → 400. An 8×8 noise image otherwise passes the blur gate and gets a confident verdict. |
| `inference_timeout` | `30.0` | Seconds before `/api/analyze` returns 504. |
| `analyze_cache_ttl_seconds` / `analyze_cache_max_entries` | `86400` / `512` | In-memory exact-bytes response dedup. |
| `infer_workers` | `1` | Inference thread pool. With `onnx_intra_op_threads=0` one scan already uses every core; raise only if you also pin intra-op low. |
| `infer_max_inflight` / `infer_max_inflight_per_ip` | `4 × infer_workers` / `2` | Admission control on `/api/analyze` + `/api/identify`: a request claims a slot before its upload is copied into memory or decoded (FastAPI has already spooled the multipart body to disk by then), holds it through inference only — never while waiting on PlantNet — and one that finds the server (or its own IP, an IPv6 /64) full gets 503 + `Retry-After: infer_busy_retry_after_seconds` (`3`). Bounds RAM to roughly that many in-memory uploads + decoded images. `0` disables a cap. If you set it in `config.json`, scale it with `infer_workers` yourself. |
| `net_workers` | `16` | Separate pool for OpenRouter / PlantNet / Wikipedia / FCM waits, so 20 s LLM calls don't queue DB writes behind them. |
| `onnx_intra_op_threads` | `0` | `0` = all physical cores — the biggest CPU-latency lever. Pin lower on a shared host. |
| `plant_photo_max_side` / `plant_photo_quality` / `popular_bundle_ttl_seconds` / `popular_placeholder_ttl_seconds` | `1000` / `80` / `1800` / `300` | Photo proxy re-encode and popular-plants bundle cache. |

**Security, auth & quotas**

| Key | Default | Notes |
|---|---|---|
| `require_api_key` | `false` | When true, every non-exempt route needs `X-API-Key:` or `Authorization: Bearer …`. An unknown key is 401 either way. |
| `api_keys` | `{}` | `{"<key>": {"rate_limit_rps": …, "burst": …, "quota_daily": …}}`. Missing sub-fields inherit the global rate/burst; `quota_daily: 0` = unlimited. Accepted as a dict or a JSON string. |
| `rate_limit_rps` / `rate_limit_burst` | `2.0` / `20.0` | Per-identity token bucket (`ip:<addr>` or `key:<key>`); over-limit = 429 + `Retry-After`. One user scan fans out into ~8 requests, hence the burst of 20. |
| `violation_threshold` / `violation_window` / `ban_duration` | `40` / `60.0` / `300.0` | Sustained over-limit requests earn a temporary 403 ban. |
| `feedback_ip_pepper` | *(empty → generated)* | Peppers the SHA-256 of a client IP before storage. Blank mints a random one persisted as `.ip_hash_pepper` beside the feedback DB — **set it explicitly** to control rotation, share one across replicas, or if the state dir is read-only. Changing it resets the 24 h per-IP write quotas once. |
| `community_author_key_pepper` / `community_cursor_secret` | `community-author-storage-v2` / falls back to `feedback_ip_pepper` then `community-cursor-v1` | Set both explicitly in production. |
| `feedback_daily_quota`, `community_daily_quota`, `community_opinion_daily_quota`, `community_like_daily_quota`, `community_safety_daily_quota` | `200`, `50`, `60`, `300`, `100` | Per hashed IP, per day. |
| `analyze_daily_quota`, `identify_daily_quota` | `300`, `150` | Per IP (IPv6 by /64), per UTC day, in memory (a restart forgets them), taken only once a request is admitted. Past it: 429. Every per-IP allowance below counts the same way; carrier NAT puts many users behind one IPv4, which is why the defaults sit far above one user's day. |
| `plantnet_daily_quota_per_ip` | `100` | PlantNet calls per IP per UTC day, shared by the analyze fan-out and `/api/identify`, so one client cannot drain the 500-a-day free tier. Past it a scan still gets its diagnosis, with `species.source: "quota"`. |
| `chat_daily_quota` | `100` | AI Plant Doctor turns per IP per UTC day, across sessions (the per-session caps reset with each client-chosen `session_id`). Past it: `available:false`, `reason: "daily_quota"`, with a bilingual `fallback`. |
| `care_plan_daily_quota` | `200` | LLM-polished care plans per IP per UTC day; past it the rules-based plan is served. |
| `species_ai_daily_quota` | `400` | `/api/species-diseases` + `/api/species-profile` together, per IP per UTC day; past it `available:false`, `reason: "daily_quota"`. |
| `community_author_daily_quota` | `10` | New Community identities (`/api/community/authors/adopt` with an unseen key) per IP per UTC day. Re-adopting a known key is free. |
| `community_require_author_key` | `true` | Second opinions need an adopted `X-Community-Author-Key` (401 `community_identity_required` otherwise), so a client cannot vote again by changing its body pseudo-id. App builds before 1.9 never send the key; set `false` only as a bridge while they are still in use. |
| `telemetry_enabled` / `telemetry_daily_cap_per_ip` | `true` / `200` | Anonymous client error reports (stores a hashed IP). |
| `scan_recording_enabled` | `true` | **On by default** — persists downscaled user scan images for the labeling loop. Disclosed by the app's consent screen and bundled privacy policy; turn it off if your deployment does not make that disclosure. |
| `scan_retention_days` / `scan_max_images` / `scan_common_keep_rate` / `scan_daily_cap_per_class` | `90` / `20000` / `0.1` / `50` | Bounds on that corpus. |
| `labeling_trust_conformal` / `labeling_conf_high` | `true` / `0.85` | Auto-accept thresholds for collected scans. |
| `feedback_retention_days`, `community_retention_days`, `telemetry_retention_days`, `prune_interval_seconds`, `issued_scan_id_ttl_days` | `180`, `90`, `30`, `86400`, `30` | Daily prune task drops rows past the retention age. |

**Storage paths** — defaults all land *inside* `server/`, which is wrong under
the hardened unit in §5. Point every one of these at the state directory.

| Key | Default |
|---|---|
| `log_dir` | `logs` (rotating, 5 MB × 5 backups) |
| `feedback_db_path` / `feedback_img_dir` | `server/feedback.db` / `server/feedback_images` |
| `community_db_path` / `community_thumb_dir` | `server/community.db` / `server/community_thumbs` |
| `labeling_db_path` / `labeling_img_dir` | `server/labeling.db` / `server/labeling_images` |
| `telemetry_db_path` | `server/telemetry.db` |
| `push_db_path` | `server/push.db` |
| `plant_photo_cache_dir` | `server/plant_photo_cache` |
| `ai_cache_dir` | `server/ai_cache` |

**Models & families**

| Key | Default | Notes |
|---|---|---|
| `models_dir` | `models` | A complete manifest-bound gamma/combined bundle (§2). |
| `frontier_enabled` | `false` | Off because it expects a GPU host. When off (or its bundle fails to load), `/api/analyze?family=frontier` returns HTTP 200 with `available:false` and `/api/capabilities` greys it out. |
| `frontier_models_dir` | `models/frontier` | A complete manifest-bound frontier bundle, including its own bound detector bytes. |
| `guide_static_dir` | `server/static/guide` | Served by `GET /api/guide/photo/{key}`, disk-cached by the client. |
| `aqua_static_dir` | `server/static/aqua` | `aqua_classifier.tflite` + `classes.json`, served by `/api/aqua/manifest` and `/api/aqua/model`. Provisioned out of band. |

**AI integrations** (all optional — see §4)

| Key | Default | Notes |
|---|---|---|
| `plantnet_api_key` / `plantnet_project` | *(empty)* / `all` | Direct key is local/test only. |
| `plantnet_api_key_file` | *(empty)* | Absolute, owner-only file holding the production key. |
| `plantnet_timeout` / `plantnet_attempts` | `10.0` / `2` | One bounded retry on a transient network fault; 401/429 are terminal. |
| `plantnet_cache_size` / `plantnet_cache_ttl_seconds` | `256` / `604800` (7 d) | |
| `openrouter_api_key` | *(empty)* | Direct key is local/test only. |
| `openrouter_api_key_file` | *(empty)* | Absolute, owner-only file holding the production key. |
| `openrouter_model` | `openai/gpt-6-luna` | Content surfaces expect structured outputs + reasoning-effort support; a model without them still works (schema dropped after one rejected request). |
| `openrouter_base_url` / `openrouter_total_timeout` | `https://openrouter.ai/api/v1` / `30.0` | Override the URL only for compatible gateways. The timeout is a per-call ceiling, retries included. |
| `openrouter_provider_only` | `openai` | Comma-separated provider slugs; blank = OpenRouter's own routing. |
| `openrouter_require_zdr` | `true` | Sends `provider.zdr=true` (zero-data-retention endpoints only). Set `false` when the OpenRouter account has no ZDR endpoints (requests otherwise fail with "No endpoints found matching your data policy"); `data_collection: deny` stays on either way. Weakens the retention guarantee — update the privacy policy to match. |
| `openrouter_reasoning_effort` + the per-caller `species_profile_` / `species_diseases_` / `care_plan_polish_reasoning_effort` | `medium` + `high` / `medium` / `medium` | `max`/`xhigh`/`high`/`medium`/`low`/`none`, or `omit` to send no field. A stale `minimal` maps to `low`. |
| `chat_max_tokens` / `species_profile_max_tokens` / `species_diseases_max_tokens` / `care_plan_polish_max_tokens` | `2048` / `4096` / `4096` / `3072` | Raising an effort means raising the matching cap: the hidden reasoning trace bills against it, and a truncated answer is rejected outright and degrades to the deterministic path. |
| `openrouter_prompt_caching` / `openrouter_prompt_cache_key` / `prompt_cache_min_prefix_tokens` | `true` / `plantvision-v1` / `1024` | See §4. |
| `chat_session_turn_cap` / `chat_session_token_cap` / `chat_session_ttl_sec` | `20` / `32000` / `3600` | Reasoning tokens draw down the token cap, which is why it is 32000 — lower values end a conversation before the turn cap does. |
| `chat_session_max_messages` / `chat_history_keep_last` / `chat_session_max_sessions` | `60` / `12` / `10000` | |
| `species_profile_cache_size` / `_ttl_seconds` | `256` / `2592000` (30 d) | `species_diseases_*` and `care_plan_*` take the same size with a `604800` (7 d) TTL. |

**Push**

| Key | Default | Notes |
|---|---|---|
| `fcm_credentials_file` | *(empty)* | Path to a Firebase **service-account JSON**. Empty → push is a no-op. The four Android `FCM_*` values are build inputs, not server config (§8.2). |

## 4. Optional integrations

None of these block boot. Missing credentials produce an `available:false`
payload and a specific "not configured" UX in the app.

**Secrets: direct value vs file.** `openrouter_api_key` and
`plantnet_api_key` are accepted directly only when `ENVIRONMENT` is `test` or
`development`. Anywhere else the direct value is refused and the integration
fails closed; provision an absolute, owner-only file and point
`OPENROUTER_API_KEY_FILE` / `PLANTNET_API_KEY_FILE` at it. Setting both the
direct and file forms, a symlink, a group/world-readable file, or an
empty/oversized one disables the integration without logging the value or the
path.

**PlantNet — species identification.** Key from
<https://my.plantnet.org/account/doc> (free tier 500 IDs/day) → `plantnet_api_key`
(or its `_FILE` form) → restart. Verify: `/api/identify` returns
`available: true`; without a key, `{"available": false, "source": "no_api_key"}`.
PlantNet spend is additionally bounded by the account service's shared budget
(`PLANTNET_BUDGET_ENABLED` and its version/attempt/exposure ceilings): every
real request, retries included, needs a fresh atomic reservation, and an
unavailable or disabled authority fails closed before any vendor call —
surfacing as a `budget_unavailable` / `budget_exhausted` source.

**OpenRouter — plant doctor, species content, care-plan polish.** Key from
<https://openrouter.ai> → `openrouter_api_key` (or its `_FILE` form) →
restart. Without it
`/api/care-plan` still returns a complete deterministic plan; only the
`summary_md` polish is skipped.

- *Provider pinning.* `openrouter_provider_only` defaults to `openai` because
  unpinned routing is price-weighted load **balancing**, not cheapest-first.
  Unpinned is still safe — every request carries
  `provider.require_parameters=true`, so a provider that would silently ignore
  `response_format` is filtered out. The pin is a request, not a receipt:
  confirm what actually served a call in OpenRouter's activity log.
- *Prompt caching.* On by default, and worth leaving on: the shared
  `_HOUSE_PREAMBLE` every request leads with is what clears the provider's
  1024-token cacheable-prefix minimum. Keep `openrouter_prompt_cache_key`
  **identical across replicas that share the preamble**; disable with
  `openrouter_prompt_caching: false`. Verify via `prompt_cache.hit_ratio` (§7).
- *Answer caching.* AI answers are cached in `ai_cache_dir` (7 days; 30 for
  species profiles) and again on the device (7 days), so a prompt or model
  change can appear to do nothing until the TTL expires. Bump the cache
  generation in `ai_services.py` to reach users sooner; orphaned generation
  dirs under the cache dir are safe to `rm -rf`.

**FCM push.** Point `fcm_credentials_file` at the service-account JSON (§8.2).
Blank, or an init failure, disables push rather than crashing.

## 5. Running in production

`server/start.sh` is a **first-run convenience only**: it always binds
`0.0.0.0` and honours only a `PORT` env var. Use the unit below for anything
long-lived.

The API speaks plain HTTP and TLS terminates in nginx (§6), so uvicorn binds
loopback. `0.0.0.0` is only correct when the TLS terminator is on another host
— and then the host firewall is what protects the port, so verify that it does.

```ini
# /etc/systemd/system/plantdiseases.service
[Unit]
Description=PlantDiseases FastAPI server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=plantd
Group=plantd
WorkingDirectory=/opt/plantdiseases/server
Environment=CORS_ORIGINS=https://plantdiseases.example.com
Environment=TRUSTED_PROXIES=127.0.0.1
Environment=FORWARDED_ALLOW_IPS=127.0.0.1
Environment=LOG_DIR=/var/lib/plantdiseases/logs
Environment=AI_CACHE_DIR=/var/lib/plantdiseases/ai_cache
Environment=FEEDBACK_DB_PATH=/var/lib/plantdiseases/feedback.db
Environment=FEEDBACK_IMG_DIR=/var/lib/plantdiseases/feedback_images
Environment=COMMUNITY_DB_PATH=/var/lib/plantdiseases/community.db
Environment=COMMUNITY_THUMB_DIR=/var/lib/plantdiseases/community_thumbs
Environment=LABELING_DB_PATH=/var/lib/plantdiseases/labeling.db
Environment=LABELING_IMG_DIR=/var/lib/plantdiseases/labeling_images
Environment=TELEMETRY_DB_PATH=/var/lib/plantdiseases/telemetry.db
Environment=PUSH_DB_PATH=/var/lib/plantdiseases/push.db
Environment=PLANT_PHOTO_CACHE_DIR=/var/lib/plantdiseases/plant_photo_cache
Environment=FCM_CREDENTIALS_FILE=/var/lib/plantdiseases/fcm-service-account.json
ExecStart=/opt/plantdiseases/.venv/bin/uvicorn main:app \
          --host 127.0.0.1 --port 8000 \
          --forwarded-allow-ips 127.0.0.1 --proxy-headers \
          --limit-concurrency 64 --timeout-keep-alive 5 --no-access-log
Restart=on-failure
RestartSec=3
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
StateDirectory=plantdiseases
MemoryMax=4G

[Install]
WantedBy=multi-user.target
```

Two things this unit is doing that are easy to get wrong:

- **Mutable state must live outside the code tree.** Under `ProtectSystem=strict`
  the whole tree is read-only, and every storage-path default lands in
  `server/`. `StateDirectory=plantdiseases` auto-creates `/var/lib/plantdiseases`
  writable for the service user; point every SQLite store, image dir, `LOG_DIR`
  **and** `AI_CACHE_DIR` there. Miss `AI_CACHE_DIR` and the LLM cache silently
  never persists, re-paying a 10–20 s OpenRouter call on every cold worker.
- **Size `MemoryMax` with headroom**, not tightly: a kill plus
  `Restart=on-failure` is a restart loop paying the cold model load every cycle.
  Raise it for `frontier_enabled=true` (ViT-B/14 weights plus its own arena) or
  `infer_workers > 1`.

`--limit-concurrency 64` caps simultaneously-accepted connections — past that
uvicorn answers 503 instead of queueing unboundedly. `--no-access-log` drops
uvicorn's own request line: the app already logs every request (method, path
without query string, status, duration, client IP), and uvicorn's line would
record query strings, where older app builds put the install's pseudo-id.

```bash
sudo useradd --system --home /opt/plantdiseases plantd
sudo -u plantd python3.14 -m venv /opt/plantdiseases/.venv
sudo -u plantd /opt/plantdiseases/.venv/bin/pip install -r /opt/plantdiseases/server/requirements.txt
sudo systemctl daemon-reload
sudo systemctl enable --now plantdiseases
journalctl -u plantdiseases -f
```

## 6. Reverse proxy & TLS (nginx + Let's Encrypt)

```nginx
# /etc/nginx/sites-available/plantdiseases.conf
server {
    listen 80;
    server_name plantdiseases.example.com;
    return 301 https://$host$request_uri;
}

# Access log without the install's pseudo-id (a bearer value). App builds that
# predate the X-User-Pseudo-Id header send it as `?user_pseudo_id=` — $uri
# drops the query — and in the path of /api/community/my-scan-opinions/<id>,
# which the map masks.
map $uri $plantdiseases_log_uri {
    ~^/api/community/my-scan-opinions/.  /api/community/my-scan-opinions/<redacted>;
    default                              $uri;
}
log_format plantdiseases '$remote_addr [$time_local] "$request_method $plantdiseases_log_uri" '
                         '$status $body_bytes_sent $request_time';

server {
    listen 443 ssl http2;
    server_name plantdiseases.example.com;
    access_log /var/log/nginx/plantdiseases.access.log plantdiseases;

- JSON counters: `GET /api/metrics`
- Prometheus exposition: `GET /api/metrics/prometheus`

### 5.1 Community moderation

Community reports are intentionally not exposed through an HTTP admin route.
Review the local queue as the service user, with the same state path as the
running API:

```bash
sudo -u plantd env COMMUNITY_DB_PATH=/var/lib/plantdiseases/community.db \
  COMMUNITY_THUMB_DIR=/var/lib/plantdiseases/community_thumbs \
  /opt/plantdiseases/.venv/bin/python \
  /opt/plantdiseases/server/tools/community_moderation.py list --limit 100

sudo -u plantd env COMMUNITY_DB_PATH=/var/lib/plantdiseases/community.db \
  COMMUNITY_THUMB_DIR=/var/lib/plantdiseases/community_thumbs \
  /opt/plantdiseases/.venv/bin/python \
  /opt/plantdiseases/server/tools/community_moderation.py \
  resolve REPORT_ID --action remove_content --confirm-remove
```

Use `--action dismiss` when the content does not violate the published rules.
The public criteria, response targets, retention, and Russian operator summary
are in
[docs/COMMUNITY_RULES_AND_MODERATION.md](docs/COMMUNITY_RULES_AND_MODERATION.md).
  (`Content-Type: text/plain; version=0.0.4`)
- Liveness probe: `GET /api/health` — also used by the container `HEALTHCHECK`.

    client_max_body_size 15m;          # must exceed max_request_bytes
    proxy_read_timeout   60s;          # must exceed inference_timeout

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo certbot --nginx -d plantdiseases.example.com
sudo systemctl enable --now certbot.timer
```

### 6.1 Getting the client IP right

`X-Forwarded-For` decides the identity every abuse control keys on — the
rate-limit bucket and the hashed `client_ip_hash` behind each 24 h write quota.
Get it wrong and you throttle either nobody or everybody.

`$proxy_add_x_forwarded_for` **appends** the peer nginx sees to whatever the
client sent, so the app receives `<whatever the client claimed>, <real peer>`
and walks it **right to left**, skipping entries in `trusted_proxies` and
taking the first that is not — leftmost entries are attacker-supplied and must
never win. `trusted_proxies` must match the **peer address**: with nginx on the
same host that is `127.0.0.1`, but put any other hop in front (a proxy on a
second machine, a NAT) and a literal `127.0.0.1` never matches, the header is
ignored, and every client collapses into one bucket.

With a CDN in front of nginx the header arrives as `<real client>, <CF edge>`,
so the app stops at the edge IP and every visitor through one PoP shares a
bucket and a daily quota — mass 429s. Fix it in nginx so the real client is
what gets appended:

```nginx
    # Cloudflare: https://www.cloudflare.com/ips/ — keep this list current
    set_real_ip_from 173.245.48.0/20;   # (…all CF ranges…)
    real_ip_header   CF-Connecting-IP;
```

Verify rather than assume — every request logs the resolved address:

```bash
journalctl -u plantdiseases | grep -o 'IP=[^ ]*' | sort | uniq -c | sort -rn | head
```

A healthy spread of addresses is right. One address for every request means the
header is not being honoured.

## 7. Monitoring & health

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | Liveness. **503 `Pipeline not ready` until the models finish loading**; then 200 with `status`, `version`, `num_classes`, `uptime_seconds`, `total_requests`. |
| `GET /api/capabilities` | Families + availability, model/version metadata, `int8_shipped`. |
| `GET /api/metrics` | JSON counters. |
| `GET /api/metrics/prometheus` | `text/plain; version=0.0.4`. |

Rate-limiter exemptions are `/api/health`, `/api/capabilities`, and GET/HEAD on
`/api/guide/*`, `/api/community/thumb/*`, `/api/aqua/manifest`. **The metrics
endpoints are not exempt** — they run full-table SQLite aggregates, so they are
metered like any other route, and they sit behind `require_api_key` when it is
on. At the default 2 rps a 15 s scrape is nowhere near the bucket; if you
tighten the limit, give the scraper its own API key.

`/api/capabilities` always reports `aqua: available:true` — it describes the
on-device tier, not server state. To check whether the TFLite model is actually
hosted, curl `/api/aqua/manifest`, which returns `available:false` when
`aqua_static_dir` is unprovisioned.

Counters worth alerting on, from `/api/metrics`:

| Counter | Healthy | A bad value means |
|---|---|---|
| `prompt_cache.hit_ratio` | somewhat under 1.0, with a small steady `cache_write_tokens_in` | Near `0.0` over real traffic: the shared prefix changed or fell under `min_prefix_tokens`. Check `shared_prefix_tokens_est` still clears it and that nothing volatile crept in ahead of the preamble. |
| `prompt_cache.enabled` | `true` | Caching was turned off; every call pays full input price. |
| `ai_doctor.cache_hit_ratio` | same shape, chat only | Chat-specific prefix breakage. |
| `care_plan_polish.reject_ratio` | near 0 | Climbing after a model/prompt change = the model is drifting off the plan's canonical watering/feeding/NPK numbers and the LLM summary is being silently discarded (users see the deterministic fallback, so nothing else surfaces it). |

## 8. Android release: signing & FCM

The Android CI job (`.github/workflows/ci.yml`) runs lint, unit tests and
non-distributable compilation on pushes and pull requests. It never receives
the permanent keystore or live provider configuration. Distributable
same-package APKs come only from the manually dispatched
`.github/workflows/android-release.yml` workflow. Its unprotected build job
uses public/extractable provider variables to produce and verify a genuinely
unsigned APK. Only that exact artifact crosses into the protected,
required-reviewer `android-signing` job. The signing job has no checkout or
Gradle invocation, but its inline shell is repository-controlled workflow code;
every channel therefore dispatches only from protected `main`, and the reviewer
must inspect that exact SHA. `app/build.gradle.kts` reads environment values
before local Gradle-property fallbacks so a committed property cannot shadow a
reviewed distribution input.
The public Yandex native client ID is pinned in `app/build.gradle.kts`; no
Yandex client secret is used. VK stays independently disabled unless both of
its extractable mobile configuration values are supplied.

### 8.1 Release signing

Do **not** generate a key until the certificate that signed PlantDiseases 1.8
has been identified. If 1.8 was distributed, 1.9 must use that same signing
identity to update it. The authoritative recovery check, one-time generation
command, provider registration fields, backup rules, and local verification
are in
[docs/ANDROID_1_9_SIGNING_AND_OAUTH_REGISTRATION.md](docs/ANDROID_1_9_SIGNING_AND_OAUTH_REGISTRATION.md).

Create these **`android-signing` environment secrets** (with PKCS12 the key
password equals the store password):

| Secret | Value |
|---|---|
| `RELEASE_KEYSTORE_BASE64` | one-line base64 of the permanent keystore |
| `RELEASE_STORE_PASSWORD` | keystore password |
| `RELEASE_KEY_ALIAS` | the alias (e.g. `plantdiseases`) |
| `RELEASE_KEY_PASSWORD` | permanent key password |

Also configure required reviewers, prevent self-review, and restrict deployment
branches to `main` only. The protected workflow refuses an APK whose
certificate differs from the fingerprint pinned in the workflow (`RELEASE_CERT_SHA256`). Never commit the keystore or
its base64 encoding.

The Yandex native client ID is pinned in the Android source and needs no
Actions variable. Optionally create the public/extractable
`STAGING_VKID_CLIENT_ID` / `STAGING_VKID_CLIENT_SECRET` and equivalent
`PRODUCTION_*` VK variables only after VK registration; each pair must be
complete, and leaving both pairs absent keeps VK disabled. The exact reviewed
`STAGING_APP_API_BASE_URL` / `STAGING_ACCOUNT_API_BASE_URL` German pair and
`ACCOUNT_REAL_OAUTH=true` are committed public defaults in
`android/gradle.properties`, so ordinary local debug/release/staging builds
need no flags. Protected staging automation still checks that pair for drift.
The reviewed production endpoints, the approved production endpoints, the
RuStore Console app and PRO product IDs, the `PRODUCTION_RUSSIAN_CUTOVER_APPROVED`
flag and the `RELEASE_CERT_SHA256` fingerprint are plain, public values written
directly into `.github/workflows/android-release.yml`; no repository or
environment variables are needed for them, and changing one is a reviewed commit
to `main`. Keep the cutover flag `false` until the exact production HTTPS routes
have passed the Russian ingress/account smoke matrix. The protected
`android-signing` environment contains only the four permanent-key secrets above.
The checkout-free signing job requires the
production URLs to match the approved URLs exactly. It also rejects
the German staging prefix, reserved `.invalid`/localhost names, IP literals,
non-443 ports, and any path other than the application origin plus `id/`.
Missing production values fail; they never fall back to staging.
Put local signing properties in `~/.gradle/gradle.properties`, outside the
repository, as shown in the dedicated guide.

### 8.2 Notifications and future Firebase Cloud Messaging (FCM)

PlantDiseases 1.9 is deliberately built with all `FCM_*` values empty and uses
the bounded WorkManager polling path. Do not enable FCM until the account system
has a durable, observable notification sink/worker with invalid-token cleanup.
When that work is accepted, two distinct credential sets will be required:

- **Android client config** — `FCM_APP_ID`, `FCM_API_KEY`, `FCM_PROJECT_ID`,
  `FCM_SENDER_ID` (from `google-services.json`: `mobilesdk_app_id`,
  `api_key[0].current_key`, `project_id`, `project_number`). These are **public** —
  shipped inside every APK — so per Google they are *not* secrets. A future
  reviewed workflow may inject them as public build variables, and must harden
  them by **restricting the Android API key** in Google Cloud Console (Application
  restrictions: Android apps + your package/SHA-1; API restrictions: Firebase/FCM).
  Unset → the app builds fine and push stays dormant (`PushManager.isConfigured()` is
  false; the client falls back to its WorkManager poll).
- **Server send-credential** — the Firebase **service-account JSON** that can actually
  send pushes. It is loaded from a path on the server via `push.fcm_credentials_file`
  (config.json) / `FCM_CREDENTIALS_FILE` (env), read in `server/push.py`. **Not** in
  this repo; empty → `/api/push/register` and sends are graceful no-ops.

### 8.3 CI and protected signing

Ordinary `android` CI runs this credential-free evidence command:

```bash
./gradlew ktlintCheck detekt testDebugUnitTest testStagingUnitTest \
  testReleaseUnitTest testRecoveryProductionUnitTest testRecoveryStagingUnitTest \
  assembleRelease assembleStaging assembleRecoveryProduction \
  assembleRecoveryStaging \
  --build-cache --configuration-cache --stacktrace
```

After it passes, dispatch `protected Android signing`, select the required
normal or recovery channel, and inspect the exact commit. The first job builds with
`ANDROID_UNSIGNED_DISTRIBUTION=true`, proves that the APK has no signature, and
uploads its package/version/endpoint provenance. Approve the environment only
after that immutable artifact exists. The second job downloads it by artifact
ID, records GitHub's archive digest, independently verifies the APK SHA-256
anchored in the build-job output, and checks provenance, package/version and
unsigned state before it uses the runner's fixed Android Build Tools to align
and sign it. It has no checkout or Gradle invocation. Its inline signing shell
is trusted code from protected `main`, so approval includes review of the exact
workflow SHA as well as the artifact. The signed artifact
includes APK SHA-256, signing-certificate fingerprint, and provenance and is
retained for 90 days; copy accepted releases to the operator's immutable
release archive.

The same protected workflow has distinct `recovery-production` and
`recovery-staging` channels. Both build the same package/certificate at
versionCode 20 and disable account UI. Production recovery consumes the reviewed
`PRODUCTION_APP_API_BASE_URL` / `PRODUCTION_ACCOUNT_API_BASE_URL` pair and the
signing job binds it to the protected `APPROVED_PRODUCTION_*` values. Staging
and staging recovery both consume the same explicit, exact reviewed `STAGING_*`
German pair. This separation keeps
the endpoint-scoped encrypted vault and queued revocations on their original
service; neither recovery artifact redirects them to the other environment.
Missing, mismatched, or invalid production endpoint variables fail the
production-recovery build. Archive the matching recovery artifact before each
versionCode 19 channel is distributed. After either recovery is published, the
next mandatory normal release must use versionCode 21 or higher.

Real-provider staging and production are intentionally blocked while the
committed privacy policy still contains the private-contact or deleted-actor
retention placeholder. The former production-only stop for the cross-store
backup epoch and durable deletion replay journal was lifted on 2026-10-09 once
they were implemented and restore-drilled (see
`docs/DELETION_JOURNAL_AND_BACKUP_EPOCH.md`). The two recovery channels stay
available during any hold.
An approval changes `release-policy-gates.json` schema 2 and the matching
structured markers in `privacy.html`: the contact must be an exact visible
`mailto:` or HTTPS link, and the English/Russian tombstone disclosures must
exactly match the approved retention mode. The JSON also requires an HTTPS
decision reference and UTC approval timestamp. Prose substring matches cannot
clear the gate.

### 8.4 RuStore in-app updates

Nothing to configure at build or deploy time. To ship an update, publish an APK
with a higher `versionCode` to the [RuStore Console](https://console.rustore.ru),
signed with the same release key. The flow can only be exercised on a device
where the app was installed *from* RuStore, not from a sideloaded build.

## 9. Go-live checklist

- [ ] TLS enforced — HTTP redirects to HTTPS, HSTS on at the proxy.
- [ ] `cors_origins` pinned to the real front-end origin(s); **no `*`**.
- [ ] `trusted_proxies` set to the real peer address(es) and **verified** with
      the `journalctl … grep IP=` one-liner in §6.1.
- [ ] Rate limits tuned (`rate_limit_rps`, `rate_limit_burst`); if you copied
      `config.example.json` you are on `1.0`, not the code default.
- [ ] `feedback_ip_pepper` and `community_author_key_pepper` set explicitly.
- [ ] nginx `client_max_body_size` > `max_request_bytes`; `proxy_read_timeout`
      > `inference_timeout`.
- [ ] A single complete model bundle is present and passes `server/tools/verify_bundle.py` (§2).
- [ ] All storage paths point into the state directory; it is backed up.
- [ ] `scan_recording_enabled` matches what your privacy policy discloses.
- [ ] Service runs as a non-root user; you know which commit is deployed.
- [ ] Logs rotating (`log_dir`) and shipped off-box; Prometheus scraping
      `/api/metrics/prometheus`.
- [ ] `GET /api/health` returns 200 with `status: "ok"` **through nginx, from
      another machine** (a loopback curl cannot tell you the bind was right).
- [ ] `GET /api/capabilities`: `gamma` true, `frontier` matches
      `frontier_enabled`. With frontier off,
      `curl -F image=@leaf.jpg ".../api/analyze?family=frontier"` returns HTTP
      200 with `available:false`.
- [ ] End-to-end: `curl -F "image=@leaf.jpg" https://…/api/analyze` returns a
      bilingual diagnosis (HTTP 200).
- [ ] The shipped APK was built with `-PAPI_BASE_URL=https://…`. Release builds
      are HTTPS-only — `res/xml/network_security_config.xml` permits no
      cleartext; the `10.0.2.2`/`localhost` exemption lives only in
      `src/debug/`.

## 10. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Process exits at boot, `BundleLoadError` | Missing manifest/file, mixed runs, changed bytes, unbound classifier sibling, or a serving-contract mismatch | Re-extract one complete notebook archive into a fresh models directory; run the verifier in §2. Never repair or synthesize a production manifest after training. |
| Process exits at boot, `DetectorLoadError` | The manifest-verified `detector.onnx` cannot be opened or violates its tensor contract | Produce a new notebook bundle; a `.pt` detector is never accepted by serving. |
| Process exits at boot, `ClassifierLoadError` naming a class count | Bound `classes.json -> class_names` length differs from the selected ONNX head width | Produce a corrected notebook bundle. There is no pad/truncate or default-taxonomy migration shim in strict startup. |
| `/api/health` 503 `Pipeline not ready` | Models still loading, or the lifespan never completed | Wait out the cold load; if it persists, check the journal for the load error |
| Every client shares one rate-limit bucket; mass 429/403 | `trusted_proxies` does not contain the actual peer address, or a CDN sits in front | §6.1 — verify with the `IP=` log walk, add `real_ip_header` for a CDN |
| Species/care-plan screens are slow for everyone, every time | `AI_CACHE_DIR` unwritable under `ProtectSystem=strict` — writes are best-effort and swallowed | Point `AI_CACHE_DIR` into `StateDirectory`; the server logs a warning at startup when the probe write fails |
| A prompt/model change has no visible effect | Server-side (7–30 d) and on-device (7 d) answer caches | Bump the cache generation in `ai_services.py`; `rm -rf` the stale dir under `ai_cache_dir` |
| `prompt_cache.hit_ratio` flat at 0.0 | Shared prefix changed or fell under `prompt_cache_min_prefix_tokens` | Check `shared_prefix_tokens_est`; build system prompts through `ai_services._system_prompt()` |
| Release APK installs but Play/RuStore rejects it | Built without `RELEASE_STORE_FILE` → debug key fallback | Supply the four `RELEASE_*` props; CI fails this case when `RELEASE_KEYSTORE_BASE64` is set |
| Push silently does nothing | Server: `fcm_credentials_file` blank or init failed (logged, then disabled). Client: any `FCM_*` build value blank | Set the service-account path server-side; rebuild with all four `FCM_*` values |
| The on-device model never downloads and `aqua` scans fall back to the server, yet `/api/capabilities` reports `aqua: available:true` | `capabilities` describes the on-device tier, not provisioning | Check `/api/aqua/manifest` — `available:false` means `aqua_static_dir` is unprovisioned |
| Restart loop after a memory kill | `MemoryMax` too tight; each cycle re-pays the cold model load | Raise `MemoryMax`, especially with `frontier_enabled=true` or `infer_workers > 1` |
