# Account System Staging Implementation and Test Guide

Status: internal staging guide for the `account-beta-test` branch. It explains
what is implemented and how to validate it without producing a publishable
artifact. The current RuStore procedure is
[ANDROID_1_9_SIGNING_AND_OAUTH_REGISTRATION.md](ANDROID_1_9_SIGNING_AND_OAUTH_REGISTRATION.md).
The German environment is a restricted test deployment, not public production
traffic or a merge to `main`.

The staging Android artifact is not named Account Beta and is not a second
package or version. It remains PlantDiseases 1.9 (`com.plantdiseases.app`), but
branch CI no longer receives the permanent keystore or publishes a signed
staging artifact. Read
[ANDROID_1_9_SIGNING_AND_OAUTH_REGISTRATION.md](ANDROID_1_9_SIGNING_AND_OAUTH_REGISTRATION.md)
before creating or registering any key.

The infrastructure transfer and day-two operations procedure is in
[RU_DE_MAIN_TRANSFER_AND_OPERATIONS.md](RU_DE_MAIN_TRANSFER_AND_OPERATIONS.md).

## What was implemented

- The independent `account-server` implements Yandex ID and VK ID exchange,
  nickname completion, sessions, refresh-token rotation, provider linking,
  reauthentication, push registration, and account deletion.
- Android uses the official Yandex LoginSDK and VK ID SDK. Provider access
  tokens are sent once to account-api and are not persisted by the app.
- The internal `staging` build type keeps package `com.plantdiseases.app`, app
  label `PlantDiseases`, version name `1.9`, and version code `19`. With the
  existing permanent certificate it updates 1.8 rather than coexisting.
- Synthetic authentication remains the default for local hermetic testing.
  `ACCOUNT_REAL_OAUTH=true` selects the real SDK callbacks for German staging
  when at least one provider and both explicit HTTPS API URLs are present.
  Yandex is pinned for 1.9; VK remains independently disabled until configured.
- AndroidX Lifecycle is pinned to `2.10.0`. Its AAR requires compile SDK 35 and
  AGP 8.6, which is compatible with this project's compile SDK 36 and AGP
  8.13.2. Lifecycle 2.11 must not be restored until the project performs a
  reviewed API 37/AGP 9 migration.
- CI always compiles the Yandex-only real-OAuth BuildConfig path. Ordinary CI
  receives no signing key and publishes no distributable artifact; permanent
  signing is isolated in the protected, manually dispatched Android workflow.

Implementation entry points:

- Android configuration: `android/app/build.gradle.kts`
- Android provider callbacks: `android/app/src/main/java/com/plantdiseases/app/ui/account/AccountActivity.kt`
- Remote provider verification: `account-server/app/providers/remote.py`
- Account API contract: `account-server/openapi.json`
- Test/deployment stack: `deploy/account-beta-test/`

## 1. Required CI result

Push the branch and require all applicable jobs in `.github/workflows/ci.yml`
to pass:

- **Android build & lint**: ktlint, detekt, production tests, staging tests and
  non-distributable release/staging compilation;
- **Account API tests & lint**: Ruff, pytest, Compose/template rendering, and
  shell syntax;
- **Server tests & lint**: Ruff and pytest.

Do not distribute an APK from ordinary CI. After every applicable job passes,
promote the reviewed SHA to protected `main`, then manually dispatch
`.github/workflows/android-release.yml` for `staging` from `main`. Inspect the
exact commit and its unsigned artifact/provenance, then have a different
authorized reviewer approve the protected `android-signing` environment. The
build job never receives the permanent key; the signing job has no checkout and
does not run Gradle. Its inline signing shell is nevertheless repository code,
so every channel must dispatch from protected `main` and the reviewer must
inspect that exact workflow SHA. Use only that workflow's
checksum/provenance-carrying signed artifact.

Before the first versionCode 19 staging distribution, dispatch
`recovery-staging` from reviewed, protected `main` and archive its
versionCode 20 artifact. Before the first production distribution, dispatch
`recovery-production` from reviewed `main` and archive that distinct versionCode
20 artifact. Both use the normal package/certificate and disable account UI,
but staging recovery is bound to the exact reviewed `STAGING_*` endpoint pair
while production recovery consumes the reviewed `PRODUCTION_*` pair. The
checkout-free signing job requires the production repository values to equal
the protected `APPROVED_PRODUCTION_*` values exactly. Never substitute one
recovery artifact for the other: endpoint-scoped encrypted state and queued
revocations must stay with their original issuer. These are the data-preserving
rollback paths because Android cannot downgrade versionCode 19 to 18. If either
recovery is used, the next mandatory account release must use versionCode 21 or
higher.

The workflow intentionally refuses real-provider staging or production while
`deploy/account-beta-test/release-policy-gates.json` marks the private contact
or deleted-actor-retention decision as blocked. Approved metadata must match a
schema-2 contact link and bilingual tombstone marker in `privacy.html` exactly,
including a real `mailto:`/HTTPS contact, the selected retention mode, decision
reference and UTC approval time. Prose substring changes cannot clear the gate.
Production is additionally blocked while the handoff records the
cross-store backup epoch/deletion replay journal as deferred. Recovery channels
remain available during those holds.

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
./gradlew :app:testStagingUnitTest :app:assembleStaging \
  -PAPP_API_BASE_URL=http://10.0.2.2:18000/ \
  -PACCOUNT_API_BASE_URL=http://10.0.2.2:18001/
```

Expected result: the app shows the synthetic-auth notice, both provider
buttons create deterministic test identities, and production remains
unmodified. Stop the test stack with ordinary `docker compose ... down`; do
not add `--volumes` unless disposable data deletion was separately approved.

## 3. Prepare real Yandex testing

Real login requires all of the following before building the APK:

1. The isolated German beta is reachable at
   `https://pdis.arbuz.lol/plantdisease-test/`; local Compose remains mock-only.
2. Account-api runs with `AUTH_PROVIDER_MODE=remote`, the matching
   `YANDEX_CLIENT_ID`, PostgreSQL readiness, persistent signing/encryption
   keys, and the private app-service credential. VK remains unconfigured for
   this release.
3. The Yandex application is registered for Android package
   `com.plantdiseases.app` and the one permanent app-signing certificate. There
   is no `.accountbeta` provider registration.
4. The APK is signed by the certificate registered with each provider. Use
   `./gradlew :app:signingReport` to obtain the fingerprint; a CI-generated
   disposable debug key is not a stable provider registration.
5. The public Yandex Android client ID pinned in `app/build.gradle.kts`
   matches the provider registration. A Yandex client secret is neither
   required nor accepted by the build.

The public beta build values are committed in `android/gradle.properties`:

```properties
ACCOUNT_REAL_OAUTH=true
STAGING_APP_API_BASE_URL=https://pdis.arbuz.lol/plantdisease-test/
STAGING_ACCOUNT_API_BASE_URL=https://pdis.arbuz.lol/plantdisease-test/id/
```

Consequently, a fresh checkout needs no OAuth or endpoint flags for
`assembleDebug`, `assembleStaging`, or a local `assembleRelease`: all three
select the live German beta pair and real Yandex flow. `ACCOUNT_REAL_OAUTH=false`
is now an explicit local-mock opt-out. Signing credentials remain private in
`~/.gradle/gradle.properties`; when the permanent release key is configured,
debug builds use it as well so Android Studio's Run action matches the
certificate registered with Yandex.

The server receives the public Yandex client ID through its protected host
environment. If VK is added later, its mobile client value is used only by the
Android SDK and never as server authentication.

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
`yandex`, must not report `vk`, and must show a working database. This is only
a readiness check; the first real-token smoke test is still required.

Render the German branch deployment before any start or update:

```sh
docker compose --env-file /protected/account-beta-german.env \
  -f deploy/account-beta-test/compose.german-vds.yml \
  -f deploy/account-beta-test/compose.german-vds.build.yml config --quiet
```

The beta application API must report the same PlantVision model family/version
as main. Its branch code, account PostgreSQL, community data, caches, logs, and
media remain isolated; only the model directory is shared read-only.

## 5. Build and run the real PlantDiseases 1.9 APK

Dispatch the protected Android signing workflow with channel `production` for
the RuStore candidate (`staging` remains limited to named pre-release testers). Review
the exact unsigned artifact/provenance before approving the checkout-free
signing job. Install its accepted APK using Android Studio or `adb install -r`.
Confirm the installed application ID is `com.plantdiseases.app`, the installed
version is 1.9/19, its certificate is the pinned permanent certificate, and the
synthetic-auth notice is not visible. A signature mismatch over 1.8 is a stop
condition, not a reason to uninstall and lose local app data.

Use a dedicated test account and execute this minimum matrix:

| Test | Expected result |
|---|---|
| First Yandex login, then cancel nickname | Returns to Guest; no account session |
| First Yandex login, complete nickname | The app reuses one durable completion UUID, confirms the provisional bundle, then the active account/current generic session appear |
| Drop the first completion response, then retry | Exact inputs recover the same provisional account/session; a different completion UUID, installation, or nickname fails closed |
| Cancel after provisional completion | Provisional account/session are removed and the app returns to Guest |
| Logout, then Yandex login again | Existing account opens without nickname creation |
| Inspect VK option | Disabled and visibly says “coming soon” |
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
| Synthetic notice is still visible | `ACCOUNT_REAL_OAUTH=false` explicitly overrode the committed default, or a different APK was installed |
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
