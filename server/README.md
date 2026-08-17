# sinulead-server

Standalone FastAPI backend for SinuLead. Lives separately from `client/`
(the desktop app) — the two only talk over HTTP once auth/credits/leads
are migrated here.

## Scraper settings

`app/routers/scraper_settings.py` owns the scraper's tunable config
(scroll/timeout/concurrency, headless, close_after_run,
phone_default_region, email enrichment settings). Two scopes:

- `AdminScraperSettings` — one private row per admin/superadmin
  (`GET`/`PATCH /admin/scraper-settings/me`).
- `GlobalScraperSettings` — a single shared row (`id=1`) every plain
  `user` account's search uses (`GET`/`PATCH /admin/scraper-settings/global`,
  admin-only).

`GET /scraper-settings` resolves the caller's effective scope
server-side (their own row if admin/superadmin, else the global row) —
this is the one endpoint the client's New Search flow calls.

Note this does **not** include `chrome_executable`, `user_data_dir`, or
`chrome_debug_port` — those describe one specific machine, not an
account, so they're never synced here; the client keeps them in its own
local `~/.sinulead_scraper_config.json` (see `client/README.md`).
