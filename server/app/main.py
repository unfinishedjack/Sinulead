"""
app/main.py

Entry point -- `uvicorn app.main:app --reload` from the server/ folder.
Phase 1 adds the first real router (auth); credits/searches/leads/
rewards routers get included here in later phases the same way. leads
(Phase 4, POST /leads/{id}/unlock) and rewards (Phase 6, rewards/
progress/referrals) are the two most recently wired in.
"""

from fastapi import FastAPI

from app.db import init_db
from app.routers import auth, credits, searches, leads, rewards, users, maintenance, exports, support, pricing, scraper_settings

app = FastAPI(title="SinuLead API")
app.include_router(auth.router)
app.include_router(credits.router)
app.include_router(searches.router)
app.include_router(leads.router)
app.include_router(rewards.router)
app.include_router(users.router)
app.include_router(maintenance.router)
app.include_router(exports.router)
app.include_router(support.router)
app.include_router(pricing.router)
app.include_router(scraper_settings.router)


@app.on_event("startup")
def _on_startup():
    # Creates tables (users, searches, leads, ...) if they don't exist
    # yet, and seeds the prototype accounts + default maintenance row on
    # a brand-new database only -- never touches an existing one. See
    # app/db.py's init_db() docstring.
    init_db()


@app.get("/health")
def health():
    return {"status": "ok"}