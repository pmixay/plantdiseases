# Account Beta Acceptance Test Report

Report date: 2026-08-07 UTC

Candidate checkpoint: `ea5cf0d` (`feat(beta): run real SDK staging beside main`)

Branch: `account-beta-test`

## Result

The implementation, local acceptance matrix, and loopback German VDS runtime
pass. Commit `ea5cf0d0d281c7fb24b9619026597e829d8843b1` is pushed to
`account-beta-test`. A separate Compose project is running beside main on the
German VDS with remote provider adapters and the real read-only PlantVision
model. It is **restricted staging, not production-ready**.

The runtime is intentionally not connected to public nginx yet. Its protected
host env contains explicit non-runnable bootstrap provider IDs because the
registered Yandex/VK beta application values and matching stable APK signing
certificate were not available to this session. CI now refuses to publish a
synthetic account-beta artifact: it can publish only a real-OAuth APK after
those values are configured. No successful real-user OAuth flow is claimed.

Main code, process, port, database, media, model files, and nginx configuration
were not changed. No real provider token, real account, FCM value, user image,
legacy database, or training process was used. The only inference input was the
repository's non-user `server/static/guide/leaf_spot.png` fixture.

## Final local verification

| Check | Result | Evidence |
|---|---:|---|
| Complete account-service suite | PASS | `153 passed, 1 skipped in 2.59s` |
| Complete application-service suite | PASS | `590 passed in 7.70s` |
| Remote provider/config contract | PASS | `41 passed`; fake HTTP transport only |
| Community authorization/deletion matrix | PASS | `115 passed in 1.83s` |
| Training/export/migration/telemetry matrix | PASS | `33 passed in 0.73s` |
| Deployment/root-path/runtime matrix | PASS | `27 passed in 0.83s` |
| Python lint | PASS | Ruff: `All checks passed!` for both services |
| Compose rendering | PASS | Local, German beta, Russian template, and German-worker template all passed `config --quiet` |
| Backup script | PASS (static) | `sh -n` passed; `/etc/hosts` was refused as a non-beta env before Docker was invoked |
| Machine-readable contracts | PASS | CI YAML, account/app/worker JSON, and 219 Android XML files parsed |
| Android hardware-identifier scan | PASS (static) | No `Build.MODEL`, manufacturer/brand/device/product/serial, Android ID, advertising ID, IMEI, or telephony access in production source/resources |
| German loopback services | PASS | PostgreSQL, account-api, and app-api healthy; only `127.0.0.1:18101/18100` published |
| Real PlantVision runtime | PASS | Gamma `plantvision`, model `2.6.7`; repository leaf fixture returned HTTP 200 with detector regions/calibrated probabilities in `0.48s` |
| Real provider server adapters | PASS (invalid-token smoke only) | Live Yandex endpoint returned stable `invalid_provider_token`; live VK endpoint returned stable `provider_application_mismatch`; no real token used |
| Main coexistence | PASS | Main remained healthy on `127.0.0.1:8000` before/after startup and real inference |
| Secret isolation | PASS | App sees only dedicated `internal_token` volume (`0640`); signing/encryption keys remain account-only (`0600`) |
| Android compile, unit tests, lint, real-OAuth APK | CI/CONFIG GATE | Commit pushed; private workflow result not observable here. Publishing fails unless real provider and stable signing secrets are configured |

The single skipped account test is the deliberately opt-in live-PostgreSQL
integration. It previously passed against the isolated digest-pinned beta
PostgreSQL process at checkpoints 3 and 4, including migration, process
reconstruction, refresh rotation, and stable JWKS. The final run did not start
or mutate a container merely to remove that intentional skip.

The PostgreSQL image reference was checked against the [Docker Official Image
metadata](https://hub.docker.com/layers/library/postgres/18.4-alpine3.23/images/sha256-15b46a9c5a6b361eb4c0ce8d689365bf49fbf6802e615dce4e5e2326b3213e15).
The committed `postgres:18.4-alpine3.23` manifest digest is
`sha256:996d0920e4ff9df1fc19dacb904492f3c1ec0ec1cc338f0ad7123be7731c5f5e`,
and tests lock PostgreSQL 18 to the `/var/lib/postgresql` parent volume with
`PGDATA=/var/lib/postgresql/18/docker`.

## Plan test matrix

| Plan area | Status | Coverage and boundary |
|---|---|---|
| 19.1 Provider/signup | PASS locally; real-token OPERATOR GATE | New/existing Yandex and VK, pending completion/cancel/expiry/replay, wrong app, rejected/invalid token, outage, missing profile, and no pre-nickname session are automated. Contract tests inject fake HTTP; German loopback additionally completed invalid-token smoke against both live providers. No successful real token is claimed. |
| 19.2 Nickname | PASS | Concurrent uniqueness, NFKC/casefold collision, code-point length, required letter, Latin/Cyrillic mixing, allowed characters, reserved values, cooldown, and fresh authorization version/token are covered. |
| 19.3 Sessions/tokens | PASS | Generic multi-device sessions, 15-minute signed JWT, 30-day idle/180-day absolute refresh bounds, rotation/replay family revocation, logout/remote revoke, issuer/audience/signature/time/version checks, unknown-`kid` refresh, and key overlap are covered. Android serialized refresh remains a CI test. |
| 19.4 Linking/reauthentication | PASS | Five-minute one-use scoped challenge, wrong scope/provider, replay/expiry, duplicate provider, other-account ownership, no merge, and no unlink path are covered. |
| 19.5 Community authorization | PASS | Guest public reads, account-only mutations/private views/counts, forged/expired/wrong-audience/deleted/revoked sessions, body spoofing, JWT-derived ownership, legacy read-only rows, and no automatic post-login mutation replay are covered. |
| 19.6 Guest minimization | PASS | No-consent inference/training and telemetry no-write, no Guest view row, Guest push refusal, no persisted IP/pseudo/account/session/actor/device/file linkage, and in-memory-only rate limiting are covered. |
| 19.7 Training/export | PASS | Client contract plus server decode/re-encode, crop-only capture, unsafe-frame rejection, SHA/pHash deduplication, retention/sampling, structured feedback receipts, out-of-head labels, export metadata/sentinel/privacy checks, checksums, provenance/inventory, and group split locking are covered. Android byte-path tests remain a CI gate. |
| 19.8 Push/deletion | PASS | Account-only encrypted/deduplicated FCM storage, no phone model, actor-only notification resolution, stale removal, immediate session/push revocation and content hiding, cleanup retry/idempotency, thumbnail removal, and eventual private-profile erasure are covered without an FCM call. |
| 19.9 Android | IMPLEMENTED; CI GATE | Coexisting account-beta identity, prefixed defaults, Guest/ProfileRequired/Authenticated state, encrypted refresh recovery, one retry/no loop, provider/nickname cancellation, logout with Room preservation, separate consents, minimized telemetry, and shared upload sanitizer have source tests wired into CI. No local Android result is claimed. |
| 19.10 Environment/deployment | PASS for contracts and German loopback runtime; remaining operator gates | The isolated German project is healthy with real models and remote adapters; loopback and inference smoke pass. Public prefix/nginx, backup restore, worker firewall/mTLS, Russian restore/key rotation, and production cutover remain unexecuted. |

## Acceptance criteria

| Handoff criterion | Status |
|---|---|
| Complete account API contract | PASS |
| Android account state machine and privacy implementation | IMPLEMENTED; CI result required |
| Guest reads and account-only community mutation | PASS |
| Authorship cannot be spoofed by request fields | PASS |
| Push registrations private, encrypted, and hardware-free | PASS |
| Unified de-identified training store; no labeling API/admin queue | PASS |
| Export contains model/disease provenance without user linkage or image metadata | PASS |
| Client and server image sanitization | Server PASS; Android CI result required |
| Empty-workspace local Compose with mock providers | PASS at process checkpoint; final render PASS |
| German staging isolation and `/plantdisease-test/` contract | Runtime PASS on loopback; public nginx activation intentionally pending real provider config |
| Production rejects mock auth/development keys/unsafe database config | PASS |
| Final RU/DE ownership and identity-free network contract | PASS as tested handoff contract; extracted services/operator acceptance required |

## Privacy and security evidence

- Access JWT claims exclude account UUID, provider subject/profile, provider
  token, refresh token, email, phone, avatar, hardware ID, IP, and FCM token.
- Provider responses are converted immediately to an allowlisted identity;
  access tokens are bounded, never placed in a URL, never persisted, and never
  included in a stable error. Redirect, network, malformed, and oversize paths
  fail closed.
- Account secret files must be absolute regular files. Signing/encryption keys
  are owner-only; the isolated shared-token volume permits group read but no
  group write/world access. Production mock auth, ephemeral keys, HTTP issuer,
  wildcard CORS, unsafe remote PostgreSQL, and missing deletion configuration
  fail startup.
- Community receives only signed random actor/nickname claims. It cannot resolve
  account UUID/provider identity/FCM values, and it forwards no bearer context
  into inference.
- Training and telemetry schemas have no account/community/session/installation
  foreign key or raw/hashed IP. Export tests open every image and scan archive
  bytes for metadata, forbidden keys, and seeded linkage values.
- German beta uses remote Yandex/VK verification and real PlantVision weights.
  Its APIs publish only loopback ports; PostgreSQL is private; every writable
  state volume is beta-named; access logging is disabled; and no production
  writable path is mounted. The immutable main model directory is shared
  read-only.
- The final German worker request allows exactly `image`, `request_id`,
  `model_family`, `language`, and `result_options` under mTLS with additional
  properties forbidden. The template mounts only read-only models/secrets and
  tmpfs, with no user-state volume.

## Deliberately unexecuted gates

These are not test failures; they require authority, infrastructure, secrets,
real data, or legal/product decisions outside this implementation:

1. Configure dedicated provider applications for
   `com.plantdiseases.app.accountbeta` plus the stable APK certificate, store
   `YANDEX_CLIENT_ID`, `VKID_CLIENT_ID`, `VKID_CLIENT_SECRET`, and `RELEASE_*`
   in GitHub Actions, then observe a green Android job and archive the
   `account-beta-real-oauth-apk` digest/provenance.
2. Replace the two VDS bootstrap IDs with the matching registered IDs, restart
   only the beta project, run successful Yandex and VK login from that signed
   APK, then install the reviewed nginx fragment after `nginx -t` passes.
3. Execute `backup-test.sh` against the disposable stack and record the
   restore inventory/checksum. The script was not run in this pass; it is
   non-blocking for provider/APK smoke but required before wider staging use.
4. Provision the Russian data plane and extracted German worker; prove private
   mTLS/firewall behavior, identity stripping against a fake worker, disk/log
   statelessness, encrypted backup restore, and Ed25519/certificate rotation.
5. Repeat real-provider and data-location acceptance on Russian staging before
   any production/public onboarding; German team staging does not satisfy that
   production gate.
6. Freeze/audit real legacy storage, obtain an explicit row/file allowlist,
   dry-run and approve migration into a fresh Russian target, verify restore and
   cutover, then separately authorize German erasure. The committed migration
   report covers synthetic fixtures only.
7. Complete Article 22 and Article 12 counsel/operator gates, accurate Russian
   and English policy/consent text, moderation/reporting/blocking/safety work,
   and the separate PlantNet/OpenRouter/FCM/location/threat-model reviews.

Until every applicable gate is recorded, production example placeholders stay
non-runnable, real onboarding stays disabled, German beta state is disposable
and non-promotable, and the project must not be described as production or
legally approved.
