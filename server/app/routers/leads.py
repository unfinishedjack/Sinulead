"""
app/routers/leads.py

Phase 4 -- the desktop app's on_unlock_row (search_leads.py) becomes a
real server call instead of a generic POST /credits/spend + local flag
flip. One endpoint:

  - POST /leads/{lead_id}/unlock -- checks-and-deducts credits for
    whichever of phone/email `field` ("phone", "email", or "all")
    targets, and flips the matching unlocked_* column(s), all in one
    transaction (see db.unlock_lead()). Same pricing rule as the
    desktop app's core/models.py: 1 credit per field that exists on the
    lead and isn't unlocked yet; a field the lead doesn't have, or one
    that's already unlocked, is free and never touches the balance.

Depends on get_current_user (app/auth.py) like every other per-user
write in this backend, so the caller can only ever unlock leads on
their own searches -- there's no user_id in the request body, and
lead_id is ownership-checked server-side via the lead's parent Search
(db.unlock_lead() 404s otherwise, same as searches.py's endpoints do
for a foreign/missing search_id).
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app import db
from app.auth import get_current_user
from app.schemas import UnlockLeadRequest, UnlockLeadResponse, ListAllLeadsResponse

router = APIRouter(tags=["leads"])


@router.get("/leads", response_model=ListAllLeadsResponse)
def list_all_leads(current_user: dict = Depends(get_current_user)):
    """All of the caller's own leads, across every search they've run
    (see db.list_all_leads docstring). Backs the Overview dashboard's
    real stat cards / charts -- unlike GET /searches/{id}/leads, this
    isn't scoped to one search."""
    leads = db.list_all_leads(current_user["id"])
    return ListAllLeadsResponse(leads=leads)


@router.post("/leads/{lead_id}/unlock", response_model=UnlockLeadResponse)
def unlock_lead(
    lead_id: int,
    body: UnlockLeadRequest,
    current_user: dict = Depends(get_current_user),
):
    try:
        result = db.unlock_lead(lead_id, current_user["id"], field=body.field)
    except ValueError as exc:
        reason = str(exc)
        if reason == "lead_not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Lead not found.",
            )
        if reason == "insufficient_credits":
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail="Not enough credits to unlock this contact.",
            )
        # invalid_field is already rejected by the schema's Field(pattern=...);
        # user_not_found shouldn't happen since get_current_user already
        # resolved this same id -- don't leak either as an unhandled 500
        # if they somehow do.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=reason,
        )

    lead = result["lead"]
    user = result["user"]
    return UnlockLeadResponse(
        lead=lead,
        credits=user["credits"],
        spent=user["spent"],
        cost=result["cost"],
    )
