"""
core/session.py

Holds the JWT (and last-fetched user dict) for the currently logged-in
user, in memory, for the lifetime of the process -- AND persists it via
core/token_store.py (OS keychain, file fallback), so closing the app
without hitting Logout doesn't force a fresh login next launch.

Deliberately a plain module-level dict rather than a class -- every part
of the app that needs the token (api_client calls made after login,
since every endpoint past login/signup requires `Authorization: Bearer
...`) just does:

    from core.session import get_token
    headers = {"Authorization": f"Bearer {get_token()}"}

instead of threading a session object through every function call.

set_session()/clear_session() are called from three places:
LoginDialog/SignupDialog's success paths (dialogs.py), the logout
action (main.py's outer loop, on `window.logged_out`), and
restore_session() below when a saved token turns out to be invalid.

restore_session() is the new bit -- called once, at startup, by
main.py, *before* deciding whether to show the login screen or go
straight to the Dashboard. It only loads what was on disk into memory;
it does NOT verify the token is still valid (expired, or revoked
server-side) -- main.py does that with one GET /me call right after,
and calls clear_session() again if that 401s.
"""

from core import token_store

_state = {"token": None, "user": None}


def set_session(token: str, user: dict) -> None:
    _state["token"] = token
    _state["user"] = user
    token_store.save(token, user)


def clear_session() -> None:
    _state["token"] = None
    _state["user"] = None
    token_store.clear()


def restore_session() -> bool:
    """Loads a previously-saved token/user (if any) into memory. Returns
    True if something was loaded -- callers still need to confirm it's
    actually valid (e.g. GET /me) before trusting it, since this can't
    tell an expired/revoked token from a live one on its own."""
    saved = token_store.load()
    if saved is None:
        return False
    _state["token"] = saved.get("token")
    _state["user"] = saved.get("user")
    return _state["token"] is not None


def get_token() -> str | None:
    return _state["token"]


def get_user() -> dict | None:
    return _state["user"]


def is_logged_in() -> bool:
    return _state["token"] is not None
