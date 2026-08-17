"""
app/routers/users.py

Admin user management -- backs users_page.py, which today reads/writes
the desktop app's local `data.db` directly (list_users(), update_user())
for everything except credits, which already went through
/admin/users/{id}/credits/adjust (see credits.py). This closes the rest
of the gap PROGRESS.md's Phase 6 note flagged as still blocking Phase 5:
the admin Users screen had no server endpoints at all before this file.

Endpoints:

  - GET /admin/users -- every account (db.list_users(), newest-first).
    users_page.py filters the logged-in admin out of the result
    client-side (via current_admin_email) same as it does against the
    local list today, so that filtering isn't duplicated here.

  - PATCH /admin/users/{user_id} -- partial update, powers the
    Suspend/Reactivate quick action (`status`) today and leaves room
    for the rest of AdminUpdateUserRequest's fields (full_name, role,
    etc.) once an editing UI for them exists -- same "endpoint is
    broader than today's only caller" shape as
    /admin/users/{id}/credits/adjust was before Add/Deduct Credits
    were its only two callers.

Both require_admin -- same gate as credits.py's admin route. Unlike
that route, deleting a user isn't exposed here: users_page.py has no
Delete User action today (only Suspend/Reactivate), so
db.delete_user() stays unwired rather than adding an endpoint nothing
calls yet.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app import db
from app.auth import require_admin
from app.schemas import AdminUpdateUserRequest, AdminUserOut, ListUsersResponse

router = APIRouter(prefix="/admin/users", tags=["admin-users"])


@router.get("", response_model=ListUsersResponse)
def admin_list_users(_admin: dict = Depends(require_admin)):
    return ListUsersResponse(users=db.list_users())


@router.patch("/{user_id}", response_model=AdminUserOut)
def admin_update_user(
    user_id: int,
    body: AdminUpdateUserRequest,
    _admin: dict = Depends(require_admin),
):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        user = db.update_user(user_id, **fields)
    except ValueError as exc:
        # Shouldn't happen -- AdminUpdateUserRequest's field names are a
        # subset of VALID_UPDATE_COLUMNS -- but don't leak it as a 500
        # if the two ever drift.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )
    return user