"""
app/schemas.py

Pydantic request/response models for the API. Starts with just the
Phase 1 auth shapes; later phases add their own request/response models
here rather than routers building raw dicts.
"""

from pydantic import BaseModel, EmailStr, Field


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: str = Field(min_length=1)
    referred_by_code: str | None = None
    avatar: str | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: int
    email: str
    full_name: str
    role: str
    status: str
    avatar: str | None = None
    phone: str | None = None
    company: str | None = None
    company_role: str | None = None
    credits: int
    spent: int
    referral_code: str | None = None
    referred_by_user_id: int | None = None
    pending_bonus_credits: int
    # Was missing here even though User.created_at/db._to_dict() always
    # had it -- Pydantic silently drops any field not declared on the
    # response_model, so GET /me (and /auth/login, /auth/signup, which
    # all return this same shape) never sent it to the client, even
    # though widgets.py's UserAccountBox has always read
    # user["created_at"] for the account panel's "Joined" date. Not
    # something Phase 5's original migration introduced -- this schema
    # just never had the field to begin with.
    created_at: str

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class CheckEmailResponse(BaseModel):
    exists: bool


class CheckReferralCodeResponse(BaseModel):
    valid: bool


class RequestOtpRequest(BaseModel):
    email: EmailStr


class RequestOtpResponse(BaseModel):
    sent: bool
    # How long the caller should wait before calling /auth/request-otp
    # again for this email -- server-owned constant (app/otp.py's
    # OTP_RESEND_COOLDOWN_SECONDS) so the client doesn't hardcode it.
    resend_after_seconds: int


class VerifyOtpRequest(BaseModel):
    email: EmailStr
    code: str = Field(min_length=6, max_length=6)


class VerifyOtpResponse(BaseModel):
    verified: bool


class UpdateMeRequest(BaseModel):
    """Self-service profile edit -- backs widgets.py's Edit Account
    dialog (UserAccountBox.open_edit_profile). Deliberately the same
    field set that local update_user() call already sent (full_name,
    email, phone, company, company_role, avatar, password) -- NOT the
    same as AdminUpdateUserRequest, which is a different, admin-only
    endpoint. `role`, `status`, `credits`, `spent`, and
    `pending_bonus_credits` are impossible to set through this request
    on purpose: a user editing their own profile must never be able to
    grant themselves admin, reactivate a suspended account, or set
    their own balance."""
    full_name: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    company: str | None = None
    company_role: str | None = None
    avatar: str | None = None
    password: str | None = Field(default=None, min_length=8)


class ClaimPendingBonusResponse(BaseModel):
    """POST /me/claim-pending-bonus -- atomically moves
    pending_bonus_credits into the real balance and zeroes it back out,
    same transaction dashboard.py's __init__ used to do with a direct
    local update_user(credits=..., pending_bonus_credits=0) call.
    Doing this as its own narrow endpoint (rather than letting
    UpdateMeRequest touch credits) means a client can only ever move an
    amount the server already allocated, never set an arbitrary
    balance."""
    credits: int
    spent: int
    pending_bonus_credits: int
    claimed: int


class CreditsOut(BaseModel):
    credits: int
    spent: int
    # Currently held by open reservations (see ReserveCreditsRequest
    # below) -- not yet spent, but not free to spend again either.
    # Defaults to 0 so existing callers that don't care about
    # reservations don't need to change anything.
    reserved: int = 0


class SpendCreditsRequest(BaseModel):
    amount: int = Field(gt=0)
    reason: str | None = None


class SpendCreditsResponse(BaseModel):
    credits: int
    spent: int
    amount_spent: int
    reason: str | None = None


class AdjustCreditsRequest(BaseModel):
    # Positive to grant, negative to deduct -- zero is rejected since
    # it's not a meaningful admin action. Unlike SpendCreditsRequest's
    # amount (always positive, always a deduction), this one carries
    # the sign because a single admin route handles both buttons.
    amount: int = Field(ne=0)


class AdjustCreditsResponse(BaseModel):
    user_id: int
    credits: int
    amount_adjusted: int


class ReserveCreditsRequest(BaseModel):
    amount: int = Field(gt=0)
    reason: str | None = None


class ReserveCreditsResponse(BaseModel):
    reservation_id: int
    amount: int
    status: str
    credits: int
    reserved: int
    spent: int


class ResolveReservationResponse(BaseModel):
    reservation_id: int
    amount: int
    status: str
    credits: int
    reserved: int
    spent: int


class CreateSupportTicketRequest(BaseModel):
    subject: str = Field(min_length=1)
    message: str = Field(min_length=1)


class SupportTicketOut(BaseModel):
    id: int
    subject: str
    message: str
    status: str
    created_at: str


class ListSupportTicketsResponse(BaseModel):
    tickets: list[SupportTicketOut]


class StartSearchRequest(BaseModel):
    title: str = Field(min_length=1)
    header: str | None = None
    query_text: str = Field(min_length=1)


class StartSearchResponse(BaseModel):
    search_id: int
    title: str
    header: str | None = None
    query_text: str
    created_at: str
    credits: int
    spent: int
    cost: int


class SearchOut(BaseModel):
    """One entry in GET /searches -- same shape as StartSearchResponse's
    search fields minus the credit/cost info (that was only relevant at
    the moment of creation, not on every later listing), plus `id`
    instead of `search_id` to match LeadOut's `search_id` FK naming from
    the other side."""
    id: int
    title: str
    header: str | None = None
    query_text: str
    created_at: str


class ListSearchesResponse(BaseModel):
    searches: list[SearchOut]


class ExportOut(BaseModel):
    """One row in GET /exports -- backs the Exports tab's table, Export
    Summary donut, and KPI cards, replacing client/data/exports.py's
    static EXPORTS list."""
    id: int
    search_id: int | None = None
    file_name: str
    source: str
    leads_count: int | None = None
    format: str
    status: str
    file_path: str | None = None
    created_at: str


class ListExportsResponse(BaseModel):
    exports: list[ExportOut]


class CreateExportRequest(BaseModel):
    """Logged by the desktop app right after data/lead_exporter.py
    finishes writing a file (or fails to) -- see
    search_leads.py's export_current_search()."""
    file_name: str = Field(min_length=1)
    source: str = Field(min_length=1)
    format: str = Field(min_length=1)
    leads_count: int | None = None
    status: str = Field(default="Completed", pattern="^(Completed|Processing|Failed)$")
    file_path: str | None = None
    search_id: int | None = None


class CreateExportResponse(ExportOut):
    """Response for POST /exports -- ExportOut's fields plus the
    caller's post-charge balance, same "echo the new balance back"
    convention as StartSearchResponse/PostSearchResultsResponse. `cost`
    is 0 whenever nothing was actually charged (status != "Completed",
    or export_cost is currently 0/free)."""
    credits: int
    spent: int
    cost: int


class RenameExportRequest(BaseModel):
    file_name: str = Field(min_length=1)


class LogActivityRequest(BaseModel):
    """Body for POST /searches/{id}/activity and POST /exports/{id}/activity
    -- one durable log line. `text` is the short headline row
    (\"Credits deducted\", \"Export renamed\", ...); `meta` is the optional
    dim-colored detail line under it (e.g. \"5 credit(s) for unlocking
    ...\"), same two-line shape the Activity Log cards have always
    rendered, just persisted now instead of only living in memory."""
    text: str = Field(min_length=1)
    meta: str | None = None


class ActivityEntryOut(BaseModel):
    id: int
    entity_type: str
    entity_id: int
    text: str
    meta: str | None = None
    created_at: str


class ListActivityResponse(BaseModel):
    activity: list[ActivityEntryOut]


class LeadIn(BaseModel):
    """One scraped business, as posted by the desktop app once
    ScraperWorker finishes. Keys match search_leads.py's
    _scraped_card_to_business() output 1:1 -- unlocked_phone/
    unlocked_email are deliberately absent, every freshly-posted lead
    starts locked server-side."""
    name: str = Field(min_length=1)
    rating: float | None = None
    reviews: int = 0
    category: str | None = None
    status: str | None = None
    desc: str | None = None
    address: str | None = None
    hours: str | None = None
    site: str | None = None
    maps_url: str | None = None
    phone_num: str | None = None
    email_addr: str | None = None


class PostSearchResultsRequest(BaseModel):
    leads: list[LeadIn]


class LeadOut(BaseModel):
    id: int
    search_id: int
    name: str
    rating: float | None = None
    reviews: int
    category: str | None = None
    status: str | None = None
    desc: str | None = None
    address: str | None = None
    hours: str | None = None
    site: str | None = None
    maps_url: str | None = None
    phone_num: str | None = None
    email_addr: str | None = None
    unlocked_phone: bool
    unlocked_email: bool
    created_at: str


class PostSearchResultsResponse(BaseModel):
    search_id: int
    count: int
    leads: list[LeadOut]
    credits: int
    spent: int
    cost: int


class ListAllLeadsResponse(BaseModel):
    """GET /leads response -- every Lead the caller owns, across every
    search they've run (not scoped to one search_id like
    ListLeadsResponse). Backs the Overview dashboard's real stat cards
    and charts (client/ui/pages/overview.py) instead of the old
    data/overview_stats.py mock lists."""
    leads: list[LeadOut]


class ListLeadsResponse(BaseModel):
    """GET /searches/{id}/leads response -- includes the parent search's
    own fields (SearchOut) alongside its leads so the desktop app can
    populate both the results table AND the header/title strip from one
    request, without a second round trip to GET /searches for the same
    search it just clicked into."""
    search: SearchOut
    leads: list[LeadOut]


class UnlockLeadRequest(BaseModel):
    field: str = Field(default="all", pattern="^(phone|email|all)$")


class UnlockLeadResponse(BaseModel):
    lead: LeadOut
    credits: int
    spent: int
    cost: int


# ---------------------------------------------------------------------------
# Phase 6 -- rewards / progress / referrals (earn_credits.py's flow)
# ---------------------------------------------------------------------------

class RewardOut(BaseModel):
    id: str
    name: str
    description: str | None = None
    type: str
    value: int
    progress_key: str | None = None
    progress_type: str | None = None
    target_value: int
    reset_interval: str
    status: str
    icon: str
    start_date: str | None = None
    end_date: str | None = None
    claims: int
    prerequisite_reward_id: str | None = None
    created_at: str
    updated_at: str


class ListRewardsResponse(BaseModel):
    rewards: list[RewardOut]


class RecordProgressRequest(BaseModel):
    progress_key: str = Field(min_length=1)
    progress_type: str = Field(default="COUNT", pattern="^(COUNT|STREAK)$")
    increment: int = Field(default=1, gt=0)


class ProgressOut(BaseModel):
    user_id: int
    progress_key: str
    progress_type: str
    progress_value: int
    last_progress_date: str | None = None
    last_activity_at: str | None = None


class ProgressValueOut(BaseModel):
    """GET /me/progress/{progress_key} response -- lighter than
    ProgressOut since db.get_user_progress() only tracks the running
    count/streak value, not the full UserProgress row (no
    last_progress_date/last_activity_at without a second query nothing
    else needs yet)."""
    progress_key: str
    progress_type: str
    progress_value: int


class UserRewardOut(BaseModel):
    user_id: int
    reward_id: str
    progress_value: int
    completed: bool
    claim_count: int
    started_at: str | None = None
    completed_at: str | None = None
    last_progress_at: str | None = None
    last_claimed_at: str | None = None


class ClaimRewardResponse(BaseModel):
    user_reward: UserRewardOut
    credits: int
    spent: int
    reward_value: int


class GetUserRewardResponse(BaseModel):
    """GET /me/rewards/{reward_id} response -- `user_reward` is null if
    the caller has never made progress on this reward at all yet (no
    UserReward row exists), which is a normal, common state (most
    rewards on the Earn Credits tab start this way), not an error."""
    user_reward: UserRewardOut | None = None


class TryReferralRequest(BaseModel):
    progress_key: str = Field(min_length=1)


class ReferralOut(BaseModel):
    id: int
    referrer_user_id: int
    referred_user_id: int
    referrer_reward_id: str | None = None
    referee_reward_id: str | None = None
    status: str
    reward_value: int
    referee_reward_value: int
    expires_at: str | None = None
    rewarded_at: str | None = None
    created_at: str


class TryReferralResponse(BaseModel):
    rewarded: bool
    referral: ReferralOut | None = None


class ListReferralsResponse(BaseModel):
    referrals: list[ReferralOut]


class TotalCreditsEarnedResponse(BaseModel):
    total_credits_earned: int


class ReferralCreditsEarnedResponse(BaseModel):
    referral_credits_earned: int


# ---------------------------------------------------------------------------
# ADMIN -- rewards_page.py's reward CRUD and users_page.py's user list/
# update, gated behind require_admin (app/auth.py). Closes the gap
# PROGRESS.md's Phase 6 note flagged as still blocking Phase 5.
# ---------------------------------------------------------------------------

class CreateRewardRequest(BaseModel):
    # Reward.id is a caller-chosen string slug (e.g. "rwd_welcome_bonus"),
    # not an autoincrement int -- see db.py's Reward model docstring --
    # so unlike every other create-style request in this file, the
    # caller supplies the id rather than the server generating one.
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str | None = None
    type: str
    value: int = Field(ge=0)
    progress_key: str | None = None
    progress_type: str | None = None
    target_value: int = Field(default=1, ge=1)
    reset_interval: str = "NONE"
    status: str = "Active"
    icon: str = "fa5s.gift"
    start_date: str | None = None
    end_date: str | None = None
    prerequisite_reward_id: str | None = None


class UpdateRewardRequest(BaseModel):
    # Every field optional -- partial update, same shape as
    # AdminUpdateUserRequest below. Only fields the caller actually
    # sets get passed through to db.update_reward().
    name: str | None = None
    description: str | None = None
    type: str | None = None
    value: int | None = Field(default=None, ge=0)
    progress_key: str | None = None
    progress_type: str | None = None
    target_value: int | None = Field(default=None, ge=1)
    reset_interval: str | None = None
    status: str | None = None
    icon: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    prerequisite_reward_id: str | None = None


class AdminUserOut(UserOut):
    """Same shape as UserOut (the login/signup response) plus the
    timestamp columns only the admin Users table needs
    (_format_joined/_format_last_active in users_page.py) --
    updated_at/last_active_at aren't on UserOut itself since /auth/login
    and /auth/signup callers never used them and there's no reason to
    widen every token response for an admin-only screen. (created_at
    is redeclared here only because it's now on UserOut too -- kept for
    clarity, not because it needs different behavior.)"""
    created_at: str
    updated_at: str
    last_active_at: str | None = None


class ListUsersResponse(BaseModel):
    users: list[AdminUserOut]


class AdminUpdateUserRequest(BaseModel):
    """Partial update for the admin Users table -- everything optional,
    only set fields are passed to db.update_user(). Deliberately
    excludes credits/spent (POST /admin/users/{id}/credits/adjust in
    credits.py already owns balance changes, and mixing the two here
    would let an admin bypass that endpoint's grant/deduct semantics)
    and password (no admin "reset password" flow exists server-side
    yet -- users_page.py's Reset Password button still shows the
    themed "not wired up" dialog, see that file's module docstring)."""
    full_name: str | None = None
    role: str | None = None
    status: str | None = None
    avatar: str | None = None
    phone: str | None = None
    company: str | None = None
    company_role: str | None = None
    pending_bonus_credits: int | None = None


# ---------------------------------------------------------------------------
# MAINTENANCE -- backs maintenance_tab.py (admin control panel) and
# maintenance_screen.py (the user-facing "under maintenance" screen),
# both of which read/write core/maintenance_state.py's thin QDateTime
# adapter over data.db's MaintenanceState table directly today. The
# table + get/update functions already existed server-side since Phase
# 0's wholesale transplant -- only the HTTP surface was missing.
# ---------------------------------------------------------------------------

class MaintenanceStateOut(BaseModel):
    id: int
    enabled: bool
    title: str
    description: str
    start_datetime: str | None = None
    end_datetime: str | None = None
    duration_minutes: int
    allow_admin_access: bool
    show_countdown: bool
    countdown_format: str


class PricingSettingsOut(BaseModel):
    id: int
    search_cost: int
    lead_unlock_email_cost: int
    lead_unlock_phone_cost: int
    export_cost: int


class UpdatePricingSettingsRequest(BaseModel):
    # All optional -- same partial-update shape as
    # UpdateMaintenanceStateRequest below (admin sends only the field(s)
    # they actually changed).
    search_cost: int | None = None
    lead_unlock_email_cost: int | None = None
    lead_unlock_phone_cost: int | None = None
    export_cost: int | None = None


class ScraperSettingsOut(BaseModel):
    # The 11 syncable scraper-behaviour fields (see
    # app/config.py's DEFAULT_SCRAPER_SETTINGS) plus id -- shared shape
    # for both AdminScraperSettings and GlobalScraperSettings responses,
    # since the two tables have identical columns aside from id/user_id.
    id: int
    max_scrolls_per_query: int
    scroll_wait_ms: int
    max_concurrent_tabs: int
    query_timeout_seconds: int
    stale_rounds_threshold: int
    headless: bool
    close_after_run: bool
    phone_default_region: str
    enable_email_enrichment: bool
    email_max_concurrent_tabs: int
    website_timeout_seconds: int


class UpdateScraperSettingsRequest(BaseModel):
    # Same 11 fields, all optional -- partial-update shape mirroring
    # UpdatePricingSettingsRequest above. One schema, reused for both
    # PATCH /admin/scraper-settings/me and PATCH
    # /admin/scraper-settings/global request bodies.
    max_scrolls_per_query: int | None = None
    scroll_wait_ms: int | None = None
    max_concurrent_tabs: int | None = None
    query_timeout_seconds: int | None = None
    stale_rounds_threshold: int | None = None
    headless: bool | None = None
    close_after_run: bool | None = None
    phone_default_region: str | None = None
    enable_email_enrichment: bool | None = None
    email_max_concurrent_tabs: int | None = None
    website_timeout_seconds: int | None = None


class UpdateMaintenanceStateRequest(BaseModel):
    # Every field optional -- maintenance_tab.py calls update_maintenance_state()
    # with a single changed field at a time in most places (e.g. just
    # `title=`, just `duration_minutes=`), same partial-update shape as
    # AdminUpdateUserRequest/UpdateRewardRequest above.
    enabled: bool | None = None
    title: str | None = None
    description: str | None = None
    start_datetime: str | None = None
    end_datetime: str | None = None
    duration_minutes: int | None = None
    allow_admin_access: bool | None = None
    show_countdown: bool | None = None
    countdown_format: str | None = None