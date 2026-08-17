"""
config.py

Theme palettes (dark + light), the app-wide QSS builder, and small
constants used across the app (logo path, session-user placeholder info,
credit defaults, table column layout).

This file should never import from any other file in this project --
everything else imports FROM here.

--- Light Mode Migration -- Step 0 (see README.md) -------------------------
Colors used to live as flat COLOR_* strings + one hardcoded DARK_QSS block.
They now live in two palette dicts (THEME_DARK / THEME_LIGHT) with shared
semantic keys, exposed through one mutable dict: PALETTE.

Everything else in this codebase should migrate to:
    from core.config import PALETTE
    ...PALETTE["text_muted"]...

instead of importing a frozen COLOR_* constant. The old COLOR_* names are
kept below as backward-compatible shims so files that haven't been migrated
yet keep working -- but they are plain strings captured once, so they will
NOT update if the theme is switched at runtime. Do not write new code
against them. Once every file in the README checklist is migrated, delete
the "legacy shims" section entirely.
-----------------------------------------------------------------------------
"""

import os

# Needed inside build_qss() below (spinbox/combobox arrow + checkbox check
# icons), so this has to exist before build_qss is defined -- ASSETS_DIR
# further down (used by avatar_path() etc.) just points at this same value
# rather than recomputing it a second time.
_ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")


def _asset_url(filename: str) -> str:
    """Absolute file:// path for a QSS `url(...)` reference, forward-slashed
    so it works in Qt's stylesheet parser on every OS (backslashes in a
    Windows path would otherwise be read as escape sequences)."""
    return os.path.join(_ASSETS_DIR, filename).replace("\\", "/")

# ---------------------------------------------------------------------------
# Theme palettes
# ---------------------------------------------------------------------------
# Both dicts share the exact same keys on purpose -- nothing that reads from
# PALETTE needs to know or care which mode is currently active.

THEME_DARK = {
    # backgrounds, darkest to lightest
    "bg_app": "#0d1117",              # QMainWindow/QWidget base, table bg, scrollbar track
    "bg_surface": "#111722",          # sidebar, inputs, combo boxes, cards, note editor
    "bg_dialog": "#161b22",           # QDialog bg, table gridlines
    "bg_hover": "#1c2128",            # hover/selected state on nav items, rows, buttons
    "bg_header_hover": "#10151d",     # table header hover-only shade

    # borders / dividers
    "border": "#21262d",
    "border_strong": "#30363d",
    "divider": "#21262d",

    # text
    "text_primary": "#ffffff",
    "text_secondary": "#d1d5db",
    "text_muted": "#9ca3af",
    "text_dim": "#6b7280",

    # accent (red) -- used for the active/brand color throughout the QSS
    "accent": "#ef4444",
    "accent_hover": "#dc2626",
    "accent_soft": "rgba(239,68,68,0.12)",
    "accent_text": "#f87171",
    "accent_text_hover": "#fca5a5",

    # status/semantic colors, used for icon tinting + status pills elsewhere.
    # NOTE: red_soft intentionally uses red_solid's rgb (matches accent_soft
    # and every existing red status pill/badge in the app), while
    # green/blue/yellow/purple _soft use their own (non-solid) rgb -- that's
    # not a typo, it's what main.py's original DARK_QSS + every page's
    # status-color dict actually did. Verified against every rgba(...) call
    # site in the codebase before locking these in.
    "red": "#f87171", "red_solid": "#ef4444", "red_soft": "rgba(239,68,68,0.12)",
    "green": "#4ade80", "green_solid": "#22c55e", "green_soft": "rgba(74,222,128,0.12)",
    "blue": "#60a5fa", "blue_solid": "#3b82f6", "blue_soft": "rgba(96,165,250,0.12)",
    "yellow": "#facc15", "yellow_solid": "#eab308", "yellow_soft": "rgba(250,204,21,0.12)",
    "purple": "#a855f7", "purple_solid": "#9333ea", "purple_soft": "rgba(168,85,247,0.12)",

    "scrollbar_bg": "#0d1117",
    "scrollbar_handle": "#21262d",
    "scrollbar_handle_hover": "#30363d",
}

THEME_LIGHT = {
    "bg_app": "#f6f7f9",
    "bg_surface": "#ffffff",
    "bg_dialog": "#ffffff",
    "bg_hover": "#eef0f3",
    "bg_header_hover": "#eceef1",

    "border": "#e2e5ea",
    "border_strong": "#c9ced6",
    "divider": "#e2e5ea",

    "text_primary": "#0f1115",
    "text_secondary": "#333a45",
    "text_muted": "#6b7280",
    "text_dim": "#5b6472",

    "accent": "#ef4444",
    "accent_hover": "#dc2626",
    "accent_soft": "rgba(239,68,68,0.10)",
    "accent_text": "#dc2626",
    "accent_text_hover": "#b91c1c",

    "red": "#dc2626", "red_solid": "#b91c1c", "red_soft": "rgba(185,28,28,0.10)",
    "green": "#16a34a", "green_solid": "#15803d", "green_soft": "rgba(22,163,74,0.10)",
    "blue": "#2563eb", "blue_solid": "#1d4ed8", "blue_soft": "rgba(37,99,235,0.10)",
    "yellow": "#ca8a04", "yellow_solid": "#a16207", "yellow_soft": "rgba(202,138,4,0.10)",
    "purple": "#9333ea", "purple_solid": "#7e22ce", "purple_soft": "rgba(147,51,234,0.10)",

    "scrollbar_bg": "#f6f7f9",
    "scrollbar_handle": "#d7dbe1",
    "scrollbar_handle_hover": "#c1c6cf",
}

# The single source of truth every other file should read from. Switch
# themes by mutating this dict IN PLACE (see set_mode()) -- never rebind
# the name (`PALETTE = THEME_LIGHT`), or modules that already did
# `from config import PALETTE` will keep pointing at the old dict object.
PALETTE = dict(THEME_DARK)
_ACTIVE_MODE = "dark"


def set_mode(mode: str) -> None:
    """Switch the active theme. mode is "dark" or "light"."""
    global _ACTIVE_MODE
    if mode not in ("dark", "light"):
        raise ValueError(f"Unknown theme mode: {mode!r}")
    _ACTIVE_MODE = mode
    PALETTE.clear()
    PALETTE.update(THEME_LIGHT if mode == "light" else THEME_DARK)
    _refresh_legacy_aliases()


def get_mode() -> str:
    return _ACTIVE_MODE


def build_qss(palette: dict) -> str:
    """Build the app-wide QSS stylesheet string from a palette dict."""
    return f"""
QMainWindow, QWidget {{ background-color: {palette['bg_app']}; color: {palette['text_secondary']}; font-family: 'Segoe UI', sans-serif; font-size: 12px; }}
QLabel {{ border: none; }}
QSplitter::handle {{ background-color: {palette['bg_app']}; }}
QSplitter::handle:hover {{ background-color: {palette['accent']}; }}
QSplitter::handle:pressed {{ background-color: {palette['accent_hover']}; }}
#Sidebar {{ background-color: {palette['bg_surface']}; border-right: 1px solid {palette['border']}; }}
#SidebarTitle {{ color: {palette['text_primary']}; font-size: 16px; font-weight: bold; padding: 4px 0 12px 0; }}
QPushButton#NavActive {{ background-color: {palette['accent_soft']}; color: {palette['accent_text']}; border: none; border-left: 2px solid {palette['accent']}; border-radius: 4px; text-align: left; padding: 8px 10px 8px 8px; font-weight: 500; }}
QPushButton#NavItem {{ background-color: transparent; color: {palette['text_muted']}; border: none; border-left: 2px solid transparent; border-radius: 6px; text-align: left; padding: 8px 10px 8px 8px; }}
QPushButton#NavItem:hover {{ background-color: {palette['bg_hover']}; }}
#NavDivider {{ background-color: {palette['border']}; max-height: 1px; min-height: 1px; margin: 8px 4px; }}
#CreditsBox {{ background-color: {palette['bg_app']}; border: 1px solid {palette['border']}; border-radius: 8px; padding: 10px; }}
#UserBox {{ background-color: transparent; border-radius: 8px; }}
#UserBox:hover {{ background-color: {palette['bg_hover']}; }}
#HeaderTitle {{ color: {palette['text_primary']}; font-size: 18px; font-weight: bold; }}
#HeaderSub {{ color: {palette['text_dim']}; font-size: 11px; }}
QLineEdit {{ background-color: {palette['bg_surface']}; border: 1px solid {palette['border']}; border-radius: 6px; padding: 9px 12px; color: {palette['text_secondary']}; font-size: 12px; selection-background-color: {palette['accent_soft']}; selection-color: {palette['accent_text']}; }}
QLineEdit:focus {{ border: 1px solid {palette['accent']}; background-color: {palette['bg_app']}; }}
QLineEdit:read-only {{ color: {palette['text_muted']}; }}
QLineEdit:disabled {{ color: {palette['text_dim']}; background-color: {palette['bg_dialog']}; }}
QSpinBox, QDoubleSpinBox, QComboBox {{ background-color: {palette['bg_surface']}; border: 1px solid {palette['border']}; border-radius: 6px; padding: 0px 10px; min-height: 32px; max-height: 32px; color: {palette['text_secondary']}; font-size: 12px; selection-background-color: {palette['accent_soft']}; selection-color: {palette['accent_text']}; }}
QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border: 1px solid {palette['accent']}; background-color: {palette['bg_app']}; }}
QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {{ border: 1px solid {palette['text_dim']}; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-origin: border; subcontrol-position: top right; width: 18px; border: none; border-left: 1px solid {palette['border']}; border-top-right-radius: 6px; background-color: transparent; }}
QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-origin: border; subcontrol-position: bottom right; width: 18px; border: none; border-left: 1px solid {palette['border']}; border-bottom-right-radius: 6px; background-color: transparent; }}
QSpinBox::up-button:hover, QSpinBox::down-button:hover, QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {{ background-color: {palette['bg_hover']}; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url({_asset_url('arrow_up.png')}); width: 10px; height: 8px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url({_asset_url('arrow_down.png')}); width: 10px; height: 8px; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{ image: url({_asset_url('arrow_combo.png')}); width: 10px; height: 8px; margin-right: 8px; }}
QComboBox QAbstractItemView {{ background-color: {palette['bg_surface']}; border: 1px solid {palette['border']}; selection-background-color: {palette['bg_hover']}; color: {palette['text_secondary']}; outline: none; padding: 0px; }}
QComboBox QAbstractItemView::item {{ padding: 0px; min-height: 22px; }}
QCheckBox {{ color: {palette['text_secondary']}; font-size: 12px; spacing: 8px; background: transparent; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {palette['border_strong']}; background-color: {palette['bg_surface']}; }}
QCheckBox::indicator:hover {{ border: 1px solid {palette['accent']}; }}
QCheckBox::indicator:checked {{ background-color: {palette['accent']}; border: 1px solid {palette['accent']}; image: url({_asset_url('check.png')}); }}
QDialog {{ background-color: {palette['bg_dialog']}; border-radius: 10px; }}
QDialog QLabel {{ background: transparent; }}
QPushButton#OutlineBtn {{ background-color: transparent; border: 1px solid {palette['border_strong']}; border-radius: 5px; padding: 6px 12px; color: {palette['text_secondary']}; }}
QPushButton#OutlineBtn:hover {{ background-color: {palette['bg_hover']}; }}
QPushButton#RedBtn {{ background-color: {palette['accent']}; border: none; border-radius: 5px; padding: 6px 14px; color: white; font-weight: 500; }}
QPushButton#RedBtn:hover {{ background-color: {palette['accent_hover']}; }}
QComboBox#RowModeCombo {{ background-color: {palette['bg_surface']}; border: 1px solid {palette['border']}; border-radius: 5px; padding: 0px 10px; min-height: 30px; max-height: 30px; color: {palette['text_secondary']}; font-size: 11px; }}
QComboBox#RowModeCombo:hover {{ border: 1px solid {palette['text_dim']}; }}
QPushButton#RowModeCombo {{ background-color: {palette['bg_surface']}; border: 1px solid {palette['border']}; border-radius: 5px; padding: 0px 10px; min-height: 30px; max-height: 30px; color: {palette['text_secondary']}; font-size: 11px; text-align: left; }}
QPushButton#RowModeCombo:hover {{ border: 1px solid {palette['text_dim']}; }}
QComboBox#RowModeCombo::drop-down {{ border: none; width: 20px; }}
QComboBox#RowModeCombo QAbstractItemView {{ background-color: {palette['bg_surface']}; border: 1px solid {palette['border']}; selection-background-color: {palette['bg_hover']}; color: {palette['text_secondary']}; outline: none; padding: 0px; }}
QComboBox#RowModeCombo QAbstractItemView::item {{ padding: 0px; min-height: 22px; }}
QTableWidget {{ background-color: {palette['bg_app']}; border: none; gridline-color: {palette['bg_dialog']}; selection-background-color: {palette['bg_hover']}; }}
QHeaderView::section {{ background-color: {palette['bg_app']}; color: {palette['text_dim']}; border: none; border-bottom: 1px solid {palette['border']}; padding: 6px; font-weight: 500; }}
QHeaderView::section:hover {{ color: {palette['text_secondary']}; background-color: {palette['bg_header_hover']}; }}
QTableWidget::item {{ border-bottom: 1px solid {palette['bg_dialog']}; padding: 4px; color: {palette['text_secondary']}; }}
QTableWidget::item:selected {{ background-color: {palette['bg_hover']}; color: {palette['text_secondary']}; }}
#DetailPanel {{ background-color: {palette['bg_app']}; border-left: 1px solid {palette['border']}; }}
#DetailHeader {{ color: {palette['text_primary']}; font-size: 13px; font-weight: 600; }}
QPushButton#DetailCloseBtn {{ background-color: transparent; border: none; border-radius: 4px; padding: 2px; }}
QPushButton#DetailCloseBtn:hover {{ background-color: {palette['bg_hover']}; }}
QProgressBar {{ background-color: {palette['border']}; border-radius: 2px; min-height: 4px; max-height: 4px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background-color: {palette['accent']}; border-radius: 2px; }}
#DashCard {{ background-color: {palette['bg_surface']}; border: 1px solid {palette['border']}; border-radius: 10px; }}
QPushButton#BillingTabActive {{ background-color: transparent; color: {palette['accent_text']}; border: none; border-bottom: 2px solid {palette['accent']}; border-radius: 0; padding: 8px 14px; font-weight: 600; }}
QPushButton#BillingTabItem {{ background-color: transparent; color: {palette['text_muted']}; border: none; border-bottom: 2px solid transparent; border-radius: 0; padding: 8px 14px; }}
QPushButton#BillingTabItem:hover {{ color: {palette['text_secondary']}; }}
QPushButton#LinkBtn {{ background-color: transparent; border: none; color: {palette['accent_text']}; font-size: 11px; font-weight: 500; }}
QPushButton#LinkBtn:hover {{ color: {palette['accent_text_hover']}; text-decoration: underline; }}
QTextEdit#NoteEdit {{ background-color: {palette['bg_surface']}; border: 1px solid {palette['border']}; border-radius: 6px; padding: 8px 10px; color: {palette['text_secondary']}; font-size: 11px; }}
QTextEdit#NoteEdit:focus {{ border: 1px solid {palette['accent']}; }}
#DashScroll {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: {palette['scrollbar_bg']}; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {palette['scrollbar_handle']}; border-radius: 5px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {palette['scrollbar_handle_hover']}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
"""


def rgba_from_hex(hex_color: str, alpha: float) -> str:
    """'#4ade80' + 0.15 -> 'rgba(74,222,128,0.15)'. Several pages build a
    custom-alpha tint of a status color for badges/banners/hover states
    that the fixed 0.12-alpha `*_soft` palette keys don't cover -- use this
    instead of hand-rolling int(hex[...],16) again (earn_credits.py and
    overview.py each independently wrote this exact function; migrate them
    to call this one instead of keeping their own copy)."""
    hex_color = hex_color.lstrip("#")
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def qss() -> str:
    """QSS for the currently active theme -- what main.py hands to
    app.setStyleSheet(...)."""
    return build_qss(PALETTE)


# ---------------------------------------------------------------------------
# Legacy shims -- DEPRECATED, remove once every file in the README's
# migration checklist has switched to `from config import PALETTE`.
# These are plain strings snapshotted from PALETTE; they exist only so
# files that haven't been migrated yet keep importing successfully. They
# will NOT change if the theme is switched at runtime.
# ---------------------------------------------------------------------------

def _refresh_legacy_aliases() -> None:
    global COLOR_MUTED, COLOR_MUTED_DIM, COLOR_WHITE, COLOR_RED, COLOR_RED_SOLID
    global COLOR_GREEN, COLOR_GREEN_SOLID, COLOR_BLUE, COLOR_BLUE_SOLID
    global COLOR_YELLOW, COLOR_YELLOW_SOLID, COLOR_PURPLE, COLOR_PURPLE_SOLID
    COLOR_MUTED = PALETTE["text_muted"]
    COLOR_MUTED_DIM = PALETTE["text_dim"]
    COLOR_WHITE = PALETTE["text_primary"]
    COLOR_RED = PALETTE["red"]
    COLOR_RED_SOLID = PALETTE["red_solid"]
    COLOR_GREEN = PALETTE["green"]
    COLOR_GREEN_SOLID = PALETTE["green_solid"]
    COLOR_BLUE = PALETTE["blue"]
    COLOR_BLUE_SOLID = PALETTE["blue_solid"]
    COLOR_YELLOW = PALETTE["yellow"]
    COLOR_YELLOW_SOLID = PALETTE["yellow_solid"]
    COLOR_PURPLE = PALETTE["purple"]
    COLOR_PURPLE_SOLID = PALETTE["purple_solid"]


_refresh_legacy_aliases()

# Legacy name -- kept so main.py keeps working until its Step 2 migration.
# Prefer config.qss() in new code; it tracks the active theme, this doesn't.
DARK_QSS = build_qss(THEME_DARK)

# Path to your own logo file. Drop a PNG/SVG here (ideally square, transparent bg)
# and it will be used automatically. Falls back to the pin icon if not found.
# NOTE (Phase 3a): this file moved from the project root into core/, one
# directory deeper than before, so this needs an extra os.path.dirname()
# to still land on the top-level assets/ dir instead of a nonexistent
# core/assets/ -- same trap as data/billing.py's _ASSETS_DIR in Phase 2d.
LOGO_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "logo.png"
)

# ---------------------------------------------------------------------------
# Avatars (Signup / View Profile / Edit Profile)
# ---------------------------------------------------------------------------
# The `users.avatar` column (data/db.py) just stores one of these filenames.
# Add more avatarN.png files to assets/ and list them here to make them
# selectable everywhere at once -- EditProfileDialog's picker builds its
# buttons straight off this list.
ASSETS_DIR = _ASSETS_DIR
AVATAR_CHOICES = ["avatar1.png", "avatar2.png", "avatar3.png", "avatar4.png"]
DEFAULT_AVATAR = AVATAR_CHOICES[0]


def avatar_path(filename: str | None) -> str:
    """Stored `avatar` value -> absolute file path under assets/. Falls
    back to DEFAULT_AVATAR for None/blank/unrecognized values, so a row
    with no avatar yet (or one referencing a file that's since been
    removed) always resolves to something renderable instead of a blank
    pixmap."""
    name = filename if filename in AVATAR_CHOICES else DEFAULT_AVATAR
    return os.path.join(ASSETS_DIR, name)

# Placeholder "logged in as" info -- wire this up to your real auth/session
# data once you have a backend. Swap these two lines for the real values.
CURRENT_USER_NAME = "Judel Federigan"
CURRENT_USER_EMAIL = "judel.federigan@example.com"

ALL_ROWS_COUNT = 500
INITIAL_CREDITS = 0  # starting/session-max credit balance shown in the sidebar

# Credits awarded to BOTH sides of a referral (the new signup and the
# friend whose code they entered) the moment the new account is created.
REFERRAL_BONUS_CREDITS = 500

# Master switch for the referral program. Enabled — see
# PROGRESS_REWARDS.md Phase 5.
REFERRAL_PROGRAM_ENABLED = True

# Column layout for the results table.
# key is None for columns that aren't sortable (row #, checkbox).
COLUMNS = [
    {"label": "", "key": None},
    {"label": "#", "key": None},
    {"label": "Business Name", "key": "name"},
    {"label": "Rating", "key": "rating"},
    {"label": "Reviews", "key": "reviews"},
    {"label": "Category", "key": "category"},
    {"label": "Phone", "key": "phone_num"},
    {"label": "Website", "key": "site"},
    {"label": "Email", "key": "email_addr"},
    {"label": "Status", "key": "status"},
    {"label": "Address", "key": "address"},
    # Admin-only: raw Google Maps URL for the scraped listing. Never
    # shown to regular users -- see the `admin_only` check in
    # search_leads.py's open_columns_menu() and exports.py's
    # visible_cols filter, both of which strip this column out unless
    # user_role == "admin".
    {"label": "Maps Link", "key": "maps_url", "admin_only": True},
]
(COL_CHECK, COL_ROWNUM, COL_NAME, COL_RATING, COL_REVIEWS, COL_CATEGORY,
 COL_PHONE, COL_WEBSITE, COL_EMAIL, COL_STATUS, COL_ADDRESS, COL_MAPS_LINK) = range(12)

# Columns hidden out of the box (still exportable/toggleable via the
# "Columns" menu -- see open_columns_menu in search_leads.py). Address and
# Maps Link add a lot of horizontal width for info most searches don't
# need visible by default, so they start unchecked/hidden.
DEFAULT_HIDDEN_COLUMNS = {COL_ADDRESS, COL_MAPS_LINK}