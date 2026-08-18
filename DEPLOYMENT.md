# Deployment Guide

Production deployment notes for the PlantDiseases FastAPI server. The
Android client needs no server-side change — point it at the public URL
through **Profile → Server URL**.

Configuration keys below match `server/main.py`, `server/ai_services.py`
and `server/config.example.json`. The server reads `server/config.json`
first; environment variables are still honoured as a fallback so existing
existing CI / systemd deployments keep working.

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
| `trusted_proxies` | `[]` | IPs / CIDRs whose `X-Forwarded-For` header is honoured. Required behind nginx / Cloudflare; otherwise `X-Forwarded-For` is ignored entirely. **Must list every hop between the client and the app** — see §3.1, which also covers how to verify the value you chose is actually being honoured. |
| `feedback_ip_pepper` (env `FEEDBACK_IP_PEPPER`) | *(empty → generated)* | Secret peppering the SHA-256 of a client IP before it is stored (`client_ip_hash` in the feedback / community / telemetry stores). IPv4 is only 32 bits, so a hash with a weak or known pepper enumerates back to the raw IP in seconds. Leave blank and the server generates a random one on first use and persists it as `.ip_hash_pepper` beside the feedback DB; **set it explicitly** to control rotation, to share one pepper across replicas, or if your state dir is read-only (see §4). Changing it resets the 24 h per-IP write quotas once. |
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
| `guide_static_dir` | `<server>/static/guide` | Directory of disease/pest example photos served by `GET /api/guide/photo/<key>`. Moved out of the Android APK to shrink the app; committed to the repo and served straight off disk. The client disk-caches them for offline use. |
| `aqua_static_dir` | `<server>/static/aqua` | Directory hosting the on-device **Aqua** TFLite model (`aqua_classifier.tflite` + `classes.json`), served by `GET /api/aqua/model` (+ `GET /api/aqua/manifest`). Provisioned out of band (not committed, like the weights); when absent the manifest reports `available:false` and the app uses the server model. The app downloads + caches it and re-downloads only when the sha changes. |
| `log_dir` | `logs` | Rotating file logs (5 MB × 5 backups). |
| `host` / `port` | `0.0.0.0` / `8000` | Uvicorn bind. |
| `openrouter_api_key` | *(empty)* | OpenRouter key. Without it the chat and care-plan polish return an offline fallback. |
| `openrouter_model` | `openai/gpt-5.6-luna` | Pinned chat model. Pick anything OpenRouter exposes; the AI content surfaces expect a model with **structured outputs** (`response_format: json_schema`) and reasoning-effort support. A model without them still works — the schema is dropped after one rejected request and the tolerant JSON parsers take over. |
| `openrouter_base_url` | `https://openrouter.ai/api/v1` | Override only for OpenRouter-compatible self-hosted gateways. |
| `openrouter_provider_only` | `openai` | Comma-separated provider slugs the model may be routed to; blank = OpenRouter's choice. See "Provider pinning" below. |
| `openrouter_reasoning_effort` | `medium` | Global fallback effort for callers that don't pin their own. `max`/`xhigh`/`high`/`medium`/`low`/`none`, or `omit` to send no reasoning field. A stale `minimal` (gpt-5.0-era, rejected by gpt-5.6) is mapped to `low` rather than failing the call. |
| `species_profile_reasoning_effort` | `high` | Flowering calendar + ISO-3166 native range — the hardest recall here, cached 30 days per species. |
| `species_diseases_reasoning_effort` | `medium` | Per-species common problems, cached 7 days. |
| `care_plan_polish_reasoning_effort` | `medium` | Care-plan Markdown rewrite; judged on copying canonical dosages verbatim. |
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
   `openrouter_model` (default `openai/gpt-5.6-luna`).
3. Restart the server. The care-plan endpoint still returns a
   complete, deterministic plan without a key; only the
   `summary_md` polish step is skipped.

**Provider pinning.** One OpenRouter model id is served by several
providers, and the price spread is not a rounding error — for
`openai/gpt-5.6-luna`:

| Endpoint | Input | Output | Reachable how |
|---|---|---|---|
| `openai` | $0.10/M | $0.60/M | default routing or pin |
| `openai/flex` | $0.05/M | $0.30/M | **opt-in only** (tier slug) |
| `amazon-bedrock/us-east-1` | $0.22/M | $1.32/M | default routing or pin |
| `azure` | $1.00/M | $6.00/M | default routing or pin |

Leaving routing to OpenRouter does *not* mean it picks the best option,
in either direction:

* Default routing is price-weighted load **balancing** (inverse square of
  price, among providers with no recent outage), not cheapest-first — the
  expensive endpoints keep a real share of traffic.
* A provider that doesn't support a parameter still receives the request
  and silently **ignores** the parameter. Balanced onto Bedrock (no
  `response_format`), a species-profile call comes back as unconstrained
  prose. That is why every request also carries
  `provider.require_parameters = true`, which is what makes an unpinned
  deployment safe.
* Cheaper service tiers are **not** reachable automatically. Tier
  endpoints are excluded from default routing and from base-slug matching,
  so `only: ["openai"]` never yields flex, and blanking the pin never
  yields it either.

So `openrouter_provider_only` defaults to `openai`.

**About flex.** It is 50% off and opt-in by slug
(`openrouter_provider_only: "openai/flex"`). Two things to weigh before
switching: a flex request never falls back to standard on a capacity
error — that would break its price guarantee, so you get a 429 (retried
here with backoff, into the same tier) — and OpenAI documents flex as
unsuitable for real-time work: normally seconds, minutes under load.
Every AI call in this server is made while a user waits on a screen (the
care-plan polish runs inside the `/api/care-plan` request), so the trade
is a visible stall against a fraction of a cent per call. Fine for a
deployment that values cost over tail latency; not the default.

The pin is a request, not a receipt — billing follows the endpoint that
actually ran, and a service tier can decline. Confirm what really served
a request in OpenRouter's own activity log, which reports the provider,
the tier and the charge per call.

**Reasoning effort.** gpt-5.6 treats `reasoning.effort` as a *ceiling*,
not a floor: on a prompt it judges easy the model may spend no reasoning
tokens at all, even at `high`. So each caller asks for the effort its own
task needs (the table above) rather than inheriting one global setting,
and a higher setting costs nothing on the calls that don't use it. Two
things to keep in mind when tuning:

* The hidden trace bills against `max_tokens`, so raising an effort means
  raising the matching `*_max_tokens` (`chat_max_tokens`,
  `species_profile_max_tokens`, `species_diseases_max_tokens`,
  `care_plan_polish_max_tokens`). A call that runs out mid-answer is
  rejected outright (`require_complete`) and degrades to the
  deterministic fallback rather than serving a truncated one.
* Reasoning tokens also count against `chat_session_token_cap`, which is
  why that cap is 32000 — low enough values end a doctor conversation
  several turns before `chat_session_turn_cap` does.

**Structured outputs.** The species-profile and species-diseases calls
send a strict JSON schema (`response_format: json_schema`) instead of
asking for a shape in prose, so the shape is a provider-side constraint
rather than an instruction the model can drift from. The tolerant parsers
and sanitisers stay in place regardless — a schema constrains shape, never
truthfulness. If the configured model/provider rejects the schema, the
server logs it, retries once without it and carries on.

**Cache generations.** Every AI answer is cached twice — server-side
(in-memory + `server/ai_cache/`, 7 days for species diseases and care-plan
polish, 30 days for species profiles) and again on the device
(`ResponseCache`, 7 days). Those caches are the reason a model or prompt
change can appear to do nothing: the old answer keeps being served until
it expires. So the cache keys carry a generation, and this change bumps
all of them — species profile `v3`→`v4`, species diseases → `v2`,
care-plan polish dir → `care_plan_polish_v3`, plus the matching Android
keys and the `profile_version` query parameter. Superseded entries are
never read again; the orphaned JSON files under `server/ai_cache/` are
harmless and can be deleted at any time (`rm -rf
server/ai_cache/care_plan_polish_v2`). Bump the generation whenever you
change a prompt, model or effort in a way that should reach users before
the TTL does.

**Prompt caching (on by default).** Every chat turn re-sends the same
~600-token system prompt plus the conversation so far. With
`openrouter_prompt_caching` enabled the server asks OpenRouter to cache
that prefix and pins each conversation to one provider (a hashed
`session_id`, so no client identifier leaves the box), so repeat turns
are billed at a fraction of input price — 0.1× on Anthropic/DeepSeek,
0.25× on Google/Grok, 0.5× on Groq — instead of full price.

It is opportunistic and safe to leave on: providers that cache
automatically (OpenAI, Groq, DeepSeek, Grok, Z.AI, Gemini 2.5 — including
the default `openai/gpt-5.6-luna`, whose cached input reads bill at 0.1×)
ignore the breakpoint, and a prompt
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

The same endpoint carries `care_plan_polish`, the quality signal for
care-plan prose:

```json
"care_plan_polish": {
  "attempts": 120, "accepted": 117, "rejected": 2, "failed": 1,
  "reject_ratio": 0.0167
}
```

`rejected` counts polishes the model returned but the canonical-dosage
guard refused (it contradicted the plan's own watering / feeding / NPK
numbers); `failed` counts calls that produced no usable text at all. Both
fall back to the deterministic summary, so neither is visible to the user
— which is exactly why they need watching. A `reject_ratio` that climbs
after a model or prompt change means the new configuration is drifting off
the canonical numbers and the LLM summary is being silently thrown away.

---

## 2. Running the server

The server is a plain Python process — `uvicorn` over a virtualenv, with
nothing containerised. `server/start.sh` creates the venv, installs CPU torch
plus `requirements.txt`, and starts uvicorn on `:8000`. That is fine for a
first run; for anything long-lived use the systemd unit in §4, which wraps the
same command with restart, logging and sandboxing.

Supported Python is **3.14**; CI runs the suite against it. Configuration
comes from `server/config.json` (copy `config.example.json`), with
environment variables as the fallback — so the same settings can be
exported before starting:

```bash
cd server
export CORS_ORIGINS="https://plantdiseases.example.com"
export TRUSTED_PROXIES="127.0.0.1,10.0.0.0/8"
export RATE_LIMIT_RPS=2.0
./start.sh
```

**Bind to loopback.** The API speaks plain HTTP and TLS terminates in nginx
(§3), so uvicorn must not be reachable from off the host. The production
invocation — what the systemd unit in §4 runs — is:

```bash
uvicorn main:app --host 127.0.0.1 --port 8000 \
  --forwarded-allow-ips 127.0.0.1 --proxy-headers \
  --limit-concurrency 64 --timeout-keep-alive 5
```

`--limit-concurrency 64` caps simultaneously-accepted connections: past that
uvicorn answers 503 rather than queueing unboundedly, so a slowloris-style
flood cannot exhaust the event loop. With `infer_workers=1` a scan's heavy
work is serialised through a single executor slot anyway.

Binding `0.0.0.0` is only correct when the TLS terminator is **not** on this
host — a cloud load balancer or a separate proxy machine. In that topology the
host firewall is what protects the port, so verify it actually does.

Memory is bounded by the systemd unit (`MemoryMax=`, §4), which matters when
`frontier_enabled=true` (ViT-B/14 is ~350 MB of weights plus its own arena) or
`infer_workers > 1`. Note that a `MemoryMax` kill plus `Restart=always` becomes
a restart loop paying the ~18 s cold model load each cycle — size it with
headroom rather than tightly.

Model weights + calibration artefacts are **not auto-downloaded**. Populate
`server/models/` with the trained artefacts before starting —
`detector.{pt,onnx}`, `classifier.onnx` (+ optional `classifier_int8.onnx`),
`classes.json`, and the calibration files. The server is fail-fast: if it
can't find `detector.{pt,onnx}` and `classifier.onnx` it refuses to boot.

Smoke test:

```bash
curl -s http://127.0.0.1:8000/api/health | jq
curl -F "image=@sample.jpg" http://127.0.0.1:8000/api/analyze | jq
```

Note this passes whether or not the port is reachable from off the host, so it
cannot tell you the bind address was right. Verify the way real traffic
arrives — through nginx, from another machine:

```bash
curl -s https://plantdiseases.example.com/api/health | jq
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

Because nginx is now forwarding, set `TRUSTED_PROXIES` on the app server so its
rate limiter reads `X-Forwarded-For` honestly — but read §3.1 first, because the
right value is often *not* `127.0.0.1`.

### 3.1 Getting the client IP right

`X-Forwarded-For` decides the identity every abuse control keys on: the
rate-limiter bucket and the hashed `client_ip_hash` behind each 24 h write
quota. Get it wrong and you either throttle nobody or throttle everybody.

`$proxy_add_x_forwarded_for` above **appends** the peer nginx sees to whatever
the client already sent, so the header the app receives is
`<whatever the client claimed>, <real peer>`. The app therefore walks the list
**right to left**, skipping entries that are in `TRUSTED_PROXIES`, and takes the
first one that is not — the leftmost entries are attacker-supplied and must
never win.

Two ways this goes wrong:

**A CDN in front of nginx.** With Cloudflare, the header arrives as
`<real client>, <CF edge>`. Because CF's ranges are not in `TRUSTED_PROXIES`,
the app stops at the CF edge IP — so *every* visitor through one PoP shares a
single rate-limit bucket and a single daily write quota. That fails **closed**:
mass `429`s and users locked out of feedback. Fix it in nginx, so the real
client is what gets appended:

```nginx
    # Cloudflare: https://www.cloudflare.com/ips/ — keep this list current
    set_real_ip_from 173.245.48.0/20;   # (…all CF ranges…)
    real_ip_header   CF-Connecting-IP;
```

Adding CF's ranges to `TRUSTED_PROXIES` also works, but it is more brittle —
that list changes.

**The peer address is what must match, not the address you expect.** With
nginx on the same host proxying to `127.0.0.1:8000`, the peer uvicorn sees is
`127.0.0.1` and that is what `trusted_proxies` must contain. Put any other
network hop in front — a proxy on a second machine, a NAT, anything that
rewrites the source address — and a literal `127.0.0.1` never matches, the
header is ignored, and every client collapses into one rate-limit bucket.

**Verify rather than assume** — every request logs the resolved address:

```bash
journalctl -u plantdiseases | grep -o 'IP=[^ ]*' | sort | uniq -c | sort -rn | head
```

A healthy spread of client addresses is right. One address repeated for every
request means the header is not being honoured and all clients share a bucket.

---

## 4. systemd unit

How the server should actually run in production — a single Python venv plus
a user-scoped service.

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
          --forwarded-allow-ips 127.0.0.1 --proxy-headers \
          --limit-concurrency 64 --timeout-keep-alive 5
Restart=on-failure
RestartSec=3
# Hardening
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
StateDirectory=plantdiseases
ReadWritePaths=/opt/plantdiseases/server/logs
# Bounds the blast radius of a decompression-bomb or oversized-image request
# to this service instead of letting the host OOM-killer pick a victim. Raise
# it for frontier_enabled=true (ViT-B/14 weights + its own arena) or
# infer_workers > 1. Do not size it tight: a kill plus Restart=on-failure is a
# restart loop paying the ~18 s cold model load every cycle.
MemoryMax=4G

[Install]
WantedBy=multi-user.target
```

```bash
sudo useradd --system --home /opt/plantdiseases plantd
sudo -u plantd python3 -m venv /opt/plantdiseases/.venv   # 3.14
sudo -u plantd /opt/plantdiseases/.venv/bin/pip install -r /opt/plantdiseases/server/requirements.txt
sudo systemctl daemon-reload
sudo systemctl enable --now plantdiseases
journalctl -u plantdiseases -f
```

---

## 5. Monitoring

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
- [ ] Service runs as a non-root user (the systemd unit in §4 sets `User=plantd`).
- [ ] Deployed from a tagged commit, not a moving branch.
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

Also create `RELEASE_CERT_SHA256` as a variable in that environment and configure
required reviewers, prevent self-review, and restrict deployment branches to
`main` only. The protected workflow refuses an APK whose
certificate differs from the pinned fingerprint. Never commit the keystore or
its base64 encoding.

The Yandex native client ID is pinned in the Android source and needs no
Actions variable. Optionally create the public/extractable
`STAGING_VKID_CLIENT_ID` / `STAGING_VKID_CLIENT_SECRET` and equivalent
`PRODUCTION_*` VK variables only after VK registration; each pair must be
complete, and leaving both pairs absent keeps VK disabled. Also create the
exact reviewed `STAGING_APP_API_BASE_URL` /
`STAGING_ACCOUNT_API_BASE_URL` German pair, and the reviewed
`PRODUCTION_APP_API_BASE_URL` / `PRODUCTION_ACCOUNT_API_BASE_URL`. Both the
normal staging APK and `recovery-staging` consume the same explicit staging
pair; the workflow refuses any drift from the reviewed URLs. Copy the two
production values into the
protected `android-signing` environment under the distinct names
`APPROVED_PRODUCTION_APP_API_BASE_URL` and
`APPROVED_PRODUCTION_ACCOUNT_API_BASE_URL`. The protected environment contains
only the four permanent-key secrets above plus those approved URL variables,
`RELEASE_CERT_SHA256`, and `PRODUCTION_RUSSIAN_CUTOVER_APPROVED`. Keep the last
variable `false` until the exact production HTTPS routes have passed the Russian
ingress/account smoke matrix. The checkout-free signing job requires the
repository URLs to match the protected approved URLs exactly. It also rejects
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
retention placeholder. Production is additionally blocked while the handoff
records the cross-store backup epoch and durable deletion replay journal as a
deferred decision. The two recovery channels stay available during these holds.
An approval changes `release-policy-gates.json` schema 2 and the matching
structured markers in `privacy.html`: the contact must be an exact visible
`mailto:` or HTTPS link, and the English/Russian tombstone disclosures must
exactly match the approved retention mode. The JSON also requires an HTTPS
decision reference and UTC approval timestamp. Prose substring matches cannot
clear the gate.

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
