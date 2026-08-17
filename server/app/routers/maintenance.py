"""
app/routers/maintenance.py

Backs maintenance_tab.py (admin control panel) and maintenance_screen.py
(the user-facing "under maintenance" screen) -- both of which read/write
core/maintenance_state.py's thin QDateTime adapter over data.db's
MaintenanceState table directly today. The table + get/update functions
already existed server-side (db.get_maintenance_state() /
db.update_maintenance_state()) and the response/request schemas already
existed in schemas.py (MaintenanceStateOut / UpdateMaintenanceStateRequest)
-- this file was the missing piece: main.py already imported and
registered `maintenance.router`, but the router itself was never written,
which left the server unable to start at all.

Endpoints:

  - GET /maintenance -- deliberately NOT behind get_current_user.
    maintenance_screen.py has to be able to show the "under maintenance"
    blocking screen even to a signed-out client (e.g. checked before/
    instead of the login screen), so this has to be reachable with no
    token. Nothing sensitive lives in MaintenanceStateOut.

  - PATCH /admin/maintenance -- require_admin-gated, same pattern as
    users.py/credits.py's admin routes. Partial update, mirroring
    AdminUpdateUserRequest/UpdateRewardRequest's "only send what
    changed" shape -- maintenance_tab.py calls update_maintenance_state()
    with a single changed field at a time in most places.
"""

from fastapi import APIRouter, Depends

from app import db
from app.auth import require_admin
from app.schemas import MaintenanceStateOut, UpdateMaintenanceStateRequest

router = APIRouter(tags=["maintenance"])


@router.get("/maintenance", response_model=MaintenanceStateOut)
def get_maintenance():
    return db.get_maintenance_state()


@router.patch("/admin/maintenance", response_model=MaintenanceStateOut)
def update_maintenance(
    body: UpdateMaintenanceStateRequest,
    _admin: dict = Depends(require_admin),
):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        return db.get_maintenance_state()
    return db.update_maintenance_state(**fields)
