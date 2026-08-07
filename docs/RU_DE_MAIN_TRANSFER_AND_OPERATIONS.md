# Main, Russian VDS, and German VDS Transfer and Operations Runbook

Status: operator handoff procedure. It explains how to keep the current main
service available while introducing the Russian data plane and the German
stateless ML worker. It is not permission to migrate or erase data, change DNS,
reload nginx, enable OAuth, or deploy production.

Testing and Android SDK setup are covered by
[ACCOUNT_BETA_FINAL_IMPLEMENTATION_GUIDE.md](ACCOUNT_BETA_FINAL_IMPLEMENTATION_GUIDE.md).

## 1. Do not confuse the four environments

| Environment | Public role | Persistent data | Authentication | Current/target runtime |
|---|---|---|---|---|
| Existing German main | Existing public app API during transition | Existing legacy application state | Existing behavior; no new real account store | `plantdiseases.service`, Uvicorn on `127.0.0.1:8000`, host nginx |
| German account beta | Optional `/plantdisease-test/` team staging | Dedicated disposable beta volumes | Mock identities only | Isolated Compose, loopback `18100/18101` |
| Russian data plane | Final Android/API ingress and all user-derived storage | Account, community, media, training, telemetry, logs, push and backups | Real Yandex/VK | Reviewed production deployment derived from `compose.russia.template.yml` |
| German ML worker | Private inference called only from Russia | None; models are read-only, request temp is tmpfs | mTLS service identity only | Reviewed worker derived from `compose.germany-worker.template.yml` |

The existing main service and German beta may remain online while Russian
staging is prepared. After production cutover, Germany may keep only the
stateless ML worker (plus an explicitly time-bounded legacy rollback service).
Android must never call the German worker directly.

## 2. Target request flow

```text
Android
  -> Russian HTTPS ingress
     -> Russian account API / PostgreSQL
     -> Russian application API / community / media / telemetry
        -> rebuild identity-free inference request
           -> private mTLS -> German ML worker
           <- diagnosis only
        <- persist or notify in Russia
  <- response from Russia
```

Only sanitized JPEG bytes, a one-use request ID, model family, language, and
result options may cross from Russia to Germany. Authorization, cookies,
account/actor/session IDs, nickname, provider data, installation/FCM values,
source filename, metadata, client address, and consent state must not cross.

## 3. Source promotion is separate from infrastructure cutover

1. Keep implementation on `account-beta-test` until Android, account, server,
   deployment-contract, privacy, and migration checks pass.
2. Open a reviewed pull request to `main`; do not rewrite shared history or
   pull the beta branch into the live `/home/plantdiseases/repo` checkout.
3. Build immutable, digest-addressed server images and signed APKs from the
   accepted commit in trusted CI. Record commit, image digest, schema revision,
   SBOM/provenance, and test run.
4. Merging the code must not automatically enable production accounts. The
   current production Android build has account UI disabled; enabling it,
   adding production OAuth configuration, and pointing it to Russian URLs is a
   separate reviewed release change.
5. Register the production package `com.plantdiseases.app` and stable release
   signing fingerprint separately from the beta package
   `com.plantdiseases.app.accountbeta`.

## 4. Controlled transfer sequence

### Phase 0 — Record a safe baseline

- Name the Russian, German, Android, database, security, and rollback owners.
- Record current main commit/configuration, systemd unit, nginx configuration,
  health response, model versions, open ports, persistent paths, and backup
  state without printing secrets.
- Preserve the current known-good release and confirm its rollback health test.
- Inventory legacy community/media, feedback, labeling, telemetry, push,
  caches, logs, snapshots, and backups. Classification and transfer approval
  are operator/legal gates, not assumptions made by a script.
- Verify that `SECRETS.md`, `ROLLBACK.md`, both OpenAPI contracts, and the
  RU/DE service manifest match the accepted commit.

### Phase 1 — Add the German worker without changing main

1. Produce a dedicated worker image containing inference/image-validation
   code only. It must not import account, community, push, telemetry, feedback,
   training-store, PlantNet/OpenRouter, or persistent-cache modules.
2. Provision the private RU-to-DE route and mTLS identities. Bind the worker to
   the private tunnel address, never `0.0.0.0` or a public nginx location.
3. Mount models read-only, use tmpfs for request handling, disable request/body
   access logs, and expose aggregate low-cardinality metrics only.
4. Keep existing German main on `127.0.0.1:8000`. If synthetic beta is active,
   retain its dedicated `18100/18101` ports. Use a separate reviewed worker
   port such as the template's `18443`; resolve collisions before starting.
5. Prove public access fails, valid Russian mTLS succeeds, extra fields fail,
   restart leaves no request artifacts, and certificate overlap/revocation
   works.

### Phase 2 — Build the Russian data plane privately

1. Replace every `.invalid` image, host, directory, and destination in
   `handoff/env.production.example` with protected operator configuration.
2. Provision Russian PostgreSQL and persistent paths for account, community,
   media, training, telemetry, logs, push, and encrypted backups.
3. Create separate owner-readable secret directories for account, app, push,
   backup, ingress, and mTLS material. Do not weaken permissions to share one
   file; copy the internal token into owner-specific files with identical
   bytes.
4. Render the final Compose/configuration before starting. Run migrations as a
   one-shot job, then require account readiness before app readiness.
5. Confirm internal services and PostgreSQL have no public host port. Expose
   only Russian TLS ingress to Android.
6. Test the Russian reconstruction of the worker request with malicious extra
   headers/fields and prove none reaches a fake German worker.
7. Complete encrypted backup and restore into a fresh target before accepting
   any real account.

### Phase 3 — Real-provider Russian staging

- Configure remote Yandex/VK verification with staging application IDs.
- Register the beta package/signing fingerprint and build `accountBeta` with
  Russian HTTPS URLs as described in the final implementation guide.
- Run the full real-provider test matrix with named testers and dedicated test
  accounts.
- Inspect provider, ingress, application, database, and worker logs for the
  prohibited token/profile/image fields before inviting another tester.
- Do not use the German synthetic endpoint as a fallback.

### Phase 4 — Migrate approved state into fresh Russian targets

1. Freeze writes at the recorded cutover time while keeping reads available if
   the application contract permits it.
2. Take protected source snapshots and run
   `server/tools/migrate_legacy_training_data.py` without `--apply` against
   explicit reviewed paths and allowlisted row IDs.
3. Review counts and exclusions, then apply only to fresh Russian targets.
   Transfer approved community/media and transformed training material over an
   encrypted channel; discard unowned old push registrations.
4. Verify schema revisions, row/image counts, hashes, application samples, and
   fresh-target backup restore. Legacy community rows remain readable,
   unclaimable, and outside new account ownership.
5. Keep the source frozen until cutover acceptance. German erasure is a later,
   separately recorded action; migration success alone is not deletion
   authorization.

### Phase 5 — Cut over clients, then retire legacy data paths

1. Release the signed production APK with account UI explicitly enabled,
   production package/provider registration, and Russian app/account URLs.
2. Change DNS/ingress only after Russian and German-worker acceptance. Monitor
   errors, latency, provider mismatch, refresh failures, deletion jobs, worker
   failures, and backup completion.
3. Retain the last known-good Russian deployment and the frozen legacy main for
   the agreed rollback window. A rollback returns clients to the prior Russian
   application state or prior non-account release; it never sends account
   traffic to Germany.
4. After acceptance and separate authorization, remove user-derived data,
   copies, logs, temporary files, backups, and snapshots from Germany. Keep the
   stateless ML worker and its read-only models only.

## 5. Operating all services during the transition

Run read-only health checks before and after every change:

```sh
systemctl is-active plantdiseases.service
curl -fsS http://127.0.0.1:8000/api/health
curl -fsS https://replace-with-russian-host/id/health
curl -fsS https://replace-with-russian-host/id/ready
curl -fsS https://replace-with-russian-host/api/health
```

The German worker health check must be made from the Russian/private mTLS path,
not from the public Internet. If the German synthetic beta is active, inspect
it only with its exact project/env file and loopback ports.

| Frequency | Existing main | Russian data plane | German worker |
|---|---|---|---|
| Every change | systemd state, loopback/public health, nginx syntax | rendered config, migration revision, account/app health | private mTLS health, public denial |
| Daily | error rate, disk, model availability | DB/storage, deletion queue, refresh/provider errors, backup result | latency/errors, certificate expiry, tmpfs/disk residue |
| Weekly | rollback release still identifiable | restore sample/fresh-target evidence, storage growth, key expiry | dependency/import drift and firewall review |
| Every release | preserve prior unit/artifact/config | preserve prior digests/schema/JWKS overlap | preserve prior worker/model digest and cert overlap |

Do not update the live main checkout with an unreviewed `git pull`. Preserve
untracked exports/backups, deploy an accepted artifact or versioned release,
run tests before switching, and keep an explicit rollback target. Do not mix a
systemd hardening/user migration with the account cutover unless it has its own
test and rollback plan.

## 6. Safe update order

For a backward-compatible release:

1. deploy a German worker that accepts both old and new request contracts;
2. deploy Russian database expand-only migration;
3. deploy Russian account API, then Russian app/push services;
4. run server and real-beta smoke tests;
5. release Android last;
6. remove old fields/schema only in a later release after old clients and the
   rollback window have expired.

Never downgrade a live database automatically. Test downgrade only on a fresh
copy and require explicit approval for any stateful rollback.

## 7. Secret and certificate rotation

- **OAuth IDs/mobile configuration:** coordinate provider console, server, and
  APK. A client-ID change requires a new APK and matching server setting.
- **Ed25519 account signing:** publish old and new public keys together, issue
  with the new key, wait through token/rollback overlap, then retire the old
  key.
- **Account encryption key:** use an explicit re-encryption plan for stored push
  tokens; never simply replace the file.
- **Internal service token:** rotate account and app owner-specific copies
  atomically.
- **RU/DE mTLS:** overlap CA/cert trust, verify both paths, switch, then revoke
  the old certificate.
- **Database/backup/TLS credentials:** rotate through protected host tooling
  and prove reconnect plus fresh-target restore.

Never print values to compare them. Record identifiers, versions, fingerprints,
expiry dates, and completion evidence only.

## 8. Failure and rollback routing

| Failure | Required behavior |
|---|---|
| Existing German main fails before cutover | Use its existing systemd/nginx rollback; do not involve Russian account state |
| Russian account/app release fails | Freeze writes if needed and return to the last known-good Russian images/config; do not redirect to German beta/main |
| German ML worker fails | Russian API returns bounded unavailable or uses an explicitly approved Russian fallback; no direct Android reroute |
| Migration verification fails | Keep sources frozen, discard only the fresh failed target, and remain on the prior service |
| Provider registration/config mismatch | Stop real-account invitation, correct package/fingerprint/client alignment, and rebuild; never weaken token verification |
| Backup restore fails | Block production cutover until a fresh-target restore passes |

Follow `deploy/account-beta-test/ROLLBACK.md` and retain the decision, named
approvers, image/config digests, schema revision, health results, backup
checksum/restore result, and German disk/log inspection.

## 9. Required transfer bundle

The receiving operator must get:

- accepted Git commit and immutable image/APK digests;
- account, app, and worker OpenAPI contracts;
- final reviewed RU and DE deployment files plus redacted config inventory;
- schema/migration revision and fresh-target restore evidence;
- provider package IDs, client-ID ownership, and signing fingerprints (no
  provider or application tokens);
- secret/certificate ownership and rotation calendar;
- DNS, TLS, firewall, proxy, port, monitoring, and backup ownership;
- migration allowlist/exclusion decision and German erasure owner;
- test report, rollback target, rollback commands, and on-call contacts.

Transfer is complete only when the receiving operator can independently render
the configurations, restore a backup into a fresh target, rotate a test key,
run the synthetic and real-provider smoke matrices, and execute a rehearsed
rollback without access to the implementer's workstation.

## Authoritative contracts

- [ACCOUNT_BETA_HANDOFF.md](ACCOUNT_BETA_HANDOFF.md)
- [ACCOUNT_BETA_TEST_REPORT.md](ACCOUNT_BETA_TEST_REPORT.md)
- [ACCOUNT_BETA_LEGACY_MIGRATION_DRY_RUN.md](ACCOUNT_BETA_LEGACY_MIGRATION_DRY_RUN.md)
- [ACCOUNT_BETA_EXPORT_PRIVACY_REPORT.md](ACCOUNT_BETA_EXPORT_PRIVACY_REPORT.md)
- [../deploy/account-beta-test/SECRETS.md](../deploy/account-beta-test/SECRETS.md)
- [../deploy/account-beta-test/ROLLBACK.md](../deploy/account-beta-test/ROLLBACK.md)
- `deploy/account-beta-test/handoff/ru-de-service-manifest.yml`
- `deploy/account-beta-test/handoff/ml-worker.openapi.json`
- `deploy/account-beta-test/handoff/compose.russia.template.yml`
- `deploy/account-beta-test/handoff/compose.germany-worker.template.yml`
