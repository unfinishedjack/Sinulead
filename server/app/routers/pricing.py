"""
app/routers/pricing.py

Backs the admin-configurable credit prices (formerly the fixed
SEARCH_COST / LEAD_UNLOCK_FIELD_COST constants in app/config.py, now
db.PricingSettings -- see db.get_pricing_settings()/update_pricing_settings()).
Same two-endpoint shape as routers/maintenance.py:

  - GET /pricing -- behind get_current_user (any signed-in role, not
    just admin) so the desktop app can show "this search costs N
    credits" in its own UI before the user commits to running one,
    without needing an admin token.

  - PATCH /admin/pricing -- require_admin-gated, partial update (only
    send the field(s) that changed), same convention as
    PATCH /admin/maintenance.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app import db
from app.auth import get_current_user, require_admin
from app.schemas import PricingSettingsOut, UpdatePricingSettingsRequest

router = APIRouter(tags=["pricing"])


@router.get("/pricing", response_model=PricingSettingsOut)
def get_pricing(_current_user: dict = Depends(get_current_user)):
    return db.get_pricing_settings()


@router.patch("/admin/pricing", response_model=PricingSettingsOut)
def update_pricing(
    body: UpdatePricingSettingsRequest,
    _admin: dict = Depends(require_admin),
):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        return db.get_pricing_settings()
    try:
        return db.update_pricing_settings(**fields)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
