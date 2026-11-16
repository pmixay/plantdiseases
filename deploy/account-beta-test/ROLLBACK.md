# Account Beta Rollback Runbook

This runbook defines operator steps; it does not execute them. Ordinary rollback
must not delete a volume, database, backup, image, or existing production
configuration.

## Android client recovery

Before distributing the same-package versionCode 19 staging APK, dispatch
`recovery-staging` from reviewed `main` or `account-beta-test`. Verify that its
endpoint hashes equal the exact `STAGING_APP_API_BASE_URL` and
`STAGING_ACCOUNT_API_BASE_URL` pair used by the staging APK. Before distributing
versionCode 19 production, dispatch `recovery-production` from reviewed `main`.
Verify its endpoint hashes equal the reviewed `PRODUCTION_APP_API_BASE_URL` and
`PRODUCTION_ACCOUNT_API_BASE_URL` values and the protected
`APPROVED_PRODUCTION_*` copies. For each artifact also verify the pinned release
certificate, disabled account UI, versionCode 20, channel-specific name, and
provenance; retain both in the immutable release archive.

Never use staging recovery for production or production recovery for staging.
The account endpoint, including its base path, scopes the encrypted refresh-token
vault, installation identity, and queued remote revocations. Redirecting a
recovery build to the other issuer can strand the very state rollback must drain.

Android will not install versionCode 18 over 19. If an account client must be
withdrawn, distribute the matching signed recovery APK as an update instead of
asking users to uninstall. This preserves local app data while preventing new
account use. Keep that channel's account services available long enough to drain
queued remote logout/deletion work, then follow the data-retention decision
below. Record which devices received which recovery artifact. Any later
mandatory normal release must have versionCode 21 or higher.

## German real-provider beta

1. Stop distributing the PlantDiseases 1.9 staging APK
   (`com.plantdiseases.app`) and notify the
   named team testers that the beta service is unavailable.
   If testers already installed versionCode 19, distribute the reviewed
   versionCode 20 `recovery-staging` APK before withdrawing account endpoints.
2. Remove only the two reviewed beta `location` blocks from the candidate nginx
   configuration. Run `nginx -t` before a reload. Do not edit the existing
   production upstream or location.
3. Run Compose `config` and `ps` with the dedicated mode-0600 German beta env
   file and confirm the project name is exactly
   `plantdisease_account_beta_german`.
4. Stop that project with ordinary `docker compose ... down`. Do not add
   `--volumes`; the debugging window and data-disposition decision are separate.
5. Record the image digests, migration revision, shutdown time, reason, and the
   inventory/retention owner for real tester account data. The existing
   production service remains running.
6. After the agreed debugging window, a named operator and reviewer inventory
   the beta-prefixed volumes and backups. Only after written confirmation may
   the operator remove those exact disposable resources. Record what was
   removed and whether any encrypted diagnostic backup remains. Provider access
   tokens are not part of any backup; verify no token was logged before closing
   the incident.

Rollback success means both beta prefixes return the intentionally selected
closed response, the old production route still passes its pre-change smoke
test, PostgreSQL was never exposed, and no non-beta resource changed.

## Application/API checkpoint rollback

Use a new revert commit on `account-beta-test`; do not reset or rewrite shared
history. Rebuild immutable images, run the complete test matrix, and change the
German env file to the newly reviewed digests. Database downgrade is never an
automatic rollback step. The Alembic downgrade is exercised only on a fresh
synthetic database; an operator must approve any stateful rollback separately.

## Evidence to retain

- incident/rollback decision and named approvers;
- exact image and configuration digests;
- `nginx -t`, Compose render, health, and synthetic smoke outcomes;
- migration revision and read-only inventory counts;
- encrypted backup checksum and restore-check result;
- German disk/log inspection result;
- confirmation that production data and the pre-existing German service were
  untouched by the beta rollback.
