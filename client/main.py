"""
main.py

Lead-gen style dashboard prototype, built with PySide6.
Sidebar + Search Queries panel + results table + business detail panel,
dark theme with red accents.

This is just the entry point: builds the QApplication, applies the activerefe
theme via config.qss() (dark or light, from core.theme_state.THEME_STATE),
and loops between the auth flow (ui.dialogs.dialogs.run_auth_flow) and the
Dashboard window. Only one thing sends it back around the loop instead of
quitting: logging out (back to Login). Toggling the theme in Settings no
longer round-trips through here at all -- Dashboard.handle_theme_toggle()
applies the new palette and rebuilds its own sidebar/page stack in place,
same window, same session, no close/reopen.

Also loads a local .env file (core/env.py, if one exists next to this
file) before anything else runs, so core/api_client.py's
SINULEAD_API_URL can come from a file on disk instead of a shell
`export` -- see .env for the format. Real shell-exported env vars still
take priority if both are present. (SMTP settings used to be loaded
this way too, back when core/otp.py sent signup-verification emails
directly from the client -- that module's gone now; SMTP lives only in
server/.env, see server/app/otp.py.)

Install with: pip install -r requirements.txt
Run with:     python main.py
"""     

import sys

from core.env import load_env_file
load_env_file()  # before any other import that might read SINULEAD_API_URL etc.

from PySide6.QtWidgets import QApplication

from core import config
from core.theme_state import THEME_STATE
from core.session import clear_session, restore_session
from core.api_client import ApiError, get_me
from ui.dialogs.dialogs import run_auth_flow
from ui.pages.dashboard import Dashboard


def _try_resume_session() -> dict | None:
    """Loads a saved token (if any) and confirms it's still good against
    the server. Returns a `session` dict ready for the Dashboard, or None
    if there's nothing saved / it's expired / the server rejected it --
    in which case any stale saved copy is wiped so we don't retry it on
    the next launch too.
    """
    if not restore_session():
        return None
    try:
        user = get_me()  # GET /me -- 401s if the token is expired/revoked
    except ApiError:
        clear_session()
        return None
    return {"email": user["email"], "name": user["full_name"], "role": user["role"]}


def main():
    app = QApplication(sys.argv)
    # Fusion style respects QSS backgrounds fully; native styles (esp. Windows
    # Vista style) can ignore background-color on QPushButton and only apply
    # the border, which is why filled buttons/checkboxes can look hollow.
    app.setStyle("Fusion")
    config.set_mode(THEME_STATE["mode"])
    app.setStyleSheet(config.qss())

    # Was the app just closed (not logged out) last time? If so and the
    # saved token's still valid, skip straight to the Dashboard instead
    # of showing the login screen.
    session = _try_resume_session()

    while True:
        if session is None:
            auth_result = run_auth_flow()
            if auth_result is None:
                sys.exit(0)
            session = {
                "email": auth_result["email"],
                "name": auth_result["name"],
                "role": auth_result["role"],
            }

        window = Dashboard(
            user_email=session["email"],
            user_name=session["name"],
            user_role=session["role"],
        )
        window.logged_out = False
        window.show()
        app.exec()

        if not getattr(window, "logged_out", False):
            sys.exit(0)

        # Logged out -- drop the local session dict *and* the in-memory
        # token (core/session.py), and loop back to the login screen.
        clear_session()
        session = None


if __name__ == "__main__":
    main()