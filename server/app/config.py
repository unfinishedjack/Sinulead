"""
app/config.py

Central settings, read once from environment / .env. The one thing to
know here: DATABASE_URL is what makes "SQLite for local dev, Postgres
for prod" just a swapped connection string instead of a code change --
SQLAlchemy's create_engine() takes either transparently (see
app/database.py).
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./sinulead.db"

    jwt_secret: str = "change-me-in-prod"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24  # 1 day

    # Phase 8 -- signup OTP delivery (see app/otp.py). Moved here from the
    # desktop client's own .env: the client never sees these values now,
    # it only ever calls POST /auth/request-otp / /auth/verify-otp. All
    # optional -- leave smtp_host blank and OTP codes get printed to this
    # server's console instead of emailed, same dev-fallback behavior the
    # old client-side version had (see otp.py's send_otp_email).
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_tls: bool = True


settings = Settings()

# Ported straight from the desktop app's core/config.py -- db.py's
# create_user()/try_reward_referral() reference these by name.
REFERRAL_BONUS_CREDITS = 500
REFERRAL_PROGRAM_ENABLED = True

# Phase 3 -- flat credit cost to start one search (POST /searches/start).
# There's no existing per-search price anywhere in the desktop app today
# (only unlocking a contact's phone/email costs credits -- see
# core/models.py's business_contact_cost/bulk_unlock_cost); this is a
# new, separate charge for running the scraper itself. Picked a
# placeholder default here rather than hardcoding it in db.py so it's
# one line to retune once real usage numbers exist.
SEARCH_COST = 0

# Phase 4 -- flat credit cost per contact field unlocked (POST
# /leads/{id}/unlock). Mirrors the desktop app's existing pricing rule
# in core/models.py (business_contact_cost/business_field_cost): N
# credits per field (phone or email) that exists and isn't unlocked yet,
# 0 for a field the lead doesn't have or that's already unlocked. Kept
# as its own constants (not reused from SEARCH_COST) since the two are
# priced independently and were already independently priced client-side.
#
# Split into separate email/phone constants so an admin can price the
# two fields differently (e.g. email worth more than phone) instead of
# one shared "field cost" applying to both -- see PricingSettings'
# lead_unlock_email_cost/lead_unlock_phone_cost columns in db.py, which
# is what's actually admin-editable at runtime; these constants only
# seed a brand-new deployment's first row.
LEAD_UNLOCK_EMAIL_COST = 1
LEAD_UNLOCK_PHONE_COST = 1

# Phase 7 -- the 11 syncable scraper-behaviour fields, moved server-side
# out of client/core/scraper_settings.py's default_scraper_config().
# Deliberately excludes the 3 machine-only fields (chrome_executable,
# user_data_dir, chrome_debug_port) -- those stay local per-machine, see
# PROGRESS.md Phase 7's intro. Used to seed both AdminScraperSettings
# (per-admin, get-or-create on first read) and the single-row
# GlobalScraperSettings in db.py, same values the old JSON default had
# so nothing changes behaviorally on upgrade.
DEFAULT_SCRAPER_SETTINGS = {
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