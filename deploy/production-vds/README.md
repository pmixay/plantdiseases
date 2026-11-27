# Production on the existing VDS (`pdis.arbuz.lol`)

Operator files used to build and run the production account/app stack beside the old service, exactly as deployed on
2026-10-09. They are VDS-specific (paths under `/opt/pd-account-prod` and `/etc/pd-account-prod`) and contain no secrets;
the env file, keys and credentials live only on the host. This is not application code and not part of the
Android release gates. It does not replace `deploy/account-beta-test/`.

| File | Purpose |
|---|---|
| `compose.production.yml` | Production stack: PostgreSQL (`account_*` roles), role provisioning, migrations, account API, app API. Loopback ports only, read-only roots, dropped capabilities, bounded resources and logs. |
| `compose.operator.yml` | One-shot, network-less key provisioning service (profile `provision-keys`). |
| `compose.build.yml` | Build overlay; only the account API service carries the build so the migrate service reuses its tag. |
| `compose.sh` | Wrapper: `compose.sh build-images`, otherwise passes arguments to `docker compose` with the recorded env file. |
| `provision_keys.py` | Writes the signing key, encryption key and internal token once (never overwrites). The repo's `generate_test_keys` stays test-only. |
| `postgres-init/10-account-roles.sh` | Role script for production names. It is the `account-beta-test` script with a pinned bootstrap role: `REASSIGN OWNED BY account_bootstrap` is rejected by PostgreSQL on a fresh database. |
| `migrate_legacy.sh` | Final copy of the old service's community and push databases into the production volumes (only while the targets are still empty). The old service is only read. |
| `nginx/` | Routing for `/id/` and the internal-route blocks, the rate-limit zone, `pd-privacy.conf` (serves the repo-root `privacy.html` from `/var/www/pdis` at `/privacy.html`; included in the site's TLS server block ahead of `location /` — re-copy `privacy.html` whenever it changes), `switch.sh` (nginx config copy, legacy copy, repoint `/`, smoke tests, automatic rollback) and `rollback.sh`. |

Order used: `compose.sh build-images`, write the env file, `compose.sh run --rm --no-deps prod-keys-provision`,
`compose.sh up -d --no-build`, verify, `nginx/switch.sh`.

Decisions baked in: Yandex only; email/password, VK, RuStore and PlantNet budget off; `/id/` is public behind its rate
limit; legacy feedback/labeling/telemetry data is not migrated (the new code has no feedback or labeling store, and its
telemetry store is the new minimized one). Consented error reports are recorded (`TELEMETRY_ENABLED: "true"`, since
2026-11-26; the stack was cut from the staging file with it off, so the app's opted-in reports were dropped until then).
`/run/app-config/config.json` wins over the compose environment, so it must not set `telemetry_enabled` to false.

AI features (doctor chat, species AI sections, care-plan polish) call an OpenAI-compatible backend, a codex-lb gateway
(since 2026-11-27; OpenRouter is gone). The env file sets `PROD_LLM_BASE_URL` (blank keeps them off) and
`PROD_LLM_KEY_FILE`, the absolute path of an owner-only file in `PROD_APP_SECRETS_DIR` (mounted at `/run/app-secrets`)
holding a codex-lb API key. codex-lb refuses non-local clients until its API-key auth is on, so turn that on in its
dashboard first. The app sits on `prod-edge`: attach the codex-lb container to that network and use
`http://<codex-lb service>:2455/v1`, or have codex-lb listen on the edge gateway address and use that. Remove any
`openrouter_*` keys from `config.json` (they are ignored, with a startup warning).

There is no backup job (owner's decision, 2026-11-14): the daily encrypted dump, its restore check and its systemd timer
were removed. The read-only `account_backup` database role and `PROD_POSTGRES_BACKUP_PASSWORD` are still provisioned by
`postgres-init/` and the compose file; nothing uses them.
