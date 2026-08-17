"""
theme_state.py

Single in-memory source of truth for which theme (dark/light) is active,
same pattern as maintenance_state.py. settings_page.py writes to this when
the user flips the toggle. main.py reads it once at startup; after that,
Dashboard.handle_theme_toggle() reads it directly and hands it to
config.set_mode() itself, rebuilding its own UI in place (no close/reopen,
no round trip through main.py).

Prototype-only -- resets to "dark" when the app restarts, same as the rest
of data.py. Swap for a real persisted user preference (a settings table,
QSettings, etc.) later if you want it to survive a restart.
"""

THEME_STATE = {
    "mode": "dark",  # "dark" | "light"
}

