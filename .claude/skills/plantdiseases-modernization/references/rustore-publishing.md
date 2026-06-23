# Publishing to RuStore

RuStore is the Russian app store (VK). It's a good fit here: the app is already EN/RU and the
audience is Russian. This is a release/process task — no app-code rewrite required. Most
Google-Play-targeted Android apps publish to RuStore unchanged.

> Verify current requirements at rustore.ru/help — store policies and the console UI change.
> Treat this as a checklist, not gospel.

## Account & legal prerequisites
- A **RuStore developer account** (console.rustore.ru). Registration supports a **legal entity,
  sole proprietor (ИП), or self-employed (самозанятый)/individual** — individual/self-employed
  is the lightest path for a student project. You'll verify identity (Gosuslugi/ЕСИА is used).
- A **privacy policy URL is mandatory** — the app uploads user **photos** and (optionally)
  location for the community feed, so this is non-negotiable. Host a short EN/RU policy
  (covers: photos sent to the diagnosis server, optional community sharing, FCM token, any
  analytics). Link it in the listing.

## The build artifact
- RuStore accepts **AAB** (preferred) or **APK**. You already produce a **signed**
  `assembleRelease` (real keystore via `-PRELEASE_STORE_FILE=…`, else debug-key fallback) —
  use a **real release keystore** for the store upload, not the debug fallback.
  - AAB: `./gradlew :app:bundleRelease -PRELEASE_STORE_FILE=… -PRELEASE_STORE_PASSWORD=… -PRELEASE_KEY_ALIAS=… -PRELEASE_KEY_PASSWORD=…`
  - Keep the keystore + passwords safe; RuStore (like Play) ties updates to the signing key.
- `versionCode` must increase per upload (currently 13). Bump for each store submission.
- Confirm `minSdk 26` / `targetSdk 35` meet RuStore's current minimum target-API policy
  (they enforce a recent targetSdk floor, like Play — verify; you're current, so fine).

## Push / Google Play services caveat (important for this app)
Many RuStore-installed devices **lack Google Play services** (Huawei, deliberately de-Googled
phones). On those, **FCM won't deliver** — which is exactly why the app's existing
**FCM → `OpinionCheckWorker` WorkManager-poll fallback** matters. Confirm before release:
- The app **does not crash** when Google Play services is absent (it already inits Firebase
  manually from `BuildConfig.FCM_*` and no-ops when unconfigured — keep that path working).
- Second-opinion replies still arrive via the hourly WorkManager poll without push.
- *(Optional, later)* add the **RuStore Push SDK** (VK's push) for real-time delivery on
  Google-less devices. Not required for a first release — the poll covers correctness.

## Store listing (bilingual — you already have the assets)
- Title, short + full description in **RU** (primary) and EN. Reuse `values-ru` tone.
- Screenshots (phone), an icon, a feature graphic. Show the diagnosis/result screen — the ML
  is the selling point.
- Category, age rating / content questionnaire, contact email.
- Data-safety / permissions rationale: camera, photos, (optional) location, notifications,
  internet.

## Submission flow
1. Create the app in the console, fill the listing (RU + EN), attach the privacy policy URL.
2. Upload the signed AAB/APK.
3. Complete content rating + data questionnaire.
4. Submit for **moderation** (review takes from hours to a few days). Address any rejections
   (common: missing privacy policy, permission justifications, broken sign-in on test devices).
5. Publish.

## Optional RuStore SDKs (only if you need them — skip for v1)
- **In-app updates**, **billing** (if you ever monetize), **review prompt**, **push**. Each is
  a separate Gradle dependency + init. Don't add for the first ship; they expand the surface
  area and review scope.

## Definition of done (first release)
- [ ] Developer account verified; privacy policy (EN/RU) hosted and linked.
- [ ] Signed **release** AAB built with the real keystore; `versionCode` bumped.
- [ ] Verified the app launches + core diagnosis flow works on a **device without Google Play
      services** (or at least doesn't crash; push degrades to poll).
- [ ] Listing complete in RU + EN with screenshots; content rating done.
- [ ] Submitted to moderation; rejections (if any) resolved.
