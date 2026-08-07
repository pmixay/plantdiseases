# Account Beta Final Implementation and Test Guide

Status: implementation guide for the `account-beta-test` branch. It explains
what is implemented and how to validate it. The German beta is a restricted
real-provider test deployment, not public production traffic or a merge to
`main`.

The infrastructure transfer and day-two operations procedure is in
[RU_DE_MAIN_TRANSFER_AND_OPERATIONS.md](RU_DE_MAIN_TRANSFER_AND_OPERATIONS.md).

## What was implemented

- The independent `account-server` implements Yandex ID and VK ID exchange,
  nickname completion, sessions, refresh-token rotation, provider linking,
  reauthentication, push registration, and account deletion.
- Android uses the official Yandex LoginSDK and VK ID SDK. Provider access
  tokens are sent once to account-api and are not persisted by the app.
- `accountBeta` is independently installable as
  `com.plantdiseases.app.accountbeta`; the existing production package remains
  `com.plantdiseases.app`.
- Synthetic authentication remains the default for local hermetic testing.
  `ACCOUNT_BETA_REAL_OAUTH=true` selects the real SDK callbacks for the German
  beta only when both provider configurations and explicit HTTPS API URLs are
  present.
- AndroidX Lifecycle is pinned to `2.10.0`. Its AAR requires compile SDK 35 and
  AGP 8.6, which is compatible with this project's compile SDK 36 and AGP
  8.13.2. Lifecycle 2.11 must not be restored until the project performs a
  reviewed API 37/AGP 9 migration.
- CI always compiles the real-OAuth BuildConfig path with inert values. When
  the three provider build values and stable signing key are configured as
  repository secrets, the branch additionally publishes
  `account-beta-real-oauth-apk` pointed at the parallel German endpoints.

Implementation entry points:

- Android configuration: `android/app/build.gradle.kts`
- Android provider callbacks: `android/app/src/main/java/com/plantdiseases/app/ui/account/AccountActivity.kt`
- Remote provider verification: `account-server/app/providers/remote.py`
- Account API contract: `account-server/openapi.json`
- Test/deployment stack: `deploy/account-beta-test/`

## 1. Required CI result

Push the branch and require all applicable jobs in `.github/workflows/ci.yml`
to pass:

- **Android build & lint**: ktlint, detekt, production tests, account-beta tests,
  release APK, and account-beta APK;
- **Account API tests & lint**: Ruff, pytest, Compose/template rendering, and
  shell syntax;
- **Server tests & lint**: Ruff and pytest.

Do not distribute an APK from a failed or cancelled run. With provider and
signing secrets configured, use only the `account-beta-real-oauth-apk`
artifact; otherwise CI labels the fallback `account-beta-synthetic-apk`.

## 2. Test synthetic login first

The synthetic test proves the account, nickname, session, community, and
deletion contracts without contacting Yandex or VK.

From the repository root:

```sh
cp deploy/account-beta-test/env.example deploy/account-beta-test/.env
docker compose --env-file deploy/account-beta-test/.env \
  -f deploy/account-beta-test/compose.local.yml config --quiet
docker compose --env-file deploy/account-beta-test/.env \
  -f deploy/account-beta-test/compose.local.yml up -d
python deploy/account-beta-test/check_integration.py \
  --account-url http://127.0.0.1:18001 \
  --app-url http://127.0.0.1:18000 \
  --image server/static/guide/healthy.png
```

Build from `android/` for an emulator:

```sh
./gradlew :app:testAccountBetaUnitTest :app:assembleAccountBeta \
  -PAPP_API_BASE_URL=http://10.0.2.2:18000/ \
  -PACCOUNT_API_BASE_URL=http://10.0.2.2:18001/
```

Expected result: the app shows the synthetic-auth notice, both provider
buttons create deterministic test identities, and production remains
unmodified. Stop the test stack with ordinary `docker compose ... down`; do
not add `--volumes` unless disposable data deletion was separately approved.

## 3. Prepare real Yandex/VK testing

Real login requires all of the following before building the APK:

1. The isolated German beta is reachable at
   `https://pdis.arbuz.lol/plantdisease-test/`; local Compose remains mock-only.
2. Account-api runs with `AUTH_PROVIDER_MODE=remote`, the matching
   `YANDEX_CLIENT_ID` and `VKID_CLIENT_ID`, PostgreSQL readiness, persistent
   signing/encryption keys, and the private app-service credential.
3. Yandex and VK applications are registered for the exact Android package and
   signing certificate:
   - beta: `com.plantdiseases.app.accountbeta`;
   - eventual production: `com.plantdiseases.app` (separate registration and
     release-key fingerprint).
4. The APK is signed by the certificate registered with each provider. Use
   `./gradlew :app:signingReport` to obtain the fingerprint; a CI-generated
   disposable debug key is not a stable provider registration.
5. The Android client IDs and VK mobile client value are available to the
   build. They are extractable from an APK but still must not be committed or
   printed in logs.

Put build values in the developer's private
`~/.gradle/gradle.properties`, not in this repository:

```properties
ACCOUNT_BETA_REAL_OAUTH=true
YANDEX_CLIENT_ID=replace-with-beta-yandex-client-id
VKID_CLIENT_ID=12345
VKID_CLIENT_SECRET=replace-with-vk-mobile-client-value
APP_API_BASE_URL=https://pdis.arbuz.lol/plantdisease-test/
ACCOUNT_API_BASE_URL=https://pdis.arbuz.lol/plantdisease-test/id/
```

The server receives the two client IDs but never uses the Android
`VKID_CLIENT_SECRET` as server authentication.

## 4. Prove the real server is ready

Before installing the APK, verify through the same public host the APK will
use:

```sh
curl -fsS https://pdis.arbuz.lol/plantdisease-test/id/health
curl -fsS https://pdis.arbuz.lol/plantdisease-test/id/ready
curl -fsS https://pdis.arbuz.lol/plantdisease-test/api/health
curl -fsS https://pdis.arbuz.lol/plantdisease-test/api/capabilities
```

Account health must report `provider_mode` as `remote`. Readiness must report
both `yandex` and `vk` plus a working database. This is only a readiness check;
the first real-token smoke test is still required.

Render the German branch deployment before any start or update:

```sh
docker compose --env-file /protected/account-beta-german.env \
  -f deploy/account-beta-test/compose.german-vds.yml \
  -f deploy/account-beta-test/compose.german-vds.build.yml config --quiet
```

The beta application API must report the same PlantVision model family/version
as main. Its branch code, account PostgreSQL, community data, caches, logs, and
media remain isolated; only the model directory is shared read-only.

## 5. Build and run the real beta APK

From `android/`:

```sh
./gradlew :app:testAccountBetaUnitTest :app:assembleAccountBeta
```

Install the APK under `app/build/outputs/apk/accountBeta/` using Android Studio
or ADB. Confirm the installed application ID is
`com.plantdiseases.app.accountbeta` and that the synthetic-auth notice is not
visible.

Use a dedicated test account and execute this minimum matrix:

| Test | Expected result |
|---|---|
| First Yandex login, then cancel nickname | Returns to Guest; no account session |
| First Yandex login, complete nickname | Account and current generic session appear |
| Logout, then Yandex login again | Existing account opens without nickname creation |
| Link VK after Yandex reauthentication | Both providers appear; no account merge |
| Restart app | Refresh-backed session restores without provider-token storage |
| Change nickname | New nickname and refreshed authorization take effect |
| Community read/write | Guest reads work; mutations require the account |
| Revoke another session | Revoked session can no longer refresh |
| Delete the dedicated test account | Sessions/push revoke and private deletion completes |

Never use a valuable personal account for the destructive deletion check.
Inspect logs after the run and confirm they contain no provider token, refresh
token, nickname, provider profile, FCM value, or image body.

## 6. Common failures

| Symptom | Check |
|---|---|
| Lifecycle AAR asks for API 37/AGP 9.1 | Confirm every resolved Lifecycle artifact is `2.10.0`; do not suppress AAR metadata checks |
| Synthetic notice is still visible | `ACCOUNT_BETA_REAL_OAUTH` was absent/false or a different APK was installed |
| Provider button says unconfigured | Client values were not available during that APK build |
| Yandex/VK application mismatch | Client ID, package ID, redirect scheme, or signing fingerprint differs between APK, provider console, and server |
| Account API returns invalid provider token | Server is in mock mode, token expired, or server client ID does not match the issuing application |
| OAuth succeeds but API is 404 | `ACCOUNT_API_BASE_URL` or ingress `/id/` routing/root path is wrong |
| Account readiness is 503 | PostgreSQL, migration, key files, or provider configuration is not ready |
| German beta rejects the token | Confirm account health says `provider_mode: remote` and both server client IDs match the APK/provider applications |

## Authoritative supporting documents

- Detailed implementation/handoff: [ACCOUNT_BETA_HANDOFF.md](ACCOUNT_BETA_HANDOFF.md)
- Acceptance evidence: [ACCOUNT_BETA_TEST_REPORT.md](ACCOUNT_BETA_TEST_REPORT.md)
- Schema: [ACCOUNT_BETA_SCHEMA.md](ACCOUNT_BETA_SCHEMA.md)
- Secrets: [../deploy/account-beta-test/SECRETS.md](../deploy/account-beta-test/SECRETS.md)
- Rollback: [../deploy/account-beta-test/ROLLBACK.md](../deploy/account-beta-test/ROLLBACK.md)
