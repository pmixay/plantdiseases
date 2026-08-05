# Deployment Guide

Production deployment notes for the PlantDiseases FastAPI server. The
Android client needs no server-side change — point it at the public URL
through **Profile → Server URL**.

Configuration keys below match `server/main.py`, `server/ai_services.py`
and `server/config.example.json`. The server reads `server/config.json`
first; environment variables are still honoured as a fallback so existing
Docker / CI deployments keep working.

---

## 1. Production configuration

The recommended path is to copy the committed example file:

```bash
cp server/config.example.json server/config.json
$EDITOR server/config.json
```

`server/config.json` is gitignored. JSON sections (`server`, `models`,
`limits`, `ai`, `plantnet`, `security`, `push`) are flattened so each leaf
key maps to the same name as the legacy environment variable.

| Key | Default | Purpose |
|---|---|---|
| `cors_origins` | `["http://localhost:3000","http://127.0.0.1:3000"]` | Allowed CORS origins. **Do not set to `["*"]` in production.** |
| `trusted_proxies` | `[]` | IPs / CIDRs whose `X-Forwarded-For` header is honoured. Required behind nginx / cloudflare; otherwise spoofed `X-Forwarded-For` is ignored. |
| `forwarded_allow_ips` | `127.0.0.1` | Passed to uvicorn `--forwarded-allow-ips`; controls which peers can update `Host` / scheme via forwarded headers. |
| `rate_limit_rps` | `1.0` | Per-IP request rate limit. Rate-limited responses return `429` with a `Retry-After` header. |
| `max_file_size` | `10485760` (10 MB) | Hard cap on uploaded bytes. |
| `max_image_dimension` | `4096` | Pixel limit per side, in addition to Pillow decompression-bomb guard. |
| `inference_timeout` | `30` | Seconds before `/api/analyze` returns `504`. |
| `infer_workers` | `1` | ThreadPoolExecutor workers for model inference. With `onnx_intra_op_threads=0` a single scan uses all cores, so one worker avoids CPU oversubscription. Raise it only if you also pin `onnx_intra_op_threads` low for real concurrency on a many-core host. |
| `net_workers` | `16` | ThreadPoolExecutor workers for external-service HTTP (OpenRouter / PlantNet / Wikipedia / FCM). These threads wait on the network, not the CPU, so the pool is sized for concurrency regardless of core count — without it, concurrent 20 s LLM calls exhaust the tiny CPU-derived default pool and queue every other threaded operation (DB writes included) behind them. |
| `onnx_intra_op_threads` | `0` | Intra-op threads per ONNX session. `0` lets ONNX Runtime use all physical cores — the biggest CPU-latency lever for the DINOv2 ViT classifier. Pin lower on a shared host. |
| `frontier_enabled` | `false` | Enable server inference for the high-capacity **frontier** family (ViT-B/14, ~86M param). Off by default because it expects a GPU host. When off (or the bundle fails to load), `/api/analyze?family=frontier` returns a graceful `available:false` payload and `/api/capabilities` reports it unavailable so the app greys it out. A broken frontier bundle never blocks gamma boot. |
| `frontier_models_dir` | `models/frontier` | Where the frontier bundle (`classifier.onnx`, `classes.json`, `temperature.json`, `conformal.json`) lives when `frontier_enabled=true`. The YOLOv8n detector is shared with gamma. |
| `guide_static_dir` | `<server>/static/guide` | Directory of disease/pest example photos served by `GET /api/guide/photo/<key>`. Moved out of the Android APK to shrink the app; committed to the repo and copied into the Docker image. The client disk-caches them for offline use. |
| `aqua_static_dir` | `<server>/static/aqua` | Directory hosting the on-device **Aqua** TFLite model (`aqua_classifier.tflite` + `classes.json`), served by `GET /api/aqua/model` (+ `GET /api/aqua/manifest`). Provisioned out of band (not committed, like the weights); when absent the manifest reports `available:false` and the app uses the server model. The app downloads + caches it and re-downloads only when the sha changes. |
| `log_dir` | `logs` | Rotating file logs (5 MB × 5 backups). |
| `host` / `port` | `0.0.0.0` / `8000` | Uvicorn bind. |
| `openrouter_api_key` | *(empty)* | OpenRouter key. Without it the chat and care-plan polish return an offline fallback. |
| `openrouter_model` | `openai/gpt-oss-120b` | Pinned chat model. Pick anything OpenRouter exposes (e.g. `openai/gpt-4o-mini`, `meta-llama/llama-3.1-8b-instruct`). |
| `openrouter_base_url` | `https://openrouter.ai/api/v1` | Override only for OpenRouter-compatible self-hosted gateways. |
| `plantnet_api_key` | *(empty)* | PlantNet identify key (optional). |
| `fcm_credentials_file` | *(empty)* | Path to a Firebase service-account JSON for FCM push (opinion notifications). Empty → push is a no-op. See §8.2. |

### 1.1 Third-party API keys

Two optional features depend on external services. The server still
starts and responds with a graceful "unavailable" payload when a key
is missing, so deployments without these features stay supported.

**PlantNet — species identification.** Used by the Android *Create
new plant* flow and the gallery's "identify species" button.

1. Sign up at <https://my.plantnet.org/account/doc> (free tier:
   500 identifications / day).
2. Generate an API key from the account page.
3. Drop it into `server/config.json` under `plantnet_api_key`, OR
   export `PLANTNET_API_KEY=...` in the environment that starts the
   server.
4. Restart the server. `/api/identify` should now return
   `available: true` with a list of candidate species; without the
   key it returns `{"available": false, "source": "no_api_key"}` and
   the Android app shows *"Species identification isn't configured
   on this server yet"* instead of the candidate list.

The PlantNet client is read in `server/ai_services.py:50` and the
identify call lives in `identify_species()` lower in the same file.

**OpenRouter — AI plant doctor + care-plan polish.** Used by the
chat sheet and the optional Markdown summary on care plans.

1. Sign up at <https://openrouter.ai> and create an API key.
2. Drop it into `server/config.json` under `openrouter_api_key`, or
   export `OPENROUTER_API_KEY=...`. Optionally pin a model with
   `openrouter_model` (default `openai/gpt-oss-120b`).
3. Restart the server. The care-plan endpoint still returns a
   complete, deterministic plan without a key; only the
   `summary_md` polish step is skipped.

**Prompt caching (on by default).** Every chat turn re-sends the same
~600-token system prompt plus the conversation so far. With
`openrouter_prompt_caching` enabled the server asks OpenRouter to cache
that prefix and pins each conversation to one provider (a hashed
`session_id`, so no client identifier leaves the box), so repeat turns
are billed at a fraction of input price — 0.1× on Anthropic/DeepSeek,
0.25× on Google/Grok, 0.5× on Groq — instead of full price.

It is opportunistic and safe to leave on: providers that cache
automatically (OpenAI, Groq, DeepSeek, Grok, Z.AI, Gemini 2.5 — including
the default `openai/gpt-oss-120b`) ignore the breakpoint, and a prompt
under a provider's minimum cacheable prefix (1024 tokens for
OpenAI/Anthropic) simply doesn't cache and costs what it costs today.
Set `openrouter_prompt_caching: false` to opt out entirely.

Verify it is working via `GET /api/metrics` → `ai_doctor`:

```json
"ai_doctor": {
  "requests": 42, "tokens_in": 61000, "tokens_out": 4300,
  "prompt_caching": true,
  "cached_tokens_in": 38200, "cache_write_tokens_in": 9100,
  "cache_hit_ratio": 0.6262
}
```

`cached_tokens_in` is the share of `tokens_in` served from the provider's
cache (a subset, not an addition). A `cache_hit_ratio` stuck at `0.0`
over real multi-turn traffic means caching is not engaging — usually a
model whose provider doesn't cache, or first turns whose prompt is still
under the provider's minimum.

---

## 2. Docker Compose (recommended)

The repo ships a production-oriented compose file: non-root user,
read-only models volume, health check, restart policy.

```bash
cd server
export CORS_ORIGINS="https://plantdiseases.example.com"
export TRUSTED_PROXIES="127.0.0.1,10.0.0.0/8"
export RATE_LIMIT_RPS=2.0
docker compose up -d --build
```

Model weights + calibration artefacts are **not auto-downloaded**.
Populate the host `./models` dir (mounted at `/app/models`) with the
trained artefacts before starting — `detector.{pt,onnx}`,
`classifier.onnx` (+ optional `classifier_int8.onnx`), `classes.json`,
and the calibration files. The server is fail-fast — if it can't find
`detector.{pt,onnx}` and `classifier.onnx` it refuses to start and the
container exits.

Smoke test:

```bash
curl -s http://127.0.0.1:8000/api/health | jq
curl -F "image=@sample.jpg" http://127.0.0.1:8000/api/analyze | jq
```

---

## 3. Reverse proxy (nginx + Let's Encrypt)

TLS termination and HTTP→HTTPS redirect belong in nginx; the app
server talks plain HTTP on `127.0.0.1:8000`.

```nginx
# /etc/nginx/sites-available/plantdiseases.conf
server {
    listen 80;
    server_name plantdiseases.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name plantdiseases.example.com;

    ssl_certificate     /etc/letsencrypt/live/plantdiseases.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/plantdiseases.example.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;

    client_max_body_size 15m;          # must exceed MAX_FILE_SIZE
    proxy_read_timeout   60s;          # must exceed INFERENCE_TIMEOUT

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }
}
```

Issue and renew the certificate:

```bash
sudo certbot --nginx -d plantdiseases.example.com
sudo systemctl enable --now certbot.timer
```

Because nginx is now forwarding, set `TRUSTED_PROXIES=127.0.0.1` on the
app server so its rate limiter reads `X-Forwarded-For` honestly.

---

## 4. systemd unit (alternative to Docker)

Use this when Docker is not an option — a single Python venv plus a
user-scoped service.

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
Environment=RATE_LIMIT_RPS=2.0
Environment=FORWARDED_ALLOW_IPS=127.0.0.1
# Mutable state must live OUTSIDE the code dir: under ProtectSystem=strict the
# whole tree is read-only, so the defaults (which land in the server dir) can't
# be written. StateDirectory auto-creates /var/lib/plantdiseases (RW for the
# service user); point the SQLite stores AND the persistent LLM cache there.
# Miss this and AI_CACHE_DIR is silently unwritable, so the species "common
# problems" / care-plan cache never persists and every open re-pays the ~20 s
# OpenRouter call.
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
ExecStart=/opt/plantdiseases/.venv/bin/uvicorn main:app \
          --host 127.0.0.1 --port 8000 \
          --forwarded-allow-ips 127.0.0.1 --proxy-headers
Restart=on-failure
RestartSec=3
# Hardening
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
StateDirectory=plantdiseases
ReadWritePaths=/opt/plantdiseases/server/logs

[Install]
WantedBy=multi-user.target
```

```bash
sudo useradd --system --home /opt/plantdiseases plantd
sudo -u plantd python3.11 -m venv /opt/plantdiseases/.venv
sudo -u plantd /opt/plantdiseases/.venv/bin/pip install -r /opt/plantdiseases/server/requirements.txt
sudo systemctl daemon-reload
sudo systemctl enable --now plantdiseases
journalctl -u plantdiseases -f
```

---

## 5. Monitoring

- JSON counters: `GET /api/metrics`
- Prometheus exposition: `GET /api/metrics/prometheus`
  (`Content-Type: text/plain; version=0.0.4`)
- Liveness probe: `GET /api/health` — also used by the container `HEALTHCHECK`.

Both metrics endpoints are exempt from the rate limiter, so they are
safe to scrape every 15 s.

---

## 6. Pre-launch checklist

Before opening the service to the public:

- [ ] TLS enforced — HTTP redirects to HTTPS, HSTS enabled at the proxy.
- [ ] `CORS_ORIGINS` pinned to the real front-end origin(s); **no `*`**.
- [ ] `TRUSTED_PROXIES` set to just the reverse-proxy IP(s)/CIDR.
- [ ] `RATE_LIMIT_RPS` tuned for expected traffic (default `1.0` is per-IP).
- [ ] `MAX_FILE_SIZE` aligned with nginx `client_max_body_size`.
- [ ] `proxy_read_timeout` ≥ `INFERENCE_TIMEOUT`.
- [ ] Model artefacts present under `server/models/`: `detector.{pt,onnx}`, `classifier.onnx` (+ optional `classifier_int8.onnx`), `classes.json` — otherwise the server fails to start. They are not auto-downloaded; there is no demo fallback.
- [ ] `/api/health` returns 200 with `status: "ok"`.
- [ ] `classes.json` `num_classes` matches the classifier head size (the server logs a mismatch otherwise).
- [ ] Logs are rotating (`LOG_DIR`) and being shipped off-box.
- [ ] Prometheus/Grafana (or equivalent) is scraping `/api/metrics/prometheus`.
- [ ] Backup strategy for `server/logs/` and `server/models/` in place.
- [ ] Container runs as a non-root user (`Dockerfile` already does this).
- [ ] Docker image built from a tagged commit, not `latest`.
- [ ] Android app `network_security_config.xml` points at the production HTTPS host (HTTP cleartext is only allowed for `10.0.2.2` / `localhost`).
- [ ] Smoke test: `curl -F "image=@leaf.jpg" https://.../api/analyze` returns a bilingual diagnosis (HTTP 200).

---

## 7. Post-deploy smoke QA (Android client + server)

Run this on a real device with the production URL configured. Target: under 5 minutes end-to-end.

**Server reachability**
- [ ] `curl https://.../api/health` returns 200 with `status: "ok"`.
- [ ] `Profile → Server Diagnostics` shows green dot, server version and class count populated (no `—`).
- [ ] Tap the refresh button on the diagnostics card — values blink to `Checking…` and reload.

**Happy-path scan**
- [ ] Take a clear photo of a healthy leaf → diagnosis screen shows `Healthy` with confidence ≥ 70 %.
- [ ] Take a photo of a known-diseased leaf → correct disease label, treatment + prevention lists are non-empty in both EN and RU (toggle via `Profile → Language`).
- [ ] Scan appears in `Gallery` and `Profile` stats increment (`Total Scans`, healthy/diseased counts).

**Model families & offline (scan-screen model picker)**
- [ ] `curl https://.../api/capabilities` lists `aqua`, `gamma`, `frontier` with `available` flags; `gamma` true, `frontier` matches `frontier_enabled`.
- [ ] Scan-screen model chip opens the picker; `gamma` selectable, `aqua` shows an **Offline** badge, `frontier` is greyed **Unavailable** unless the server enables it.
- [ ] With `frontier_enabled=false`, `curl -F "image=@leaf.jpg" "https://.../api/analyze?family=frontier"` returns `{"available": false, ...}` (HTTP 200, no diagnosis).
- [ ] **Aqua offline**: if `static/aqua/` is provisioned, open the scan screen once online (model downloads), then enable airplane mode and scan with `aqua` selected → a diagnosis appears with **no network**. If not provisioned, picking `aqua` toasts "offline model isn't installed" and falls back to the server.
- [ ] Disease example photos still render on the result/guide screens (served by `/api/guide/photo/<key>`, disk-cached) — `curl -o /dev/null -w "%{http_code}" https://.../api/guide/photo/powdery_mildew` returns 200.

**Error states (each must show a clear card with a one-tap Retry that re-uses the same photo)**
- [ ] **Timeout**: throttle network or stop the API container mid-request → `Server Not Responding` card; image preview still shown above the error.
- [ ] **No network**: enable airplane mode → `No Internet Connection` card with retry CTA.
- [ ] **429 rate limit**: hit `/api/analyze` rapidly from same IP (or set `RATE_LIMIT_RPS=0.1`) → `Server Busy` card; retry succeeds after the `Retry-After` window.
- [ ] **503 server starting / pipeline unavailable**: restart the server and scan within the warm-up window → `Server is warming up` card; retry succeeds once `/api/health` returns 200.
- [ ] **Unsupported image (400/415)**: pick a non-image file or an unsupported format from the gallery → `Unsupported image` card; Retry button re-runs analysis on the same selection.
- [ ] After every error card, the captured image preview is still visible at the top of the screen and Retry does **not** re-prompt the user to pick a photo.

**Localization**
- [ ] Switch to Russian: every new diagnostics row (`Версия сервера`, `Классов болезней`) and every new error card title/body renders in RU with no `@string/...` fallbacks.

**Regressions**
- [ ] Onboarding still launches from `Profile → How to Use`.
- [ ] `Clear All History` empties stats and gallery.
- [ ] Light Meter entry still opens.

---

## 8. Android release: signing & FCM

The Android CI job (`.github/workflows/ci.yml`) runs lint + unit tests and assembles
the **R8-minified release APK** on every push touching `android/**`. It works with
**no** secrets — it falls back to the debug signing key and leaves push messaging
dormant. Add the secrets below to ship an APK signed with your own key and to enable
FCM. All Android secrets live in **GitHub → repo → Settings → Secrets and variables →
Actions**; `app/build.gradle.kts` reads each from a Gradle property **or** the
same-named env var, so nothing is committed.

### 8.1 Release signing

Generate an upload keystore (needs JDK `keytool`; keep the file + passwords safe — lose
it and you can't publish updates to the same Play app):

```bash
keytool -genkeypair -v -keystore plantdiseases-release.jks -storetype PKCS12 \
  -alias plantdiseases -keyalg RSA -keysize 2048 -validity 10000
base64 -w0 plantdiseases-release.jks > keystore.b64   # macOS: base64 -i … -o keystore.b64
```

Create these repo secrets (with PKCS12 the key password equals the store password):

| Secret | Value |
|---|---|
| `RELEASE_KEYSTORE_BASE64` | contents of `keystore.b64` |
| `RELEASE_STORE_PASSWORD` | keystore password |
| `RELEASE_KEY_ALIAS` | the alias (e.g. `plantdiseases`) |
| `RELEASE_KEY_PASSWORD` | key password (= store password for PKCS12) |

With all four set, CI decodes the keystore and emits a release-signed APK (artifact
`app-release-apk`); absent them it uses the debug key (sideload-installable, not for
Play). Don't commit the `.jks`/`keystore.b64`; delete `keystore.b64` after copying.
Build locally with `./gradlew :app:assembleRelease -PRELEASE_STORE_FILE=/abs/path.jks
-PRELEASE_STORE_PASSWORD=… -PRELEASE_KEY_ALIAS=plantdiseases -PRELEASE_KEY_PASSWORD=…`
(or put the props in `~/.gradle/gradle.properties`, outside the repo).

### 8.2 Firebase Cloud Messaging (FCM)

Two distinct credential sets:

- **Android client config** — `FCM_APP_ID`, `FCM_API_KEY`, `FCM_PROJECT_ID`,
  `FCM_SENDER_ID` (from `google-services.json`: `mobilesdk_app_id`,
  `api_key[0].current_key`, `project_id`, `project_number`). These are **public** —
  shipped inside every APK — so per Google they are *not* secrets. Inject them as
  Gradle props / GitHub secrets only to keep them out of the source tree, and harden
  by **restricting the Android API key** in Google Cloud Console (Application
  restrictions: Android apps + your package/SHA-1; API restrictions: Firebase/FCM).
  Unset → the app builds fine and push stays dormant (`PushManager.isConfigured()` is
  false; the client falls back to its WorkManager poll).
- **Server send-credential** — the Firebase **service-account JSON** that can actually
  send pushes. It is loaded from a path on the server via `push.fcm_credentials_file`
  (config.json) / `FCM_CREDENTIALS_FILE` (env), read in `server/push.py`. **Not** in
  this repo; empty → `/api/push/register` and sends are graceful no-ops.

### 8.3 CI

The `android` job exposes the secrets as env vars; the keystore is decoded only when
`RELEASE_KEYSTORE_BASE64` is set, and one Gradle invocation runs everything:

```bash
./gradlew ktlintCheck detekt testDebugUnitTest assembleRelease --build-cache --configuration-cache --stacktrace
```

### 8.4 RuStore in-app updates

The app bundles the [RuStore in-app updates SDK](https://www.rustore.ru/help/guides/sdk-in-app-updates)
(`ru.rustore.sdk:appupdate`, resolved from VK's artifactory — see
`android/settings.gradle.kts`). Nothing to configure at build or deploy time:

- **Only installs that came from RuStore get updates.** On launch,
  `util/InAppUpdateManager` asks the RuStore app whether a newer build is
  published; if so it shows the store's *flexible* update dialog (at most once
  per 24 h), downloads in the background, and offers an "Install" snackbar
  when the APK is ready. Sideloads / other stores / devices without RuStore:
  every call fails inside the SDK and the app behaves exactly as before.
- **To ship an update**, publish the new APK (higher `versionCode`) in the
  [RuStore Console](https://console.rustore.ru) — same artifact CI builds,
  signed with the release keystore (the signature must match the version
  already in the store).
- **Testing the flow end-to-end** requires a device with RuStore where the app
  was installed *from the store* and a published build with a higher
  `versionCode` — it cannot be exercised from a sideloaded debug build (the
  SDK reports `RuStoreNotInstalledException`/`ERROR_APP_NOT_OWNED` instead).

