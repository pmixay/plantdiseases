# Account System Staging Environment and Secret Inventory

No confidential secret belongs in Git, an image layer, an APK, a command line,
a log, or this document. Android OAuth application identifiers—and the VK
mobile value commonly named a client secret—are extractable public client
configuration and are never accepted as proof of identity. The registered
Yandex Android client ID is intentionally pinned in source so release builds
cannot silently omit LoginSDK; the Yandex client secret is never used.

## Local and German account-system staging

| Value | Classification | Owner/storage | Consumer | Rotation |
|---|---|---|---|---|
| `BETA_ACCOUNT_IMAGE`, `BETA_APP_IMAGE` | Public immutable image references | Mode-0600 operator env file | Compose | Every tested release |
| `BETA_POSTGRES_ADMIN_PASSWORD` | Staging bootstrap secret | Mode-0600 operator env file | PostgreSQL initialization/idempotent role upgrade only | Preserve for an existing beta volume; rotate deliberately |
| `BETA_POSTGRES_MIGRATOR_PASSWORD` | Staging DDL/ownership secret | Mode-0600 operator env file | One-shot Alembic migration and post-migration grant convergence | Recreate with disposable stack |
| `BETA_POSTGRES_RUNTIME_PASSWORD` | Staging application-DML secret | Mode-0600 operator env file | Account API; SELECT-only on `alembic_version`, no sequence access | Recreate with disposable stack |
| `BETA_POSTGRES_BACKUP_PASSWORD` | Staging read-only secret | Mode-0600 operator env file | Encrypted backup check | Recreate with disposable stack |
| `YANDEX_CLIENT_ID`, `VKID_CLIENT_ID` | Public provider application IDs registered for `com.plantdiseases.app` | Yandex: tracked Android release config plus mode-0600 server env; VK: local build property plus server env | Account API/Android | Change only with provider registration |
| `EMAIL_PASSWORD_AUTH_ENABLED` | Non-secret production-HOLD control | Mode-0600 operator env plus local Android build configuration; tracked German Compose default is `false` | Account API/Android | Explicit `true` only for a scheduled non-production exercise after the named-tester edge allowlist is active; return to `false`; production rejects it |
| `/etc/nginx/plantdisease-beta-testers.allow` | Restricted tester/VPN egress addresses; do not place names, emails, or roster metadata in this file | Root-owned host nginx configuration, least-readable mode accepted by `nginx -t`; roster/approval stays in the protected operator record | German host nginx | Replace exact `/32`/`/128` entries for each scheduled exercise and remove access immediately when a tester leaves |
| Android `VKID_CLIENT_SECRET` | Extractable public mobile config | Local Android build property (public, extractable), never server env | VK ID SDK | Change only with provider registration |
| `RUSTORE_APPLICATION_ID`, `RUSTORE_KEY_ID`, subscription product IDs, notification-key epoch | Public RuStore configuration | Mode-0600 operator env plus local Android build properties where applicable | Account API/Android | Change only with reviewed Console configuration; advance the epoch atomically with every AES-key rotation |
| `private-key.b64` | Staging-only Base64 PKCS#8 RSA private key | `BETA_RUSTORE_SECRETS_DIR`, account-owned mode 0400 | Account API RuStore Public API authorization | Destroy with the disposable sandbox integration |
| `notification-key.b64` | Staging-only Base64 AES-256 callback key | `BETA_RUSTORE_SECRETS_DIR`, account-owned mode 0400 | Account API callback decryption | Destroy with the disposable sandbox integration |
| `BETA_MODELS_DIR` | Non-secret absolute model path | Host-only operator env | App API read-only mount | Verify at each model release |
| `signing.json` | Beta Ed25519 private key ring | Dedicated `*_account_keys` volume, mode 0600 | Account API | Exercise before handoff; never promote |
| `encryption.key` | Beta AES-256 account encryption/derivation key | Same beta-only key volume, mode 0600 | Account API refresh rotation, email fingerprints, and future push-token protection | Recreate only with the disposable stack; existing sessions must reauthenticate and password lookup identities change |
| `internal.token` | Beta service credential | Dedicated `*_internal_token` volume, mode 0640; read-only in app | Account and app APIs | Recreate with disposable stack |
| `BETA_FORWARDED_ALLOW_IPS`, `BETA_TRUSTED_PROXIES` | Security configuration, not secret | Mode-0600 operator env file | Uvicorn/application API | Verify whenever Docker networking changes |

Generate the migrator, runtime, and backup database passwords with
`openssl rand -hex 32`. Their 64-hex format is validated before migrations so
raw URL interpolation cannot reinterpret `@`, `:`, `/`, `%`, or `#`. On an
existing German beta volume, keep the original `beta_account` password as
`BETA_POSTGRES_ADMIN_PASSWORD`; it is used only by the idempotent role-upgrade
job and never by an API.

The German environment has dedicated values for each enabled provider; Yandex
and VK do not depend on one another. Provider tokens pass once through memory
for verification and are never persisted or
logged. It has no FCM credentials, PlantNet/OpenRouter keys, production account
keys, or production backup credential. Key generation has no network and
refuses to overwrite an existing key file.

## Android signing

There is no CI signing. `.github/workflows/ci.yml` is compilation/test evidence
only and must never receive the permanent keystore or live provider
configuration. Distributable APKs are built and signed locally by the owner with
Gradle (`-PRELEASE_STORE_FILE`, `-PRELEASE_STORE_PASSWORD`,
`-PRELEASE_KEY_ALIAS`, `-PRELEASE_KEY_PASSWORD`, or the same-named environment
variables); procedure in `DEPLOYMENT.md` §8.
The keystore, its passwords and any base64 copy stay on the owner's machine and
encrypted backups, out of Git and out of GitHub secrets or variables. Do not
hand them to another person or tool.

Everything else the Android build needs is public, extractable mobile
configuration, not a secret: the Yandex native client ID is pinned in source;
the optional VK pair is absent for the current Yandex-only release (if enabled
later it must be a complete pair, distinct for staging and production); the
reviewed endpoint pairs, the RuStore Console application ID and the PRO product
IDs are reference values documented in the signing guide and passed to Gradle as
`-P` properties or environment variables. Gradle itself validates endpoints and
RuStore values at configuration time. Production and staging recovery APKs
remain distinct because endpoint-derived encrypted stores and queued
revocations must stay with their original account issuer.

FCM values are deliberately absent: version 1.9 uses polling until a durable
push worker is implemented and reviewed.

## Secret file handling

Account-service startup checks every configured account secret is an absolute
regular file. Signing and encryption keys permit no group/world access. The
beta internal token may be group-readable, but never group-writable or
world-accessible, because it lives alone in a dedicated volume mounted
read-only into the differently-UID app container.
The app API applies the same absolute, regular, non-symlink, owner-only and
bounded-file contract to optional vendor API keys. Production rejects direct
`PLANTNET_API_KEY` / `OPENROUTER_API_KEY` environment/config values and any
simultaneous direct and file setting. Leave each `*_API_KEY_FILE` blank to keep
that feature off.
Migrator/runtime/backup database passwords use a validated
64-hex format; the bootstrap password is transported through `PGPASSWORD`, not
embedded in a URL.

PlantNet is default-off. To enable it after the
named review gates pass, keep `PLANTNET_BUDGET_ENABLED=false` while an operator
provisions `plantnet-api.key` in the app secrets directory for the app UID as a regular
mode-0400 file. Set only the in-container path
`PLANTNET_API_KEY_FILE=/run/app-secrets/plantnet-api.key`, validate the reviewed
policy version, attempt ceiling, marginal cost, and cost-exposure ceiling, then
enable the account authority with `PLANTNET_BUDGET_ENABLED=true` and restart the
account and app services. Never put the credential itself in the environment
file. For rotation or emergency disablement, first restore
`PLANTNET_BUDGET_ENABLED=false` and blank `PLANTNET_API_KEY_FILE`, deploy that
disabled state and verify no new upstream-attempt metric increments; only then
replace the host file. Re-enable through the same reviewed sequence. A missing
file, invalid file, unavailable authority, or disabled budget must fail closed
before PlantNet network I/O.

`ACCOUNT_ENCRYPTION_KEY_FILE` is not a push-only secret. The account service
purpose-separates it to derive both private email lookup fingerprints and the
deterministic refresh-token successor that can be returned again after a lost
refresh response; future push support would also use it for push-token
encryption and fingerprints. A consumed refresh and
its matching `rotation_id` remain eligible for that idempotent retry for seven
days. The current service loads one key, not an old/new key ring: replacing the
key during that window prevents reproduction of the stored successor and the
retry is treated as token reuse, revoking the session. It also makes existing
password credentials unreachable through their email until their fingerprints
are migrated. Production rotation must therefore add and test an overlap-capable
key/credential migration first, or be a communicated session revocation plus
password-credential recovery event. Preserve the old key for rollback until
that plan completes;
never assume polling-only 1.9 makes this key safe to regenerate.

## Prohibited handling

- Do not put usable provider, refresh, pending-signup, reauthentication, FCM,
  internal, database, or backup values in committed environment files.
  The examples contain visibly non-secret, non-runnable render placeholders
  only.
- Do not pass a token in a URL, query string, metric label, exception, support
  ticket, screenshot, or test fixture copied from a real account.
- Do not mount a production secret directory into the local or German beta.
- Do not reuse beta signing/encryption/database values in production.
