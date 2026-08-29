# PlantDiseases PRO — RuStore Release and Operations Runbook

Status: pre-production operator contract. Last source review: 2026-08-29.

This document defines the commercial offer, security boundary, external RuStore
Console work, and evidence required to launch PlantDiseases PRO. It is not
permission to enable real payments. Every unchecked production gate remains a
release blocker.

## 1. Supported payment stack

Use **RuStore Pay SDK**, artifact `ru.rustore.sdk:pay`, through a compatible
RuStore BOM. Do not add or restore `ru.rustore.sdk:billingclient`.

As of this review, RuStore labels Pay SDK **11.1.0** as the current Kotlin/Java
version. Recheck the [current Pay SDK Kotlin/Java
page](https://www.rustore.ru/help/en/sdk/pay/kotlin-java) before each dependency
upgrade, pin the reviewed BOM in source, and let CI test the exact resolved
dependency graph. An operator must not silently change the SDK/BOM while
building a release.

RuStore ended BillingClient support on **2026-08-01** and states that purchases,
including subscriptions, are no longer processed through it after that date.
The [deprecated BillingClient page](https://www.rustore.ru/help/en/sdk/payments)
and [Pay SDK migration guide](https://www.rustore.ru/help/en/sdk/pay/migration)
are the authoritative migration references.

The Pay SDK client is a checkout and catalog surface, not the entitlement
authority. A modified client must never be able to grant itself PRO. The
account service grants access only after server-side RuStore validation.

## 2. Product catalog and launch hypothesis

These identifiers are immutable launch contracts. They must match in RuStore
Console, Android release configuration, and the account-service allowlist.

| Plan | Exact RuStore product ID | Standard period | Launch price hypothesis |
|---|---|---:|---:|
| Monthly | `plantdiseases_pro_monthly` | 1 month | ₽249/month |
| Yearly | `plantdiseases_pro_yearly` | 1 year | ₽1,990/year |

The prices above are an initial affordability/profitability hypothesis, not an
in-app constant or a promise that can override RuStore. The yearly hypothesis
is about ₽166/month effective and about 33% below twelve monthly payments.
The Android offer must render the current price/currency label returned by Pay
SDK and gracefully hide checkout if either configured product is absent. The
server persists V5-validated price and currency for restricted
profitability telemetry, but never uses a hardcoded price to decide validity.

Recommended Console settings at launch:

- `plantdiseases_pro_monthly`: one **7-day free period**, then the standard
  monthly charge;
- `plantdiseases_pro_yearly`: no second free period; the annual discount is its
  launch incentive;
- both products: no separate reduced-price introductory period;
- both products: **7-day grace**, followed by **7-day hold**.

Keeping the free period on one SKU avoids giving one person independent free
periods by switching products. If product/legal later approves a free period
on both SKUs, it must first prove that RuStore and the account service enforce
one lifetime PlantDiseases PRO free period across the two product IDs.

During grace, PRO stays active while RuStore retries the charge daily. During
hold, the account is downgraded to Free while charge retries continue. If all
retries fail, the subscription closes. RuStore permits grace and hold periods
up to 14 days and applies grace before hold when both are configured; see
[subscription creation and period
behavior](https://www.rustore.ru/help/en/developers/monetization/create-app-subscription).

RuStore says a person can receive a configured free/reduced-price period only
once for that subscription and that changes to period settings do not alter
already-purchased subscriptions. Record every Console change with UTC time,
operator, old/new value, and affected cohort.

## 3. Free and PRO product contract

Daily counters are server-authoritative. Launch defaults use UTC calendar-day
buckets; clients display the authoritative `remaining` and `reset_at` values
rather than calculating them locally. A retry with the same idempotency/request
ID must not consume another unit.

| Capability | Guest / Free launch default | PRO launch default |
|---|---:|---:|
| Aqua on-device diagnosis | Unlimited | Unlimited |
| Gamma cloud analysis | 5/day | 30/day |
| Plant identification | 1/day | 3/day |
| AI Doctor | 3/day | 15/day |
| Generated care plan | 5/day | 20/day |
| Frontier cloud analysis | Not available | 3/day, only while the server advertises the model as available |
| Current moon phase/basic preview | Visible | Visible |
| Full lunar calendar | Blurred in place with a lock/PRO explanation | Unlocked |
| Garden-wide lunar recommendations | Blurred in place with a lock/PRO explanation | Unlocked |
| Core camera, garden records, reminders, and local history | Available | Available |

These are useful but commercially conservative fair-use launch defaults, not permanent contractual
minimums. Keep them server-configurable and version every change. Do not reduce
limits for an active cohort without product/legal review and clear in-app
notice. Do not market Frontier as guaranteed: it remains conditional on the
server capability response.

There is no separate launch limit on taking a photo or saving a local garden
record. A photo sent to a cost-bearing cloud operation consumes that operation's
quota only when the backend accepts the operation. Aqua remains useful offline
and unlimited, so a Free user is never left without the core diagnosis path.
On a first-time Gamma/Frontier response, the optional automatic Pl@ntNet
species fan-out consumes one unit from the same Plant identification allowance
as the dedicated identify screen. A cached diagnosis consumes no additional
identification unit or vendor call. If that shared allowance is exhausted, the
disease diagnosis still completes and only its optional species block is
unavailable. This prevents the two UI surfaces from silently multiplying the
published vendor-cost ceiling.

Quota exhaustion is not an authentication failure. Return a stable reason,
remaining count, and reset time; keep the locked/blurred content in context and
offer PRO without erasing the user's work.

### 3.1 Plant-identification cost ceiling

The per-account limits above are fair-use and abuse ceilings, not a prepaid
allocation. At the 2026-08-29 source review, Pl@ntNet's official Free tier is a
single shared **500-identification/day** allowance. Its contract-based Pro tier
starts at **EUR 1,000/year for 200,000 identifications**, with usage above the
included volume charged at EUR 0.005 per identification through 3 million
annual identifications; prices exclude tax. Recheck the live pricing and legal
terms before launch because these are vendor terms, not PlantDiseases product
promises.

The 3/day PRO ceiling permits 1,095 identifications per account/year. If one
account actually reaches that ceiling after the shared included volume is
exhausted, the nominal marginal Pl@ntNet charge alone is about EUR 5.48/year. That
is why neither the daily cap nor the proposed RuStore price can, by itself,
prove profitability. Cache hits, rejected/invalid uploads, and provider-not-
configured responses must not be counted as vendor calls; every real attempt,
including a retry after an ambiguous timeout, must be counted for cost
telemetry.

Before enabling paid traffic, operators must enable the PostgreSQL-backed,
versioned daily attempt and marginal-cost exposure policy below the active
Pl@ntNet contract limit, and alert before either ceiling. Before aggregate use
can exceed the Free allowance, either obtain
the applicable commercial contract or disable PlantNet cleanly. The app must
also satisfy the current Pl@ntNet attribution/logo conditions for its plan.
Crossing either ceiling or losing the authority must return the existing
provider-unavailable experience without consuming a user's PlantDiseases
allowance. The durable multi-instance implementation exists, but production
policy values, credential provisioning, alert evidence, and a clean-disable
exercise remain launch gates; per-account quotas are not a substitute for it.

## 4. Configuration and secret ownership

### Android build-time values (public, but release-controlled)

| Value | Production contract |
|---|---|
| `RUSTORE_CONSOLE_APP_ID` | Positive, non-zero numeric application ID from the exact RuStore Console app |
| `RUSTORE_PRO_MONTHLY_PRODUCT_ID` | `plantdiseases_pro_monthly` |
| `RUSTORE_PRO_YEARLY_PRODUCT_ID` | `plantdiseases_pro_yearly` |
| Pay return scheme | `plantdiseases-rustore-pay` |
| Package | `com.plantdiseases.app` |

The Console app ID is public configuration, not the API key ID. The published
APK/AAB must use the permanent PlantDiseases release signing certificate
registered for the same Console app. A blank ID/product setting must disable
checkout cleanly; it must not fall back to a guessed production value.
Protected staging and production builds reject blank or zero IDs and either
missing product; recovery builds intentionally keep Pay disabled.

### Account-service values

| Value | Purpose and handling |
|---|---|
| `RUSTORE_LIFECYCLE_ENABLED` | Builds callback decryption, canonical V5 validation, reconciliation, and existing-subscription lifecycle; `false` until the complete server secret configuration is ready |
| `RUSTORE_CHECKOUT_ENABLED` | Advertises products and permits app-initiated purchase verification; requires lifecycle enabled and may be set `false` independently to stop new sales |
| `RUSTORE_APPLICATION_ID` | Numeric Console application ID used by server-side operations |
| `RUSTORE_KEY_ID` | ID of the restricted RuStore API RSA key; configuration, not an Android value |
| `RUSTORE_PRIVATE_KEY_FILE` | Server-only Base64 PKCS#8 RSA private key file; secret, mode `0400` |
| `RUSTORE_NOTIFICATION_KEY_FILE` | Server-only Base64 AES-256 callback decryption key file; secret, mode `0400` |
| `RUSTORE_PACKAGE_NAME` | Exactly `com.plantdiseases.app` |
| `RUSTORE_SUBSCRIPTION_PRODUCTS` | Exactly `plantdiseases_pro_monthly,plantdiseases_pro_yearly` |
| `RUSTORE_ALLOW_SANDBOX` | `true` only in isolated staging; always `false` in production |
| `RUSTORE_NOTIFICATION_KEY_EPOCH` | Reviewed safe identifier changed atomically with every notification AES-key rotation |
| `RUSTORE_NOTIFICATION_MAX_ATTEMPTS` / `RUSTORE_NOTIFICATION_MAX_AGE_HOURS` | Bounded callback-worker retry policy; launch defaults are 8 attempts and 168 hours |
| `ENTITLEMENT_QUOTA_POLICY_VERSION` | Version frozen into every feature decision receipt |
| `ENTITLEMENT_FREE_DAILY_LIMITS` / `ENTITLEMENT_PRO_DAILY_LIMITS` | Exact five-feature `feature=value` maps; missing, duplicate, unknown, negative, or oversized limits fail startup |
| `PLANTNET_BUDGET_ENABLED` | Explicit shared PlantNet authority switch; `false` by default |
| `PLANTNET_BUDGET_POLICY_VERSION` | Reviewed low-cardinality policy/cohort version, 1–64 safe characters |
| `PLANTNET_DAILY_ATTEMPT_LIMIT` | Deployment-wide maximum real upstream attempts per UTC day |
| `PLANTNET_ATTEMPT_COST_MICROS` | Conservative marginal exposure per attempt in micro-euros |
| `PLANTNET_DAILY_COST_LIMIT_MICROS` | Deployment-wide UTC-day marginal exposure ceiling in micro-euros |

The Russian application service accepts `PLANTNET_API_KEY_FILE`, not a
plaintext production `PLANTNET_API_KEY`. It must be an absolute, regular,
owner-only, non-symlink file. Direct production configuration, an unsafe file,
or simultaneous direct/file values disables the integration before any vendor
request. The RU egress proxy permits only `my-api.plantnet.org` for this path.

Production and sandbox use separate secret files and separate callback URLs.
Never put an RSA private key, AES key, generated JWE, purchase receipt, or any
OAuth/client secret in Git, Gradle properties committed to the repository, an
APK, analytics, support tickets, screenshots, or shell history. Do not print
secret-file contents during health checks.

The electronic signature used to enroll the company in monetization is a
Console/legal credential, never a runtime application credential. RuStore's
[monetization enrollment guide](https://www.rustore.ru/help/en/developers/monetization/enable-monetization)
describes the application and qualified electronic-signature process.

## 5. RSA API authorization and subscription validation

Create the RSA key pair under **Company/Developer → RuStore API** in Console.
Restrict it to the PlantDiseases application and only the methods needed for:

- subscription data V5;
- subscription cancellation, if support/deletion cancellation is enabled;
- any explicitly implemented reconciliation/catalog call.

RuStore stores the public key. The backend receives the key ID and private key.
The [API authorization
guide](https://www.rustore.ru/help/en/work-with-rustore-api/api-authorization-process)
states that Console RSA keys are company-scoped and that owners/administrators
are responsible for rotation.

For each Public API token, the backend signs `keyId + timestamp` using
`SHA512withRSA`, calls `POST https://public-api.rustore.ru/public/auth/`, and
uses the returned JWE in the `Public-Token` header. RuStore documents a
900-second JWE lifetime in the [token
method](https://www.rustore.ru/help/en/work-with-rustore-api/api-authorization-token).
Cache it only in memory and refresh before expiry.

Checkout requires a signed-in PlantDiseases account. Android first obtains the
account server's dedicated random `billing_external_id` and passes that value
as Pay SDK `appUserId` (never an email, OAuth subject, or community actor ID).
After checkout it submits only the configured product ID, UUID purchase ID,
and sandbox marker to the account service with its normal account access token;
the client result alone never activates PRO.

The backend calls the current Pay SDK subscription method:

```text
Production:
GET https://public-api.rustore.ru/public/v5/subscription/
    {packageName}/{subscriptionId}/{purchaseId}

Sandbox:
GET https://public-api.rustore.ru/public/sandbox/v5/subscription/
    {packageName}/{subscriptionId}/{purchaseId}
```

The exact current contract is in [Getting Subscription Data
(V5)](https://www.rustore.ru/help/work-with-rustore-api/api-subscription-payment/api-subscription-validation-v5).
It works only for Pay SDK subscriptions.

Grant/extend PRO only when all checks succeed:

1. Environment is allowed (`sandbox` cannot enter production).
2. Package is exactly `com.plantdiseases.app`.
3. Product is one of the two exact allowlisted IDs.
4. Purchase ID parses as a UUID and has not been bound to another account.
5. `externalAccountId` is present and matches the authenticated account's
   server-issued `billing_external_id` supplied as `appUserId`; a missing or
   mismatched value fails closed, and the server never binds by email alone.
6. `paymentState` is `1` (paid) or `2` (free period), never `0` (pending).
7. The verified expiry is in the future and timestamps are plausible.
8. The full response passes strict size/type/status validation.

Persist the verified source, product, purchase ID, opaque billing-customer
binding, sandbox flag, start/expiry, auto-renew state, payment/acknowledgement
state, validated price/currency, last verification time, and provider
order reference. Treat purchase IDs and account bindings as restricted billing
data. Never log a full Public API response.

If RuStore or the account service is temporarily unavailable, fail closed for
a new grant. Do not erase an already verified entitlement before its recorded
expiry merely because a refresh timed out. Once the verified expiry passes,
access downgrades until a fresh authoritative result succeeds.

## 6. Encrypted server notifications

Configure **Monetization → Server notifications** for subscription events. Use
an HTTPS endpoint, a CA-issued TLS certificate, and a separate staging URL. The
[notification enablement
guide](https://www.rustore.ru/help/en/developers/monetization/payment-callback/enable-notifications)
requires a successful Console connection test before real delivery is enabled.

Expose only the exact callback path, never the whole account prefix, to the
published RuStore source networks. At the 2026-08-29 review those networks are
`95.163.133.0/30`, `85.198.107.0/24`, and `79.137.140.128/26`; recheck the
official server-setup page at every release and reject all other sources. The
tracked nginx contracts use a 96 KiB body ceiling—large enough for the maximum
64 KiB encrypted payload after Base64/JSON expansion—and short 3/5-second
send/read bounds. Do not place a CDN or untrusted reverse proxy in front of
this source-IP rule unless its authenticated client-address contract is
separately proven.

RuStore sends a JSON envelope with `id`, `timestamp`, and a Base64 encrypted
`payload`. Decrypt only with the Console-provided AES-256 key using:

- `AES/GCM/NoPadding`;
- 12-byte IV at the beginning of the decoded payload;
- 16-byte authentication tag.

Follow RuStore's [server setup and AES-GCM
parameters](https://www.rustore.ru/help/en/developers/monetization/payment-callback/server-set-up)
and [notification structure](https://www.rustore.ru/help/en/developers/monetization/payment-callback/notification).
Reject authentication/decryption failures without logging ciphertext, keys, or
the decrypted document.

Notification handling must be idempotent and order-safe:

1. Validate the HTTPS route, body size, envelope types, `app_id`, environment,
   notification type, product allowlist, and timestamps.
2. Deduplicate on the notification-key epoch plus SHA-256 fingerprint of the
   authenticated decrypted payload. The outer notification ID is not
   AES-GCM-authenticated and is kept only as bounded audit metadata. After 90
   days, compact terminal inbox rows but retain the epoch/fingerprint replay
   tombstone long-term.
3. Parse only `SUBSCRIPTION_EVENT` / `SUBSCRIPTION_EVENT_SANDBOX`, an allowlisted
   product, and a UUID purchase ID, then commit the parsed event to the durable
   pending inbox before returning a literal `200`.
4. A bounded background worker claims pending rows, calls subscription V5
   outside the callback request, and retries transient failures with backoff.
   Terminal failures such as canonical `404` go directly to a durable
   dead-letter state; transient failures also dead-letter after the configured
   attempt or age bound. Alert on the aggregate dead-letter gauge and replay
   only through the internal-token-protected aggregate operator endpoint. Apply the
   canonical result; authenticated event status/period may only revoke access
   or bound an already verified seven-day grace decision. A callback by itself
   never grants PRO.
5. Reserve a durable purchase-scoped fetch generation before every out-of-lock
   V5 request (app verification, callback, and reconciliation). If another
   request reserves a newer generation, skip the older upsert in its entirety.
   A superseded callback becomes terminal only after a newer canonical result
   has committed; a newer reservation that fails leaves the callback pending
   for bounded backoff and eventual dead-letter/manual replay.
6. Return literal `200` only after durable acceptance and without waiting for
   Public API network I/O. Return `4xx`/`5xx` when authentication or durable
   acceptance failed and RuStore should retry.

RuStore documents retries when it does not receive `200` within three seconds:
16 retry attempts over 36 hours. Test callbacks have no delivery guarantee.
Therefore, keep the handler fast and reconcile subscriptions from Public API;
notifications are not the only recovery path.

Do not turn callbacks off casually. RuStore states that events are not queued
while delivery is disabled. After any callback outage, URL change, or AES-key
rotation, run an explicit Public API reconciliation before declaring recovery.

## 7. Credential rotation

### RSA Public API key

Preferred zero/low-downtime rotation:

1. Create a second least-privilege key for the same app/methods.
2. Store the new private key in a new owner-readable secret file.
3. Deploy the new key ID/path and prove token creation plus one sandbox or
   read-only production validation.
4. Remove the old key from runtime, then revoke/delete it in Console.
5. Record operator, UTC time, affected services, verification evidence, and
   old-key revocation.

If Console policy forces **Replace key**, the old key becomes invalid
immediately. Use a coordinated change window and deploy the replacement to all
consumers at once. Rotate immediately after suspected disclosure or when a
staff member with key access leaves; RuStore explicitly identifies that case.

### AES notification key

RuStore shows a callback key once, and a rotated key applies to new
notifications immediately. For production:

1. Enter a controlled maintenance window and retain Public API reconciliation.
2. Generate/rotate the key in Console and save it directly to the secret store.
3. Atomically deploy the new `RUSTORE_NOTIFICATION_KEY_FILE` and a never-reused
   `RUSTORE_NOTIFICATION_KEY_EPOCH` value.
4. Run the Console connection test, then verify a real lifecycle event when
   safe.
5. Reconcile the possible transition gap and destroy superseded copies.

Never reuse sandbox AES/RSA material in production. Rotate the sandbox keys
first as a rehearsal.

## 8. Account deletion is not subscription cancellation

PlantDiseases account deletion, RuStore auto-renewal cancellation, and a refund
are separate operations.

- Deleting the PlantDiseases account erases/disassociates app account data
  according to the account deletion policy. It must not claim that RuStore
  billing was canceled unless the cancellation call actually succeeded.
- Canceling auto-renewal prevents another charge but normally leaves PRO active
  through the already paid/free period.
- A refund is a finance/support action and may or may not terminate the
  subscription, depending on the explicit operator choice in Console.

Before account-deletion confirmation, show an unambiguous bilingual warning
and a **Manage subscription** action. The preferred path is
`rustore://profile/subscriptions`, with a safe fallback when RuStore is not
installed; it is listed in RuStore's current [deeplink
documentation](https://www.rustore.ru/help/en/sdk/rustore-deeplinks). Support
may also cancel auto-renewal using the Pay SDK Public API operation for a known
purchase. RuStore documents that its
[cancel-subscription method](https://www.rustore.ru/help/en/work-with-rustore-api/api-subscription-payment/cancel-subscription)
ends the subscription at the paid-period boundary rather than immediately.

Do not block a valid account-erasure request indefinitely because RuStore is
unavailable. Record the cancellation outcome separately, give the person a
clear RuStore management route, and never imply that deleting or uninstalling
the app stops recurring charges. Recreating an account must not automatically
claim an old purchase by matching email; restoration requires fresh RuStore
proof and the original safe account binding.

Before production, product/legal must update the bilingual terms and privacy
surfaces with recurring-charge timing, the 7-day free-period conversion,
grace/hold behavior, cancellation, deletion separation, refund/support route,
purchase identifiers sent to RuStore/account service, and retention of the
minimum billing/audit records. RuStore states that recurring charges can occur
within eight hours before expiry and requires developers to describe charging
terms in user documentation.

## 9. Sandbox acceptance checklist

RuStore's [sandbox guide](https://www.rustore.ru/help/en/developers/monetization/sandbox)
uses accelerated subscription behavior (including ten-minute charge intervals)
and test cards. It is test evidence, not a forecast of production timing.

### Console and infrastructure

- [ ] Company/app owner and billing/security/support/rollback owners are named.
- [ ] Monetization enrollment is approved; no runtime secret is copied from an
      electronic-signature workstation.
- [ ] Console app ID, package `com.plantdiseases.app`, release certificate, and
      signed test artifact refer to the same app.
- [ ] Both exact products exist as unpublished subscriptions. Unpublished
      products remain available for sandbox testing but not production.
- [ ] Monthly and yearly standard periods, prices, free/grace/hold settings,
      and bilingual product descriptions match section 2.
- [ ] A least-privilege RSA key is restricted to this app and required methods.
- [ ] A separate sandbox HTTPS notification URL and AES key are configured.
- [ ] Secret files are owned by the runtime identity, mode `0400`, outside the
      repository and image layers.
- [ ] Staging has `RUSTORE_ALLOW_SANDBOX=true`; no production service does.

### Functional and abuse tests

- [ ] Pay SDK catalog returns both products and the UI renders its price labels.
- [ ] Monthly free-period activation, monthly paid state, and yearly paid state
      each bind to the intended signed-in account after V5 validation.
- [ ] Cancel/close, renew, payment failure, grace, hold, expiry, and restoration
      update entitlement without app restart.
- [ ] A canceled auto-renewal stays PRO until verified expiry, then downgrades.
- [ ] Duplicate and out-of-order callbacks do not double-apply or regress state.
- [ ] Invalid product/package/app ID, malformed UUID, wrong account binding,
      expired state, pending payment, oversized response, and forged client
      success never grant PRO.
- [ ] Sandbox data is rejected when the production flag/config is simulated.
- [ ] API/callback timeouts fail safely; reconciliation repairs a deliberately
      missed callback.
- [ ] Quotas are atomic under concurrency and an idempotent retry is charged
      once.
- [x] The deployment-wide Pl@ntNet attempt/exposure authority is PostgreSQL-
      durable, atomic across application instances, and fail-closed; cache hits
      and pre-provider failures do not debit it. Production values/alerts and
      live multi-instance evidence remain unchecked release gates below.
- [ ] Free/PRO limits and both lunar locks match the server entitlement.
- [ ] Account deletion copy and subscription-management path are exercised with
      active, canceled, grace, and no-subscription accounts.
- [ ] Logs, traces, crash reports, and analytics contain no keys, JWE, callback
      payload, raw Public API response, email, or full purchase identifier.

Archive the app commit/artifact digest, resolved Pay SDK/BOM, Console settings
screenshots with secrets redacted, test purchase IDs in restricted evidence,
callback/reconciliation results, schema revision, and test timestamps.

## 10. Production cutover checklist

### External operator steps

1. Have the company owner complete/confirm RuStore monetization and banking
   details, using the required qualified electronic signature where applicable.
2. Confirm the production Console app ID, package, and permanent release
   signing certificate; do not create a second accidental PlantDiseases app.
3. Create/review the two exact subscription products. Enter the launch-price
   hypotheses in Console, but verify the released app displays store-returned
   labels.
4. Generate the production least-privilege RSA key. Place only its key ID/path
   in deployment configuration; put the private key in the production secret
   store.
5. Configure the production HTTPS callback for subscription events, capture
   its one-time AES key directly into the secret store, and pass the Console
   connection test.
6. Render the production deployment. Verify the exact allowlist, package/app
   IDs, `RUSTORE_LIFECYCLE_ENABLED=true`, `RUSTORE_CHECKOUT_ENABLED=true`, and
   `RUSTORE_ALLOW_SANDBOX=false` without printing secrets.
7. Complete bilingual legal/product/support review, including recurring timing,
   free-to-paid conversion, grace/hold, cancellation versus deletion, refund
   handling, privacy, support contact, and the active Pl@ntNet plan's
   contract/attribution requirements.
8. Deploy account service and migrations first, verify callback and V5
   readiness, then deploy the application API, then the signed Android app.
9. Make one named internal real purchase on the lowest-risk plan, validate the
   account binding/entitlement, cancel renewal, and complete the approved refund
   test. Keep financial evidence restricted.
10. Publish both subscription products only after server readiness, production
    callback evidence, rollback readiness, and the signed client are accepted.
11. Roll out in stages (internal → small production cohort → wider cohorts),
    reviewing the gates below before each expansion.

### Required production gates

- [ ] CI passes for the exact release commit and resolved dependencies.
- [ ] Database backup/restore and entitlement migration rollback are proven.
- [ ] Existing verified PRO access survives a transient RuStore outage until
      its recorded expiry; new/unverified grants fail closed.
- [ ] Pl@ntNet aggregate usage cannot exceed the reviewed contract/spend limit
      merely because per-account quota remains, and the clean-disable path has
      been exercised without charging the user's allowance.
- [ ] Alerts cover V5 validation errors/latency, callback decrypt/retry lag and
      aggregate dead-letter backlog,
      reconciliation drift, subscription state transitions, quota denials, and
      purchase funnel regressions.
- [ ] `RUSTORE_ALLOW_SANDBOX=false` is asserted by deployment and readiness.
- [ ] Secret scanning finds no RSA/AES/JWE or unrelated provider secret in Git,
      artifacts, logs, or analytics.
- [ ] Product IDs and Console app ID in the signed artifact match production.
- [ ] Support can locate a subscription by restricted purchase/invoice evidence,
      cancel renewal, perform an approved refund, and explain deletion safely.
- [ ] The privacy/terms text matches the actual build and Console offer.

To stop new sales, set `RUSTORE_CHECKOUT_ENABLED=false` while keeping
`RUSTORE_LIFECYCLE_ENABLED=true`; this hides products and rejects app-initiated
verification without deleting stored expiries, disabling callbacks, or
stopping reconciliation. Do not disable lifecycle as a casual rollback: missed events are not queued. If a
callback interruption is unavoidable, record the window and reconcile through
Public API before restoring normal operation.

Downgrading schema revision `0010` to `0009` restores revision `0008`'s global
callback-fingerprint uniqueness. If the same authenticated payload was
retained under multiple key epochs, the downgrade deliberately keeps one row:
an `updated`/`test` outcome beats another terminal result, another terminal
result beats pending, and the most recently updated row breaks ties. Back up
the database first because the discarded per-epoch audit rows cannot be
reconstructed after rollback.

## 11. Telemetry for the profitability decision

Collect low-cardinality events with a pseudonymous internal account/cohort ID.
Never collect card/bank data, callback ciphertext/plaintext, keys, JWE, email,
or raw `developerPayload`. Keep raw purchase/invoice IDs in the restricted
billing ledger, not product analytics.

### Funnel and reliability

- offer impression, plan shown, store price label available/missing;
- upgrade tap, payment sheet opened, completed, canceled, or failed by safe
  error category;
- client success → V5 validation success/failure and validation latency;
- trial activation, trial-to-paid conversion, monthly/yearly mix;
- renewal, cancel, resume, grace entry/recovery, hold, close, refund;
- callback delivery/decrypt/dedup latency and reconciliation drift;
- quota denial, locked-feature impression, upgrade conversion, and `reset_at`.

### Cost and value

- active paid accounts and net subscription receipts by monthly/yearly cohort;
- refunds/chargebacks/taxes/store deductions from actual RuStore finance
  reports, not a guessed commission;
- Gamma/Frontier inference count and compute time by plan;
- PlantNet/OpenRouter and other per-request vendor cost by feature/plan;
- storage, image processing, egress, notification, and attributable support
  cost;
- D7/D30/D90 retention, first renewal, paid churn, and annual renewal;
- contribution margin per paid account and per plan.

Use this monthly contribution calculation:

```text
net RuStore receipts
- refunds and chargebacks
- variable model/vendor compute
- attributable storage, egress, notification, and support cost
= PRO contribution
```

Initial decision hypotheses (not contractual promises):

- trial-to-paid conversion at least 25%;
- first monthly renewal at least 65%;
- refunds below 5% of paid starts;
- p95 variable cost per paid account below 30% of net ARPU;
- overall PRO contribution margin at least 60% after the first complete paid
  monthly cohort.

Review after a minimum of one complete monthly renewal cohort; do not optimize
from trial starts alone. If margin misses, first inspect model/vendor costs,
abuse, annual discount, and quota utilization. Change one commercial variable
at a time, record the cohort boundary, and continue rendering prices from the
store. Never make the useful Aqua/free core worse merely to force conversion.

## 12. Official RuStore references

Only current official RuStore material was used for payment claims in this
runbook:

- [Pay SDK overview](https://www.rustore.ru/help/en/sdk/pay)
- [Current Pay SDK Kotlin/Java version](https://www.rustore.ru/help/en/sdk/pay/kotlin-java)
- [RuStore subscription-management deeplink](https://www.rustore.ru/help/en/sdk/rustore-deeplinks)
- [Migration from BillingClient to Pay SDK](https://www.rustore.ru/help/en/sdk/pay/migration)
- [BillingClient deprecation and 2026-08-01 cutoff](https://www.rustore.ru/help/en/sdk/payments)
- [Enable monetization](https://www.rustore.ru/help/en/developers/monetization/enable-monetization)
- [Create and configure a subscription](https://www.rustore.ru/help/en/developers/monetization/create-app-subscription)
- [Sandbox payment testing](https://www.rustore.ru/help/en/developers/monetization/sandbox)
- [RuStore API key-pair authorization](https://www.rustore.ru/help/en/work-with-rustore-api/api-authorization-process)
- [Create the short-lived Public API JWE](https://www.rustore.ru/help/en/work-with-rustore-api/api-authorization-token)
- [Validate a Pay SDK subscription with V5](https://www.rustore.ru/help/work-with-rustore-api/api-subscription-payment/api-subscription-validation-v5)
- [Enable encrypted server notifications](https://www.rustore.ru/help/en/developers/monetization/payment-callback/enable-notifications)
- [Configure callback TLS and AES-GCM](https://www.rustore.ru/help/en/developers/monetization/payment-callback/server-set-up)
- [Callback and subscription-event schema](https://www.rustore.ru/help/en/developers/monetization/payment-callback/notification)
- [Sandbox callback setup and key handling](https://www.rustore.ru/help/en/developers/monetization/payment-callback/pay-sandbox-notifications)
- [Cancel a Pay SDK subscription](https://www.rustore.ru/help/en/work-with-rustore-api/api-subscription-payment/cancel-subscription)

Current third-party cost assumptions in section 3.1 come from Pl@ntNet's
official [pricing page](https://my.plantnet.org/pricing), [Pro-plan billing
guide](https://my.plantnet.org/doc/getting-started/pro-plan), and [API access
terms](https://my.plantnet.org/terms_of_use). Recheck all three alongside the
signed contract before changing quotas or enabling paid scale.
