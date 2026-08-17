"""
app/referral.py

Direct port of generate_referral_code from the desktop app's
core/models.py -- the one function db.py needs from that file. Kept as
its own module rather than dumped into db.py so it's easy to find/reuse
(e.g. later from a /auth/signup response).
"""

import random
import string


def generate_referral_code(name: str, existing_codes: set) -> str:
    """4 letters from the name (fallback 'USER') + 4 random digits,
    retried until it doesn't collide with an already-issued code."""
    base = "".join(ch for ch in name.upper() if ch.isalpha())[:4] or "USER"
    base = base.ljust(4, "X")
    while True:
        code = base + "".join(random.choices(string.digits, k=4))
        if code not in existing_codes:
            return code
