"""
core/api_client.py

Phase 1 -- auth over HTTP (login/signup). Phase 2 adds the credits
endpoints (get_credits/spend_credits). Thin `httpx` wrapper around the
backend (see server/app/routers/auth.py and routers/credits.py).

The server is now the one doing password verification, JWT issuance,
credit balance checks/deductions, and the post-auth side effects
(stamping last_active_at, bumping the login streak, recording
ACCOUNT_CREATED progress) -- see those routers' docstrings. The
desktop app's job here is just: send the request (attaching the
bearer token from core/session.py for anything past login/signup),
surface a friendly error message on failure, and hand back the parsed
response on success.

Base URL comes from the SINULEAD_API_URL environment variable (settable
via the local .env file -- see core/env.py) so a packaged build can point
at a real deployment without a code change; defaults to a local dev
server for anyone running `uvicorn app.main:app --reload` out of server/.
"""

import os

import httpx

from core.session import get_token

API_BASE_URL = os.environ.get("SINULEAD_API_URL", "https://sinulead-machine-linux.tail127b85.ts.net").rstrip("/") #http://127.0.0.1:8000

_TIMEOUT = 15.0  # seconds -- generous but bounded; this is a modal login dialog, it shouldn't hang forever


class ApiError(Exception):
    """Raised for anything the caller should show to the user as-is:
    a 4xx with a `detail` message from the server, or a friendly
    fallback for connection failures/timeouts/unexpected server errors."""


def _auth_headers() -> dict:
    token = get_token()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _friendly_detail(detail) -> str | None:
    """Turns FastAPI's `detail` field into a single human-readable
    string, whatever shape it came in as.

    Most of our own endpoints raise HTTPException(detail="a plain
    string"), which passes straight through. But FastAPI/Pydantic's
    automatic 422 request-validation errors put a *list* of error dicts
    in `detail` instead (one per bad field, each with its own 'msg'),
    e.g.:
        [{'type': 'value_error', 'loc': ['body', 'email'],
          'msg': 'value is not a valid email address...', ...}]
    Without this, that whole list gets str()'d and dumped verbatim into
    the UI. Here we just pull out the 'msg' text(s) instead.
    """
    if detail is None:
        return None
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):
        messages = [
            item.get("msg") for item in detail
            if isinstance(item, dict) and item.get("msg")
        ]
        if messages:
            return " ".join(messages)
        return None
    if isinstance(detail, dict):
        return detail.get("msg")
    return None


def _request(method: str, path: str, json: dict | None = None, params: dict | None = None) -> dict:
    url = f"{API_BASE_URL}{path}"
    try:
        response = httpx.request(
            method, url, json=json, params=params, headers=_auth_headers(), timeout=_TIMEOUT,
        )
    except httpx.ConnectError:
        raise ApiError("Can't reach the server. Check your internet connection and try again.")
    except httpx.TimeoutException:
        raise ApiError("The server took too long to respond. Please try again.")
    except httpx.HTTPError:
        raise ApiError("Something went wrong talking to the server. Please try again.")

    if response.status_code >= 400:
        try:
            raw_detail = response.json().get("detail")
        except ValueError:
            raw_detail = None
        detail = _friendly_detail(raw_detail)
        raise ApiError(detail or f"Request failed ({response.status_code}). Please try again.")

    # 204 No Content (e.g. DELETE /admin/rewards/{id}) has no body to
    # parse -- every other response here is JSON.
    if response.status_code == 204 or not response.content:
        return {}
    return response.json()


def _post(path: str, json: dict) -> dict:
    return _request("POST", path, json=json)


def _get(path: str, params: dict | None = None) -> dict:
    return _request("GET", path, params=params)


def _patch(path: str, json: dict) -> dict:
    return _request("PATCH", path, json=json)


def _delete(path: str) -> dict:
    return _request("DELETE", path)


def login(email: str, password: str) -> dict:
    """POST /auth/login. Returns the TokenResponse body
    ({"access_token", "token_type", "user"}) on success; raises ApiError
    (with a message safe to show directly) on invalid credentials, a
    suspended account, or a connection problem."""
    return _post("/auth/login", {"email": email, "password": password})


def signup(email: str, password: str, full_name: str,
           referred_by_code: str | None = None, avatar: str | None = None) -> dict:
    """POST /auth/signup. Same return/raise shape as login().

    As of Phase 8 this requires `email` to have a currently-valid
    verify_otp_code() success on file server-side (see
    server/app/routers/auth.py's signup(), app/otp.py's
    is_verified/OTP_VERIFIED_TTL_SECONDS) -- call request_otp() then
    verify_otp_code() first, same order SignupDialog already follows.
    Raises ApiError with "Please verify your email first." if called
    without that, rather than silently accepting an unverified email."""
    return _post("/auth/signup", {
        "email": email,
        "password": password,
        "full_name": full_name,
        "referred_by_code": referred_by_code,
        "avatar": avatar,
    })


def get_credits() -> dict:
    """GET /me/credits. Returns {"credits": int, "spent": int} for the
    logged-in user (see core/session.py for the token this sends).
    Raises ApiError on a missing/expired token or a connection problem."""
    return _get("/me/credits")


def get_me() -> dict:
    """GET /me. Full profile row for the logged-in user -- same shape
    /auth/login's response.user has. Backs widgets.py's UserAccountBox,
    which used to build its profile dict from a direct
    data.db.get_user_by_email() call."""
    return _get("/me")


def update_me(**fields) -> dict:
    """PATCH /me. Self-service profile edit -- pass only the fields
    being changed (full_name, email, phone, company, company_role,
    avatar, password). Backs widgets.py's Edit Account dialog. Can't
    touch credits/role/status -- see UpdateMeRequest's docstring
    server-side; the server silently drops anything outside its
    allowed field set rather than erroring, so passing an unsupported
    key here just has no effect instead of raising."""
    return _patch("/me", fields)


def claim_pending_bonus() -> dict:
    """POST /me/claim-pending-bonus. Atomically moves pending_bonus_credits
    into the real balance -- backs dashboard.py's login-time cash-in,
    which used to be a direct local update_user() call. Safe to call
    unconditionally on every login; a no-op if nothing's pending."""
    return _post("/me/claim-pending-bonus", {})


def spend_credits(amount: int, reason: str | None = None) -> dict:
    """POST /credits/spend. Deducts `amount` from the logged-in user's
    balance atomically on the server (see server/app/db.py's
    spend_credits docstring) and returns the post-spend
    {"credits", "spent", "amount_spent", "reason"}.

    Raises ApiError with a message safe to show directly -- in
    particular a 402 ("Not enough credits for this action.") if the
    balance was too low, which can legitimately happen even after a
    client-side check if another spend landed first (e.g. two tabs, or
    a bulk unlock racing a row unlock)."""
    return _post("/credits/spend", {"amount": amount, "reason": reason})


def unlock_lead(lead_id: int, field: str = "all") -> dict:
    """POST /leads/{lead_id}/unlock. Atomically checks-and-deducts
    credits for whichever of phone/email `field` ("phone", "email", or
    "all") targets, AND persists the matching unlocked_* column(s) on
    the Lead row server-side, in one transaction (see
    server/app/routers/leads.py, server/app/db.py's unlock_lead).

    Use this instead of spend_credits() + a local flag flip for
    unlocking contact info -- spend_credits alone only deducts credits
    without saving which fields got unlocked, so the unlock silently
    reverts to locked next time this search's leads are reloaded from
    the server (e.g. after logging out and back in), even though the
    credits already spent stay spent.

    Returns {"lead", "credits", "spent", "cost"} -- `lead` is the
    updated LeadOut dict (already carrying the new unlocked_phone/
    unlocked_email), so callers can pass it straight to
    _lead_to_business() instead of hand-updating the local business
    dict.

    Raises ApiError with a message safe to show directly -- in
    particular a 402 ("Not enough credits to unlock this contact.") if
    the balance was too low, or a 404 if the lead doesn't exist or
    belongs to another account."""
    return _post(f"/leads/{lead_id}/unlock", {"field": field})


def start_search(title: str, query_text: str, header: str | None = None) -> dict:
    """POST /searches/start. Atomically checks-and-deducts SEARCH_COST
    credits and creates the Search row server-side (see server/app/db.py's
    start_search docstring), returning
    {"search_id", "title", "header", "query_text", "created_at",
     "credits", "spent", "cost"}.

    Call this *before* running ScraperWorker locally (see
    SearchLeadsPage._start_real_search) -- results get POSTed back in as
    Lead rows separately once the scrape finishes (PROGRESS.md Phase 3's
    next step). Raises ApiError with a message safe to show directly --
    in particular a 402 ("Not enough credits to start this search.") if
    the balance is too low."""
    return _post("/searches/start", {
        "title": title,
        "header": header,
        "query_text": query_text,
    })


def list_searches() -> list[dict]:
    """GET /searches. The logged-in user's own past searches, newest
    first (see server/app/db.py's list_searches docstring) -- no leads
    on each entry, just enough to populate the sidebar's history cards.
    Pair with get_search_leads() below for a given search's actual
    results. Replaces the desktop app's old in-memory `data.SEARCHES`
    list (always empty on every launch) now that Search rows are
    server-backed (PROGRESS.md Phase 4)."""
    return _get("/searches")["searches"]


def list_all_leads() -> list[dict]:
    """GET /leads. Every lead the logged-in user owns, across every
    search they've ever run (newest first) -- not scoped to one
    search_id like get_search_leads() below. Backs the Overview
    dashboard's real stat cards / charts (see ui/pages/overview.py),
    replacing the old data/overview_stats.py mock lists."""
    return _get("/leads")["leads"]


def get_search_leads(search_id: int) -> dict:
    """GET /searches/{search_id}/leads. Returns {"search": {...},
    "leads": [...]} -- the parent search's own fields plus every Lead
    row on it, in insertion order (see server/app/db.py's
    get_search_leads docstring). Raises ApiError -- in particular a 404
    if `search_id` doesn't exist or belongs to another account."""
    return _get(f"/searches/{search_id}/leads")


def post_search_results(search_id: int, leads: list[dict]) -> dict:
    """POST /searches/{search_id}/results. Bulk-writes `leads` (business
    dicts already run through search_leads.py's _scraped_card_to_business
    -- same keys the Lead table expects) onto the given Search, once
    ScraperWorker's finished_ok fires. Returns
    {"search_id", "count", "leads"} on success.

    Raises ApiError -- in particular a 404 if `search_id` doesn't exist
    or doesn't belong to the logged-in account."""
    return _post(f"/searches/{search_id}/results", {"leads": leads})


def delete_search(search_id: int) -> None:
    """DELETE /searches/{search_id}. Soft-deletes the search server-side
    (see server/app/db.py's delete_search docstring) -- the row and its
    leads stay in the DB, it just stops coming back from list_searches()
    on next login. Called from SearchLeadsPage.remove_search() when the
    recent-search card's x button is clicked. Raises ApiError -- in
    particular a 404 if `search_id` doesn't exist, doesn't belong to the
    logged-in account, or was already deleted."""
    _delete(f"/searches/{search_id}")


def list_exports() -> list[dict]:
    """GET /exports. The logged-in user's own export history, newest
    first (see server/app/db.py's list_exports docstring). Replaces
    the desktop app's old data.EXPORTS static list (PROGRESS.md
    Exports-tab cutover) -- backs exports.py's ExportsPage the same way
    list_searches() backs the search history sidebar."""
    return _get("/exports")["exports"]


def create_export(file_name: str, source: str, format: str,
                   leads_count: int | None = None, status: str = "Completed",
                   file_path: str | None = None, search_id: int | None = None) -> dict:
    """POST /exports. Logs one export right after
    data/lead_exporter.py finishes writing the file to disk (or right
    after it raises, with status="Failed" and leads_count=None) -- see
    search_leads.py's export_current_search(). Charges the account's
    export_cost server-side, but only when status=="Completed" -- a
    logged failure never costs credits.

    Returns the created export's fields plus "credits"/"spent" (the
    balance after any charge) and "cost" (0 if nothing was actually
    charged) -- see schemas.CreateExportResponse."""
    return _post("/exports", {
        "file_name": file_name, "source": source, "format": format,
        "leads_count": leads_count, "status": status,
        "file_path": file_path, "search_id": search_id,
    })


def rename_export(export_id: int, file_name: str) -> dict:
    """PATCH /exports/{export_id}. Backs the Exports tab row menu's
    Rename action. Raises ApiError -- in particular a 404 if
    `export_id` doesn't exist or belongs to another account."""
    return _patch(f"/exports/{export_id}", {"file_name": file_name})


def delete_export(export_id: int) -> None:
    """DELETE /exports/{export_id}. Backs the Exports tab row menu's
    Delete Export action -- hard-deletes the history row server-side.
    Raises ApiError -- in particular a 404 if `export_id` doesn't
    exist or belongs to another account."""
    _delete(f"/exports/{export_id}")


def list_search_activity(search_id: int) -> list[dict]:
    """GET /searches/{search_id}/activity. Every durable log line
    recorded on this search, oldest first (see server/app/db.py's
    list_activity docstring) -- replaces search_leads.py's old
    approach of re-deriving a shorter activity_log from aggregate lead
    counts on every _load_searches_from_server() call. Raises
    ApiError -- in particular a 404 if search_id doesn't exist or
    belongs to another account."""
    return _get(f"/searches/{search_id}/activity")["activity"]


def log_search_activity(search_id: int, text: str, meta: str | None = None) -> dict:
    """POST /searches/{search_id}/activity. Appends one line to this
    search's Activity Log -- call right after something worth
    recording happens (search completed, an unlock's credit
    deduction, ...). Best-effort from the caller's perspective: a
    failed call here shouldn't block whatever already-successful
    action triggered it (see search_leads.py's _log_activity, which
    catches ApiError around this the same way export_current_search
    already does around create_export)."""
    return _post(f"/searches/{search_id}/activity", {"text": text, "meta": meta})


def list_export_activity(export_id: int) -> list[dict]:
    """GET /exports/{export_id}/activity. Every durable log line
    recorded on this export, oldest first. Raises ApiError -- in
    particular a 404 if export_id doesn't exist or belongs to another
    account."""
    return _get(f"/exports/{export_id}/activity")["activity"]


def log_export_activity(export_id: int, text: str, meta: str | None = None) -> dict:
    """POST /exports/{export_id}/activity. Appends one line to this
    export's Activity Log -- call after export creation/failure,
    rename, delete, a re-downloaded file, or a failed retry."""
    return _post(f"/exports/{export_id}/activity", {"text": text, "meta": meta})


def admin_adjust_credits(user_id: int, amount: int) -> dict:
    """POST /admin/users/{user_id}/credits/adjust. Grants (amount > 0)
    or deducts (amount < 0) credits for another user -- powers
    users_page.py's Add/Deduct Credits buttons. Requires the logged-in
    user to be an admin/superadmin (enforced server-side); raises
    ApiError on a 403 (not an admin), 404 (user_id doesn't exist), or
    connection problem. Returns {"user_id", "credits", "amount_adjusted"}."""
    return _post(f"/admin/users/{user_id}/credits/adjust", {"amount": amount})


# ---------------------------------------------------------------------------
# AUTH pre-checks -- Phase 5. SignupDialog (ui/dialogs/dialogs.py) used to
# call data.db.get_user_by_email/get_user_by_referral_code directly to
# fail fast *before* the OTP step; these wrap the read-only endpoints
# that replace those calls now that the desktop app has no local DB.
# ---------------------------------------------------------------------------

def check_email_exists(email: str) -> bool:
    """GET /auth/check-email. True if an account with this email already
    exists -- SignupDialog shows its "already exists" error and skips
    the OTP step entirely when this is True, same as the old
    get_user_by_email(email) is not None check."""
    return _get("/auth/check-email", params={"email": email})["exists"]


def check_referral_code_valid(code: str) -> bool:
    """GET /auth/check-referral-code. True if `code` matches an existing
    account's referral_code."""
    return _get("/auth/check-referral-code", params={"code": code})["valid"]


def request_otp(email: str) -> dict:
    """POST /auth/request-otp -- Phase 8. Tells the server to generate a
    fresh signup OTP and email it (see server/app/otp.py). This replaces
    the old client-side core/otp.py, which generated the code and sent
    it via smtplib right here in the desktop app -- meaning every
    install needed the real SMTP credential locally. The client no
    longer holds that credential at all; it only ever calls this
    endpoint.

    Returns {"sent": True, "resend_after_seconds": int} on success --
    the latter is a server-owned constant (app/otp.py's
    OTP_RESEND_COOLDOWN_SECONDS), not hardcoded here, so OTPDialog's
    local resend countdown always matches what the server will actually
    allow. Raises ApiError -- in particular a 409 if this email already
    has an account, or a 429 (with a "wait Ns" message safe to show
    directly) if called again before the previous code's cooldown
    elapsed."""
    return _post("/auth/request-otp", {"email": email})


def verify_otp_code(email: str, code: str) -> dict:
    """POST /auth/verify-otp -- Phase 8. Checks `code` against the
    pending OTP for `email` (single-use, expires, rate-limited on wrong
    guesses -- see server/app/otp.py's verify_otp). Returns
    {"verified": True} on success. Raises ApiError with a message safe
    to show directly on an incorrect/expired/already-used code.

    A True return here is required (but not sufficient on its own --
    see signup()'s docstring) for a subsequent signup() call for the
    same email to succeed; the server tracks this itself, so nothing
    needs to be threaded through from here to signup()."""
    return _post("/auth/verify-otp", {"email": email, "code": code})


# ---------------------------------------------------------------------------
# REWARDS/REFERRALS -- Phase 5. Backs rewards_page.py (admin CRUD),
# earn_credits.py (task checklist/claiming), settings_page.py (referral
# stats), and search_leads.py's referral-payout call -- see those
# routers' docstrings in server/app/routers/rewards.py for the full
# per-endpoint behavior; these are thin wrappers, same shape as
# get_credits()/spend_credits() above.
# ---------------------------------------------------------------------------

def list_rewards() -> list[dict]:
    """GET /rewards. Every reward definition (Active/Scheduled/Inactive
    alike) -- not user-scoped."""
    return _get("/rewards")["rewards"]


def get_reward(reward_id: str) -> dict | None:
    """GET /rewards/{id}. The reward definition itself, or None if no
    reward with this id exists (same None-on-missing shape the old
    data.db.get_reward() had, instead of raising)."""
    try:
        return _get(f"/rewards/{reward_id}")
    except ApiError:
        return None


def get_my_reward(reward_id: str) -> dict | None:
    """GET /me/rewards/{id}. The logged-in user's UserReward row for one
    reward, or None if they've never made progress on it."""
    return _get(f"/me/rewards/{reward_id}")["user_reward"]


def get_my_progress(progress_key: str, progress_type: str = "COUNT") -> int:
    """GET /me/progress/{key}. Just the running count/streak value --
    same shape data.db.get_user_progress() returned."""
    return _get(f"/me/progress/{progress_key}", params={"progress_type": progress_type})["progress_value"]


def record_my_progress(progress_key: str, progress_type: str = "COUNT", increment: int = 1) -> dict:
    """POST /me/progress. Bumps (or creates) this user's progress row for
    progress_key -- the write-side counterpart to get_my_progress() above.
    Every other progress-driving action in this app funnels through here
    (or the server calls db.record_progress() directly, as auth.py's
    signup handler does for ACCOUNT_CREATED); callers are responsible for
    not double-counting (e.g. only calling this once a qualifying action
    actually completes, not on every unrelated save)."""
    return _post("/me/progress", {
        "progress_key": progress_key,
        "progress_type": progress_type,
        "increment": increment,
    })


def claim_reward(reward_id: str) -> dict | None:
    """POST /rewards/{id}/claim. Returns the claim result
    ({"user_reward", "credits", "spent", "reward_value"}) on success, or
    None if the claim's no longer valid -- not found, not complete yet,
    or already claimed (same "None means don't bother, just refresh"
    shape earn_credits.py's on_claim() already expects from the old
    data.db.claim_reward()). Still raises ApiError for anything that
    isn't one of those three expected failure cases (a 401, a
    connection problem, etc.) -- those are real errors, not "someone
    else already claimed it."""
    try:
        return _post(f"/rewards/{reward_id}/claim", {})
    except ApiError as exc:
        if any(s in str(exc) for s in ("not found", "hasn't been completed", "already been claimed")):
            return None
        raise


def reward_referral(progress_key: str) -> dict:
    """POST /referrals/reward. Pays out a pending referral if the
    caller's qualifying action just happened -- safe to call on every
    completed search (search_leads.py's _maybe_reward_referral()); a
    no-op ({"rewarded": False}) if there's no pending referral, the cap
    was already hit, or the program's disabled."""
    return _post("/referrals/reward", {"progress_key": progress_key})


def list_my_referrals() -> list[dict]:
    """GET /me/referrals. Every referral where the caller is the
    referrer -- powers settings_page.py's "Friends Joined" count."""
    return _get("/me/referrals")["referrals"]


def get_my_credits_earned() -> int:
    """GET /me/credits-earned. Lifetime credits earned via reward claims
    + referral payouts combined."""
    return _get("/me/credits-earned")["total_credits_earned"]


def get_my_referral_credits_earned() -> int:
    """GET /me/referral-credits-earned. The referral-only subset of the
    above -- powers settings_page.py's "Credits Earned" stat, which
    intentionally excludes non-referral reward claims."""
    return _get("/me/referral-credits-earned")["referral_credits_earned"]


# ---------------------------------------------------------------------------
# ADMIN -- Phase 5b. rewards_page.py's Create/Edit/Duplicate/Delete and
# users_page.py's list/Suspend-Reactivate, all require_admin server-side.
# ---------------------------------------------------------------------------

def admin_list_users() -> list[dict]:
    """GET /admin/users. Every account -- users_page.py filters the
    logged-in admin's own row out client-side, same as it did against
    the local list_users() result before."""
    return _get("/admin/users")["users"]


def admin_update_user(user_id: int, **fields) -> dict:
    """PATCH /admin/users/{id}. Partial update -- pass only the columns
    you're changing (e.g. admin_update_user(3, status='suspended')),
    same call shape data.db.update_user() had."""
    return _patch(f"/admin/users/{user_id}", fields)


def admin_create_reward(reward_id: str, **fields) -> dict:
    """POST /admin/rewards. `reward_id` is the caller-chosen slug (e.g.
    "rwd_welcome_bonus"), same as data.db.create_reward()'s first
    positional arg. Raises ApiError with a 409 message if the id's
    already taken."""
    return _post("/admin/rewards", {"id": reward_id, **fields})


def admin_update_reward(reward_id: str, **fields) -> dict:
    """PATCH /admin/rewards/{id}. Partial update, same call shape
    data.db.update_reward() had."""
    return _patch(f"/admin/rewards/{reward_id}", fields)


def admin_delete_reward(reward_id: str) -> None:
    """DELETE /admin/rewards/{id}. Same silent-no-op-on-missing-id shape
    data.db.delete_reward() had."""
    _delete(f"/admin/rewards/{reward_id}")


# ---------------------------------------------------------------------------
# MAINTENANCE -- Phase 5b. Backs core/maintenance_state.py, which wraps
# these two calls with a QDateTime<->string adapter for start_datetime/
# end_datetime -- see that file. GET is unauthenticated server-side (a
# logged-out user still needs to know if the app's in maintenance mode);
# PATCH requires admin, enforced server-side.
# ---------------------------------------------------------------------------

def get_maintenance_state() -> dict:
    """GET /maintenance."""
    return _get("/maintenance")


def update_maintenance_state(**fields) -> dict:
    """PATCH /admin/maintenance. Partial update, same call shape
    data.db.update_maintenance_state() had. (Admin-write endpoints all
    live under /admin/... server-side -- see routers/users.py,
    routers/credits.py's admin route, routers/rewards.py's admin
    routes -- app/routers/maintenance.py follows the same convention.)"""
    return _patch("/admin/maintenance", fields)


# ---------------------------------------------------------------------------
# PRICING -- backs ui/pages/pricing_tab.py (admin control panel). GET
# requires a signed-in user (any role) so the desktop app can show
# "this costs N credits" before running a search; PATCH requires admin,
# enforced server-side. See app/routers/pricing.py.
# ---------------------------------------------------------------------------

def get_pricing_settings() -> dict:
    """GET /pricing. Returns {"id", "search_cost", "lead_unlock_email_cost",
    "lead_unlock_phone_cost", "export_cost"}."""
    return _get("/pricing")


def update_pricing_settings(**fields) -> dict:
    """PATCH /admin/pricing. Partial update -- pass only the field(s)
    that changed, e.g. update_pricing_settings(search_cost=15)."""
    return _patch("/admin/pricing", fields)


# ---------------------------------------------------------------------------
# SCRAPER SETTINGS -- Phase 7. Backs core/scraper_settings.py, which wraps
# these with the local/server merge + on-disk cache -- see that file. GET
# /scraper-settings requires a signed-in user of any role (this is what
# search_leads.py's New Search flow calls, scope resolved server-side);
# the /admin/scraper-settings/* routes all require admin, enforced
# server-side. See app/routers/scraper_settings.py.
# ---------------------------------------------------------------------------

def get_scraper_settings() -> dict:
    """GET /scraper-settings. The effective synced scraper config for the
    logged-in user: their own AdminScraperSettings row if they're an
    admin/superadmin, otherwise the shared GlobalScraperSettings row.
    Scope is resolved server-side -- callers don't need to know which
    one they got."""
    return _get("/scraper-settings")


def get_my_admin_scraper_settings() -> dict:
    """GET /admin/scraper-settings/me. The logged-in admin's own synced
    scraper config (get-or-create -- always returns a row, seeded from
    defaults the first time). Backs the Web Scraper tab's "My Settings"
    sub-tab."""
    return _get("/admin/scraper-settings/me")


def update_my_admin_scraper_settings(fields: dict) -> dict:
    """PATCH /admin/scraper-settings/me. Partial update on the caller's
    own row -- pass only the field(s) that changed."""
    return _patch("/admin/scraper-settings/me", fields)


def get_global_scraper_settings() -> dict:
    """GET /admin/scraper-settings/global. The single shared scraper
    config every plain `user` account's New Search reads. Backs the Web
    Scraper tab's "Default for Users" sub-tab."""
    return _get("/admin/scraper-settings/global")


def update_global_scraper_settings(fields: dict) -> dict:
    """PATCH /admin/scraper-settings/global. Partial update on the
    shared global row -- pass only the field(s) that changed."""
    return _patch("/admin/scraper-settings/global", fields)


# ---------------------------------------------------------------------------
# SUPPORT -- backs ui/pages/help_support_page.py's contact form. See
# server/app/routers/support.py; no admin-facing endpoints yet, just
# submit + list-your-own.
# ---------------------------------------------------------------------------

def create_support_ticket(subject: str, message: str) -> dict:
    """POST /support/tickets. Returns the created ticket
    ({"id", "subject", "message", "status", "created_at"}). Raises
    ApiError with a message safe to show directly on a validation
    failure (empty subject/message) or a connection problem."""
    return _post("/support/tickets", {"subject": subject, "message": message})


def list_support_tickets() -> list[dict]:
    """GET /support/tickets. The logged-in user's own submitted
    tickets, newest first."""
    return _get("/support/tickets")["tickets"]