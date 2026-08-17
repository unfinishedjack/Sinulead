"""
data/__init__.py

Re-exports every name the old flat data.py used to expose, so existing
`from data import ACCOUNTS` (etc.) call sites across the app keep working
completely unchanged. This is the Phase 2f cutover: the moment this file
exists, `import data` / `from data import X` resolves to this package
instead of the old data.py (regular modules and packages can't coexist
under the same name -- see data/accounts.py's docstring, Phase 2a, for
why data/*.py sat inert as plain files until now).

See PROGRESS.md Phase 2 for the full history of how data.py (774 lines,
mixing auth/leads/overview/billing/transactions/users/exports/rewards
domains) was split into these one-domain-per-file modules:
  - data/accounts.py        -- historical note only, see its own
                                docstring (ACCOUNTS/DEFAULT_OTP both
                                deleted in Phase 6 -- users now live in
                                data/db.py, OTP now lives in
                                server/app/otp.py as of Phase 8)
  - data/leads.py            -- SEARCHES, generate_dummy_businesses
  - data/overview_stats.py   -- Overview/Dashboard tab mock data
  - data/billing.py          -- Payment methods, packages, invoices
  - data/transactions.py     -- Admin "Payment History" table
  - data/users.py            -- Admin "Users" tab roster
  - data/exports.py          -- Exports tab history + format reference

The old flat data.py is deleted as part of this same step.

Note: data/rewards.py (rewards catalog + Earn Credits seed data) was
later deleted in PROGRESS_REWARDS.md Phase 4 -- once rewards_page.py and
earn_credits.py both read/write the real `rewards` table (data/db.py)
instead, its REWARDS/EARN_CREDITS_SECTIONS/TOTAL_CREDITS_EARNED
constants had no readers left. The one-time DB seed data that used to
live there moved into data/db.py's _SEED_REWARDS (init_db() is its only
caller, so it made more sense living next to the table it seeds).
"""

from data.leads import (
    SEARCHES,
    generate_dummy_businesses,
)
from data.overview_stats import (
    DASHBOARD_STATS,
    LEADS_OVER_TIME,
    LEADS_BY_CATEGORY,
    TOP_RATED,
    LEAD_SOURCES,
    RECENT_EXPORTS,
    LEADS_BY_RATING,
    RECENT_ACTIVITY,
    SEARCH_PERFORMANCE,
)
from data.billing import (
    PAYMENT_METHODS,
    PAYMENT_SETTINGS,
    CREDIT_PACKAGES,
    BILLING_TRANSACTIONS,
    INVOICES,
)
from data.transactions import TRANSACTIONS
from data.users import USERS
from data.exports import FILE_FORMATS, EXPORT_TIPS, EXPORT_GUIDE_SECTIONS

__all__ = [
    "SEARCHES", "generate_dummy_businesses",
    "DASHBOARD_STATS", "LEADS_OVER_TIME", "LEADS_BY_CATEGORY", "TOP_RATED",
    "LEAD_SOURCES", "RECENT_EXPORTS", "LEADS_BY_RATING", "RECENT_ACTIVITY",
    "SEARCH_PERFORMANCE",
    "PAYMENT_METHODS", "PAYMENT_SETTINGS", "CREDIT_PACKAGES",
    "BILLING_TRANSACTIONS", "INVOICES",
    "TRANSACTIONS",
    "USERS",
    "FILE_FORMATS", "EXPORT_TIPS", "EXPORT_GUIDE_SECTIONS",
]