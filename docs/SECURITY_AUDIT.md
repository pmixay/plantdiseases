# Security audit — August 2026

Scope: the whole repository at `22b58bc` (server `main` lineage) — the FastAPI +
ONNX inference API in `server/`, the Kotlin client in `android/`, the CI
workflow, the container definition, and the dependency sets of both
deliverables. Method: dependency/CVE research against PyPI, Maven Central,
Google's Maven, OSV and Docker Hub, plus a parallel hot-path review across nine
areas (HTTP layer, SSRF/outbound, persistence & PII, ML inference, Android
network, Android platform, CI/supply-chain, secrets, cross-cutting), each
followed by an adversarial pass whose job was to *refute* what the first pass
claimed.

**Final tally: 49 findings raised, 38 adversarially verified — 22 confirmed, 16
refuted.** Five of those refutations are themselves wrong; see §1. The
refutation rate is the point of the exercise, not a defect in it: a third of
what a first-pass review produces does not survive someone actively trying to
break it.

---

## 1. Two corrections to this report's own earlier draft

Accuracy notes, kept visible rather than silently edited.

### 1.1 The Pillow finding was overstated

An earlier draft (and commit `d553953`) called Pillow 12.2.0 **Critical**, on the
grounds that CVE-2026-59199 — a heap out-of-bounds write in
`Image.paste()`/`Image.crop()` — was "reached on every scan" by the Stage-1
letterbox paste and Stage-2 box crops. **That is wrong**, in two independent
ways:

1. CVE-2026-59199 needs coordinates near the int32 limit.
   `MAX_IMAGE_DIMENSION = 4096` (`main.py:553`) is enforced before any crop, so
   those coordinates are unreachable.
2. The format-specific CVEs (EPS infinite loop, JPEG2000 realloc, PDF, TGA, and
   the BDF/PCF/GD bomb-check bypasses) need those formats to reach a decoder.
   Both decode paths gate on magic bytes first:
   - scan/identify — `_validate_image_bytes` (`main.py:1396`) allows only
     JPEG/PNG/WebP/BMP and runs at `main.py:1911` and `2747`, *before*
     `_open_image`;
   - photo proxy — `plant_photos._sniff` (`plant_photos.py:167`) caches only
     JPEG/PNG/WebP/GIF and returns `None` for anything else, so the later
     `Image.open(src)` in the WebP transcode (`main.py:4050`) only ever sees
     those four.

**Corrected assessment: Medium.** 12.2.0 genuinely carries 26 advisories and
12.3.0 clears all of them, so the pin is still right and still worth shipping —
but no specific CVE was demonstrated reachable in this codebase, and the two
magic-byte gates are the reason. Credit where due: those gates are good design
that a lazier audit would have missed.

### 1.2 Five refutations are false — caused by auditing a moving tree

Fixes were committed while the review was still running, so verifiers read the
*already-corrected* code and concluded the quoted vulnerable lines never
existed. Three were labelled "hallucinated evidence"; one verifier cited the
newly-added `_is_trusted_proxy` helper and the new *"There is deliberately NO
hard-coded default"* comment as proof of fabrication. Git disagrees:

```
origin/main:server/main.py:955      # Take the leftmost non-empty entry that parses as an IP
origin/main:server/feedback.py:50   _IP_HASH_PEPPER = get_str("FEEDBACK_IP_PEPPER", "plantvision-static-pepper-v1")
origin/main:server/docker-compose.yml:8      - "8000:8000"
```

Falsely refuted, all real and all fixed: the `X-Forwarded-For` spoof (×2, raised
by two independent reviewers), the hard-coded IP pepper (×2), and the release
cleartext exemption (×1). A sixth (multipart body size) leans partly on the
loopback-binding change, though the verifier independently confirmed the
underlying Starlette behaviour.

**Process fix for next time: freeze the tree, or review a fixed commit.** A
verifier reading a moving tree produces false negatives, which are the expensive
kind. The remaining ten refutations in §5 were dismissed on their merits and
stand.

---

## 2. Fixed on this branch

Verified locally (server suite 529 → **540 passed**, ruff clean) and green in
CI, including the Android `ktlintCheck detekt testDebugUnitTest assembleRelease`
job.

| # | Finding | Severity | Where |
|---|---|---|---|
| 1 | **`X-Forwarded-For` was attacker-controlled.** `_client_ip` took the *leftmost* entry, but the nginx config in `DEPLOYMENT.md:288` forwards `$proxy_add_x_forwarded_for`, which *appends* the real peer. One forged header chose the caller's identity — and that identity keys the rate limiter, the request log, and the hashed `client_ip` on feedback, community and telemetry writes. The regression test shows 29 forged headers producing 29 distinct rate-limit buckets before the fix. Now walks right-to-left, skipping trusted hops. | **Critical** | `main.py:923`, `tests/test_client_ip.py` |
| 2 | **"Irreversible" IP hashes were reversible.** The pepper defaulted to a literal committed in this public repo. IPv4 is a 32-bit space, so a SHA-256 with a known pepper enumerates back to the raw address in seconds — every `client_ip_hash` stored beside photos, coordinates and pseudo-ids was effectively a plaintext IP. `FEEDBACK_IP_PEPPER` appeared in no deployment doc, so deployments ran on the default. Now: no hard-coded default; a random 256-bit pepper minted on first use and persisted `0600` beside the DB. | **High** | `feedback.py:48` |
| 3 | **CI handed the release keystore to every third-party action.** Signing and FCM secrets sat in a job-level `env:`, injected into *every* step — `checkout`, `setup-java`, `setup-android`, both Gradle actions. Each secret is now scoped to the one step that uses it; a boolean step output carries the "is signing configured" decision instead of the keystore blob. | **High** | `ci.yml` |
| 4 | `GITHUB_TOKEN` inherited the repo default (often read-write). Added `permissions: contents: read` and `persist-credentials: false` on every checkout. | **High** | `ci.yml` |
| 5 | **Pillow 12.2.0 → 12.3.0**, clearing 26 advisories. See §1.1 — the class is worth closing, but severity here is Medium, not Critical. | Medium | `requirements.txt` |
| 6 | Third-party actions referenced by mutable tag — a tag can be silently repointed. Pinned to commit SHAs. | Medium | `ci.yml` |
| 7 | The Gradle distribution was downloaded and executed unverified. Added `distributionSha256Sum` + a `wrapper-validation` step. (The committed wrapper JAR turned out to be a genuine Gradle 8.14 build — stale, not tampered — but nothing in the repo could have told the difference.) | Medium | `gradle-wrapper.properties`, `ci.yml` |
| 8 | `docker-compose` published on `0.0.0.0:8000`, bypassing both the documented nginx TLS termination and any host firewall (Docker's DNAT precedes `INPUT`). Bound to loopback; added `no-new-privileges` and a memory limit. | Medium | `docker-compose.yml` |
| 9 | Base image `python:3.11.9-slim` was six CPython patch releases behind. Now `3.11.15-slim`, pinned by digest. | Medium | `Dockerfile` |
| 10 | The `pull_request` trigger the workflow comment already promised did not exist, so fork PRs and off-pattern branches reached `main` unchecked. Added. | Medium | `ci.yml` |
| 11 | `.gitignore` only matched `android/local.properties`, never the `android/app/local.properties` Android Studio actually writes — which is how a file containing the maintainer's workstation path got committed. Untracked it, widened the pattern, and added keystore/`.env` patterns (`DEPLOYMENT.md` walks the developer through creating a `.jks` and its base64 blob using *relative* paths). | Medium | `.gitignore` |
| 12 | The localhost/`10.0.2.2` cleartext exemption shipped in the release APK. Moved to a debug-only resource variant. | Low | `network_security_config.xml` |
| 13 | No automated dependency updates existed — the root cause of every item in §4. Added `.github/dependabot.yml` covering pip, gradle, docker and github-actions. | — | `dependabot.yml` |

---

## 3. Confirmed open findings

All 22 survived adversarial verification. Nothing here is fixed — these are
behaviour changes to a live consumer app (cost controls, retention, privacy
posture), so they are written up for scoping rather than applied unilaterally.

### 3.1 An unauthenticated caller spends your money or your uptime

This is the dominant theme of the audit. The rate limiter is the *only* brake on
every one of these, and it is a 2 req/s token bucket — generous enough that an
attacker who simply stays under it is never throttled at all.

| Finding | Sev | Where |
|---|---|---|
| **Unmetered billed LLM/PlantNet calls.** Caches are keyed on client-chosen strings, so every distinct `name` (validated only as 1–128 chars) is a guaranteed miss and a fresh billable completion at `reasoning_effort="high"`, `max_tokens=4096`. No global budget, no per-client cap, no circuit breaker. | **High** | `ai_services.py:1257`, `main.py:3865`, `2001` |
| **Unbounded disk fill via `/api/feedback/image`** — the daily quota reads a counter this endpoint never increments, so it can never trigger, and stored images are never deleted. | **High** | `main.py:3043` |
| **No inference admission control.** With `INFER_WORKERS=1`, a client *within* the rate limit queues full-resolution decoded images and pins RAM; one IP can starve every other user. | **High** | `main.py:2004`, `782` |
| **`/api/metrics` is exempt from rate limiting** and runs full-table SQLite aggregates on the event loop — unauthenticated, unthrottled, blocking. | Medium | `main.py:1711` |
| **`/api/plant-photo` is a bandwidth/disk amplifier** with an unbounded WebP variant set; the LRU caps file *count*, not bytes. Any client can also evict every warm photo. | Medium / Low | `main.py:4041`, `plant_photos.py:184` |
| **`/api/progression` takes an unbounded list** and processes it on the event loop, while every comparable field in the file is length-capped. | Medium | `main.py:2491` |
| **`/api/chat` caps are decorative** — `session_id` is client-chosen and never minted or signed, so a fresh id per request resets turn count and token budget. | Low | `main.py:2226` |

### 3.2 Integrity & data quality

| Finding | Sev | Where |
|---|---|---|
| **`/api/feedback/image` validates nothing** — no magic-byte check, no decode — unlike `/api/analyze` and `/api/community/scan`. Arbitrary bytes flow straight into the retraining corpus. | Medium | `main.py:3068` |
| **Prompt-cache poisoning.** `/api/species-diseases` concatenates the client's `common` parameter into the prompt but omits it from the cache key, so one request poisons the *shared* answer every later user reads. | Medium | `ai_services.py:1257` |
| **`/api/analyze` cache replays another user's `scan_id`.** The key is the SHA-256 of the upload bytes with no notion of who uploaded them — and `scan_id` is the authorisation token for the mutating endpoints. | Low | `main.py:1972` |
| **Forgeable second-opinion consensus** — `author_pseudo_id` is client-supplied, so per-author dedup does not bind. | Low | `community.py:636` |
| **Prompt injection via `/api/care-plan`** returns attacker-steered free-form LLM text. | Low | `ai_services.py:2614` |

### 3.3 Privacy

Most of the privacy findings were refuted (§5) — the community surface is
better guarded than the first pass assumed. These two survived:

| Finding | Sev | Where |
|---|---|---|
| **Raw camera JPEG with EXIF GPS intact** is uploaded verbatim to the training corpus on negative feedback, while `ImageUtils.prepareImageForUpload` exists and would strip it. `privacy.html:87` says verified data may be published as an open training dataset. | Medium | `ScanRepository.kt:576` |
| Same geotagged original forwarded off-platform to third-party PlantNet. | Low | `ScanRepository.kt:238` |
| Per-install pseudo-id — a bearer capability — travels in GET paths and query strings, so it lands in server and proxy access logs. | Low | `ScanRepository.kt:775` |

### 3.4 Infrastructure disclosure (not a credential leak)

**The production host's connection profile is in git history** — added in
`cd4bda2`, removed with the admin panel in #149, still reachable via
`git log --all -p`. It names the host IP, SSH user `root`, the deploy paths,
the systemd unit and the health endpoint (`admin-panel/README.md:34`,
`app/static/app.js:15`, `static/index.html:209`, `tests/test_models_security.py:37`).

**No credentials leaked, and this was originally over-stated in this report.**
Verified across the whole history: no private key (`git grep "BEGIN … PRIVATE
KEY"` over `git rev-list --all` returns nothing) and no password.
`admin-panel/app/ssh_client.py:309-318` takes the key path or password at
runtime as a per-connection `SecretStr`, with `look_for_keys=False`, and the
README explicitly calls the committed values "non-secret settings" that still
require a verified fingerprint plus a local key or password. That
classification was correct.

So the exposure is **recon value, not access**: a public repo points at a
specific host and tells an attacker that `root` is the SSH user to target. The
action is therefore ordinary hardening, made more urgent by having a named
target — not rotation, since there is nothing to rotate:

- `PermitRootLogin prohibit-password` (or `no`, with a sudo-capable non-root
  account), and `PasswordAuthentication no`;
- rate-limit/ban SSH (fail2ban or equivalent) and firewall port 22 to known
  source addresses if practical;
- if the host IP is worth un-publishing, that requires a history rewrite —
  optional, and lower value than the two items above.

Severity: **Low**. Recorded because "your production host and its SSH username
are public" is worth someone deciding about deliberately, not because anything
was compromised.

---

## 4. Dependencies

Updated to latest stable **except where a ceiling is real**. Every ceiling below
was established from artefact metadata or a failing build, and is documented
inline so the next person does not rediscover it.

**Server** — Pillow `12.3.0`, FastAPI `0.141.1`, uvicorn `0.52.1`, onnxruntime
`>=1.28.0`, requests `>=2.34.2`, ImageHash `>=4.3.2`, firebase-admin `>=7.5.0`,
pytest `>=9.1`, ruff `>=0.16`, scikit-learn `>=1.9`; base image `3.11.15-slim`.

**Android** — compileSdk 35 → **36**, targetSdk deliberately held at 35 (it is
what opts the app into Android 16's mandatory edge-to-edge — a UI decision, not
a side effect of a dependency refresh). ~30 artefacts moved: core-ktx `1.18.0`,
activity `1.13.0`, CameraX `1.6.1`, Room `2.8.4`, lifecycle `2.11.0`, Material
`1.14.0`, navigation `2.9.8`, work `2.11.2`, DataStore `1.2.1`, gson `2.14.0`,
firebase-bom `34.17.0`, Robolectric `4.16.1`, and the test stack.

### Ceilings — do not "just bump" these

| Held at | Blocked by |
|---|---|
| **numpy `>=2.4,<3.0`** | numpy 2.5.0 requires Python ≥ 3.12; this service runs 3.11. Raising the floor makes the install *unresolvable*, not newer. |
| **Gradle `9.5.0`** | Gradle 9.6.0 removed `org.gradle.api.problems.internal.InternalProblems`, which AGP 8.13.2 still uses. Fails during plugin application. |
| **LiteRT `2.1.3`** | 2.1.4+ ship Kotlin metadata `2.3.0`; the Kotlin 2.1.21 compiler accepts only one minor ahead. 2.1.0–2.1.3 carry `2.2.0`, which is why the previous 2.1.0 built. |
| **Hilt `2.57.2`** | 2.58+ depend on kotlin-stdlib 2.2.20, 2.60+ on 2.3.21 — newer than the compiler in use. |
| **kotlinx-serialization `1.8.1`** | 1.9.0 needs stdlib 2.2.0, 1.11.0 needs 2.3.20. |
| **coroutines `1.10.2`** | 1.11.0 needs stdlib 2.2.20. |
| **core-ktx `1.18.0`** | 1.19.0 declares `minCompileSdk=37` and `minAgp=9.1.0`. |
| **Glide `4.16.0`**, **OkHttp `4.12.0`**, **Ktor `2.3.13`** | Major-version migrations with breaking APIs; Glide 5.0.9 also needs compileSdk 37. |

Two upgrade hazards that a version-string-only bump would have shipped silently:

- **Material 1.14.0 stopped declaring `colorPrimary` and `borderlessButtonStyle`
  in its own `R.txt`** (1.12.0 declared both). With
  `android.nonTransitiveRClass=true` that broke four references, now repointed
  to `androidx.appcompat.R.attr.*`. Every remaining
  `com.google.android.material.R.attr.*` reference was checked against 1.14.0's
  `R.txt`, not just the ones the compiler happened to report.
- **LiteRT renamed `libLiteRtOpenClAccelerator.so` → `libLiteRtClGlAccelerator.so`**,
  so the packaging exclude stopped matching and the unused GPU delegate would
  have shipped in the APK. Both names are now excluded.

### Still open

- **`starlette` is unconstrained.** FastAPI requires `starlette>=0.46.0` with no
  upper bound, and nothing else pins it. It resolves to 1.4.1 today (clean), but
  the permitted range includes versions with real advisories — 0.49.3 carries
  CVE-2026-54283 (`request.form()` ignores `max_fields`/`max_part_size` for
  urlencoded bodies), CVE-2026-48710 and CVE-2026-54282 (Host/path not validated
  before URL reconstruction). Worth a floor.
- **`firebase-admin` 7.5.0 hard-pins `httpx[http2]==0.28.1`**, which will
  conflict with `requirements-dev.txt`'s `httpx>=0.28` the moment httpx 0.29
  ships — a surprise build break rather than a vulnerability. It also drags
  grpcio, cryptography, pyjwt and three google-cloud packages into the runtime
  image for a subsystem that is a documented no-op without credentials. Consider
  an extra.
- No lockfile or hash pinning; dependencies install from `>=` floors into both
  the production image and CI. Consider `pip-compile --generate-hashes` +
  `pip install --require-hashes`.
- ktlint (`12.1.2` → `14.2.0`) deliberately not bumped: the engine is pinned
  separately at `1.3.1`, and moving it reformats 128 Kotlin files and
  invalidates the checked-in baselines — real churn, no security benefit. detekt
  `1.23.8` is already current.

---

## 5. Refuted — do not re-raise

Dismissed on their merits (unlike the five in §1.2). Recorded because the
reasoning is worth keeping.

- **`/api/push/register` token hijack** — `user_pseudo_id` is a 122-bit UUIDv4
  minted per install; the server never returns it (the community feed exposes
  only a one-way, 30-day-rotating digest), so there is no reachable entry point.
- **`/api/community/map` k-anonymity** — the premise that community upload
  defaults to ON after first-scan consent is false, so the corpus is not "most
  users".
- **`tools/export_data.py` PII leak** — an offline maintainer CLI run by hand on
  the server box. No attacker, no attack.
- **Server-side EXIF/GPS forwarding** — the geotagged data no longer exists by
  the time it reaches the server. (The *client-side* half of this survived; see
  §3.3.)
- **`ACTION_VIEW` scheme allowlist** and **`photoUrl()` absolute-URL
  passthrough** — the server never emits an attacker-settable value into either
  field; `page_url` comes from Wikipedia server-side and photo URLs are
  rewritten to relative proxy paths before serialisation.
- **Community note in notifications** — gated by an explicit opt-in the first
  pass did not read.
- **`/api/community/request-opinion`** — quota mismatch is real but the impact is
  negligible and the ownership half has a missing precondition.
- **`/api/plant-photo?name=` amplifier** — the fan-out arithmetic did not hold up.
- **`plant_photos` blind SSRF via redirects** — ordering observation correct
  (`requests` follows redirects before `is_allowed_url(resp.url)` runs), but no
  exploitable impact: the body is never read or returned, and reaching an
  internal host needs an open redirect on a Wikimedia property.
- **Multipart body-size limit** — Starlette does spool file parts unbounded, but
  the attack premise assumed a directly-exposed port that no longer exists.

---

## 6. Not covered

- No runtime/DAST testing — the server needs model artefacts that are not in the
  repo, so every finding here is from source review.
- No Android instrumented tests (device required).
- `train_notebook.ipynb` was scanned for secrets; its ML logic was not audited.
- The `frontier` model tier is off by default and was not exercised.
- Secret scanning covered the working tree and git history for credential
  patterns; it cannot prove the absence of a secret that never matched a pattern.
- 11 of 49 raised findings were never adversarially verified (the CI/supply-chain
  and secrets areas had no verification stage). Those are all in §2, fixed and
  independently confirmed by hand.
