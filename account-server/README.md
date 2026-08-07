# Account API

The account service owns private provider identities, sessions, refresh tokens,
reauthentication, deletion state, and encrypted push registrations. Community
services receive only signed `community_actor_id` and nickname claims; the
internal account UUID and provider subject never appear in HTTP responses or
JWTs.

This branch permits deterministic mock providers only in `ENVIRONMENT=test`.
Remote Yandex/VK adapters deliberately fail startup until their separately
reviewed Russian-data-plane implementation exists. Do not use real provider or
FCM tokens in the local or German synthetic environments.

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
  version, revokes all sessions, disables push, and creates a cleanup job;
- push registrations are authenticated, AES-GCM encrypted, and deduplicated by
  a keyed fingerprint. Hardware names/identifiers are neither accepted nor
  stored.

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
is a mode-`0600` base64url-encoded 256-bit AES key. Neither file belongs in Git
or an image.

The local Compose stack runs `app.generate_test_keys` in a network-disabled
one-shot container. It creates random files once in the beta-only
`account_keys` volume and refuses non-test environments, relaxed permissions,
or overwriting existing keys. Production requires operator-provisioned files.

## Verification

From `account-server/`, with an explicit test environment configured:

```sh
ruff check .
pytest -q
python -m tools.export_openapi openapi.json
```

The PostgreSQL process-restart test is opt-in and accepts only an isolated beta
database URL:

```sh
ACCOUNT_POSTGRES_INTEGRATION_URL='postgresql+psycopg://...@127.0.0.1:15432/plantdisease_beta' \
  pytest tests/test_postgres_integration.py -q
```

It creates a uniquely generated synthetic provider subject and leaves the beta
volume intact for inspection.
