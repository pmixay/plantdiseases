# Account Beta Private Schema

The authoritative definitions are Alembic migrations `0001` and `0002` plus
`account-server/app/db/models.py`. This diagram is a handoff view, not a second
schema source.

```mermaid
erDiagram
    accounts ||--o{ account_identities : owns
    accounts ||--o{ sessions : opens
    sessions ||--o{ refresh_tokens : rotates
    refresh_tokens o|--o| refresh_tokens : replaces
    accounts ||--o{ reauth_challenges : authorizes
    sessions ||--o{ reauth_challenges : scopes
    accounts ||--o| account_deletion_jobs : queues
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
        timestamp expires_at
        timestamp consumed_or_cancelled_at
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

`pending_signups` intentionally has no account foreign key: a new identity does
not become an account or receive a normal session until nickname reservation
succeeds. The database stores only hashes of refresh, pending-signup, and
reauthentication tokens. FCM values are randomized AES-GCM ciphertext plus a
keyed deduplication fingerprint; no phone model or hardware ID is present.

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
