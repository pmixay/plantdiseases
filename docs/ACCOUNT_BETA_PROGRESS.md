# Account Beta Implementation Progress

This log tracks implementation on `account-beta-test`. It is updated at each
verified checkpoint; commands use only mock/synthetic data and local resources.

## Current status

- Active phase: Phase A — local engineering and test foundation.
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

## Remaining work (plan order)

1. Add the account PostgreSQL schema/migrations, repository test fixtures, and
   database-backed readiness checks.
2. Add the isolated local Compose stack and prove empty-state health/readiness.
3. Implement and verify the account API contract, token/session security,
   nickname rules, linking, reauthentication, deletion, and private push data.
4. Enforce community authentication and migrate authorship to signed actors.
5. Replace feedback/labeling stores with the anonymous training store/export.
6. Minimize telemetry and add image-sanitization coverage.
7. Implement the Android account-beta variant, auth state/client/UI flows, and
   upload sanitization.
8. Add German staging artefacts and final RU/DE handoff contracts/checklists.
9. Run the complete acceptance and privacy/security verification matrix.
