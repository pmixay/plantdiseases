# Account Beta Implementation Progress

> Historical checkpoint log. It does not describe the current release gate.
> Use [ACCOUNT_RELEASE_SECURITY_RECHECK.md](ACCOUNT_RELEASE_SECURITY_RECHECK.md):
> the RuStore candidate is Yandex-only, VK is coming soon, and permanent
> signing is isolated to the protected main release job.

This log tracks implementation on `account-beta-test`. It is updated at each
verified checkpoint. Checkpoints 0–10 used only mock/synthetic data and local
resources; checkpoint 11 records the explicitly authorized German VDS beta.

## Current status

- Active phase: German loopback beta is running; awaiting registered provider
  applications/stable APK signing before public prefix activation and real
  mobile login.
- Production services/data touched: main was observed for health only; its
  code, process, nginx, database, writable data, and model files were unchanged.
- Deployments performed: isolated Compose project
  `plantdisease_account_beta_german`, commit/image tag `ea5cf0d`, loopback ports
  `18100/18101`, real PlantVision model mounted read-only.
- Current blocker: `YANDEX_CLIENT_ID`, numeric `VKID_CLIENT_ID`, Android
  `VKID_CLIENT_SECRET`, and the permanent provider-registered
  `com.plantdiseases.app` signing certificate. No successful real-user OAuth
  flow is claimed without them.

## Checkpoints

### 0 — Repository baseline and hermetic server tests (complete, 2026-08-07)

Completed work:

- Read `CLAUDE.md` and `docs/ACCOUNT_BETA_TEST_PLAN.md` completely.
- Confirmed the clean working branch is `account-beta-test` at `ee10140`.
- Inspected the repository structure, CI, server tests, Android build setup,
  current persistence modules, and community/feedback/push/telemetry seams.
- Added `account-beta-test` to the GitHub Actions push branch filter.
- Redirected all server-test persistence to pytest-owned temporary paths.
- Disabled the production-only external photo warm-up inside tests so the
  local suite remains deterministic and network-independent.
- Added Starlette's maintained `httpx2` TestClient dependency instead of
  relying on its deprecated legacy `httpx` compatibility path.

Decisions:

- The existing ML pipeline stays replaced by `FakePipeline` in HTTP tests; no
  model artefacts are downloaded and no model training/build is performed.
- External provider and Wikimedia calls must be mocked at the test boundary.
- Existing ignored runtime files, `exports/`, and `server/admin_backups/` remain
  untouched.

Verification so far:

- `/home/plantdiseases/repo/server/venv/bin/ruff check .` — passed.
- Initial `pytest tests -q` baseline — reached the first TestClient-backed test
  after 65 pure unit tests, then waited because this execution sandbox blocks
  AnyIO's cross-thread portal wake-up. A minimal one-route reproduction passed
  outside the sandbox.
- `/home/plantdiseases/repo/server/venv/bin/pip install --target
  /tmp/account-beta-pydeps 'httpx2>=2.7,<3'` — installed `httpx2==2.9.1`
  and its dependencies into temporary test storage only.
- `PYTHONPATH=/tmp/account-beta-pydeps .../pytest
  tests/test_aqua_hosting.py -q` — 4 passed in 0.41s.
- `PYTHONPATH=/tmp/account-beta-pydeps .../pytest tests -q` — 540 passed in
  7.99s.
- Final `/home/plantdiseases/repo/server/venv/bin/ruff check .` — passed.

Checkpoint commit:

- `test: establish hermetic account beta baseline`

### 1 — Fail-closed account service and provider test scaffold (complete, 2026-08-07)

Completed work:

- Created the independent `account-server/app` FastAPI package and a non-root
  multi-stage container definition.
- Added required, no-default environment settings for environment, provider
  mode, issuer, audience, and PostgreSQL URL.
- Added production startup rejection for mock auth, HTTP issuers, wildcard
  CORS, unencrypted remote PostgreSQL, missing secret files, and secret files
  readable by group/world.
- Added fresh server-owned request IDs, no-store/security headers, strict
  allowlisted CORS, and health/readiness contracts.
- Added provider-neutral identity/error types and deterministic Yandex/VK mock
  adapters for new, existing, both-linked, and owned-by-another fixtures plus
  expired, forged, wrong-app, unavailable, and incomplete-profile failures.
- Added an independent Ruff/pytest configuration and account CI job/path
  filter. The mock adapters have no network path and refuse production use.

Decisions:

- Account service settings have no implicit runnable defaults. Compose/tests
  must state the environment and beta-only PostgreSQL URL explicitly.
- Provider adapters return only subject, display name, avatar URL, provider,
  and refresh time; account-existence/ownership remains a repository concern.
- Remote Yandex/VK adapters fail closed until their contract checkpoint is
  implemented; there is no placeholder that accepts unverified tokens.
- The account service does not connect to a database yet. Schema, migrations,
  repository fixtures, and PostgreSQL readiness form the next checkpoint.

Verification:

- `/home/plantdiseases/repo/server/venv/bin/ruff check .` from
  `account-server/` — passed.
- `PYTHONPATH=/tmp/account-beta-pydeps .../pytest -q` from `account-server/`
  — 24 passed in 0.29s.
- CI workflow YAML parsed successfully with PyYAML; `git diff --check` passed.
- Test data uses only `*-test-subject-*`, `Synthetic *`, `.invalid` URLs, and
  explicit `synthetic-test-only` database credentials.

Checkpoint commit:

- `feat(account): add fail-closed service scaffold`

### 2 — Private PostgreSQL schema and migration foundation (complete, 2026-08-07)

Completed work:

- Added SQLAlchemy 2.0 models for `accounts`, `account_identities`, `sessions`,
  `refresh_tokens`, `pending_signups`, `reauth_challenges`,
  `account_deletion_jobs`, `push_devices`, `account_consents`, and
  `account_security_events`.
- Added database-enforced uniqueness for normalized nicknames, random community
  actors, provider subjects, one provider per account, token hashes/families,
  push fingerprints, and versioned account consent.
- Added bounded enum/check constraints, SHA-256-length checks, expiry ordering,
  cascading private-data ownership, and generic `Android device` session labels.
- Added an explicit, reversible Alembic `0001` migration and offline PostgreSQL
  SQL verification; migration tables match ORM columns exactly.
- Added a short transaction-scoped repository helper with commit/rollback and
  no process-global session.
- Added database-aware readiness. Database/driver failures return a redacted
  `503 {"status":"unavailable"}`; liveness remains independent.
- Packaged Alembic migrations in the account image and made its container
  healthcheck use database-backed `/ready` rather than liveness-only `/health`.
- Pinned SQLAlchemy `2.0.51`, Alembic `1.18.5`, and Psycopg `3.3.4`.

Decisions:

- Token and pending/reauth secrets are stored only as 32-byte SHA-256 hashes.
- FCM registrations store ciphertext plus a keyed 32-byte fingerprint; there
  is no plaintext token, phone model, or hardware identifier column.
- Account security events are allowlisted and expiring, with no provider
  subject, nickname, token, request body, raw/hashed IP, or arbitrary message.
- SQLite is used only as a fast migration/constraint test dialect. The next
  checkpoint must run the same migration against the beta PostgreSQL container
  before the local foundation is considered complete.

Verification:

- Temporary dependency install into `/tmp/account-beta-dbdeps` — installed the
  three pinned database/migration packages successfully.
- `/home/plantdiseases/repo/server/venv/bin/ruff check .` from
  `account-server/` — passed.
- `PYTHONPATH=/tmp/account-beta-dbdeps:/tmp/account-beta-pydeps .../pytest -q`
  — 38 passed in 0.64s.
- `python -m alembic upgrade head --sql` with explicit synthetic beta settings
  — passed and rendered transactional PostgreSQL DDL for all 10 tables.
- Tests cover schema privacy, migration upgrade/downgrade and PostgreSQL SQL,
  transaction rollback, nickname/actor/provider uniqueness, exact Psycopg URL
  enforcement, generic sessions, encrypted push storage, hash length checks,
  and readiness redaction.

Checkpoint commit:

- `feat(account): add private database schema`

### 3 — Isolated local Compose and process foundation (complete, 2026-08-07)

Completed work:

- Added the isolated `plantdisease_account_beta_test` Compose project: private
  PostgreSQL, one-shot account key generation and migration, account API, and
  existing application API.
- Kept PostgreSQL/migration private-only and put every service on the internal
  network; the APIs additionally join a project-scoped host-access network and
  their two published ports bind only to `127.0.0.1`.
- Added explicit health/dependency gates so migration waits for PostgreSQL,
  account startup waits for migration completion, and application startup waits
  for database-backed account readiness.
- Added beta-prefixed named volumes for PostgreSQL, models, community data,
  application compatibility data, thumbnails, legacy training data/images,
  telemetry, caches, and logs. There are no host bind mounts
  or production storage paths.
- Added a deterministic no-weight application pipeline for local process tests.
  Its switch fails closed unless `ENVIRONMENT=test`, its output is visibly
  marked `synthetic_test_result`, and production behaviour remains the default.
- Made optional popular-photo warming independently switchable so the local
  stack has no external Wikimedia dependency.
- Prepared the existing server image's beta volume targets for its non-root UID
  and kept API root filesystems read-only with explicit writable volumes.
- Documented local start, inspection, endpoints, non-destructive shutdown, and
  the separate explicitly destructive volume-removal operation.
- Added parsed-Compose contract tests and CI `docker compose config` validation.

Decisions:

- Deterministic in-process Yandex/VK adapters satisfy the local mock-provider
  role; an additional network service would add no contract coverage.
- The committed PostgreSQL service has no published port. Runtime migration
  verification used a temporary, uncommitted loopback-only port/network
  override solely so the host test client could reach the container.
- No server/account Docker image or ML artefact was built locally, consistent
  with repository guidance. Process tests ran the same entry points from the
  checked-out source against isolated synthetic state.
- Ordinary Compose shutdown retained the beta PostgreSQL volume. It remains
  disposable test state and is never eligible for promotion.

Verification:

- `docker compose -f deploy/account-beta-test/compose.local.yml config --quiet`
  — passed.
- Account and server Ruff checks — passed.
- Synthetic runtime tests — 10 passed; parsed Compose isolation tests — 7
  passed.
- Digest-pinned `postgres:18.4-alpine3.23` started healthy with a fresh beta
  named volume; online Alembic upgrade reached
  `0001_private_account_schema` and created 11 public tables including
  `alembic_version`.
- After a PostgreSQL process restart, the same revision and 11-table count were
  present.
- Account `/health` and database-backed `/ready` returned 200 with mock Yandex
  and VK providers.
- Application `/api/health`, `/api/capabilities`, and `/api/analyze` returned
  200 without model weights; analysis used a repository test image and carried
  the `synthetic_test_result` warning.
- Ordinary `docker compose down` removed only the beta container/network;
  `plantdisease_account_beta_test_postgres` remained present.
- Complete account suite — 45 passed in 2.56s.
- Complete existing-server suite — 550 passed in 12.21s.
- `git diff --check` — passed.

Checkpoint commit:

- `test(beta): add isolated local compose foundation`

### 4 — Secure account API and session contract (complete, 2026-08-07)

Completed work:

- Implemented every planned account endpoint for Yandex/VK exchange, pending
  signup completion/cancellation, refresh, logout, session listing/revocation,
  scoped reauthentication, identity linking, profile/nickname, deletion, and
  JWKS publication.
- Added authenticated push registration/removal to the private account API;
  tokens use randomized AES-GCM ciphertext and keyed fingerprints, with no
  device/hardware label accepted or stored.
- Added Ed25519 access JWTs with `kid`, 15-minute expiry, strict issuer/audience
  validation, exact allowlisted claims, and active/recently-retired JWKS keys.
  JWTs contain no account UUID, provider subject/profile, email, phone, or
  provider token.
- Added rotating random 256-bit refresh tokens, 30-day idle and 180-day
  absolute session expiry, retained consumed rows, and family revocation on
  replay.
- Added one-use 10-minute pending-signup and five-minute scope-bound reauth
  tokens; only SHA-256 hashes are persisted.
- Enforced NFKC-plus-casefold nickname uniqueness, 3–24 code-point/letter/script
  rules, configured/built-in reserved names, transactional reservation, and a
  30-day change cooldown. Nickname changes increment authorization version and
  return a replacement access token.
- Implemented provider-profile refresh, multiple generic `Android device`
  sessions, caller-owned revocation, immediate deletion-pending state, global
  session/push revocation, and queued cleanup jobs.
- Added strict request schemas that reject extra fields and a non-reflective
  validation/error format so rejected provider/refresh/pending/reauth/FCM
  values cannot appear in responses.
- Added bounded in-memory per-IP/per-provider-subject/session rate limiting with
  stale-bucket reclamation and no persisted address.
- Added in-transaction active-account/session rechecks for every sensitive
  mutation, closing deletion/nickname/session races between HTTP authentication
  and the domain transaction.
- Added hourly pruning for expired pending signups, reauth challenges, 30-day
  security events, and session families only after their 180-day absolute
  lifetime. Migration `0002_account_retention_index` indexes that bounded scan.
- Added deterministic UUID-suffixed mock identities for repeatable PostgreSQL
  integration runs; no adapter has a real provider network path.
- Added a network-disabled one-shot local key generator. Random Ed25519/AES
  keys persist in a beta-only named volume with mode `0600`, are never printed
  or committed, and survive process restarts.
- Added typed public response schemas and committed a generated, regression-
  tested `account-server/openapi.json` contract. Request and response validation
  failures are both redacted without logging token-bearing payloads.

Decisions:

- `AUTH_PROVIDER_MODE=remote` continues to fail closed. Real Yandex/VK OAuth is
  intentionally outside this synthetic German/local checkpoint and must be
  implemented only on the separately reviewed Russian data plane.
- Ephemeral signing/encryption keys require both `ENVIRONMENT=test` and an
  explicit opt-in, used by hermetic unit tests only. Local Compose uses its
  persistent generated-key volume instead.
- The real account UUID remains internal even in profile/session responses.
  The random community actor is the only account-related public identifier.
- Account deletion queues the cross-store cleanup job; community content/media
  cleanup and retry execution are implemented with the next community
  authorization checkpoint.
- The opt-in PostgreSQL integration test leaves its uniquely generated
  synthetic account in the disposable beta volume for inspection.

Verification:

- Installed declared `PyJWT==2.13.0` and `cryptography==49.0.0` into temporary
  test storage and ran the suite against those exact versions.
- Account Ruff check — passed.
- Complete account suite — 113 passed, 1 opt-in PostgreSQL test skipped in
  2.45s.
- Opt-in PostgreSQL test — 1 passed in 0.72s against the digest-pinned beta
  container. It proved generated-identity signup, persisted token hashes,
  refresh rotation, stable JWKS across account-process reconstruction, and
  refresh-based profile recovery.
- Online Alembic upgrade reached `0002_account_retention_index`; PostgreSQL
  reported both that revision and `ix_sessions_absolute_expires_at`.
- Account HTTP state-machine tests cover every stable plan error, cancellation,
  expiration, cooldown, refresh replay, multi-session revocation, linking
  ownership, deletion, encrypted push, validation redaction, and rate limits.
- Key tests prove private permissions, non-overwrite, non-test refusal, signing
  rotation overlap, and push ciphertext decryption after reconstruction.
- Committed OpenAPI equals the live generated schema; protected operations
  declare Bearer auth and contain no private identity/hardware fields.
- `docker compose ... config --quiet` and parsed Compose isolation tests —
  passed.

Checkpoint commit:

- `feat(account): implement secure account API contract`

### 5 — Community authorization, private push, and deletion cleanup (complete, 2026-08-07)

Completed work:

- Added local Ed25519 JWT verification in the application API with exact claim,
  issuer, audience, time, algorithm, and `kid` validation. An unknown `kid`
  receives exactly one bounded JWKS refresh; malformed/unknown keys fail
  closed.
- Added private account-session introspection after local JWT verification, so
  logout, authorization-version changes, revoked sessions, and deletion take
  effect immediately rather than at access-token expiry.
- Gated scan publication, opinions, opinion request/cancel, view tracking,
  private owner polling, and batched owner counts behind Bearer accounts.
  Public feeds, maps, thumbnails, heatmaps, and opinion reads remain anonymous.
- Removed caller-controlled pseudo-ID/nickname fields from the active community
  contract. New authorship is derived only from signed actor/nickname claims;
  spoofed legacy fields are ignored and cannot influence stored ownership.
- Migrated community tables transactionally to nullable legacy identity columns
  plus separate nullable account actor columns. Existing pseudo-ID rows remain
  readable, unlinked, read-only, and unclaimable; new rows leave pseudo-ID
  columns NULL.
- Made account-private opinion/view counts owner-filtered and ensured anonymous
  reads create no stable per-view record. Public reads may optionally use a
  valid account token only to exclude the caller's own rows.
- Removed the plaintext `/api/push/register` route and its beta `push.db`
  volume. Community now emits an internal actor-keyed event; only account-api
  resolves and decrypts live device registrations, and no FCM token returns to
  the community process.
- Extended the network-disabled key generator with a persistent random
  mode-`0600` internal service credential shared through the beta key volume.
  Internal auth, notification, and deletion routes are excluded from public
  OpenAPI and require that separate credential.
- Implemented deletion tombstones that hide actor content and thumbnails before
  returning success, plus idempotent cleanup jobs with bounded exponential
  retry. Successful community acknowledgement hard-deletes private account,
  identity, session, and push rows while retaining a minimal completed job.
- Added a deterministic loopback integration checker and regenerated the
  versioned application OpenAPI contract. No JWT/account context enters the ML
  pipeline.

Decisions:

- JWT signature validation and live session introspection are both required;
  either dependency failing keeps community mutations closed.
- Anonymous reads never synthesize or persist a guest identity. Legacy query or
  body identity fields have no authority in the account-era handlers.
- Community deletion uses logical hiding before asynchronous physical cleanup,
  so a filesystem retry cannot make deleted content visible again.
- Push delivery remains a replaceable account-owned sink. Local tests resolve
  only synthetic registrations and perform no real FCM call.
- The dormant legacy push module remains outside the active route graph for
  compatibility with the pre-existing standalone unit tests; beta Compose no
  longer configures or mounts its plaintext store.

Verification:

- Complete server suite — 581 passed in 7.81s.
- Complete account suite — 121 passed, 1 opt-in PostgreSQL test skipped in
  2.94s.
- Latest focused contract/auth/deletion/config matrices — 66 passed.
- Account/server Ruff checks, `git diff --check`, parsed Compose contract, and
  `docker compose ... config --quiet` — passed.
- Host-process integration against the digest-pinned loopback beta PostgreSQL
  container — passed signup, signed upload, public Guest read, Guest mutation
  rejection, private counts, encrypted push resolution, deletion callback,
  immediate hiding, private profile erasure, and revoked-token rejection.
- Temporary processes and generated key/state directory were removed after the
  smoke test. Ordinary Compose shutdown retained
  `plantdisease_account_beta_test_postgres`; no production service or data was
  touched.

Checkpoint commit:

- `feat(community): bind mutations to signed accounts`

### 6 — Anonymous training, minimal telemetry, and media/log privacy (complete, 2026-08-07)

Completed work:

- Replaced the linked `feedback.db`/`labeling.db` design with one internal
  `training_store.py`, one privacy-minimized schema, and a sanitized image
  directory. Removed the legacy storage modules, review-queue tests, and old
  exporter/analyser instead of leaving two active write paths.
- Added random server sample UUIDs and independent random 256-bit feedback
  receipts whose hashes alone are stored. Receipts expire, reject replay, and
  cannot authorize community or account operations.
- Made automatic collection fail closed by default and require both an
  operator switch and the exact per-request model-improvement consent version.
  It rejects `not_a_plant`, blur/private full frames, and any capture without a
  detector-backed leaf crop.
- Added bounded decode/decompression checks, EXIF orientation, fresh RGB JPEG
  rendering, metadata-free encoding, post-sanitization SHA/pHash calculation,
  deterministic healthy sampling, disease/uncertainty retention, per-class
  daily caps, structured correction/out-of-head fields, and two-phase pruning
  that does not drop a row when its image deletion fails.
- Replaced feedback request linkage/free text with strict structured receipt,
  correctness, corrected-class, and allowlisted-reason contracts. Negative
  feedback with a usable crop is protected; positive feedback promotes the
  weak label; negative feedback reopens it for offline review.
- Added a read-only timestamped training exporter with sanitized images,
  manifest, schema/version/split policy, class/source/model inventory,
  checksums, and pHash near-duplicate groups. `auto_yes` remains train-only so
  related images cannot leak into evaluation/calibration groups.
- Added an offline analysis tool for inventory, feedback quality, calibration,
  top-k/conformal coverage, out-of-head corrections, and overconfident errors.
  No serving-side retraining or human-review endpoint was added.
- Added an explicit-approval, dry-run-by-default legacy migration tool. It
  opens legacy databases read-only, joins old scan IDs only in memory,
  re-sanitizes selected images, preserves authoritative offline verdicts, and
  refuses an existing destination. All migration tests use seeded synthetic
  legacy databases and media only.
- Rebuilt telemetry as an errors-only, explicit-consent schema containing only
  allowlisted stage/kind/fatal/status bucket/model family/app version/API
  bucket/breadcrumb dimensions and a coarse day. It rejects messages,
  identifiers, hardware models, images, unknown enums, and legacy linked
  schemas; quotas retain live addresses in memory only.
- Made community thumbnails orientation-normalized, bounded 512px fresh JPEGs
  with no EXIF/ICC/source filename and hashes calculated over stored bytes.
- Minimized application/config/provider logs, ignored client correlation IDs,
  logged route templates without query strings or addresses, redacted
  exception details, and disabled raw Uvicorn access logs in both API images.
- Regenerated the application OpenAPI contract and updated local/standard
  Compose configuration, example settings, volumes, and documentation for the
  unified stores and separate consent versions.

Decisions:

- The records remain described as de-identified, not anonymous, pending the
  required legal/security review of retained pixels.
- Feedback uploads follow the same detector-crop-only rule as automatic
  capture; the server never falls back to storing a full room/background frame.
- Legacy databases and media are not migrated automatically. An operator must
  first audit and list exact approved row IDs, and apply into a fresh beta-only
  destination.
- Precise per-request diagnostic values remain in API responses where needed,
  but are not copied into request logs. Aggregated operational dimensions stay
  available through bounded metrics and the offline analyser.

Verification:

- Complete application-server suite — 572 passed in 7.25s.
- Complete account-service suite — 122 passed, 1 opt-in PostgreSQL test skipped
  in 2.85s.
- Tests cover no-consent no-write, safe crops/unsafe-frame rejection,
  orientation and metadata removal, SHA/pHash idempotency, balanced retention,
  receipt expiry/replay/promotion/reopening, pruning failures, legacy migration,
  export sentinels/checksums/duplicate grouping, telemetry schema and retention,
  thumbnail deletion, and route/header/filename/exception log redaction.
- Server and account Ruff checks, `git diff --check`, JSON contract/example
  parsing, parsed Compose contracts, and
  `docker compose -f deploy/account-beta-test/compose.local.yml config --quiet`
  — passed.
- No external provider, real user image, production database, deployment,
  model build, or training run was used.

Checkpoint commit:

- `feat(privacy): replace linked training and telemetry stores`

### 7 — Android account-beta client and privacy boundary (complete, 2026-08-07)

Completed work:

- Added the independently installable `accountBeta` build type with its own
  application ID suffix, label, account/application URL properties, enabled
  account UI, and hardcoded synthetic-auth mode. Production/release builds keep
  account UI and synthetic auth disabled.
- Added official Yandex LoginSDK and VK ID integrations behind fail-closed
  build-time configuration. The synthetic beta variant never initializes or
  starts either real provider flow, even if public OAuth application values are
  supplied accidentally.
- Added an application installation UUID, deterministic mock-provider token
  generator, private account API client, account models, Android Keystore-backed
  refresh-token vault, provider-session cleanup, and observable Guest,
  profile-required, and authenticated states.
- Implemented sign-in, nickname completion/cancellation, private profile,
  nickname change, session listing/revocation, two-step provider linking,
  deletion reauthentication, logout, and account deletion UI in English and
  Russian. Provider access and reauthentication tokens are memory-only; only
  non-secret flow routing survives activity recreation.
- Hardened account races so an operation started under one session cannot be
  replayed under a replacement account, stale unauthorized responses cannot
  erase newer credentials, cancellation propagates, and one-use mutations are
  never transport-retried automatically.
- Changed every community mutation and private count/poll call to use signed
  account credentials with one bounded refresh retry. Guest reads remain
  public; explicit Guest mutations open account UI and are never replayed
  automatically after authentication.
- Routed FCM registration/removal through the private account API and stopped
  account opinion polling on logout/deletion. No Android call sends a nickname,
  pseudo-ID, hardware identifier, or device model as mutation authority.
- Split model-improvement and telemetry into separate versioned opt-ins,
  removed the stable telemetry/pseudo identity, gated feedback on current
  training consent, and constrained telemetry to allowlisted error dimensions.
- Added one shared fresh JPEG upload sanitizer for server inference, community,
  and feedback paths. It normalizes orientation, bounds decoding, removes
  metadata, and prevents original URI/path/filename context entering requests.
- Replaced feedback linkage with server-issued random receipts plus structured
  correctness/class/reason fields, and removed free text from the Android
  contract and UI.
- Removed or made static Android logs that exposed local paths, URIs, exception
  text, plant identifiers, provider details, or request context.
- Extended CI to run ktlint, detekt, both unit-test variants, the release APK,
  and the account-beta APK; the beta APK is retained only for this branch.

Decisions:

- Synthetic provider tokens are branch/build-type fixtures only. They are
  accepted by the deterministic mock adapters and cannot trigger provider
  network access or be promoted into a production build.
- Provider display name/avatar appear only on the private account screen.
  Community rendering continues to use the nickname from verified access-token
  claims.
- The account client owns exactly one 401 refresh retry. Non-authentication
  transport failures surface to the user instead of risking duplicate one-use
  actions.
- This checkout has no Java/JDK runtime, and repository guidance excludes a
  full local Gradle build. Android compilation, SDK binary compatibility, unit
  tests, lint, and APK assembly are therefore explicit CI gates rather than
  being reported as locally executed.

Verification:

- Complete application-server suite — 572 passed in 11.23s.
- Complete account-service suite — 122 passed, 1 opt-in PostgreSQL test skipped
  in 6.62s.
- Server and account Ruff checks, `git diff --check`, Compose configuration,
  Android XML parsing, CI YAML parsing, Kotlin import/reference hygiene, and
  forbidden identity/log-field scans — passed.
- Android tests added for production/beta build identity, synthetic-token
  format, account API error/one-use retry behavior, auth lifecycle and race
  handling, separate consents, telemetry allowlists, upload metadata removal,
  and updated repository network contracts.
- No real OAuth flow, provider credential, user image, production service,
  deployment, model build, or training run was used.

Checkpoint commit:

- `feat(android): add synthetic account beta client`

### 8 — Remote provider verification contract (complete, 2026-08-07)

Completed work:

- Replaced the deliberate remote-mode startup refusal with separate Yandex ID
  and VK ID adapters selected only by validated `AUTH_PROVIDER_MODE=remote`.
- Added required public application IDs, bounded provider timeout settings,
  fixed HTTPS endpoints, system TLS, disabled redirects, a 64 KiB response
  ceiling, strict visible-ASCII token/subject validation, and stable
  non-reflective failures.
- Yandex sends its opaque token only in `Authorization: OAuth`, verifies the
  response `client_id`, and retains the app-specific `psuid`. VK sends its
  token only in the form body to client-bound `oauth2/user_info`.
- Both adapters retain only provider subject, display name, optional safe HTTPS
  avatar, and refresh time. Email, phone, birthday, gender, contacts, global
  Yandex ID, and unknown fields are discarded.
- Added fake-transport tests for exact request placement, wrong application,
  invalid/expired-style errors, outages, incomplete profiles, malformed tokens,
  unvalidated settings, unsafe subjects, oversize/malformed responses, and
  fixed transport errors. No provider network or real token was used.

Decisions:

- Local and German environments remain `AUTH_PROVIDER_MODE=mock`; remote mode
  exists solely to prevent Phase D from redesigning the account API.
- Real provider package/signature registration and token smoke tests remain a
  Russian-staging operator/legal gate. This checkpoint supersedes the earlier
  historical decision that remote mode must always refuse startup.

Verification:

- Remote-provider/config focused suite — 41 passed.
- Complete account-service suite — 150 passed, 1 opt-in PostgreSQL integration
  test skipped in 3.01s.
- Account Ruff check and `git diff --check` — passed.

Checkpoint commit:

- `feat(account): verify remote Yandex and VK tokens`

### 9 — German staging candidate and RU/DE handoff (complete, 2026-08-07)

Completed work:

- Added a no-build, digest-pinned German Compose candidate with mock auth,
  explicit synthetic-staging acknowledgement, weight-free marked inference,
  disabled collection/external credentials, loopback-only API ports, private
  PostgreSQL, read-only services, and dedicated named volumes/log policy.
- Added normalized FastAPI root-path support and strict failure for production,
  development, or unacknowledged-staging synthetic inference.
- Added distinct nginx prefix routes, independent limits, overwritten trusted
  proxy context, disabled access logs, no upstream fallback, and an operator
  requirement to run `nginx -t` before reload.
- Added a non-destructive PostgreSQL backup/restore checker that accepts only
  the exact German beta project/prefix, uses no-clobber output, restores to a
  uniquely generated database, compares every allowlisted table count, and
  publishes a checksum only after verification.
- Added a secret inventory, rollback runbook, private schema diagram, synthetic
  legacy-migration dry-run report, export privacy report, and comprehensive
  operator/legal handoff checklist.
- Added deployment-blocked RU/DE ownership and network manifests, an exact
  identity-free mTLS worker request schema, and render-tested production
  templates. The Russian backup contract uses `pg_dump` and SQLite online
  backups rather than copying live PostgreSQL storage.
- Extended CI to render local, German, Russian, and German-worker Compose files
  and parse the backup script before running the complete account suite.

Decisions:

- These are operator candidates and frozen contracts, not a deployment. No VDS,
  nginx, DNS, firewall, OAuth application, secret, APK distribution, backup,
  migration, provider, or production-data action was performed.
- Placeholder `.invalid` image/host values and zero digests are intentionally
  non-runnable. Extracted Russian app/push/backup and German-worker images are
  Phase D deliverables with explicit acceptance gates.
- The German all-in-one candidate is disposable synthetic staging only. No
  state from it may be promoted to Russia or treated as legal-production proof.

Verification:

- Complete application-server suite — 582 passed in 7.99s.
- Complete account-service suite — 150 passed, 1 skipped in 3.01s.
- Handoff/root-path/runtime focused suite — 27 passed.
- Community authorization/deletion focused suite — 115 passed.
- Training/export/migration/telemetry focused suite — 33 passed.
- All four Compose configurations rendered with `config --quiet`; backup
  script `sh -n` passed and a non-beta environment was refused before Docker.
- Official Docker metadata confirmed the pinned PostgreSQL 18.4 Alpine tag and
  digest. The correct `/var/lib/postgresql` parent mount and versioned `PGDATA`
  path are regression-tested.
- Ruff, CI YAML, three JSON contracts, 219 Android XML resources, Android
  hardware-identifier scan, and `git diff --check` passed.
- Android compilation/tests/APK assembly remain CI gates because this checkout
  has no JDK/Android SDK and repository guidance excludes a local full build.

Checkpoint commit:

- `ops(beta): add isolated staging handoff`

### 10 — Final acceptance matrix (complete, 2026-08-07)

Completed work:

- Added `ACCOUNT_BETA_TEST_REPORT.md` with exact final commands/results and a
  plan-section acceptance matrix.
- Separated locally passing implementation evidence from Android CI, German
  VDS, Russian data-plane, stateless worker, real-provider, backup/restore,
  key/certificate rotation, legacy migration/erasure, moderation, and legal
  gates.
- Recorded residual risk without claiming an APK build, deployment, provider
  end-to-end result, restore, migration, public launch, or legal approval.

Verification:

- Final account suite — 150 passed, 1 intentionally skipped opt-in PostgreSQL
  integration test.
- Final application suite — 582 passed.
- Focused matrices — provider/config 41, community/deletion 115,
  training/export/migration/telemetry 33, deployment/runtime/root path 27; all
  passed.
- Ruff, four Compose renders, shell syntax/negative scope guard, CI YAML,
  three JSON contracts, 219 Android XML resources, hardware-identifier scan,
  secret-pattern review (only explicit synthetic unit bearer fixtures), and
  `git diff --check` passed.

Checkpoint commit:

- `test(beta): record final acceptance matrix`

### 11 — Parallel German real-provider/model beta (runtime complete; mobile credentials pending, 2026-08-07)

Completed work:

- Converted the German Compose candidate from mock/weight-free mode to remote
  Yandex/VK adapters and the existing real PlantVision model bundle, mounted
  read-only. All writable databases, media, caches, logs, keys, and networks
  remain beta-prefixed and isolated from main.
- Added branch-local image builds and deployed exact tag `ea5cf0d` beside main:
  beta app `127.0.0.1:18100`, beta account `127.0.0.1:18101`, main unchanged on
  `127.0.0.1:8000`.
- Split the inter-service token into a dedicated volume. The app UID receives
  only its `0640` read-only token; Ed25519 signing and AES encryption files stay
  account-only at `0600`.
- Preserved the public HTTPS JWT issuer while validating JWKS/introspection and
  notification calls against an explicitly private Docker service origin.
- Updated Android to Yandex AuthSDK `3.2.1` and retained current VK ID SDK
  `2.7.2`. The `accountBeta` build keeps its distinct
  `com.plantdiseases.app.accountbeta` ID and fixed German beta URLs.
- Changed CI so the branch cannot publish a synthetic beta APK. Publication
  requires all real provider values and stable signing secrets, verifies the
  beta certificate matches the release certificate, and uploads only
  `account-beta-real-oauth-apk`.
- Rebased over remote CI fixes and pushed commit `ea5cf0d` to GitHub.

Runtime verification:

- PostgreSQL, account-api, and app-api are healthy; account reports remote mode
  and both providers ready.
- `/api/capabilities` reports real Gamma `plantvision` model `2.6.7` and Aqua
  available. A repository leaf image completed real detector/classifier
  inference with HTTP 200 in `0.48s`.
- Live invalid-token smoke reached both real provider endpoints and failed
  closed: Yandex `invalid_provider_token`, VK
  `provider_application_mismatch`. No real provider token was used.
- Main health passed before startup, after startup, and after inference.
- Complete suites: account `153 passed, 1 skipped`; application `590 passed`.
  Ruff, Compose rendering, YAML parsing, and `git diff --check` passed.

Boundary:

- Nginx beta prefixes remain inactive until the dedicated provider IDs and
  matching signed APK exist. The current host env uses deliberately non-runnable
  bootstrap IDs and is mode `0600`; it is ignored by Git.
- SDK code is real, but a successful SDK login and distributable APK cannot be
  truthfully produced without the external provider registrations/signing key.

Checkpoint commit:

- `feat(beta): run real SDK staging beside main`

### 12 — Correct PlantDiseases 1.9 identity and permanent signing contract (implemented, 2026-08-08)

Completed work:

- Superseded the separate installed beta identity. The internal Android build
  type is now `staging`, but its package is `com.plantdiseases.app`, launcher
  label is `PlantDiseases`, version name is `1.9`, and version code is `19`.
  It is intended to update a same-signed 1.8 installation, not coexist.
- Replaced `ACCOUNT_BETA_REAL_OAUTH` with `ACCOUNT_REAL_OAUTH`, renamed the
  staging test/tasks/output paths, and removed `.accountbeta`, the version
  suffix, and the Account Beta launcher label.
- Changed the branch artifact to `plantdiseases-1.9-staging-apk`. CI requires
  all permanent signing fields, all real provider values, and the pinned
  public `RELEASE_CERT_SHA256`; it verifies the release and staging APKs use
  that certificate.
- Added `ANDROID_1_9_SIGNING_AND_OAUTH_REGISTRATION.md` with the mandatory 1.8
  certificate recovery check, one-time key creation procedure, exact Yandex/VK
  registration identity, GitHub configuration, VDS values, and local real-SDK
  test procedure.

Boundary:

- No private key was generated. The repository has no existing APK/keystore,
  so the owner must first establish whether a distributed 1.8 already has a
  permanent signing identity. Generating a different key would break the
  update path.
- Provider IDs, protected VK mobile configuration, GitHub signing secrets,
  public nginx activation, and successful real-user OAuth remain operator
  gates.

## Remaining work (plan order)

Repository and loopback VDS work are complete. Supply/configure the provider
applications and permanent PlantDiseases signing key to unlock the real APK/login and
public beta-prefix steps. Remaining production/legal gates are listed in
`ACCOUNT_BETA_TEST_REPORT.md` and `ACCOUNT_BETA_HANDOFF.md`; they must not be
inferred as complete from the German team beta.
