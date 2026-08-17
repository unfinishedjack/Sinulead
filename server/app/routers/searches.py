"""
app/routers/searches.py

Phase 3 -- wires the real scraper to regular users, credit-gated. Two
endpoints so far:

  - POST /searches/start -- checks/deducts SEARCH_COST credits and
    creates the Search row, atomically (see db.start_search()). The
    desktop app calls this *before* running ScraperWorker locally; on
    finished_ok it POSTs results back in as Lead rows via the endpoint
    below.

  - POST /searches/{search_id}/results -- bulk-writes ScraperWorker's
    output into the Lead table (see db.add_search_results()), once the
    desktop app's finished_ok handler has already run
    _scraped_card_to_business() on every raw card. Ownership-checked
    against the caller the same way every other per-user lookup here
    is, so a search_id from another account 404s instead of silently
    accepting rows onto it.

Phase 4 adds:

  - GET /searches -- lists the caller's own Search rows, newest first
    (see db.list_searches()). No leads on each entry -- just enough to
    populate the sidebar's history cards.

  - GET /searches/{id}/leads -- the caller's own Search row plus every
    Lead on it, in insertion order (see db.get_search_leads()). This is
    what replaces the desktop app's in-memory SEARCHES list from
    data/leads.py once search_leads.py is wired to call it.

Depends on get_current_user (app/auth.py) like the credits routes do,
so the caller can only ever start/post-to a search for themselves -- no
user_id in either request body, on purpose.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app import db
from app.auth import get_current_user
from app.schemas import (
    StartSearchRequest, StartSearchResponse,
    PostSearchResultsRequest, PostSearchResultsResponse,
    ListSearchesResponse, ListLeadsResponse,
    UnlockLeadRequest, UnlockLeadResponse,
    LogActivityRequest, ActivityEntryOut, ListActivityResponse,
)

router = APIRouter(tags=["searches"])


@router.get("/searches", response_model=ListSearchesResponse)
def list_searches(current_user: dict = Depends(get_current_user)):
    searches = db.list_searches(current_user["id"])
    return ListSearchesResponse(searches=searches)


@router.get("/searches/{search_id}/leads", response_model=ListLeadsResponse)
def get_search_leads(
    search_id: int,
    current_user: dict = Depends(get_current_user),
):
    try:
        result = db.get_search_leads(search_id, current_user["id"])
    except ValueError as exc:
        if str(exc) == "search_not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Search not found.",
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    return ListLeadsResponse(search=result["search"], leads=result["leads"])


@router.post("/searches/start", response_model=StartSearchResponse, status_code=status.HTTP_201_CREATED)
def start_search(
    body: StartSearchRequest,
    current_user: dict = Depends(get_current_user),
):
    # Admin/superadmin still run free; everyone else pays whatever the
    # admin has currently configured in PricingSettings (was a fixed
    # SEARCH_COST constant -- see db.get_pricing_settings()).
    cost = 0 if current_user["role"] in ("admin", "superadmin") else db.get_pricing_settings()["search_cost"]
    try:
        result = db.start_search(
            current_user["id"],
            title=body.title,
            query_text=body.query_text,
            header=body.header,
            cost=cost,
        )
    except ValueError as exc:
        if str(exc) == "insufficient_credits":
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail="Not enough credits to start this search.",
            )
        # cost_must_not_be_negative/user_not_found shouldn't happen in
        # practice (cost is a non-negative admin-configured value, and
        # user_id comes from a token get_current_user already resolved)
        # -- don't leak either as an unhandled 500 if they somehow do.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    search = result["search"]
    user = result["user"]
    return StartSearchResponse(
        search_id=search["id"],
        title=search["title"],
        header=search["header"],
        query_text=search["query_text"],
        created_at=search["created_at"],
        credits=user["credits"],
        spent=user["spent"],
        cost=cost,
    )


@router.post("/searches/{search_id}/results", response_model=PostSearchResultsResponse)
def post_search_results(
    search_id: int,
    body: PostSearchResultsRequest,
    current_user: dict = Depends(get_current_user),
):
    # Same free-for-admins rule as /searches/start -- the actual charge
    # now happens here, only if the scrape came back with leads (see
    # db.add_search_results's docstring).
    cost = 0 if current_user["role"] in ("admin", "superadmin") else db.get_pricing_settings()["search_cost"]
    try:
        result = db.add_search_results(
            search_id,
            current_user["id"],
            leads=[lead.model_dump() for lead in body.leads],
            cost=cost,
        )
    except ValueError as exc:
        if str(exc) == "search_not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Search not found.",
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    return PostSearchResultsResponse(
        search_id=result["search"]["id"],
        count=len(result["leads"]),
        leads=result["leads"],
        credits=result["user"]["credits"],
        spent=result["user"]["spent"],
        cost=cost if result["leads"] else 0,
    )


@router.delete("/searches/{search_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_search(
    search_id: int,
    current_user: dict = Depends(get_current_user),
):
    """Recent-search card's x button. Soft-delete (see db.delete_search) --
    the row and its leads stay in the DB, it just drops out of GET
    /searches. Ownership-checked the same way every other search-scoped
    endpoint here is, so this 404s on someone else's search_id same as
    GET /searches/{id}/leads does."""
    try:
        db.delete_search(search_id, current_user["id"])
    except ValueError as exc:
        if str(exc) == "search_not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Search not found.",
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

@router.get("/searches/{search_id}/activity", response_model=ListActivityResponse)
def list_search_activity(
    search_id: int,
    current_user: dict = Depends(get_current_user),
):
    """Search Leads page's Activity Log card / "View all logs" dialog --
    every durable log line recorded against this search (see
    db.log_activity's docstring), oldest first. 404s the same way
    GET /searches/{id}/leads does if search_id doesn't exist or
    belongs to another account."""
    try:
        entries = db.list_activity(current_user["id"], "search", search_id)
    except ValueError as exc:
        if str(exc) == "search_not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Search not found.",
            )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return ListActivityResponse(activity=entries)


@router.post(
    "/searches/{search_id}/activity",
    response_model=ActivityEntryOut,
    status_code=status.HTTP_201_CREATED,
)
def log_search_activity(
    search_id: int,
    body: LogActivityRequest,
    current_user: dict = Depends(get_current_user),
):
    """Appends one line to this search's Activity Log -- called right
    after something worth recording happens client-side (search
    completed, an unlock's credit deduction, ...). See
    db.log_activity's docstring for why text/meta are trusted as
    given rather than server-computed."""
    try:
        entry = db.log_activity(current_user["id"], "search", search_id, body.text, body.meta)
    except ValueError as exc:
        if str(exc) == "search_not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Search not found.",
            )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return ActivityEntryOut(**entry)
