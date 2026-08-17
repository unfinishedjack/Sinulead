"""
app/routers/scraper_settings.py

Backs Phase 7's server-side scraper config (see db.py's
AdminScraperSettings/GlobalScraperSettings and
get_effective_scraper_settings()). Five endpoints:

  - GET /scraper-settings -- behind get_current_user (any signed-in
    role). Calls get_effective_scraper_settings(current_user), so this
    is the one endpoint search_leads.py's New Search flow calls
    regardless of who's asking -- the correct scope (that admin's own
    row, or the shared global row for a plain user) is resolved
    server-side.

  - GET/PATCH /admin/scraper-settings/me -- require_admin-gated, the
    caller's own AdminScraperSettings row (get-or-create on GET).

  - GET/PATCH /admin/scraper-settings/global -- require_admin-gated,
    the single shared GlobalScraperSettings row that "Default for
    Users" reads/writes.

PATCH bodies use the same partial-update convention as
routers/pricing.py: only non-None fields are applied, and an
all-empty body is a no-op that just returns the current row.
"""

from fastapi import APIRouter, Depends

from app import db
from app.auth import get_current_user, require_admin
from app.schemas import ScraperSettingsOut, UpdateScraperSettingsRequest

router = APIRouter(tags=["scraper-settings"])


@router.get("/scraper-settings", response_model=ScraperSettingsOut)
def get_effective_settings(current_user: dict = Depends(get_current_user)):
    return db.get_effective_scraper_settings(current_user)


@router.get("/admin/scraper-settings/me", response_model=ScraperSettingsOut)
def get_my_admin_settings(current_admin: dict = Depends(require_admin)):
    return db.get_admin_scraper_settings(current_admin["id"])


@router.patch("/admin/scraper-settings/me", response_model=ScraperSettingsOut)
def update_my_admin_settings(
    body: UpdateScraperSettingsRequest,
    current_admin: dict = Depends(require_admin),
):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        return db.get_admin_scraper_settings(current_admin["id"])
    return db.update_admin_scraper_settings(current_admin["id"], **fields)


@router.get("/admin/scraper-settings/global", response_model=ScraperSettingsOut)
def get_global_settings(_admin: dict = Depends(require_admin)):
    return db.get_global_scraper_settings()


@router.patch("/admin/scraper-settings/global", response_model=ScraperSettingsOut)
def update_global_settings(
    body: UpdateScraperSettingsRequest,
    _admin: dict = Depends(require_admin),
):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        return db.get_global_scraper_settings()
    return db.update_global_scraper_settings(**fields)