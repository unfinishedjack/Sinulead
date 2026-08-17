"""
app/routers/auth.py

Phase 1 -- auth over HTTP. Two endpoints, each a thin wrapper around the
existing db.py functions (verify_login / create_user) that already
carry all the real logic (bcrypt, referral bonuses, legacy-plaintext
self-heal, etc. -- see db.py's module docstring); this file's only job
is HTTP plumbing: request validation, status codes, and issuing a JWT
on success.

Mirrors the same post-auth side effects the desktop app's
LoginDialog/SignupDialog do today (see client/ui/dialogs/dialogs.py):
stamping last_active_at, bumping the login streak, and -- for signup --
recording the ACCOUNT_CREATED progress event. Doing that here too means
a client that has fully switched to this endpoint gets the same
behavior it used to get from calling those db.py functions directly.

Phase 8 adds /request-otp and /verify-otp, moving the whole signup-OTP
flow (code generation, SMTP send, verification) server-side -- see
app/otp.py for why (short version: the old client-side version shipped
a real SMTP credential inside the desktop app's own .env). /signup now
requires the target email to be currently OTP-verified (otp.is_verified)
before it will create an account, which also closes the gap where
/signup could previously be called directly, skipping the OTP dialog
entirely, since nothing server-side was actually checking that it ran.
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from sqlalchemy.exc import IntegrityError

from app import db, otp
from app.auth import create_access_token
from app.schemas import (
    LoginRequest, SignupRequest, TokenResponse,
    CheckEmailResponse, CheckReferralCodeResponse,
    RequestOtpRequest, RequestOtpResponse,
    VerifyOtpRequest, VerifyOtpResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])

_OTP_FAILURE_MESSAGES = {
    "no_pending": "No code is pending -- request a new one.",
    "expired": "That code expired -- request a new one.",
    "too_many_attempts": "Too many incorrect attempts -- request a new one.",
    "incorrect": "Incorrect code. Try again.",
}


@router.get("/check-email", response_model=CheckEmailResponse)
def check_email(email: str):
    """GET /auth/check-email?email=... -- read-only pre-check so
    SignupDialog (client/ui/dialogs/dialogs.py) can fail fast with "An
    account with this email already exists" *before* running the OTP
    step, same as it could with a direct data.db.get_user_by_email()
    call. /auth/signup itself still re-checks (and is the real source
    of truth, closing the race this can't) -- this only saves the user
    a wasted OTP round-trip on the common case."""
    return CheckEmailResponse(exists=db.get_user_by_email(email) is not None)


@router.get("/check-referral-code", response_model=CheckReferralCodeResponse)
def check_referral_code(code: str):
    """Same idea as check_email above, for the optional referral-code
    field -- lets SignupDialog show "That referral code doesn't match
    any account" before the OTP step instead of only finding out via
    /auth/signup's 400 afterward."""
    return CheckReferralCodeResponse(valid=db.get_user_by_referral_code(code) is not None)


@router.post("/request-otp", response_model=RequestOtpResponse)
def request_otp(body: RequestOtpRequest):
    """POST /auth/request-otp -- Phase 8. Generates a fresh signup OTP,
    emails it (or, with no SMTP configured, prints it to this server's
    console -- see app/otp.py), and returns how long the caller should
    wait before asking again. This -- not client/core/otp.py, which no
    longer exists -- is now the only place that ever touches the SMTP
    credential; the desktop client just calls this endpoint."""
    email = body.email.lower()
    if db.get_user_by_email(email) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    remaining = otp.seconds_until_resend_allowed(email)
    if remaining > 0:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Please wait {remaining}s before requesting another code.",
        )

    otp.issue_otp(email)
    return RequestOtpResponse(sent=True, resend_after_seconds=otp.OTP_RESEND_COOLDOWN_SECONDS)


@router.post("/verify-otp", response_model=VerifyOtpResponse)
def verify_otp(body: VerifyOtpRequest):
    """POST /auth/verify-otp -- Phase 8. Checks the code against the
    pending OTP for this email (single-use, expires, rate-limited on
    wrong guesses -- see app/otp.py's verify_otp). On success, marks the
    email as OTP-verified for OTP_VERIFIED_TTL_SECONDS: /auth/signup
    below refuses to create an account for an email that isn't
    currently marked verified, which is what actually stops someone
    from calling /auth/signup directly and skipping the OTP step --
    the old client-side flow only *looked* like it enforced that, via
    UI ordering a caller of the raw API was never bound by."""
    email = body.email.lower()
    ok, reason = otp.verify_otp(email, body.code)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_OTP_FAILURE_MESSAGES.get(reason, "Incorrect code. Try again."),
        )
    otp.mark_verified(email)
    return VerifyOtpResponse(verified=True)


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def signup(body: SignupRequest):
    email = body.email.lower()

    if db.get_user_by_email(email) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    if not otp.is_verified(email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please verify your email first.",
        )

    if body.referred_by_code and db.get_user_by_referral_code(body.referred_by_code) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That referral code doesn't match any account.",
        )

    try:
        user = db.create_user(
            body.email, body.password, body.full_name,
            referred_by_code=body.referred_by_code, avatar=body.avatar,
        )
    except IntegrityError:
        # Closes the email-uniqueness race the pre-check above can't
        # fully rule out (two signups for the same address landing
        # concurrently) -- see db.py's create_user docstring.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    otp.consume_verified(email)

    db.record_progress(user["id"], "ACCOUNT_CREATED", "COUNT")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    user = db.update_user(user["id"], last_active_at=now) or user
    db.record_login_streak(user["id"])

    token = create_access_token(user)
    return TokenResponse(access_token=token, user=user)


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest):
    user = db.verify_login(body.email, body.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
        )
    if user["status"] == "suspended":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been suspended. Please contact support.",
        )

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    user = db.update_user(user["id"], last_active_at=now) or user
    db.record_login_streak(user["id"])

    token = create_access_token(user)
    return TokenResponse(access_token=token, user=user)