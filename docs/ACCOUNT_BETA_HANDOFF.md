# Account Beta RU/DE Infrastructure Handoff

Contract version: `account-beta-ru-de-v1`

Status: application and restricted German-beta handoff. The branch defines a
parallel real-provider/real-model staging service and real-OAuth APK build.
Public production, Russian migration, final RU/DE split, and German-beta state
promotion remain blocked by the checklists below.

Operator entry points:

- implementation and test procedure:
  [ACCOUNT_BETA_FINAL_IMPLEMENTATION_GUIDE.md](ACCOUNT_BETA_FINAL_IMPLEMENTATION_GUIDE.md);
- source promotion, RU/DE transfer, and joint maintenance:
  [RU_DE_MAIN_TRANSFER_AND_OPERATIONS.md](RU_DE_MAIN_TRANSFER_AND_OPERATIONS.md).

## Delivered application contracts

- Account OpenAPI: `account-server/openapi.json`
- Application OpenAPI: `server/openapi.json`
- Private schema/migrations: `account-server/migrations/` and
  `ACCOUNT_BETA_SCHEMA.md`
- Local stack: `deploy/account-beta-test/compose.local.yml`
- German staging candidate: `compose.german-vds.yml`, `env.german.example`,
  and `nginx.location.conf`
- Secret inventory and rollback: `SECRETS.md` and `ROLLBACK.md`
- Final ownership/network manifest:
  `handoff/ru-de-service-manifest.yml`
- Stateless worker request contract: `handoff/ml-worker.openapi.json`
- Final render-tested templates: `handoff/compose.russia.template.yml` and
  `handoff/compose.germany-worker.template.yml`
- Migration/export evidence: `ACCOUNT_BETA_LEGACY_MIGRATION_DRY_RUN.md` and
  `ACCOUNT_BETA_EXPORT_PRIVACY_REPORT.md`
- Verification evidence: `ACCOUNT_BETA_TEST_REPORT.md` and the checkpoint log.

The production Compose files are contract templates. The Phase D owner must
produce digest-pinned extracted `RU_APP_API_IMAGE`, `RU_PUSH_WORKER_IMAGE`,
`RU_BACKUP_IMAGE`, and `DE_ML_WORKER_IMAGE` artifacts. Placeholder `.invalid`
references, nonexistent paths, and required variable guards prevent the
committed example from being a runnable production configuration.

## Frozen provider verification contract

`AUTH_PROVIDER_MODE=remote` is selected by German beta and final production;
local automated testing remains mock-only. Remote startup requires the two
public application IDs.
The account API calls only fixed HTTPS provider endpoints with a five-second
bounded default timeout and a 64 KiB response ceiling; redirects and malformed
or oversized responses fail closed.

- Yandex: send the opaque token only as `Authorization: OAuth`, require the
  response `client_id` to equal the configured application, retain app-specific
  `psuid`, display name, optional derived HTTPS avatar, and refresh time.
- VK ID: send the opaque token only in the HTTPS form body to client-bound
  `oauth2/user_info`, retain user ID, combined display name, optional HTTPS
  avatar, and refresh time.
- Both: ignore email, phone, birthday, gender, contacts, and unknown response
  fields; never log or persist the provider access token.

The behavior follows [Yandex's documented user-information response and
application binding](https://yandex.com/dev/id/doc/en/user-information) and
[VK ID's official SDK `user_info` request
shape](https://github.com/VKCOM/vkid-web-sdk/blob/master/src/auth/auth.ts).
Tests inject a fake transport and never contact either provider. Provider
package/signature registration and real-token smoke tests occur first on the
restricted German beta and must be repeated on Russian staging before release.

## German real-provider staging operator sequence

1. Check out the accepted `account-beta-test` commit and use its git SHA in the
   two local image tags. Build only through `compose.german-vds.build.yml`.
2. Copy `env.german.example` to a mode-0600 host file. Set the image tags,
   password, provider application IDs, and read-only PlantVision model path;
   retain the exact project and volume prefix. Keep FCM/external-AI disabled.
3. Inventory current Docker projects, volumes, ports, nginx locations, and
   production paths read-only. Resolve any collision before continuing.
4. Render `compose.german-vds.yml`. Confirm only `127.0.0.1:18100/18101` are
   published and every persistent resource begins
   `plantdisease_account_beta_german_`.
5. Determine the exact proxy peer address seen inside each container. Set
   `BETA_FORWARDED_ALLOW_IPS` and `BETA_TRUSTED_PROXIES` to exact values; never
   `*` or an unnecessarily broad network.
6. Add the two rate-limit zones and beta location fragment to an nginx
   candidate. `nginx -t` must pass. Diff must show no change to production
   upstream/location/error fallback.
7. The operator may then start the isolated project. Wait for PostgreSQL,
   migration, account readiness, and application health in dependency order.
8. Run loopback health/model smoke tests with deliberately prepared metadata-free
   plant imagery. Verify the beta model family/version matches main.
9. Build/install the permanently signed PlantDiseases 1.9 real-OAuth staging
   APK for named team testers. Confirm it is `com.plantdiseases.app`, updates a
   same-signed 1.8 install, uses `/plantdisease-test/` URLs, and does not show
   the synthetic-auth notice.
10. Record image/config digests, schema revision, tests, storage inventory, and
    tester list. Staging data is disposable and never promotable.

The operator records actual execution of steps 7-10 in the progress/test report.

## Final inference boundary

```text
Android
  -> Russian TLS ingress
     -> Russian app API: validate + fresh JPEG re-sanitize
        -> construct exactly five allowlisted fields
           -> private mTLS German worker
           <- request_id + diagnosis result
        <- Russian persistence/feedback/community decisions
     <- Android response
```

The Russian client must build a new multipart request object rather than proxy
the inbound request. Only `image`, one-use random `request_id`, `model_family`,
`language`, and `result_options` are serialized. Authorization/cookies, all
other headers/query/form fields, account/actor/session/nickname/provider data,
installation/FCM values, source filename, metadata, client address, and
training/feedback consent never cross the border. The response request ID must
match, be consumed once, and be discarded after the bounded request.

The German worker imports only inference/image-validation/static taxonomy
modules. It has a read-only root and model mount, tmpfs only, no database or
user-data volume, no request/body/access log, and aggregate low-cardinality
metrics. Firewall plus mTLS restrict access to Russian ingress. Android has no
German URL or trust credential.

## Russian data-plane acceptance checklist

- [ ] Infrastructure and every persistent volume/backup physically reside in
  Russia under the Russian operator.
- [ ] Account/application/push/backup/ingress images are immutable digests from
  the accepted commit; SBOM/provenance and vulnerability review are recorded.
- [ ] Production renders with `AUTH_PROVIDER_MODE=remote`; mock/synthetic or
  ephemeral keys cause startup refusal.
- [ ] Account signing, encryption, internal, database, FCM, mTLS, TLS, and
  backup keys follow `SECRETS.md`; secret files pass non-root ownership and
  mode-0400 checks.
- [ ] PostgreSQL/internal services have no host/public port; strict CORS and
  exact trusted proxies are configured.
- [ ] Application, community, user media, push, training, telemetry, logs, and
  backups all use Russian-owned paths. German paths are absent.
- [ ] Russian app reconstructs the identity-free worker request and a negative
  test proves injected Authorization/account/actor/session/nickname fields do
  not reach a fake worker.
- [ ] Community Guest/read and account/write matrix, encrypted push, deletion
  outage/retry/hide/erase, media removal, and stale-token cleanup pass in RU
  staging.
- [ ] Nightly encrypted backup retention is seven daily/four weekly; a restore
  into a fresh target and an Ed25519 overlap/rotation exercise pass. PostgreSQL
  is captured with a consistent `pg_dump`, never by copying live `PGDATA`;
  SQLite stores use their online backup API before encryption.
- [ ] Metrics contain only allowlisted low-cardinality dimensions; logs and
  exception paths pass the token/profile/nickname/image/IP inspection.
- [ ] Real Yandex/VK smoke tests use dedicated Russian staging fixtures after
  provider package/signature registration and legal approval.

## German worker acceptance checklist

- [ ] The only running application service is the worker in the final manifest;
  no account/community/push/training/telemetry/cache/database/backup process or
  volume exists.
- [ ] Direct public and Android access fail at firewall; only the fixed Russian
  source plus valid mTLS reaches `/v1/infer`.
- [ ] OpenAPI/request parser rejects every additional field and non-JPEG,
  oversize, over-dimension, decompression-bomb, replayed-ID, and concurrent
  over-limit request.
- [ ] Runtime uses read-only image/model files and tmpfs; a restart and host
  disk scan find no request image, result, ID, token, cache, or temp file.
- [ ] Access/body logging is disabled. Error logs and metrics contain no request
  ID beyond bounded in-memory correlation and no image hash or personal label.
- [ ] Dependency/import scan proves account, community, push, telemetry,
  feedback, training store, PlantNet/OpenRouter, and persistent cache modules
  are absent.
- [ ] Certificate rotation overlap/revocation and Russian outage behavior pass.

## Migration and cutover checklist

- [ ] Freeze German writes and inventory community/media, feedback, labeling,
  telemetry, push, application/nginx/system logs, caches, admin backups, host
  backups, and provider snapshots.
- [ ] Legal/data owner classifies every field/file and signs the exact allowlist
  and exclusions. No “delete one column equals anonymous” assumption is used.
- [ ] Run the legacy training tool without `--apply`; review counts and privacy
  exclusions. Apply only to a fresh Russian target after written approval.
- [ ] Transfer valid community/media and transformed training material through
  an encrypted channel; discard unowned old FCM rows.
- [ ] Compare row/image counts, hashes, schema revision, and application samples;
  restore encrypted backups into fresh Russian targets.
- [ ] Change Android/Russian ingress only after RU/DE acceptance. Monitor before
  external invitation.
- [ ] Separately authorize and record German erasure of migrated personal/user-
  linked data, copies, backups, logs, temporary files, and snapshots.
- [ ] Legacy public rows remain readable, unclaimable, and outside new account
  ownership.

## Release-blocking legal/product gates

- Article 22 operator notification or documented counsel exception;
- Article 12 assessment/notification and German processor/hosting documents for
  potential image transfer;
- accurate Russian and English policies plus separately versioned account,
  community, push, telemetry, model-improvement, export, retention, deletion,
  and external-service disclosures;
- external community reporting, blocking, moderation, and safety controls;
- separate review of PlantNet/OpenRouter, FCM consent/initialization, location,
  threat model, and any future sync/media changes.

Any attempt to open German beta beyond named testers, enable personal FCM,
training/telemetry capture or production user media, promote German beta state,
or expose the final worker directly is a release-blocking architecture change,
not an operator shortcut.
