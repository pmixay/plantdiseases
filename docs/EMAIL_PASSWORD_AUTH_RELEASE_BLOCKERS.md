# Email/password authentication: production HOLD

The account server and Android client contain a controlled beta slice for
email/password accounts, but it is **not approved for production exposure**.
`EMAIL_PASSWORD_AUTH_ENABLED=true` is permitted only in test/development/staging;
production configuration rejects it and the protected Android production build
hides the entry point. Do not advertise or enable the three
`/v1/auth/email/*` endpoints in production until every blocker below is closed
and tested.

German staging is also off by default. A named operator may enable it only for
a scheduled exercise after every human-facing route capable of reaching email
auth is restricted at the edge to exact named-tester or controlled VPN egress
addresses with a final deny-all rule. A separately source-allowlisted machine
billing callback must not share or bypass those email routes. Disposable
addresses are required. The flag is not access control and must return to
`false` when the exercise ends. An email completion or confirmation presented
while the flag is closed is durably cancelled, so that pending bearer cannot
resume after a later re-enable; OAuth pending signups on the shared endpoints
are unaffected.

## Blocking gaps

1. **Email ownership is not verified.** The current slice treats an email
   address only as a normalized login identifier, persists only a keyed HMAC
   lookup fingerprint, and activates an account without proving inbox control.
   Production requires a durable mail outbox, short-lived one-use verification
   tokens stored only as hashes, resend throttles, bounce handling, and an
   explicit unverified-account lifecycle.
2. **Password recovery is not implemented.** Because no recoverable address is
   stored, support cannot safely reset a password. Production requires an
   encrypted, purpose-limited delivery address with documented retention and
   key rotation; generic reset responses; one-use expiring reset tokens; and
   revocation of existing sessions after a successful reset. Support must never
   bypass this flow manually.
3. **Abuse controls are process-local.** The existing limiter is per process,
   so attempts are not shared across replicas and disappear on restart.
   Registration, login, verification, resend, and reset need a shared bounded
   limiter with per-IP, per-account-fingerprint, and aggregate controls. The
   responses and timing must be reviewed for account enumeration and credential
   stuffing, then load-tested with the Argon2 concurrency bound.
4. **Operational security is incomplete.** Add password-change and recovery
   notifications, breached-password screening without logging the password,
   mail-provider secret rotation, recovery abuse alerts, and runbooks before
   enabling the feature.

## Properties already enforced by this slice

- The normalized address is never persisted or returned. Database lookup uses
  a purpose-separated HMAC-SHA-256 fingerprint.
- Passwords are length-checked and stored as Argon2id PHC hashes; verification
  includes a dummy-hash path for unknown accounts and bounded in-process Argon2
  concurrency.
- Login failures use the same `invalid_credentials` response for an unknown
  address, malformed address, or wrong password. Reauthentication failures use
  the generic `reauth_required` response.
- Email-primary accounts use the existing one-use, scoped reauthentication
  challenge for OAuth linking and account deletion.

These properties reduce risk but do not close the production HOLD. A release
review must include automated verification/reset tests, multi-replica abuse
tests, privacy review of the encrypted delivery address, and a documented
rollback/migration plan.

The flag is defense in depth, not the mechanism for declaring the blockers
closed. Removing the production validation guard requires a reviewed code
change, updated privacy/operations contracts, and the evidence above; changing
an environment variable alone must never expose unfinished password auth.
