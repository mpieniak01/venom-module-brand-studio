# venom-module-brand-studio

Optional Venom module for personal-brand workflow:
discovery -> scoring -> drafts -> manual publish.

## Scope
- Module owns its backend router, schemas, services, tests, and optional frontend page.
- Venom core only discovers and hosts this module via `module.json` + env flags.
- No product-specific logic should be added to Venom core.

## Repository layout
```text
venom-module-brand-studio/
├─ module.json
├─ pyproject.toml
├─ Makefile
├─ AGENTS.md
├─ venom_module_brand_studio/
│  ├─ __init__.py
│  ├─ api/
│  │  ├─ __init__.py
│  │  └─ routes.py
│  └─ services/
│     ├─ __init__.py
│     └─ service.py
├─ web-next/
│  ├─ page.tsx
│  └─ i18n/
│     ├─ pl.json
│     ├─ en.json
│     └─ de.json
└─ tests/
   ├─ test_manifest.py
   └─ test_routes.py
```

## Local development
1. Activate Python environment (recommended: Venom workspace `.venv`).
2. Run module gates:
   - `make pr-fast`
   - `make check-new-code-coverage`
3. Run explicit tests:
   - `pytest -q`

## Testing only this module
To avoid running tests from the whole Venom core repository, run tests from this module repo:

```bash
cd /home/ubuntu/venom/modules/venom-module-brand-studio
pytest -q
```

Alternative (without `cd`):

```bash
pytest -q /home/ubuntu/venom/modules/venom-module-brand-studio/tests
```

Recommended module-only gates:

```bash
cd /home/ubuntu/venom/modules/venom-module-brand-studio
make pr-fast PYTHON=/home/ubuntu/venom/.venv/bin/python
make check-new-code-coverage PYTHON=/home/ubuntu/venom/.venv/bin/python
```

## Integrating with local Venom workspace
In `/home/ubuntu/venom/.env`:

```bash
API_OPTIONAL_MODULES=manifest:/home/ubuntu/venom/modules/venom-module-brand-studio/module.json
FEATURE_BRAND_STUDIO=true
NEXT_PUBLIC_FEATURE_BRAND_STUDIO=true
BRAND_STUDIO_LLM_ENABLED=false
BRAND_STUDIO_LLM_CORE_BASE_URL=http://127.0.0.1:8000
BRAND_STUDIO_LLM_TIMEOUT_SECONDS=25
BRAND_STUDIO_LLM_MAX_TOKENS=800
BRAND_STUDIO_LLM_TEMPERATURE=0.3
BRAND_STUDIO_LLM_AUTO_START_LOCAL_SERVER=true
BRAND_STUDIO_AUDIT_PUBLISH_ENABLED=true
BRAND_STUDIO_AUDIT_CORE_BASE_URL=http://127.0.0.1:8000
BRAND_STUDIO_AUDIT_TIMEOUT_SECONDS=0.8
BRAND_STUDIO_AUDIT_SOURCE=module.brand_studio
BRAND_STUDIO_AUDIT_INGEST_TOKEN=
BRAND_STUDIO_DRAFT_CACHE_TTL_SECONDS=86400
FEATURE_BRAND_STUDIO_MONITORING=true
BRAND_STUDIO_ALLOWED_USERS=
BRAND_STUDIO_DISCOVERY_MODE=hybrid
BRAND_STUDIO_RSS_URLS=https://example.org/feed.xml,https://example.org/another-feed.xml
BRAND_STUDIO_CACHE_TTL_SECONDS=1800
BRAND_STUDIO_MONITORING_SCHEDULE_CRON=*/30 * * * *
BRAND_STUDIO_MONITORING_FILE=/tmp/venom-brand-studio/monitoring-state.json
BRAND_STUDIO_GOOGLE_CSE_API_KEY=<api-key>
BRAND_STUDIO_GOOGLE_CSE_CX=<search-engine-id>
    BRAND_STUDIO_CACHE_FILE=/tmp/venom-brand-studio/candidates-cache.json
    BRAND_STUDIO_STATE_FILE=/tmp/venom-brand-studio/runtime-state.json
    BRAND_STUDIO_ACCOUNTS_FILE=/tmp/venom-brand-studio/accounts-state.json
    GITHUB_TOKEN_BRAND=<token>
    BRAND_TARGET_REPO=mpieniak01/mpieniak01
    BRAND_GITHUB_PUBLISH_MODE=commit
    BRAND_GITHUB_BASE_BRANCH=main
    DEVTO_API_KEY=<token>
    REDDIT_CLIENT_ID=<client-id>
    REDDIT_CLIENT_SECRET=<client-secret>
    REDDIT_REFRESH_TOKEN=<refresh-token>
    REDDIT_USER_AGENT=venom-brand-studio/1.0 by /u/your-account
```

After changing env values, restart Venom services.

### Discovery cache behavior
1. Candidate discovery results are cached locally in `BRAND_STUDIO_CACHE_FILE`.
2. Reopening the screen does not trigger external APIs while cache is fresh (`BRAND_STUDIO_CACHE_TTL_SECONDS`).
3. After backend restart, cached candidates are restored from the local file.
4. Cache is local-only (not pushed anywhere unless you manually commit that file path in your module repo).

### Access governance
1. Set `FEATURE_BRAND_STUDIO=false` to hard-disable module API endpoints (returns `403`).
2. Set `BRAND_STUDIO_ALLOWED_USERS=user1,user2` to allow only selected actors.
3. Mutating endpoints require authenticated actor header (`X-Authenticated-User` / `X-User` / `X-Admin-User`), otherwise `401`.

### Global API traffic-control inheritance
1. Brand Studio runs under Venom core global traffic-control (anti-spam / anti-ban).
2. Outbound requests to external APIs are guarded by provider/method policies, retries, and circuit breakers.
3. Inbound requests from UI to backend are guarded by endpoint-group policies with actor/session/IP keys and `429` + `Retry-After`.
4. Detailed traffic logs are opt-in in core via:
   - `ENABLE_TRAFFIC_CONTROL_LOGGING=false` (default),
   - `TRAFFIC_CONTROL_LOG_DIR=/tmp/venom/traffic-control`.

### Canonical audit stream publishing
1. Module audit entries are still persisted locally in `BRAND_STUDIO_STATE_FILE`.
2. Each new audit entry is also published (best-effort) to core endpoint `/api/v1/audit/stream`.
3. Queue events for `github` channel are marked as technical (`core.technical.github_publish`) for visibility in core audit.
4. Publishing can be controlled by:
   - `BRAND_STUDIO_AUDIT_PUBLISH_ENABLED=true|false`
   - `BRAND_STUDIO_AUDIT_CORE_BASE_URL=http://127.0.0.1:8000`
   - `BRAND_STUDIO_AUDIT_TIMEOUT_SECONDS=0.8`
   - `BRAND_STUDIO_AUDIT_SOURCE=module.brand_studio`
   - `BRAND_STUDIO_AUDIT_INGEST_TOKEN=<optional token>`

### Runtime state persistence
1. Queue and audit are persisted in `BRAND_STUDIO_STATE_FILE`.
2. After backend restart, queue and audit entries are restored from local state file.
3. Channel accounts and account telemetry are persisted in `BRAND_STUDIO_ACCOUNTS_FILE`.
4. Draft bundles and draft-generation cache are persisted in `BRAND_STUDIO_STATE_FILE`.

### Draft generation cache (LLM stability)
1. Repeated `POST /drafts/generate` with the same input returns cached draft by default.
2. Cached draft is stable (content does not change between page refreshes) until TTL expires.
3. Use `refresh=true` in `DraftGenerateRequest` to force new draft generation.
4. TTL is controlled by `BRAND_STUDIO_DRAFT_CACHE_TTL_SECONDS` (default: 86400s).

### Brand monitoring schedule (161_G)
1. Monitoring API can be disabled independently via `FEATURE_BRAND_STUDIO_MONITORING=false`.
2. Automatic monitoring scans are enabled with one of:
   - `BRAND_STUDIO_MONITORING_SCHEDULE_CRON`:
     - supported values: `@hourly`, `@daily`, `@weekly`, `*/N * * * *`,
   - `BRAND_STUDIO_MONITORING_SCHEDULE_MINUTES` (fallback when CRON is not set).
3. Scheduled scans are triggered lazily on monitoring reads (`/monitoring/summary`, `/monitoring/results`) when interval is due.
4. Monitoring entities (keywords/sources/scans/campaigns/idempotency keys) are persisted in `BRAND_STUDIO_MONITORING_FILE`.

### Channel capability matrix (161_C)
1. `github` / `blog`: real publish connector.
2. `devto`: real publish connector.
3. `reddit`: real publish connector (OAuth refresh token flow).
4. `x`: manual publish marker in MVP.
5. `linkedin`, `medium`, `hf_blog`, `hf_spaces`, `hashnode`: account + test/status + publish connector available.

## CI
- GitHub Actions workflow: `.github/workflows/ci.yml`
- Required checks:
  - `make pr-fast`
  - `make check-new-code-coverage`
