# Account System Staging Environment and Secret Inventory

No secret value belongs in Git, an image layer, an APK, a command line, a log,
or this document. Android OAuth application identifiers—and the VK mobile
client value commonly named a client secret—are extractable public client
configuration and are never accepted as proof of identity.

## Local and German account-system staging

| Value | Classification | Owner/storage | Consumer | Rotation |
|---|---|---|---|---|
| `BETA_ACCOUNT_IMAGE`, `BETA_APP_IMAGE` | Public immutable image references | Mode-0600 operator env file | Compose | Every tested release |
| `BETA_POSTGRES_ADMIN_PASSWORD` | Staging bootstrap secret | Mode-0600 operator env file | PostgreSQL initialization/idempotent role upgrade only | Preserve for an existing beta volume; rotate deliberately |
| `BETA_POSTGRES_MIGRATOR_PASSWORD` | Staging DDL/ownership secret | Mode-0600 operator env file | One-shot Alembic migration and post-migration grant convergence | Recreate with disposable stack |
| `BETA_POSTGRES_RUNTIME_PASSWORD` | Staging application-DML secret | Mode-0600 operator env file | Account API; SELECT-only on `alembic_version`, no sequence access | Recreate with disposable stack |
| `BETA_POSTGRES_BACKUP_PASSWORD` | Staging read-only secret | Mode-0600 operator env file | Encrypted backup check | Recreate with disposable stack |
| `YANDEX_CLIENT_ID`, `VKID_CLIENT_ID` | Public provider application IDs registered for `com.plantdiseases.app` | Mode-0600 operator env plus protected Android build config | Account API/Android | Change only with provider registration |
| Android `VKID_CLIENT_SECRET` | Extractable public mobile config | Protected Android/CI build config, never server env | VK ID SDK | Change only with provider registration |
| `BETA_MODELS_DIR` | Non-secret absolute model path | Host-only operator env | App API read-only mount | Verify at each model release |
| `signing.json` | Beta Ed25519 private key ring | Dedicated `*_account_keys` volume, mode 0600 | Account API | Exercise before handoff; never promote |
| `encryption.key` | Beta AES-256 account encryption/derivation key | Same beta-only key volume, mode 0600 | Account API refresh rotation (and future push-token protection) | Recreate only with the disposable stack; existing sessions must reauthenticate |
| `internal.token` | Beta service credential | Dedicated `*_internal_token` volume, mode 0640; read-only in app | Account and app APIs | Recreate with disposable stack |
| `BETA_FORWARDED_ALLOW_IPS`, `BETA_TRUSTED_PROXIES` | Security configuration, not secret | Mode-0600 operator env file | Uvicorn/application API | Verify whenever Docker networking changes |

Generate the migrator, runtime, and backup database passwords with
`openssl rand -hex 32`. Their 64-hex format is validated before migrations so
raw URL interpolation cannot reinterpret `@`, `:`, `/`, `%`, or `#`. On an
existing German beta volume, keep the original `beta_account` password as
`BETA_POSTGRES_ADMIN_PASSWORD`; it is used only by the idempotent role-upgrade
job and never by an API.

The German environment has dedicated Yandex/VK application values. Provider
tokens pass once through memory for verification and are never persisted or
logged. It has no FCM credentials, PlantNet/OpenRouter keys, production account
keys, or production backup credential. Key generation has no network and
refuses to overwrite an existing key file.

## Protected Android signing

`.github/workflows/ci.yml` is compilation/test evidence only and must never
receive the permanent keystore or live provider configuration. Distributable
APKs are built only by the manually dispatched `android-release.yml` workflow.
Its first job runs reviewed Gradle without an environment or signing secrets,
forces an unsigned output, verifies it, and uploads the exact APK plus
provenance. The required-reviewer `android-signing` job downloads that artifact
by ID and signs it without checkout, Gradle, or repository file execution.
Repository administrators must configure that environment with required
reviewers, prevent self-review, and restrict deployment branches to `main` and
`account-beta-test`. A private repository is not a substitute for this
boundary: repository-controlled Gradle code can read every secret deliberately
supplied to its build step.

Store only `RELEASE_KEYSTORE_BASE64`, `RELEASE_STORE_PASSWORD`,
`RELEASE_KEY_ALIAS`, and `RELEASE_KEY_PASSWORD` as protected environment
secrets. Store the public `RELEASE_CERT_SHA256`, the cutover gate
`PRODUCTION_RUSSIAN_CUTOVER_APPROVED`, and the independently reviewed
`APPROVED_PRODUCTION_APP_API_BASE_URL` /
`APPROVED_PRODUCTION_ACCOUNT_API_BASE_URL` as variables in that environment.

Store separate `STAGING_{YANDEX_CLIENT_ID,VKID_CLIENT_ID,VKID_CLIENT_SECRET}`
and `PRODUCTION_{YANDEX_CLIENT_ID,VKID_CLIENT_ID,VKID_CLIENT_SECRET}` values as
repository Actions variables. These mobile values are extractable from an APK;
keeping them outside the protected environment lets the credential-free build
finish before a reviewer grants key access. Staging and production provider
projects must still be different even though both register the mandatory same
package/certificate. The protected production workflow requires all three
staging values to be present and refuses equal staging/production Yandex client
IDs, VK application IDs, or VK mobile values. Define `PRODUCTION_APP_API_BASE_URL`
and `PRODUCTION_ACCOUNT_API_BASE_URL` as repository variables used by the
credential-free build. Put the exact independently reviewed copies under the
`APPROVED_PRODUCTION_*` names in the protected environment. The checkout-free
signing job refuses a mismatch, the German staging prefix, `.invalid` or
localhost names, IP literals, non-443 ports, and non-root application paths.
Keep the cutover gate false until both URLs terminate at the reviewed Russian ingress.
The workflow never falls back from missing production provider values to
staging values, requires successful CI for the exact commit, pins each
third-party action, records GitHub's artifact digest, and independently verifies
the build-job-anchored APK digest, provenance, certificate, package/version,
endpoints, and provider mode.

Also define `STAGING_APP_API_BASE_URL` and `STAGING_ACCOUNT_API_BASE_URL` as
repository variables containing the exact reviewed German staging pair. Both
the normal staging artifact and `recovery-staging` consume these values; the
workflow rejects missing or changed values. Production and staging recovery
artifacts are distinct because endpoint-derived encrypted stores and queued
revocations must stay with their original account issuer.

FCM values are deliberately absent: version 1.9 uses polling until a durable
push worker is implemented and reviewed.

## Final Russian data plane

| Value/file | Classification | Required owner/location | Consumer | Rotation/recovery rule |
|---|---|---|---|---|
| `YANDEX_CLIENT_ID` | Public application ID | Protected host configuration | Account API and Android build | Change only with provider registration |
| `VKID_CLIENT_ID` | Public application ID | Protected host configuration | Account API and Android build | Change only with provider registration |
| Android `VKID_CLIENT_SECRET` | Extractable public mobile config | CI/build configuration | Android VK ID SDK | Never use as server authentication |
| `signing.json` | Critical private Ed25519 key ring | Russia, account UID-readable mode 0400 | Account API | Keep active plus reviewed retired public keys; perform overlap exercise |
| `encryption.key` | Critical AES-256 account encryption/derivation key | Russia, account-owned mode 0400 | Account API refresh rotation; future push-token protection remains disabled | Do not replace in place; use a reviewed overlap migration or planned session revocation and reauthentication |
| `internal.token` | Critical service credential | Separate account/app-owned mode-0400 copies in Russia | Private account/app calls | Rotate atomically across both services |
| `RU_POSTGRES_ADMIN_PASSWORD` | Bootstrap-only database secret | Root-managed mode-0600 host environment/secret manager in Russia | PostgreSQL initialization/idempotent role provision only | Never mount into migrator, API, app, or backup |
| `RU_POSTGRES_MIGRATOR_PASSWORD` | DDL/ownership secret | Root-managed mode-0600 host environment/secret manager in Russia | One-shot Alembic migration and post-migration grant convergence | Rotate after reviewed migrations when practical |
| `RU_POSTGRES_RUNTIME_PASSWORD` | Restricted application-DML secret | Root-managed mode-0600 host environment/secret manager in Russia | Account API; SELECT-only on `alembic_version`, no sequence access | Rotate and test reconnect/rollback |
| `RU_POSTGRES_BACKUP_PASSWORD` | Read-only database secret | Root-managed mode-0600 host environment/secret manager in Russia | Encrypted backup job | Rotate with restore test |
| Future `fcm.json` (absent in 1.9) | External service credential | App/push-owned mode-0400 files in Russia | A separately implemented and reviewed push worker only | Provider policy plus incident rotation |
| ML worker CA/client cert/key | Cross-border service credential | App-owned mode-0400 files in Russia | Russian app API | Short validity; overlap before replacement |
| German worker CA/server cert/key | Cross-border service credential | Worker-owned mode-0400 files in Germany | Stateless worker | Short validity; revoke on source change |
| Backup encryption recipient/key | Critical recovery secret | Separate Russian-controlled location | Backup/restore job | Test restore before rotation; escrow separately |
| TLS private keys | Critical ingress secret | Respective host secret store | Russian ingress/German private worker | Normal certificate lifecycle |

Account-service startup checks every configured account secret is an absolute
regular file. Signing and encryption keys permit no group/world access. The
beta internal token may be group-readable, but never group-writable or
world-accessible, because it lives alone in a dedicated volume mounted
read-only into the differently-UID app container. The final Russian template
instead expects owner-specific mode-0400 copies whose byte values match; never
print them for comparison.
Russian migrator/runtime/backup database passwords use the same validated
64-hex format; the bootstrap password is transported through `PGPASSWORD`, not
embedded in a URL.

`ACCOUNT_ENCRYPTION_KEY_FILE` is not a push-only secret. The account service
purpose-separates it to derive the deterministic refresh-token successor that
can be returned again after a lost refresh response; future push support would
also use it for push-token encryption and fingerprints. A consumed refresh and
its matching `rotation_id` remain eligible for that idempotent retry for seven
days. The current service loads one key, not an old/new key ring: replacing the
key during that window prevents reproduction of the stored successor and the
retry is treated as token reuse, revoking the session. Production rotation must
therefore either add and test an overlap-capable key migration first or be a
communicated, planned session revocation with mandatory account
reauthentication. Preserve the old key for rollback until that plan completes;
never assume polling-only 1.9 makes this key safe to regenerate.

## Prohibited handling

- Do not put usable provider, refresh, pending-signup, reauthentication, FCM,
  internal, database, mTLS, or backup values in committed environment files.
  The examples contain visibly non-secret, non-runnable render placeholders
  only.
- Do not pass a token in a URL, query string, metric label, exception, support
  ticket, screenshot, or test fixture copied from a real account.
- Do not mount a production secret directory into the local or German beta.
- Do not reuse beta signing/encryption/database values in Russia.
- Do not retain a decrypted backup or provider response snapshot in Germany.
