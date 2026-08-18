# PlantDiseases 1.9 Account Release Security Recheck

**Checked:** 2026-08-16
**Decision:** **HOLD — code-side release controls pass, but live account routing,
a public moderation contact, the permanent signing identity, provider-dashboard
proof, and a signed end-to-end RuStore artifact are not ready yet.**

This review covers the one existing Android application only. It does not
create another package or version.

## Frozen release contract

| Property | Required release value | Verified locally |
|---|---|---|
| Package | `com.plantdiseases.app` | Yes |
| Version | `1.9` / `19` | Yes |
| App API | `https://pdis.arbuz.lol/` | Yes |
| Account API | `https://pdis.arbuz.lol/id/` | Yes |
| Account UI | enabled | Yes |
| Synthetic authentication | disabled | Yes |
| Yandex LoginSDK | `com.yandex.android:authsdk:3.2.1` | Yes |
| Yandex provider | enabled independently | Yes |
| VK provider | disabled independently; visible as coming soon | Yes |

The Yandex native Client ID is public/extractable Android configuration and is
pinned in `android/app/build.gradle.kts`. The Yandex client secret is neither
needed nor accepted by the Android build or account server and is not present
in the repository. Because a secret was pasted into chat, rotate it in the
Yandex dashboard before release even though PlantDiseases does not use it.

## Security controls verified or remediated

- Release account traffic is HTTPS-only, trusts system roots only, forbids
  Android backup/device transfer, and keeps private activities non-exported.
- The merged release manifest enables the Yandex SDK callback components and
  embeds the expected Yandex Client ID. All discovered VK auth, redirect, and
  captcha components are disabled when VK is unconfigured.
- Provider access tokens are used once for verification, are never persisted,
  and are sent to fixed provider HTTPS origins in authorization headers or
  POST bodies. Provider, refresh, application bearer, pending-signup, and
  reauthentication credentials are never followed across HTTP redirects on
  either Android or the servers.
- The account refresh token is AES-GCM encrypted with a non-exportable Android
  Keystore key. Application access tokens remain in memory. Server refresh
  tokens are stored as hashes; push tokens are encrypted.
- Yandex token verification checks that the provider response's `client_id`
  equals the configured application ID and uses the app-specific `psuid` as
  the subject. Sign-in needs no optional email, phone, birth-date, gender,
  name, or avatar scope; the standard login is the private display fallback.
- Account signup, reauthentication, provider linking, deletion, session
  revocation, authorization-version checks, bounded provider responses,
  rate-limiting, generic error responses, and durable cross-service deletion
  were covered by the account test suite.
- Direct API uploads are now decoded, EXIF-oriented, bounded, and rebuilt as a
  new metadata-free RGB JPEG before PlantNet. Raw camera EXIF/GPS is never used
  as a third-party fallback.
- CI uses read-only repository permissions, disables checkout credential
  persistence, pins every external action to a full commit, and validates the
  Gradle wrapper. Branch-facing CI is secret-free; permanent signing and
  optional FCM values are available only to a main-only `rustore-release`
  environment job, which removes its temporary keystore immediately and pins
  the final APK certificate by SHA-256.
- Python runtime requirements passed vulnerability auditing. The known
  vulnerable `cryptography` version was raised to `50.0.0`. A one-time OSV
  scan of 292 resolved Android runtime coordinates and 159 Gradle build
  coordinates returned no known vulnerabilities.
- Community scans and individual replies now expose Report and Block-author
  actions. Safety mutations accept only opaque content tokens, resolve the
  stored author server-side, require a signed account, reject self-targeting,
  use a fixed report taxonomy, enforce per-account caps, and make blocks mutual
  for signed-in feed visibility and interaction. Block management, a local-only
  moderation queue, destructive confirmation, account-deletion cleanup,
  bilingual content criteria, and contact UI are present; the live contact
  destination remains a release gate below.
- The local privacy policy now discloses accounts, Yandex, PlantNet,
  OpenRouter, conditional FCM, community reports/blocks, retention, deletion,
  and opt-in model-training capture. It also carries a restrictive document
  CSP and no-referrer policy and makes no third-party font/subresource requests.
- Main-branch CI now fails before permanent signing unless the app and account
  health checks work, the ready account service exposes Yandex but not VK, the
  updated privacy policy and all required response headers are live, and the
  moderation-contact URL is publicly reachable.
- After assembly, CI now inspects the packaged APK rather than trusting only
  Gradle intermediates: exact package/version/SDK levels, non-debuggable and
  no-backup/no-cleartext application flags, Yandex callback components/client
  ID/SDK bytecode, disabled VK components, exactly one pinned signer, and an
  APK SHA-256 handoff file must all pass before artifact upload.

## Verification evidence

- Main server: Ruff clean; **605/605 tests passed**.
- Account server: Ruff clean; **161 passed, 1 intentional skip**.
- Android: ktlint and detekt clean; the release source compiled and **56/56**
  focused release tests passed: **1** release identity assertion, **4** account/
  safety presentation tests, **6** account transport tests, and **45**
  repository network/security tests.
- Bandit medium/high scan: account application, server trust-boundary files,
  community storage/safety code, and the local moderator tool pass. Dynamic
  SQLite query fragments use fixed predicates, allowlisted identifiers and
  bound values; reviewed B608 sites are documented in code. The public API's
  intentional container-network bind is explicitly documented; ingress and
  firewall policy own exposure.
- `pip-audit`: no known vulnerabilities in either runtime requirements file.
- Gitleaks 8.30.1, redacted scan: no real secret in the working tree. Six
  matches are URL/configuration-symbol false positives. Full history also
  contains one old Android Firebase/GCP API key; if that Firebase project is
  still used, restrict the key by package/certificate and API, then rotate it.
- Generated release `BuildConfig`: exact package/version and production URLs;
  account UI and Yandex true; synthetic auth and VK false.
- Generated release manifest: backup and cleartext disabled; Yandex components
  enabled; VK auth/redirect/captcha components disabled.
- CI workflow YAML has unique keys, its live and final-artifact shell gates
  parse, and community safety code is included in the continuing Bandit gate.

No full local release APK was assembled during this review: repository policy
assigns R8 packaging to CI, and the permanent release key is not available in
the workspace. Existing local staging/account-beta APKs are debug-signed and
are not publishable evidence. The existing staging APK also predates the final
VK-component hardening and is correctly rejected by the new packaged-manifest
check. The local machine has no Docker CLI, so the four Compose rendering
commands remain an explicit CI gate; the corresponding handoff/configuration
contract tests did pass in the account suite.

## Blocking release gates

1. **Deploy the account origin.** On 2026-08-16 production `/api/health` and
   `/api/capabilities` returned 200, but production `/id/health` and
   `/id/ready`, plus staging `/plantdisease-test/id/health`, `/id/ready`,
   `/api/health`, and `/api/capabilities`, returned 404. Until nginx routing
   and the account service are live in remote-provider mode, Yandex sign-in
   cannot complete after LoginSDK returns a token.
2. **Recover and prove the permanent RuStore signing identity.** The supplied
   certificate is `CN=Android Debug`. Existing local APKs match it, but no
   PlantDiseases 1.8/RuStore APK or permanent keystore is present. Compare the
   certificate of the currently published/installed 1.8 APK with the selected
   permanent key. Do not register or ship the debug key as production merely
   because it updates a local test install.
3. **Finish Yandex dashboard registration.** The Android platform entry must
   contain package `com.plantdiseases.app` and the SHA-256 fingerprint of the
   proven permanent release certificate. Confirm its Client ID is the one
   pinned in the project. VK needs no action for this release.
4. **Configure protected release signing.** Create the `rustore-release`
   GitHub environment, restrict it to protected `main`, require reviewer
   approval, and place the four `RELEASE_*` secrets plus public
   `RELEASE_CERT_SHA256` variable only there. Remove repository-wide copies so
   automation-branch workflows cannot request the signing material.
5. **Deploy privacy/headers and a public moderation contact.** The hosted page
   is still the 18 July 2026 copy and does not contain the new account/external
   service disclosures. The live nginx response also lacks HSTS; the static
   privacy response lacks CSP/referrer/frame/nosniff headers. In addition, the
   configured GitHub issue tracker returns 404 to a public client, so it is not
   valid end-user contact information. Deploy the local page and headers, then
   either make that tracker publicly reachable or replace it with an operator-
   approved support address. Main CI now enforces all of these checks before
   signing.
6. **Run the final signed-device matrix.** From the permanently signed CI APK:
   install/update over 1.8 without data loss; sign in with Yandex; complete and
   cancel signup; restart and refresh; logout; reauthenticate and delete;
   confirm VK is disabled and says “coming soon”; confirm no provider token is
   logged; and provide RuStore moderators a working test path. Archive the APK
   digest, certificate output, CI run, and results.

The repository-side RuStore UGC mechanics are closed: reporting, signed-in
mutual blocking/unblocking, rules, and an operator removal workflow are
implemented and covered by server and release-presentation tests. The contact
portion remains open because its current public destination is dead. The
release must remain on hold until all six external/final-artifact gates have
evidence. Passing repository tests alone cannot substitute for a reachable
support channel, provider dashboard, signing, deployment, and physical-device
verification.

## Official references

- [Yandex LoginSDK for mobile applications](https://yandex.ru/dev/id/doc/ru/mobileauthsdk/about)
- [Yandex authorization-app registration](https://yandex.ru/dev/id/doc/ru/register-auth)
- [Yandex login-button constructor (web visual reference)](https://yandex.ru/dev/id/doc/ru/suggest/but-const)
- [Yandex user-information response](https://yandex.ru/dev/id/doc/ru/user-information)
- [RuStore application requirements](https://www.rustore.ru/help/developers/publishing-and-verifying-apps/requirement-apps)
- [RuStore signature verification](https://www.rustore.ru/help/guides/check-sign)
