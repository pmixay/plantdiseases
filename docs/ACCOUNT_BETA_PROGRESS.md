# Account Beta Implementation Progress

This log tracks implementation on `account-beta-test`. It is updated at each
verified checkpoint; commands use only mock/synthetic data and local resources.

## Current status

- Active phase: Phase B — local backend/client implementation and integration.
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

- Added the four-service `plantdisease_account_beta_test` Compose project:
  private PostgreSQL, one-shot account migration, account API, and existing
  application API.
- Kept PostgreSQL/migration private-only and put every service on the internal
  network; the APIs additionally join a project-scoped host-access network and
  their two published ports bind only to `127.0.0.1`.
- Added explicit health/dependency gates so migration waits for PostgreSQL,
  account startup waits for migration completion, and application startup waits
  for database-backed account readiness.
- Added beta-prefixed named volumes for PostgreSQL, models, community data,
  application compatibility data, thumbnails, legacy training data/images,
  telemetry, legacy push data, caches, and logs. There are no host bind mounts
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

## Remaining work (plan order)

1. Implement and verify the account API contract, token/session security,
   nickname rules, linking, reauthentication, deletion, and private push data.
2. Enforce community authentication and migrate authorship to signed actors.
3. Replace feedback/labeling stores with the anonymous training store/export.
4. Minimize telemetry and add image-sanitization coverage.
5. Implement the Android account-beta variant, auth state/client/UI flows, and
   upload sanitization.
6. Add German staging artefacts and final RU/DE handoff contracts/checklists.
7. Run the complete acceptance and privacy/security verification matrix.
