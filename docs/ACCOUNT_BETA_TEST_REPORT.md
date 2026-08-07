# Account Beta Acceptance Test Report

Report date: 2026-08-07 UTC

Candidate checkpoint: `2cb1717` (`ops(beta): add isolated staging handoff`)

Branch: `account-beta-test`

## Result

The repository implementation and synthetic local acceptance matrix pass. The
candidate is ready for Android CI and the documented infrastructure handoff;
it is **not deployed and not production-ready**. Android compilation/APK
assembly, German VDS smoke/restore tests, Russian infrastructure, extracted
German-worker tests, real-provider smoke tests, legacy-data operations, and
legal/product release gates require their named external owners.

No production service, VDS configuration, DNS, nginx process, firewall,
provider application, real OAuth/FCM token, real user image, backup, legacy
database, model artefact, or training process was accessed or changed. Test
identities, credentials, images, databases, IPs, and sentinels were generated
or documentation-range synthetic values.

## Final local verification

| Check | Result | Evidence |
|---|---:|---|
| Complete account-service suite | PASS | `150 passed, 1 skipped in 3.01s` |
| Complete application-service suite | PASS | `582 passed in 7.99s` |
| Remote provider/config contract | PASS | `41 passed`; fake HTTP transport only |
| Community authorization/deletion matrix | PASS | `115 passed in 1.83s` |
| Training/export/migration/telemetry matrix | PASS | `33 passed in 0.73s` |
| Deployment/root-path/runtime matrix | PASS | `27 passed in 0.83s` |
| Python lint | PASS | Ruff: `All checks passed!` for both services |
| Compose rendering | PASS | Local, German beta, Russian template, and German-worker template all passed `config --quiet` |
| Backup script | PASS (static) | `sh -n` passed; `/etc/hosts` was refused as a non-beta env before Docker was invoked |
| Machine-readable contracts | PASS | CI YAML, account/app/worker JSON, and 219 Android XML files parsed |
| Android hardware-identifier scan | PASS (static) | No `Build.MODEL`, manufacturer/brand/device/product/serial, Android ID, advertising ID, IMEI, or telephony access in production source/resources |
| Android compile, unit tests, lint, APKs | CI GATE | No Java/JDK, Kotlin compiler, Android SDK, or ADB exists in this checkout; the workflow runs ktlint, detekt, both unit-test variants, release APK, and account-beta APK |

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
| 19.1 Provider/signup | PASS locally; real-token OPERATOR GATE | New/existing Yandex and VK, pending completion/cancel/expiry/replay, wrong app, rejected/invalid token, outage, missing profile, and no pre-nickname session are automated. Remote calls follow [Yandex user info](https://yandex.com/dev/id/doc/en/user-information) and [VK ID's official SDK request](https://github.com/VKCOM/vkid-web-sdk/blob/master/src/auth/auth.ts), but tests inject fake HTTP and use no real token. |
| 19.2 Nickname | PASS | Concurrent uniqueness, NFKC/casefold collision, code-point length, required letter, Latin/Cyrillic mixing, allowed characters, reserved values, cooldown, and fresh authorization version/token are covered. |
| 19.3 Sessions/tokens | PASS | Generic multi-device sessions, 15-minute signed JWT, 30-day idle/180-day absolute refresh bounds, rotation/replay family revocation, logout/remote revoke, issuer/audience/signature/time/version checks, unknown-`kid` refresh, and key overlap are covered. Android serialized refresh remains a CI test. |
| 19.4 Linking/reauthentication | PASS | Five-minute one-use scoped challenge, wrong scope/provider, replay/expiry, duplicate provider, other-account ownership, no merge, and no unlink path are covered. |
| 19.5 Community authorization | PASS | Guest public reads, account-only mutations/private views/counts, forged/expired/wrong-audience/deleted/revoked sessions, body spoofing, JWT-derived ownership, legacy read-only rows, and no automatic post-login mutation replay are covered. |
| 19.6 Guest minimization | PASS | No-consent inference/training and telemetry no-write, no Guest view row, Guest push refusal, no persisted IP/pseudo/account/session/actor/device/file linkage, and in-memory-only rate limiting are covered. |
| 19.7 Training/export | PASS | Client contract plus server decode/re-encode, crop-only capture, unsafe-frame rejection, SHA/pHash deduplication, retention/sampling, structured feedback receipts, out-of-head labels, export metadata/sentinel/privacy checks, checksums, provenance/inventory, and group split locking are covered. Android byte-path tests remain a CI gate. |
| 19.8 Push/deletion | PASS | Account-only encrypted/deduplicated FCM storage, no phone model, actor-only notification resolution, stale removal, immediate session/push revocation and content hiding, cleanup retry/idempotency, thumbnail removal, and eventual private-profile erasure are covered without an FCM call. |
| 19.9 Android | IMPLEMENTED; CI GATE | Coexisting account-beta identity, prefixed defaults, Guest/ProfileRequired/Authenticated state, encrypted refresh recovery, one retry/no loop, provider/nickname cancellation, logout with Room preservation, separate consents, minimized telemetry, and shared upload sanitizer have source tests wired into CI. No local Android result is claimed. |
| 19.10 Environment/deployment | PASS for contracts; OPERATOR GATE for runtime | The local stack previously passed empty-volume startup/restart and synthetic smoke. All final Compose files render and isolation/root-path/nginx contracts are automated. Actual German prefix smoke, backup restore, worker firewall/mTLS/disk inspection, Russian restore/key rotation, and production cutover were not authorized or performed. |

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
| German staging isolation and `/plantdisease-test/` contract | Static PASS; VDS operator smoke required |
| Production rejects mock auth/development keys/unsafe database config | PASS |
| Final RU/DE ownership and identity-free network contract | PASS as tested handoff contract; extracted services/operator acceptance required |

## Privacy and security evidence

- Access JWT claims exclude account UUID, provider subject/profile, provider
  token, refresh token, email, phone, avatar, hardware ID, IP, and FCM token.
- Provider responses are converted immediately to an allowlisted identity;
  access tokens are bounded, never placed in a URL, never persisted, and never
  included in a stable error. Redirect, network, malformed, and oversize paths
  fail closed.
- Account secret files must be absolute private regular files; production mock
  auth, ephemeral keys, HTTP issuer, wildcard CORS, unsafe remote PostgreSQL,
  and missing deletion configuration fail startup.
- Community receives only signed random actor/nickname claims. It cannot resolve
  account UUID/provider identity/FCM values, and it forwards no bearer context
  into inference.
- Training and telemetry schemas have no account/community/session/installation
  foreign key or raw/hashed IP. Export tests open every image and scan archive
  bytes for metadata, forbidden keys, and seeded linkage values.
- German beta uses mock auth and explicit synthetic-data acknowledgement. Its
  APIs publish only loopback ports; PostgreSQL is private; every state volume
  is beta-named; access logging is disabled; and no production path is mounted.
- The final German worker request allows exactly `image`, `request_id`,
  `model_family`, `language`, and `result_options` under mTLS with additional
  properties forbidden. The template mounts only read-only models/secrets and
  tmpfs, with no user-state volume.

## Deliberately unexecuted gates

These are not test failures; they require authority, infrastructure, secrets,
real data, or legal/product decisions outside this implementation:

1. Observe a green GitHub Actions Android job and archive the tested
   `accountBeta` APK digest/provenance.
2. Have the VDS operator render reviewed image digests, insert the nginx
   fragment into the real `http`/TLS context, run `nginx -t`, start only the
   isolated German project, and execute loopback/prefixed synthetic smoke.
3. Execute `backup-test.sh` against that disposable stack and record the
   restore inventory/checksum. The script was not run locally because no
   candidate deployment/database exists.
4. Provision the Russian data plane and extracted German worker; prove private
   mTLS/firewall behavior, identity stripping against a fake worker, disk/log
   statelessness, encrypted backup restore, and Ed25519/certificate rotation.
5. Register real provider package signatures and run dedicated real-token smoke
   only on approved Russian staging.
6. Freeze/audit real legacy storage, obtain an explicit row/file allowlist,
   dry-run and approve migration into a fresh Russian target, verify restore and
   cutover, then separately authorize German erasure. The committed migration
   report covers synthetic fixtures only.
7. Complete Article 22 and Article 12 counsel/operator gates, accurate Russian
   and English policy/consent text, moderation/reporting/blocking/safety work,
   and the separate PlantNet/OpenRouter/FCM/location/threat-model reviews.

Until every applicable gate is recorded, `.invalid` references and zero
digests in production examples remain placeholders, real onboarding stays
disabled, German beta state is disposable and non-promotable, and the project
must not be described as production or legally approved.
