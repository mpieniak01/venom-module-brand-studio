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

## Integrating with local Venom workspace
In `/home/ubuntu/venom/.env`:

```bash
API_OPTIONAL_MODULES=manifest:/home/ubuntu/venom/modules/venom-module-brand-studio/module.json
FEATURE_BRAND_STUDIO=true
NEXT_PUBLIC_FEATURE_BRAND_STUDIO=true
BRAND_STUDIO_DISCOVERY_MODE=hybrid
BRAND_STUDIO_RSS_URLS=https://example.org/feed.xml,https://example.org/another-feed.xml
GITHUB_TOKEN_BRAND=<token>
BRAND_TARGET_REPO=mpieniak01/mpieniak01
BRAND_GITHUB_PUBLISH_MODE=commit
BRAND_GITHUB_BASE_BRANCH=main
```

After changing env values, restart Venom services.

## CI
- GitHub Actions workflow: `.github/workflows/ci.yml`
- Required checks:
  - `make pr-fast`
  - `make check-new-code-coverage`
