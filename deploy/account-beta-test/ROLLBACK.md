# Account Beta Rollback Runbook

This runbook defines operator steps; it does not execute them. Ordinary rollback
must not delete a volume, database, backup, image, or existing production
configuration.

## German real-provider beta

1. Stop distributing the `com.plantdiseases.app.accountbeta` APK and notify the
   named team testers that the beta service is unavailable.
2. Remove only the two reviewed beta `location` blocks from the candidate nginx
   configuration. Run `nginx -t` before a reload. Do not edit the existing
   production upstream or location.
3. Run Compose `config` and `ps` with the dedicated mode-0600 German beta env
   file and confirm the project name is exactly
   `plantdisease_account_beta_german`.
4. Stop that project with ordinary `docker compose ... down`. Do not add
   `--volumes`; the debugging window and data-disposition decision are separate.
5. Record the image digests, migration revision, shutdown time, reason, and
   synthetic-data assertion. The existing production service remains running.
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

## Final Russian/German split

Before enabling real accounts, preserve the last known-good Russian image
digests, schema revision, encrypted backup, JWKS overlap, and Android endpoint
configuration. A failed release rolls Android/Russian ingress traffic back to
the prior Russian services. It must not redirect Android directly to Germany.

If the German ML worker fails, the Russian API returns a bounded unavailable
response or uses an explicitly approved Russian fallback; it never forwards
account context to a different endpoint. If account/community migration fails,
writes remain frozen and clients remain on the prior reviewed Russian state.
Any restore uses a fresh target followed by count/checksum/application checks;
it never overwrites the only copy in place.

## Evidence to retain

- incident/rollback decision and named approvers;
- exact image and configuration digests;
- `nginx -t`, Compose render, health, and synthetic smoke outcomes;
- migration revision and read-only inventory counts;
- encrypted backup checksum and restore-check result;
- German disk/log inspection result;
- confirmation that production data and the pre-existing German service were
  untouched by the beta rollback.
