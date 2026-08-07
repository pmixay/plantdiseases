# Account Beta Environment and Secret Inventory

No secret value belongs in Git, an image layer, an APK, a command line, a log,
or this document. Android OAuth application identifiers—and the VK mobile
client value commonly named a client secret—are extractable public client
configuration and are never accepted as proof of identity.

## Synthetic local and German beta

| Value | Classification | Owner/storage | Consumer | Rotation |
|---|---|---|---|---|
| `BETA_ACCOUNT_IMAGE`, `BETA_APP_IMAGE` | Public immutable image references | Mode-0600 operator env file | Compose | Every tested release |
| `BETA_POSTGRES_PASSWORD` | Synthetic staging secret | Mode-0600 operator env file | PostgreSQL/account API | Recreate with disposable stack |
| `signing.json` | Beta Ed25519 private key ring | Dedicated `*_account_keys` volume, mode 0600 | Account API | Exercise before handoff; never promote |
| `encryption.key` | Beta AES-256 push key | Same beta-only key volume, mode 0600 | Account API | Recreate with disposable stack |
| `internal.token` | Beta service credential | Same beta-only key volume, mode 0600 | Account and app APIs | Recreate with disposable stack |
| `BETA_FORWARDED_ALLOW_IPS`, `BETA_TRUSTED_PROXIES` | Security configuration, not secret | Mode-0600 operator env file | Uvicorn/application API | Verify whenever Docker networking changes |

The German environment has no Yandex/VK application values, real provider
tokens, FCM credentials, PlantNet/OpenRouter keys, production signing key, or
production backup credential. Key generation has no network and refuses to
overwrite an existing key file.

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

Account-service startup checks all configured account secret files are absolute,
regular files with no group/world permission bits. Because the account and app
containers use different non-root UIDs, duplicate `internal.token` into two
owner-specific mode-0400 directories rather than weakening file permissions.
The byte values must match; the copies must never be printed for comparison.

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
