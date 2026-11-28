# Account API

The account service owns private provider identities, sessions, refresh tokens,
reauthentication, deletion state, and encrypted push registrations. Community
services receive only signed `community_actor_id` and nickname claims; the
internal account UUID and provider subject never appear in HTTP responses or
JWTs.

Local automated tests use deterministic mock providers. A deployed environment
may enable Yandex, VK, or both remote adapters independently; no real
application ID or provider token is committed or used by the automated suite.
German testing is restricted to dedicated tester accounts and keeps FCM
disabled.

In `AUTH_PROVIDER_MODE=remote`, startup requires at least one of the public
`YANDEX_CLIENT_ID` or numeric `VKID_CLIENT_ID` values. Yandex tokens are sent only in
the `Authorization: OAuth` header to its fixed HTTPS user-information endpoint;
the returned `client_id` must match and the app-specific `psuid` is retained as
subject. VK tokens are sent only in an HTTPS form body to VK ID's
client-bound `oauth2/user_info` endpoint. Both adapters discard email, phone,
birthday, gender, and every unrecognized response field. Network, timeout,
oversize, redirect, malformed-response, wrong-application, invalid-token, and
incomplete-profile paths fail closed with the stable provider errors. An API
request for a provider not enabled on that deployment returns
`provider_unavailable`.

Provider contracts: [Yandex ID user information](https://yandex.com/dev/id/doc/en/user-information)
and [VK ID's official SDK user-info request](https://github.com/VKCOM/vkid-web-sdk/blob/master/src/auth/auth.ts).

## Email and password sign-in

The second sign-in method next to Yandex ID. It is on only when
`EMAIL_PASSWORD_AUTH_ENABLED=true` **and** the mail settings are complete.
Otherwise every email route answers `503 email_auth_unavailable`; the service
still starts and Yandex is unaffected. Production settings: `DEPLOYMENT.md` §4.

| Flow | Routes |
|---|---|
| Sign up | `/v1/auth/email/register` mails a 6-digit code and returns `verification_required`; `/v1/auth/email/verify` accepts it; then the shared `signup/complete` and `signup/confirm`. No account row exists before the code is entered. |
| Sign in | `/v1/auth/email/login` |
| Forgotten password | `/v1/auth/email/password-reset/request`, then `.../confirm` with the code and the new password. Every session is revoked. |
| Add email to a Yandex account | `/v1/auth/reauth` with scope `link_email`, then `/v1/auth/email/add` and `/v1/auth/email/add/confirm` from the same session. |
| Add Yandex to an email account | `/v1/auth/email/reauth` with scope `link_yandex`, then `/v1/auth/yandex/link`. |

- **Storage.** Accounts are found by a keyed fingerprint of the normalized
  address. The address is also stored encrypted (AES-256-GCM, bound to that
  fingerprint) with a key derived from `ACCOUNT_ENCRYPTION_KEY_FILE`. A deletion
  request erases it at once, before community cleanup finishes.
- **Codes** work once, expire after 15 minutes and die after five wrong
  guesses. Only a keyed digest is stored; asking again retires the previous one.
- **Limits** per address: 3 messages per 15 minutes and 10 per day, 10 guesses
  per 15 minutes. Per IP: 20 messages per hour, 30 guesses per 15 minutes. Like
  the other limits here they live in memory, so they assume one replica and reset
  on restart. The five-guess limit per code is in the database.
- **No account enumeration.** Registering, adding or resetting an address answers
  the same whether or not it is in use. An address that is in use gets a notice
  instead of a sign-up or add code, and the stored code matches nothing. Mail is
  sent from a background queue so that response time does not depend on it. A
  failed send is logged by error type only; the member asks again.
- Switching the flag off, or removing the mail settings, also stops email
  members from signing in or reauthenticating, including to delete the account
  in the app. A pending email sign-up presented meanwhile is cancelled for good.
- Credentials stored before revision `0012` have no address and sign in again
  only after a password reset.

## Contract

The versioned schema is [`openapi.json`](openapi.json). Main flows are:

- provider exchange returns either a 10-minute, one-use pending-signup token or
  a new session;
- signup reserves an NFKC/casefold nickname transactionally;
- Ed25519 access JWTs last 15 minutes and contain only session ID, random
  community actor, nickname, issuer/audience/times, and authorization version;
- random 256-bit refresh tokens rotate on every use, idle-expire after 30 days,
  and absolute-expire after 180 days; replay revokes the whole family;
- reauthentication challenges are one-use, scope-bound, and valid for five
  minutes;
- deletion immediately marks the account pending, increments authorization
  version, revokes all sessions, disables push, and creates a retryable cleanup
  job. Community acknowledgement tombstones public content before private
  provider/profile/session rows are hard-deleted;
- push registrations are authenticated, AES-GCM encrypted, and deduplicated by
  a keyed fingerprint. Hardware names/identifiers are neither accepted nor
  stored.

The application API validates JWT signatures locally and then calls the hidden
`/v1/internal/auth/verify` route with a separate service credential to confirm
the current session and authorization version.

Errors have one non-reflective shape:

```json
{"error":{"code":"auth_required"}}
```

Request-validation errors never echo rejected input, which keeps provider,
pending, refresh, reauth, and FCM values out of responses. Request models reject
unknown fields.

## Test keys

`ACCOUNT_SIGNING_KEY_FILE` is a mode-`0600` JSON key ring containing an active
Ed25519 private seed and optional retired public keys. `ACCOUNT_ENCRYPTION_KEY_FILE`
is a mode-`0600` base64url-encoded 256-bit AES key.
`INTERNAL_SERVICE_TOKEN_FILE` is a random service credential used only on the
private network. It is owner-only in production; the isolated beta stack uses
mode `0640` in its dedicated shared-token volume. None of these files belongs
in Git or an image.

The local Compose stack runs `app.generate_test_keys` in a network-disabled
one-shot container. It creates signing/encryption files once in the beta-only
`account_keys` volume and the service token in a separate `internal_token`
volume that the app mounts read-only. It refuses non-test environments, unsafe
permissions, or overwriting existing values. Production requires
operator-provisioned files.

## PRO subscriptions

The account service is the authority for PRO state and daily server-feature
budgets. Android purchase results never unlock access by themselves: the
backend validates the purchase against RuStore's Public API V5, requires its
dedicated random billing account ID to match, stores the canonical expiry, and
then serves `/v1/entitlements`. The application API consumes signed-in quotas
through the hidden internal entitlement route before cost-bearing work.
Encrypted callbacks are authenticated and committed to a durable deduplicated
inbox before the route returns the required literal `200`; a bounded maintenance
worker performs canonical V5 fetches and bounded retries outside the callback
deadline. Purchase-scoped fetch generations prevent older responses from
regressing canonical state; terminal/exhausted jobs dead-letter with aggregate
metrics and an internal-token-protected manual replay endpoint.

Lifecycle and checkout are separate fail-closed switches. Existing callbacks
and reconciliation remain active when checkout is stopped. Enabling lifecycle requires decimal
`RUSTORE_APPLICATION_ID` and `RUSTORE_KEY_ID`, a Base64 PKCS#8 RSA private-key
file, a separate Base64 AES-256 notification-key file, the fixed package
`com.plantdiseases.app`, and the allowlisted monthly/yearly product IDs.
Production rejects sandbox validation. Never place either key in Git, an APK,
an image layer, or an environment variable.

Feature quotas come from exact Free/PRO operator maps and a validated policy
version that is frozen into every retry-safe decision receipt. The hidden
release route refunds an allowed reservation exactly once only when the trusted
application service proves no cost-bearing work began.

The same PostgreSQL service is also the deployment-wide PlantNet cost
authority. `PLANTNET_BUDGET_ENABLED=true` requires a versioned policy, a daily
attempt ceiling, a marginal micro-euro cost per attempt, and a daily exposure
ceiling. A migration-seeded provider row serializes every application replica;
successful anonymous attempt UUIDs are idempotent and retained for 90 days,
while denials create no attacker-amplifiable receipt rows. Missing configuration,
authority state, or database access denies the vendor attempt before network
I/O. The application service loads the actual PlantNet credential only through
its owner-only `PLANTNET_API_KEY_FILE` in production.

## Verification

The account tests live in `../server/tests/account/` and run with the server suite
(Python 3.14, one venv from `server/requirements-dev.txt`):

```sh
cd ../server
ruff check . ../account-server
pytest tests -q -n auto
```

To regenerate the committed schema, export the account environment variables
(see `server/tests/account/conftest.py`) and run, from `account-server/`:

```sh
python export_openapi.py openapi.json
```
