# SinuLead — Progress

## Phase 7 — Server-Side Scraper Settings

**Goal.** Scraper config currently lives only on disk on each machine
(`~/.sinulead_scraper_config.json`, see `client/core/scraper_settings.py`),
read fresh by `settings_page.py`'s Web Scraper tab and by `search_leads.py`
on every "New Search". Move it server-side with two separate scopes:

- **Personal (per-admin).** Every admin/superadmin gets their own private
  scraper config, used when *they* run a search. Editing it never affects
  any other admin.
- **Global (all users).** A single shared config that every plain `user`
  account's "New Search" reads. Only an admin/superadmin can edit it.

Machine-only facts (`chrome_executable`, `user_data_dir`,
`chrome_debug_port`) stay local — they describe *this* computer, not an
account, so syncing them server-side would just make them wrong on
someone else's machine. Everything else (scroll/timeout/concurrency
tuning, headless, close_after_run, phone_default_region, email
enrichment tunables) moves server-side.

Check items off `[x]` as each one lands, in order — later steps assume
earlier ones are done and merged.

### 7a — DB layer (`server/app/db.py`)

- [x] Add `AdminScraperSettings` model: `id` PK, `user_id` (FK → users.id,
      unique, one row per admin), plus the syncable fields from
      `default_scraper_config()` (`max_scrolls_per_query`,
      `scroll_wait_ms`, `max_concurrent_tabs`, `query_timeout_seconds`,
      `stale_rounds_threshold`, `headless`, `close_after_run`,
      `phone_default_region`, `enable_email_enrichment`,
      `email_max_concurrent_tabs`, `website_timeout_seconds`).
- [x] Add `GlobalScraperSettings` model — same columns, single-row
      table (`id`, always `1`), same "one row, admin-editable" shape as
      `PricingSettings`/`MaintenanceState`.
- [x] `_admin_scraper_to_dict(row)` / `_global_scraper_to_dict(row)`
      helpers (same pattern as `_pricing_to_dict`).
- [x] `get_admin_scraper_settings(user_id)` — get-or-create: if no row
      exists yet for this admin, insert one seeded from the same
      defaults `default_scraper_config()` used, then return it.
- [x] `update_admin_scraper_settings(user_id, **fields)` — partial
      update, get-or-create first, same calling convention as
      `update_pricing_settings`.
- [x] `get_global_scraper_settings()` — reads the id=1 row.
- [x] `update_global_scraper_settings(**fields)` — partial update on
      the id=1 row.
- [x] `get_effective_scraper_settings(user: dict)` — the single helper
      the "run a search" path calls: returns the caller's own
      `AdminScraperSettings` row if `role` is `admin`/`superadmin`,
      otherwise `GlobalScraperSettings`. This is what makes "admin has
      separate settings, but also sets the settings users get" actually
      true at read time.
- [x] Seed `GlobalScraperSettings(id=1, ...)` from
      `default_scraper_config()`'s values inside `init_db()`, same spot
      `PricingSettings`/`MaintenanceState` get seeded — only on a
      brand-new DB, never touches an existing row.
- [x] Checkpoint: fresh DB boots, `GlobalScraperSettings` row `id=1`
      exists with the same defaults the old JSON file used to have.

### 7b — Schemas (`server/app/schemas.py`)

- [x] `ScraperSettingsOut` — the 11 syncable fields + `id`.
- [x] `UpdateScraperSettingsRequest` — same 11 fields, all `| None`,
      partial-update shape (mirrors `UpdatePricingSettingsRequest`).
      One schema, reused for both the personal and global PATCH bodies.
- [x] Checkpoint: `UpdateScraperSettingsRequest().model_dump()` with
      nothing set round-trips to an all-`None` dict (nothing accidentally
      gets overwritten by an empty PATCH).

### 7c — Router (new `server/app/routers/scraper_settings.py`)

- [x] `GET /scraper-settings` — `Depends(get_current_user)`, any signed-in
      role. Calls `get_effective_scraper_settings(current_user)`. This is
      what `search_leads.py`'s New Search flow calls — one endpoint,
      correct scope resolved server-side regardless of who's asking.
- [x] `GET /admin/scraper-settings/me` — `Depends(require_admin)`, returns
      the caller's own `AdminScraperSettings` (get-or-create).
- [x] `PATCH /admin/scraper-settings/me` — `Depends(require_admin)`,
      partial update on the caller's own row via
      `update_admin_scraper_settings(current_admin["id"], **fields)`.
- [x] `GET /admin/scraper-settings/global` — `Depends(require_admin)`,
      returns the current global row (so the "Default for Users" tab has
      something to load).
- [x] `PATCH /admin/scraper-settings/global` — `Depends(require_admin)`,
      partial update via `update_global_scraper_settings(**fields)`.
- [x] Register `scraper_settings.router` in `server/app/main.py` next to
      `pricing.router`.
- [x] Checkpoint: as a `user`-role token, `GET /scraper-settings` returns
      the global row's values; as an `admin`-role token it returns that
      admin's own row; a `user`-role token gets `403` on every
      `/admin/scraper-settings/*` route.

### 7d — Client core (`client/core/scraper_settings.py`)

- [x] Add `get_scraper_settings()`, `get_my_admin_scraper_settings()`,
      `update_my_admin_scraper_settings(fields)`,
      `get_global_scraper_settings()`, `update_global_scraper_settings(fields)`
      to `client/core/api_client.py`, following the existing
      `list_my_referrals`/`get_pricing`-style thin wrapper pattern
      (same base URL + auth header handling already in that file).
- [x] Rework `default_scraper_config()` into two pieces: a small
      `default_local_config()` (just the 3 machine-only fields, still
      auto-detected via `scraper_engine`) and the rest now coming from
      the server.
- [x] `load_scraper_config()` — calls `get_scraper_settings()` for the
      synced fields, merges in `default_local_config()`/whatever's saved
      locally for the machine-only fields. On a network failure, fall
      back to the last-known-good server values cached at
      `SCRAPER_CONFIG_PATH` (don't hard-fail New Search just because the
      settings fetch timed out) plus a locally-cached copy of the last
      values it did fetch.
- [x] `save_scraper_config(cfg)` → split into
      `save_local_scraper_config(cfg)` (machine-only fields, still plain
      JSON on disk) and callers explicitly choosing
      `update_my_admin_scraper_settings` vs `update_global_scraper_settings`
      for the synced fields (this split happens in the UI layer, 7e,
      since only the UI knows which scope the admin is editing).
- [x] `redetect_chrome(cfg)` stays as-is — purely local-machine logic.
- [x] Checkpoint: `load_scraper_config()` on a machine with no local file
      yet still returns a complete, valid config (server values +
      freshly auto-detected Chrome fields).

### 7e — Settings page UI (`client/ui/pages/settings_page.py`)

- [x] Add a small segmented control / two sub-tabs at the top of the
      existing "Web Scraper" admin tab: **"My Settings"** (default) and
      **"Default for Users"**.
- [x] "My Settings" = today's full form (Behaviour, Chrome/Browser,
      Enrichment, Output) — Chrome/Browser card only ever shown here,
      never under "Default for Users". Save button calls
      `update_my_admin_scraper_settings`.
- [x] "Default for Users" = same Behaviour/Enrichment/Output cards minus
      the Chrome/Browser card (nothing machine-specific to set for other
      people's machines). Save button calls
      `update_global_scraper_settings`. Add one line of hint text: "These
      are the settings every non-admin account's searches will run with."
- [x] Load state: `_build_scraper_settings_page()` now needs data from
      the network before it can render values — show existing widgets
      disabled with a brief loading state until `load_scraper_config()`
      / `get_global_scraper_settings()` resolves, instead of assuming an
      instant local read like before.
- [x] Checkpoint: Admin A edits and saves "My Settings" → logs out, Admin
      B logs in → Admin B's "My Settings" are unaffected (still their
      own prior values, or defaults if first time). Either admin editing
      "Default for Users" and saving → the other admin sees the updated
      values under their own "Default for Users" tab (it's the one
      shared global row).

### 7f — New Search flow (`client/ui/pages/search_leads.py`)

- [x] Replace the direct `cfg = load_scraper_config()` local-file read
      with the same `load_scraper_config()` function now backed by 7d
      (no call-site change needed here beyond confirming it still
      returns a plain dict synchronously) — this is what makes a plain
      `user`'s search actually pick up the admin-maintained global
      config, and an admin's own search pick up their personal one.
- [x] Checkpoint: as a `user` role, run New Search, confirm the scrape
      behaves per the current "Default for Users" values (e.g. change
      `max_scrolls_per_query` server-side, confirm the next run respects
      it without touching that user's machine at all).

### 7g — Cleanup

- [x] One-time note in `server/README.md` / `client/README.md` that
      `~/.sinulead_scraper_config.json` now only holds machine-local
      Chrome fields (+ a cache), not the full config — avoids confusion
      for anyone who goes looking at that file expecting the old shape.
- [x] Sanity pass: no remaining code path reads the old full local
      config as the source of truth for behaviour fields (grep for
      `load_scraper_config` / `save_scraper_config` call sites once more
      after 7d–7f land).