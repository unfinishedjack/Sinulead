"""
app/routers/rewards.py

Phase 6 -- rewards/referrals become server-authoritative, powering the
desktop app's Earn Credits tab (ui/pages/earn_credits.py). All the
underlying logic already lived in db.py since Phase 0 transplanted it
wholesale (see PROGRESS.md's Phase 6 note); this router is just the
HTTP surface nothing was exposing yet.

Endpoints:

  - GET /rewards -- every reward definition (Active/Scheduled/Inactive
    alike; the desktop app decides what to show/gray out). Read-only,
    no auth-scoped filtering -- reward definitions aren't per-user.

  - POST /me/progress -- bumps (or creates) a progress counter/streak
    for the caller. This is record_progress() -- call it wherever the
    qualifying action actually happens (a completed search, an export,
    etc.), same as auth.py's signup handler already does for
    ACCOUNT_CREATED.

  - GET /me/progress/{progress_key} -- read-only counterpart, defaults
    progress_type to COUNT via a query param since most progress in
    this app is COUNT-type (STREAK is only LOGIN today).

  - GET /me/rewards/{reward_id} -- the caller's UserReward row for one
    reward, or null if they haven't made any progress on it yet.

  - POST /rewards/{reward_id}/claim -- claim_reward(). Raises distinct
    errors for "doesn't exist/not active", "not complete yet", and
    "already claimed" (one-time rewards only) so the desktop app can
    show a specific message instead of one generic failure.

  - POST /referrals/reward -- try_reward_referral() for the caller (the
    referred user whose qualifying action just happened -- mirrors
    search_leads.py's _maybe_reward_referral(), which today calls this
    logic locally via data.db). Silently no-ops (rewarded: false) for
    every "not applicable" case -- no pending referral, cap already hit
    this period, program disabled -- since none of those are errors
    from the caller's point of view, just "nothing to pay out right now".

  - GET /me/referrals -- every referral where the caller is the
    referrer, for a "friends you've referred" list.

  - GET /me/credits-earned -- lifetime credits earned via claims +
    referral payouts (db.get_total_credits_earned()), separate from
    /me/credits' balance/spent so the Earn Credits tab can show
    "lifetime earned" without it being conflated with current balance.

All depend on get_current_user except GET /rewards (reward definitions
aren't user-scoped) -- same as every other per-user route in this
backend, so a caller only ever reads/writes their own progress, claims,
and referrals.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError

from app import db
from app.auth import get_current_user, require_admin
from app.schemas import (
    ListRewardsResponse,
    RecordProgressRequest, ProgressOut, ProgressValueOut,
    GetUserRewardResponse, ClaimRewardResponse,
    TryReferralRequest, TryReferralResponse,
    ListReferralsResponse, TotalCreditsEarnedResponse,
    ReferralCreditsEarnedResponse,
    CreateRewardRequest, UpdateRewardRequest, RewardOut,
)

router = APIRouter(tags=["rewards"])


@router.get("/rewards", response_model=ListRewardsResponse)
def list_rewards():
    return ListRewardsResponse(rewards=db.list_rewards())


@router.get("/rewards/{reward_id}", response_model=RewardOut)
def get_reward(reward_id: str):
    """Single reward definition -- not user-scoped, same as GET /rewards
    above. Powers earn_credits.py's prerequisite-reward lookup (showing
    'Unlocks after you complete "X"' on a locked task), which needs one
    other reward's name/details, not the caller's progress on it (that's
    GET /me/rewards/{reward_id})."""
    reward = db.get_reward(reward_id)
    if reward is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Reward not found.",
        )
    return reward


@router.post("/me/progress", response_model=ProgressOut)
def record_progress(
    body: RecordProgressRequest,
    current_user: dict = Depends(get_current_user),
):
    row = db.record_progress(
        current_user["id"], body.progress_key,
        progress_type=body.progress_type, increment=body.increment,
    )
    return ProgressOut(**row)


@router.get("/me/progress/{progress_key}", response_model=ProgressValueOut)
def get_progress(
    progress_key: str,
    progress_type: str = Query(default="COUNT", pattern="^(COUNT|STREAK)$"),
    current_user: dict = Depends(get_current_user),
):
    value = db.get_user_progress(current_user["id"], progress_key, progress_type=progress_type)
    return ProgressValueOut(progress_key=progress_key, progress_type=progress_type, progress_value=value)


@router.get("/me/rewards/{reward_id}", response_model=GetUserRewardResponse)
def get_my_reward(
    reward_id: str,
    current_user: dict = Depends(get_current_user),
):
    user_reward = db.get_user_reward(current_user["id"], reward_id)
    return GetUserRewardResponse(user_reward=user_reward)


@router.post("/rewards/{reward_id}/claim", response_model=ClaimRewardResponse)
def claim_reward(
    reward_id: str,
    current_user: dict = Depends(get_current_user),
):
    try:
        result = db.claim_reward(current_user["id"], reward_id)
    except ValueError as exc:
        reason = str(exc)
        if reason == "reward_not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Reward not found.",
            )
        if reason == "reward_not_complete":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This reward hasn't been completed yet.",
            )
        if reason == "already_claimed":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This reward has already been claimed.",
            )
        # user_not_found shouldn't happen since get_current_user already
        # resolved this same id -- don't leak it as an unhandled 500 if
        # it somehow does.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=reason,
        )

    user = result["user"]
    return ClaimRewardResponse(
        user_reward=result["user_reward"],
        credits=user["credits"],
        spent=user["spent"],
        reward_value=result["reward_value"],
    )


@router.post("/referrals/reward", response_model=TryReferralResponse)
def reward_referral(
    body: TryReferralRequest,
    current_user: dict = Depends(get_current_user),
):
    referral = db.try_reward_referral(current_user["id"], body.progress_key)
    return TryReferralResponse(rewarded=referral is not None, referral=referral)


@router.get("/me/referrals", response_model=ListReferralsResponse)
def list_my_referrals(current_user: dict = Depends(get_current_user)):
    return ListReferralsResponse(referrals=db.list_referrals_for_user(current_user["id"]))


@router.get("/me/credits-earned", response_model=TotalCreditsEarnedResponse)
def get_my_credits_earned(current_user: dict = Depends(get_current_user)):
    return TotalCreditsEarnedResponse(
        total_credits_earned=db.get_total_credits_earned(current_user["id"])
    )


@router.get("/me/referral-credits-earned", response_model=ReferralCreditsEarnedResponse)
def get_my_referral_credits_earned(current_user: dict = Depends(get_current_user)):
    """Backs settings_page.py's "Credits Earned" referral stat -- the
    referral-only subset of /me/credits-earned's total (that one also
    counts non-referral reward claims). Separate endpoint rather than a
    query param on /me/credits-earned since db.py already keeps these
    as two distinct functions with different WHERE clauses
    (get_total_credits_earned vs get_referral_credits_earned)."""
    return ReferralCreditsEarnedResponse(
        referral_credits_earned=db.get_referral_credits_earned(current_user["id"])
    )


# ---------------------------------------------------------------------------
# ADMIN -- backs rewards_page.py's Create/Edit/Duplicate/Delete actions,
# which today mutate data.db (the desktop app's own local Reward table)
# directly. Same require_admin gate as credits.py's
# /admin/users/{id}/credits/adjust. GET /rewards above stays
# unauthenticated on purpose (reward definitions aren't per-user and
# every signed-in user's Earn Credits tab needs to read them); these
# three are the write side, admin-only.
# ---------------------------------------------------------------------------

@router.post("/admin/rewards", response_model=RewardOut, status_code=status.HTTP_201_CREATED)
def admin_create_reward(
    body: CreateRewardRequest,
    _admin: dict = Depends(require_admin),
):
    fields = body.model_dump(exclude={"id"})
    try:
        reward = db.create_reward(body.id, **fields)
    except IntegrityError:
        # Mirrors routers/auth.py's signup IntegrityError handling --
        # closes the race the caller-chosen id makes possible (two
        # admins creating the same slug concurrently), same as email
        # uniqueness does there.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A reward with this id already exists.",
        )
    return reward


@router.patch("/admin/rewards/{reward_id}", response_model=RewardOut)
def admin_update_reward(
    reward_id: str,
    body: UpdateRewardRequest,
    _admin: dict = Depends(require_admin),
):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    reward = db.update_reward(reward_id, **fields)
    if reward is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Reward not found.",
        )
    return reward


@router.delete("/admin/rewards/{reward_id}", status_code=status.HTTP_204_NO_CONTENT)
def admin_delete_reward(
    reward_id: str,
    _admin: dict = Depends(require_admin),
):
    # Same as db.delete_user()/desktop's delete flows -- deleting a
    # reward_id that doesn't exist is a silent no-op, not a 404. An
    # admin's "Delete" click racing a second tab that already deleted
    # it should still land as success, not an error toast.
    db.delete_reward(reward_id)