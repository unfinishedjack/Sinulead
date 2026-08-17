"""
models.py

Pure business logic (no Qt imports) around the credit-based contact-unlock
system, plus the referral-code helpers used by signup. Kept separate from
widgets/dialogs so these rules can be tested or reused without spinning up
a QApplication.

Pricing rule:
    phone only  OR  email only  -> that field's configured cost
    phone AND email             -> both fields' costs, added together
    neither                     -> free (nothing to unlock)

Phone and email are priced independently server-side (PricingSettings.
lead_unlock_phone_cost / lead_unlock_email_cost -- see server/app/db.py
and ui/pages/pricing_tab.py), so every cost helper below takes the
current per-field prices as arguments rather than assuming 1 credit
each. Callers fetch the live prices once via
core.api_client.get_pricing_settings() and pass them through; a
default of 1/1 is kept on every parameter purely as a safe fallback
for a caller that hasn't wired that fetch through yet, not as a
real price -- the server is always the authority on what actually
gets charged."""

import random
import string


def business_has_phone(biz: dict) -> bool:
    val = biz.get("phone_num", "")
    return bool(val) and val != "--"


def business_has_email(biz: dict) -> bool:
    val = biz.get("email_addr", "")
    return bool(val) and val != "--"


def business_contact_cost(biz: dict, email_cost: int = 1, phone_cost: int = 1) -> int:
    """Total credits to fully unlock this business right now --
    `phone_cost`/`email_cost` for each of phone/email that exists and
    isn't unlocked yet, 0 for a field that's already unlocked or that
    the business doesn't have."""
    cost = 0
    if business_has_phone(biz) and not biz.get("unlocked_phone", False):
        cost += phone_cost
    if business_has_email(biz) and not biz.get("unlocked_email", False):
        cost += email_cost
    return cost


def business_field_cost(biz: dict, field: str, email_cost: int = 1, phone_cost: int = 1) -> int:
    """Credits to unlock just one field ("phone" or "email") on this
    business -- that field's configured cost if it exists and isn't
    unlocked yet, else 0."""
    if field == "phone":
        return phone_cost if (business_has_phone(biz) and not biz.get("unlocked_phone", False)) else 0
    if field == "email":
        return email_cost if (business_has_email(biz) and not biz.get("unlocked_email", False)) else 0
    return 0


def business_fully_unlocked(biz: dict) -> bool:
    """True once both fields that exist on this business are unlocked
    (fields the business doesn't have count as already-satisfied)."""
    phone_ok = biz.get("unlocked_phone", False) or not business_has_phone(biz)
    email_ok = biz.get("unlocked_email", False) or not business_has_email(biz)
    return phone_ok and email_ok


def ensure_unlock_state(data: list) -> list:
    """Adds independent 'unlocked_phone' / 'unlocked_email' flags (default
    False) to any business dict that doesn't already have them -- lets
    old/new datasets mix safely. A field with nothing to unlock (business
    has no phone, or no email) is auto-marked unlocked since there's
    nothing to pay for."""
    for biz in data:
        if "unlocked_phone" not in biz:
            biz["unlocked_phone"] = not business_has_phone(biz)
        if "unlocked_email" not in biz:
            biz["unlocked_email"] = not business_has_email(biz)
    return data


def apply_unlock(biz: dict, mode: str = "all") -> None:
    """Marks the field(s) relevant to `mode` ("phone", "email", or "all")
    unlocked on this business. Used by both the row-level Unlock click and
    bulk unlock so the two stay in sync."""
    if mode in ("phone", "all"):
        biz["unlocked_phone"] = True
    if mode in ("email", "all"):
        biz["unlocked_email"] = True


# ---------------------------------------------------------------------------
# Bulk unlock
#
# Phone and email now unlock independently, tracked by their own
# 'unlocked_phone' / 'unlocked_email' flags. "mode" ("phone", "email", or
# "all") changes both *which checked businesses* a bulk action targets
# (e.g. "Unlock Phone" skips rows with no phone number, or whose phone is
# already unlocked) AND *which field(s)* actually get revealed/charged --
# "Unlock Phone" only ever touches unlocked_phone, never email.
# ---------------------------------------------------------------------------

def bulk_unlock_target_businesses(businesses: list, mode: str = "all") -> list:
    """Filters `businesses` down to the ones relevant to a bulk-unlock
    `mode` ("phone", "email", or "all") that have at least one relevant
    field still locked -- i.e. what a click on that bulk-unlock button
    would affect."""
    def _wants(biz: dict) -> bool:
        if mode == "phone":
            return business_has_phone(biz) and not biz.get("unlocked_phone", False)
        if mode == "email":
            return business_has_email(biz) and not biz.get("unlocked_email", False)
        return not business_fully_unlocked(biz)

    return [biz for biz in businesses if _wants(biz)]


def bulk_unlock_cost(businesses: list, mode: str = "all", email_cost: int = 1, phone_cost: int = 1) -> int:
    """Total credits to unlock every business in `businesses` relevant to
    `mode`. Each business is priced per-field via business_field_cost(),
    so "Unlock Phone" only ever charges for phone, "Unlock Email" only
    for email, and "Both" charges for whichever of the two are still
    locked, at their respective configured prices."""
    def _cost(biz: dict) -> int:
        total = 0
        if mode in ("phone", "all"):
            total += business_field_cost(biz, "phone", email_cost, phone_cost)
        if mode in ("email", "all"):
            total += business_field_cost(biz, "email", email_cost, phone_cost)
        return total

    return sum(_cost(biz) for biz in bulk_unlock_target_businesses(businesses, mode))


# ---------------------------------------------------------------------------
# Referral codes
#
# Every account gets a short, shareable code (e.g. "JUDE4821") generated
# once at account-creation time. Codes are looked up case-insensitively
# since people will be retyping them from a screenshot or a text message.
# ---------------------------------------------------------------------------

def generate_referral_code(name: str, existing_codes: set) -> str:
    """4 letters from the name (fallback 'USER') + 4 random digits, retried
    until it doesn't collide with an already-issued code."""
    base = "".join(ch for ch in name.upper() if ch.isalpha())[:4] or "USER"
    base = base.ljust(4, "X")
    while True:
        code = base + "".join(random.choices(string.digits, k=4))
        if code not in existing_codes:
            return code


def find_account_by_referral_code(accounts: dict, code: str):
    """Returns (email, account_dict) for the account owning `code`, or
    (None, None) if no account has it. Case-insensitive."""
    code = code.strip().upper()
    if not code:
        return None, None
    for email, account in accounts.items():
        if account.get("referral_code", "").upper() == code:
            return email, account
    return None, None