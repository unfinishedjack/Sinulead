"""
core/__init__.py

Groups the app's cross-cutting, non-page infrastructure modules together:
  - core/config.py             -- theme palettes, QSS builder, app-wide
                                   constants (LOGO_PATH, CURRENT_USER_*, etc.)
  - core/theme_state.py        -- THEME_STATE, the live dark/light toggle
  - core/maintenance_state.py  -- MAINTENANCE_STATE, the maintenance-mode flag
  - core/models.py             -- small pure-function helpers (referral
                                   codes, business contact-cost calculations)

Deliberately empty otherwise -- call sites use `from core import config`
(binding the config module itself, so existing `config.PALETTE`-style
dot-access throughout the app keeps working unchanged) or
`from core.config import PALETTE, ...` / `from core.models import ...`
for direct name imports, matching whichever pattern each call site
already used before Phase 3a moved these files here. See PROGRESS.md
Phase 3a.
"""
