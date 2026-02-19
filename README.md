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
BRAND_STUDIO_ALLOWED_USERS=
BRAND_STUDIO_DISCOVERY_MODE=hybrid
BRAND_STUDIO_RSS_URLS=https://example.org/feed.xml,https://example.org/another-feed.xml
BRAND_STUDIO_CACHE_TTL_SECONDS=1800
BRAND_STUDIO_CACHE_FILE=/tmp/venom-brand-studio/candidates-cache.json
BRAND_STUDIO_STATE_FILE=/tmp/venom-brand-studio/runtime-state.json
GITHUB_TOKEN_BRAND=<token>
BRAND_TARGET_REPO=mpieniak01/mpieniak01
BRAND_GITHUB_PUBLISH_MODE=commit
BRAND_GITHUB_BASE_BRANCH=main
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

### Runtime state persistence
1. Queue and audit are persisted in `BRAND_STUDIO_STATE_FILE`.
2. After backend restart, queue and audit entries are restored from local state file.

## CI
- GitHub Actions workflow: `.github/workflows/ci.yml`
- Required checks:
  - `make pr-fast`
  - `make check-new-code-coverage`
