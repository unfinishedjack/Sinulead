"""
app/otp.py

Signup email verification -- moved server-side (Phase 8) from the old
desktop app's client/core/otp.py. That version ran entirely inside the
distributed client, which meant every installed copy of the app had to
carry the real SMTP host/user/password in its local .env just to send
one email -- readable by anyone with the client, and the reason the
prototype's Gmail App Password ended up shipped in a zip and had to be
rotated. The client should never hold a mail credential; it only ever
calls POST /auth/request-otp and POST /auth/verify-otp (see
routers/auth.py) and gets back a plain success/failure.

A code generated here is:
  - cryptographically random (secrets, not random) -- not guessable
  - single-use -- consumed the moment it's checked correctly, so it
    can't be replayed
  - short-lived -- expires after OTP_TTL_SECONDS
  - rate-limited on resend -- OTP_RESEND_COOLDOWN_SECONDS between
    sends to the same email, so "Resend" can't be hammered into a
    send-spam button

Storage is in-memory (plain dicts, keyed by email), which is the same
tradeoff the client-side version made: signup is a short-lived flow,
and losing in-flight codes on a server restart just means the user
requests a new one. If this backend ever runs as more than one process
(e.g. multiple uvicorn workers behind a load balancer), swap these
dicts for Redis/DB-backed storage -- a single dict only works because
today there's exactly one process holding it.

Delivery: if SMTP is configured (settings.smtp_host is set -- see
app/config.py / server/.env), a real email goes out over
smtplib/STARTTLS. If it isn't, the code is logged to the server
console/log instead, clearly labeled as a dev fallback -- this keeps
the API runnable without a mail server while being honest that no
email was actually sent, rather than silently pretending to. That's a
deliberate difference from the old client-side fallback: the printed
code now lands in the *server's* console (wherever `uvicorn` is
running), which only whoever's operating the backend can see -- never
the end user's machine.

A second, separate piece of state lives here too: _verified_store.
Passing the OTP check only proves the caller can read that email's
inbox right now -- it doesn't by itself create the account. auth.py's
signup() checks is_verified() before calling db.create_user(), and
consume_verified() on success, so POST /auth/signup can't be called
directly (skipping the OTP step entirely) by anyone who hasn't just
passed verify-otp for that exact email. Without this check, moving OTP
server-side would just relocate the vulnerability rather than fix it --
the old client-side flow was "trust the desktop UI to have shown an
OTP dialog first," which was never actually enforced anywhere a server
could check.
"""

import logging
import os
import secrets
import smtplib
import time
from email.message import EmailMessage

from app.config import settings

logger = logging.getLogger("sinulead.otp")

OTP_TTL_SECONDS = 10 * 60
OTP_RESEND_COOLDOWN_SECONDS = 30
OTP_MAX_ATTEMPTS = 5  # incorrect guesses allowed before the code is invalidated

# How long a successful verify-otp stays "good" for a follow-up signup
# call -- generous enough that a slow signup form submit right after
# verifying doesn't get bounced, short enough that a verified-but-
# abandoned signup can't be resurrected much later.
OTP_VERIFIED_TTL_SECONDS = 15 * 60

# email (lowercased) -> {"code", "expires_at", "sent_at", "attempts"}
_otp_store: dict[str, dict] = {}

# email (lowercased) -> expires_at -- set the moment verify_otp() succeeds,
# consumed by signup(). See module docstring.
_verified_store: dict[str, float] = {}


def generate_otp() -> str:
    """6-digit code, zero-padded, drawn from a CSPRNG."""
    return f"{secrets.randbelow(1_000_000):06d}"


def _smtp_configured() -> bool:
    return bool(settings.smtp_host)


def send_otp_email(email: str, code: str) -> bool:
    """Sends the OTP over SMTP if configured, otherwise logs it to the
    server console as a clearly-labeled dev fallback. Returns True if a
    real email send was attempted and succeeded, False otherwise (dev
    fallback or send failure) -- callers shouldn't hard-fail the request
    over a False here, since the console fallback is a legitimate dev
    mode, not an error state.
    """
    if not _smtp_configured():
        print(f"[SinuLead] SMTP not configured -- OTP for {email}: {code}")
        logger.info(
            "SMTP not configured (set SMTP_HOST in server/.env to enable "
            "real email delivery) -- printed OTP for %s to console instead",
            email,
        )
        return False

    host = settings.smtp_host
    port = settings.smtp_port
    user = settings.smtp_user or None
    password = settings.smtp_password or None
    from_addr = settings.smtp_from or user or "no-reply@sinulead.local"
    use_tls = settings.smtp_tls

    msg = EmailMessage()
    msg["Subject"] = "Your SinuLead verification code"
    msg["From"] = from_addr
    msg["To"] = email
    msg.set_content(
        f"Your SinuLead verification code is: {code}\n\n"
        f"This code expires in {OTP_TTL_SECONDS // 60} minutes. "
        "If you didn't request this, you can safely ignore this email."
    )

    try:
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            if use_tls:
                smtp.starttls()
            if user and password:
                smtp.login(user, password)
            smtp.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError):
        logger.exception("Failed to send OTP email to %s -- falling back to console", email)
        print(f"[SinuLead] Email send failed -- OTP for {email}: {code}")
        return False


def issue_otp(email: str) -> None:
    """Generates a fresh code for `email`, stores it, and sends it.
    Overwrites any still-pending code for the same email (e.g. a
    resend). Runs the SMTP send synchronously -- fine here because
    FastAPI routes defined with plain `def` (see routers/auth.py) run
    in a threadpool, so this doesn't block the event loop the way it
    would have blocked a Qt UI thread in the old client-side version.
    """
    email = email.lower()
    code = _store_new_code(email)
    send_otp_email(email, code)


def _store_new_code(email: str) -> str:
    email = email.lower()
    code = generate_otp()
    _otp_store[email] = {
        "code": code,
        "expires_at": time.time() + OTP_TTL_SECONDS,
        "sent_at": time.time(),
        "attempts": 0,
    }
    return code


def seconds_until_resend_allowed(email: str) -> int:
    entry = _otp_store.get(email.lower())
    if entry is None:
        return 0
    remaining = OTP_RESEND_COOLDOWN_SECONDS - (time.time() - entry["sent_at"])
    return max(0, int(remaining + 0.999))  # round up so a UI countdown never shows 0 too early


def verify_otp(email: str, code: str) -> tuple[bool, str]:
    """Checks `code` against the pending OTP for `email`. Single-use: a
    correct match consumes the code so it can't be replayed. Returns
    (ok, reason) -- reason is only meaningful when ok is False, one of
    "no_pending", "expired", "too_many_attempts", "incorrect".
    """
    email = email.lower()
    entry = _otp_store.get(email)
    if entry is None:
        return False, "no_pending"

    if time.time() > entry["expires_at"]:
        del _otp_store[email]
        return False, "expired"

    if entry["attempts"] >= OTP_MAX_ATTEMPTS:
        del _otp_store[email]
        return False, "too_many_attempts"

    if secrets.compare_digest(entry["code"], code.strip()):
        del _otp_store[email]
        return True, ""

    entry["attempts"] += 1
    return False, "incorrect"


def clear_otp(email: str) -> None:
    _otp_store.pop(email.lower(), None)


# ---------------------------------------------------------------------------
# Verified-for-signup tracking -- see module docstring.
# ---------------------------------------------------------------------------

def mark_verified(email: str) -> None:
    _verified_store[email.lower()] = time.time() + OTP_VERIFIED_TTL_SECONDS


def is_verified(email: str) -> bool:
    email = email.lower()
    expires_at = _verified_store.get(email)
    if expires_at is None:
        return False
    if time.time() > expires_at:
        del _verified_store[email]
        return False
    return True


def consume_verified(email: str) -> None:
    """Pops the verified flag so the same OTP pass can't back a second
    signup call (e.g. a retried/duplicated request after the first one
    already created the account)."""
    _verified_store.pop(email.lower(), None)
