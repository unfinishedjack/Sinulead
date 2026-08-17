"""
app/routers/support.py

Backs the Help and Support nav tab's contact form (client's
ui/pages/help_support_page.py). Intentionally small:

  - POST /support/tickets -- submit a ticket. Requires a token (same as
    every other /me-style route) so the ticket can be tied to an
    account and pre-filled with the caller's email; there's no
    signed-out support form in this app.

  - GET /support/tickets -- the caller's own tickets, newest first, so
    the page can show "your recent requests" under the form.

No admin-facing list/close endpoints yet -- tickets land in the
support_tickets table and get triaged some other way for now (email
notification, direct DB query, etc.), same as how this backend doesn't
yet have an admin UI for every table it owns.
"""

from fastapi import APIRouter, Depends, status

from app import db
from app.auth import get_current_user
from app.schemas import (
    CreateSupportTicketRequest, SupportTicketOut, ListSupportTicketsResponse,
)

router = APIRouter(tags=["support"])


@router.post("/support/tickets", response_model=SupportTicketOut, status_code=status.HTTP_201_CREATED)
def create_support_ticket(
    body: CreateSupportTicketRequest,
    current_user: dict = Depends(get_current_user),
):
    ticket = db.create_support_ticket(
        user_id=current_user["id"],
        email=current_user["email"],
        subject=body.subject,
        message=body.message,
    )
    return SupportTicketOut(**ticket)


@router.get("/support/tickets", response_model=ListSupportTicketsResponse)
def list_support_tickets(current_user: dict = Depends(get_current_user)):
    tickets = db.list_my_support_tickets(current_user["id"])
    return ListSupportTicketsResponse(tickets=tickets)
