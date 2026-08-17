# SinuLead (prototype)

Lead-gen style dashboard prototype built with PySide6.

## Project structure

```
leadscout/
├── main.py                    # entry point - run this
├── requirements.txt
├── core/
│   ├── config.py               # colors, QSS themes, constants, table column layout
│   ├── theme_state.py          # live light/dark mode state
│   ├── maintenance_state.py    # maintenance-mode toggle state
│   └── models.py                # pure logic: contact-unlock cost rules
├── data/
│   ├── accounts.py              # ACCOUNTS (login db)
│   ├── leads.py                  # sample business/lead datasets, saved searches
│   ├── users.py                   # users-page mock data
│   ├── billing.py                  # billing mock data
│   ├── rewards.py                   # rewards mock data
│   ├── transactions.py               # transactions mock data
│   ├── exports.py                     # exports mock data
│   └── overview_stats.py               # dashboard/overview stats
├── ui/
│   ├── components/
│   │   └── widgets.py            # shared reusable widgets (status_badge, dash_card,
│   │                              # StatCard, pill, DonutChartWidget, etc.)
│   ├── dialogs/
│   │   └── dialogs.py             # every popup: login, signup, OTP, profile, filters...
│   └── pages/
│       ├── dashboard.py            # the main window (sidebar + table + panels)
│       ├── detail_panel.py          # right-hand "Business Details" panel
│       ├── overview.py
│       ├── search_leads.py
│       ├── billing.py
│       ├── earn_credits.py
│       ├── exports.py
│       ├── maintenance_screen.py
│       ├── maintenance_tab.py
│       ├── packages_page.py
│       ├── rewards_page.py
│       ├── settings_page.py
│       ├── transactions.py
│       └── users_page.py
└── assets/
    ├── logo.png                # optional - drop your own square logo here
    ├── gcash.png / gcash.jpeg
    ├── maya.png / MAYA-Mint Green.png
    └── paypal.png
```

## Setup (first time only)

Open a terminal in this folder, then:

```bash
python -m venv .venv
```

Activate the virtual environment:
- **Windows (PowerShell):** `.venv\Scripts\Activate.ps1`
- **Windows (cmd):** `.venv\Scripts\activate.bat`
- **macOS / Linux:** `source .venv/bin/activate`

Install dependencies:

```bash
pip install -r requirements.txt
```

## Run

```bash
python main.py
```

## Login

Two accounts are seeded in memory (see `data/accounts.py` -> `ACCOUNTS`):

| Email               | Password      | Role  |
|---------------------|---------------|-------|
| user@gmail.com      | userpassword  | user  |
| admin@gmail.com     | adminpassword | admin |

You can also click "Sign Up" to create a new account -- the OTP step
always accepts `000000`. Everything is in-memory only and resets when
you close the app.

## Adding a new tab (e.g. Billing)

1. In `ui/pages/dashboard.py`, find `nav_items_top` inside
   `build_sidebar()` -- that's the list of sidebar buttons.
2. Decide how you want to swap views: the simplest approach is to put
   the main content area (`build_main()`'s return value) inside a
   `QStackedWidget`, add a new page file under `ui/pages/` (e.g.
   `ui/pages/billing_tab.py`) that builds a `QWidget`, and switch pages
   when a nav button is clicked.
3. Keep each new tab's build logic in its own file once it grows past
   ~100 lines, the same way `ui/pages/detail_panel.py` was split out.

## Notes

- All contact info (phone numbers, websites, emails) in `data/leads.py`
  is placeholder data. Swap `SAMPLE_DATA_*` / `generate_dummy_businesses`
  for real API calls once you have a backend.
- Credits, unlock state, and accounts are all in-memory only -- nothing
  persists between runs yet.
- `~/.sinulead_scraper_config.json` (see `core/scraper_settings.py`) no
  longer holds the full scraper config. Since Phase 7 it only holds the
  3 machine-local Chrome fields (`chrome_executable`, `user_data_dir`,
  `chrome_debug_port`) plus a best-effort local cache of the last
  server-fetched values, used only as a fallback if a New Search can't
  reach the server. Everything else (scroll/timeout/concurrency tuning,
  headless, close_after_run, phone_default_region, email enrichment
  tunables) now lives server-side -- an admin's own config, or the
  shared config every plain `user` account's search reads -- and is
  edited from Settings > Web Scraper's "My Settings" / "Default for
  Users" tabs.

