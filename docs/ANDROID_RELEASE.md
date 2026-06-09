# Android release: signing & secrets

The Android CI job (`.github/workflows/ci.yml`) runs lint + unit tests and
assembles the **R8-minified release APK** on every push that touches
`android/**`. The build works with **no** secrets configured (it falls back to
the debug signing key and leaves push messaging dormant). Add the secrets below
to ship an APK signed with your own key and to enable Firebase Cloud Messaging.

All secrets go in **GitHub → repo → Settings → Secrets and variables → Actions
→ "New repository secret"** (`https://github.com/pmixay/plantdiseases/settings/secrets/actions`).

---

## 1. Release signing

### 1.1 Generate an upload keystore

Run this on your machine (needs the JDK's `keytool`, included with Temurin/any
JDK 17). Pick strong passwords and **keep the file and passwords safe** — if you
lose this keystore you can no longer publish updates to the same app on Google
Play.

```bash
keytool -genkeypair -v \
  -keystore plantdiseases-release.jks \
  -storetype PKCS12 \
  -alias plantdiseases \
  -keyalg RSA -keysize 2048 \
  -validity 10000
```

`keytool` prompts for a keystore password and your name/org (the certificate
"distinguished name"). With PKCS12 the key password equals the store password —
use the same value for both secrets below. Note the values you choose:

| Prompt / choice            | Goes into secret          |
| -------------------------- | ------------------------- |
| keystore password          | `RELEASE_STORE_PASSWORD`  |
| `-alias` (here `plantdiseases`) | `RELEASE_KEY_ALIAS`  |
| key password (= store pw)  | `RELEASE_KEY_PASSWORD`    |

### 1.2 Base64-encode the keystore for the secret

GitHub secrets are text, so the binary `.jks` is stored base64-encoded. CI
decodes it back to a file before building.

```bash
# Linux
base64 -w0 plantdiseases-release.jks > keystore.b64

# macOS
base64 -i plantdiseases-release.jks -o keystore.b64
```

Copy the contents of `keystore.b64` into the secret `RELEASE_KEYSTORE_BASE64`.

### 1.3 Secrets to create

| Secret                     | Value                                            |
| -------------------------- | ------------------------------------------------ |
| `RELEASE_KEYSTORE_BASE64`  | contents of `keystore.b64`                       |
| `RELEASE_STORE_PASSWORD`   | the keystore password                            |
| `RELEASE_KEY_ALIAS`        | the alias (e.g. `plantdiseases`)                 |
| `RELEASE_KEY_PASSWORD`     | the key password (= store password for PKCS12)   |

Once all four are set, the next Android CI run decodes the keystore to
`$RUNNER_TEMP/release.keystore` and assembles a **release-signed** APK
(artifact `app-release-apk`). If they're absent the build falls back to the
debug key — still installable for sideload testing, but not for Play upload.

> **Tip:** don't paste these into PRs/logs, and never commit the `.jks` or
> `keystore.b64`. Delete `keystore.b64` after copying it into the secret.

### 1.4 Building a signed release locally

Pass the same values as Gradle properties (`-P…`) or environment variables:

```bash
cd android
./gradlew :app:assembleRelease \
  -PRELEASE_STORE_FILE=/absolute/path/to/plantdiseases-release.jks \
  -PRELEASE_STORE_PASSWORD=*** \
  -PRELEASE_KEY_ALIAS=plantdiseases \
  -PRELEASE_KEY_PASSWORD=***
```

Or keep them out of your shell history by putting them in
`~/.gradle/gradle.properties` (outside the repo).

---

## 2. Firebase Cloud Messaging (FCM)

### 2.1 What these values actually are

The four `FCM_*` values are Firebase **client** configuration — App ID, API
key, project ID and sender ID — the same identifiers normally found in
`google-services.json`. They are **shipped inside every APK** and can be
extracted from any installed build, so per Google they are *not* secrets:
<https://firebase.google.com/docs/projects/api-keys>. The credential that can
actually *send* pushes is the server's Firebase **service-account JSON**
(`FCM_CREDENTIALS_FILE` in `server/push.py`), which is loaded from a file path
on the server and is **not** in this repo.

They were previously hard-coded in `android/app/build.gradle.kts`. We've removed
those defaults and now inject them from secrets so they're no longer in the
source tree. Two caveats worth knowing:

- This does **not** erase them from git history or from any APK already built
  with the old defaults.
- Because they were public, the right hardening is to **restrict the Android
  API key** in Google Cloud Console (APIs & Services → Credentials → that key →
  *Application restrictions: Android apps* with your package name + SHA-1, and
  *API restrictions* limited to the Firebase/FCM APIs you use). Consider
  rotating the key if you want a clean break.

### 2.2 Secrets to create

Read the values from your Firebase project's `google-services.json`
(`client[].client_info.mobilesdk_app_id`, `api_key[].current_key`,
`project_info.project_id`, `project_info.project_number`):

| Secret           | Source field in google-services.json            |
| ---------------- | ----------------------------------------------- |
| `FCM_APP_ID`     | `mobilesdk_app_id` (e.g. `1:123…:android:abc…`) |
| `FCM_API_KEY`    | `api_key[0].current_key` (`AIza…`)              |
| `FCM_PROJECT_ID` | `project_id`                                    |
| `FCM_SENDER_ID`  | `project_number`                                |

If you leave these unset the app still builds; push messaging just stays
dormant and the client falls back to its WorkManager poll
(`PushManager.isConfigured()` returns `false`).

For local builds, pass them like the signing props
(`-PFCM_APP_ID=… -PFCM_API_KEY=…`) or via `~/.gradle/gradle.properties`.

---

## 3. How CI consumes the secrets

In the `android` job the secrets are exposed as environment variables. The
keystore is decoded only when `RELEASE_KEYSTORE_BASE64` is set, and a single
Gradle invocation runs everything:

```bash
./gradlew lintDebug testDebugUnitTest assembleRelease --build-cache --stacktrace
```

`app/build.gradle.kts` reads each value from a Gradle property **or** the
same-named environment variable, so no value is committed to the repo.
