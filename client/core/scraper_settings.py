"""
core/scraper_settings.py

Persisted configuration for the real Google-Maps scraper engine
(core/scraper_engine.py). Lives as its own small module so both the
Settings > Web Scraper admin tab and SearchLeadsPage's "New Search" flow
can read/write the same config without importing Qt widgets into each
other.

Phase 7 split this in two:

  - Machine-only fields (chrome_executable, user_data_dir,
    chrome_debug_port) describe *this* computer, not an account, so
    they stay local -- plain JSON on disk at SCRAPER_CONFIG_PATH, same
    as before.
  - Everything else (scroll/timeout/concurrency tuning, headless,
    close_after_run, phone_default_region, email enrichment tunables)
    now lives server-side (see server/app/routers/scraper_settings.py):
    a private config per admin, plus one shared config every plain
    `user` account's New Search reads. core/api_client.py's
    get_scraper_settings/get_my_admin_scraper_settings/
    update_my_admin_scraper_settings/get_global_scraper_settings/
    update_global_scraper_settings wrap those endpoints.

SCRAPER_CONFIG_PATH now holds the local machine fields plus a cached
copy of the last-known-good synced values, so a New Search that can't
reach the server (offline, server hiccup) still has *something*
reasonable to run with instead of hard-failing.
"""

import json
import os

from core.scraper_engine import detect_chrome_executable, detect_default_profile_dir, find_free_port
from core.api_client import ApiError, get_scraper_settings

SCRAPER_CONFIG_PATH = os.path.expanduser("~/.sinulead_scraper_config.json")

# The 11 fields that live server-side now (AdminScraperSettings /
# GlobalScraperSettings columns, minus id) -- used as the fallback
# defaults when there's no cached server response yet at all (e.g.
# first run on a machine, offline, before it's ever reached the
# server successfully).
_DEFAULT_SYNCED_CONFIG = {
    "max_scrolls_per_query": 30,
    "scroll_wait_ms": 1000,
    "max_concurrent_tabs": 3,
    "query_timeout_seconds": 120,
    "stale_rounds_threshold": 5,
    "headless": False,
    "close_after_run": True,
    "phone_default_region": "PH",
    "enable_email_enrichment": False,
    "email_max_concurrent_tabs": 5,
    "website_timeout_seconds": 15,
}


def default_local_config() -> dict:
    """The 3 machine-only fields, freshly auto-detected. Never synced
    server-side -- syncing them would just make them wrong on someone
    else's machine."""
    return {
        "chrome_debug_port": 9222,
        "chrome_executable": detect_chrome_executable(),
        "user_data_dir": detect_default_profile_dir(),
    }


def _read_disk_cache() -> dict:
    """Raw contents of SCRAPER_CONFIG_PATH, or {} if it doesn't exist
    yet / is unreadable. Shape: {"local": {...}, "server_cache": {...}}."""
    if os.path.exists(SCRAPER_CONFIG_PATH):
        try:
            with open(SCRAPER_CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def load_scraper_config() -> dict:
    """Returns a single flat dict with all 14 fields -- the 11 synced
    ones plus the 3 machine-only ones -- same shape callers got from
    the old all-local config, so New Search / the settings page don't
    need to know where each field actually lives.

    Synced fields come from GET /scraper-settings (scope -- personal
    vs global -- already resolved server-side for whoever's logged
    in). On a network failure, falls back to the last-known-good
    values cached at SCRAPER_CONFIG_PATH from a previous successful
    fetch, and only reaches for _DEFAULT_SYNCED_CONFIG if there's no
    cache yet either (e.g. first run, never been online).

    Machine-only fields come from whatever's saved locally on disk;
    freshly auto-detected via default_local_config() if this is the
    first run on this machine.
    """
    disk = _read_disk_cache()

    try:
        synced = get_scraper_settings()
        synced = {k: v for k, v in synced.items() if k in _DEFAULT_SYNCED_CONFIG}
        # Remember this as the new last-known-good cache for next time.
        disk["server_cache"] = synced
        try:
            _write_disk_cache(disk)
        except Exception:
            pass  # caching is best-effort -- don't block a New Search over a disk write failure
    except ApiError:
        synced = dict(_DEFAULT_SYNCED_CONFIG)
        synced.update(disk.get("server_cache") or {})

    cfg = dict(synced)
    cfg.update(default_local_config())
    cfg.update(disk.get("local") or {})
    return cfg


def _write_disk_cache(disk: dict) -> None:
    with open(SCRAPER_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(disk, f, indent=2, ensure_ascii=False)


def save_local_scraper_config(cfg: dict) -> None:
    """Persists just the machine-only fields (chrome_executable,
    user_data_dir, chrome_debug_port) to SCRAPER_CONFIG_PATH, leaving
    any cached server values already on disk untouched. The synced
    fields are saved separately by callers via
    update_my_admin_scraper_settings/update_global_scraper_settings
    (core/api_client.py) -- that split happens in the UI layer, since
    only it knows which scope is being edited.
    """
    disk = _read_disk_cache()
    local = dict(disk.get("local") or {})
    local.update({k: v for k, v in cfg.items() if k in ("chrome_executable", "user_data_dir", "chrome_debug_port")})
    disk["local"] = local
    _write_disk_cache(disk)


def redetect_chrome(cfg: dict) -> dict:
    """Refreshes the auto-detected fields in-place and returns cfg."""
    cfg["chrome_executable"] = detect_chrome_executable()
    cfg["user_data_dir"] = detect_default_profile_dir()
    cfg["chrome_debug_port"] = find_free_port(preferred=cfg.get("chrome_debug_port") or 9222)
    return cfg
