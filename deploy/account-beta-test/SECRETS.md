# Account System Staging Environment and Secret Inventory

No secret value belongs in Git, an image layer, an APK, a command line, a log,
or this document. Android OAuth application identifiers—and the VK mobile
client value commonly named a client secret—are extractable public client
configuration and are never accepted as proof of identity.

## Local and German account-system staging

| Value | Classification | Owner/storage | Consumer | Rotation |
|---|---|---|---|---|
| `BETA_ACCOUNT_IMAGE`, `BETA_APP_IMAGE` | Public immutable image references | Mode-0600 operator env file | Compose | Every tested release |
| `BETA_POSTGRES_PASSWORD` | Staging secret | Mode-0600 operator env file | PostgreSQL/account API | Recreate with disposable stack |
| `YANDEX_CLIENT_ID`, `VKID_CLIENT_ID` | Public provider application IDs registered for `com.plantdiseases.app` | Mode-0600 operator env plus protected Android build config | Account API/Android | Change only with provider registration |
| Android `VKID_CLIENT_SECRET` | Extractable public mobile config | Protected Android/CI build config, never server env | VK ID SDK | Change only with provider registration |
| `BETA_MODELS_DIR` | Non-secret absolute model path | Host-only operator env | App API read-only mount | Verify at each model release |
| `signing.json` | Beta Ed25519 private key ring | Dedicated `*_account_keys` volume, mode 0600 | Account API | Exercise before handoff; never promote |
| `encryption.key` | Beta AES-256 push key | Same beta-only key volume, mode 0600 | Account API | Recreate with disposable stack |
| `internal.token` | Beta service credential | Dedicated `*_internal_token` volume, mode 0640; read-only in app | Account and app APIs | Recreate with disposable stack |
| `BETA_FORWARDED_ALLOW_IPS`, `BETA_TRUSTED_PROXIES` | Security configuration, not secret | Mode-0600 operator env file | Uvicorn/application API | Verify whenever Docker networking changes |

The German environment has dedicated Yandex/VK application values. Provider
tokens pass once through memory for verification and are never persisted or
logged. It has no FCM credentials, PlantNet/OpenRouter keys, production account
keys, or production backup credential. Key generation has no network and
refuses to overwrite an existing key file.

## Final Russian data plane

| Value/file | Classification | Required owner/location | Consumer | Rotation/recovery rule |
|---|---|---|---|---|
| `YANDEX_CLIENT_ID` | Public application ID | Protected host configuration | Account API and Android build | Change only with provider registration |
| `VKID_CLIENT_ID` | Public application ID | Protected host configuration | Account API and Android build | Change only with provider registration |
| Android `VKID_CLIENT_SECRET` | Extractable public mobile config | CI/build configuration | Android VK ID SDK | Never use as server authentication |
| `signing.json` | Critical private Ed25519 key ring | Russia, account UID-readable mode 0400 | Account API | Keep active plus reviewed retired public keys; perform overlap exercise |
| `encryption.key` | Critical AES-256 key | Russia, account/push-owned copies, mode 0400 | Account API/push worker | Rotate with explicit token re-encryption plan |
| `internal.token` | Critical service credential | Separate account/app-owned mode-0400 copies in Russia | Private account/app calls | Rotate atomically across both services |
| `RU_POSTGRES_PASSWORD` | Database secret | Root-managed mode-0600 host environment/secret manager in Russia | PostgreSQL/account API/push worker | Rotate and test reconnect/rollback |
| `fcm.json` | External service credential | App/push-owned mode-0400 files in Russia | Push worker only | Provider policy plus incident rotation |
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
