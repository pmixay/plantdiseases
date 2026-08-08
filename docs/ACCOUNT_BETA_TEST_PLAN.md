# PlantDiseases Account System Staging and RU/DE Deployment Plan

> **Superseding Android identity decision (2026-08-08):** “Account beta” is a
> workstream/environment name only. The tester APK is PlantDiseases 1.9,
> package `com.plantdiseases.app`, version code 19, signed by the one permanent
> PlantDiseases app key. The internal Gradle build type is `staging`; it changes
> endpoints but has no package suffix, version suffix, or alternate app label.
> It updates a same-signed 1.8 install and cannot coexist with it. Any older
> requirement below implying `.accountbeta` or a second installed app is
> replaced by this decision.

**Working branch:** `account-beta-test`

**Document type:** implementation, test, deployment, and handoff plan

**Status:** planning only; this document does not authorize a production launch

**Intended readers:** Android, backend, infrastructure, ML, security, and legal reviewers

## 1. Executive Summary

PlantDiseases will add accounts using **Yandex ID and VK ID only**. Email/password authentication is
not part of this release. Guest mode remains a first-class mode: guests may scan plants, keep local
garden/history data, submit explicitly consented anonymous model-improvement material, submit
minimal anonymous error telemetry, and read community content. An account is required for every
community mutation, including publishing a scan/post, writing a comment or second opinion,
requesting or cancelling a second opinion, registering for push notifications, and recording
account-private counters or views.

Development will occur on the dedicated `account-beta-test` branch. During the first engineering
phase, all backend processes may run together on a developer workstation. During the second phase,
the same test stack may run as an isolated service on the existing German VDS under the URL prefix
`/plantdisease-test/`. This German deployment is a **development/staging environment**, not the
Russian production data plane. It uses dedicated real Yandex/VK test applications and named tester
accounts so the official mobile SDKs and token exchange can be exercised end to end. It is not open
for public onboarding, its state is disposable, and it does not change the final production split.

The final production topology will be completed as a separate handoff task:

- Russia: account API, account PostgreSQL, community API/store, user media, push registrations,
  anonymous training store, telemetry, logs containing user-derived information, and backups.
- Germany: stateless ML inference only, with no account token, account/profile data, community data,
  training corpus, telemetry database, user media, or persistent request cache.
- Android: talks only to Russian public endpoints in production. The Russian application gateway
  calls the private German ML worker after removing metadata and identity context.

The 1 July 2025 wording of Article 18(5) does not permit collection, recording, accumulation,
storage, updating, or extraction of Russian citizens' personal data using databases outside Russia.
An explanatory letter from the Ministry of Digital Development specifically states that this also
applies to stored copies. A later cross-border transfer is a separate process under Article 12; it
does not make a German account or linked-community database an acceptable production database.

- [152-FZ Article 18](https://www.consultant.ru/document/cons_doc_LAW_61801/cbf4e15b7c330f9372e876cdf2bc928bad7950ef/)
- [Ministry of Digital Development letter of 12 May 2025](https://www.consultant.ru/document/cons_doc_LAW_511584/)
- [152-FZ Article 12](https://www.consultant.ru/document/cons_doc_LAW_61801/e4ebbe1780de623c7cf32a59ca82a7bb523a25dd/)
- [152-FZ Article 22](https://www.consultant.ru/document/cons_doc_LAW_61801/d996966e22e1320c9de1ab82d9f6be12c3d9d765/)

This architecture is a technical risk-control proposal, not a substitute for advice from Russian
privacy counsel.

## 2. Locked Product and Architecture Decisions

### 2.1 Authentication and profile

- Implement Yandex ID and VK ID.
- Do not implement email/password, email verification, password reset, or recovery codes.
- Never request Yandex/VK email, phone, birthday, gender, or contacts.
- Store only provider subject, provider display name, provider avatar URL, provider refresh time,
  and the minimum application/account state described below.
- The first provider used successfully to create an account becomes the immutable primary identity.
- A second provider may be linked, but providers cannot be unlinked in this MVP.
- Never merge two existing accounts automatically or through support tooling in this MVP.
- Recovery is possible only through a linked Yandex/VK identity. Losing all linked identities means
  losing access.
- Provider display name and avatar are private profile fields. Community surfaces display only the
  separate public nickname.
- A new identity does not receive a normal application session until nickname completion succeeds.

Email/password may be legally possible for an operator-owned Russian authentication system, but it
adds credential storage, verification, reset, mail delivery, anti-stuffing, abuse, and support work.
It is deliberately deferred. See [149-FZ Article 8](https://www.consultant.ru/document/cons_doc_LAW_61798/78b773a28f3ad19eb234697b20ab1d48c09f748a/).

### 2.2 Guest mode

Guests can:

- run on-device Aqua inference;
- submit a scan for server inference;
- maintain Room-based garden, scan history, reminders, and settings locally;
- read public community feeds, maps, thumbnails, posts, and opinions;
- submit structured diagnosis feedback for model improvement;
- opt in to anonymous training-scan collection;
- opt in to minimal error telemetry.

Guests cannot:

- publish a scan/post to the community;
- write a comment or second opinion;
- request or cancel a second opinion;
- register an FCM token;
- create account-private counters, view records, or subscriptions;
- claim an old anonymous community row;
- sync, back up, or restore local garden/history through the server.

`/api/analyze` is not persistent by default. A guest scan may enter the training corpus only after
the user has enabled the separately disclosed model-improvement consent. Community participation
and model-improvement consent are independent choices.

### 2.3 Scope exclusions

This release does not include:

- account sync or cloud backup;
- historical photo upload;
- Premium, purchases, or entitlements;
- email/password;
- provider unlinking or account merging;
- a server-side account outbox;
- blocking logout while any work is pending;
- a full Yandex Cloud managed-services stack;
- public moderation/reporting/blocking tools;
- changes to the current model taxonomy or model-training recipe;
- a Room schema migration: `Database.kt` is already version 6;
- automatic publication of an action that originally triggered login.

Logout is immediate from the user's perspective. The client clears local account credentials and
authenticated UI state even if the best-effort server revocation request fails. Local Room data is
preserved.

## 3. Delivery Phases and Ownership

### Phase A — Branch and local engineering environment

Owner: account-feature implementer.

1. Perform all implementation on `account-beta-test`; do not modify or deploy `main` directly.
2. Add `account-beta-test` to the GitHub Actions branch filters. The current CI configuration does
   not run for this exact branch name, so this is required before relying on CI.
3. Create a local Compose stack containing:
   - the existing PlantDiseases FastAPI application, modified for the beta contract;
   - a new independent `account-server` FastAPI application;
   - PostgreSQL for account/private data;
   - an optional fake provider server or in-process mock provider adapter;
   - isolated test volumes for community, anonymous training data, telemetry, thumbnails, and model
     artefacts.
4. Keep production `main` behaviour and the current production VDS untouched.
5. Use only generated provider subjects and generated profile fields in local automated tests.

### Phase B — Local integration and Android 1.9 staging build

Owner: account-feature implementer.

1. Introduce a `staging` Android build type:
   - application ID remains `com.plantdiseases.app`;
   - app label remains `PlantDiseases`;
   - version name/code are `1.9`/`19`;
   - use the permanent PlantDiseases signing certificate so it updates 1.8;
   - account UI enabled;
   - URLs supplied by Gradle properties, never scattered constants.
2. Point emulator builds at the local Compose stack.
3. Run account, privacy, community-auth, training-store, and process-restart tests.
4. Confirm that cancelling the new-account nickname flow returns to Guest and invalidates or allows
   the short-lived pending signup token to expire without creating an account.

### Phase C — Isolated German VDS test service

Owner: account-feature implementer for application artefacts; VDS operator for deployment.

1. Deploy the branch as a second, isolated Compose project on the existing German VDS.
2. Serve it below `https://pdis.arbuz.lol/plantdisease-test/` without modifying the existing
   production location.
3. Use dedicated containers, ports, networks, database names, secrets, volumes, backup policy, and
   log policy. No beta path may reuse production SQLite files or media directories.
4. Mount the already-provisioned PlantVision model bundle read-only and run the branch application
   server with real inference; do not share main's writable state.
5. Build the Android beta variant with the German test URLs, official Yandex/VK SDKs, matching
   provider application IDs, and a stable registered signing certificate.
6. Run real-provider tests only with named testers and dedicated test accounts. Do not enable public
   onboarding on this environment.
7. Treat every test volume as disposable. Do not plan to promote its PostgreSQL or SQLite files to
   production.

### Phase D — RU/DE production split handoff

Owner: the separately assigned infrastructure/server-split participant.

The handoff participant receives tested containers, explicit service contracts, migration tooling,
and acceptance tests. Their task is to:

1. provision the Russian data plane;
2. move account, community, push, user media, anonymous training data, telemetry, and applicable
   logs/backups to Russia;
3. extract or deploy a stateless German ML worker;
4. place a Russian ingress in front of inference;
5. complete legacy-data audit, migration, and deletion;
6. configure production OAuth applications, DNS, TLS, firewall, secrets, backup, and monitoring;
7. enable real team accounts only after the legal and operational gates pass.

This task must be a deployment and controlled extraction task, not a redesign of account APIs or
data ownership. Phase A-C code therefore must observe the final boundaries even while all test
processes are physically colocated.

## 4. Repository and Service Structure

### 4.1 Structure on `account-beta-test`

The beta branch should minimize high-risk movement of the current 4,000+ line `server/main.py` while
still creating enforceable service boundaries:

```text
plantdiseases/
├── account-server/
│   ├── app/
│   │   ├── main.py
│   │   ├── api/
│   │   │   ├── auth.py
│   │   │   ├── sessions.py
│   │   │   ├── me.py
│   │   │   └── internal.py
│   │   ├── auth/
│   │   │   ├── access_tokens.py
│   │   │   ├── refresh_tokens.py
│   │   │   ├── jwks.py
│   │   │   └── reauth.py
│   │   ├── providers/
│   │   │   ├── base.py
│   │   │   ├── mock.py
│   │   │   ├── yandex.py
│   │   │   └── vk.py
│   │   ├── domain/
│   │   │   ├── nicknames.py
│   │   │   ├── sessions.py
│   │   │   └── deletion.py
│   │   └── db/
│   │       ├── models.py
│   │       └── repository.py
│   ├── migrations/
│   ├── tests/
│   ├── Dockerfile
│   └── requirements.txt
├── server/
│   ├── main.py                    # existing API, auth gate, JWKS verifier
│   ├── community.py               # legacy store during beta; RU-owned later
│   ├── training_store.py          # replaces feedback.py + labeling.py storage
│   ├── telemetry.py               # minimized anonymous error telemetry
│   ├── pipeline.py                # unchanged ML core
│   ├── detector.py
│   ├── classifier.py
│   ├── conformal.py
│   ├── ood.py
│   └── tools/
│       ├── export_training_data.py
│       └── analyse_training_data.py
├── android/
├── deploy/
│   └── account-beta-test/
│       ├── compose.local.yml
│       ├── compose.german-vds.yml
│       ├── nginx.location.conf
│       ├── env.example
│       ├── backup-test.sh
│       └── README.md
└── docs/
    └── ACCOUNT_BETA_TEST_PLAN.md
```

`server/labeling.py`, `server/labeling_auto.py`, the separate `feedback.db`, the separate
`labeling.db`, claim/session fields left from the old review queue, and their old exporter are
removed only after a migration test proves that the replacement contains the necessary training
signals. No public or admin labeling endpoints are introduced.

### 4.2 Final handoff structure

The server-split participant may then evolve the tested beta modules into independently deployable
applications:

```text
services/
├── account-api/       # Russia: identity, session, push, deletion
├── app-api/           # Russia: community, training store, telemetry, content
└── ml-worker/         # Germany: health, capabilities, inference only
```

The German worker is composed from `pipeline.py`, `detector.py`, `classifier.py`, `conformal.py`,
`ood.py`, image validation, and the minimum static disease/model response data needed by inference.
It must not import account, community, push, telemetry, feedback, or training-store modules.

`ai_services.py` is not automatically part of the German ML worker. It handles PlantNet,
OpenRouter, user chat/context, and persistent caches. It remains in the Russian application service
or is disabled until its separate external-transfer/consent design is approved.

## 5. Local Compose Test System

### 5.1 Services

`deploy/account-beta-test/compose.local.yml` should define at least:

| Service | Local role | Suggested binding |
|---|---|---|
| `beta-app-api` | Current API plus account-aware community gate | `127.0.0.1:18000` |
| `beta-account-api` | Account/OAuth/session API | `127.0.0.1:18001` |
| `beta-postgres` | Account/private PostgreSQL | Docker network only |
| `beta-provider-mock` | Deterministic Yandex/VK fixtures if not in-process | `127.0.0.1:18003` |

Use `COMPOSE_PROJECT_NAME=plantdisease_account_beta_test`. Every state path must be under an
explicit beta-only named volume or a beta-only directory. Never rely on current `server/*.db`
defaults.

Required local environment defaults:

```text
ENVIRONMENT=test
AUTH_PROVIDER_MODE=mock
ACCOUNT_ISSUER=http://beta-account-api:8000
ACCOUNT_AUDIENCE=plantdiseases-app
ACCOUNT_JWKS_URL=http://beta-account-api:8000/.well-known/jwks.json
ACCOUNT_DATABASE_URL=postgresql+...://beta-postgres/...
COMMUNITY_DB_PATH=/data/community-beta.db
COMMUNITY_THUMB_DIR=/data/community-thumbs-beta
TRAINING_DB_PATH=/data/training-beta.db
TRAINING_IMAGE_DIR=/data/training-images-beta
TELEMETRY_DB_PATH=/data/telemetry-beta.db
```

The mock adapter must refuse to boot when `ENVIRONMENT=production`. Conversely, production mode
must refuse to boot with `AUTH_PROVIDER_MODE=mock`, default signing keys, missing issuer/audience,
or an unencrypted database connection across hosts.

### 5.2 Deterministic mock identities

Fixtures should cover:

- new Yandex identity requiring nickname;
- existing Yandex identity;
- new VK identity;
- existing account with both providers linked;
- identity already owned by a different account;
- expired, forged, wrong-application, provider-unavailable, and incomplete-profile responses.

Fixtures use clearly synthetic values such as `yandex-test-subject-001` and generated placeholder
avatars. Automated tests must not require real provider network access or real OAuth tokens.

### 5.3 Local Android configuration

The emulator configuration should be:

```text
APP_API_BASE_URL=http://10.0.2.2:18000/
ACCOUNT_API_BASE_URL=http://10.0.2.2:18001/
```

A USB-connected physical device may use `adb reverse` and the permitted
`127.0.0.1` URLs. Arbitrary LAN cleartext is not enabled. Loopback cleartext is
limited to debug and internal staging builds through their source-set network
security policy; production release builds require HTTPS.

### 5.4 Local developer workflow

The future implementation should provide documented, non-destructive commands equivalent to:

```text
docker compose -f deploy/account-beta-test/compose.local.yml up -d
docker compose -f deploy/account-beta-test/compose.local.yml ps
docker compose -f deploy/account-beta-test/compose.local.yml logs --follow
docker compose -f deploy/account-beta-test/compose.local.yml down
```

Volume deletion must be a separate explicit command and must never be part of ordinary `down`.

## 6. Isolated German VDS Test Deployment

### 6.1 URL routing

Use the existing TLS domain but a dedicated prefix:

```text
Application API: https://pdis.arbuz.lol/plantdisease-test/
Account API:     https://pdis.arbuz.lol/plantdisease-test/id/
```

Android account-beta defaults:

```text
APP_API_BASE_URL=https://pdis.arbuz.lol/plantdisease-test/
ACCOUNT_API_BASE_URL=https://pdis.arbuz.lol/plantdisease-test/id/
```

Nginx routing semantics:

- `/plantdisease-test/id/...` strips `/plantdisease-test/id/` and proxies to the beta account API;
- `/plantdisease-test/...` strips `/plantdisease-test/` and proxies to the beta application API;
- pass `X-Forwarded-Proto`, `X-Forwarded-Host`, and `X-Forwarded-Prefix` from trusted nginx only;
- configure FastAPI `root_path`/OpenAPI paths consistently;
- do not route the beta prefixes to the existing production process on error;
- set independent body-size, timeout, and rate-limit zones;
- disable request-body logging and raw authorization-header logging;
- disable or anonymize access logs for the beta locations.

### 6.2 Process and storage isolation

Suggested host bindings:

```text
127.0.0.1:18100 -> beta-app-api:8000
127.0.0.1:18101 -> beta-account-api:8000
```

PostgreSQL remains Docker-network-only. Use beta-specific names such as:

```text
plantdisease_beta_postgres
plantdisease_beta_community
plantdisease_beta_training
plantdisease_beta_training_images
plantdisease_beta_telemetry
plantdisease_beta_thumbnails
```

The test project must not mount production paths such as the current community, feedback, labeling,
telemetry, push, model-improvement, thumbnail, log, or backup paths. Read-only model weights may be
shared only if doing so cannot modify production artefacts.

### 6.3 German staging data rule

The German test deployment may contain:

- Yandex/VK provider subjects and minimal profiles for named dedicated test accounts;
- generated public nicknames and real beta sessions;
- synthetic community posts;
- public-dataset or deliberately prepared plant images with metadata removed;
- disabled FCM, training capture, telemetry, PlantNet, and OpenRouter integrations.

It must not be used for:

- public registration;
- valuable personal accounts or testers outside the named beta group;
- logging, snapshotting, or persisting provider access tokens;
- persistent FCM tokens from personal devices;
- promoting its databases into production;
- presenting the environment as legally production-ready.

Automated tests continue to inject mocked provider HTTP responses. The German end-to-end matrix uses
the official SDKs and live provider endpoints only from the permanently signed PlantDiseases 1.9
staging APK. The
operator accepts this restricted staging choice; public/production onboarding remains blocked on the
Russian data-plane and legal gates below.

### 6.4 German beta rollback

Rollback is operationally simple:

1. stop distributing the PlantDiseases 1.9 staging build to testers;
2. disable the two nginx beta locations;
3. stop the beta Compose project;
4. retain volumes only for the agreed short debugging window;
5. delete beta volumes and any provider snapshots after confirmation;
6. leave the existing production service and `main` branch untouched.

## 7. Account Data Model

PostgreSQL stores private account data. Use UUIDs internally, but do not expose the real account UUID
to the community service or Android where a narrower identifier works.

### 7.1 Core entities

#### `accounts`

- internal account UUID;
- random unique `community_actor_id` unrelated to provider/account identifiers;
- public nickname and normalized nickname;
- immutable primary provider;
- private primary-provider display name/avatar URL;
- nickname changed timestamp;
- authorization version;
- account/deletion state;
- created/updated timestamps.

#### `account_identities`

- account UUID;
- provider enum (`yandex`, `vk`);
- provider subject;
- provider display name and avatar URL;
- last provider-profile refresh timestamp;
- created timestamp.

Database constraints:

- unique `(provider, provider_subject)`;
- unique `(account_id, provider)`;
- unique `community_actor_id`;
- unique normalized nickname.

#### `sessions`

- random session ID;
- account UUID;
- random installation ID;
- generic device label, defaulting to `Android device`;
- creation, last-used, idle-expiry, absolute-expiry, and revoked timestamps;
- token-family/revocation state.

Do not collect or derive phone manufacturer, brand, hardware model, product name, serial, Android ID,
advertising ID, IMEI, or SIM information. A user-editable session label may be added later; automatic
hardware naming is prohibited.

#### `refresh_tokens`

- session/token-family reference;
- SHA-256 hash of a random token containing at least 256 bits of entropy;
- issued, consumed, idle-expiry, and absolute-expiry timestamps;
- replacement-token reference sufficient to detect replay.

Consumed rows are retained through the session family's validity window. Reuse of a consumed token
revokes the entire session family.

#### Other private tables

- `pending_signups`;
- `reauth_challenges`;
- `account_deletion_jobs`;
- `push_devices`;
- `account_consents` where a versioned account-level consent record is required;
- minimal `account_security_events` if required for incident response, with no request body, token,
  display name, avatar, or raw IP and with a documented short retention period.

### 7.2 Push storage

Remove the separate `push.db`. Store authenticated push registrations in `push_devices` in the
private account PostgreSQL database:

- account UUID and session ID;
- encrypted FCM token;
- keyed token fingerprint for uniqueness/deduplication;
- platform enum only;
- created/updated/disabled timestamps.

Do not store a phone model. Guests cannot register push tokens. Community code emits an internal
notification request keyed by signed `community_actor_id`; the account/notification component
resolves devices privately. The community database never receives the FCM token.

## 8. Access and Refresh Token Security

- Use an Ed25519 signing key held only by `account-api`.
- Publish current and recently retired public keys at `/.well-known/jwks.json`.
- Include `kid` and support overlap during key rotation.
- Issue 15-minute access tokens.
- Use 30-day idle refresh expiry and 180-day absolute session expiry.
- Access-token claims are limited to:
  - session ID;
  - random `community_actor_id`;
  - public nickname;
  - issuer;
  - audience;
  - issued/not-before/expiry timestamps;
  - authorization version.
- Never include provider token, provider subject, email, phone, real account UUID, provider display
  name, or avatar.
- Treat Android OAuth application identifiers/secrets as extractable public client configuration.
  They are never accepted as proof of identity.
- Send provider access tokens only to the account API, validate them against the configured provider
  application, never log them, and discard them after verification.
- Store signing keys and encryption keys in root-readable mounted files or an external secret
  mechanism, outside Git and outside container images.
- Apply per-IP and per-provider-subject rate limits to exchange, signup completion, refresh,
  nickname, reauthentication, and linking. The team-beta implementation may use an in-memory limiter;
  the final topology must document how limits behave across replicas.

## 9. Nickname and Profile Rules

- Normalize nickname uniqueness using Unicode NFKC followed by casefold.
- Accept 3-24 Unicode code points.
- Require at least one letter.
- Allow letters, digits, and underscore only.
- Allow Latin or Cyrillic letters, but never both in one nickname.
- Reject configured reserved and offensive values after normalization.
- Change nickname at most once every 30 days.
- Defer Unicode confusable-skeleton detection; mixed-script rejection is the MVP defense.
- Reserve nicknames transactionally in PostgreSQL; never rely on a pre-check alone.
- Community always renders the signed nickname, never provider display name/avatar.
- Logging, metrics labels, and error traces must not contain either nickname or provider display name.

## 10. Account API Contract

| Endpoint | Behaviour |
|---|---|
| `POST /v1/auth/yandex/exchange` | Validate a Yandex token. Return a session for an existing identity or a short-lived pending-signup token for a new identity. |
| `POST /v1/auth/vk/exchange` | Same flow for VK. |
| `POST /v1/auth/signup/complete` | Consume pending signup, reserve nickname transactionally, create account and primary identity, and return access/refresh tokens. |
| `POST /v1/auth/signup/cancel` | Invalidate the presented pending-signup token when nickname onboarding is cancelled. Client discarding plus short expiry remains a fallback. |
| `POST /v1/auth/refresh` | Rotate the refresh token and return a new pair; replay revokes the family. |
| `POST /v1/auth/logout` | Revoke the current session; client clears credentials regardless of network result. |
| `GET /v1/auth/sessions` | Return current/other sessions with random installation ID, generic or user-provided label, creation, and last-used time. |
| `DELETE /v1/auth/sessions/{session_id}` | Revoke a caller-owned session. |
| `POST /v1/auth/reauth` | Revalidate an already-linked provider and return a one-use five-minute challenge scoped to deletion or linking. |
| `POST /v1/auth/yandex/link` | Link Yandex after a link-scoped challenge. |
| `POST /v1/auth/vk/link` | Link VK after a link-scoped challenge. |
| `GET /v1/me` | Return nickname, private primary provider profile, linked providers, and timestamps. |
| `PATCH /v1/me/nickname` | Apply validation, uniqueness, and cooldown; return a fresh access token. |
| `DELETE /v1/me` | Require deletion-scoped reauthentication, revoke all sessions, mark deletion pending, and return `202`. |
| `GET /.well-known/jwks.json` | Publish active and recently retired verification keys. |

Stable machine-readable errors include:

```text
invalid_provider_token
provider_unavailable
provider_application_mismatch
profile_required
pending_signup_expired
pending_signup_replayed
nickname_invalid
nickname_taken
nickname_cooldown
reauth_required
reauth_expired
identity_already_linked
identity_owned_by_another_account
refresh_expired
refresh_reused
session_revoked
deletion_pending
auth_required
```

## 11. Community Authorization Contract

### 11.1 Public reads

Unauthenticated access remains available for community GET endpoints that expose public content,
including feeds, maps, heatmaps, scan details, thumbnails, and opinions/comments.

Guest reads must not create persistent per-view records. Aggregate traffic metrics, if needed, are
maintained outside community data and must not use a stable guest identifier.

### 11.2 Authenticated writes

Require a valid Bearer access token for:

- scan/post publication;
- comments/second opinions;
- second-opinion request and cancellation;
- view tracking when a private/account-specific view count is required;
- batched private counts and "my scans" state;
- push registration;
- deletion or mutation of caller-owned community content.

Validate issuer, audience, signature, expiry, not-before, authorization version, and `kid`. Fail
closed for unknown signing keys after one bounded JWKS refresh. Never forward the access token to
the ML worker.

Derive authorship exclusively from signed `community_actor_id` and nickname. Remove
`user_pseudo_id`, author ID, and nickname from write request bodies, or reject them when sent by a
new client. Do not trust a client-provided ownership field.

Add nullable account actor columns while retaining old pseudo-ID columns only for legacy rows.
Legacy anonymous rows remain readable, cannot be claimed, and cannot be mutated by a newly created
account. Old clients receive `401 auth_required` on write endpoints after the gate is enabled; reads
continue.

### 11.3 Login interception

When a guest taps a community write action:

1. show the login flow;
2. complete provider login and, if necessary, nickname onboarding;
3. return to the originating screen;
4. do not automatically publish, comment, vote, or request a second opinion;
5. require a new explicit user action.

This prevents an action initiated under one identity/consent state from being silently replayed
under another.

## 12. Anonymous Training and Feedback Store

### 12.1 Replace the legacy split stores

Remove the legacy distinction between `feedback.db` and `labeling.db`. Replace `feedback.py`,
`labeling.py`, and `labeling_auto.py` storage with one internal `training_store.py` and one
`training.db` plus sanitized image directory.

There are no labeling HTTP endpoints and no server-side human review queue. User-facing feedback
continues through feedback endpoints, while automatic scan capture occurs only as an internal
post-response operation. Offline export and review remain the only dataset-maintenance path.

Remove legacy fields and concepts that create unnecessary linkage:

- `user_pseudo_id`;
- account UUID or `community_actor_id`;
- client installation ID;
- client-generated persistent scan ID;
- raw or hashed client IP;
- phone model/manufacturer/device/product;
- `claimed_by_session` and `claimed_at`;
- free-text feedback notes;
- source URL for user scans;
- original file name/path supplied by the client.

Technical sample IDs, hashes, and a short-lived feedback receipt are not user identifiers and must
never be reused across other databases.

### 12.2 Suggested training schema

`training_samples` stores:

- random server sample UUID;
- sanitized image object key;
- SHA-256 and perceptual hash;
- coarse creation day or required timestamp;
- source enum (`consented_scan`, `feedback_upload`);
- model family/version;
- predicted class and confidence;
- top-k classes/scores;
- conformal prediction set;
- structured warning/status values;
- blur score bucket and detector region count/bbox-area features needed to diagnose pipeline errors;
- model severity result;
- structured user correctness;
- corrected class;
- structured correction reason;
- `out_of_head` flag;
- offline-review status/provenance;
- consent disclosure version;
- export/prune status.

`feedback_receipts` stores only:

- hash of a random 256-bit receipt returned with an analysis response;
- optional training sample UUID;
- expiry and consumed/updated state.

The receipt lets later feedback update the matching anonymous sample without a user, account,
installation, or community identifier. It must not be accepted by account/community APIs and should
expire after 30 days.

### 12.3 Capture policy

- Model-improvement collection is separately consent-gated.
- If consent is absent, inference completes without writing image/training metadata.
- Always retain consented disease predictions and uncertain predictions, subject to disk and daily
  per-class caps.
- Sample abundant confident healthy predictions deterministically by sanitized image hash.
- Retain every explicit negative correction with a usable sanitized image.
- Positive feedback may promote a matching weak label to `auto_yes`; negative feedback reopens it as
  pending with the corrected class.
- Never auto-retain `not_a_plant` or blur-gated full frames from guests; such frames are more likely
  to contain people or private surroundings. Obtain rejection-head hard negatives from curated
  sources or explicit privacy-screened feedback.
- Record model prediction as a weak prior, not ground truth.
- Accept deferred corrections such as `edema`, `sunburn_or_scorch`, and `is_a_plant`, marking them
  out-of-head where appropriate.

### 12.4 Image anonymization

Client-side processing before every upload:

1. decode the selected/captured image;
2. apply EXIF orientation to pixels;
3. render into a fresh RGB bitmap;
4. resize to the endpoint's maximum dimensions;
5. encode a new JPEG/WebP without EXIF, GPS, XMP, IPTC, ICC, thumbnail, file name, camera make/model,
   serial, timestamp, or editing history;
6. use a generic multipart name such as `scan.jpg`;
7. send no original URI or file-system path.

Server-side defense in depth:

1. decode the image with size/decompression guards;
2. apply orientation if a legacy client still sends it;
3. re-encode without metadata;
4. for automatic training capture, store detected leaf crops rather than the full room/background
   whenever a valid crop exists;
5. reject automatic storage when no safe crop exists;
6. apply a privacy screen for recognizable people/faces and other obvious identifying content before
   accepting a full-frame feedback image;
7. calculate hashes only after sanitization.

The system must call these records **de-identified** until a legal/security review confirms that the
stored pixels and metadata meet the intended anonymity standard. Removing an ID alone does not make
an identifiable photograph anonymous.

### 12.5 Disease-data quality

The new store must preserve the signals required by the existing ML discipline:

- per-class inventory by model version and source;
- disease and uncertainty oversampling without flooding from confident healthy scans;
- pHash near-duplicate detection;
- sanitized SHA-based idempotency;
- weak-label provenance (`model`, `model+user`, `offline_agent`);
- structured corrected classes and out-of-head labels;
- calibration diagnostics using top-k and conformal sets;
- overconfidence analysis;
- source inventory in every export;
- train-only treatment for unverified `auto_yes` rows;
- group/near-duplicate clustering during offline preparation so similar images cannot leak across
  train/evaluation/calibration splits.

The serving box never retrains or judges images. Retraining remains offline through
`train_notebook.ipynb`, followed by the existing real-world ship gate, calibration, conformal
fitting, and paired-bootstrap comparisons.

### 12.6 Export contract

`tools/export_training_data.py` produces a read-only timestamped archive containing:

- sanitized images;
- a manifest with training-relevant fields;
- class/source/model-version inventory;
- SHA/pHash duplicate report;
- schema/export version;
- checksums.

The archive must exclude:

- feedback receipt/hash;
- precise request time where a day bucket is sufficient;
- IP or IP hash;
- account, session, actor, installation, push, or community identifiers;
- nickname/provider profile;
- phone hardware model;
- raw logs;
- free text.

An automated privacy test opens every exported image and asserts that EXIF/XMP/IPTC metadata is
absent. Another test scans schema, manifest, and archive text for forbidden field names and seeded
sentinel values.

## 13. Minimal Telemetry

Guests may submit errors-only telemetry after explicit telemetry consent. Store only allowlisted,
bounded fields needed to reproduce failures:

- coarse stage enum;
- error kind enum;
- fatal flag;
- HTTP status bucket where applicable;
- model family;
- application version;
- Android API level or a coarse API bucket;
- allowlisted breadcrumb enums.

Remove or prohibit:

- `Build.MODEL`, manufacturer, brand, device, product, serial, Android ID, advertising ID, IMEI;
- installation/user pseudo-ID;
- raw or hashed IP persistence;
- nickname/account/session ID;
- precise location;
- images;
- arbitrary exception/free-text messages that may contain file paths, URLs, names, or tokens;
- request/response bodies.

Use an explicit generic User-Agent rather than a hardware-derived string. Perform abuse limiting in
memory using the live connection address; do not persist that address or its hash. Retain telemetry
for 30 days by default and aggregate only by non-identifying technical dimensions.

Authenticated users are subject to the same telemetry minimization. Telemetry is not joined to the
account database.

## 14. Android Authentication Architecture

### 14.1 Configuration and clients

- Replace the single logical backend assumption with:
  - `APP_API_BASE_URL` for inference/community/content;
  - `ACCOUNT_API_BASE_URL` for identity/session/profile.
- Production Android never receives the German ML worker URL.
- Use separate `AppApiClient` and `AccountApiClient`, sharing only safe HTTP configuration.
- Add an authentication interceptor only to calls that require the application access token.
- Never attach the access token to public images, third-party URLs, or the future German worker.

### 14.2 State and token storage

Represent authentication as:

```text
Guest
ProfileRequired(pendingSignup)
Authenticated(profile, session)
```

- Store refresh token only in Android Keystore-backed encrypted storage.
- Keep access token in memory.
- On process restart, recover by exchanging the encrypted refresh token.
- Serialize refresh operations so concurrent `401` responses cause one refresh.
- Retry an application request once after a successful refresh.
- Never create refresh loops.
- Clear refresh/access/pending tokens, provider SDK state, and authenticated UI on logout.
- Preserve Room database, scans, garden, reminders, and guest settings.

### 14.3 OAuth and UI

- Integrate official Yandex ID and VK ID mobile flows without email scope.
- Register the permanent release signature for `com.plantdiseases.app` with
  both providers. Debug registrations may be used only for local development;
  there is no `.accountbeta` package registration.
- Show a non-skippable nickname completion screen after first OAuth.
- Cancelling nickname completion invokes signup cancellation if reachable, clears pending state, and
  returns to Guest.
- Add profile surfaces for nickname, private provider name/avatar, linked identities, active
  sessions, provider linking, logout, and deletion.
- Display generic session labels; do not display or upload the phone hardware model.

### 14.4 Client image sanitization tests

Add fixtures containing:

- GPS EXIF;
- camera make/model and serial;
- original timestamp;
- embedded thumbnail;
- rotated EXIF orientation;
- XMP/IPTC text;
- PNG/WebP input.

Tests must prove that uploaded bytes:

- preserve visual orientation;
- contain no metadata or original file name;
- remain within size/dimension limits;
- are the only bytes passed to inference, community upload, feedback image, and training-consent
  flows.

## 15. Account Deletion

1. Require a one-time deletion-scoped reauthentication challenge.
2. Mark the account `deletion_pending` and revoke every session immediately.
3. Disable/delete account push registrations.
4. Hide the actor's community content immediately.
5. Run idempotent cleanup of posts, comments/opinions, requests, views, thumbnails, and related
   account-private records.
6. Retry cleanup jobs with exponential backoff.
7. After community cleanup succeeds, hard-delete provider identities and private profile data as
   allowed by the documented retention policy.
8. Keep only the minimum non-personal deletion-job outcome required for operations.

The anonymous training store intentionally has no account/actor mapping. Account deletion cannot
target genuinely anonymized samples. This must be disclosed accurately; if any retained mapping
allows an operator to reconnect a sample to an account, the sample is not anonymous and must join
the deletion workflow.

In the temporary German beta, account deletion calls the beta application's TLS/private internal
cleanup endpoint. In the final Russian topology, both services communicate over the private Russian
network. The German ML worker never participates in deletion because it stores no user state.

## 16. Legacy Data Audit and Migration

Before final RU activation, inventory all current German material, including:

- `community.db` and thumbnails;
- `feedback.db` and feedback images;
- `labeling.db` and labeling images;
- `telemetry.db`;
- `push.db`;
- current `server/admin_backups/` equivalents on the real VDS;
- application/nginx/system logs;
- AI caches and plant-photo caches;
- host/provider snapshots.

Migration procedure:

1. stop or freeze writes;
2. classify each field/file and its legal basis;
3. exclude corrupt, unnecessary, or impermissibly collected material;
4. transform valid feedback+labeling rows into the new anonymous training schema, removing joins and
   sanitizing/re-encoding images;
5. migrate valid community data/media to Russia;
6. migrate authenticated push registrations only into the private account database after ownership
   can be established lawfully; otherwise discard old anonymous FCM rows;
7. transfer through an encrypted channel;
8. verify row counts, image counts, checksums, and application-level samples;
9. complete a restore test in Russia;
10. cut clients/DNS over;
11. delete migrated personal/user-linked data, backups, logs, temporary files, and snapshots from
    Germany;
12. record completion evidence.

Legacy anonymous community rows remain read-only and unclaimable. Do not assume that deleting one
column makes a row anonymous; assess all available linking information.

## 17. Final RU/DE Production Topology

### 17.1 Russian data plane

One inexpensive Russian VPS is sufficient for the initial team release. Yandex Cloud is not
required. Deploy through Docker Compose:

- nginx/TLS ingress;
- account API;
- application/community API;
- PostgreSQL;
- community storage;
- account push worker;
- anonymous training store and sanitized image volume;
- telemetry store;
- backup job.

Bind PostgreSQL and internal APIs to the private Docker network only. Store secrets outside Git in
root-readable mounted files or environment managed by the host.

Create encrypted nightly backups outside the VPS but in a Russian location. Retain seven daily and
four weekly copies. Complete one restore and one Ed25519 key-rotation exercise before expanding
beyond the development team.

### 17.2 German inference plane

Expose no public Android-facing German endpoint. The worker accepts only the Russian ingress source
IP plus mTLS or a rotating service credential.

The worker:

- accepts sanitized image bytes, model family, language/result options, and a one-use request ID;
- does not receive JWT, account/actor/session ID, nickname, provider data, installation ID, or FCM
  token;
- processes in memory;
- uses read-only model/static volumes;
- writes no request image, result, feedback, training record, or user cache;
- uses `tmpfs` for unavoidable temporary files;
- disables request/body/access logging for inference;
- emits only aggregate health/latency/error metrics.

### 17.3 Inference data flow

```text
Android
  -> Russian application ingress
       -> validates size/type and strips metadata again
       -> removes all account context
       -> creates one-use inference request ID
       -> private German ML worker
       <- diagnosis result
  <- response to Android
```

Because a plant photo can accidentally include a person, home, license plate, or other identifying
content, treat RU-to-Germany inference as a potential cross-border transfer until legal review says
otherwise. Complete the Article 12 assessment/notification and document the German hosting
relationship before real use.

## 18. Security and Logging Requirements

- Redact `Authorization`, OAuth tokens, refresh tokens, pending-signup tokens, reauth challenges,
  FCM tokens, cookies, request bodies, provider display names, avatar URLs, and nicknames.
- Do not include secrets in exception strings or metrics labels.
- Disable debug HTTP body logging in account-beta distributions used by testers.
- Add request correlation IDs that are random per request and are not persisted across services
  longer than operationally necessary.
- Use strict CORS origins; Android itself does not require permissive browser CORS.
- Set trusted proxy networks explicitly.
- Bound upload size, decoded dimensions, request concurrency, inference time, and decompression.
- Restrict internal account cleanup/push/JWKS consumers by private network and service credential.
- Encrypt FCM tokens and sensitive mounted backups.
- Run containers as non-root with read-only root filesystem where practical.
- Separate production, beta, and local signing/encryption keys.
- Ensure the application refuses unsafe environment combinations at startup.

## 19. Test Plan

### 19.1 Provider and signup

- valid/new/existing Yandex and VK identity;
- forged, expired, wrong-application, missing-profile, and provider-rejected token;
- provider timeout/outage;
- pending signup expiry, replay, and cancel;
- no normal session before nickname completion;
- no forbidden OAuth scope or stored profile field.

### 19.2 Nickname

- concurrent reservation of the same nickname;
- NFKC/casefold collision;
- Latin/Cyrillic mixed-script rejection;
- length by Unicode code point, not UTF-8 bytes;
- digit/underscore rules and required letter;
- reserved/offensive values;
- 30-day cooldown;
- fresh access token after change.

### 19.3 Sessions and tokens

- multiple devices with generic labels;
- serialized Android refresh;
- refresh rotation and consumed-token replay;
- current logout and remote-session revoke;
- 30-day idle and 180-day absolute expiry;
- signing-key rotation and unknown `kid` refresh;
- wrong issuer/audience/signature/expiry/authorization version;
- provider outage does not invalidate an existing application session.

### 19.4 Linking and reauthentication

- five-minute scoped challenge expiry/replay;
- wrong provider and wrong challenge scope;
- same provider linked twice;
- identity already owned by another account;
- explicit absence of automatic merge and unlink.

### 19.5 Community authorization matrix

For each community endpoint, test Guest, valid account, expired token, forged token,
wrong-audience token, and deleted account.

- every public GET remains readable by Guest;
- every community mutation returns `401 auth_required` to Guest;
- client actor/nickname spoofing is ignored or rejected;
- ownership derives only from JWT;
- legacy rows remain readable and unclaimable;
- login interception never auto-replays the original mutation.

### 19.6 Guest-data minimization

- inference without training consent writes no scan/training record;
- telemetry without consent writes nothing;
- Guest read creates no community view row;
- Guest cannot register FCM;
- guest databases/logs contain no pseudo-ID, account/session/actor ID, raw/hashed IP, nickname, phone
  model, file name, or path;
- rate limiting functions without persistent IP storage.

### 19.7 Training store and export

- client and server metadata stripping;
- safe leaf-crop capture and unsafe full-frame rejection;
- SHA and pHash idempotency;
- class-balanced retention and healthy sampling;
- disease/uncertain/negative-feedback retention;
- feedback receipt expiry and inability to use it outside feedback;
- positive promotion and negative reopening;
- deferred/out-of-head correction;
- no legacy user/scan linkage in schema or export;
- archive EXIF/XMP/IPTC absence;
- manifest provenance, class inventory, and checksums;
- offline group/near-duplicate clustering prevents split leakage.

### 19.8 Push and deletion

- Guest push rejection;
- encrypted authenticated token registration/deduplication;
- no phone model in push/session records;
- opinion event resolves only the intended account devices;
- stale FCM token deletion;
- deletion immediately revokes sessions/disables push/hides content;
- cleanup retries during community outage;
- thumbnails and related rows are removed;
- private profile/identities are eventually removed.

### 19.9 Android

- PlantDiseases 1.9 staging updates a same-signed 1.8 install;
- the staging defaults point only to `/plantdisease-test/`;
- process restart restores through encrypted refresh token;
- logout removes account credentials without deleting Room data;
- Guest/ProfileRequired/Authenticated UI transitions;
- OAuth cancel and nickname cancel;
- one refresh retry and no loop;
- no `Build.MODEL` or other hardware identifiers in serialized requests;
- sanitized bytes used by inference, feedback, training, and community upload.

### 19.10 Environment and deployment

- local Compose health and dependency readiness;
- mock provider cannot boot in production mode;
- German beta paths cannot reach production volumes/processes;
- beta nginx prefix and generated media URLs work;
- direct German worker access is blocked in final topology;
- Russian ingress never forwards JWT/account context;
- backup restore and key rotation;
- German disk/log inspection finds no final-production user state.

## 20. Rollout and Observability

### 20.1 Account-beta branch rollout

1. land backend unit tests;
2. land Android unit/Robolectric tests;
3. make GitHub Actions run on `account-beta-test`;
4. run local Compose integration tests;
5. produce the permanently signed PlantDiseases 1.9 staging APK;
6. deploy isolated German staging;
7. run smoke tests against `/plantdisease-test/`;
8. distribute only to named team testers with dedicated provider test accounts;
9. collect defects without promoting staging data;
10. freeze the API/data contracts for handoff.

### 20.2 Final split rollout

1. provision isolated Russian staging;
2. deploy account/application services and restore a synthetic snapshot;
3. connect private German stateless inference;
4. complete legal/operator/trans-border notices and policy updates;
5. register provider debug/release signatures and enable real provider adapters;
6. run real-provider smoke tests on RU staging;
7. migrate/audit valid legacy data;
8. install the updated team client;
9. enable the community auth gate;
10. monitor before any external invitation.

### 20.3 Metrics

Monitor without high-cardinality personal labels:

- provider exchange success/failure/latency by provider and error code;
- pending-signup completion rate;
- refresh success, expiry, and replay detection;
- nickname collision/cooldown counts;
- JWT verification and JWKS refresh failures;
- community write `401/403/5xx` counts;
- deletion-job age and retry count;
- push success/stale-token counts;
- training samples by class/model/source/status;
- training-store rejection/privacy-screen counts;
- telemetry error counts by allowlisted technical dimensions;
- German inference latency/error/concurrency;
- PostgreSQL/SQLite volume capacity;
- backup and restore-check status.

Never label metrics by account, actor, session, nickname, provider subject, FCM token, IP, or image
hash.

## 21. Acceptance Criteria and Handoff Package

The account-beta implementation is ready for infrastructure handoff when:

- the complete account API contract and Android state machine pass tests;
- Guest can read community but cannot mutate it;
- account authorship cannot be spoofed from request bodies;
- push registrations live in private PostgreSQL and contain no phone model;
- the old feedback/labeling split has been replaced by one anonymous training store;
- no labeling HTTP/admin queue exists;
- exported training archives contain useful disease/model/calibration provenance but no user,
  account, installation, community, IP, device-model, or metadata linkage;
- Android strips image metadata before every relevant upload and the server repeats sanitization;
- local Compose starts from an empty workspace with mock providers;
- German staging runs under `/plantdisease-test/` without touching production volumes;
- production mode cannot start with mock auth or development keys;
- the final RU/DE service ownership and network contract is documented and tested.

Handoff artefacts:

- versioned OpenAPI documents for account and application APIs;
- database migrations and schema diagram;
- local and German beta Compose files;
- final RU/DE Compose templates or service manifests;
- nginx prefix configuration and health checks;
- environment/secret inventory without secret values;
- legacy migration dry-run report;
- anonymous export privacy report;
- automated test report;
- rollback runbook;
- Russian data-plane and German worker acceptance checklist.

## 22. Legal and Public-Release Gates

- The development-team restriction is not treated as an exemption from personal-data law.
- The German all-in-one test topology is restricted real-provider staging only; it is not public or
  production and its data is never promoted.
- Before processing real account data, submit or update the Article 22 operator notification unless
  counsel documents an applicable exception.
- Before potential image processing in Germany, complete the Article 12 assessment/notification and
  hosting/processor documentation.
- Publish an accurate Russian/English privacy policy and versioned consent text before real beta.
- The policy must separately explain account/profile, community publication, push, telemetry,
  model-improvement capture, anonymous export, retention, deletion, and external services.
- Before invited external testing/public release, complete community reporting, blocking,
  moderation, and safety filters.
- PlantNet/OpenRouter, FCM initialization/consent details, city-only location policy, sync, media
  normalization beyond this plan, and a full threat model require their own reviewed release work.
- Managed Russian cloud services may be introduced later, but they are not required for this MVP.

## 23. Final Assumptions

- The Russian operator owns the OAuth applications, domain, Russian services, signing keys, and
  production databases.
- The existing German production service remains available while the beta path is developed.
- German beta databases are disposable and contain only the minimal account data of named testers.
- The final German worker stores no user-derived state or backup.
- The final Russian environment is available before real team account onboarding.
- The infrastructure/server-split participant accepts ownership of Phase D and the legacy migration.
- Existing local Room data remains device-local and outside the account MVP.
- Expanding German beta beyond named dedicated test accounts, enabling public onboarding, or
  promoting its state is a release-blocking architecture/legal change requiring explicit review.
