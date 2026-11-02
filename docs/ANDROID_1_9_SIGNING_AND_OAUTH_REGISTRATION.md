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
| RuStore build | `production` distribution channel, built and signed locally |
| Production app API | `https://pdis.arbuz.lol/` |
| Production account API | `https://pdis.arbuz.lol/id/` |

Only the production-channel APK is a RuStore candidate. The internal
`staging` build changes endpoints only; it deliberately has no application-ID
suffix, version suffix, alternate launcher label, or alternate signing key, so
it is not another app or version. Never upload that staging artifact to
RuStore. This release has no Google Play registration or publication step.

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
   provider registration. A sideload signed with a different key cannot update a store install
   unless its installed signing certificate is the same. For RuStore AAB
   signing, distinguish the final app-signing certificate from the upload
   certificate; provider registration needs the certificate on the APK users
   actually install.
3. **1.8 was never permanently signed or distributed.** It is safe to create
   the one permanent PlantDiseases app-signing key in the next section.

If the recovered certificate subject is `CN=Android Debug`, do not promote
that debug keystore to permanent signing merely because it can update a test
install. Debug keys use tool-managed credentials and are insecure by design.
If 1.8 was only sideloaded for testing and its local data can be discarded,
choose path 3. If that debug-signed build was distributed or its local data
must survive, stop and design an explicit migration before provider
registration; do not register the production provider applications against
the debug certificate as a shortcut.

Git history contains no tracked 1.8 APK or keystore from which the existing
certificate can be recovered. Ignored local build outputs do not establish
what users received. Distribution history must therefore be confirmed by the
app owner.

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

The SHA-256 value is the permanent certificate fingerprint. For this project it
is `ef9563c5db397924c1f81239b9ed5fa1e9b233f034ce10eb75f16efd36a26367`; it must
be registered with Yandex for `com.plantdiseases.app`. Compare every
distributable APK against it (section 7), so an accidental replacement keystore
is caught before distribution.

## 3. Register Yandex ID

In [Yandex OAuth](https://oauth.yandex.com/):

1. Create an application **for user authorization**, with the user-visible
   name `PlantDiseases` and the real app icon/contact email.
2. Add the **Android application** platform.
3. Set Android package name to exactly `com.plantdiseases.app`.
4. Add the permanent certificate's SHA-256 fingerprint. Do not add
   `com.plantdiseases.app.accountbeta` and do not use a generated debug
   fingerprint for the distributable build.
5. This release is for RuStore only. Do not create a Google Play listing or
   register another Android application; if Yandex shows the optional Android
   Google Play URL field, leave it unset.
6. Enable **User's profile picture** (`login:avatar`). Yandex returns
   `default_avatar_id` only when the token grants that permission; the account
   service turns that identifier into Yandex's documented HTTPS avatar URL.
   Do not add optional email, phone, birth-date, or name/gender permissions.
   The picture is used only on the private Account/Profile surfaces and is not
   exposed in community responses. Sign-in itself still works when the user
   has no Yandex picture; the app shows its local placeholder instead.
7. Confirm the resulting **Client ID** matches the public ID pinned in
   `android/app/build.gradle.kts`. The Android build embeds that ID directly;
   it never needs or accepts the Yandex client secret.

After adding `login:avatar` to an existing Yandex application, log out and sign
in again during the release smoke test so Yandex can issue a new token with the
new permission and the account service can refresh the stored provider photo.

If Yandex test and production projects are separated, both Android entries
still use the same real package and permanent signing fingerprint. The
separation is a provider-side environment distinction, not another installed
Android app.

## 4. Register VK ID (optional; disabled for the current release)

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
credential. Supply it through a Gradle property or environment variable rather than commit it,
and never accept it as proof of a caller's identity.
Until VK is registered, leave both `VKID_CLIENT_ID` and `VKID_CLIENT_SECRET`
unset. The Android app shows a disabled **VK ID — coming soon** option, does
not initialize its SDK, and the account server returns `provider_unavailable`
for VK routes. Adding VK later does not require changing the Yandex
configuration.

## 5. Build and sign locally

There is no CI signing, no protected GitHub environment and no `RELEASE_*`
GitHub secret. Ordinary CI (`.github/workflows/ci.yml`) holds no signing or
provider credentials. The owner builds the distributable APK on a trusted
machine with Gradle (section 7) and signs it with the permanent key through the
`RELEASE_*` Gradle properties or same-named environment variables:

| Property / env var | Value |
|---|---|
| `RELEASE_STORE_FILE` | Absolute path of the permanent keystore, outside the repository |
| `RELEASE_STORE_PASSWORD` | Permanent keystore password |
| `RELEASE_KEY_ALIAS` | `plantdiseases`, or the recovered key's actual alias |
| `RELEASE_KEY_PASSWORD` | Permanent key password |

The keystore, its passwords and any base64 copy of it stay on the owner's
machine (and in the controlled password manager and encrypted backups from
section 2). They never go into Git, a GitHub secret or variable, chat, or a
ticket. Keep them in the user's private `~/.gradle/gradle.properties`
(restricted to the user) or in the shell environment of the build only.

Everything else the build needs is public, extractable mobile configuration,
not a secret. The values below are reviewed reference values; Gradle reads each
one from a `-P` property or a same-named environment variable:

| Gradle property / env var | Reference value |
|---|---|
| `PRODUCTION_APP_API_BASE_URL` | `https://pdis.arbuz.lol/` |
| `PRODUCTION_ACCOUNT_API_BASE_URL` | `https://pdis.arbuz.lol/id/` |
| `STAGING_APP_API_BASE_URL` | `https://pdis.arbuz.lol/plantdisease-test/` |
| `STAGING_ACCOUNT_API_BASE_URL` | `https://pdis.arbuz.lol/plantdisease-test/id/` |
| `RUSTORE_CONSOLE_APP_ID` | `2063726125` (RuStore Console application ID) |
| `RUSTORE_PRO_MONTHLY_PRODUCT_ID` | `plantdiseases_pro_monthly` |
| `RUSTORE_PRO_YEARLY_PRODUCT_ID` | `plantdiseases_pro_yearly` |
| `VKID_CLIENT_ID` / `VKID_CLIENT_SECRET` | Optional VK pair; leave both unset for the current release (both or neither) |

Gradle enforces what it can at configuration time. `ANDROID_DISTRIBUTION_CHANNEL`
selects a distribution channel: `staging`, `production`, `recovery-production`
or `recovery-staging`. `production` and `recovery-production` need an explicit
HTTPS production pair (app URL ending in `/`, account URL the same origin plus
`id/`); `staging` and `recovery-staging` require the reviewed staging pair
above. The PRO-enabled channels (`staging`, `production`) refuse to build
unless all three RuStore values are present and both product IDs match the
account-server allowlist; the recovery channels inject no Pay values and remain
account-disabled rollback APKs. Yandex uses the one public native client ID
pinned in `android/app/build.gradle.kts`; the Yandex client secret is never
used. None of these values is the server RSA private key or the notification
AES key.

The two recovery channels share the package, permanent certificate and
versionCode 20 but differ in endpoints and version name; they are not
interchangeable. Keep a copy of the matching recovery APK for each channel
before distributing its versionCode 19 counterpart.

Nothing enforces the privacy-policy or deletion-retention release gates at
build time any more. The owner confirms them manually before distributing
(`docs/ACCOUNTS_PRODUCTION_PLAN.md`). The consistency between the root
`privacy.html` markers and
`deploy/account-beta-test/release-policy-gates.json` is still checked by
`test_deployment_handoff.py::test_privacy_policy_matches_account_release`.

Yandex needs no secret anywhere in this flow. Leave both VK values unset for the
current release; adding both later enables only VK and does not alter Yandex.

## 6. Deploy the production account origin

The account server needs the public ID for each provider enabled in the APK.
For the current Yandex-only release:

```properties
YANDEX_CLIENT_ID=2b439944bb4c4e33b85e20290aca6c7f
```

Put them in the protected VDS environment file, not the committed example.
`VKID_CLIENT_SECRET` and the Android signing keystore do not belong on the
account server.

Before enabling PRO, create the monthly and yearly subscriptions in the
matching RuStore Console application and keep the identifiers exactly aligned
between the Android build values (section 5) and the account server:

```properties
RUSTORE_LIFECYCLE_ENABLED=true
RUSTORE_CHECKOUT_ENABLED=true
RUSTORE_APPLICATION_ID=<same decimal RuStore application ID>
RUSTORE_SUBSCRIPTION_PRODUCTS=plantdiseases_pro_monthly,plantdiseases_pro_yearly
```

The private Public API RSA key and callback AES key are deployed only through
the account-server secret files described in
`docs/PLANTDISEASES_PRO_RELEASE_OPERATIONS.md`; they must never become Android
Gradle properties. The app will not load products, buy,
or restore until a fresh authenticated account response says
`billing_available=true`, so enable the backend verifier before distributing a
Pay-enabled APK.

Before building the RuStore candidate, the production checks must return
through the exact public origins compiled into the release APK:

```sh
curl -fsS https://pdis.arbuz.lol/id/health
curl -fsS https://pdis.arbuz.lol/id/ready
curl -fsS https://pdis.arbuz.lol/api/health
curl -fsS https://pdis.arbuz.lol/api/capabilities
```

The account runtime may bind on loopback behind nginx, but `/id/health`,
`/id/ready`, and `/id/v1/...` must be routed publicly over HTTPS. Changing
Android signing does not expose the server by itself.

The real SDK is tested on a phone or emulator; it does not run on the VDS. An
optional pre-release test can use the same package/version against the German
staging endpoints below, but that APK is an internal test artifact only:

```sh
curl -fsS https://pdis.arbuz.lol/plantdisease-test/id/health
curl -fsS https://pdis.arbuz.lol/plantdisease-test/id/ready
curl -fsS https://pdis.arbuz.lol/plantdisease-test/api/health
curl -fsS https://pdis.arbuz.lol/plantdisease-test/api/capabilities
```

## 7. Local beta build, production override, install, and verification

The repository commits the public, non-secret beta defaults. After fetching,
Android Studio Run and plain `assembleDebug`, `assembleStaging`, or local
`assembleRelease` builds use real Yandex OAuth plus:

```text
https://pdis.arbuz.lol/plantdisease-test/
https://pdis.arbuz.lol/plantdisease-test/id/
```

No `ACCOUNT_REAL_OAUTH` or endpoint arguments are needed. A local release is
therefore an internal beta artifact unless both production URLs are explicitly
overridden. Never upload a beta-default artifact to RuStore.

Keep only signing credentials and any deliberate production override outside
the repository in the user's private `~/.gradle/gradle.properties`, and
restrict that file to the user. Example production values:

```properties
ACCOUNT_REAL_OAUTH=true
ANDROID_DISTRIBUTION_CHANNEL=production
APP_API_BASE_URL=https://pdis.arbuz.lol/
ACCOUNT_API_BASE_URL=https://pdis.arbuz.lol/id/
RUSTORE_CONSOLE_APP_ID=2063726125
RUSTORE_PRO_MONTHLY_PRODUCT_ID=plantdiseases_pro_monthly
RUSTORE_PRO_YEARLY_PRODUCT_ID=plantdiseases_pro_yearly
RELEASE_STORE_FILE=/absolute/path/to/plantdiseases-app-signing.p12
RELEASE_STORE_PASSWORD=<store password>
RELEASE_KEY_ALIAS=plantdiseases
RELEASE_KEY_PASSWORD=<key password>
```

For the zero-configuration beta, build from `android/` with either:

```sh
./gradlew :app:assembleDebug
./gradlew :app:assembleRelease
```

For an actual production artifact, supply the reviewed production values from
the properties above (channel `production`, the production endpoint pair, the
RuStore values and the `RELEASE_*` signing properties) and run the same command
locally. The owner builds and signs; there is no CI signing path.

```sh
./gradlew :app:assembleRelease
```

Verify the actual APK, not just the keystore:

```sh
apksigner verify --print-certs app/build/outputs/apk/release/app-release.apk
apkanalyzer manifest application-id app/build/outputs/apk/release/app-release.apk
apkanalyzer manifest version-name app/build/outputs/apk/release/app-release.apk
apkanalyzer manifest version-code app/build/outputs/apk/release/app-release.apk
```

The package must be `com.plantdiseases.app`, the certificate SHA-256 must equal
`ef9563c5db397924c1f81239b9ed5fa1e9b233f034ce10eb75f16efd36a26367` (section 2),
and the version must be 1.9/19. Also confirm the compiled endpoints are the
production pair, not the beta defaults, and keep the APK's SHA-256
(`sha256sum app-release.apk`) with the release notes as the RuStore handoff
record. These checks are manual; ordinary CI only compiles and tests the
variants and holds no signing key, so it never produces the distributable APK.

Install as an update:

```sh
adb install -r app/build/outputs/apk/release/app-release.apk
```

If ADB reports `INSTALL_FAILED_UPDATE_INCOMPATIBLE`, stop. That normally means
the installed 1.8 certificate differs. Do not uninstall the old app merely to
hide the mismatch; uninstalling removes its app-local data and avoids, rather
than proves, the required update path.

## 8. What to provide for the implementation pass

After registration, provide only:

- confirmation whether 1.8 has an existing permanent signing identity;
- the old APK's and selected keystore's SHA-256 fingerprints, which are public;
- confirmation that the matching public Yandex Client ID is configured on the
  account server (the Android value is already pinned);
- if VK is enabled later, its numeric application ID and confirmation that the
  matching `VKID_CLIENT_SECRET` was supplied only as a local Gradle property or
  environment variable;
- confirmation that the reviewed staging and production endpoint pairs, the
  RuStore Console app ID and the exact monthly/yearly product IDs used for the
  local build match the deployment and the account-server allowlist;
- confirmation that the keystore and its passwords exist only on the owner's
  machine and in encrypted backups, never in Git or GitHub;
- confirmation that production URLs point to Russian ingress and the cutover
  smoke matrix passed;
- confirmation that both endpoint-matched recovery APKs were archived before
  either versionCode 19 APK was distributed;
- confirmation that every enabled provider dashboard shows package
  `com.plantdiseases.app` and the selected permanent certificate;
- a public moderation/support URL or support email approved for end users (the
  currently configured private GitHub issue tracker returns 404 publicly);
- whether the test should use the German public staging URL or a local
  account-server URL.

Do not provide the private keystore or either keystore password in chat. With
the public IDs and the configuration confirmations, the remaining repository
and VDS wiring can be completed and the enabled-provider login matrix
can be run from the signed PlantDiseases 1.9 APK.

## Official references

- [Android app signing and update identity](https://developer.android.com/studio/publish/app-signing)
- [Yandex authorization-app registration](https://yandex.ru/dev/id/doc/ru/register-auth)
- [Yandex ID Android LoginSDK](https://yandex.ru/dev/id/doc/ru/mobileauthsdk/about)
- [VK ID Android SDK](https://github.com/VKCOM/vkid-android-sdk)
- [RuStore Pay SDK for Kotlin/Java](https://www.rustore.ru/help/en/sdk/pay/kotlin-java)
- [RuStore subscription creation](https://www.rustore.ru/help/en/developers/monetization/create-app-subscription)
