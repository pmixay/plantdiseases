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

`server/config.json` is gitignored. JSON sections (`server`, `limits`,
`ai`, `plantnet`) are flattened so each leaf key maps to the same name
as the legacy environment variable.

| Key | Default | Purpose |
|---|---|---|
| `cors_origins` | `["http://localhost:3000","http://127.0.0.1:3000"]` | Allowed CORS origins. **Do not set to `["*"]` in production.** |
| `trusted_proxies` | `[]` | IPs / CIDRs whose `X-Forwarded-For` header is honoured. Required behind nginx / cloudflare; otherwise spoofed `X-Forwarded-For` is ignored. |
| `forwarded_allow_ips` | `127.0.0.1` | Passed to uvicorn `--forwarded-allow-ips`; controls which peers can update `Host` / scheme via forwarded headers. |
| `rate_limit_rps` | `1.0` | Per-IP request rate limit. Rate-limited responses return `429` with a `Retry-After` header. |
| `max_file_size` | `10485760` (10 MB) | Hard cap on uploaded bytes. |
| `max_image_dimension` | `4096` | Pixel limit per side, in addition to Pillow decompression-bomb guard. |
| `inference_timeout` | `30` | Seconds before `/api/analyze` returns `504`. |
| `infer_workers` | `2` | ThreadPoolExecutor workers for model inference. |
| `log_dir` | `logs` | Rotating file logs (5 MB × 5 backups). |
| `host` / `port` | `0.0.0.0` / `8000` | Uvicorn bind. |
| `openrouter_api_key` | *(empty)* | OpenRouter key. Without it the chat and care-plan polish return an offline fallback. |
| `openrouter_model` | `openai/gpt-oss-120b` | Pinned chat model. Pick anything OpenRouter exposes (e.g. `openai/gpt-4o-mini`, `meta-llama/llama-3.1-8b-instruct`). |
| `openrouter_base_url` | `https://openrouter.ai/api/v1` | Override only for OpenRouter-compatible self-hosted gateways. |
| `plantnet_api_key` | *(empty)* | PlantNet identify key (optional). |

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

