# Account System Test Environments

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
their loopback ports. Startup order is PostgreSQL health, bootstrap role
provisioning, one-shot Alembic migration plus migrator-owned grant convergence,
account readiness, then application readiness.

Community writes verify the account JWT locally from account-api's JWKS and
then confirm the live session/authorization version through a private internal
route. Opinion notifications travel in the opposite direction keyed only by
the signed community actor; account-api resolves encrypted device tokens
without returning them. Account deletion uses the same private credential to
tombstone community content before private account rows are erased.

## Build PlantDiseases 1.9 for staging

The branch-only `staging` build type is an endpoint-selecting build of the real
app: package `com.plantdiseases.app`, launcher name `PlantDiseases`, version
name `1.9`, and version code `19`. It does not coexist with 1.8; with the same
permanent certificate it updates 1.8 in place. It uses the same permanent signing certificate as production (`DEPLOYMENT.md` §8).

With `ACCOUNT_REAL_OAUTH=false`, local builds use deterministic
synthetic authentication: tapping Yandex or VK creates a mock-provider
credential from the app's random installation UUID. The first use still
exercises the pending-signup/nickname flow; later uses of the same
installation/provider exercise existing-account sign-in.

With this local stack running, build and test the emulator APK from `android/`:

```sh
./gradlew :app:assembleDebug \
  -PAPP_API_BASE_URL=http://10.0.2.2:18000/ \
  -PACCOUNT_API_BASE_URL=http://10.0.2.2:18001/ \
  -PACCOUNT_REAL_OAUTH=false
```

Install the APK from `app/build/outputs/apk/debug/`. Cleartext remains
limited to emulator loopback (`10.0.2.2`, `localhost`, and `127.0.0.1`) in this
variant; arbitrary LAN cleartext is not enabled. Without URL properties every
build points to the production server (`https://pdis.arbuz.lol/` and its `id/`
account route): the Android app no longer has a staging variant.

### Build with one or both real provider SDKs

Real-provider testing is opt-in at build time. Register Android application ID
`com.plantdiseases.app` and the permanent PlantDiseases signing-certificate
fingerprint with each provider you enable. The public Yandex client ID is
pinned in the Android release configuration. Put any future VK mobile values
in the user's private `~/.gradle/gradle.properties` (never in this repository).
The current Yandex-only configuration is:

```properties
ACCOUNT_REAL_OAUTH=true
APP_API_BASE_URL=https://pdis.arbuz.lol/plantdisease-test/
ACCOUNT_API_BASE_URL=https://pdis.arbuz.lol/plantdisease-test/id/
```

Then build from `android/`:

```sh
./gradlew :app:assembleDebug
```

The real mode requires at least one provider. Yandex is already enabled; VK
independently needs both `VKID_CLIENT_ID` and `VKID_CLIENT_SECRET`. Both
explicit API URLs are always required and must be
credential-free HTTPS origins. The
official SDK callbacks pass the short-lived provider access token directly to
account-api; the app does not persist it. The configured account-api must use
`AUTH_PROVIDER_MODE=remote` with the same ID for every enabled provider. A real
distributable build must use the permanent certificate; a debug-signed local
APK is not an acceptable substitute.

The local stack remains mock-only. The German `/plantdisease-test/id/` stack is
the dedicated real-provider staging target and must use the same enabled-provider
client IDs as the APK. Keep it limited to named testers and dedicated test accounts;
it is not a public or production account service. German staging defaults
`EMAIL_PASSWORD_AUTH_ENABLED` to `false`. Changing it to `true` is a scheduled
operator action, not a general beta default.

## Build and run the parallel German real-provider beta

`compose.german-vds.yml` defines the isolated runtime and
`compose.german-vds.build.yml` builds its application images from the checked
out `account-beta-test` branch. Copy `env.german.example` to a host-only
mode-0600 file, use git-SHA-qualified local image tags, generate the three
URL-facing PostgreSQL role passwords with `openssl rand -hex 32`, set the
registered Yandex application ID, leave VK unset until it is independently
enabled, keep email/password auth off, set the absolute PlantVision model directory and an account-owned
`BETA_RUSTORE_SECRETS_DIR`, and verify the exact Docker proxy peer addresses.
Keep both `RUSTORE_LIFECYCLE_ENABLED=false` and
`RUSTORE_CHECKOUT_ENABLED=false` and the secret directory empty for an
OAuth-only run. A subscription sandbox run additionally requires separate
mode-0400 `private-key.b64` and `notification-key.b64` files plus its reviewed
application/key IDs; never reuse production material.
Wildcard trusted-proxy values are forbidden.

For an existing beta volume, set `BETA_POSTGRES_ADMIN_PASSWORD` to its original
`beta_account` password. Compose runs `beta-account-role-upgrade` before
Alembic; that idempotent job creates/rotates the least-privilege roles, moves
existing schema ownership to the migrator, and removes stale grants. The
migrator command then upgrades to the exact reviewed head and, using that same
non-superuser owner connection, runs `converge-runtime-grants.py`. Convergence
requires the exact 24 application tables plus `alembic_version`, rejects any
sequence or unexpected table, grants runtime DML only on application tables,
and grants runtime SELECT-only on `alembic_version`. Account API startup waits
for both operations. A failed role upgrade, migration, or grant convergence is
a stop condition—do not delete the volume to make it pass.

Build into temporary commit-specific registry tags without starting anything:

```sh
docker compose --env-file /protected/account-beta-german.env \
  -f deploy/account-beta-test/compose.german-vds.yml \
  -f deploy/account-beta-test/compose.german-vds.build.yml config --quiet
docker compose --env-file /protected/account-beta-german.env \
  -f deploy/account-beta-test/compose.german-vds.yml \
  -f deploy/account-beta-test/compose.german-vds.build.yml build
docker compose --env-file /protected/account-beta-german.env \
  -f deploy/account-beta-test/compose.german-vds.yml \
  -f deploy/account-beta-test/compose.german-vds.build.yml push
```

Resolve the two pushed manifest references as
`registry/project/image@sha256:<64 hex>` and replace
`BETA_ACCOUNT_IMAGE` / `BETA_APP_IMAGE` in the protected environment file.
The all-zero example digests are a stop condition. Render the runtime file and
start it **without** the build overlay, so a later tag move cannot change what
will execute:

```sh
docker compose --env-file /protected/account-beta-german.env \
  -f deploy/account-beta-test/compose.german-vds.yml config --quiet
docker compose --env-file /protected/account-beta-german.env \
  -f deploy/account-beta-test/compose.german-vds.yml up -d
```

Record the two resolved manifest digests with the tested commit. If the
operator cannot push to and resolve an approved registry, real-provider staging
remains blocked; a mutable local tag is not an equivalent provenance boundary.

The candidate binds only `127.0.0.1:18100` and `127.0.0.1:18101` for the host
nginx. PostgreSQL remains internal. It uses `ENVIRONMENT=staging`, remote
provider adapters, real read-only PlantVision weights, disabled training
capture/telemetry/FCM/external AI, and dedicated
`plantdisease_account_beta_german_*` writable volumes. Main continues on
`127.0.0.1:8000`; beta uses only `18100/18101`.

The Compose file caps CPU, memory, PIDs, and local logs. Before `up`, the host
owner must also place the beta volumes on a monitored filesystem/project quota
with an explicit byte limit and alert threshold; Compose cannot portably cap a
named volume. Missing disk isolation is a stop condition on the shared VDS.

The app receives only the group-readable internal service token through its
dedicated volume. The Ed25519 signing key, account encryption/derivation key,
and optional RuStore RSA/AES sandbox keys stay account-owned and are not
mounted into the app container.

Before nginx reload, add the two rate-limit zones documented at the top of
`nginx.location.conf`. Copy `nginx.testers.allow.example` to
`/etc/nginx/plantdisease-beta-testers.allow`, replace every documentation-only
address with an exact named tester `/32`, `/128`, or controlled VPN egress
address, retain the final `deny all`, and never use `allow all` or a broad
mobile/ISP range. Include the location blocks in a candidate TLS server and run
`nginx -t`; the missing allowlist file must remain a fail-closed configuration
error. The `id/` prefix is routed first to account API; both
prefixes strip correctly, overwrite forwarded client context, disable access
logs, and have no production fallback. `ACCOUNT_ROOT_PATH` and `APP_ROOT_PATH`
keep generated documentation/media paths under their external prefixes.

Only after that edge restriction is active on the human-facing account and app
locations and its denied/allowed probes are recorded may an operator explicitly
set `EMAIL_PASSWORD_AUTH_ENABLED=true` for a scheduled exercise using disposable
addresses. The separately source-allowlisted RuStore notification callback is
not a human tester path and grants no access to email routes. Turn the flag back
to `false` after the exercise. Disabling it rejects register/login/reauth; any
email completion or confirmation presented while disabled is durably cancelled
and cannot resume after a later re-enable. OAuth pending signups continue
normally. The flag is not a substitute for the edge allowlist.

The synthetic integration checker is only for `compose.local.yml`; it sends mock
provider tokens and therefore must not be run against German remote-provider
mode. For German staging, check both loopback health endpoints, compare
`/api/capabilities` with main, then run the real login matrix from the
permanently signed PlantDiseases 1.9 staging APK.

The optional `backup-test.sh` requires Bash (for fail-closed pipeline status),
Docker Compose, age, and age-keygen. It accepts only the exact German beta project and
volume prefix plus an absolute mode-0400/0600 age identity. It streams the
custom-format dump directly through age encryption, writes only ciphertext to
the explicit absolute output directory, decrypts as a pipe into a uniquely
named temporary restore database owned by the migrator, reapplies the canonical
runtime/backup grants, compares the exact reviewed table set and allowlisted
row counts through the read-only backup role, requires both source and restore
to be at Alembic revision `0010_billing_lifecycle_quota_policy` (including the durable
nickname outbox/claim registry, two-phase signup completion, private password
credential, PRO entitlement tables, and aggregate PlantNet budget authority),
and actively verifies runtime DML on the restored signup table, denied runtime
DDL, denied runtime UPDATE/DELETE of `alembic_version`, retained migrator
ownership/write, and denied
backup writes before dropping only that generated restore database. It pins the
Compose project and rendered volume namespace against ambient overrides, takes
an atomic per-output-directory lock, and rejects stale `beta_restore_*`
databases left by a hard-killed earlier check, and prunes only its own encrypted
backup/checksum namespace after four weeks inside the canonical non-root output
directory. It never writes a plaintext dump file, stops the stack, or removes
volumes. A stale restore is an incident: inspect it, record the exact name, then
drop only that database before rerunning the check.

## Related documents

- The email/password beta is a production HOLD: German staging defaults it
  off and requires an explicit, edge-restricted tester exercise; production
  settings reject an attempted enable.
- `SECRETS.md` inventories environment and secret ownership without values.
- `ROLLBACK.md` defines non-destructive beta rollback evidence.

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
