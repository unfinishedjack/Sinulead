"""
app/security.py

Direct port of the desktop app's core/security.py -- bcrypt password
hashing. Only two functions matter to callers:
    hash_password(plain)           -> str, safe to store in `password`
    verify_password(plain, stored) -> bool

looks_hashed() lets db.py's verify_login() recognize and transparently
upgrade a pre-existing plaintext row (e.g. a sinulead.db copied over
from the old prototype) instead of just breaking that login.
"""

import bcrypt

_MAX_PASSWORD_BYTES = 72


def hash_password(plain: str) -> str:
    if not plain:
        raise ValueError("hash_password: password can't be empty")
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, stored: str) -> bool:
    if not plain or not stored:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), stored.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def looks_hashed(value: str) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 60
        and value.startswith(("$2a$", "$2b$", "$2y$"))
    )
