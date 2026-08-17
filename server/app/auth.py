"""
app/auth.py

JWT issuing/verification for Phase 1 (auth over HTTP). Two things live
here:

  - create_access_token(user) -- called by the /auth/login and
    /auth/signup routes on success. Payload is intentionally tiny (just
    `sub`=user id, plus `role` since several later endpoints will want
    to gate on it without a DB round-trip) -- everything else about the
    user is returned alongside the token in the response body, not
    baked into it, so a profile edit doesn't require re-issuing a token.
  - get_current_user -- an HTTPBearer-based FastAPI dependency, ready
    for Phase 2+ routes (`/me/credits`, `/credits/spend`, etc.) to
    depend on instead of each one reimplementing token parsing. Not
    used by anything yet in Phase 1 itself, but wiring it now means the
    login/signup routes and every route after them share one definition
    of "what a valid token looks like."
"""

from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.config import settings
from app import db

_bearer_scheme = HTTPBearer(auto_error=False)


def create_access_token(user: dict) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": str(user["id"]), "role": user["role"], "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> dict:
    """Raises 401 for a missing/invalid/expired token or a token whose
    user no longer exists; otherwise returns the user dict (same shape
    db.get_user_by_id returns). Not called by any route yet in Phase 1
    -- Phase 2's /me/credits is the first real consumer -- but it's
    defined here so that route (and every one after it) can just
    `Depends(get_current_user)` instead of duplicating this."""
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized

    payload = decode_access_token(credentials.credentials)
    if payload is None or "sub" not in payload:
        raise unauthorized

    user = db.get_user_by_id(int(payload["sub"]))
    if user is None:
        raise unauthorized
    return user


def require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    """Same as get_current_user, plus a 403 if the caller isn't "admin"
    or "superadmin". First consumer: the admin credit-adjustment route
    in routers/credits.py (users_page.py's Add/Deduct Credits buttons),
    which -- unlike /credits/spend -- legitimately needs a user_id in
    the request path since the admin is acting on someone else's
    balance."""
    if current_user["role"] not in ("admin", "superadmin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )
    return current_user