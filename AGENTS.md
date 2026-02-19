# Coding Agent Rules (venom-module-brand-studio)

This file defines coding-agent workflow for this module repository.

## Required gates
Before marking work as done, run:
1. `make pr-fast`
2. `make check-new-code-coverage`

If any gate fails:
1. do not mark task as done,
2. fix issues,
3. rerun both gates until green.

## Scope boundaries
1. Keep business logic inside this module repo.
2. Do not add module-specific logic into Venom core.
3. Integration with Venom core must happen through:
   - `module.json`,
   - env flags,
   - optional module registry contract.

## Testing expectations
1. Add or update unit tests for every behavioral change.
2. Keep API routes covered by tests (`tests/test_routes.py` style).
3. Keep `module.json` contract validated by tests.

## Final summary format
Include:
1. commands executed,
2. pass/fail status,
3. changed-lines coverage (or explain if unavailable),
4. known risks/skips with reason.
