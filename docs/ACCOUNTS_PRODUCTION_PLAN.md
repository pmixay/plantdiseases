# Accounts: from beta to RuStore release

Status: working plan, written 2026-10-09 after the German-VDS beta was deployed and verified.
It supersedes the Russian-VDS / German-worker split described in
[RU_DE_MAIN_TRANSFER_AND_OPERATIONS.md](RU_DE_MAIN_TRANSFER_AND_OPERATIONS.md) and
[ACCOUNT_BETA_HANDOFF.md](ACCOUNT_BETA_HANDOFF.md); those files are kept until they are
deliberately retired.

## Decisions

| # | Decision | Date |
|---|---|---|
| 1 | No separate Russian data plane. Production accounts, the app API and PostgreSQL run on the existing VDS. Whether storing accounts outside Russia is acceptable for the RuStore audience is the owner's call. | 2026-10-09 |
| 2 | Production hostname: **reuse `pdis.arbuz.lol`**. App API at `/`, account API at `/id/` (the Android release workflow rejects any path prefix such as `/plantdisease-test/`). | 2026-10-09 |
| 3 | Yandex only. VK, email/password, FCM push and RuStore billing stay off unless decided separately. The Yandex client secret is never used (the SDK access-token flow does not need it). | 2026-10-09 |

## Where things stand

- Beta (`ENVIRONMENT=staging`) is deployed from `account-beta-test@03bc2359` under `/plantdisease-test/`
  and `/plantdisease-test/id/`: Alembic head `0010_billing_lifecycle_quota_policy`, 311 account tests
  green on Python 3.11, encrypted backup and restore check passing, `/v1/internal/*` blocked at the edge,
  access restricted to an IP allowlist (currently loopback only).
- The app-to-account trust boundary (JWT via JWKS, introspection, nickname and deletion callbacks,
  revocation) passed on the repo's synthetic mock-provider stack. It has not been run with a real
  Yandex token.
- The live main service on `/` is untouched.

## Release gates in plain words

Things outside the code that must be true before real users get accounts and RuStore publishes:

1. The production address works over HTTPS.
2. A working public support and moderation contact exists (required for user-generated content).
3. The APK is signed with the permanent app key.
4. The Yandex console shows `com.plantdiseases.app` with that key's certificate fingerprint.
5. The signed build has been tried end to end on a real phone.
6. The RuStore listing is complete.

Email/password stays off in production: production settings reject it until its blockers
([EMAIL_PASSWORD_AUTH_RELEASE_BLOCKERS.md](EMAIL_PASSWORD_AUTH_RELEASE_BLOCKERS.md)) are closed.

## Steps

"Agent" means the VDS agent or a PC agent. Agents can run all builds and tests, including on-device.

| # | Step | Owner: you | Owner: agents | State |
|---|---|---|---|---|
| 0 | Open the beta to the test phone | Confirm the tester's public IP(s) | Add exact `/32`/`/128` entries to the nginx allowlist, reload, re-probe | pending |
| 1 | Android build and tests | Connect the phone (USB debugging) | PC agent: ktlint, detekt, unit tests, staging APK, `adb install`, instrumented tests against `/plantdisease-test/` | pending |
| 2 | Real Yandex login | Sign in on the phone (only you can) | PC agent watches logcat; VDS agent watches exchange, confirm, refresh, logout, deletion | pending |
| 3 | Production hostname | Done: reuse `pdis.arbuz.lol` | none | done |
| 4 | Production backend on this VDS | Say yes before the switch | Build the production stack (`ENVIRONMENT=production`, `account_*` roles, fresh keys, root-path URLs) beside the old service on loopback; compare against the old service; migrate legacy data (dry run first); switch nginx with instant rollback; encrypted backup plus restore check | next |
| 5 | Support and moderation contact | Provide a real public contact | Wire it into the app and privacy text; check links resolve | pending |
| 6 | Merge to `main` | Review and approve the PR | Apply the tooling fixes, open the PR (needs your GitHub login), fix CI | pending |
| 7 | Signed release build | Keystore secrets, required reviewers, `PRODUCTION_*` URLs, cutover switch set to true | Trigger the protected workflow; verify APK digest, certificate fingerprint, endpoints. Agents never handle the keystore | pending |
| 8 | Device check of the signed APK | Log in on the production build | PC agent: install and run the checklist (signup, cancel, refresh, logout, deletion) | pending |
| 9 | RuStore listing | Console clicks and APK upload | Release notes, checklist, privacy page check | pending |
| 10 | Housekeeping | Copy the backup key off the VDS | Scheduled backups, SSH key-only login, monitoring | pending |

Only the owner can: supply the support contact, hold the keystore and GitHub secrets, do the logins,
and click through Yandex, GitHub and RuStore consoles.

## What taking over `/` means (step 4)

One nginx change: `/` goes to the new app, `/id/` to the account service. It is a graceful reload with no downtime.

- **Data:** the new app starts with empty community, feedback and telemetry stores. The old data stays in
  `/var/lib/plantdiseases` and is carried over only by the legacy-migration tool (dry run first).
- **Push, AI features:** the beta runs without FCM and without PlantNet/OpenRouter keys. Production must be given the
  old service's settings (keys loaded from owner-only files, never printed).
- **Older app versions:** they will call the new server. `/api/capabilities` matched main's apart from the account
  fields, but an old client has not been tested against it.
- **Rollback:** point nginx back at the old service (seconds). Writes made to the new app after the switch (accounts,
  posts) are not in the old one.

Order: build and verify beside the old service, compare responses, only then switch, keep the old service running as the fallback.

## Safety rules

- Never pull `account-beta-test` into the live main checkout; deploy from a separate release directory at a recorded SHA.
- Never `docker compose down --volumes` on the beta or production project. Never downgrade the database automatically.
- Secrets live outside Git and images; never print them. The test key generator stays test-only.
- Tester access is an exact-IP allowlist; the internal `/v1/internal/*` and `/api/internal/*` routes stay blocked at the edge.

## Open inputs

Tester IP(s); support contact; GitHub signing secrets and variables; RuStore console access; confirmation before the step 4 switch.
