# Production on the existing VDS (`pdis.arbuz.lol`)

Operator files used to build and run the production account/app stack beside the old service, as deployed on
2026-10-09. They are VDS-specific (paths under `/opt/pd-account-prod` and `/etc/pd-account-prod`) and contain no secrets;
the env file, keys and credentials live only on the host. This is not application code and not part of the
Android release gates. It does not replace `deploy/account-beta-test/`.

## What runs on the host (checked 2026-11-28)

**The host differs from the files here.** Check it before following any `compose.sh` command below.

- **App API:** not a container. It is the systemd unit `plantdiseases.service` (`uvicorn` on `127.0.0.1:8000`, user
  `plantd`), run from `/opt/plantdiseases`; `/opt/plantdiseases/RELEASE_SHA` records its commit. Its config is
  `/etc/plantdiseases/config.json`, and its stores are set under `/var/lib/plantdiseases/`.
- **Compose:** the installed stack defines only `prod-postgres`, `prod-account-role-provision`,
  `prod-account-migrate` and `prod-account-api`. There is no `prod-app-api`.
- **Wrapper:** the installed `/opt/pd-account-prod/deploy/compose.sh` takes the release from
  `/opt/pd-account-prod/RELEASE_SHA`, passes every argument to `docker compose` and has no `build-images` command.
- **Forum roles:** the account image does not contain `manage_roles.py` (the Dockerfile does not copy it). Pipe the
  running release's copy into the container (`list`, `grant` or `revoke`):

  ```bash
  /opt/pd-account-prod/deploy/compose.sh exec -T prod-account-api python -B - list \
    < /opt/pd-account-prod/releases/$(cat /opt/pd-account-prod/RELEASE_SHA)/account-server/manage_roles.py
  ```

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
telemetry store is the new minimized one). Consented error reports are enabled: on this host the app API's
`config.json` sets `telemetry_enabled` to true. The file wins over the environment, so it must not set it to false.
(`compose.production.yml` sets `TELEMETRY_ENABLED: "true"` for its `prod-app-api` service.)

AI features (doctor chat, species AI sections, care-plan polish) call an OpenAI-compatible backend, a codex-lb gateway
(since 2026-11-27; OpenRouter is gone). On this host the app API's `config.json` sets `llm_base_url`
(`http://127.0.0.1:2455/v1`; blank keeps the features off) and `llm_api_key_file`, the absolute path of an owner-only
file holding a codex-lb API key. codex-lb refuses non-local clients until its API-key auth is on, so turn that on in
its dashboard first. Remove any `openrouter_*` keys from `config.json` (they are ignored, with a startup warning).
(For its `prod-app-api` service, `compose.production.yml` takes `PROD_LLM_BASE_URL` and `PROD_LLM_KEY_FILE`, a file
in `PROD_APP_SECRETS_DIR`; that container would reach codex-lb over `prod-edge`, not loopback.)

There is no backup job (owner's decision, 2026-11-14): the daily encrypted dump, its restore check and its systemd timer
were removed. The read-only `account_backup` database role and `PROD_POSTGRES_BACKUP_PASSWORD` are still provisioned by
`postgres-init/` and the compose file; nothing uses them.
