"""
data/accounts.py

Historical note only -- this file used to hold the prototype's in-memory
"user database" (ACCOUNTS, an in-memory dict that reset on every app
restart) and DEFAULT_OTP (a hardcoded '000000' code always accepted by
the old OTP step). Both are gone now:

  - ACCOUNTS was fully replaced by the `users` table in data/db.py
    (PROGRESS.md Phases 2-5 migrated login, signup, and profile editing
    off it one at a time) and deleted here in Phase 6 once nothing read
    it anymore.
  - DEFAULT_OTP was replaced by real, per-signup generated codes
    (single-use, expiring, rate-limited) at the same time OTPDialog
    (ui/dialogs/dialogs.py) stopped hardcoding an always-accepted value.
    That logic briefly lived client-side in core/otp.py, then moved to
    server/app/otp.py in Phase 8 -- see that module's docstring.

Left in place (rather than deleting the file outright) purely because
several sibling modules in this package still point back to this file's
docstring in their own "see data/accounts.py" comments explaining the
Phase 2a flat-data.py -> data/ package split -- deleting the file would
leave those references dangling for no benefit.
"""
