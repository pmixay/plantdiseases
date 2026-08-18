# Account Beta Private Schema

The authoritative definitions are Alembic migrations `0001` through `0006` plus
`account-server/app/db/models.py`. This diagram is a handoff view, not a second
schema source. The reviewed Alembic head is
`0006_signup_completion_idempotency`.

```mermaid
erDiagram
    accounts ||--o{ account_identities : owns
    accounts ||--o{ sessions : opens
    sessions ||--o{ refresh_tokens : rotates
    refresh_tokens o|--o| refresh_tokens : replaces
    pending_signups ||--o| signup_completions : starts
    accounts o|--o{ signup_completions : provisions
    sessions o|--o{ signup_completions : provisions
    accounts ||--o{ reauth_challenges : authorizes
    sessions ||--o{ reauth_challenges : scopes
    accounts ||--o| account_deletion_jobs : queues
    accounts ||--o| account_nickname_jobs : propagates
    accounts ||--o{ account_nickname_claims : reserves
    accounts ||--o{ push_devices : owns
    sessions ||--o{ push_devices : registers
    accounts ||--o{ account_consents : records
    accounts ||--o{ account_security_events : emits
    sessions ||--o{ account_security_events : contextualizes

    accounts {
        uuid id PK
        uuid community_actor_id UK
        string nickname
        string normalized_nickname UK
        enum primary_provider
        string private_provider_display_name
        string private_provider_avatar_url
        int authorization_version
        enum state
    }
    account_identities {
        uuid id PK
        uuid account_id FK
        enum provider
        string provider_subject
        string private_display_name
        string private_avatar_url
        timestamp refreshed_at
    }
    sessions {
        uuid id PK
        uuid account_id FK
        uuid installation_id
        string generic_device_label
        uuid token_family_id UK
        timestamp idle_expires_at
        timestamp absolute_expires_at
        timestamp revoked_at
    }
    refresh_tokens {
        uuid id PK
        uuid session_id FK
        bytes sha256_token_hash UK
        timestamp consumed_at
        uuid replacement_token_id FK
    }
    pending_signups {
        uuid id PK
        bytes sha256_token_hash UK
        enum provider
        string provider_subject
        string private_display_name
        string private_avatar_url
        uuid installation_id
        timestamp expires_at
        timestamp consumed_or_cancelled_at
    }
    signup_completions {
        uuid id PK
        uuid pending_signup_id FK,UK
        uuid installation_id
        bytes sha256_request_fingerprint
        uuid account_id FK
        uuid session_id FK
        timestamp expires_at
        timestamp confirmed_at
        timestamp cancelled_at
    }
    reauth_challenges {
        uuid id PK
        uuid account_id FK
        uuid session_id FK
        bytes sha256_token_hash UK
        enum provider
        enum scope
        timestamp expires_at
        timestamp consumed_at
    }
    account_deletion_jobs {
        uuid id PK
        uuid account_id FK
        uuid community_actor_id
        enum state
        int attempt_count
        string allowlisted_error_code
        timestamp next_attempt_at
    }
    account_nickname_jobs {
        uuid id PK
        uuid account_id FK,UK
        uuid community_actor_id
        string nickname
        enum state
        int attempt_count
        string allowlisted_error_code
        timestamp next_attempt_at
    }
    account_nickname_claims {
        uuid id PK
        uuid account_id FK
        string normalized_nickname UK
        enum state
        timestamp updated_at
    }
    push_devices {
        uuid id PK
        uuid account_id FK
        uuid session_id FK
        bytes aes_gcm_ciphertext
        bytes keyed_fingerprint UK
        enum platform
        timestamp disabled_at
    }
    account_consents {
        uuid id PK
        uuid account_id FK
        enum consent_type
        string disclosure_version
        bool granted
    }
    account_security_events {
        uuid id PK
        uuid account_id FK
        uuid session_id FK
        enum event_type
        enum outcome
        timestamp expires_at
    }
```

`pending_signups` intentionally has no account foreign key. Its installation
binding is established during provider exchange. Nickname completion creates a
provisional `signup_pending` account/session plus one bounded
`signup_completions` row. An exact retry with the same random completion ID,
installation ID, pending bearer, and normalized nickname recovers the same
derived credentials after a lost response; a mismatched retry fails closed.
Those provisional credentials cannot authorize normal account or community
operations until `/v1/auth/signup/confirm` atomically activates the account.
Cancellation or expiry removes the provisional account and its cascade-owned
private records, while a confirmed account remains active. Completion metadata
expires after the seven-day idempotency window.

The completion row stores a keyed request fingerprint, not a recoverable
pending or refresh bearer. The database otherwise stores only hashes of
refresh, pending-signup, and reauthentication tokens. FCM values are randomized
AES-GCM ciphertext plus a keyed deduplication fingerprint; no phone model or
hardware ID is present.

The real account UUID and private provider profile never cross into community.
The random `community_actor_id` and current nickname are the only account-owned
authorship claims. Deletion jobs retain that actor only until community cleanup
is acknowledged, then the account relationship becomes null and only the
minimal non-personal outcome remains.

The community/training/telemetry stores are deliberately outside this diagram:

- community holds account actor/nickname for new signed writes and nullable
  read-only legacy pseudo identity for old rows;
- training uses random sample IDs and receipt hashes with no account, actor,
  installation, scan, IP, device, or community foreign key;
- telemetry uses only bounded technical dimensions and a coarse day, with no
  stable subject.
