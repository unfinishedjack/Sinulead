"""
app/routers/exports.py

Backend cutover for the Exports tab (see client/data/exports.py's old
docstring and client/ui/pages/exports.py's module docstring -- both
called it a prototype backed by an in-memory static list). Four
endpoints, all scoped to the caller's own history the same way every
other per-user table in this backend is:

  - GET  /exports          -- list the caller's export history, newest
    first (see db.list_exports()).
  - POST /exports          -- log one export the desktop app just wrote
    to disk locally (see db.create_export()). Called from
    search_leads.py's export_current_search() right after
    data/lead_exporter.py finishes writing the file (or right after it
    raises, with status="Failed").
  - PATCH /exports/{id}    -- rename an export (the row "..." menu's
    Rename action).
  - DELETE /exports/{id}   -- remove an export from history (the row
    "..." menu's Delete Export action).

Depends on get_current_user like every other per-user route here, so
there's no user_id in any request body -- ownership is enforced
server-side (db.rename_export/delete_export 404 on a foreign id).
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app import db
from app.auth import get_current_user
from app.schemas import (
    ListExportsResponse, CreateExportRequest, ExportOut, CreateExportResponse,
    RenameExportRequest,
    LogActivityRequest, ActivityEntryOut, ListActivityResponse,
)

router = APIRouter(tags=["exports"])


@router.get("/exports", response_model=ListExportsResponse)
def list_exports(current_user: dict = Depends(get_current_user)):
    exports = db.list_exports(current_user["id"])
    return ListExportsResponse(exports=exports)


@router.post("/exports", response_model=CreateExportResponse, status_code=status.HTTP_201_CREATED)
def create_export(
    body: CreateExportRequest,
    current_user: dict = Depends(get_current_user),
):
    # Admins export free, same convention as searches/unlocks. Everyone
    # else pays export_cost, but only if body.status == "Completed" --
    # see db.create_export's docstring. The desktop app always calls
    # this AFTER data/lead_exporter.py has already written the file (or
    # already failed), so "successfully exported" has already happened
    # by the time this request is even sent.
    cost = 0 if current_user["role"] in ("admin", "superadmin") else db.get_pricing_settings()["export_cost"]
    result = db.create_export(
        current_user["id"],
        file_name=body.file_name,
        source=body.source,
        format=body.format,
        leads_count=body.leads_count,
        status=body.status,
        file_path=body.file_path,
        search_id=body.search_id,
        cost=cost,
    )
    charged = cost if body.status == "Completed" else 0
    return CreateExportResponse(
        **result["export"],
        credits=result["user"]["credits"],
        spent=result["user"]["spent"],
        cost=charged,
    )


@router.patch("/exports/{export_id}", response_model=ExportOut)
def rename_export(
    export_id: int,
    body: RenameExportRequest,
    current_user: dict = Depends(get_current_user),
):
    try:
        export = db.rename_export(export_id, current_user["id"], body.file_name)
    except ValueError as exc:
        if str(exc) == "export_not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Export not found.",
            )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return ExportOut(**export)


@router.delete("/exports/{export_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_export(
    export_id: int,
    current_user: dict = Depends(get_current_user),
):
    try:
        db.delete_export(export_id, current_user["id"])
    except ValueError as exc:
        if str(exc) == "export_not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Export not found.",
            )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("/exports/{export_id}/activity", response_model=ListActivityResponse)
def list_export_activity(
    export_id: int,
    current_user: dict = Depends(get_current_user),
):
    """Exports page's (new) Activity Log card / "View all logs" dialog --
    every durable log line recorded against this export (created,
    renamed, deleted, download re-generated, retry failed, ...),
    oldest first. 404s the same way PATCH/DELETE /exports/{id} do if
    export_id doesn't exist or belongs to another account."""
    try:
        entries = db.list_activity(current_user["id"], "export", export_id)
    except ValueError as exc:
        if str(exc) == "export_not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Export not found.",
            )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return ListActivityResponse(activity=entries)


@router.post(
    "/exports/{export_id}/activity",
    response_model=ActivityEntryOut,
    status_code=status.HTTP_201_CREATED,
)
def log_export_activity(
    export_id: int,
    body: LogActivityRequest,
    current_user: dict = Depends(get_current_user),
):
    """Appends one line to this export's Activity Log -- called right
    after something worth recording happens client-side (export
    created/failed, renamed, downloaded again, retry attempted, ...)."""
    try:
        entry = db.log_activity(current_user["id"], "export", export_id, body.text, body.meta)
    except ValueError as exc:
        if str(exc) == "export_not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Export not found.",
            )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return ActivityEntryOut(**entry)
