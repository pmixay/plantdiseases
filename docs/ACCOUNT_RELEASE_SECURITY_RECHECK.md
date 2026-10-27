# PlantDiseases 1.9 Account Release Security Recheck

> Historical 2026-08-19 release snapshot. It predates email/password beta and
> PlantDiseases PRO. Current implementation and release gates are defined by
> [ACCOUNT_BETA_FINAL_IMPLEMENTATION_GUIDE.md](ACCOUNT_BETA_FINAL_IMPLEMENTATION_GUIDE.md),
> [EMAIL_PASSWORD_AUTH_RELEASE_BLOCKERS.md](EMAIL_PASSWORD_AUTH_RELEASE_BLOCKERS.md),
> and [PLANTDISEASES_PRO_RELEASE_OPERATIONS.md](PLANTDISEASES_PRO_RELEASE_OPERATIONS.md).

**Checked:** 2026-08-19 (revalidated after the Community V2/account merge)
**Decision:** **HOLD FOR RUSTORE PUBLICATION — the merged code and local release
artifact pass, but production account routing, a public moderation contact, the
permanent signing identity, provider-dashboard proof, and a permanently signed
end-to-end RuStore artifact are not ready yet.**

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
| Yandex portrait permission | `login:avatar` | Provider-dashboard proof required |
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
  the subject. The private provider-photo feature uses the disclosed
  `login:avatar` permission; sign-in requests no optional email, phone,
  birth-date, gender, or name data, and the standard login remains the private
  display fallback.
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

## Follow-up pass — 2026-09-16 (post-avatar-editor commit)

Reviewed `7c938e6` (account redesign + avatar editor) and re-swept the account
trust boundary. The Android side of that commit introduced no new external
attack surface: `CropActivity` stays `exported="false"`, the picked image is
bounded, decoded, EXIF-stripped and re-encoded by `UploadImageSanitizer` before
anything becomes durable, the stored portrait lives in `noBackupFilesDir` keyed
by a SHA-256 of the account's community actor ID (so one account can never read
another's local picture), and account deletion clears it. Four defects and
hardening gaps were found elsewhere on the boundary and are fixed.

### Fixed in this pass

- **Unauthenticated 500 on both internal-service auth checks.**
  `secrets.compare_digest` raises `TypeError` on non-ASCII `str` operands, and
  Starlette decodes header bytes before the check sees them. A single high byte
  in `X-Internal-Service-Token` therefore escaped the comparison as an
  unhandled exception: a bare 500 plus a server traceback, from an
  unauthenticated caller, on `POST /v1/internal/auth/verify`,
  `POST /v1/internal/notifications/opinion` (account API) and
  `POST /api/internal/account-deletions` / `account-nicknames` (app API — these
  are deliberately exempt from the API-key gate, so they are reachable without
  any credential). No authentication bypass: the comparison never returned
  true. Both checks now reject a non-ASCII candidate before comparing, which is
  sound because both loaders read the credential with `encoding="ascii"`.
  Regression tests assert a stable 401 on every affected route.

- **Unhandled exceptions bypassed the account API's response contract.**
  Anything not converted by an existing handler fell through to Starlette's
  plain-text 500, which also skips the middleware that sets `Cache-Control:
  no-store`, `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` and
  the per-hop `X-Request-ID`. The middleware now converts an unexpected failure
  into the same redacted `{"error": {"code": "service_unavailable"}}` envelope
  used elsewhere, keeps the headers, and logs the exception *type name* only —
  never its message or repr, which can carry provider, refresh, pending-signup
  or FCM values.

- **A VK portrait URL could name any HTTPS host.** The Yandex path builds its
  avatar URL from an opaque `default_avatar_id` and is fixed to
  `avatars.yandex.net` by construction, but VK returns a whole URL that was
  accepted on nothing but scheme. That value reaches `/v1/me` and is handed to
  the phone's image loader, so a provider-side response (or anything able to
  forge one) could learn every signed-in user's IP address. VK portraits are
  now pinned to VK's own photo infrastructure (`userapi.com`, `vk.com`,
  `vk.ru`, `vk-cdn.net`, `vkuser.net`, `vkuserphoto.ru`, and subdomains).
  Anything else degrades to "no portrait", which the app already renders as its
  placeholder. This closes before VK is enabled rather than after.

- **Access-token lifetime was taken on the issuer's word.** Both verifiers
  required `exp > iat` but never bounded `exp - iat`, so a token signed by a
  trusted key but minted with a far-future expiry would have been honoured for
  as long as it claimed. Both `TokenService.decode_access` and the app API's
  `RemoteAccountAuthenticator._verify_local` now enforce the published
  15-minute `ACCESS_TOKEN_TTL` independently, so the revocation window stays
  the only variable a session's reach depends on.

### Reviewed and found sound (no change)

Two-stage token validation (local Ed25519 verify, then live introspection with
an equality check on the whole principal, and a JWKS outage failing 503 rather
than 401); refresh-token rotation with reuse detection, family revocation and
HMAC-derived idempotent retries; reauthentication scoping for deletion and
linking; per-account authorization-version invalidation; single-script
nickname validation (blocks homoglyph, RTL and zero-width injection);
advisory-lock serialization of first-time provider-subject signups; SQL —
every dynamic fragment in `server/` interpolates only fixed literal table
names, with values always bound; path handling on `/api/community/thumb/` and
the Wikimedia photo proxy; Android credential storage (AES-GCM under a
non-exportable Keystore key, with the normalized issuer as authenticated
additional data); and redirect refusal on every credential-bearing request
across both servers and the app.

### Open, not fixed here

- **VK access-token → application binding is unverified.** Yandex is safe by
  cross-check: its `/info` response carries `client_id` and the adapter refuses
  a mismatch, so a token issued to a *different* Yandex application cannot be
  replayed into PlantDiseases. The VK adapter passes `client_id` as a query
  parameter to `id.vk.ru/oauth2/user_info` and trusts VK to reject a token that
  belongs to another application — the response contains no field to
  cross-check. If VK does not enforce that binding, anyone operating any VK
  application could sign in here as any of *their* users. This must be proven
  empirically before VK is enabled: obtain an access token from a second,
  unrelated VK application and confirm `POST /v1/auth/vk/exchange` returns
  `invalid_provider_token` or `provider_application_mismatch` rather than
  authenticating. If VK does not bind it, move VK verification to the `id_token`
  (JWT) flow and check its `aud` claim against `VKID_CLIENT_ID`.

- **`VKIDClientSecret` is embedded in the APK.** The VK ID Android SDK requires
  it as a manifest placeholder, so it is extractable from any published build
  and must be treated as public. Never reuse that VK application for a
  server-side confidential flow, and rotate it only through VK's dashboard.

- **`RedirectUriReceiverActivity` uses a `vk<client-id>` custom scheme.** A
  custom scheme is claimable by any other app on the device. VK ID mitigates
  with PKCE and a signature check; confirm on a real device that a second app
  registering the same scheme cannot complete the flow.

- **Android `FileProvider` maps the whole cache root** (`<cache-path
  name="share_images" path="/">`), which now also covers the avatar working
  files. Not exploitable today — grants are per-URI, no prefix grant is used,
  and no provider path is built from untrusted input — but the tree should be
  narrowed to the directories actually shared when the share and avatar
  temporary files are next touched.

- **Account-service push delivery is not wired.** `create_app` never passes
  `notification_sink`, so `PUT /v1/push/device` always answers
  `503 push_unavailable` and `/ready` reports `push_delivery: disabled`. This
  fails closed (no unusable FCM credentials accumulate), but account-bound
  opinion notifications cannot be delivered until the FCM sink is connected.

- **`IDEMPOTENT_REFRESH_RETRY_GRACE` is 7 days.** A consumed refresh token
  still returns its successor for a week when replayed with the matching
  client-chosen `rotation_id`. That is far longer than a lost-response retry
  needs; consider minutes.

### Verification

- Account server: Ruff clean; **217 passed, 3 skipped** (was 206/3).
- Main server: Ruff clean; **642 passed** (was 635).
- Each fix was confirmed by reverting the source change and watching its new
  test fail, then restoring it and watching it pass.
- Android was not compiled locally per repository policy; `ktlintCheck`,
  `detekt`, unit tests and R8 `assembleRelease` run in CI. This pass changed no
  Android source.

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

1. **Deploy the account origin.** Resolved for beta on 2026-08-18: staging
   `/plantdisease-test/id/health`, `/id/ready`, `/api/health`, and
   `/api/capabilities` return 200, the account service reports remote-provider
   mode, and readiness advertises Yandex only. Production `/id/health` and
   `/id/ready` remain a separate undeployed origin and correctly return 404;
   zero-configuration local debug/release/staging builds use the complete beta
   pair instead. A production artifact must still wait for a separate
   production account/app deployment rather than aliasing beta health checks.
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
