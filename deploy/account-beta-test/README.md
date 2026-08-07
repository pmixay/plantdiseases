# Account Beta Test Environments

This directory contains two environments for the `account-beta-test` branch:
the local stack remains deterministic and synthetic, while the isolated German
VDS stack uses real Yandex/VK provider verification and the real PlantVision
model bundle. Neither environment reuses main's database, media, logs, caches,
account state, or writable model paths.

A one-shot, network-disabled key generator creates random signing/encryption
keys in an account-only volume plus one internal service token in a separate
read-only-to-the-app volume. It never prints or commits them, and it leaves
existing values untouched so
account/container restarts retain the same JWKS, can decrypt previously
registered beta push tokens, and keep private inter-service calls stable.

The local application API uses a deterministic, weight-free response pipeline
and requires `ENVIRONMENT=test` plus
`ACCOUNT_BETA_SYNTHETIC_PIPELINE=true`. German staging explicitly disables
that switch and mounts the already-provisioned PlantVision model directory
read-only, so its diagnosis path is the same model as main but its executable
code and all writable state come from this branch.

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

## Build the Android account beta

The branch-only `accountBeta` variant has application ID
`com.plantdiseases.app.accountbeta`, so it can coexist with the production app.
It defaults to deterministic synthetic authentication: tapping Yandex or VK
creates a mock-provider credential from the app's random installation UUID.
The first use still exercises the pending-signup/nickname flow; later uses of
the same installation/provider exercise existing-account sign-in.

With this local stack running, build and test the emulator APK from `android/`:

```sh
./gradlew :app:testAccountBetaUnitTest :app:assembleAccountBeta \
  -PAPP_API_BASE_URL=http://10.0.2.2:18000/ \
  -PACCOUNT_API_BASE_URL=http://10.0.2.2:18001/
```

Install the APK from `app/build/outputs/apk/accountBeta/`. Cleartext remains
limited to emulator loopback (`10.0.2.2`, `localhost`, and `127.0.0.1`) in this
variant; arbitrary LAN cleartext is not enabled. Without URL properties the
variant points only to `https://pdis.arbuz.lol/plantdisease-test/` and its
`id/` account route, never to the production API root.

### Build with the real Yandex and VK ID SDKs

Real-provider testing is opt-in at build time. Register the Android application ID
`com.plantdiseases.app.accountbeta` and the APK signing-certificate fingerprint
with the providers first. Put the mobile client values in the user's private
`~/.gradle/gradle.properties` (never in this repository):

```properties
ACCOUNT_BETA_REAL_OAUTH=true
YANDEX_CLIENT_ID=replace-with-yandex-client-id
VKID_CLIENT_ID=12345
VKID_CLIENT_SECRET=replace-with-vk-mobile-client-value
APP_API_BASE_URL=https://pdis.arbuz.lol/plantdisease-test/
ACCOUNT_API_BASE_URL=https://pdis.arbuz.lol/plantdisease-test/id/
```

Then build from `android/`:

```sh
./gradlew :app:testAccountBetaUnitTest :app:assembleAccountBeta
```

The real mode fails configuration if either provider value or either explicit
API URL is absent; both URLs must be credential-free HTTPS origins. The
official SDK callbacks pass the short-lived provider access token directly to
account-api; the app does not persist it. The configured account-api must use
`AUTH_PROVIDER_MODE=remote` with the same Yandex/VK application IDs.

The local stack remains mock-only. The German `/plantdisease-test/id/` stack is
the dedicated real-provider beta target and must use the same Yandex/VK client
IDs as the APK. Keep it limited to named testers and dedicated test accounts;
it is not a public or production account service.

## Build and run the parallel German real-provider beta

`compose.german-vds.yml` defines the isolated runtime and
`compose.german-vds.build.yml` builds its application images from the checked
out `account-beta-test` branch. Copy `env.german.example` to a host-only
mode-0600 file, use git-SHA-qualified local image tags, generate a dedicated
PostgreSQL password, set both provider application IDs and the absolute
PlantVision model directory, and verify the exact Docker proxy peer addresses.
Wildcard trusted-proxy values are forbidden.

Render without starting anything:

```sh
docker compose --env-file /protected/account-beta-german.env \
  -f deploy/account-beta-test/compose.german-vds.yml \
  -f deploy/account-beta-test/compose.german-vds.build.yml config --quiet
docker compose --env-file /protected/account-beta-german.env \
  -f deploy/account-beta-test/compose.german-vds.yml \
  -f deploy/account-beta-test/compose.german-vds.build.yml build
docker compose --env-file /protected/account-beta-german.env \
  -f deploy/account-beta-test/compose.german-vds.yml \
  -f deploy/account-beta-test/compose.german-vds.build.yml up -d
```

The candidate binds only `127.0.0.1:18100` and `127.0.0.1:18101` for the host
nginx. PostgreSQL remains internal. It uses `ENVIRONMENT=staging`, remote
provider adapters, real read-only PlantVision weights, disabled training
capture/telemetry/FCM/external AI, and dedicated
`plantdisease_account_beta_german_*` writable volumes. Main continues on
`127.0.0.1:8000`; beta uses only `18100/18101`.

The app receives only the group-readable internal service token through its
dedicated volume. The Ed25519 signing key and AES push-encryption key stay in
the account-only volume and are not mounted into the app container.

Before nginx reload, add the two rate-limit zones documented at the top of
`nginx.location.conf`, include its location blocks in a candidate TLS server,
and run `nginx -t`. The `id/` prefix is routed first to account API; both
prefixes strip correctly, overwrite forwarded client context, disable access
logs, and have no production fallback. `ACCOUNT_ROOT_PATH` and `APP_ROOT_PATH`
keep generated documentation/media paths under their external prefixes.

The synthetic integration checker is only for `compose.local.yml`; it sends mock
provider tokens and therefore must not be run against German remote-provider
mode. For German staging, check both loopback health endpoints, compare
`/api/capabilities` with main, then run the real login matrix from the signed
account-beta APK.

The optional `backup-test.sh` accepts only the exact German beta project and
volume prefix. It writes a protected custom-format dump to an explicit absolute
directory, restores it into a uniquely named temporary database, compares
allowlisted table counts, and drops only that generated restore database. It
does not stop the stack or remove volumes.

## Handoff material

- `../../docs/ACCOUNT_BETA_FINAL_IMPLEMENTATION_GUIDE.md` is the concise
  implementation, build, synthetic-test, and real-SDK test entry point.
- `../../docs/RU_DE_MAIN_TRANSFER_AND_OPERATIONS.md` defines source promotion,
  staged transfer, cutover, and concurrent main/Russian/German maintenance.
- `SECRETS.md` inventories environment and secret ownership without values.
- `ROLLBACK.md` defines non-destructive beta and final-split rollback evidence.
- `handoff/ru-de-service-manifest.yml` freezes data ownership and network flow.
- `handoff/ml-worker.openapi.json` is the identity-free mTLS inference request
  contract.
- `handoff/compose.russia.template.yml` and
  `handoff/compose.germany-worker.template.yml` are render-tested contract
  templates for the separately assigned split participant. They are explicitly
  blocked until extracted images, infrastructure, secrets, legal gates, backup
  restore, key rotation, and operator acceptance exist.

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

Automated contract tests parse both Compose environments and reject unsafe
ports, writable production paths, or relaxed guards. German staging's sole
shared host path is the immutable PlantVision bundle mounted read-only.

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
