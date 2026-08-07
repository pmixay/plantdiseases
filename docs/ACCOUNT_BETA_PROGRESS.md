# Account Beta Implementation Progress

This log tracks implementation on `account-beta-test`. It is updated at each
verified checkpoint; commands use only mock/synthetic data and local resources.

## Current status

- Active phase: Phase C — isolated German staging artefacts and handoff.
- Production services/data touched: none.
- Deployments performed: none.
- Blockers: none.

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

## Remaining work (plan order)

1. Add German staging artefacts and final RU/DE handoff contracts/checklists.
2. Run the complete acceptance and privacy/security verification matrix.
