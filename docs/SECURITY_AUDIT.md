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

Findings below are marked with their verification state:

- **confirmed** — survived the adversarial pass
- **fixed** — remediated on `claude/security-audit-dependencies-csihfe`
- **reported** — raised by review but not yet adversarially re-checked
- **refuted** — investigated and dismissed, kept here so it is not re-raised

---

## 1. Read this first: a methodology caveat

Three findings are recorded by the adversarial pass as *"hallucinated
evidence"*. **That verdict is wrong**, and the cause is how this audit was
sequenced, not the findings.

Fixes were committed while the review was still running, so the verifier read
the already-corrected code and reasonably concluded the quoted vulnerable lines
had never existed. It went as far as citing the newly-added `_is_trusted_proxy`
helper and the new *"There is deliberately NO hard-coded default"* comment as
proof of fabrication. Git disagrees:

```
origin/main:server/main.py:955      # Take the leftmost non-empty entry that parses as an IP
origin/main:server/feedback.py:50   _IP_HASH_PEPPER = get_str("FEEDBACK_IP_PEPPER", "plantvision-static-pepper-v1")
origin/main:server/docker-compose.yml:8      - "8000:8000"
```

A fourth refutation (multipart body size) also leans on the loopback-binding
change made during the audit, although the verifier independently confirmed the
underlying Starlette behaviour is real.

**Lesson for the next run: freeze the tree, or run the review against a fixed
commit.** The adversarial pass is worth keeping — it legitimately killed four
findings on their merits (§5) — but a verifier reading a moving tree produces
false negatives, which are the expensive kind.

---

## 2. Fixed on this branch

All verified locally (server suite 529 → **540 passed**, ruff clean) and green
in CI, including the Android `ktlintCheck detekt testDebugUnitTest
assembleRelease` job.

| # | Finding | Severity | Where |
|---|---|---|---|
| 1 | **Pillow 12.2.0 carried 26 published advisories.** The one that bites here is CVE-2026-59199, a heap out-of-bounds write in `Image.paste()`/`Image.crop()` via signed coordinate overflow — reached on *every scan* by the Stage-1 letterbox paste (`detector.py:113`) and the Stage-2 box crops (`pipeline.py:1021`, `classifier.py:344`), on attacker-supplied images. Also CVE-2026-54060/54058/54059 and several decompression-bomb bypasses. | **Critical** | `requirements.txt` |
| 2 | **`X-Forwarded-For` was attacker-controlled.** `_client_ip` took the *leftmost* entry, but the nginx config in `DEPLOYMENT.md:288` forwards `$proxy_add_x_forwarded_for`, which *appends* the real peer. One forged header chose the caller's identity — and that identity keys the rate limiter, the request log, and the hashed `client_ip` on feedback, community and telemetry writes. The regression test shows 29 forged headers producing 29 distinct rate-limit buckets before the fix. Now walks right-to-left, skipping trusted hops. | **Critical** | `main.py:923`, `tests/test_client_ip.py` |
| 3 | **"Irreversible" IP hashes were reversible.** The pepper defaulted to a literal committed in this public repo. IPv4 is a 32-bit space, so a SHA-256 with a known pepper enumerates back to the raw address in seconds — every `client_ip_hash` stored beside photos, coordinates and pseudo-ids was effectively a plaintext IP. `FEEDBACK_IP_PEPPER` appeared in no deployment doc, so deployments ran on the default. Now: no hard-coded default; a random 256-bit pepper is minted on first use and persisted `0600` beside the DB. | **High** | `feedback.py:48` |
| 4 | **CI handed the release keystore to every third-party action.** Signing and FCM secrets sat in a job-level `env:`, which is injected into *every* step — `checkout`, `setup-java`, `setup-android`, both Gradle actions. Each secret is now scoped to the one step that uses it; a boolean step output carries the "is signing configured" decision instead of the keystore blob. | **High** | `ci.yml` |
| 5 | `GITHUB_TOKEN` inherited the repo default (often read-write). Added `permissions: contents: read` and `persist-credentials: false` on every checkout. | **High** | `ci.yml` |
| 6 | Third-party actions referenced by mutable tag — a tag can be silently repointed. Pinned to commit SHAs. | Medium | `ci.yml` |
| 7 | The Gradle distribution was downloaded and executed unverified. Added `distributionSha256Sum` + a `wrapper-validation` step. (The committed wrapper JAR turned out to be a genuine Gradle 8.14 build — stale, not tampered — but nothing in the repo could have told the difference.) | Medium | `gradle-wrapper.properties`, `ci.yml` |
| 8 | `docker-compose` published on `0.0.0.0:8000`, bypassing both the documented nginx TLS termination and any host firewall (Docker's DNAT precedes `INPUT`). Bound to loopback; added `no-new-privileges` and a memory limit. | Medium | `docker-compose.yml` |
| 9 | Base image `python:3.11.9-slim` was six CPython patch releases behind. Now `3.11.15-slim`, pinned by digest. | Medium | `Dockerfile` |
| 10 | The `pull_request` trigger the workflow comment already promised did not exist, so fork PRs and off-pattern branches reached `main` unchecked. Added. | Medium | `ci.yml` |
| 11 | `.gitignore` only matched `android/local.properties`, never the `android/app/local.properties` Android Studio actually writes — which is how a file containing the maintainer's workstation path got committed. Untracked it, widened the pattern, and added keystore/`.env` patterns (`DEPLOYMENT.md` walks the developer through creating a `.jks` and its base64 blob using *relative* paths). | Medium | `.gitignore` |
| 12 | The localhost/`10.0.2.2` cleartext exemption shipped in the release APK. Moved to a debug-only resource variant. | Low | `network_security_config.xml` |
| 13 | No automated dependency updates existed — the root cause of every item in §4. Added `.github/dependabot.yml` covering pip, gradle, docker and github-actions. | — | `dependabot.yml` |

---

## 3. Open findings

Nothing below is fixed. These are behaviour changes to a live consumer app —
cost controls, privacy posture, data retention — and they are yours to scope,
not mine to impose.

### 3.1 Cost & availability (an unauthenticated caller spends your money)

| Finding | State | Where |
|---|---|---|
| **Unmetered billed LLM calls.** Every distinct `name` string (validated only as 1–128 chars) misses the cache and mints a fresh OpenRouter completion at `reasoning_effort="high"`, `max_tokens=4096`. No global budget, no per-client cap, no circuit breaker. Walking random names bills the operator per request. | confirmed ×3 | `ai_services.py:1319`, `main.py:3865` |
| **Prompt-cache poisoning.** `/api/species-diseases` concatenates the client's `common` parameter into the prompt but omits it from the cache key, so one request can poison the *shared* cached answer every later user reads. | confirmed ×2 | `ai_services.py:1257` |
| **`/api/chat` caps are decorative.** `session_id` is client-chosen and never minted or signed server-side; a fresh random id per request resets `turn_count` and the token budget to zero. | confirmed | `main.py:2226` |
| **`/api/metrics` is exempt from rate limiting** and runs full-table SQLite aggregates on the event loop. Unauthenticated, unthrottled, blocking. | confirmed | `main.py:1711` |
| **`/api/progression` takes an unbounded list** and processes it on the event loop, while every comparable field in the file is length-capped. | confirmed | `main.py:2491` |
| **No inference admission control.** With `INFER_WORKERS=1`, a client *within* the rate limit can queue full-resolution decoded images and pin RAM; the token bucket never fires. | reported | `main.py:1977` |
| **PlantNet credits** are spent per uncached `/api/analyze` with no server-side budget. | reported | `main.py:1973` |
| **Plant-photo cache is flushable.** Any client can walk 1500 arbitrary Wikimedia URLs through `?u=` and evict every warm photo; the LRU caps file *count*, not bytes (1500 × 16 MB). | confirmed | `plant_photos.py:184` |

### 3.2 Privacy

| Finding | State | Where |
|---|---|---|
| **EXIF/GPS leaves the device.** The raw camera JPEG is uploaded verbatim on negative feedback and forwarded to third-party PlantNet — while `_anonymise_scan_image` shows the intent to strip it for the labeling store. `privacy.html:87` says verified data may be published as an open training dataset. | reported ×2 (client + server) | `ScanRepository.kt:576,238`, `ai_services.py:394` |
| **`/api/community/map` has no k-anonymity.** The `k>=3` aggregation exists only on the heatmap, so anyone can grid-walk the globe and enumerate every sharing user's ~1.1 km location, photo, handle and timestamp. | reported | `community.py:1064` |
| **`tools/export_data.py` uses `SELECT *`**, so IP hashes, per-install pseudo-ids, coordinates and free-text notes land in the archive earmarked for the open dataset. | reported | `export_data.py:101` |
| **Pseudo-id in GET paths/query strings**, so it lands in server and proxy access logs — and it is the only thing authenticating the community surface. | reported | `ScanRepository.kt:775` |

### 3.3 Integrity & validation

| Finding | State | Where |
|---|---|---|
| **`/api/analyze` cache replays another user's `scan_id`.** The key is the SHA-256 of the upload bytes with no notion of who uploaded them, and `scan_id` is the authorisation token for the mutating endpoints. | confirmed | `main.py:1972` |
| **`/api/feedback/image` validates nothing** — no magic-byte check, no decode — unlike `/api/analyze` and `/api/community/scan`, and its daily quota reads a counter it never increments. Arbitrary bytes flow into the retraining corpus. | reported ×2 | `main.py:3020,3041` |
| **Forgeable second-opinion consensus.** `author_pseudo_id` is client-supplied, so per-author dedup does not bind and one client can cast unlimited votes on a victim's scan. | reported | `main.py:3307` |
| Server-supplied URL launched via implicit `ACTION_VIEW` with no scheme allowlist; `photoUrl()` passes any absolute URL to Glide. | reported | `SpeciesDetailActivity.kt:561`, `ServerConfig.kt:15` |

### 3.4 Requires action outside this repo

**The production host's SSH profile is in git history** (`admin-panel/app/static/app.js`, removed in #149 but still reachable via `git log --all -p`): host address, `root` login, deploy paths and the systemd unit. Deleting the file did not un-publish it. Treat as disclosed: disable password auth and `PermitRootLogin`, move to a key-only non-root account, rotate any keys authorised for root, and firewall the host. History rewriting is optional and secondary to rotation.

---

## 4. Dependencies

Updated to latest stable, **except where a ceiling is real**. Each ceiling below
was established from artefact metadata or a failing build, not from guesswork,
and is documented inline so the next person does not rediscover it.

**Server** — Pillow `12.3.0`, FastAPI `0.141.1`, uvicorn `0.52.1`, onnxruntime
`>=1.28.0`, requests `>=2.34.2`, ImageHash `>=4.3.2`, firebase-admin `>=7.5.0`,
pytest `>=9.1`, ruff `>=0.16`, scikit-learn `>=1.9`; base image `3.11.15-slim`.

**Android** — compileSdk 35 → **36**, targetSdk deliberately held at 35 (it is
what opts the app into Android 16's mandatory edge-to-edge, a UI decision rather
than a side effect of a dependency refresh). ~30 artefacts moved: core-ktx
`1.18.0`, activity `1.13.0`, CameraX `1.6.1`, Room `2.8.4`, lifecycle `2.11.0`,
Material `1.14.0`, navigation `2.9.8`, work `2.11.2`, DataStore `1.2.1`, gson
`2.14.0`, firebase-bom `34.17.0`, Robolectric `4.16.1`, and the test stack.

### Ceilings — do not "just bump" these

| Held at | Blocked by |
|---|---|
| **numpy `>=2.4,<3.0`** | numpy 2.5.0 requires Python ≥ 3.12; this service runs 3.11. Raising the floor makes the install *unresolvable*, not newer. |
| **Gradle `9.5.0`** | Gradle 9.6.0 removed `org.gradle.api.problems.internal.InternalProblems`, which AGP 8.13.2 still uses. Fails during plugin application. |
| **LiteRT `2.1.3`** | 2.1.4+ ship Kotlin metadata `2.3.0`; the Kotlin 2.1.21 compiler accepts only one minor ahead. 2.1.0–2.1.3 carry `2.2.0`, which is why the previous 2.1.0 built. |
| **Hilt `2.57.2`** | 2.58+ depend on kotlin-stdlib 2.2.20, 2.60+ on 2.3.21 — newer than the Kotlin compiler in use. |
| **kotlinx-serialization `1.8.1`** | 1.9.0 needs stdlib 2.2.0, 1.11.0 needs 2.3.20. |
| **coroutines `1.10.2`** | 1.11.0 needs stdlib 2.2.20. |
| **core-ktx `1.18.0`** | 1.19.0 declares `minCompileSdk=37` and `minAgp=9.1.0`. |
| **Glide `4.16.0`**, **OkHttp `4.12.0`**, **Ktor `2.3.13`** | Major-version migrations with breaking APIs; Glide 5.0.9 also needs compileSdk 37. |

Two upgrade hazards worth recording, both of which a version-string-only bump
would have shipped silently:

- **Material 1.14.0 stopped declaring `colorPrimary` and `borderlessButtonStyle`
  in its own `R.txt`** (1.12.0 declared both). With
  `android.nonTransitiveRClass=true` that broke four references, now repointed
  to `androidx.appcompat.R.attr.*`. Every remaining
  `com.google.android.material.R.attr.*` reference was checked against 1.14.0's
  `R.txt` rather than only the ones the compiler happened to report.
- **LiteRT renamed `libLiteRtOpenClAccelerator.so` → `libLiteRtClGlAccelerator.so`**,
  so the packaging exclude stopped matching and the unused GPU delegate would
  have shipped in the APK. Both names are now excluded.

### Still open

Dependencies install from unpinned `>=` floors with no lockfile or hash
pinning, into both the production image and CI. Consider
`pip-compile --generate-hashes` + `pip install --require-hashes`. ktlint
(`12.1.2` → `14.2.0`) was deliberately not bumped: the engine version is pinned
separately at `1.3.1`, and moving it reformats 128 Kotlin files and invalidates
the checked-in baselines — real churn, no security benefit. detekt `1.23.8` is
already current.

---

## 5. Refuted

Kept so they are not re-raised. These were dismissed on their merits, unlike the
three in §1.

- **`/api/push/register` token hijack** — `user_pseudo_id` is a 122-bit UUIDv4
  minted per install; the server never returns it (the community feed exposes
  only a one-way, 30-day-rotating digest), so there is no reachable entry point.
- **`/api/plant-photo?name=` request amplifier** — the quantitative claims about
  upstream fan-out did not hold up.
- **`plant_photos` blind SSRF via redirects** — the ordering observation is
  correct (`requests` follows redirects before `is_allowed_url(resp.url)` runs),
  but the exploitable impact was not demonstrated: the body is never read or
  returned, and reaching an internal host requires an open redirect on a
  Wikimedia property.
- **Multipart body-size limit** — Starlette does spool file parts unbounded, but
  the attack premise assumed a directly-exposed port that no longer exists.

---

## 6. Not covered

- No runtime/DAST testing — the server needs model artefacts that are not in the
  repo, so every finding here is from source review.
- No Android instrumented tests (device required).
- `train_notebook.ipynb` was scanned for secrets but its ML logic was not audited.
- The `frontier` model tier is off by default and was not exercised.
- Secret scanning covered the working tree and git history for credential
  patterns; it cannot prove the absence of a secret that never matched a pattern.
