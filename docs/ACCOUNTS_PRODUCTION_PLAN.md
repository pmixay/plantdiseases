# Accounts: from beta to RuStore release

Status: working plan, written 2026-10-09 and updated 2026-10-12 after production deletion verification.
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
| 6 | Deleted-account marker retention: **fixed 30 days** after deletion, then erased. The marker is the minimal pseudonymous record that stops removed content from reappearing when cleanup is replayed or a backup is restored; 30 days outlasts the 4-week backup window. The server must enforce this expiry before release (step 6a). Approved by the owner 2026-10-09. | 2026-10-09 |
| 7 | First release stores all data (accounts, PostgreSQL, app API) on the German VDS; there is no Russian server. The privacy policy (hosted and in-app) states this and no longer describes a Russian server forwarding photos to a German worker. Whether this is acceptable for the RuStore audience remains the owner's responsibility. | 2026-10-13 |
| 8 | AI model: the cheapest, `openai/gpt-6-luna` (the code default; production is rebuilt without pinning the older `gpt-5.6-luna`). AI spend is to be capped; the per-account quota numbers are not decided yet. | 2026-10-13 |

## Where things stand

- Beta (`ENVIRONMENT=staging`) is deployed from `account-beta-test@03bc2359` under `/plantdisease-test/`
  and `/plantdisease-test/id/`: Alembic head `0010_billing_lifecycle_quota_policy`, 311 account tests
  green on Python 3.11, encrypted backup and restore check passing, `/v1/internal/*` blocked at the edge,
  access restricted to the owner's VPN allowlist.
- **Production backend is live (2026-10-09)** on the same VDS from commit `305f1ad` (the deletion-journal work)
  (`ENVIRONMENT=production`, separate PostgreSQL with `account_*` roles, fresh keys). `https://pdis.arbuz.lol/` now serves the
  new app (the old service stays running, idle, as the rollback); `/id/` is the account API, restricted to the VPN allowlist until
  launch. Daily encrypted backups with the deletion-journal epoch run from a systemd timer. The VDS operator files are not part of
  the application code; see `deploy/production-vds/`.
- The app-to-account trust boundary passed on the repo's synthetic mock-provider stack and on the production stack with
  secret-free checks. The production pair also passed real Yandex signup, profile/cold-start restore, a signed-in live
  Community read, logout/cold-start guest state and re-login on the locally signed production-configured APK.
  Production deletion was completed by the owner on 2026-10-09 and verified from the PC and VDS on
  2026-10-12: the phone stayed signed out after a cold start and Yandex required a fresh signup;
  the original profile and identities are absent, the deletion job is complete and the journal contains the deletion.
  The app marker exists under the deployed 30-day retention policy. The first daily backup after deletion
  (2026-10-10) and the latest backup (2026-10-12) both contain the deletion and their matching epochs.
  The latest encrypted artifact checksums and scheduled backup/restore check also passed.
- **Android detekt failure fixed locally:** `3e6d346` extracts species metadata and cache policy from
  `ScanRepository` into `SpeciesRepository`. The original finding counted 1333 lines against the unchanged 1300 limit
  (1849 was the whole source-file length). Both `ktlintCheck` and `detekt` pass, all five unit-test variants pass
  (2887 tests), and release/staging/both recovery APKs build. See the
  [Android beta verification report](test-reports/android-account-beta-2026-10-09.json).
  The signing workflow still requires successful GitHub CI for the exact commit before step 7.
- **Real beta Yandex flow passed on the phone:** the owner completed sign-in and nickname/signup confirmation
  on the locally signed staging APK at `03bc2359`. Cold-start session restore and the signed-in live Community read
  also passed after the Android fix. Logout stayed signed out after a cold restart. The owner then completed
  real staging deletion: the phone stayed signed out after another cold restart, and the same Yandex identity
  required a new signup. The owner completed that new staging signup. These checks use the local debug certificate
  added to Yandex by the owner. The production-configured local APK also passed signup, restore, signed-in Community
  read, logout, re-login and deletion. The owner created a fresh production profile after the 2026-10-12
  new-signup check; it restored after a cold restart, and the original deleted profile remains absent.
  See the [production deletion follow-up](test-reports/android-account-production-2026-10-12.json).
  The permanently signed APK still needs
  its own real-provider test. No fresh deletion or deployment was performed during the 2026-10-12 verification.
- **Ordinary CI signing references removed:** `b2e842b` removes the release-signing passwords/alias and stale signing
  step output from the compilation/test job. A deployment contract prevents `RELEASE_*` inputs and a signing
  environment in that job; all 19 deployment contract tests and Ruff passed.
- **GitHub CI passed on `3f13385`:** all four jobs, including Android build/lint and both server suites,
  succeeded in [run 37965210687](https://github.com/pmixay/plantdiseases/actions/runs/37965210687).
  Main has since advanced to `73cfb24`, and PR #182 now reports conflicts. The earlier merge at `aab7795`
  resolved conflicts with the then-current main (`e4b4a45`); it does not cover the newer main changes.


## Release gates in plain words

Things outside the application code that must be true before real users get accounts and RuStore publishes.
Items 2-4 are checked by machine in `.github/workflows/android-release.yml`.

1. The production address works over HTTPS.
2. **Private contact** is published and approved (`release-policy-gates.json` + the `privacy-private-contact`
   link in `privacy.html` and the in-app policy): **approved 2026-10-09**.
3. **Deleted-account marker retention** is decided: how long the minimal pseudonymous marker of a deleted account
   is kept (it stops removed content from reappearing on cleanup replay or backup restore), with matching English and
   Russian disclosures in the policy: **30 days, approved 2026-10-09.** The deployed app's retention constant
   and the original account's marker were checked on 2026-10-12. The marker's calculated policy expiry is
   2026-11-08; natural expiry of this particular marker has not yet elapsed.
4. **Production hard stop** (lifted 2026-10-09) in the signing workflow: the `production` channel could not be built until a *deletion
   replay journal* and a coordinated *backup epoch* exist and are restore-tested. Purpose: restoring an old
   backup must never bring back an account or content that was deleted after the backup was taken. Recovery
   channels stay buildable. This is engineering work, not a form to fill in (step 6a).
5. The APK is signed with the permanent app key (the protected signing job; agents never hold the keystore).
6. The Yandex console shows `com.plantdiseases.app` with that key's certificate fingerprint (owner confirmed
   the application ID is registered; the fingerprint must match the permanent key).
7. The signed build has been tried end to end on a real phone.
8. The RuStore listing is complete.

Building the real-provider *staging* APK through the protected workflow was blocked until items 2 and 3 were
approved; both now are. Local Gradle builds are not blocked.

Email/password stays off in production: production settings reject it until its blockers
([EMAIL_PASSWORD_AUTH_RELEASE_BLOCKERS.md](EMAIL_PASSWORD_AUTH_RELEASE_BLOCKERS.md)) are closed.

## Steps

"Agent" means the VDS agent or a PC agent. Agents can run all builds and tests, including on-device.

| # | Step | Owner: you | Owner: agents | State |
|---|---|---|---|---|
| 0 | Open the beta to the test phone | Confirm the tester's public IP(s) | Add exact `/32`/`/128` entries to the nginx allowlist, reload, re-probe | done (owner VPN) |
| 1 | Android build and tests | Connect the phone (USB debugging) | PC: ktlint, detekt, all five unit-test variants, all four CI APK builds, staging install and live device smoke passed; all 9 instrumented tests passed after fixing their Hilt host, permission dialog and locale/view assertions | build/unit/device/instrumented checks passed |
| 2 | Real Yandex login | Completed both real-provider tests and production deletion | Beta and local production APK signup, restore, signed-in app read, logout, re-login and deletion passed. Production deletion was verified on the phone and VDS, including its journal, marker and subsequent encrypted backups. The owner created a fresh production profile | beta and production checks passed with the local certificate |
| 3 | Production hostname | Done: reuse `pdis.arbuz.lol` | none | done |
| 4 | Production backend on this VDS | Said yes (2026-10-09) | Built beside the old service, compared (same capabilities and scan result), legacy data copied (4 scans, 3 push tokens), nginx switched with a rollback script; `/id/` stays VPN-only until launch | **done** |
| 5 | Private contact | Provide a real public contact | Wire it into `privacy.html`, the in-app policy and the gate file; run the repo tests | done |
| 5b | Deleted-account marker retention | Choose the number of days: **30** | Write the gate entry and the English/Russian disclosures; update the policy marker and tests | done |
| 6 | Merge to `main` | Review and approve [PR #182](https://github.com/pmixay/plantdiseases/pull/182) | All four CI jobs passed on `3f13385`. Earlier main conflicts were resolved at `aab7795`; the conflicts with `main@73cfb24` (#185) were resolved in `5135c89` and all four CI jobs passed on that head | mergeable; final-head CI required before merging |
| 6a | Deletion replay journal + backup epoch | Approved the design (2026-10-09) | Implemented, restore-drilled (15/15), review fixes applied (closed-service restore step, off-host retention rule, backup snapshot path); the production stop in `android-release.yml` is removed | **done** |
| 7 | Signed release build | Configure the `android-signing` environment, its required reviewers/secrets/certificate and approved environment variables per [DEPLOYMENT.md](../DEPLOYMENT.md); approve main merge | The workflow is absent from main, and the protected environment is missing. The PC account has write access without admin access; it configured and read back the four public repository API URL variables. Trigger the protected workflow after the owner setup and green main CI; verify APK digest, certificate and endpoints. Agents never handle the keystore. Findings 2026-10-13: the permanent key already exists as repository-level secrets (created 2026-06-09) and signs the current CI `app-release-apk`; its certificate SHA-256 is `ef9563c5db397924c1f81239b9ed5fa1e9b233f034ce10eb75f16efd36a26367` (the `RELEASE_CERT_SHA256` value). GitHub never shows secret values, so the four secrets must be re-entered in the environment from the original keystore. The production channel also needs the three `PRODUCTION_RUSTORE_*` repository variables (RuStore Console app ID, `plantdiseases_pro_monthly`, `plantdiseases_pro_yearly`) even though server billing stays off | blocked on owner environment setup/main merge |
| 8 | Device check of the signed APK | Log in on the production build | PC agent: install and run the checklist (signup, cancel, refresh, logout, deletion) | pending |
| 9 | RuStore listing | Console clicks and APK upload | Release notes, checklist, privacy page check | pending |
| 10 | Housekeeping | Copy the backup key (and backups) off the VDS | Daily encrypted backups scheduled and tested (done); still open: SSH key-only login, monitoring with alerts on failed backups and `journal_unavailable` deletion retries | in progress |

The owner supplies the support contact, holds the keystore and GitHub secrets, and approves protected
signing and publication. Agents may drive the connected phone and complete authorized login/consent
clicks; the owner supplies any required private credentials or verification codes.

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

Merge of PR #182 after final-head CI;
protected GitHub signing environment, secrets and approved variables; RuStore console access. The production hostname,
tester allowlist, support contact and step 4 switch are already approved and configured.
