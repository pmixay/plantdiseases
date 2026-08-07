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

## Remaining work (plan order)

1. Finish and commit the hermetic local test baseline.
2. Add the account service test/application scaffold, deterministic mock
   providers, PostgreSQL schema/migrations, safety configuration, and CI job.
3. Add the isolated local Compose stack and prove empty-state health/readiness.
4. Implement and verify the account API contract, token/session security,
   nickname rules, linking, reauthentication, deletion, and private push data.
5. Enforce community authentication and migrate authorship to signed actors.
6. Replace feedback/labeling stores with the anonymous training store/export.
7. Minimize telemetry and add image-sanitization coverage.
8. Implement the Android account-beta variant, auth state/client/UI flows, and
   upload sanitization.
9. Add German staging artefacts and final RU/DE handoff contracts/checklists.
10. Run the complete acceptance and privacy/security verification matrix.
