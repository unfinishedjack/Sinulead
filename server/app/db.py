"""
app/db.py

Transplanted from the desktop app's data/db.py (Phase 0, step 3 of the
backend migration) -- this is the "barely changes" part. Every model
(User/MaintenanceState/Reward/UserProgress/UserReward/Referral/
RewardLog) and every function below is the same logic as the original,
byte-for-byte in most functions. The only real edits are in this header:

  - engine/SessionLocal/Base now come from app.database (shared with
    the rest of this backend, DATABASE_URL-driven -- SQLite locally,
    Postgres in prod) instead of this file creating its own SQLite-only
    engine.
  - generate_referral_code / REFERRAL_BONUS_CREDITS /
    REFERRAL_PROGRAM_ENABLED / hash_password / verify_password /
    looks_hashed now come from this backend's own app.referral /
    app.config / app.security modules instead of the desktop app's
    core/ package, so this backend has zero import dependency on
    client/ code.

Password note: hashed with bcrypt (app/security.py) before ever
touching this table -- create_user, update_user, and the seed rows in
init_db() all hash on the way in, and verify_login checks via bcrypt
instead of a plaintext `==`. The `password` column never holds a
plaintext value for any row created after this landed.

Legacy rows: if a sinulead.db from before this change is reused, its
password columns are still plaintext. verify_login() detects that (a
value that doesn't look like a bcrypt hash -- see
app.security.looks_hashed) and, on a successful plaintext match,
transparently rehashes and persists it, so an old install self-heals the
first time each user logs in again instead of needing a manual migration
script.

Returns: every function below returns plain dict(s), not ORM objects.
Callers (API route handlers) shouldn't have to deal with
detached-instance issues just to read a field -- dicts stay valid after
the session that fetched them closes.
"""

from datetime import datetime, timedelta

from sqlalchemy import (
    Column, Integer, String, Boolean, Float, ForeignKey, CheckConstraint,
    UniqueConstraint, select,
)
from sqlalchemy.sql import func

from app.database import engine, SessionLocal, Base
from app.referral import generate_referral_code
from app.config import (
    REFERRAL_BONUS_CREDITS, REFERRAL_PROGRAM_ENABLED, SEARCH_COST,
    LEAD_UNLOCK_EMAIL_COST, LEAD_UNLOCK_PHONE_COST, DEFAULT_SCRAPER_SETTINGS,
)
from app.security import hash_password, verify_password, looks_hashed


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String, nullable=False, unique=True)
    password = Column(String, nullable=False)
    full_name = Column(String, nullable=False)
    role = Column(String, nullable=False, default="user")
    status = Column(String, nullable=False, default="active")
    avatar = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    company = Column(String, nullable=True)
    # Their job title/position (e.g. "Lead Generation Specialist"),
    # NOT the same thing as `role` above. `role` is app-permission level
    # (user/admin -- what gates the admin tab, free contact unlocks,
    # etc). `company_role` is just descriptive profile text with zero
    # effect on permissions -- an admin's company_role could say
    # "Intern" and a plain user's could say "CEO", and neither changes
    # what they're allowed to click in the app.
    company_role = Column(String, nullable=True)
    credits = Column(Integer, nullable=False, default=0)
    spent = Column(Integer, nullable=False, default=0)
    # Sum of this user's currently-held (status='held') CreditReservation
    # rows -- see reserve_credits()'s docstring. Kept denormalized here
    # (rather than SUM()-ing the reservations table on every balance
    # check) so "available balance" is a single cheap `credits -
    # reserved_credits` read, same spirit as `spent` being a running
    # total instead of a derived query.
    reserved_credits = Column(Integer, nullable=False, default=0)
    referral_code = Column(String, unique=True, nullable=True)
    referred_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    pending_bonus_credits = Column(Integer, nullable=False, default=0)
    created_at = Column(String, nullable=False, server_default=func.datetime("now"))
    last_active_at = Column(String, nullable=True)
    # Bumped automatically whenever a row changes (signup edits their own
    # profile, admin edits them in the Users tab, credits get adjusted,
    # etc.) -- separate from created_at (never changes) and last_active_at
    # (bumped by login/activity, not edits).
    updated_at = Column(
        String, nullable=False,
        server_default=func.datetime("now"),
        onupdate=func.datetime("now"),
    )

    __table_args__ = (
        CheckConstraint("role IN ('user', 'admin', 'superadmin')", name="ck_users_role"),
        CheckConstraint(
            "status IN ('active', 'inactive', 'suspended')", name="ck_users_status"
        ),
    )


# The two accounts data/accounts.py currently seeds ACCOUNTS with, so a
# fresh install of the SQLAlchemy version still logs in with the same
# credentials the dict version does.
_SEED_ACCOUNTS = [
    {"email": "superadmin@gmail.com", "password": "adminpassword",
     "full_name": "Super Admin", "role": "superadmin", "credits": 0},
    {"email": "judelfederigan1@gmail.com", "password": "Judelpogisobra1",
     "full_name": "Judel Federigan", "role": "admin", "credits": 0},
    {"email": "judelfederigan11@gmail.com", "password": "Judelpogisobra1",
     "full_name": "Nikola Tesla", "role": "user", "credits": 0},
]

VALID_UPDATE_COLUMNS = {
    "email", "password", "full_name", "role", "status", "avatar",
    "phone", "company", "company_role", "credits", "spent",
    "pending_bonus_credits", "last_active_at",
}


def _to_dict(user: User | None) -> dict | None:
    """ORM object -> plain dict, so callers never hold a User instance
    past the session that fetched it."""
    if user is None:
        return None
    return {c.name: getattr(user, c.name) for c in User.__table__.columns}


class MaintenanceState(Base):
    __tablename__ = "maintenance_state"

    id = Column(Integer, primary_key=True)  # single-row table, always id=1
    enabled = Column(Boolean, nullable=False, default=False)
    title = Column(String, nullable=False, default="We're Currently Under Maintenance")
    description = Column(String, nullable=False,
                          default="We're improving your experience. Please check back later.")
    start_datetime = Column(String, nullable=True)  # ISO 8601 string -- no longer editable in the UI
    end_datetime = Column(String, nullable=True)  # ISO 8601 string -- now computed as now() + duration_minutes when mode is switched on
    duration_minutes = Column(Integer, nullable=False, default=240)  # how long maintenance runs once enabled; drives end_datetime
    allow_admin_access = Column(Boolean, nullable=False, default=True)
    show_countdown = Column(Boolean, nullable=False, default=True)
    countdown_format = Column(String, nullable=False, default="Hours & Minutes")


def _maintenance_to_dict(row: MaintenanceState) -> dict:
    return {c.name: getattr(row, c.name) for c in MaintenanceState.__table__.columns}


def get_maintenance_state() -> dict:
    with SessionLocal() as session:
        return _maintenance_to_dict(session.get(MaintenanceState, 1))


def update_maintenance_state(**fields) -> dict:
    with SessionLocal() as session:
        row = session.get(MaintenanceState, 1)
        for col, value in fields.items():
            setattr(row, col, value)
        session.commit()
        session.refresh(row)
        return _maintenance_to_dict(row)


class PricingSettings(Base):
    """Single-row (id=1) table holding the credit prices that used to be
    hardcoded constants in app/config.py (SEARCH_COST /
    LEAD_UNLOCK_FIELD_COST). Same "one row, admin-editable" shape as
    MaintenanceState above -- init_db() seeds it once from the old
    constants so existing deployments keep their current pricing on
    upgrade, and PATCH /admin/pricing lets an admin retune it afterwards
    without a code change or redeploy.

    export_cost has no old constant to seed from (exports were never
    credit-gated before) -- defaults to 0 (free) on a brand-new row so
    upgrading an existing deployment doesn't suddenly start charging for
    something that used to be free, same "don't change behavior on
    upgrade" reasoning as the other two columns.

    lead_unlock_field_cost (the old single shared price for unlocking
    either a phone or an email) has been split into
    lead_unlock_email_cost / lead_unlock_phone_cost so an admin can
    price the two fields independently -- see the migration in
    init_db(), which adds these two columns and seeds each from
    whatever lead_unlock_field_cost was already set to on an existing
    row (so a deployment that had, say, a 2-credit field cost keeps
    charging 2 for both email and phone until an admin explicitly
    splits them apart in the Pricing tab), or from the
    LEAD_UNLOCK_EMAIL_COST/LEAD_UNLOCK_PHONE_COST constants on a
    brand-new row."""
    __tablename__ = "pricing_settings"

    id = Column(Integer, primary_key=True)  # single-row table, always id=1
    search_cost = Column(Integer, nullable=False, default=SEARCH_COST)
    lead_unlock_email_cost = Column(Integer, nullable=False, default=LEAD_UNLOCK_EMAIL_COST)
    lead_unlock_phone_cost = Column(Integer, nullable=False, default=LEAD_UNLOCK_PHONE_COST)
    export_cost = Column(Integer, nullable=False, default=0)


def _pricing_to_dict(row: PricingSettings) -> dict:
    return {c.name: getattr(row, c.name) for c in PricingSettings.__table__.columns}


def get_pricing_settings() -> dict:
    with SessionLocal() as session:
        row = session.get(PricingSettings, 1)
        return _pricing_to_dict(row)


_PRICING_COST_COLUMNS = ("search_cost", "lead_unlock_email_cost", "lead_unlock_phone_cost", "export_cost")


def update_pricing_settings(**fields) -> dict:
    """Partial update, same calling convention as
    update_maintenance_state(). Raises ValueError("cost_must_not_be_negative")
    if a caller tries to set any cost below 0 -- mirrors start_search's
    own guard so a bad admin input can't silently let searches/unlocks/
    exports run free (or crash later on a negative deduction)."""
    for col, value in fields.items():
        if col in _PRICING_COST_COLUMNS and value is not None and value < 0:
            raise ValueError("cost_must_not_be_negative")

    with SessionLocal() as session:
        row = session.get(PricingSettings, 1)
        for col, value in fields.items():
            setattr(row, col, value)
        session.commit()
        session.refresh(row)
        return _pricing_to_dict(row)


# The 11 syncable scraper-behaviour columns shared by both
# AdminScraperSettings and GlobalScraperSettings below -- kept as one
# tuple so the two models, their to-dict helpers, and their
# get-or-create/update functions can't drift out of sync with each
# other (or with DEFAULT_SCRAPER_SETTINGS' keys) as fields get added.
_SCRAPER_SETTINGS_COLUMNS = tuple(DEFAULT_SCRAPER_SETTINGS.keys())


class AdminScraperSettings(Base):
    """Phase 7 -- one private row per admin/superadmin, holding the
    syncable scraper-behaviour fields (everything in
    DEFAULT_SCRAPER_SETTINGS) that used to live only in the local
    ~/.sinulead_scraper_config.json on whichever machine that admin
    happened to save settings from. Read via
    get_effective_scraper_settings() when *that admin* runs a search;
    editing one admin's row never touches another admin's row or the
    shared GlobalScraperSettings row below.

    Machine-only fields (chrome_executable, user_data_dir,
    chrome_debug_port) are deliberately NOT columns here -- they
    describe the computer running the client, not the admin's account,
    so they stay local-only (see client/core/scraper_settings.py's
    default_local_config(), Phase 7d)."""
    __tablename__ = "admin_scraper_settings"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True)
    max_scrolls_per_query = Column(Integer, nullable=False, default=DEFAULT_SCRAPER_SETTINGS["max_scrolls_per_query"])
    scroll_wait_ms = Column(Integer, nullable=False, default=DEFAULT_SCRAPER_SETTINGS["scroll_wait_ms"])
    max_concurrent_tabs = Column(Integer, nullable=False, default=DEFAULT_SCRAPER_SETTINGS["max_concurrent_tabs"])
    query_timeout_seconds = Column(Integer, nullable=False, default=DEFAULT_SCRAPER_SETTINGS["query_timeout_seconds"])
    stale_rounds_threshold = Column(Integer, nullable=False, default=DEFAULT_SCRAPER_SETTINGS["stale_rounds_threshold"])
    headless = Column(Boolean, nullable=False, default=DEFAULT_SCRAPER_SETTINGS["headless"])
    close_after_run = Column(Boolean, nullable=False, default=DEFAULT_SCRAPER_SETTINGS["close_after_run"])
    phone_default_region = Column(String, nullable=False, default=DEFAULT_SCRAPER_SETTINGS["phone_default_region"])
    enable_email_enrichment = Column(Boolean, nullable=False, default=DEFAULT_SCRAPER_SETTINGS["enable_email_enrichment"])
    email_max_concurrent_tabs = Column(Integer, nullable=False, default=DEFAULT_SCRAPER_SETTINGS["email_max_concurrent_tabs"])
    website_timeout_seconds = Column(Integer, nullable=False, default=DEFAULT_SCRAPER_SETTINGS["website_timeout_seconds"])


class GlobalScraperSettings(Base):
    """Phase 7 -- single-row (id=1) table holding the scraper-behaviour
    defaults every plain `user` account's "New Search" reads. Same
    "one row, admin-editable" shape as MaintenanceState/PricingSettings
    above. Only an admin/superadmin can PATCH it (see
    routers/scraper_settings.py); any signed-in role can read it
    indirectly through GET /scraper-settings ->
    get_effective_scraper_settings()."""
    __tablename__ = "global_scraper_settings"

    id = Column(Integer, primary_key=True)  # single-row table, always id=1
    max_scrolls_per_query = Column(Integer, nullable=False, default=DEFAULT_SCRAPER_SETTINGS["max_scrolls_per_query"])
    scroll_wait_ms = Column(Integer, nullable=False, default=DEFAULT_SCRAPER_SETTINGS["scroll_wait_ms"])
    max_concurrent_tabs = Column(Integer, nullable=False, default=DEFAULT_SCRAPER_SETTINGS["max_concurrent_tabs"])
    query_timeout_seconds = Column(Integer, nullable=False, default=DEFAULT_SCRAPER_SETTINGS["query_timeout_seconds"])
    stale_rounds_threshold = Column(Integer, nullable=False, default=DEFAULT_SCRAPER_SETTINGS["stale_rounds_threshold"])
    headless = Column(Boolean, nullable=False, default=DEFAULT_SCRAPER_SETTINGS["headless"])
    close_after_run = Column(Boolean, nullable=False, default=DEFAULT_SCRAPER_SETTINGS["close_after_run"])
    phone_default_region = Column(String, nullable=False, default=DEFAULT_SCRAPER_SETTINGS["phone_default_region"])
    enable_email_enrichment = Column(Boolean, nullable=False, default=DEFAULT_SCRAPER_SETTINGS["enable_email_enrichment"])
    email_max_concurrent_tabs = Column(Integer, nullable=False, default=DEFAULT_SCRAPER_SETTINGS["email_max_concurrent_tabs"])
    website_timeout_seconds = Column(Integer, nullable=False, default=DEFAULT_SCRAPER_SETTINGS["website_timeout_seconds"])


def _admin_scraper_to_dict(row: AdminScraperSettings) -> dict:
    return {c.name: getattr(row, c.name) for c in AdminScraperSettings.__table__.columns}


def _global_scraper_to_dict(row: GlobalScraperSettings) -> dict:
    return {c.name: getattr(row, c.name) for c in GlobalScraperSettings.__table__.columns}


def get_admin_scraper_settings(user_id: int) -> dict:
    """Get-or-create: an admin's first-ever read (before they've saved
    anything) seeds and returns a row from DEFAULT_SCRAPER_SETTINGS
    instead of 404ing, same "always something valid to read" shape as
    get_effective_scraper_settings() needs at search time."""
    with SessionLocal() as session:
        row = session.scalar(
            select(AdminScraperSettings).where(AdminScraperSettings.user_id == user_id)
        )
        if row is None:
            row = AdminScraperSettings(user_id=user_id, **DEFAULT_SCRAPER_SETTINGS)
            session.add(row)
            session.commit()
            session.refresh(row)
        return _admin_scraper_to_dict(row)


def update_admin_scraper_settings(user_id: int, **fields) -> dict:
    """Partial update, get-or-create first so an admin can PATCH before
    ever having done a GET. Same calling convention as
    update_pricing_settings/update_maintenance_state."""
    with SessionLocal() as session:
        row = session.scalar(
            select(AdminScraperSettings).where(AdminScraperSettings.user_id == user_id)
        )
        if row is None:
            row = AdminScraperSettings(user_id=user_id, **DEFAULT_SCRAPER_SETTINGS)
            session.add(row)
            session.flush()
        for col, value in fields.items():
            if value is not None:
                setattr(row, col, value)
        session.commit()
        session.refresh(row)
        return _admin_scraper_to_dict(row)


def get_global_scraper_settings() -> dict:
    with SessionLocal() as session:
        row = session.get(GlobalScraperSettings, 1)
        return _global_scraper_to_dict(row)


def update_global_scraper_settings(**fields) -> dict:
    with SessionLocal() as session:
        row = session.get(GlobalScraperSettings, 1)
        for col, value in fields.items():
            if value is not None:
                setattr(row, col, value)
        session.commit()
        session.refresh(row)
        return _global_scraper_to_dict(row)


def get_effective_scraper_settings(user: dict) -> dict:
    """The single helper the "run a search" path calls: an
    admin/superadmin's own AdminScraperSettings row if that's who's
    asking, otherwise the shared GlobalScraperSettings row. This is
    what makes "admin has separate settings, but also sets the
    settings users get" actually true at read time -- callers never
    branch on role themselves."""
    if user.get("role") in ("admin", "superadmin"):
        return get_admin_scraper_settings(user["id"])
    return get_global_scraper_settings()


class SupportTicket(Base):
    """Backs the Help and Support nav tab's contact form. Deliberately
    minimal (subject/message/status only, no threaded replies) -- this
    is a "get a message to a human" form, not a full ticketing system.
    user_id is nullable so a not-yet-logged-in user could in principle
    submit one too (not wired up client-side yet, but the schema
    doesn't force a login), same spirit as MaintenanceState's GET being
    public."""
    __tablename__ = "support_tickets"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    email = Column(String, nullable=False)  # captured directly so a ticket is still readable/contactable even if the account is later deleted
    subject = Column(String, nullable=False)
    message = Column(String, nullable=False)
    status = Column(String, nullable=False, default="open")  # open | closed -- no in-between states needed yet
    created_at = Column(String, nullable=False, server_default=func.datetime("now"))


def _support_ticket_to_dict(row: SupportTicket) -> dict:
    return {c.name: getattr(row, c.name) for c in SupportTicket.__table__.columns}


def create_support_ticket(user_id: int | None, email: str, subject: str, message: str) -> dict:
    """Powers POST /support/tickets. No credit cost, no rate limiting --
    this is a low-volume "contact us" form, not a spend-gated feature."""
    with SessionLocal() as session:
        ticket = SupportTicket(user_id=user_id, email=email, subject=subject, message=message)
        session.add(ticket)
        session.commit()
        session.refresh(ticket)
        result = _support_ticket_to_dict(ticket)
    # Feeds the "Contact Us" reward (progress_key CONTACT_US, target_value
    # 1) -- same pattern as search_leads.py's SEARCH/EXPORT progress, but
    # recorded server-side here since every ticket submission (signed-in
    # only, see the router) should count, not just the first one attempted
    # client-side. record_progress() just keeps incrementing past the
    # target on repeat submissions, which is harmless for a COUNT>=target
    # claimable check.
    if user_id is not None:
        record_progress(user_id, "CONTACT_US", "COUNT")
    return result


def list_my_support_tickets(user_id: int) -> list[dict]:
    """Powers GET /support/tickets -- the caller's own submitted
    tickets, newest first, so Help and Support can show a small
    "your recent requests" list under the form."""
    with SessionLocal() as session:
        rows = (
            session.query(SupportTicket)
            .filter(SupportTicket.user_id == user_id)
            .order_by(SupportTicket.created_at.desc(), SupportTicket.id.desc())
            .all()
        )
        return [_support_ticket_to_dict(row) for row in rows]


class Reward(Base):
    """Mirrors the DBML `rewards` table. `id` is kept as a String (e.g.
    "rwd_welcome_bonus") rather than an autoincrement int so it lines up
    1:1 with the ids rewards_page.py / data/rewards.py already use for
    prerequisite lookups and combo boxes -- avoids having to touch every
    reference to reward ids elsewhere in the UI in this pass."""
    __tablename__ = "rewards"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    type = Column(String, nullable=False)  # Welcome/Daily/Referral/Usage/Milestone/Engagement/Special
    value = Column(Integer, nullable=False)
    progress_key = Column(String, nullable=True)
    progress_type = Column(String, nullable=True)
    target_value = Column(Integer, nullable=False, default=1)
    reset_interval = Column(String, nullable=False, default="NONE")
    status = Column(String, nullable=False, default="Active")
    icon = Column(String, nullable=False, default="fa5s.gift")
    start_date = Column(String, nullable=True)
    end_date = Column(String, nullable=True)
    claims = Column(Integer, nullable=False, default=0)
    prerequisite_reward_id = Column(String, ForeignKey("rewards.id"), nullable=True)
    created_at = Column(String, nullable=False, server_default=func.datetime("now"))
    updated_at = Column(
        String, nullable=False,
        server_default=func.datetime("now"),
        onupdate=func.datetime("now"),
    )

    __table_args__ = (
        CheckConstraint("status IN ('Active', 'Scheduled', 'Inactive')", name="ck_rewards_status"),
    )


def _reward_claims_map(session) -> dict:
    """Real per-reward claim totals, aggregated live from UserReward.claim_count
    -- the actual per-user claim ledger -- rather than trusted from the
    separately-incremented Reward.claims counter below. That counter is
    still bumped on every claim_reward() call (harmless to keep, and
    cheap), but it must never be read back as the source of truth: this
    dev DB's seed data set that column to fabricated baseline numbers
    (245, 567, ...) with zero matching rows in user_rewards, so the
    admin Rewards tab was showing thousands of "claims" nobody ever
    made. Aggregating from user_rewards on every read means the number
    shown is always exactly what real users have actually claimed."""
    rows = session.execute(
        select(UserReward.reward_id, func.coalesce(func.sum(UserReward.claim_count), 0))
        .group_by(UserReward.reward_id)
    ).all()
    return {reward_id: int(total) for reward_id, total in rows}


def _reward_claim_count(session, reward_id: str) -> int:
    """Single-reward version of _reward_claims_map, for the get/create/
    update paths that only ever touch one reward at a time."""
    total = session.scalar(
        select(func.coalesce(func.sum(UserReward.claim_count), 0))
        .where(UserReward.reward_id == reward_id)
    )
    return int(total or 0)


def _reward_to_dict(reward: Reward | None, claims: int | None = None) -> dict | None:
    if reward is None:
        return None
    d = {c.name: getattr(reward, c.name) for c in Reward.__table__.columns}
    if claims is not None:
        d["claims"] = claims
    return d


class UserProgress(Base):
    __tablename__ = "user_progress"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    progress_key = Column(String, nullable=False)
    progress_type = Column(String, nullable=False)
    progress_value = Column(Integer, nullable=False, default=0)
    last_progress_date = Column(String, nullable=True)
    last_activity_at = Column(String, nullable=True)
    created_at = Column(String, nullable=False, server_default=func.datetime("now"))
    updated_at = Column(
        String, nullable=False,
        server_default=func.datetime("now"),
        onupdate=func.datetime("now"),
    )

    __table_args__ = (
        # Matches the DBML unique index -- one progress row per
        # user+key+type, so "logged in" and "3-day streak" don't collide.
        UniqueConstraint(
            "user_id", "progress_key", "progress_type", name="uq_user_progress"
        ),
    )


class UserReward(Base):
    __tablename__ = "user_rewards"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    reward_id = Column(String, ForeignKey("rewards.id"), nullable=False)
    progress_value = Column(Integer, nullable=False, default=0)
    completed = Column(Boolean, nullable=False, default=False)
    claim_count = Column(Integer, nullable=False, default=0)
    started_at = Column(String, nullable=True)
    completed_at = Column(String, nullable=True)
    last_progress_at = Column(String, nullable=True)
    last_claimed_at = Column(String, nullable=True)
    created_at = Column(String, nullable=False, server_default=func.datetime("now"))
    updated_at = Column(
        String, nullable=False,
        server_default=func.datetime("now"),
        onupdate=func.datetime("now"),
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id", "reward_id", name="uq_user_rewards"
        ),
    )


class Referral(Base):
    __tablename__ = "referrals"

    id = Column(Integer, primary_key=True)
    referrer_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    referred_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True)
    referrer_reward_id = Column(String, ForeignKey("rewards.id"), nullable=True)
    referee_reward_id = Column(String, ForeignKey("rewards.id"), nullable=True)
    status = Column(String, nullable=False, default="PENDING")
    reward_value = Column(Integer, nullable=False, default=0)
    referee_reward_value = Column(Integer, nullable=False, default=0)
    expires_at = Column(String, nullable=True)
    rewarded_at = Column(String, nullable=True)
    created_at = Column(String, nullable=False, server_default=func.datetime("now"))

    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'REWARDED', 'CANCELLED')", name="ck_referrals_status"
        ),
        CheckConstraint(
            "referred_user_id != referrer_user_id", name="ck_referrals_no_self_referral"
        ),
    )


def _referral_to_dict(ref: Referral | None) -> dict | None:
    if ref is None:
        return None
    return {c.name: getattr(ref, c.name) for c in Referral.__table__.columns}


class RewardLog(Base):
    __tablename__ = "reward_logs"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    reward_id = Column(String, ForeignKey("rewards.id"), nullable=False)
    referral_id = Column(Integer, ForeignKey("referrals.id"), nullable=True)
    reward_type = Column(String, nullable=True)
    reward_value = Column(Integer, nullable=True)
    created_at = Column(String, nullable=False, server_default=func.datetime("now"))


class Search(Base):
    """A saved search run (Phase 0, step 4a). Mirrors one entry of the
    desktop app's in-memory `data/leads.py` SEARCHES list -- `title` /
    `header` are the same display strings shown on the Search Query
    card, `query_text` is the raw text the user typed (New Search) or
    the query the admin ran, and `id` replaces the client-generated
    string id with a real autoincrement PK once this table is the
    source of truth (Phase 4)."""
    __tablename__ = "searches"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String, nullable=False)
    header = Column(String, nullable=True)
    query_text = Column(String, nullable=False)
    created_at = Column(String, nullable=False, server_default=func.datetime("now"))
    # Soft-delete marker -- NULL means active/visible. Set (not row-deleted)
    # when the user hits the recent-search card's x button, so the credit
    # spend in start_search() and the leads scraped under it stay on record
    # for support/audit purposes; list_searches() just filters these out.
    deleted_at = Column(String, nullable=True)


class CreditReservation(Base):
    """(Optional) Phase 3 follow-up -- backs POST /credits/reserve.
    start_search()/spend_credits() already deduct atomically up front,
    which is correct but final: there's no way to hold credits for a
    long-running operation and only commit or release them once it
    actually finishes. This table is that middle state -- a row here
    means "these credits are set aside, pending an outcome," separate
    from both the free balance (User.credits) and money already spent
    (User.spent).

    status is one of:
      - 'held'      -- credits are reserved, outcome not decided yet.
      - 'finalized' -- the hold was converted into a real spend
                        (moved credits -> spent, same effect as
                        spend_credits()).
      - 'released'  -- the hold was cancelled; credits returned to the
                        free balance, nothing was ever spent.
    A row's status only ever moves held -> finalized or held ->
    released, never back, so resolved_at (null while held) doubles as
    "has this reservation already been settled."
    """
    __tablename__ = "credit_reservations"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    amount = Column(Integer, nullable=False)
    reason = Column(String, nullable=True)
    status = Column(String, nullable=False, default="held")
    created_at = Column(String, nullable=False, server_default=func.datetime("now"))
    resolved_at = Column(String, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('held', 'finalized', 'released')",
            name="ck_credit_reservations_status",
        ),
    )


def _reservation_to_dict(res: "CreditReservation | None") -> dict | None:
    if res is None:
        return None
    return {c.name: getattr(res, c.name) for c in CreditReservation.__table__.columns}


def reserve_credits(user_id: int, amount: int, reason: str | None = None) -> dict:
    """Puts a hold on `amount` credits without spending them yet --
    the optional counterpart to start_search()'s deduct-up-front
    approach, for a caller that wants to hold credits for the duration
    of a long-running search and decide afterwards (via
    finalize_reservation()/release_reservation()) whether it actually
    happened.

    "Available" balance for this check is `credits - reserved_credits`,
    not just `credits` -- otherwise two overlapping reservations could
    each pass a check against the same free balance and together hold
    more than the user actually has (the same double-spend shape
    spend_credits()'s atomic check-then-deduct already guards against
    for plain spends).

    Raises ValueError("amount_must_be_positive") for amount <= 0,
    ValueError("user_not_found") if user_id doesn't exist, and
    ValueError("insufficient_credits") if the available balance is too
    low. Returns {"reservation": ..., "user": ...} -- same
    {"search": ..., "user": ...} shape start_search() uses, so routers
    can pull both without a second query.
    """
    if amount <= 0:
        raise ValueError("amount_must_be_positive")

    with SessionLocal() as session:
        user = session.get(User, user_id)
        if user is None:
            raise ValueError("user_not_found")
        available = user.credits - user.reserved_credits
        if available < amount:
            raise ValueError("insufficient_credits")

        user.reserved_credits += amount
        reservation = CreditReservation(
            user_id=user_id, amount=amount, reason=reason, status="held"
        )
        session.add(reservation)
        session.commit()
        session.refresh(user)
        session.refresh(reservation)
        return {"reservation": _reservation_to_dict(reservation), "user": _to_dict(user)}


def _resolve_reservation(reservation_id: int, user_id: int, *, finalize: bool) -> dict:
    """Shared body for finalize_reservation()/release_reservation() --
    the two only differ in whether the held amount moves into `spent`
    (finalize) or just drops back out of `reserved_credits` with
    nothing charged (release).

    Raises ValueError("reservation_not_found") if the id doesn't exist
    or belongs to a different user (404, same "don't leak existence"
    shape as get_search_leads()'s ownership check), and
    ValueError("reservation_already_resolved") if it's not still
    'held'.
    """
    with SessionLocal() as session:
        reservation = session.get(CreditReservation, reservation_id)
        if reservation is None or reservation.user_id != user_id:
            raise ValueError("reservation_not_found")
        if reservation.status != "held":
            raise ValueError("reservation_already_resolved")

        user = session.get(User, user_id)
        user.reserved_credits -= reservation.amount
        if finalize:
            user.credits -= reservation.amount
            user.spent += reservation.amount
        reservation.status = "finalized" if finalize else "released"
        reservation.resolved_at = datetime.utcnow().isoformat(sep=" ", timespec="seconds")

        session.commit()
        session.refresh(user)
        session.refresh(reservation)
        return {"reservation": _reservation_to_dict(reservation), "user": _to_dict(user)}


def finalize_reservation(reservation_id: int, user_id: int) -> dict:
    """Converts a held reservation into an actual spend -- same net
    effect on the user's row as spend_credits(reservation.amount),
    just arriving at it from an already-held amount instead of
    checking the free balance again."""
    return _resolve_reservation(reservation_id, user_id, finalize=True)


def release_reservation(reservation_id: int, user_id: int) -> dict:
    """Cancels a held reservation -- the credits go back to being part
    of the free balance, nothing is added to `spent`."""
    return _resolve_reservation(reservation_id, user_id, finalize=False)


def _search_to_dict(search: "Search | None") -> dict | None:
    if search is None:
        return None
    return {c.name: getattr(search, c.name) for c in Search.__table__.columns}


class Lead(Base):
    """One business row within a Search's results (Phase 0, step 4a).
    Field names deliberately match the business dicts
    generate_dummy_businesses()/the scraper produce today (see
    data/leads.py and search_leads.py's _card_to_business), so Phase 3's
    "POST results into the Lead table" and Phase 4's "GET .../leads"
    can map 1:1 without renaming anything in the desktop UI.
    unlocked_phone/unlocked_email start False and flip server-side in
    Phase 4's POST /leads/{id}/unlock, in the same transaction as the
    credit deduction."""
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True)
    search_id = Column(Integer, ForeignKey("searches.id"), nullable=False)
    name = Column(String, nullable=False)
    rating = Column(Float, nullable=True)
    reviews = Column(Integer, nullable=False, default=0)
    category = Column(String, nullable=True)
    status = Column(String, nullable=True)
    desc = Column(String, nullable=True)
    address = Column(String, nullable=True)
    hours = Column(String, nullable=True)
    site = Column(String, nullable=True)
    maps_url = Column(String, nullable=True)
    phone_num = Column(String, nullable=True)
    email_addr = Column(String, nullable=True)
    unlocked_phone = Column(Boolean, nullable=False, default=False)
    unlocked_email = Column(Boolean, nullable=False, default=False)
    created_at = Column(String, nullable=False, server_default=func.datetime("now"))


def _lead_to_dict(lead: "Lead | None") -> dict | None:
    if lead is None:
        return None
    return {c.name: getattr(lead, c.name) for c in Lead.__table__.columns}


class Export(Base):
    """One row in the Exports tab's history (backend cutover -- see
    client/data/exports.py's old docstring, "swap EXPORTS for a real
    export-history query once there's a backend"). Written by the
    desktop app right after data/lead_exporter.py finishes actually
    writing a CSV/Excel/JSON/HTML file to disk (search_leads.py's
    export_current_search) -- so unlike Search/Lead this table doesn't
    represent something that happened server-side, it's just a durable
    log of exports the user has produced locally, synced up so the
    Exports tab survives a relaunch instead of resetting to the
    prototype's static list every time.

    file_path is the on-disk location at the moment the file was
    written -- best-effort only: the desktop app uses it to try
    "Download" (re-opening the file) and to rename it on disk when the
    user renames the export, but the file itself can move or be
    deleted outside the app, so callers must handle it being stale.

    leads_count is nullable for a Failed row (nothing was written), same
    as the old EXPORTS prototype list's leads=None convention."""
    __tablename__ = "exports"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    search_id = Column(Integer, ForeignKey("searches.id"), nullable=True)
    file_name = Column(String, nullable=False)
    source = Column(String, nullable=False)
    leads_count = Column(Integer, nullable=True)
    format = Column(String, nullable=False)
    status = Column(String, nullable=False, default="Completed")
    file_path = Column(String, nullable=True)
    created_at = Column(String, nullable=False, server_default=func.datetime("now"))


def _export_to_dict(export: "Export | None") -> dict | None:
    if export is None:
        return None
    return {c.name: getattr(export, c.name) for c in Export.__table__.columns}


class ActivityLog(Base):
    """Persistent, server-side log lines for a Search or an Export --
    replaces the old approach where the Search Leads page's Activity
    Log card (and, before this, the Exports page's lack of one at all)
    re-derived their lines every time from aggregate counts computed
    on the fly. That never actually survived anything: reload the app
    and _load_searches_from_server() rebuilt a shorter, generic-ized
    version of the log from scratch, and any "Credits deducted"/unlock
    lines appended mid-session (_log_activity) lived only in the
    client's in-memory self.searches list -- gone on the next launch.
    This table is the durable version: an entry written here is the
    real record of something that happened, in the order it happened,
    and both tabs' "View all logs" dialogs now read it back instead of
    reconstructing an approximation.

    entity_type is "search" or "export"; entity_id is a Search.id or
    Export.id. There's no FK constraint tying entity_id to whichever
    table entity_type names (SQLite/SQLAlchemy don't support a
    conditional FK), so ownership is enforced in log_activity()/
    list_activity() by loading the actual Search/Export row and
    checking its user_id, the same ownership pattern every other
    per-user table in this file uses -- an entity_id that doesn't
    exist, or belongs to another account, is treated as not found
    rather than trusted at face value.
    """
    __tablename__ = "activity_log"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    entity_type = Column(String, nullable=False)
    entity_id = Column(Integer, nullable=False)
    text = Column(String, nullable=False)
    meta = Column(String, nullable=True)
    created_at = Column(String, nullable=False, server_default=func.datetime("now"))

    __table_args__ = (
        CheckConstraint(
            "entity_type IN ('search', 'export')",
            name="ck_activity_log_entity_type",
        ),
    )


def _activity_entry_to_dict(row: "ActivityLog | None") -> dict | None:
    if row is None:
        return None
    return {c.name: getattr(row, c.name) for c in ActivityLog.__table__.columns}


def _load_activity_owner(session, entity_type: str, entity_id: int):
    """Shared ownership lookup for log_activity()/list_activity() --
    entity_type picks which table entity_id is checked against. Raises
    ValueError("invalid_entity_type") for anything other than
    "search"/"export"."""
    if entity_type == "search":
        return session.get(Search, entity_id)
    if entity_type == "export":
        return session.get(Export, entity_id)
    raise ValueError("invalid_entity_type")


def log_activity(user_id: int, entity_type: str, entity_id: int,
                  text: str, meta: str | None = None) -> dict:
    """Appends one durable log line onto a Search or an Export --
    powers POST /searches/{id}/activity and POST /exports/{id}/activity.
    `text`/`meta` are caller-supplied (same trust level as
    CreateExportRequest.file_name -- this is a descriptive history
    record, not something the credit/unlock logic depends on), so the
    desktop app can log whatever line makes sense for the event that
    just happened (search completed, website scan completed, credits
    deducted, export renamed, download re-generated, ...) without the
    server needing to know every possible event type up front.

    Raises ValueError("invalid_entity_type") for an entity_type other
    than "search"/"export", and ValueError("search_not_found") /
    ValueError("export_not_found") if entity_id doesn't exist or
    belongs to another account -- same ownership-check shape as
    rename_export()/delete_search() etc., so one account can never log
    (or later list) entries against another account's search/export.
    """
    with SessionLocal() as session:
        owner = _load_activity_owner(session, entity_type, entity_id)
        if owner is None or owner.user_id != user_id:
            raise ValueError(f"{entity_type}_not_found")

        entry = ActivityLog(
            user_id=user_id, entity_type=entity_type, entity_id=entity_id,
            text=text, meta=meta,
        )
        session.add(entry)
        session.commit()
        session.refresh(entry)
        return _activity_entry_to_dict(entry)


def list_activity(user_id: int, entity_type: str, entity_id: int) -> list[dict]:
    """Powers GET /searches/{id}/activity and GET /exports/{id}/activity
    -- every ActivityLog row for the given Search/Export, oldest first
    (chronological, matching the order the old client-built lists
    displayed in -- not the newest-first order list_exports()/
    list_searches() use for history tables). Same ownership check and
    error shape as log_activity()."""
    with SessionLocal() as session:
        owner = _load_activity_owner(session, entity_type, entity_id)
        if owner is None or owner.user_id != user_id:
            raise ValueError(f"{entity_type}_not_found")

        rows = (
            session.query(ActivityLog)
            .filter(
                ActivityLog.entity_type == entity_type,
                ActivityLog.entity_id == entity_id,
            )
            .order_by(ActivityLog.created_at.asc(), ActivityLog.id.asc())
            .all()
        )
        return [_activity_entry_to_dict(row) for row in rows]


def create_export(user_id: int, file_name: str, source: str, format: str,
                   leads_count: int | None = None, status: str = "Completed",
                   file_path: str | None = None, search_id: int | None = None,
                   cost: int = 0) -> dict:
    """Powers POST /exports -- logs one export the desktop app just
    wrote to disk (or attempted to). Always succeeds (no
    insufficient-credits rejection, unlike start_search) since this is
    primarily a history record of something that already happened
    locally -- the file is already on disk (or already failed to write)
    by the time this is called.

    Charges `cost` credits, but only if `status == "Completed"` -- a
    failed export (the desktop app posts status="Failed" when
    lead_exporter.py raises) never touches the balance, since nothing
    was actually produced. Same clamp-don't-reject behavior as
    add_search_results() if the balance has dropped below `cost` since
    the export was attempted: charge whatever's left rather than error
    out on an export that already succeeded.

    Returns {"export": ..., "user": ...} -- callers that don't care
    about the credit side (e.g. a future admin-only export log viewer)
    can just ignore "user"."""
    with SessionLocal() as session:
        export = Export(
            user_id=user_id, search_id=search_id, file_name=file_name,
            source=source, leads_count=leads_count, format=format,
            status=status, file_path=file_path,
        )
        session.add(export)

        user = session.get(User, user_id)
        if status == "Completed" and cost > 0 and user is not None:
            charge = min(cost, user.credits)
            user.credits -= charge
            user.spent += charge

        session.commit()
        session.refresh(export)
        if user is not None:
            session.refresh(user)
        return {"export": _export_to_dict(export), "user": _to_dict(user)}


def list_exports(user_id: int) -> list[dict]:
    """Powers GET /exports -- every Export row belonging to `user_id`,
    most recent first (same created_at DESC, id DESC tiebreak pattern
    as list_searches())."""
    with SessionLocal() as session:
        rows = (
            session.query(Export)
            .filter(Export.user_id == user_id)
            .order_by(Export.created_at.desc(), Export.id.desc())
            .all()
        )
        return [_export_to_dict(row) for row in rows]


def rename_export(export_id: int, user_id: int, file_name: str) -> dict:
    """Powers PATCH /exports/{id}. Ownership-checked the same way every
    other per-row export/search endpoint is -- raises
    ValueError("export_not_found") if `export_id` doesn't exist or
    belongs to another account."""
    with SessionLocal() as session:
        export = session.get(Export, export_id)
        if export is None or export.user_id != user_id:
            raise ValueError("export_not_found")
        export.file_name = file_name
        session.commit()
        session.refresh(export)
        return _export_to_dict(export)


def delete_export(export_id: int, user_id: int) -> None:
    """Powers DELETE /exports/{id} -- the row "..." menu's Delete
    Export. Hard-deletes the history row (unlike delete_search's
    soft-delete -- there's no audit reason to keep a purely local
    export log entry once the user says forget it). Ownership-checked
    the same way rename_export() is."""
    with SessionLocal() as session:
        export = session.get(Export, export_id)
        if export is None or export.user_id != user_id:
            raise ValueError("export_not_found")
        session.delete(export)
        session.commit()


def start_search(user_id: int, title: str, query_text: str,
                  header: str | None = None, cost: int | None = None) -> dict:
    """Phase 3 -- powers POST /searches/start. Creates the Search row and
    verifies the user can afford `cost`, but -- unlike the old
    behaviour -- does NOT deduct credits here. Charging happens later,
    in add_search_results(), and only if the scrape actually comes back
    with at least one lead: a search that finds nothing costs nothing.
    (No refund path exists or is needed, since nothing was ever taken.)

    Still raises ValueError("insufficient_credits") up front if the
    balance is currently too low to cover `cost` -- this keeps the
    original guardrail against starting scrapes (which cost real
    Chrome/proxy time either way) a broke account can't pay for, without
    charging for a search that turns out empty.

    `cost` defaults to None, which means "use the admin-configured
    PricingSettings.search_cost" (see get_pricing_settings()) rather
    than the old fixed SEARCH_COST constant -- routers/searches.py
    still passes 0 explicitly for admin/superadmin callers, so that
    free-for-admins behavior is unchanged.

    Raises ValueError("cost_must_not_be_negative") for cost < 0,
    ValueError("user_not_found") if user_id doesn't exist, and
    ValueError("insufficient_credits") if the balance is too low --
    callers (routers/searches.py) translate these into HTTP status
    codes the same way routers/credits.py does for spend_credits().
    """
    if cost is None:
        cost = get_pricing_settings()["search_cost"]

    if cost < 0:
        raise ValueError("cost_must_not_be_negative")

    with SessionLocal() as session:
        user = session.get(User, user_id)
        if user is None:
            raise ValueError("user_not_found")
        if user.credits < cost:
            raise ValueError("insufficient_credits")

        search = Search(user_id=user_id, title=title, header=header, query_text=query_text)
        session.add(search)
        session.commit()
        session.refresh(user)
        session.refresh(search)
        return {"search": _search_to_dict(search), "user": _to_dict(user)}


def list_searches(user_id: int) -> list[dict]:
    """Phase 4 -- powers GET /searches. Every Search row belonging to
    `user_id`, most recent first (created_at DESC, then id DESC as a
    tiebreaker since created_at's server_default only has second-level
    precision -- two searches started in the same second still need a
    stable, newest-first order). No leads included here; that's
    GET /searches/{id}/leads's job, kept separate so listing searches
    for the sidebar's history cards doesn't have to pull every Lead row
    along with it."""
    with SessionLocal() as session:
        rows = (
            session.query(Search)
            .filter(Search.user_id == user_id, Search.deleted_at.is_(None))
            .order_by(Search.created_at.desc(), Search.id.desc())
            .all()
        )
        return [_search_to_dict(row) for row in rows]


def delete_search(search_id: int, user_id: int) -> None:
    """Powers DELETE /searches/{id} -- the recent-search card's x button.
    Soft-delete only: stamps `deleted_at` instead of removing the row, so
    the Search stays behind list_searches()'s filter (out of the sidebar)
    but the credits already spent on it via start_search(), and every Lead
    scraped under it, stay intact for support/audit lookups.

    Ownership-checked the same way get_search_leads()/add_search_results()
    are -- raises ValueError("search_not_found") if `search_id` doesn't
    exist, doesn't belong to `user_id`, or is already deleted (idempotent:
    deleting twice 404s the second time instead of erroring).
    """
    with SessionLocal() as session:
        search = session.get(Search, search_id)
        if search is None or search.user_id != user_id or search.deleted_at is not None:
            raise ValueError("search_not_found")

        search.deleted_at = datetime.utcnow().isoformat()
        session.commit()


def get_search_leads(search_id: int, user_id: int) -> dict:
    """Phase 4 -- powers GET /searches/{id}/leads. Returns the Search row
    itself plus every Lead attached to it, ordered by id (insertion
    order -- matches the order add_search_results() wrote them in,
    which is the order ScraperWorker found them in).

    Ownership-checked the same way add_search_results() is: `search_id`
    must belong to `user_id`, so one account can never read another
    account's leads by guessing an id.

    Raises ValueError("search_not_found") if `search_id` doesn't exist
    or doesn't belong to `user_id` -- routers/searches.py turns that
    into a 404, same as the other search-scoped endpoints.
    """
    with SessionLocal() as session:
        search = session.get(Search, search_id)
        if search is None or search.user_id != user_id:
            raise ValueError("search_not_found")

        rows = (
            session.query(Lead)
            .filter(Lead.search_id == search_id)
            .order_by(Lead.id.asc())
            .all()
        )
        return {"search": _search_to_dict(search), "leads": [_lead_to_dict(row) for row in rows]}


def list_all_leads(user_id: int) -> list[dict]:
    """Powers GET /leads (all-searches version, not the per-search
    GET /searches/{id}/leads above) -- every Lead row that belongs to
    `user_id`, across every Search they've ever run, newest first.
    Backs the Overview dashboard (client/ui/pages/overview.py), which
    needs to aggregate across a user's whole lead history (total
    found, average rating, category/rating breakdowns, top-rated
    businesses, ...) rather than one search at a time.

    Deliberately does NOT filter out leads whose parent Search has
    been soft-deleted (Search.deleted_at) -- those businesses were
    still genuinely found, and the Overview page's "Total Leads Found"
    counter would otherwise silently shrink every time someone clears
    an old search from their sidebar, which isn't what that stat
    means.
    """
    with SessionLocal() as session:
        rows = (
            session.query(Lead)
            .join(Search, Lead.search_id == Search.id)
            .filter(Search.user_id == user_id)
            .order_by(Lead.created_at.desc(), Lead.id.desc())
            .all()
        )
        return [_lead_to_dict(row) for row in rows]


def _lead_has_phone(lead: "Lead") -> bool:
    val = lead.phone_num
    return bool(val) and val != "--"


def _lead_has_email(lead: "Lead") -> bool:
    val = lead.email_addr
    return bool(val) and val != "--"


def unlock_lead(lead_id: int, user_id: int, field: str = "all") -> dict:
    """Phase 4 -- powers POST /leads/{id}/unlock. Mirrors the desktop
    app's on_unlock_row/business_field_cost pricing (core/models.py):
    `field` is "phone", "email", or "all", and phone/email are priced
    independently -- lead_unlock_phone_cost/lead_unlock_email_cost
    credits respectively to reveal each -- but only if the lead
    actually has that field and it isn't unlocked already -- a field
    the lead doesn't have, or one that's already unlocked, is free and
    doesn't touch the balance at all.

    Same atomic check-then-deduct-then-write pattern as start_search():
    if the cost is 0 (nothing left to pay for on the requested field(s))
    the flags still flip idempotently with no credit check at all, since
    there's nothing to gate. Otherwise the balance check, deduction, and
    flag flip happen in one transaction -- either the lead ends up
    unlocked and paid for, or nothing changes.

    Ownership-checked via the lead's parent Search, same as
    add_search_results()/get_search_leads() -- a lead_id belonging to
    another account's search behaves exactly like one that doesn't
    exist.

    Raises ValueError("lead_not_found") if `lead_id` doesn't exist or
    its search doesn't belong to `user_id`; ValueError("invalid_field")
    for anything other than "phone"/"email"/"all"; and
    ValueError("insufficient_credits") if the balance is too low for a
    non-zero cost -- routers/searches.py maps these to 404/400/402
    respectively, same as every other credit-gated write here.
    (user_not_found shouldn't happen in practice, same reasoning as
    start_search()'s docstring -- user_id comes from a token
    get_current_user already resolved.)
    """
    if field not in ("phone", "email", "all"):
        raise ValueError("invalid_field")

    pricing = get_pricing_settings()
    email_cost = pricing["lead_unlock_email_cost"]
    phone_cost = pricing["lead_unlock_phone_cost"]

    with SessionLocal() as session:
        lead = session.get(Lead, lead_id)
        if lead is None:
            raise ValueError("lead_not_found")
        search = session.get(Search, lead.search_id)
        if search is None or search.user_id != user_id:
            raise ValueError("lead_not_found")

        cost = 0
        want_phone = field in ("phone", "all") and _lead_has_phone(lead) and not lead.unlocked_phone
        want_email = field in ("email", "all") and _lead_has_email(lead) and not lead.unlocked_email
        if want_phone:
            cost += phone_cost
        if want_email:
            cost += email_cost

        # Feeds the "Unlock 50 Contacts" milestone reward (progress_key
        # UNLOCK_CONTACT). Matches overview.py's own "Unlocked Contacts"
        # stat definition (unlocked_phone OR unlocked_email) -- a lead
        # counts once it has ANY field revealed, not once per field, so
        # unlocking both phone and email together (field="all") is still
        # +1, and unlocking a second field later on an already-partially-
        # unlocked lead adds nothing (it was already counted). Checked
        # *before* the flags below get flipped, so this only fires on
        # the actual zero-to-one transition.
        newly_unlocked_contact = (want_phone or want_email) and not (lead.unlocked_phone or lead.unlocked_email)

        user = session.get(User, user_id)
        if user is None:
            raise ValueError("user_not_found")
        if cost > 0:
            if user.credits < cost:
                raise ValueError("insufficient_credits")
            user.credits -= cost
            user.spent += cost

        if want_phone:
            lead.unlocked_phone = True
        if want_email:
            lead.unlocked_email = True

        session.commit()
        session.refresh(lead)
        session.refresh(user)

        if newly_unlocked_contact:
            record_progress(user_id, "UNLOCK_CONTACT", "COUNT")

        return {
            "lead": _lead_to_dict(lead),
            "user": _to_dict(user),
            "cost": cost,
        }


def add_search_results(search_id: int, user_id: int, leads: list[dict], cost: int = 0) -> dict:
    """Phase 3 -- powers POST /searches/{search_id}/results. Bulk-inserts
    `leads` (already-mapped business dicts -- see search_leads.py's
    _scraped_card_to_business, whose output keys are a 1:1 match for
    Lead's columns) as rows on the given Search, once ScraperWorker
    finishes locally.

    Also where the actual credit charge for the search now happens
    (moved here from start_search() -- see that function's docstring):
    if `leads` is non-empty, `cost` credits are deducted in the same
    transaction as the insert. An empty result set costs nothing and
    nothing is deducted. If the account's balance has dropped below
    `cost` since start_search()'s up-front affordability check (e.g.
    credits spent elsewhere mid-scrape), the deduction is clamped to
    whatever's left rather than going negative or discarding the
    already-scraped leads -- there's no refund path, so there's also no
    "reject already-done work" path.

    Ownership-checked: `search_id` must belong to `user_id`, same
    pattern as every other per-user write in this file, so one account
    can never write leads onto another account's search by guessing an
    id. unlocked_phone/unlocked_email aren't accepted here -- every
    freshly-scraped lead starts locked, same as Phase 4's POST
    /leads/{id}/unlock will expect.

    Raises ValueError("search_not_found") if `search_id` doesn't exist
    or doesn't belong to `user_id` -- routers/searches.py turns that
    into a 404, same as any other not-mine-or-doesn't-exist lookup.
    """
    with SessionLocal() as session:
        search = session.get(Search, search_id)
        if search is None or search.user_id != user_id:
            raise ValueError("search_not_found")

        rows = [
            Lead(
                search_id=search_id,
                name=lead.get("name", ""),
                rating=lead.get("rating"),
                reviews=lead.get("reviews", 0),
                category=lead.get("category"),
                status=lead.get("status"),
                desc=lead.get("desc"),
                address=lead.get("address"),
                hours=lead.get("hours"),
                site=lead.get("site"),
                maps_url=lead.get("maps_url"),
                phone_num=lead.get("phone_num"),
                email_addr=lead.get("email_addr"),
            )
            for lead in leads
        ]
        session.add_all(rows)

        if rows and cost > 0:
            user = session.get(User, user_id)
            if user is not None:
                charge = min(cost, user.credits)
                user.credits -= charge
                user.spent += charge
        else:
            user = session.get(User, user_id)

        session.commit()
        for row in rows:
            session.refresh(row)
        session.refresh(user)
        return {
            "search": _search_to_dict(search),
            "leads": [_lead_to_dict(row) for row in rows],
            "user": _to_dict(user),
        }


# One-time seed data for the `rewards` table on a brand-new database only
# -- init_db() imports this exactly once (see below), then the `rewards`
# table is the live source of truth from then on (rewards_page.py's admin
# CRUD, see PROGRESS_REWARDS.md Phase 2). Moved here from the old
# data/rewards.py module (Phase 4 cleanup) since that was this constant's
# only remaining reader -- db.py no longer needs to reach into data/ for
# its own bootstrap data.
_SEED_REWARDS = [
    {"id": "rwd_welcome_bonus", "name": "Welcome Bonus",
     "description": "For new users who sign up", "type": "Welcome", "value": 100,
     "status": "Active", "start_date": "Jul 1, 2026", "end_date": "Dec 31, 2026",
     "claims": 0, "icon": "fa5s.gift",
     "progress_key": "ACCOUNT_CREATED", "progress_type": "COUNT",
     "target_value": 1, "reset_interval": "NONE", "prerequisite_reward_id": None},
    {"id": "rwd_login_streak_3", "name": "Login Streak (3 Days)",
     "description": "Login to SinuLead for 3 days", "type": "Daily", "value": 40,
     "status": "Active", "start_date": "Jun 1, 2026", "end_date": "Dec 31, 2026",
     "claims": 0, "icon": "fa5s.calendar-check",
     "progress_key": "LOGIN", "progress_type": "STREAK",
     "target_value": 3, "reset_interval": "NONE", "prerequisite_reward_id": None},
    {"id": "rwd_login_streak_7", "name": "Login Streak (7 Days)",
     "description": "Login to SinuLead for 7 days", "type": "Daily", "value": 100,
     "status": "Active", "start_date": "Jun 1, 2026", "end_date": "Dec 31, 2026",
     "claims": 0, "icon": "fa5s.calendar-alt",
     "progress_key": "LOGIN", "progress_type": "STREAK",
     "target_value": 7, "reset_interval": "NONE", "prerequisite_reward_id": "rwd_login_streak_3"},
    {"id": "rwd_refer_3", "name": "Refer 3 Friends",
     "description": "Invite 3 friends to join SinuLead", "type": "Referral", "value": 30,
     "status": "Active", "start_date": "May 1, 2026", "end_date": "Dec 31, 2026",
     "claims": 0, "icon": "fa5s.user-friends",
     "progress_key": "REFERRAL", "progress_type": "COUNT",
     "target_value": 3, "reset_interval": "NONE", "prerequisite_reward_id": None},
    {"id": "rwd_refer_10", "name": "Refer 10 Friends",
     "description": "Invite 10 friends to join SinuLead", "type": "Referral", "value": 150,
     "status": "Active", "start_date": "May 1, 2026", "end_date": "Dec 31, 2026",
     "claims": 0, "icon": "fa5s.users",
     "progress_key": "REFERRAL", "progress_type": "COUNT",
     "target_value": 10, "reset_interval": "NONE", "prerequisite_reward_id": "rwd_refer_3"},
    {"id": "rwd_refer_instant", "name": "Instant Referral Bonus",
     "description": "Get credits immediately when a friend you referred completes their first search",
     "type": "Referral", "value": 25,
     "status": "Active", "start_date": "Aug 1, 2026", "end_date": "Dec 31, 2026",
     "claims": 0, "icon": "fa5s.bolt",
     "progress_key": "REFERRAL", "progress_type": "COUNT",
     "target_value": 10, "reset_interval": "MONTHLY", "prerequisite_reward_id": None},
    {"id": "rwd_first_search", "name": "First Search",
     "description": "Complete your first business search", "type": "Usage", "value": 20,
     "status": "Active", "start_date": "Jul 1, 2026", "end_date": "Dec 31, 2026",
     "claims": 0, "icon": "fa5s.search",
     "progress_key": "SEARCH", "progress_type": "COUNT",
     "target_value": 1, "reset_interval": "NONE", "prerequisite_reward_id": None},
    {"id": "rwd_export_csv", "name": "Export First CSV",
     "description": "Export your first search results", "type": "Usage", "value": 20,
     "status": "Scheduled", "start_date": "Aug 5, 2026", "end_date": "Dec 31, 2026",
     "claims": 0, "icon": "fa5s.file-csv",
     "progress_key": "EXPORT", "progress_type": "COUNT",
     "target_value": 1, "reset_interval": "NONE", "prerequisite_reward_id": None},
    {"id": "rwd_unlock_50", "name": "Unlock 50 Contacts",
     "description": "Unlock a total of 50 business contacts", "type": "Milestone", "value": 50,
     "status": "Inactive", "start_date": "Apr 1, 2026", "end_date": "Jun 30, 2026",
     "claims": 0, "icon": "fa5s.lock",
     "progress_key": "UNLOCK_CONTACT", "progress_type": "COUNT",
     "target_value": 50, "reset_interval": "NONE", "prerequisite_reward_id": None},
    {"id": "rwd_write_review", "name": "Contact Us",
     "description": "Send us a message through Contact Us", "type": "Engagement", "value": 30,
     "status": "Active", "start_date": "Jul 1, 2026", "end_date": "Dec 31, 2026",
     "claims": 0, "icon": "fa5s.headset",
     "progress_key": "CONTACT_US", "progress_type": "COUNT",
     "target_value": 1, "reset_interval": "NONE", "prerequisite_reward_id": None},
    {"id": "rwd_top_contributor", "name": "Top Contributor",
     "description": "Be one of the top 10 active users", "type": "Special", "value": 500,
     "status": "Scheduled", "start_date": "Aug 1, 2026", "end_date": "Aug 31, 2026",
     "claims": 0, "icon": "fa5s.trophy"},
    {"id": "rwd_complete_profile", "name": "Complete Your Profile",
     "description": "Fill out all fields on your profile", "type": "Milestone", "value": 25,
     "status": "Active", "start_date": "Jun 1, 2026", "end_date": "Dec 31, 2026",
     "claims": 0, "icon": "fa5s.id-card",
     "progress_key": "PROFILE_COMPLETED", "progress_type": "COUNT",
     "target_value": 1, "reset_interval": "NONE", "prerequisite_reward_id": None},
    {"id": "rwd_anniversary", "name": "Anniversary Bonus",
     "description": "Reward for users who joined a year ago", "type": "Special", "value": 200,
     "status": "Inactive", "start_date": "Jan 1, 2026", "end_date": "Jan 31, 2026",
     "claims": 0, "icon": "fa5s.birthday-cake"},
]


def init_db() -> None:
    """Creates the tables if they don't exist yet, and seeds the two
    prototype accounts + the default maintenance row on a brand-new
    database only (never overwrites real data on an existing one)."""
    Base.metadata.create_all(bind=engine)

    # create_all() only creates missing *tables* -- it won't add a
    # column to a `leads` table that already exists from before
    # `maps_url` was added to the model. Patch it in by hand so
    # existing databases (like the one shipped in this repo) pick it
    # up instead of silently dropping every maps_url that gets posted.
    with engine.connect() as conn:
        existing_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(leads)")}
        if "leads" not in Base.metadata.tables:
            pass
        elif existing_cols and "maps_url" not in existing_cols:
            conn.exec_driver_sql("ALTER TABLE leads ADD COLUMN maps_url VARCHAR")
            conn.commit()

        # Same story for pricing_settings.export_cost -- added after
        # search_cost/lead_unlock_field_cost already shipped, so an
        # existing row from before this column existed needs it patched
        # in (defaulting to 0/free, per the PricingSettings docstring)
        # rather than crashing every read/write of that row.
        pricing_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(pricing_settings)")}
        if "pricing_settings" not in Base.metadata.tables:
            pass
        else:
            if pricing_cols and "export_cost" not in pricing_cols:
                conn.exec_driver_sql("ALTER TABLE pricing_settings ADD COLUMN export_cost INTEGER NOT NULL DEFAULT 0")
                conn.commit()

            # lead_unlock_field_cost -> lead_unlock_email_cost /
            # lead_unlock_phone_cost split (see PricingSettings'
            # docstring). Add both new columns, each seeded from the old
            # shared field_cost value if that column is still present on
            # this row (existing deployment), so an admin's current
            # price keeps applying to both fields until they explicitly
            # split them in the Pricing tab. On a brand-new deployment
            # (no old column at all) the column defaults kick in
            # instead -- handled below via the LEAD_UNLOCK_EMAIL_COST/
            # LEAD_UNLOCK_PHONE_COST-seeded row insert.
            had_old_field_cost = "lead_unlock_field_cost" in pricing_cols
            if pricing_cols and "lead_unlock_email_cost" not in pricing_cols:
                default_email = LEAD_UNLOCK_EMAIL_COST
                conn.exec_driver_sql(
                    f"ALTER TABLE pricing_settings ADD COLUMN lead_unlock_email_cost INTEGER NOT NULL DEFAULT {default_email}"
                )
                if had_old_field_cost:
                    conn.exec_driver_sql(
                        "UPDATE pricing_settings SET lead_unlock_email_cost = lead_unlock_field_cost"
                    )
                conn.commit()
            if pricing_cols and "lead_unlock_phone_cost" not in pricing_cols:
                default_phone = LEAD_UNLOCK_PHONE_COST
                conn.exec_driver_sql(
                    f"ALTER TABLE pricing_settings ADD COLUMN lead_unlock_phone_cost INTEGER NOT NULL DEFAULT {default_phone}"
                )
                if had_old_field_cost:
                    conn.exec_driver_sql(
                        "UPDATE pricing_settings SET lead_unlock_phone_cost = lead_unlock_field_cost"
                    )
                conn.commit()

    with SessionLocal() as session:
        if session.get(MaintenanceState, 1) is None:
            default_end = (datetime.now() + timedelta(hours=4)).isoformat()
            session.add(MaintenanceState(id=1, end_datetime=default_end))
            session.commit()

        if session.get(PricingSettings, 1) is None:
            # Seeded from the old hardcoded constants so upgrading an
            # existing deployment doesn't silently change anyone's price.
            # export_cost has no old constant (exports weren't
            # credit-gated before) -- defaults to 0/free on a brand-new row.
            session.add(PricingSettings(
                id=1,
                search_cost=SEARCH_COST,
                lead_unlock_email_cost=LEAD_UNLOCK_EMAIL_COST,
                lead_unlock_phone_cost=LEAD_UNLOCK_PHONE_COST,
                export_cost=0,
            ))
            session.commit()

        if session.get(GlobalScraperSettings, 1) is None:
            # Seeded from DEFAULT_SCRAPER_SETTINGS -- the same values
            # the old ~/.sinulead_scraper_config.json default carried --
            # so a brand-new DB's global row matches what every
            # plain-user search already behaved like. Only ever fires
            # once, on a fresh database; never touches an existing row.
            session.add(GlobalScraperSettings(id=1, **DEFAULT_SCRAPER_SETTINGS))
            session.commit()

        if session.scalar(select(func.count()).select_from(User)) == 0:
            existing_codes = set()
            for seed in _SEED_ACCOUNTS:
                code = generate_referral_code(seed["full_name"], existing_codes)
                existing_codes.add(code)
                session.add(User(
                    email=seed["email"], password=hash_password(seed["password"]),
                    full_name=seed["full_name"], role=seed["role"],
                    credits=seed["credits"], referral_code=code,
                ))
            session.commit()

        if session.scalar(select(func.count()).select_from(Reward)) == 0:
            # One-time import of _SEED_REWARDS into the real table. After
            # this, the `rewards` table is the live source of truth --
            # see rewards_page.py, PROGRESS_REWARDS.md Phase 2.
            for r in _SEED_REWARDS:
                session.add(Reward(
                    id=r["id"], name=r["name"], description=r.get("description", ""),
                    type=r["type"], value=r["value"], status=r.get("status", "Active"),
                    start_date=r.get("start_date"), end_date=r.get("end_date"),
                    claims=r.get("claims", 0),
                    icon=r.get("icon", "fa5s.gift"),
                    progress_key=r.get("progress_key"), progress_type=r.get("progress_type"),
                    target_value=r.get("target_value", 1),
                    reset_interval=r.get("reset_interval", "NONE"),
                    prerequisite_reward_id=r.get("prerequisite_reward_id"),
                ))
            session.commit()

        # One-time migration for databases seeded before "Write a Review"
        # (rwd_write_review, type Engagement, no progress_key -- dead on
        # arrival since EarnCreditsPage only shows rewards with a
        # progress_key, see earn_credits.py's build_sections) became
        # "Contact Us" (type Engagement, progress_key CONTACT_US, wired to
        # POST /support/tickets). Only touches that one row, and only if
        # it still looks like the old seed -- an admin who already
        # customized rwd_write_review by hand keeps their edits.
        old_review_row = session.get(Reward, "rwd_write_review")
        if old_review_row is not None and old_review_row.progress_key is None:
            old_review_row.name = "Contact Us"
            old_review_row.description = "Send us a message through Contact Us"
            old_review_row.type = "Engagement"
            old_review_row.icon = "fa5s.headset"
            old_review_row.progress_key = "CONTACT_US"
            old_review_row.progress_type = "COUNT"
            old_review_row.target_value = 1
            old_review_row.reset_interval = "NONE"
            session.commit()


def get_user_by_email(email: str) -> dict | None:
    with SessionLocal() as session:
        user = session.scalar(select(User).where(User.email == email.lower()))
        return _to_dict(user)


def get_user_by_id(user_id: int) -> dict | None:
    with SessionLocal() as session:
        return _to_dict(session.get(User, user_id))


def get_user_by_referral_code(code: str) -> dict | None:
    with SessionLocal() as session:
        user = session.scalar(
            select(User).where(User.referral_code == code.strip().upper())
        )
        return _to_dict(user)


def list_users(order_by=None) -> list[dict]:
    with SessionLocal() as session:
        stmt = select(User).order_by(order_by if order_by is not None else User.created_at.desc())
        users = session.scalars(stmt).all()
        return [_to_dict(u) for u in users]


def create_user(email: str, password: str, full_name: str,
                 referred_by_code: str | None = None,
                 avatar: str | None = None) -> dict:
    """Inserts a new user row. Raises sqlalchemy.exc.IntegrityError if the
    email is already taken -- callers should catch that and show a
    friendly 'account already exists' message rather than letting it
    bubble up.

    If referred_by_code resolves to an existing user, both that user and
    the new signup get REFERRAL_BONUS_CREDITS added to their
    pending_bonus_credits, in the same transaction as the insert -- so a
    signup either fully succeeds (row created + both sides credited) or
    fully fails, no partial state where the account exists but the
    referrer never got credited. (Dashboard.__init__ is what folds
    pending_bonus_credits into the visible balance and zeroes it back
    out, on whichever side next logs in -- see PROGRESS.md Phase 4.)
    """
    with SessionLocal() as session:
        existing_codes = set(
            session.scalars(
                select(User.referral_code).where(User.referral_code.is_not(None))
            ).all()
        )
        code = generate_referral_code(full_name, existing_codes)

        referred_by_user_id = None
        referrer = None
        if referred_by_code:
            referrer = session.scalar(
                select(User).where(
                    User.referral_code == referred_by_code.strip().upper()
                )
            )
            if referrer:
                referred_by_user_id = referrer.id

        user = User(
            email=email.lower(), password=hash_password(password), full_name=full_name,
            role="user", avatar=avatar, referral_code=code,
            referred_by_user_id=referred_by_user_id,
        )
        session.add(user)
        session.commit()
        session.refresh(user)

        if referrer and REFERRAL_PROGRAM_ENABLED:
            # No instant credit anymore -- this just records a PENDING
            # referral. It only pays out once the referred user completes
            # the qualifying action, via try_reward_referral() (see that
            # function's docstring below), and only if the referrer
            # hasn't hit their reset_interval cap.
            # Prefer the reward actually designed for this flow -- paying
            # out on the referred user's first completed action -- instead
            # of grabbing whichever "Referral"-type reward happened to be
            # inserted first. Without this, a query with no ORDER BY could
            # (and did, in practice) return "Refer 3 Friends" instead of
            # "Instant Referral Bonus", crediting 30/150 instead of the
            # intended 25 (see PROGRESS_REWARDS.md Phase 5). Falls back to
            # the lowest-target_value active Referral reward if the seed
            # id has been renamed/deleted by an admin, so this doesn't
            # break on a customized rewards table.
            referrer_reward = session.scalar(
                select(Reward).where(
                    Reward.id == "rwd_refer_instant", Reward.status == "Active"
                )
            )
            if referrer_reward is None:
                referrer_reward = session.scalar(
                    select(Reward)
                    .where(Reward.type == "Referral", Reward.status == "Active")
                    .order_by(Reward.target_value.asc())
                )
            create_referral(
                referrer_user_id=referrer.id, referred_user_id=user.id,
                referrer_reward_id=referrer_reward.id if referrer_reward else None,
            )
            # Bumps UserProgress so milestone referral rewards (Refer 3/10
            # Friends) can actually be claimed later -- claim_reward()
            # checks UserProgress, and referrals were never wired into it
            # before now, which is why those two rewards could show
            # "Claimed" in the UI without ever crediting anything (see
            # PROGRESS_REWARDS.md Phase 5). rwd_refer_instant itself
            # doesn't need this -- it pays out separately, automatically,
            # via try_reward_referral() once the referred user searches.
            record_progress(referrer.id, "REFERRAL", "COUNT")

        return _to_dict(user)


def update_user(user_id: int, **fields) -> dict | None:
    """Partial update -- pass only the columns you want to change, e.g.
    update_user(3, status='suspended', credits=1200). Unknown column
    names are rejected rather than silently ignored, since a typo here
    should be loud, not a quiet no-op."""
    if not fields:
        return get_user_by_id(user_id)

    unknown = set(fields) - VALID_UPDATE_COLUMNS
    if unknown:
        raise ValueError(f"update_user: unknown column(s) {unknown}")

    with SessionLocal() as session:
        user = session.get(User, user_id)
        if user is None:
            return None
        for col, value in fields.items():
            if col == "password":
                value = hash_password(value)
            setattr(user, col, value)
        session.commit()
        session.refresh(user)
        return _to_dict(user)


def spend_credits(user_id: int, amount: int, reason: str | None = None) -> dict:
    """Atomically deducts `amount` from the user's credits balance and
    adds it to `spent`, in a single transaction -- unlike calling
    update_user() after a separate read, this checks the balance and
    writes the new one without ever letting two concurrent spends both
    read the same starting balance (the classic double-spend race).

    Raises ValueError("amount_must_be_positive") for amount <= 0,
    ValueError("user_not_found") if user_id doesn't exist, and
    ValueError("insufficient_credits") if the balance is too low --
    callers (routers/credits.py) translate these into the right HTTP
    status codes. `reason` is accepted for the caller's own logging/
    error messages (e.g. "unlock_contact") but isn't persisted yet --
    there's no per-spend log table in this schema (see PROGRESS.md
    Phase 6 for the closest analog, reward_logs, which is claims-only).
    """
    if amount <= 0:
        raise ValueError("amount_must_be_positive")

    with SessionLocal() as session:
        user = session.get(User, user_id)
        if user is None:
            raise ValueError("user_not_found")
        if user.credits < amount:
            raise ValueError("insufficient_credits")

        user.credits -= amount
        user.spent += amount
        session.commit()
        session.refresh(user)
        return _to_dict(user)


def claim_pending_bonus(user_id: int) -> dict:
    """Atomically moves a user's pending_bonus_credits into their real
    balance and zeroes pending_bonus_credits back out, in one
    transaction -- same operation dashboard.py's __init__ used to do
    with two separate local writes (read user, then update_user(credits=...,
    pending_bonus_credits=0)). A no-op (returns the user unchanged) if
    pending_bonus_credits is already 0, so callers can call this
    unconditionally on every login without checking first.

    Raises ValueError("user_not_found") if user_id doesn't exist.
    """
    with SessionLocal() as session:
        user = session.get(User, user_id)
        if user is None:
            raise ValueError("user_not_found")

        pending = user.pending_bonus_credits
        if pending:
            user.credits += pending
            user.pending_bonus_credits = 0
            session.commit()
            session.refresh(user)
        return _to_dict(user)


def admin_adjust_credits(user_id: int, amount: int) -> dict:
    """Admin-only grant/deduct: adds `amount` (negative to deduct) to
    the user's balance atomically. Unlike spend_credits(), this doesn't
    touch `spent` -- an admin adjustment isn't the user spending
    anything -- and a deduction is clamped at 0 instead of raising, to
    match the desktop app's previous local `max(0, ...)` behavior in
    users_page.py.

    Raises ValueError("user_not_found") if user_id doesn't exist.
    """
    if amount == 0:
        raise ValueError("amount_must_be_nonzero")

    with SessionLocal() as session:
        user = session.get(User, user_id)
        if user is None:
            raise ValueError("user_not_found")

        user.credits = max(0, user.credits + amount)
        session.commit()
        session.refresh(user)
        return _to_dict(user)


def verify_login(email: str, password: str) -> dict | None:
    """Returns the user row on a correct email+password match, else None.

    Checks via bcrypt (core.security.verify_password). If the stored
    value doesn't look like a bcrypt hash at all -- a plaintext row left
    over from before hashing existed -- falls back to a direct compare,
    and on a match, transparently rehashes and persists it so the row is
    self-healed for next time (see module docstring)."""
    user = get_user_by_email(email)
    if user is None:
        return None

    stored = user["password"]
    if looks_hashed(stored):
        return user if verify_password(password, stored) else None

    # Legacy plaintext row.
    if stored == password:
        healed = update_user(user["id"], password=password)
        return healed if healed is not None else user
    return None


def delete_user(user_id: int) -> None:
    with SessionLocal() as session:
        user = session.get(User, user_id)
        if user is not None:
            session.delete(user)
            session.commit()

# ---------------------------------------------------------------------------
# REWARDS -- Phase 2/3: real CRUD for the admin Rewards page, replacing
# rewards_page.py's in-memory mutation of data.REWARDS.
# ---------------------------------------------------------------------------

def list_rewards() -> list[dict]:
    with SessionLocal() as session:
        rows = session.scalars(select(Reward).order_by(Reward.created_at.desc())).all()
        claims_map = _reward_claims_map(session)
        return [_reward_to_dict(r, claims_map.get(r.id, 0)) for r in rows]


def get_reward(reward_id: str) -> dict | None:
    with SessionLocal() as session:
        reward = session.get(Reward, reward_id)
        if reward is None:
            return None
        return _reward_to_dict(reward, _reward_claim_count(session, reward_id))


def create_reward(reward_id: str, **fields) -> dict:
    with SessionLocal() as session:
        reward = Reward(id=reward_id, **fields)
        session.add(reward)
        session.commit()
        session.refresh(reward)
        return _reward_to_dict(reward, _reward_claim_count(session, reward_id))


def update_reward(reward_id: str, **fields) -> dict | None:
    with SessionLocal() as session:
        reward = session.get(Reward, reward_id)
        if reward is None:
            return None
        for col, value in fields.items():
            setattr(reward, col, value)
        session.commit()
        session.refresh(reward)
        return _reward_to_dict(reward, _reward_claim_count(session, reward_id))


def delete_reward(reward_id: str) -> None:
    with SessionLocal() as session:
        reward = session.get(Reward, reward_id)
        if reward is not None:
            session.delete(reward)
            session.commit()


# ---------------------------------------------------------------------------
# USER PROGRESS -- generic "did this user do X" counter, driven by real app
# events (login, search, export, etc.) instead of hardcoded status strings
# in earn_credits.py's EARN_CREDITS_SECTIONS.
# ---------------------------------------------------------------------------

def record_progress(user_id: int, progress_key: str, progress_type: str = "COUNT",
                     increment: int = 1) -> dict:
    """Bumps (or creates) this user's progress row for progress_key. For
    COUNT, adds `increment`. For STREAK, the caller is responsible for
    deciding whether today continues or resets the streak (needs a
    calendar-day comparison against last_progress_date) -- left for
    Phase 5 when this gets wired into the login flow."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with SessionLocal() as session:
        row = session.scalar(
            select(UserProgress).where(
                UserProgress.user_id == user_id,
                UserProgress.progress_key == progress_key,
                UserProgress.progress_type == progress_type,
            )
        )
        if row is None:
            row = UserProgress(
                user_id=user_id, progress_key=progress_key, progress_type=progress_type,
                progress_value=increment, last_progress_date=now, last_activity_at=now,
            )
            session.add(row)
        else:
            row.progress_value += increment
            row.last_progress_date = now
            row.last_activity_at = now
        session.commit()
        session.refresh(row)
        return {c.name: getattr(row, c.name) for c in UserProgress.__table__.columns}


def record_login_streak(user_id: int) -> dict:
    """Login-streak specific counterpart to record_progress() -- this is
    the calendar-day comparison record_progress()'s own docstring flags
    as the caller's responsibility for STREAK progress_type, which
    nothing ever actually implemented, which is why Login Streak (3/7
    Days) was permanently stuck at 0 for every user regardless of how
    many times they logged in (see PROGRESS_REWARDS.md Phase 5). Call
    once per successful login (see ui/dialogs/dialogs.py's
    _on_login_clicked). Stores date-only (not a full timestamp) in
    last_progress_date so "same day" / "consecutive day" comparisons are
    simple string equality against date.today():
      - same calendar day as last login -> no change (logging out and
        back in twice today isn't two days of streak)
      - exactly one calendar day later -> streak continues, +1
      - any bigger gap (or first-ever login) -> streak resets to 1"""
    today = datetime.now().date()
    today_str = today.isoformat()
    with SessionLocal() as session:
        row = session.scalar(
            select(UserProgress).where(
                UserProgress.user_id == user_id,
                UserProgress.progress_key == "LOGIN",
                UserProgress.progress_type == "STREAK",
            )
        )
        now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if row is None:
            row = UserProgress(
                user_id=user_id, progress_key="LOGIN", progress_type="STREAK",
                progress_value=1, last_progress_date=today_str, last_activity_at=now_ts,
            )
            session.add(row)
        elif row.last_progress_date == today_str:
            pass  # already logged in today -- no change
        else:
            last_date = None
            try:
                last_date = datetime.strptime(row.last_progress_date, "%Y-%m-%d").date()
            except (TypeError, ValueError):
                pass
            if last_date is not None and today - last_date == timedelta(days=1):
                row.progress_value += 1
            else:
                row.progress_value = 1
            row.last_progress_date = today_str
            row.last_activity_at = now_ts
        session.commit()
        session.refresh(row)
        return {c.name: getattr(row, c.name) for c in UserProgress.__table__.columns}


def get_user_progress(user_id: int, progress_key: str, progress_type: str = "COUNT") -> int:
    """Read-only counterpart to record_progress() -- returns the current
    progress_value for this user/key/type, or 0 if the user hasn't
    generated any progress yet (no row exists). Used by earn_credits.py
    to compute a task's real in_progress/claimable state instead of a
    hardcoded status string (see PROGRESS_REWARDS.md Phase 3)."""
    if not progress_key:
        return 0
    with SessionLocal() as session:
        row = session.scalar(
            select(UserProgress).where(
                UserProgress.user_id == user_id,
                UserProgress.progress_key == progress_key,
                UserProgress.progress_type == progress_type,
            )
        )
        return row.progress_value if row is not None else 0


# ---------------------------------------------------------------------------
# USER REWARDS -- per-user claim state for the generic (non-referral)
# rewards a user completes via record_progress()'d actions, and the
# "Claim" button on the Earn Credits tab that cashes them in.
# ---------------------------------------------------------------------------

def _user_reward_to_dict(ur) -> dict | None:
    if ur is None:
        return None
    return {c.name: getattr(ur, c.name) for c in UserReward.__table__.columns}


def get_user_reward(user_id: int, reward_id: str) -> dict | None:
    with SessionLocal() as session:
        row = session.scalar(
            select(UserReward).where(
                UserReward.user_id == user_id, UserReward.reward_id == reward_id,
            )
        )
        return _user_reward_to_dict(row)


def claim_reward(user_id: int, reward_id: str) -> dict:
    """Cashes in a completed reward: credits the user's real `credits`
    balance (not pending_bonus_credits -- there's no second login-time
    reconciliation step for these like there is for referrals), bumps
    UserReward.claim_count/completed, and writes a reward_logs row.

    Raises ValueError instead of returning None so routers/rewards.py
    (Phase 6) can map each case to a distinct HTTP status/message rather
    than one generic "couldn't claim":
      - "reward_not_found" -- no such reward, or its status isn't Active
      - "reward_not_complete" -- the user's progress hasn't reached
        target_value yet
      - "already_claimed" -- a one-time reward (reset_interval ==
        "NONE") that's already been claimed once; re-claiming a
        repeatable (DAILY/WEEKLY/...) reward is allowed instead and
        just bumps claim_count again, since a new period's worth of
        progress is what got them back to "complete"
      - "user_not_found" -- shouldn't happen in practice, same
        reasoning as every other post-get_current_user lookup in this
        file, but don't leak it as an unhandled crash if it somehow does
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with SessionLocal() as session:
        reward = session.get(Reward, reward_id)
        if reward is None or reward.status != "Active":
            raise ValueError("reward_not_found")

        progress_value = 0
        if reward.progress_key:
            prow = session.scalar(
                select(UserProgress).where(
                    UserProgress.user_id == user_id,
                    UserProgress.progress_key == reward.progress_key,
                    UserProgress.progress_type == reward.progress_type,
                )
            )
            progress_value = prow.progress_value if prow is not None else 0
        if progress_value < reward.target_value:
            raise ValueError("reward_not_complete")

        ur = session.scalar(
            select(UserReward).where(
                UserReward.user_id == user_id, UserReward.reward_id == reward_id,
            )
        )
        if ur is not None and ur.completed and reward.reset_interval == "NONE":
            raise ValueError("already_claimed")

        if ur is None:
            ur = UserReward(
                user_id=user_id, reward_id=reward_id, started_at=now,
                claim_count=0, completed=False,
            )
            session.add(ur)

        ur.progress_value = progress_value
        ur.completed = True
        ur.completed_at = ur.completed_at or now
        ur.claim_count = (ur.claim_count or 0) + 1
        ur.last_progress_at = now
        ur.last_claimed_at = now

        user = session.get(User, user_id)
        if user is None:
            raise ValueError("user_not_found")
        user.credits += reward.value
        reward.claims += 1

        session.add(RewardLog(
            user_id=user_id, reward_id=reward_id,
            reward_type="CREDITS", reward_value=reward.value,
        ))

        session.commit()
        session.refresh(ur)
        session.refresh(user)
        return {
            "user_reward": _user_reward_to_dict(ur),
            "user": _to_dict(user),
            "reward_value": reward.value,
        }


def get_total_credits_earned(user_id: int) -> int:
    """Lifetime credits earned via the rewards system (claims + referral
    payouts), summed straight from reward_logs -- both claim_reward() and
    try_reward_referral() write a row there, so this one query covers
    every source instead of needing separate running totals."""
    with SessionLocal() as session:
        total = session.scalar(
            select(func.coalesce(func.sum(RewardLog.reward_value), 0))
            .where(RewardLog.user_id == user_id)
        )
        return int(total or 0)


# ---------------------------------------------------------------------------
# REFERRALS -- Phase 4: replaces the instant "both sides get credited at
# signup" behavior in create_user() with a proper PENDING -> REWARDED flow,
# a real monthly cap (counted, not just labeled), and a reward_logs trail.
# ---------------------------------------------------------------------------

def _period_start(reset_interval: str) -> datetime | None:
    now = datetime.now()
    if reset_interval == "DAILY":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if reset_interval == "WEEKLY":
        return (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    if reset_interval == "MONTHLY":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if reset_interval == "YEARLY":
        return now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    return None  # NONE -- no periodic reset, cap is lifetime


def create_referral(referrer_user_id: int, referred_user_id: int,
                     referrer_reward_id: str | None = None,
                     referee_reward_id: str | None = None) -> dict | None:
    """Records that referred_user_id signed up with referrer_user_id's
    code. Does NOT pay out yet -- stays PENDING until try_reward_referral()
    confirms the referred user completed the qualifying action (and the
    referrer hasn't hit their monthly cap). Returns None (no row created)
    for a self-referral, matching the DBML fraud-guard note."""
    if referrer_user_id == referred_user_id:
        return None
    with SessionLocal() as session:
        ref = Referral(
            referrer_user_id=referrer_user_id, referred_user_id=referred_user_id,
            referrer_reward_id=referrer_reward_id, referee_reward_id=referee_reward_id,
            status="PENDING",
        )
        session.add(ref)
        session.commit()
        session.refresh(ref)
        return _referral_to_dict(ref)


def try_reward_referral(referred_user_id: int, progress_key: str) -> dict | None:
    """Call this from wherever the qualifying action actually happens
    (e.g. search_leads.py after a user's first successful search, if the
    referral reward's progress_key is SEARCH). Looks up this user's
    PENDING referral; if the referrer hasn't hit their reset_interval cap
    yet, pays out both sides, writes reward_logs rows, and flips the
    referral to REWARDED. Otherwise leaves it PENDING (does not cancel --
    a full cap just means "not this period")."""
    with SessionLocal() as session:
        ref = session.scalar(
            select(Referral).where(
                Referral.referred_user_id == referred_user_id,
                Referral.status == "PENDING",
            )
        )
        if ref is None or not REFERRAL_PROGRAM_ENABLED:
            return None

        reward = session.get(Reward, ref.referrer_reward_id) if ref.referrer_reward_id else None
        if reward is None or reward.progress_key != progress_key or reward.status != "Active":
            return None

        period_start = _period_start(reward.reset_interval)
        cap_query = select(func.count()).select_from(Referral).where(
            Referral.referrer_user_id == ref.referrer_user_id,
            Referral.status == "REWARDED",
        )
        if period_start is not None:
            cap_query = cap_query.where(Referral.rewarded_at >= period_start.isoformat())
        already_rewarded_this_period = session.scalar(cap_query)

        if already_rewarded_this_period >= reward.target_value:
            return None  # cap hit for this period -- stays PENDING, no payout

        referrer = session.get(User, ref.referrer_user_id)
        referee = session.get(User, ref.referred_user_id)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        referrer.pending_bonus_credits += reward.value
        referee.pending_bonus_credits += reward.value

        ref.status = "REWARDED"
        ref.reward_value = reward.value
        ref.referee_reward_value = reward.value
        ref.rewarded_at = now

        session.add(RewardLog(
            user_id=referrer.id, reward_id=reward.id, referral_id=ref.id,
            reward_type="CREDITS", reward_value=reward.value,
        ))
        session.add(RewardLog(
            user_id=referee.id, reward_id=reward.id, referral_id=ref.id,
            reward_type="CREDITS", reward_value=reward.value,
        ))
        reward.claims += 1

        session.commit()
        session.refresh(ref)
        return _referral_to_dict(ref)


def list_referrals_for_user(referrer_user_id: int) -> list[dict]:
    with SessionLocal() as session:
        rows = session.scalars(
            select(Referral).where(Referral.referrer_user_id == referrer_user_id)
        ).all()
        return [_referral_to_dict(r) for r in rows]


def sync_referral_progress() -> int:
    """One-time repair helper for databases that already have referrals
    predating the record_progress() wiring above (see
    PROGRESS_REWARDS.md Phase 5) -- recomputes every referrer's REFERRAL
    UserProgress row from the real users.referred_by_user_id counts, so
    existing accounts get credit for friends they already referred
    without needing a brand-new referral to trigger it. Sets the value
    directly (never lowers it) rather than incrementing, so it's safe to
    run more than once. Returns how many users' progress rows changed."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with SessionLocal() as session:
        counts = dict(
            session.execute(
                select(User.referred_by_user_id, func.count(User.id))
                .where(User.referred_by_user_id.is_not(None))
                .group_by(User.referred_by_user_id)
            ).all()
        )
        touched = 0
        for referrer_id, count in counts.items():
            row = session.scalar(
                select(UserProgress).where(
                    UserProgress.user_id == referrer_id,
                    UserProgress.progress_key == "REFERRAL",
                    UserProgress.progress_type == "COUNT",
                )
            )
            if row is None:
                row = UserProgress(
                    user_id=referrer_id, progress_key="REFERRAL",
                    progress_type="COUNT", progress_value=count,
                    last_progress_date=now, last_activity_at=now,
                )
                session.add(row)
                touched += 1
            elif row.progress_value < count:
                row.progress_value = count
                row.last_progress_date = now
                row.last_activity_at = now
                touched += 1
        session.commit()
        return touched


def get_referral_credits_earned(user_id: int) -> int:
    """Real total this user has actually been credited through referrals
    -- sums reward_logs rows tied to a referral (referral_id is not null)
    for this user, on either side (as referrer or as the referred user).
    Replaces settings_page.py's old `friends_joined * REFERRAL_BONUS_CREDITS`
    guess, which didn't reflect the real per-reward value or whether a
    payout had actually happened yet (see PROGRESS_REWARDS.md Phase 5)."""
    with SessionLocal() as session:
        total = session.scalar(
            select(func.coalesce(func.sum(RewardLog.reward_value), 0)).where(
                RewardLog.user_id == user_id,
                RewardLog.referral_id.is_not(None),
            )
        )
        return int(total or 0)