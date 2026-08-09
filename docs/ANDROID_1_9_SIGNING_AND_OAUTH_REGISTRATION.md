# PlantDiseases 1.9 Signing and Real OAuth Registration

This is the one-time operator procedure for the real Android application. The
account-system test build is not a second app and must never be registered as
an `accountbeta` package.

| Field | Required value |
|---|---|
| Launcher name | `PlantDiseases` |
| Android application ID | `com.plantdiseases.app` |
| Version name | `1.9` |
| Version code | `19` |
| Internal CI build type | `staging` |
| CI artifact | `plantdiseases-1.9-staging-apk` |
| Staging app API | `https://pdis.arbuz.lol/plantdisease-test/` |
| Staging account API | `https://pdis.arbuz.lol/plantdisease-test/id/` |

`staging` changes endpoints only. It deliberately has no application-ID
suffix, version suffix, alternate launcher label, or alternate signing key.
Installing it replaces an installed PlantDiseases 1.8 and preserves that
app's local data only when the signing certificate matches.

## 1. Stop and identify the PlantDiseases 1.8 certificate

Do this before creating any key. Android accepts an update only when its
package and signing identity match the installed app. A new key cannot be made
equivalent to an old key.

If a PlantDiseases 1.8 APK exists, inspect it with the Android SDK's
`apksigner`:

```sh
apksigner verify --print-certs PlantDiseases-1.8.apk
```

Record the `Signer #1 certificate SHA-256 digest` and SHA-1 digest. If the only
copy is installed on a phone, first run `adb shell pm path
com.plantdiseases.app`, pull the reported `base.apk`, and inspect that APK.

Then choose exactly one of these paths:

1. **1.8 was distributed or is installed with data that must survive.** Recover
   the exact keystore that signed it and compare its SHA-256 fingerprint. Do
   not generate a new key.
2. **A store re-signs 1.8.** Use the store's app-signing certificate for
   provider registration. A CI-signed sideload cannot update a store install
   unless its installed signing certificate is the same. Google Play exposes
   app-signing and upload certificates separately; do not confuse them.
3. **1.8 was never permanently signed or distributed.** It is safe to create
   the one permanent PlantDiseases app-signing key in the next section.

The repository contains no APK or keystore from which the existing 1.8
certificate can currently be recovered. That history must therefore be
confirmed by the app owner.

## 2. Create the permanent key once, only if path 3 applies

Create it on a trusted local machine, outside the repository. Let `keytool`
prompt for the passwords so they do not enter shell history:

```sh
keytool -genkeypair -v \
  -keystore plantdiseases-app-signing.p12 \
  -storetype PKCS12 \
  -alias plantdiseases \
  -keyalg RSA \
  -keysize 4096 \
  -sigalg SHA256withRSA \
  -validity 10000
```

Use a strong unique password. Keep the keystore, alias, store password, and key
password in the project's controlled password manager. Store at least two
encrypted backups under different administrative failure domains. Never put
the private keystore in Git, a VDS image, a provider dashboard, a ticket, or
chat. Never rerun the generation command to prepare an ordinary app update.

Exporting the public certificate is safe and does not export the private key:

```sh
keytool -exportcert -rfc \
  -keystore plantdiseases-app-signing.p12 \
  -alias plantdiseases \
  -file plantdiseases-app-signing-certificate.pem
```

Print and record both fingerprints:

```sh
keytool -list -v \
  -keystore plantdiseases-app-signing.p12 \
  -alias plantdiseases
```

The SHA-256 value becomes the protected `android-signing` environment variable
`RELEASE_CERT_SHA256`. The signing job compares every distributable APK against
that pinned value, so an accidental replacement keystore fails the workflow.

## 3. Register Yandex ID

In [Yandex OAuth](https://oauth.yandex.com/):

1. Create an application **for user authorization**, with the user-visible
   name `PlantDiseases` and the real app icon/contact email.
2. Add the **Android application** platform.
3. Set Android package name to exactly `com.plantdiseases.app`.
4. Add the permanent certificate's SHA-256 fingerprint. Do not add
   `com.plantdiseases.app.accountbeta` and do not use a generated debug
   fingerprint for the distributable build.
5. Request only the profile information used by the app. PlantDiseases does
   not request email or phone scope.
6. Save the resulting **Client ID** as `YANDEX_CLIENT_ID`.

If Yandex test and production projects are separated, both Android entries
still use the same real package and permanent signing fingerprint. The
separation is a provider-side environment distinction, not another installed
Android app.

## 4. Register VK ID

In the [VK ID integration cabinet](https://id.vk.ru/business/go):

1. Create the Android integration with the user-visible name
   `PlantDiseases`.
2. Where the cabinet requests Android identity, enter package
   `com.plantdiseases.app` and the permanent certificate fingerprint. Do not
   enter the removed `.accountbeta` package or a disposable debug key.
3. Obtain the numeric application ID (`app_id`) and the mobile protected value
   (`client_secret`). They become `VKID_CLIENT_ID` and
   `VKID_CLIENT_SECRET` respectively.
4. Keep the redirect configuration at the SDK-required values:
   `VKIDRedirectHost=vk.ru` and `VKIDRedirectScheme=vk<app_id>`.
5. Request no optional VK scopes; the app currently calls the SDK with an
   empty scope set and does not need email or phone.

The VK mobile `client_secret` is embedded in the APK as required by the VK ID
Android SDK and is therefore extractable client configuration, not a server
credential. Pass it through a repository Actions variable rather than commit it,
and never accept it as proof of a caller's identity.

## 5. Configure GitHub Actions

Open the repository's **Settings → Secrets and variables → Actions**.

Create these secrets in the required-reviewer `android-signing` environment:

| Secret | Value |
|---|---|
| `RELEASE_KEYSTORE_BASE64` | One-line base64 of the permanent keystore |
| `RELEASE_STORE_PASSWORD` | Permanent keystore password |
| `RELEASE_KEY_ALIAS` | `plantdiseases`, or the recovered key's actual alias |
| `RELEASE_KEY_PASSWORD` | Permanent key password |

Create these environment **variables** (not secrets):

| Variable | Value |
|---|---|
| `RELEASE_CERT_SHA256` | SHA-256 fingerprint of the permanent app certificate |
| `PRODUCTION_RUSSIAN_CUTOVER_APPROVED` | `false` until the reviewed Russian cutover smoke matrix passes |
| `APPROVED_PRODUCTION_APP_API_BASE_URL` | Independently reviewed Russian application HTTPS origin, ending in `/` |
| `APPROVED_PRODUCTION_ACCOUNT_API_BASE_URL` | The same approved origin plus `id/` |

Create these **repository Actions variables**. They are public/extractable
mobile configuration and must be available to the unsigned build before a
reviewer grants access to the permanent signing key:

| Variable | Value |
|---|---|
| `STAGING_YANDEX_CLIENT_ID` / `PRODUCTION_YANDEX_CLIENT_ID` | Separate Yandex Android Client IDs |
| `STAGING_VKID_CLIENT_ID` / `PRODUCTION_VKID_CLIENT_ID` | Separate numeric VK application IDs |
| `STAGING_VKID_CLIENT_SECRET` / `PRODUCTION_VKID_CLIENT_SECRET` | Separate VK mobile values |
| `STAGING_APP_API_BASE_URL` | Exact reviewed German staging application URL: `https://pdis.arbuz.lol/plantdisease-test/` |
| `STAGING_ACCOUNT_API_BASE_URL` | Exact reviewed German staging account URL: `https://pdis.arbuz.lol/plantdisease-test/id/` |
| `PRODUCTION_APP_API_BASE_URL` | Reviewed production application HTTPS base URL |
| `PRODUCTION_ACCOUNT_API_BASE_URL` | Reviewed production account HTTPS base URL |

Set each repository production URL to exactly the corresponding protected
`APPROVED_PRODUCTION_*` environment value. The unsigned build uses the
repository values, while the checkout-free signing job independently refuses
to sign unless the artifact provenance matches the protected copies. Both
production and `recovery-production` reject the German staging prefix, `.invalid`
or localhost names, every IP literal, non-443 ports, and application paths below
the origin root. Staging and `recovery-staging` require the two explicit
`STAGING_*` URL variables and reject any value other than the reviewed German
pair. This keeps their endpoint-derived encrypted stores and revocation queues
on the same issuer.

All four signing channels must be dispatched from protected `main`. Configure
the `android-signing` environment deployment-branch rule for `main` only; do
not allow `account-beta-test` or wildcard branches. Protect `main` with required
pull-request review and CI, and disallow force pushes and deletion; changes to
this workflow require the same release-security review as signing code.
Environment values take precedence over Gradle-property fallbacks for
distribution inputs. The build
records SHA-256 values for the exact Yandex client ID, VK application ID and VK
mobile value found in generated `BuildConfig`; the signing job validates those
hashes in provenance before decoding or using the permanent key. Raw VK values
are not written to provenance or logs.

The workflow fails a production build if either provider application ID, or
the VK mobile value, equals its staging counterpart. This is a security
boundary: because both channels intentionally use the same Android package and
certificate, distinct provider projects prevent a provider bearer issued for
German staging from being accepted by the production account service.

The manual workflow first runs Gradle in an unprotected job with
`ANDROID_UNSIGNED_DISTRIBUTION=true`. That job receives no `RELEASE_*` value,
proves that its APK is unsigned, checks package/version/endpoints/provider mode,
and uploads the APK and provenance. Only then can a reviewer approve the
`android-signing` job. That job downloads the exact artifact by ID, records the
GitHub archive digest, independently checks the APK digest anchored in the
build-job output plus its provenance, and uses fixed runner Android Build Tools
to align, sign, and verify it. It performs no checkout or Gradle invocation.
Its inline shell is still repository-controlled workflow code, so it is trusted
only because every channel runs the workflow from protected `main`; the
required reviewer must inspect that exact workflow SHA before approval.

Archive both account-disabled rollback artifacts before their corresponding
versionCode 19 release: `recovery-staging` for staging, and
`recovery-production` for production. They share the package, permanent
certificate and versionCode 20, but have distinct version names, endpoint hashes
and artifact names. They are not interchangeable. The workflow also blocks
real-provider staging and production while the machine-readable
`deploy/account-beta-test/release-policy-gates.json` records the private contact
or deleted-actor-retention decision as blocked. An approved gate must include
an HTTPS decision reference and UTC approval timestamp. Schema 2 accepts only a
real `mailto:` or credential-free HTTPS contact whose exact `href` is on the
stable `privacy-private-contact` policy link; that anchor also carries
`data-policy-purpose="privacy-and-deletion"`. Tombstone approval selects either
`fixed_days` or `legal_basis`; the `deleted-actor-tombstone-policy` paragraph's
status, mode, day count/legal basis, English disclosure and Russian disclosure
must exactly match the JSON fields. Changing or reusing an
unrelated policy substring cannot clear it. Production
alone has an intentional code-level stop until the cross-store backup epoch and
durable deletion replay journal are implemented and restore-tested; recovery
remains usable.

Create the one-line base64 value locally:

```sh
base64 -w 0 plantdiseases-app-signing.p12 > plantdiseases-keystore.base64
```

On macOS, use `base64 -i plantdiseases-app-signing.p12 -o
plantdiseases-keystore.base64`. Copy the file contents into the GitHub secret,
then remove the temporary plaintext base64 file through the machine's normal
secure file-handling process. Base64 is encoding, not encryption.

Do not send the keystore or its passwords to another person merely so they can
paste them into GitHub. The key owner should enter those four values directly.

## 6. Configure the German staging server

The account server needs the same two public application IDs as the APK:

```properties
YANDEX_CLIENT_ID=<same Yandex Client ID used by the APK>
VKID_CLIENT_ID=<same numeric VK app ID used by the APK>
```

Put them in the protected VDS environment file, not the committed example.
`VKID_CLIENT_SECRET` and the Android signing keystore do not belong on the
account server.

Before an end-to-end phone test, all four staging checks must return through
the exact public origin compiled into the APK:

```sh
curl -fsS https://pdis.arbuz.lol/plantdisease-test/id/health
curl -fsS https://pdis.arbuz.lol/plantdisease-test/id/ready
curl -fsS https://pdis.arbuz.lol/plantdisease-test/api/health
curl -fsS https://pdis.arbuz.lol/plantdisease-test/api/capabilities
```

The VDS runtime currently binds its parallel services on loopback. Public
nginx routing must be reviewed and enabled before these device-facing checks
can pass; changing Android signing does not expose the server by itself.

The real SDK is tested on a local phone or emulator; it does not need to run on
the VDS. The signed app can call the German HTTPS staging service. A completely
local backend is also possible, but it must run `AUTH_PROVIDER_MODE=remote`
with the same provider IDs. For a USB-connected phone, `adb reverse` can map
local ports 18000/18001 to the phone's permitted `127.0.0.1` URLs. The committed
local Compose profile is intentionally synthetic and cannot validate a real
provider token without that explicit remote-mode reconfiguration.

```sh
adb reverse tcp:18000 tcp:18000
adb reverse tcp:18001 tcp:18001
```

## 7. Local real-SDK build, install, and verification

For a local build, keep values outside this repository in the user's private
`~/.gradle/gradle.properties` and restrict that file to the user. Example key
names:

```properties
ACCOUNT_REAL_OAUTH=true
YANDEX_CLIENT_ID=<Yandex Client ID>
VKID_CLIENT_ID=<numeric VK app ID>
VKID_CLIENT_SECRET=<VK mobile protected value>
APP_API_BASE_URL=https://pdis.arbuz.lol/plantdisease-test/
ACCOUNT_API_BASE_URL=https://pdis.arbuz.lol/plantdisease-test/id/
RELEASE_STORE_FILE=/absolute/path/to/plantdiseases-app-signing.p12
RELEASE_STORE_PASSWORD=<store password>
RELEASE_KEY_ALIAS=plantdiseases
RELEASE_KEY_PASSWORD=<key password>
```

Build from `android/`:

```sh
./gradlew :app:testStagingUnitTest :app:assembleStaging
```

Verify the actual APK, not just the keystore:

```sh
apksigner verify --print-certs app/build/outputs/apk/staging/app-staging.apk
apkanalyzer manifest application-id app/build/outputs/apk/staging/app-staging.apk
apkanalyzer manifest version-name app/build/outputs/apk/staging/app-staging.apk
apkanalyzer manifest version-code app/build/outputs/apk/staging/app-staging.apk
```

The package must be `com.plantdiseases.app`, the certificate SHA-256 must equal
`RELEASE_CERT_SHA256`, and the version must be 1.9/19. Install as an update:

```sh
adb install -r app/build/outputs/apk/staging/app-staging.apk
```

If ADB reports `INSTALL_FAILED_UPDATE_INCOMPATIBLE`, stop. That normally means
the installed 1.8 certificate differs. Do not uninstall the old app merely to
hide the mismatch; uninstalling removes its app-local data and avoids, rather
than proves, the required update path.

## 8. What to provide for the implementation pass

After registration, provide only:

- confirmation whether 1.8 has an existing permanent signing identity;
- the old APK's and selected keystore's SHA-256 fingerprints, which are public;
- the separate staging/production Yandex Client IDs;
- the separate staging/production numeric VK application IDs;
- confirmation that all provider mobile values, the exact reviewed staging
  endpoint pair, and both reviewed production base URLs were placed in
  repository Actions variables;
- confirmation that all four `RELEASE_*` secrets plus `RELEASE_CERT_SHA256`,
  `PRODUCTION_RUSSIAN_CUTOVER_APPROVED`, and both `APPROVED_PRODUCTION_*` URL
  variables were placed in the protected environment;
- confirmation that the reviewed production app/account base URLs point to
  Russian ingress and that the cutover gate stays false until the smoke matrix
  passes;
- confirmation that both channel-matched recovery artifacts and their
  provenance were archived before either versionCode 19 artifact was distributed;
- confirmation that all four provider dashboards show package
  `com.plantdiseases.app` and the selected permanent certificate;
- whether the test should use the German public staging URL or a local
  account-server URL.

Do not provide the private keystore or either keystore password in chat. With
the public IDs and the configuration confirmations, the remaining repository
and protected VDS wiring can be completed and the real Yandex/VK login matrix
can be run from the signed PlantDiseases 1.9 APK.

## Official references

- [Android app signing and update identity](https://developer.android.com/studio/publish/app-signing)
- [Yandex authorization-app registration](https://yandex.com/dev/id/doc/en/register-auth)
- [Yandex ID Android SDK setup](https://yandex.com/dev/id/doc/en/mobileauthsdk/android/3.1.3/sdk-android)
- [VK ID Android SDK](https://github.com/VKCOM/vkid-android-sdk)
- [GitHub Actions secrets](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets)
