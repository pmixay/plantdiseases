# Accounts: from beta to RuStore release

Status: working plan, written 2026-10-09 after the German-VDS beta was deployed and verified.
It supersedes the Russian-VDS / German-worker split that earlier handoff documents described
(`RU_DE_MAIN_TRANSFER_AND_OPERATIONS.md`, `ACCOUNT_BETA_HANDOFF.md` and four other beta narrative
docs); those files were removed and remain in git history. The RU/DE `handoff/` templates under
`deploy/account-beta-test/` are still present and are not part of this plan.

## Decisions

| # | Decision | Date |
|---|---|---|
| 1 | No separate Russian data plane. Production accounts, the app API and PostgreSQL run on the existing VDS. Whether storing accounts outside Russia is acceptable for the RuStore audience is the owner's call. | 2026-10-09 |
| 2 | Production hostname: **reuse `pdis.arbuz.lol`**. App API at `/`, account API at `/id/` (the Android release workflow rejects any path prefix such as `/plantdisease-test/`). | 2026-10-09 |
| 3 | Yandex only. VK, email/password, FCM push and RuStore billing stay off unless decided separately. The Yandex client secret is never used (the SDK access-token flow does not need it). | 2026-10-09 |
| 4 | Private data/deletion and support contact: `mailto:gavel8980@gmail.com`, approved by the owner on 2026-10-09 (used for the privacy policy link and `release-policy-gates.json`). | 2026-10-09 |
| 5 | Beta tester access: the owner's VPN egress address (laptop and phone) is on the nginx allowlist; the address is deliberately not recorded in the repo. | 2026-10-09 |

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

Things outside the application code that must be true before real users get accounts and RuStore publishes.
Items 2-4 are checked by machine in `.github/workflows/android-release.yml`.

1. The production address works over HTTPS.
2. **Private contact** is published and approved (`release-policy-gates.json` + the `privacy-private-contact`
   link in `privacy.html` and the in-app policy): **approved 2026-10-09**.
3. **Deleted-account content retention** is decided: how long public community content of a deleted account
   stays tombstoned before it is physically removed (a fixed number of days, or a named legal basis), with
   matching English and Russian disclosures in the policy. **Blocked: the owner must choose.**
4. **Production hard stop** in the signing workflow: the `production` channel cannot be built until a *deletion
   replay journal* and a coordinated *backup epoch* exist and are restore-tested. Purpose: restoring an old
   backup must never bring back an account or content that was deleted after the backup was taken. Recovery
   channels stay buildable. This is engineering work, not a form to fill in (step 6a).
5. The APK is signed with the permanent app key (the protected signing job; agents never hold the keystore).
6. The Yandex console shows `com.plantdiseases.app` with that key's certificate fingerprint (owner confirmed
   the application ID is registered; the fingerprint must match the permanent key).
7. The signed build has been tried end to end on a real phone.
8. The RuStore listing is complete.

Building the real-provider *staging* APK through the protected workflow is also blocked until items 2 and 3 are
approved. Local Gradle builds are not blocked.

Email/password stays off in production: production settings reject it until its blockers
([EMAIL_PASSWORD_AUTH_RELEASE_BLOCKERS.md](EMAIL_PASSWORD_AUTH_RELEASE_BLOCKERS.md)) are closed.

## Steps

"Agent" means the VDS agent or a PC agent. Agents can run all builds and tests, including on-device.

| # | Step | Owner: you | Owner: agents | State |
|---|---|---|---|---|
| 0 | Open the beta to the test phone | Confirm the tester's public IP(s) | Add exact `/32`/`/128` entries to the nginx allowlist, reload, re-probe | done (owner VPN) |
| 1 | Android build and tests | Connect the phone (USB debugging) | PC agent: ktlint, detekt, unit tests, staging APK, `adb install`, instrumented tests against `/plantdisease-test/` | pending |
| 2 | Real Yandex login | Sign in on the phone (only you can) | PC agent watches logcat; VDS agent watches exchange, confirm, refresh, logout, deletion | pending |
| 3 | Production hostname | Done: reuse `pdis.arbuz.lol` | none | done |
| 4 | Production backend on this VDS | Say yes before the switch | Build the production stack (`ENVIRONMENT=production`, `account_*` roles, fresh keys, root-path URLs) beside the old service on loopback; compare against the old service; migrate legacy data (dry run first); switch nginx with instant rollback; encrypted backup plus restore check | next |
| 5 | Private contact | Provide a real public contact | Wire it into `privacy.html`, the in-app policy and the gate file; run the repo tests | done |
| 5b | Deleted-account content retention | **Choose the number of days (or a legal basis)** | Write the gate entry and the English/Russian disclosures; update the policy marker and tests | **needs owner** |
| 6 | Merge to `main` | Review and approve the PR | Apply the tooling fixes, open the PR (needs your GitHub login), fix CI | pending |
| 6a | Deletion replay journal + backup epoch | Accept the design, review | Implement an append-only deletion journal and a backup epoch across the account database and community stores, replay after restore, restore-test it, then remove the workflow stop in the same reviewed commit | pending |
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
