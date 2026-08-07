# Local Account Beta Stack

This Compose project is a synthetic-data-only engineering environment for the
`account-beta-test` branch. It does not contact Yandex or VK, use real OAuth
tokens, reuse production storage, or download/train model artefacts. The
account service uses deterministic in-process provider adapters, so a separate
provider-mock container is unnecessary.

A one-shot, network-disabled key generator creates random signing, encryption,
and internal service-authentication keys in a beta-only named volume. It never
prints or commits them, and it leaves existing keys untouched so
account/container restarts retain the same JWKS, can decrypt previously
registered synthetic push tokens, and keep private inter-service calls stable.

The application API uses a deterministic, weight-free response pipeline. That
pipeline is guarded by both `ENVIRONMENT=test` and
`ACCOUNT_BETA_SYNTHETIC_PIPELINE=true`; requesting it in any other environment
aborts startup. Its results are marked `synthetic_test_result` and must never
be treated as diagnoses.

Model-improvement capture is disabled in this stack. The application still
uses isolated beta-only `training.db` and sanitized-image volumes so receipt,
export, retention, and migration contracts can be exercised with generated
fixtures. Feedback and community capabilities are unrelated, and no legacy
`feedback.db` or `labeling.db` is mounted.

## Start and inspect

From the repository root:

```sh
cp deploy/account-beta-test/env.example deploy/account-beta-test/.env
docker compose --env-file deploy/account-beta-test/.env \
  -f deploy/account-beta-test/compose.local.yml config --quiet
docker compose --env-file deploy/account-beta-test/.env \
  -f deploy/account-beta-test/compose.local.yml up -d
docker compose --env-file deploy/account-beta-test/.env \
  -f deploy/account-beta-test/compose.local.yml ps
docker compose --env-file deploy/account-beta-test/.env \
  -f deploy/account-beta-test/compose.local.yml logs --follow
```

The loopback-only endpoints are:

- application API: `http://127.0.0.1:18000/`;
- account API: `http://127.0.0.1:18001/`;
- Android emulator equivalents: `http://10.0.2.2:18000/` and
  `http://10.0.2.2:18001/`.

PostgreSQL has no host port and joins only the internal `beta-private` network.
The APIs additionally join a project-scoped edge network so Docker can publish
their loopback ports. Startup order is PostgreSQL health, one-shot Alembic
migration, account readiness, then application readiness.

Community writes verify the account JWT locally from account-api's JWKS and
then confirm the live session/authorization version through a private internal
route. Opinion notifications travel in the opposite direction keyed only by
the signed community actor; account-api resolves encrypted device tokens
without returning them. Account deletion uses the same private credential to
tombstone community content before private account rows are erased.

## Stop without deleting data

Ordinary shutdown retains every named beta volume:

```sh
docker compose --env-file deploy/account-beta-test/.env \
  -f deploy/account-beta-test/compose.local.yml down
```

Volume deletion is intentionally not part of that command. After an explicit
decision that the disposable synthetic state is no longer needed, deletion is
a separate action by the operator:

```sh
docker compose --env-file deploy/account-beta-test/.env \
  -f deploy/account-beta-test/compose.local.yml down --volumes
```

Do not run the deletion command merely to restart or troubleshoot the stack.
No volume, database, image, or identity from this environment may be promoted
to staging or production.

## Isolation contract

All persistent paths, including the existing image's `/app/data` compatibility
mount, use named volumes prefixed by
`plantdisease_account_beta_test`. There are no host bind mounts, production
database paths, or `server/admin_backups` mounts. APIs bind to loopback while
PostgreSQL and migration stay on the private-only network, containers drop
privilege escalation, and application containers use read-only root
filesystems with explicit writable volumes and temporary filesystems.

Automated contract tests parse this Compose file and reject unsafe ports,
mounts, mutable image tags, production paths, or relaxed test guards. CI also
runs `docker compose config --quiet`; runtime integration uses generated
fixtures only.

The loopback-only synthetic trust-boundary check can be run after both APIs are
healthy:

```sh
python deploy/account-beta-test/check_integration.py \
  --account-url http://127.0.0.1:18001 \
  --app-url http://127.0.0.1:18000 \
  --image server/static/guide/healthy.png
```

It creates only generated mock-provider identities, deletes its owner account,
and never accepts non-loopback URLs.
