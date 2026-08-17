"""
app/routers/credits.py

Phase 2 -- credits become server-authoritative. Two endpoints:

  - GET /me/credits -- current balance + lifetime spent, for anywhere
    the desktop app just needs a number to show (header pill,
    dashboard, billing page) without pulling the whole user object.
  - POST /credits/spend -- the one place a credit deduction happens.
    Wraps db.spend_credits(), which does the balance-check-and-deduct
    atomically in a single transaction so two concurrent spends can't
    both read the same starting balance and overdraw the account.

Both endpoints depend on get_current_user (app/auth.py), so the caller
is always spending/reading their own balance -- there's no user_id in
either request body/path, on purpose, so a client can't pass someone
else's id.

Not wired up yet: the desktop app still touches user["credits"]
directly in ui/pages/search_leads.py, ui/pages/billing.py, and
core/models.py. That grep-and-replace is the next PROGRESS.md item
under this same phase, once these endpoints exist to replace it with.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app import db
from app.auth import get_current_user, require_admin
from app.schemas import (
    AdjustCreditsRequest,
    AdjustCreditsResponse,
    CreditsOut,
    ReserveCreditsRequest,
    ReserveCreditsResponse,
    ResolveReservationResponse,
    SpendCreditsRequest,
    SpendCreditsResponse,
    UserOut,
    UpdateMeRequest,
    ClaimPendingBonusResponse,
)

router = APIRouter(tags=["credits"])


@router.get("/me", response_model=UserOut)
def get_me(current_user: dict = Depends(get_current_user)):
    """Self-service profile read -- backs widgets.py's UserAccountBox,
    which used to build its `self.profile` dict from a direct
    data.db.get_user_by_email() call. Returns the same shape
    /auth/login's response.user does."""
    return current_user


@router.patch("/me", response_model=UserOut)
def update_me(
    body: UpdateMeRequest,
    current_user: dict = Depends(get_current_user),
):
    """Self-service profile edit -- backs widgets.py's Edit Account
    dialog. Deliberately narrower than AdminUpdateUserRequest (see that
    schema's docstring): a user can change their own display info and
    password, never their own role/status/balance."""
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        return current_user
    try:
        user = db.update_user(current_user["id"], **fields)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return user


@router.post("/me/claim-pending-bonus", response_model=ClaimPendingBonusResponse)
def claim_pending_bonus(current_user: dict = Depends(get_current_user)):
    """Backs dashboard.py's login-time pending_bonus_credits cash-in --
    used to be a direct local update_user(credits=..., pending_bonus_credits=0)
    call; now an atomic server-side move (see db.claim_pending_bonus's
    docstring for why this is its own narrow endpoint rather than
    letting UpdateMeRequest touch credits)."""
    before = current_user["pending_bonus_credits"]
    user = db.claim_pending_bonus(current_user["id"])
    return ClaimPendingBonusResponse(
        credits=user["credits"],
        spent=user["spent"],
        pending_bonus_credits=user["pending_bonus_credits"],
        claimed=before,
    )


@router.get("/me/credits", response_model=CreditsOut)
def get_my_credits(current_user: dict = Depends(get_current_user)):
    return CreditsOut(
        credits=current_user["credits"],
        spent=current_user["spent"],
        reserved=current_user.get("reserved_credits", 0),
    )


@router.post("/credits/spend", response_model=SpendCreditsResponse)
def spend_credits(
    body: SpendCreditsRequest,
    current_user: dict = Depends(get_current_user),
):
    try:
        user = db.spend_credits(current_user["id"], body.amount, reason=body.reason)
    except ValueError as exc:
        if str(exc) == "insufficient_credits":
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail="Not enough credits for this action.",
            )
        # amount_must_be_positive is already rejected by the schema's
        # Field(gt=0); user_not_found shouldn't happen since
        # get_current_user already resolved this same id -- but don't
        # leak an unhandled ValueError as a 500 if it somehow does.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    return SpendCreditsResponse(
        credits=user["credits"],
        spent=user["spent"],
        amount_spent=body.amount,
        reason=body.reason,
    )


@router.post("/credits/reserve", response_model=ReserveCreditsResponse)
def reserve_credits(
    body: ReserveCreditsRequest,
    current_user: dict = Depends(get_current_user),
):
    """(Optional) Holds `amount` credits without spending them yet --
    for a caller that wants to protect a long-running operation (e.g.
    a slow search) against the balance changing mid-flight, without
    committing to the spend until the operation actually finishes.
    Follow up with POST /credits/reserve/{id}/finalize on success or
    POST /credits/reserve/{id}/release on failure/cancellation -- an
    unresolved reservation just sits as 'held' and keeps its amount
    out of the spendable balance until one of those is called.
    """
    try:
        result = db.reserve_credits(current_user["id"], body.amount, reason=body.reason)
    except ValueError as exc:
        if str(exc) == "insufficient_credits":
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail="Not enough available credits to reserve.",
            )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    reservation, user = result["reservation"], result["user"]
    return ReserveCreditsResponse(
        reservation_id=reservation["id"],
        amount=reservation["amount"],
        status=reservation["status"],
        credits=user["credits"],
        reserved=user["reserved_credits"],
        spent=user["spent"],
    )


@router.post(
    "/credits/reserve/{reservation_id}/finalize",
    response_model=ResolveReservationResponse,
)
def finalize_reservation(
    reservation_id: int,
    current_user: dict = Depends(get_current_user),
):
    """Converts a held reservation into an actual spend -- call this
    once the thing the credits were reserved for (e.g. a search)
    completed successfully."""
    try:
        result = db.finalize_reservation(reservation_id, current_user["id"])
    except ValueError as exc:
        if str(exc) == "reservation_not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Reservation not found.",
            )
        if str(exc) == "reservation_already_resolved":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Reservation was already finalized or released.",
            )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    reservation, user = result["reservation"], result["user"]
    return ResolveReservationResponse(
        reservation_id=reservation["id"],
        amount=reservation["amount"],
        status=reservation["status"],
        credits=user["credits"],
        reserved=user["reserved_credits"],
        spent=user["spent"],
    )


@router.post(
    "/credits/reserve/{reservation_id}/release",
    response_model=ResolveReservationResponse,
)
def release_reservation(
    reservation_id: int,
    current_user: dict = Depends(get_current_user),
):
    """Cancels a held reservation and returns the credits to the free
    balance -- call this if the thing the credits were reserved for
    (e.g. a search) failed or was cancelled before finishing."""
    try:
        result = db.release_reservation(reservation_id, current_user["id"])
    except ValueError as exc:
        if str(exc) == "reservation_not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Reservation not found.",
            )
        if str(exc) == "reservation_already_resolved":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Reservation was already finalized or released.",
            )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    reservation, user = result["reservation"], result["user"]
    return ResolveReservationResponse(
        reservation_id=reservation["id"],
        amount=reservation["amount"],
        status=reservation["status"],
        credits=user["credits"],
        reserved=user["reserved_credits"],
        spent=user["spent"],
    )


@router.post("/admin/users/{user_id}/credits/adjust", response_model=AdjustCreditsResponse)
def adjust_user_credits(
    user_id: int,
    body: AdjustCreditsRequest,
    _admin: dict = Depends(require_admin),
):
    """Powers users_page.py's Add/Deduct Credits buttons. `body.amount`
    carries the sign (positive = grant, negative = deduct); the admin
    performing the action is authenticated via require_admin but is
    never the one whose balance changes -- that's always `user_id` in
    the path."""
    try:
        user = db.admin_adjust_credits(user_id, body.amount)
    except ValueError as exc:
        if str(exc) == "user_not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found.",
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    return AdjustCreditsResponse(
        user_id=user["id"],
        credits=user["credits"],
        amount_adjusted=body.amount,
    )