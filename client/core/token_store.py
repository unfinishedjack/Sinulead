"""
core/token_store.py

Persists the JWT (and last-fetched user dict) across app restarts, so
closing the app without logging out doesn't force a fresh login next
launch -- core/session.py's docstring called this "Phase 1" behavior
and explicitly left it for later; this is that later.

Tries the OS keychain first, via the `keyring` package (Windows
Credential Locker, macOS Keychain, Secret Service/KWallet on Linux) --
the token sits encrypted by the OS, unreadable by other processes or
by just opening a file. Falls back to a plain JSON file in the user's
app-data dir if no keychain backend is available (e.g. a headless
Linux box with no Secret Service daemon running) -- meaningfully worse
security (anyone with filesystem access can read the token), but
better than forcing a login every single launch on those boxes.

Only ever holds ONE session -- this is a single-account desktop app,
not a multi-profile one, so there's a single fixed keychain "username"
rather than one entry per email.

Nothing here decides whether a saved token is still *valid* -- that's
core/session.py's restore_session() calling GET /me and clearing on a
401. This module is just the disk/keychain read-write layer under it.
"""

import json
import os
from pathlib import Path

import keyring
from keyring.errors import NoKeyringError, PasswordDeleteError

SERVICE_NAME = "sinulead"
KEYCHAIN_USER = "session"  # fixed slot -- single-session app, see module docstring

_FALLBACK_DIR = Path(os.getenv("APPDATA") or Path.home() / ".config") / "sinulead"
_FALLBACK_FILE = _FALLBACK_DIR / "session.json"


def save(token: str, user: dict) -> None:
    """Persists token+user. Called from core.session.set_session()."""
    payload = json.dumps({"token": token, "user": user})
    try:
        keyring.set_password(SERVICE_NAME, KEYCHAIN_USER, payload)
    except NoKeyringError:
        _FALLBACK_DIR.mkdir(parents=True, exist_ok=True)
        _FALLBACK_FILE.write_text(payload, encoding="utf-8")
        try:
            os.chmod(_FALLBACK_FILE, 0o600)  # best-effort; no-op on Windows
        except OSError:
            pass


def load() -> dict | None:
    """Returns {"token": ..., "user": ...} if a session was saved, else None.
    Called once at startup by core.session.restore_session()."""
    raw = None
    try:
        raw = keyring.get_password(SERVICE_NAME, KEYCHAIN_USER)
    except NoKeyringError:
        if _FALLBACK_FILE.exists():
            raw = _FALLBACK_FILE.read_text(encoding="utf-8")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        # Corrupted/partial write -- treat as "no saved session" rather
        # than crashing startup over it.
        return None


def clear() -> None:
    """Wipes any persisted session. Called from core.session.clear_session()
    (both the explicit-logout path and restore_session()'s reject-on-401 path)."""
    try:
        keyring.delete_password(SERVICE_NAME, KEYCHAIN_USER)
    except (NoKeyringError, PasswordDeleteError):
        pass
    if _FALLBACK_FILE.exists():
        _FALLBACK_FILE.unlink()
