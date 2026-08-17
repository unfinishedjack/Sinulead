"""
users_page.py

UsersPage: the "Users" nav tab -- admin-only. KPI stat cards, pill-tab +
search/role/status filtered + paginated table of every account (mirrors
the same pattern as exports.ExportsPage), plus a right-hand User Details
panel (mirrors detail_panel.DetailPanel) with account info and quick
actions: Add Credits, Deduct Credits, Reset Password, Suspend/Reactivate
User, and an admin-only notes box.

Backed by the real `users` table (server/app/db.py) -- self.users is
loaded via api_client.admin_list_users() and the Add Credits / Deduct
Credits / Suspend-Reactivate quick actions persist back via
admin_adjust_credits()/admin_update_user(), instead of only mutating an
in-memory dummy list. Reset Password and the notes box still don't touch
a backend (no email-sending / notes column exists yet), so those keep
showing the themed "not wired up yet" dialog (see dialogs.InfoDialog).

The logged-in admin is excluded from their own table -- UsersPage is
constructed with current_admin_email (see dashboard.py) and filters that
row out of admin_list_users()'s result, so an admin never sees themselves
in the list, while still seeing every other admin.

Depends on: config, core.api_client (admin_list_users, admin_update_user,
admin_adjust_credits -- see PROGRESS.md Phase 5; used to hit data.db
directly), dialogs (InfoDialog, ConfirmDialog, ModernDialog,
labeled_field), overview (StatCard).
"""

from datetime import datetime, timedelta

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QPushButton, QFrame, QScrollArea, QSizePolicy, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QComboBox,
    QSplitter, QTextEdit, QDialog,
)
from PySide6.QtCore import Qt, QSize, QRectF, Signal
from PySide6.QtGui import QPainter, QColor, QBrush, QPen, QPixmap
import qtawesome as qta

from core import config
from core.config import PALETTE
from core.api_client import admin_list_users, admin_update_user, admin_adjust_credits, ApiError
from ui.dialogs.dialogs import InfoDialog, ConfirmDialog, ModernDialog, labeled_field
from ui.components.widgets import status_badge, circular_avatar_pixmap, SearchLineEdit
from ui.pages.overview import StatCard


def _format_joined(raw: str | None) -> str:
    """'2026-07-25 03:45:12' (sqlite datetime('now') format) -> 'Jul 25, 2026'."""
    if not raw:
        return "\u2014"
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").strftime("%b %d, %Y")
    except ValueError:
        return raw


def _format_last_active(raw: str | None) -> str:
    """Same source format -> 'Jul 31, 2026 03:45 PM'; blank until the user
    has actually logged in at least once."""
    if not raw:
        return "\u2014"
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").strftime("%b %d, %Y %I:%M %p")
    except ValueError:
        return raw


# Maps the DB's lowercase `role` column ("user" / "admin" / "superadmin",
# see data/db.py's ck_users_role CHECK constraint) to the Title-Case label
# the table/detail-panel/pill code expects. Anything not listed here
# (including plain "user") falls back to "User".
_ROLE_LABELS = {"admin": "Admin", "superadmin": "Super Admin"}


def _db_row_to_ui(row: dict) -> dict:
    """users_page's table/detail-panel code was built against the old
    dummy USERS dict shape (Title-Case role/status, `name`, `joined`,
    `last_active`) -- this adapts a real data.db row to that same shape
    so none of the rendering code below has to change. `id` is carried
    through so quick actions know which row to persist back to."""
    return {
        "id": row["id"],
        "name": row["full_name"],
        "email": row["email"],
        "phone": row.get("phone") or "",
        "role": _ROLE_LABELS.get(row["role"], "User"),
        "avatar": row.get("avatar"),
        "credits": row.get("credits", 0),
        "spent": row.get("spent", 0),
        "joined": _format_joined(row.get("created_at")),
        "joined_at_raw": row.get("created_at"),
        "last_active": _format_last_active(row.get("last_active_at")),
        "last_active_at_raw": row.get("last_active_at"),
        "status": row["status"].capitalize(),
        "notes": "",
    }


def _initials_pixmap(name: str, size: int = 32) -> QPixmap:
    """Colored-circle-with-initials avatar, same painting approach as
    billing.py's _letter_pixmap, just circular instead of rounded-square.

    Not used by this file's own User column anymore (that renders the
    account's real `avatar` via circular_avatar_pixmap, see _user_cell
    below) -- kept here because transactions.py still imports this for
    its own transaction rows, which aren't tied to a real user avatar."""
    parts = name.split()
    initials = (parts[0][0] + parts[-1][0]).upper() if len(parts) > 1 else name[:2].upper()
    palette = [PALETTE['red_solid'], PALETTE['blue'], PALETTE['green'], PALETTE['yellow'], PALETTE['purple']]
    color = palette[sum(ord(c) for c in name) % len(palette)]

    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(QColor(color)))
    painter.drawEllipse(0, 0, size, size)
    font = painter.font()
    font.setBold(True)
    font.setPointSize(max(8, size // 3))
    painter.setFont(font)
    painter.setPen(QPen(QColor("white")))
    painter.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, initials)
    painter.end()
    return pix


def _user_cell(user: dict) -> QWidget:
    """Avatar + name (bold) / email (muted) stacked, same look as the
    screenshot's User column. Avatar is the account's real
    `users.avatar` choice (see core.config.AVATAR_CHOICES), rendered via
    the same circular_avatar_pixmap() the sidebar/profile dialogs use --
    not a hand-drawn initials circle."""
    wrap = QWidget()
    wrap.setStyleSheet("background: transparent;")
    row = QHBoxLayout(wrap)
    row.setContentsMargins(6, 4, 6, 4)
    row.setSpacing(10)

    avatar = QLabel()
    avatar.setStyleSheet("background: transparent;")
    avatar.setPixmap(circular_avatar_pixmap(user.get("avatar"), 32))
    row.addWidget(avatar)

    col = QVBoxLayout()
    col.setSpacing(1)
    name_lbl = QLabel(user["name"])
    name_lbl.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 12px; font-weight: 600; background: transparent;")
    col.addWidget(name_lbl)
    email_lbl = QLabel(user["email"])
    email_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 11px; background: transparent;")
    col.addWidget(email_lbl)
    row.addLayout(col, stretch=1)
    return wrap


_ROLE_PILL_COLORS = {
    "Admin": (PALETTE['yellow'], PALETTE['yellow_soft']),
    "Super Admin": (PALETTE['purple'], PALETTE['purple_soft']),
}


def _role_pill(role: str) -> QWidget:
    accent, bg = _ROLE_PILL_COLORS.get(role, (PALETTE['blue'], PALETTE['blue_soft']))
    pill = QFrame()
    pill.setStyleSheet(f"background-color: {bg}; border-radius: 4px;")
    pill.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    row = QHBoxLayout(pill)
    row.setContentsMargins(8, 3, 8, 3)
    lbl = QLabel(role)
    lbl.setStyleSheet(f"color: {accent}; font-size: 10px; font-weight: 600; background: transparent;")
    row.addWidget(lbl)

    outer = QWidget()
    outer.setStyleSheet("background: transparent;")
    outer_row = QHBoxLayout(outer)
    outer_row.setContentsMargins(0, 0, 0, 0)
    outer_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
    outer_row.addWidget(pill)
    return outer


def _status_colors() -> dict:
    """Looked up fresh on every call (not a frozen module-level dict) so it
    tracks theme switches -- same tinting logic as elsewhere, just sourced
    from the live PALETTE. The soft-tint values line up exactly with the
    existing green_soft/yellow_soft/red_soft palette keys."""
    return {
        "Active": (PALETTE["green_solid"], PALETTE["green_soft"]),
        "Inactive": (PALETTE["yellow_solid"], PALETTE["yellow_soft"]),
        "Suspended": (PALETTE["red_solid"], PALETTE["red_soft"]),
    }


def _status_pill(status: str) -> QWidget:
    """Thin wrapper over the shared widgets.status_badge (see PROGRESS.md,
    Phase 1a) -- kept as a local name so call sites below didn't need to
    change."""
    return status_badge(status, _status_colors())


class _AmountDialog(ModernDialog):
    """Small themed prompt for a credits amount -- used by Add Credits /
    Deduct Credits. Not a generic reusable dialog (lives here, not in
    dialogs.py) since it's specific to the Users tab's quick actions."""
    def __init__(self, title: str, confirm_text: str, icon_name: str, parent=None):
        super().__init__(parent)
        self.setFixedWidth(300)
        layout = self.card_layout()
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)
        self.add_header(layout, title)

        self.amount_edit = QLineEdit()
        self.amount_edit.setPlaceholderText("e.g. 500")
        self.amount_edit.returnPressed.connect(self._on_confirm)
        layout.addLayout(labeled_field("Amount", self.amount_edit))

        self.error_lbl = QLabel("")
        self.error_lbl.setStyleSheet(f"color: {PALETTE['red']}; font-size: 10px;")
        self.error_lbl.hide()
        layout.addWidget(self.error_lbl)

        # Cache the confirmed amount as a plain int at confirm-time (see
        # _on_confirm) rather than re-reading amount_edit.text() from
        # amount() after exec() returns. ModernDialog sets
        # WA_DeleteOnClose, so the instant accept()/exec() unwinds, Qt
        # tears down amount_edit's underlying C++ object -- reading it
        # afterward raises "libshiboken: Internal C++ object ... already
        # deleted" instead of returning a value.
        self._confirmed_amount: int | None = None

        layout.addSpacing(4)
        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("OutlineBtn")
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        # Explicitly NOT the default/auto-default button -- otherwise
        # it can end up sharing the Enter-key "default button" role
        # with Confirm below, so pressing Enter in the amount field
        # could fire reject() as well as/instead of _on_confirm,
        # incorrectly closing the dialog on a normal Enter submit
        # (same fix as OTPDialog's Cancel/Verify pairing).
        cancel_btn.setAutoDefault(False)
        cancel_btn.setDefault(False)
        cancel_btn.clicked.connect(self.reject)
        confirm_btn = QPushButton(qta.icon(icon_name, color="#ffffff"), " " + confirm_text)
        confirm_btn.setObjectName("RedBtn")
        confirm_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        # Confirm is the one true default button -- Enter (whether via
        # amount_edit.returnPressed above or the dialog's own
        # default-button handling) should only ever trigger this.
        confirm_btn.setAutoDefault(True)
        confirm_btn.setDefault(True)
        confirm_btn.clicked.connect(self._on_confirm)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(confirm_btn)
        layout.addLayout(btn_row)

    def _on_confirm(self):
        text = self.amount_edit.text().strip().replace(",", "")
        if not text.isdigit() or int(text) <= 0:
            self.error_lbl.setText("Enter a whole number greater than 0.")
            self.error_lbl.show()
            return
        self._confirmed_amount = int(text)
        self.accept()

    def amount(self) -> int:
        return self._confirmed_amount

    @staticmethod
    def ask(parent, title: str, confirm_text: str, icon_name: str):
        """Returns the entered amount (int) or None if cancelled."""
        dlg = _AmountDialog(title, confirm_text, icon_name, parent)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            return dlg.amount()
        return None


class UserDetailPanel(QFrame):
    """
    Right-hand "User Details" panel -- shows the currently-selected user
    from the Users table, with Quick Actions (Add Credits, Deduct Credits,
    Reset Password, Suspend/Reactivate User) and an admin notes box.

    Every widget is built exactly once in __init__; show_user() only
    updates text/icons/styles on those existing widgets (same
    crash-avoidance pattern as detail_panel.DetailPanel -- see that
    file's docstring for why rebuilding the tree on every click is
    dangerous).
    """
    user_changed = Signal()  # emitted after any quick action mutates the user dict
    close_requested = Signal()  # emitted when the X is clicked, so UsersPage can hide/collapse this panel

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DetailPanel")
        self.setMinimumWidth(300)
        self.setMaximumWidth(400)
        self._current_user = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)

        header_row = QHBoxLayout()
        header = QLabel("User Details")
        header.setObjectName("DetailHeader")
        header_row.addWidget(header)
        header_row.addStretch()
        self.close_btn = QPushButton()
        self.close_btn.setObjectName("DetailCloseBtn")
        self.close_btn.setIcon(qta.icon('fa5s.times', color=PALETTE['text_muted']))
        self.close_btn.setFixedSize(20, 20)
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.clicked.connect(self.close_panel)
        header_row.addWidget(self.close_btn)
        outer.addLayout(header_row)

        self.placeholder = QLabel("Select a user to see details")
        self.placeholder.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 11px;")
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(self.placeholder)

        # Scroll area so the panel never forces the window taller than
        # the screen once Quick Actions + Notes are all visible.
        scroll = QScrollArea()
        scroll.setObjectName("DashScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.content = QFrame()
        content_layout = QVBoxLayout(self.content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(14)

        # --- Identity row: avatar + name + status pill ---
        identity_row = QHBoxLayout()
        identity_row.setSpacing(10)
        self.avatar_lbl = QLabel()
        self.avatar_lbl.setStyleSheet("background: transparent;")
        identity_row.addWidget(self.avatar_lbl)

        name_col = QVBoxLayout()
        name_col.setSpacing(3)
        name_top_row = QHBoxLayout()
        name_top_row.setSpacing(8)
        self.name_lbl = QLabel()
        self.name_lbl.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 15px; font-weight: bold; background: transparent;")
        name_top_row.addWidget(self.name_lbl)
        self.status_pill_slot = QHBoxLayout()
        name_top_row.addLayout(self.status_pill_slot)
        name_top_row.addStretch()
        name_col.addLayout(name_top_row)

        self.email_row_lbl = QLabel()
        self.email_row_lbl.setStyleSheet(f"color: {PALETTE['text_muted']}; font-size: 11px; background: transparent;")
        name_col.addWidget(self.email_row_lbl)
        self.phone_row_lbl = QLabel()
        self.phone_row_lbl.setStyleSheet(f"color: {PALETTE['text_muted']}; font-size: 11px; background: transparent;")
        name_col.addWidget(self.phone_row_lbl)
        self.joined_row_lbl = QLabel()
        self.joined_row_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 10px; background: transparent;")
        name_col.addWidget(self.joined_row_lbl)

        identity_row.addLayout(name_col, stretch=1)
        content_layout.addLayout(identity_row)

        # --- Current Credits / Total Spent mini stat cards ---
        stats_row = QHBoxLayout()
        stats_row.setSpacing(10)
        self.credits_card, self.credits_value_lbl = self._mini_stat("Current Credits", PALETTE['green'])
        self.spent_card, self.spent_value_lbl = self._mini_stat("Total Spent", PALETTE['text_primary'])
        stats_row.addWidget(self.credits_card)
        stats_row.addWidget(self.spent_card)
        content_layout.addLayout(stats_row)

        # --- Account Information ---
        info_card, info_layout = self._card("Account Information")
        grid = QGridLayout()
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(10)
        self.role_value_lbl = self._grid_field(grid, 0, 0, "Role")
        self.email_value_lbl = self._grid_field(grid, 0, 1, "Email")
        self.phone_value_lbl = self._grid_field(grid, 1, 0, "Phone")
        self.status_value_lbl = self._grid_field(grid, 1, 1, "Status")
        self.lastactive_value_lbl = self._grid_field(grid, 2, 0, "Last Active")
        self.verified_value_lbl = self._grid_field(grid, 2, 1, "Email Verified")
        info_layout.addLayout(grid)
        content_layout.addWidget(info_card)

        # --- Quick Actions ---
        actions_card, actions_layout = self._card("Quick Actions")
        actions_grid = QGridLayout()
        actions_grid.setSpacing(8)

        self.add_credits_btn = QPushButton(qta.icon('fa5s.plus', color=PALETTE['green']), " Add Credits")
        self.add_credits_btn.setObjectName("OutlineBtn")
        self.add_credits_btn.setStyleSheet(
            f"QPushButton#OutlineBtn {{ border-color: {config.rgba_from_hex(PALETTE['green'], 0.4)}; color: {PALETTE['green']}; }}"
        )
        self.add_credits_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_credits_btn.clicked.connect(self._add_credits)
        actions_grid.addWidget(self.add_credits_btn, 0, 0)

        self.deduct_credits_btn = QPushButton(qta.icon('fa5s.minus', color=PALETTE['yellow']), " Deduct Credits")
        self.deduct_credits_btn.setObjectName("OutlineBtn")
        self.deduct_credits_btn.setStyleSheet(
            f"QPushButton#OutlineBtn {{ border-color: {config.rgba_from_hex(PALETTE['yellow'], 0.4)}; color: {PALETTE['yellow']}; }}"
        )
        self.deduct_credits_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.deduct_credits_btn.clicked.connect(self._deduct_credits)
        actions_grid.addWidget(self.deduct_credits_btn, 0, 1)

        self.reset_pw_btn = QPushButton(qta.icon('fa5s.lock', color=PALETTE['blue']), " Reset Password")
        self.reset_pw_btn.setObjectName("OutlineBtn")
        self.reset_pw_btn.setStyleSheet(
            f"QPushButton#OutlineBtn {{ border-color: {config.rgba_from_hex(PALETTE['blue'], 0.4)}; color: {PALETTE['blue']}; }}"
        )
        self.reset_pw_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reset_pw_btn.clicked.connect(self._reset_password)
        actions_grid.addWidget(self.reset_pw_btn, 1, 0)

        self.suspend_btn = QPushButton(qta.icon('fa5s.user-slash', color="#ffffff"), " Suspend User")
        self.suspend_btn.setObjectName("RedBtn")
        self.suspend_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.suspend_btn.clicked.connect(self._toggle_suspend)
        actions_grid.addWidget(self.suspend_btn, 1, 1)

        actions_layout.addLayout(actions_grid)

        view_profile_btn = QPushButton(qta.icon('fa5s.external-link-alt', color=PALETTE['text_muted']), " View Full Profile")
        view_profile_btn.setObjectName("OutlineBtn")
        view_profile_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        view_profile_btn.clicked.connect(self._view_full_profile_not_wired_up)
        actions_layout.addWidget(view_profile_btn)

        content_layout.addWidget(actions_card)

        # --- Notes (Admin Only) ---
        notes_card, notes_layout = self._card("Notes (Admin Only)")
        self.notes_edit = QTextEdit()
        self.notes_edit.setObjectName("NoteEdit")
        self.notes_edit.setPlaceholderText("Add a note about this user...")
        self.notes_edit.setFixedHeight(80)
        notes_layout.addWidget(self.notes_edit)
        save_note_btn = QPushButton("Save Note")
        save_note_btn.setObjectName("RedBtn")
        save_note_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_note_btn.clicked.connect(self._save_note)
        notes_layout.addWidget(save_note_btn, alignment=Qt.AlignmentFlag.AlignRight)
        content_layout.addWidget(notes_card)

        content_layout.addStretch()
        scroll.setWidget(self.content)
        outer.addWidget(scroll)

        self.content.hide()

    # ------------------------------------------------------------------
    # Small widget builders
    # ------------------------------------------------------------------
    def _card(self, title: str):
        frame = QFrame()
        frame.setObjectName("DashCard")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 12px; font-weight: 600; background: transparent;")
        layout.addWidget(title_lbl)
        return frame, layout

    def _mini_stat(self, label: str, value_color: str):
        frame = QFrame()
        frame.setObjectName("DashCard")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(2)
        label_lbl = QLabel(label)
        label_lbl.setStyleSheet(f"color: {PALETTE['text_muted']}; font-size: 10px; background: transparent;")
        label_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label_lbl)
        value_lbl = QLabel("0")
        value_lbl.setStyleSheet(f"color: {value_color}; font-size: 18px; font-weight: bold; background: transparent;")
        value_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(value_lbl)
        return frame, value_lbl

    def _grid_field(self, grid: QGridLayout, row: int, col: int, label: str) -> QLabel:
        cell = QVBoxLayout()
        cell.setSpacing(2)
        label_lbl = QLabel(label)
        label_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 10px; background: transparent;")
        cell.addWidget(label_lbl)
        value_lbl = QLabel("")
        value_lbl.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 11px; font-weight: 500; background: transparent;")
        value_lbl.setWordWrap(True)
        cell.addWidget(value_lbl)
        grid.addLayout(cell, row, col)
        return value_lbl

    # ------------------------------------------------------------------
    # Show / close
    # ------------------------------------------------------------------
    def show_user(self, user: dict):
        self._current_user = user
        self.placeholder.hide()
        self.content.show()

        self.avatar_lbl.setPixmap(circular_avatar_pixmap(user.get("avatar"), 48))
        self.name_lbl.setText(user["name"])
        while self.status_pill_slot.count():
            item = self.status_pill_slot.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.status_pill_slot.addWidget(_status_pill(user["status"]))

        self.email_row_lbl.setText(f'\u2709  {user["email"]}')
        self.phone_row_lbl.setText(f'\U0001F4DE  {user.get("phone", "\u2014")}')
        self.joined_row_lbl.setText(f'Joined on {user.get("joined", "\u2014")}')

        self.credits_value_lbl.setText(f'{user.get("credits", 0):,}')
        self.spent_value_lbl.setText(f'{user.get("spent", 0):,}')

        self.role_value_lbl.setText(user["role"])
        self.email_value_lbl.setText(user["email"])
        self.phone_value_lbl.setText(user.get("phone", "\u2014"))
        accent, _ = _status_colors().get(user["status"], (PALETTE["text_muted"], ""))
        self.status_value_lbl.setText(user["status"])
        self.status_value_lbl.setStyleSheet(f"color: {accent}; font-size: 11px; font-weight: 600; background: transparent;")
        self.lastactive_value_lbl.setText(user.get("last_active", "\u2014"))
        self.verified_value_lbl.setText("\u2713 Verified")
        self.verified_value_lbl.setStyleSheet(f"color: {PALETTE['green']}; font-size: 11px; font-weight: 500; background: transparent;")

        self._refresh_suspend_button()
        self.notes_edit.setPlainText(user.get("notes", ""))

    def close_panel(self):
        self._current_user = None
        self.content.hide()
        self.placeholder.show()
        self.close_requested.emit()

    def _refresh_suspend_button(self):
        if self._current_user is None:
            return
        if self._current_user["status"] == "Suspended":
            self.suspend_btn.setText(" Reactivate User")
            self.suspend_btn.setIcon(qta.icon('fa5s.user-check', color="#ffffff"))
        else:
            self.suspend_btn.setText(" Suspend User")
            self.suspend_btn.setIcon(qta.icon('fa5s.user-slash', color="#ffffff"))

    # ------------------------------------------------------------------
    # Quick actions
    # ------------------------------------------------------------------
    def _add_credits(self):
        if self._current_user is None:
            return
        amount = _AmountDialog.ask(self, "Add Credits", "Add Credits", 'fa5s.plus')
        if amount is None:
            return
        self._apply_credit_adjustment(amount)

    def _deduct_credits(self):
        if self._current_user is None:
            return
        amount = _AmountDialog.ask(self, "Deduct Credits", "Deduct Credits", 'fa5s.minus')
        if amount is None:
            return
        self._apply_credit_adjustment(-amount)

    def _apply_credit_adjustment(self, signed_amount: int):
        """Shared by _add_credits/_deduct_credits -- amount is already
        signed (positive to grant, negative to deduct) by the time it
        gets here. Server-authoritative now: the balance change and the
        clamp-at-0 both happen in admin_adjust_credits() on the backend
        (see server/app/db.py), so this just sends the request and
        reflects whatever balance comes back."""
        try:
            result = admin_adjust_credits(self._current_user["id"], signed_amount)
        except ApiError as exc:
            InfoDialog.show(self, "Couldn't update credits", str(exc), success=False)
            return

        self._current_user["credits"] = result["credits"]
        self.show_user(self._current_user)
        self.user_changed.emit()

    def _reset_password(self):
        if self._current_user is None:
            return
        confirmed = ConfirmDialog.ask(
            self, "Reset Password",
            f'Send a password reset link to {self._current_user["email"]}?',
            confirm_text="Send Reset Link", icon_name='fa5s.lock', danger=False,
        )
        if not confirmed:
            return
        InfoDialog.show(
            self, "Reset Password",
            "Sending real reset emails isn't wired up to a backend yet -- "
            "this is a prototype-only Users tab.",
            icon_name='fa5s.lock', success=True,
        )

    def _toggle_suspend(self):
        if self._current_user is None:
            return
        suspending = self._current_user["status"] != "Suspended"
        confirmed = ConfirmDialog.ask(
            self, "Suspend User" if suspending else "Reactivate User",
            f'{"Suspend" if suspending else "Reactivate"} {self._current_user["name"]}\'s account?',
            confirm_text="Suspend User" if suspending else "Reactivate User",
            icon_name='fa5s.user-slash', danger=suspending,
        )
        if not confirmed:
            return
        new_status = "Suspended" if suspending else "Active"
        try:
            admin_update_user(self._current_user["id"], status=new_status.lower())
        except ApiError as exc:
            InfoDialog.show(self, "Couldn't update status", str(exc), success=False)
            return
        self._current_user["status"] = new_status
        self.show_user(self._current_user)
        self.user_changed.emit()

    def _view_full_profile_not_wired_up(self):
        InfoDialog.show(
            self, "View Full Profile",
            "A full profile page (activity log, transaction history, device "
            "list) isn't wired up to a backend yet -- this is a "
            "prototype-only Users tab.",
            icon_name='fa5s.external-link-alt', success=True,
        )

    def _save_note(self):
        if self._current_user is None:
            return
        self._current_user["notes"] = self.notes_edit.toPlainText()
        InfoDialog.show(
            self, "Note Saved", f'Your note about {self._current_user["name"]} was saved.',
            icon_name='fa5s.check-circle', success=True,
        )


class UsersPage(QSplitter):
    """
    The "Users" nav tab (admin-only). Splitter of [table column,
    UserDetailPanel] -- same shape as search_leads.SearchLeadsPage's
    [queries panel, main, detail panel]. Owns:
      - self.users -- loaded from the real `users` table via
        api_client.admin_list_users(), converted to the UI's dict shape
        by _db_row_to_ui(); quick actions mutate these same dict objects
        *and* persist via api_client.admin_update_user()/
        admin_adjust_credits(), so the table/detail panel and the
        database stay in sync.
      - self.current_admin_email -- the currently logged-in admin's own
        email (passed in from dashboard.py). Their own row is filtered
        out of self.users so an admin never sees themselves in the
        table -- other admins are still shown normally.
      - self.status_filter / self.role_filter / self.search_text --
        current pill-tab + dropdown + search box state, recombined by
        apply_filter() into self.current_data.
      - pagination state (page_size / current_page), same pattern as
        exports.ExportsPage.
    """
    def __init__(self, current_admin_email: str = None, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setHandleWidth(3)
        self.setChildrenCollapsible(False)

        self.current_admin_email = current_admin_email
        self.users = self._load_users()
        self.status_filter = "All Users"
        self.role_filter = "All Roles"
        self.search_text = ""
        self.current_data = list(self.users)
        self.page_size = 10
        self.current_page = 1
        self.sort_column = None
        self.sort_ascending = True

        self.detail_panel = UserDetailPanel()
        self.detail_panel.user_changed.connect(self._on_user_changed)
        self.detail_panel.close_requested.connect(self._close_detail_panel)

        main = self._build_main()

        self.addWidget(main)
        self.addWidget(self.detail_panel)
        self.setStretchFactor(0, 1)
        self.setStretchFactor(1, 0)
        self.setSizes([1100, 340])
        self.detail_panel.hide()  # closed by default -- nothing selected yet

        self.refresh_stats()
        self.apply_filter()

    def _load_users(self) -> list:
        """Every row in the `users` table, converted to the UI dict shape,
        with the currently logged-in admin's own row excluded (matched by
        email, case-insensitively -- they can still see *other* admins).
        Phase 5: goes through GET /admin/users (api_client.admin_list_users())
        instead of a direct data.db call; on a connection failure this
        starts empty rather than erroring out of UsersPage construction."""
        own_email = (self.current_admin_email or "").strip().lower()
        try:
            rows = admin_list_users()
        except ApiError:
            return []
        return [
            _db_row_to_ui(row) for row in rows
            if row["email"].strip().lower() != own_email
        ]

    def _open_detail_panel(self, user: dict):
        """Shared by row clicks and the eye-icon button -- opens the panel
        (if it was collapsed) and points it at the given user."""
        self.detail_panel.show_user(user)
        self.detail_panel.show()

    def _close_detail_panel(self):
        self.detail_panel.hide()

    def _on_user_changed(self):
        """A quick action in the detail panel mutated a user dict in
        place -- refresh the table/stats to reflect it."""
        self.apply_filter()
        self.refresh_stats()

    # ------------------------------------------------------------------
    # Left column: header + stat cards + pill tabs + search/filter row +
    # table + pagination footer, all inside a QScrollArea.
    # ------------------------------------------------------------------
    def _build_main(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setObjectName("DashScroll")
        scroll.setWidgetResizable(True)

        content = QWidget()
        outer = QVBoxLayout(content)
        outer.setContentsMargins(20, 16, 20, 20)
        outer.setSpacing(14)

        # --- Header ---
        header_row = QHBoxLayout()
        header_col = QVBoxLayout()
        header_col.setSpacing(2)
        title_lbl = QLabel("Users")
        title_lbl.setObjectName("HeaderTitle")
        header_col.addWidget(title_lbl)
        sub_lbl = QLabel("Manage and monitor all user accounts.")
        sub_lbl.setObjectName("HeaderSub")
        header_col.addWidget(sub_lbl)
        header_row.addLayout(header_col)
        header_row.addStretch()

        add_btn = QPushButton(qta.icon('fa5s.plus', color="#ffffff"), " Add User")
        add_btn.setObjectName("RedBtn")
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.clicked.connect(self._add_user_not_wired_up)
        header_row.addWidget(add_btn)

        outer.addLayout(header_row)
        outer.addWidget(self._build_table_column())

        scroll.setWidget(content)
        return scroll

    # ------------------------------------------------------------------
    # Stat cards (derived from self.users, so they stay correct after a
    # Suspend/Delete)
    # ------------------------------------------------------------------
    def refresh_stats(self):
        while self.stats_row.count():
            item = self.stats_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        total = len(self.users)
        active = [u for u in self.users if u["status"] == "Active"]
        suspended = [u for u in self.users if u["status"] == "Suspended"]
        with_credits = [u for u in self.users if u.get("credits", 0) > 0]

        def _joined(u):
            raw = u.get("joined_at_raw")
            if not raw:
                return None
            try:
                return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return None

        now = datetime.now()
        cutoff_7 = now - timedelta(days=7)
        cutoff_14 = now - timedelta(days=14)
        joined_dates = [d for d in (_joined(u) for u in self.users) if d is not None]

        def _pct_delta(old_count: int, new_count: int) -> str | None:
            """Real %-change string, or None when there's no honest baseline
            to divide by (e.g. 0 users 7 days ago) -- shown blank rather
            than a made-up number in that case."""
            if old_count <= 0:
                return None
            pct = (new_count - old_count) / old_count * 100
            sign = "+" if pct >= 0 else ""
            return f"{sign}{pct:.1f}% vs last 7 days"

        # Total Users: real signups have a real created_at, so "vs last 7
        # days" can honestly compare today's total against the total that
        # already existed 7 days ago.
        total_7d_ago = sum(1 for d in joined_dates if d <= cutoff_7)
        total_delta = _pct_delta(total_7d_ago, total)

        # New Users: this week's signups vs the week before -- also real,
        # from the same created_at timestamps.
        new_this_week = sum(1 for d in joined_dates if d > cutoff_7)
        new_prev_week = sum(1 for d in joined_dates if cutoff_14 < d <= cutoff_7)
        new_delta = _pct_delta(new_prev_week, new_this_week)

        # Active / Suspended / Users-with-credits have no honest "7 days
        # ago" to compare against -- the DB doesn't keep a history of past
        # status or credit balances (see data/db.py), only the current
        # row. Rather than invent a number, these three cards show just
        # the current count and skip the delta line.
        cards = [
            ("fa5s.users", config.rgba_from_hex(PALETTE['blue'], 0.15), PALETTE['blue'], "Total Users",
             f"{total:,}", total_delta),
            ("fa5s.user-check", config.rgba_from_hex(PALETTE['green'], 0.15), PALETTE['green'], "Active Users",
             f"{len(active):,}", None),
            ("fa5s.user-plus", config.rgba_from_hex(PALETTE['blue_solid'], 0.15), PALETTE['blue'], "New Users (7d)",
             f"{new_this_week:,}", new_delta),
            ("fa5s.coins", config.rgba_from_hex(PALETTE['yellow'], 0.15), PALETTE['yellow'], "Users with Credits",
             f"{len(with_credits):,}", None),
            ("fa5s.user-slash", config.rgba_from_hex(PALETTE['red_solid'], 0.15), PALETTE['red'], "Suspended Users",
             f"{len(suspended):,}", None),
        ]
        for icon, bg, color, label, value, delta in cards:
            self.stats_row.addWidget(StatCard(icon, bg, color, label, value, delta))

    def _add_user_not_wired_up(self):
        InfoDialog.show(
            self, "Add User",
            "Creating a new user account isn't wired up to a backend yet -- "
            "this is a prototype-only Users tab.",
            icon_name='fa5s.user-plus', success=True,
        )

    # ------------------------------------------------------------------
    # Pill tabs + search/filter row + table + pagination footer
    # ------------------------------------------------------------------
    def _build_table_column(self) -> QWidget:
        col = QWidget()
        layout = QVBoxLayout(col)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        self.stats_row = QHBoxLayout()
        self.stats_row.setSpacing(14)
        layout.addLayout(self.stats_row)

        # --- Pill tab bar ---
        tab_bar = QHBoxLayout()
        tab_bar.setSpacing(4)
        self.tab_buttons = {}
        for name in ("All Users", "Active", "Inactive", "Suspended"):
            btn = QPushButton(name)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _checked=False, n=name: self.set_status_filter(n))
            self.tab_buttons[name] = btn
            tab_bar.addWidget(btn)
        tab_bar.addStretch()
        layout.addLayout(tab_bar)

        divider = QFrame()
        divider.setStyleSheet(f"background-color: {PALETTE['divider']}; max-height: 1px; min-height: 1px;")
        layout.addWidget(divider)

        # --- Search + role filter + Filters button row ---
        search_row = QHBoxLayout()
        search_row.setSpacing(8)

        self.search_edit = SearchLineEdit()
        self.search_edit.setPlaceholderText("Search by name, email or phone... (press Enter)")
        # Search only runs on explicit action -- Enter, or clicking the
        # magnifying-glass icon -- not on every keystroke, matching the
        # same search_triggered/returnPressed pattern as Search Leads,
        # Exports, Transactions, and Rewards (see SearchLineEdit's
        # docstring in ui/components/widgets.py).
        self.search_edit.search_triggered.connect(self._trigger_search)
        self.search_edit.returnPressed.connect(self._trigger_search)
        search_row.addWidget(self.search_edit, stretch=1)

        self.role_combo = QComboBox()
        self.role_combo.setObjectName("RowModeCombo")
        roles = ["All Roles"] + sorted({u["role"] for u in self.users})
        self.role_combo.addItems(roles)
        self.role_combo.setMinimumWidth(120)
        self.role_combo.currentTextChanged.connect(self.on_role_changed)
        search_row.addWidget(self.role_combo)

        filters_btn = QPushButton(qta.icon('fa5s.filter', color=PALETTE['text_muted']), " Filters")
        filters_btn.setObjectName("OutlineBtn")
        filters_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        filters_btn.clicked.connect(self._open_filters_not_wired_up)
        search_row.addWidget(filters_btn)

        layout.addLayout(search_row)

        # --- Table ---
        self.columns = [
            {"label": "", "key": None},
            {"label": "#", "key": None},
            {"label": "User", "key": "name"},
            {"label": "Role", "key": "role"},
            {"label": "Credits", "key": "credits"},
            {"label": "Spent Credits", "key": "spent"},
            {"label": "Joined Date", "key": "joined"},
            {"label": "Last Active", "key": "last_active"},
            {"label": "Status", "key": "status"},
        ]
        (self.COL_CHECK, self.COL_NUM, self.COL_USER, self.COL_ROLE, self.COL_CREDITS,
         self.COL_SPENT, self.COL_JOINED, self.COL_LASTACTIVE, self.COL_STATUS,
         ) = range(9)

        self.table = QTableWidget()
        self.table.setColumnCount(len(self.columns))
        self.table.setHorizontalHeaderLabels([c["label"] for c in self.columns])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(52)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.cellClicked.connect(self.on_row_clicked)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(self.COL_CHECK, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(self.COL_CHECK, 30)
        header.setSectionResizeMode(self.COL_NUM, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(self.COL_NUM, 40)
        for col_idx, width in [
            (self.COL_USER, 220), (self.COL_ROLE, 110), (self.COL_CREDITS, 90),
            (self.COL_SPENT, 100), (self.COL_JOINED, 130), (self.COL_LASTACTIVE, 130),
            (self.COL_STATUS, 100),
        ]:
            header.setSectionResizeMode(col_idx, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(col_idx, width)
        header.setSectionResizeMode(self.COL_USER, QHeaderView.ResizeMode.Stretch)
        header.setMinimumSectionSize(30)

        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.sectionClicked.connect(self.handle_sort)

        layout.addWidget(self.table)

        # --- Footer: result count + pagination ---
        footer = QHBoxLayout()
        self.footer_label = QLabel("")
        self.footer_label.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 11px;")
        footer.addWidget(self.footer_label)
        footer.addStretch()

        page_size_lbl = QLabel("Rows per page:")
        page_size_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 11px;")
        footer.addWidget(page_size_lbl)

        self.page_size_combo = QComboBox()
        self.page_size_combo.addItems(["5", "10", "25", "50"])
        self.page_size_combo.setCurrentText(str(self.page_size))
        self.page_size_combo.setFixedWidth(64)
        self.page_size_combo.setStyleSheet(
            f"QComboBox {{ background-color: {PALETTE['bg_surface']}; border: 1px solid {PALETTE['border']}; "
            f"border-radius: 5px; color: {PALETTE['text_secondary']}; padding: 0px 6px; }}"
        )
        self.page_size_combo.currentTextChanged.connect(self._on_page_size_changed)
        footer.addWidget(self.page_size_combo)

        footer.addSpacing(10)

        self.prev_page_btn = QPushButton()
        self.prev_page_btn.setObjectName("OutlineBtn")
        self.prev_page_btn.setIcon(qta.icon('fa5s.chevron-left', color=PALETTE['text_muted']))
        self.prev_page_btn.setIconSize(QSize(10, 10))
        self.prev_page_btn.setFixedSize(28, 28)
        self.prev_page_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.prev_page_btn.clicked.connect(lambda: self.go_to_page(self.current_page - 1))
        footer.addWidget(self.prev_page_btn)

        self.page_buttons_container = QWidget()
        self.page_buttons_layout = QHBoxLayout(self.page_buttons_container)
        self.page_buttons_layout.setContentsMargins(4, 0, 4, 0)
        self.page_buttons_layout.setSpacing(4)
        footer.addWidget(self.page_buttons_container)

        self.next_page_btn = QPushButton()
        self.next_page_btn.setObjectName("OutlineBtn")
        self.next_page_btn.setIcon(qta.icon('fa5s.chevron-right', color=PALETTE['text_muted']))
        self.next_page_btn.setIconSize(QSize(10, 10))
        self.next_page_btn.setFixedSize(28, 28)
        self.next_page_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.next_page_btn.clicked.connect(lambda: self.go_to_page(self.current_page + 1))
        footer.addWidget(self.next_page_btn)

        layout.addLayout(footer)

        self.set_status_filter("All Users")
        return col

    # ------------------------------------------------------------------
    # Filtering / search / pill tabs
    # ------------------------------------------------------------------
    def set_status_filter(self, name: str):
        self.status_filter = name
        for btn_name, btn in self.tab_buttons.items():
            is_active = btn_name == name
            btn.setObjectName("BillingTabActive" if is_active else "BillingTabItem")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        self.current_page = 1
        self.apply_filter()

    def _trigger_search(self):
        self.search_text = self.search_edit.text().strip().lower()
        self.current_page = 1
        self.apply_filter()

    def on_role_changed(self, role: str):
        self.role_filter = role
        self.current_page = 1
        self.apply_filter()

    def apply_filter(self):
        filtered = list(self.users)

        if self.status_filter != "All Users":
            filtered = [u for u in filtered if u["status"] == self.status_filter]

        if self.role_filter and self.role_filter != "All Roles":
            filtered = [u for u in filtered if u["role"] == self.role_filter]

        if self.search_text:
            filtered = [
                u for u in filtered
                if self.search_text in u["name"].lower()
                or self.search_text in u["email"].lower()
                or self.search_text in u.get("phone", "").lower()
            ]

        if self.sort_column is not None:
            key = self.columns[self.sort_column]["key"]
            filtered = sorted(
                filtered,
                key=lambda u: self._sort_key(u, key),
                reverse=not self.sort_ascending,
            )

        self.current_data = filtered
        self.populate_table()

    def handle_sort(self, column: int):
        key = self.columns[column]["key"]
        if key is None:
            return

        if self.sort_column == column:
            self.sort_ascending = not self.sort_ascending
        else:
            self.sort_column = column
            self.sort_ascending = True

        order = Qt.SortOrder.AscendingOrder if self.sort_ascending else Qt.SortOrder.DescendingOrder
        self.table.horizontalHeader().setSortIndicator(column, order)

        self.current_page = 1
        self.apply_filter()

    @staticmethod
    def _sort_key(user: dict, key: str):
        if key == "joined":
            return user.get("joined_at_raw") or ""
        if key == "last_active":
            return user.get("last_active_at_raw") or ""
        if key in ("credits", "spent"):
            return user.get(key, 0)
        return str(user.get(key, "")).lower()

    def _open_filters_not_wired_up(self):
        InfoDialog.show(
            self, "Filters",
            "Advanced user filters (joined date range, credits range, etc.) "
            "aren't wired up to a backend yet -- use the tabs, search box, "
            "and role dropdown above for now.",
            icon_name='fa5s.filter', success=True,
        )

    # ------------------------------------------------------------------
    # Table population + pagination
    # ------------------------------------------------------------------
    def total_pages(self) -> int:
        return max(1, (len(self.current_data) + self.page_size - 1) // self.page_size)

    def go_to_page(self, page: int):
        total = self.total_pages()
        page = max(1, min(page, total))
        if page == self.current_page:
            return
        self.current_page = page
        self.populate_table()

    def _on_page_size_changed(self, text: str):
        try:
            self.page_size = int(text)
        except ValueError:
            return
        self.current_page = 1
        self.populate_table()

    def on_row_clicked(self, row: int, column: int):
        if 0 <= row < len(self._current_page_data):
            self._open_detail_panel(self._current_page_data[row])

    def populate_table(self):
        self.current_page = max(1, min(self.current_page, self.total_pages()))
        start = (self.current_page - 1) * self.page_size
        page_data = self.current_data[start:start + self.page_size]
        self._current_page_data = page_data

        self.table.setRowCount(len(page_data))
        for row, user in enumerate(page_data):
            self.table.setItem(row, self.COL_CHECK, QTableWidgetItem())

            num_item = QTableWidgetItem(str(start + row + 1))
            num_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            num_item.setForeground(QColor(PALETTE['text_muted']))
            self.table.setItem(row, self.COL_NUM, num_item)

            self.table.setItem(row, self.COL_USER, QTableWidgetItem())
            self.table.setCellWidget(row, self.COL_USER, _user_cell(user))

            self.table.setItem(row, self.COL_ROLE, QTableWidgetItem())
            self.table.setCellWidget(row, self.COL_ROLE, _role_pill(user["role"]))

            is_admin = user["role"] in ("Admin", "Super Admin")

            credits_item = QTableWidgetItem("\u2014" if is_admin else f'{user.get("credits", 0):,}')
            credits_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if is_admin:
                credits_item.setForeground(QColor(PALETTE['text_muted']))
            self.table.setItem(row, self.COL_CREDITS, credits_item)

            spent_item = QTableWidgetItem("\u2014" if is_admin else f'{user.get("spent", 0):,}')
            spent_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if is_admin:
                spent_item.setForeground(QColor(PALETTE['text_muted']))
            self.table.setItem(row, self.COL_SPENT, spent_item)

            self.table.setItem(row, self.COL_JOINED, QTableWidgetItem(user.get("joined", "")))
            self.table.setItem(row, self.COL_LASTACTIVE, QTableWidgetItem(user.get("last_active", "")))

            self.table.setItem(row, self.COL_STATUS, QTableWidgetItem())
            self.table.setCellWidget(row, self.COL_STATUS, _status_pill(user["status"]))

        total_results = len(self.current_data)
        showing_from = start + 1 if page_data else 0
        showing_to = start + len(page_data)
        self.footer_label.setText(f"Showing {showing_from} to {showing_to} of {total_results} users")

        # Keep the detail panel in sync if it's showing a user that just
        # scrolled off this page (e.g. after a filter change) -- leave it
        # open on stale data rather than surprise-closing it.
        self.render_page_buttons()

    def render_page_buttons(self):
        while self.page_buttons_layout.count():
            item = self.page_buttons_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        total = self.total_pages()
        current = self.current_page

        if total <= 7:
            pages = list(range(1, total + 1))
        else:
            keep = {1, 2, total - 1, total, current - 1, current, current + 1}
            pages = sorted(p for p in keep if 1 <= p <= total)

        display = []
        prev_p = None
        for p in pages:
            if prev_p is not None and p - prev_p > 1:
                display.append(None)
            display.append(p)
            prev_p = p

        for p in display:
            if p is None:
                dots = QLabel("...")
                dots.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 11px; background: transparent;")
                self.page_buttons_layout.addWidget(dots)
                continue

            btn = QPushButton(str(p))
            btn.setFixedSize(28, 28)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            if p == current:
                # Mirrors #RedBtn's accent-bg / white-text styling (see
                # config.qss()) -- built by hand since this button isn't
                # routed through the QSS object-name system.
                btn.setStyleSheet(
                    f"background-color: {PALETTE['accent']}; color: white; border: none; "
                    "border-radius: 5px; font-weight: 600; font-size: 11px;"
                )
            else:
                btn.setStyleSheet(
                    f"QPushButton {{ background-color: transparent; color: {PALETTE['text_muted']}; "
                    f"border: 1px solid {PALETTE['border']}; border-radius: 5px; font-size: 11px; }}"
                    f"QPushButton:hover {{ background-color: {PALETTE['bg_hover']}; color: {PALETTE['text_secondary']}; }}"
                )
            btn.clicked.connect(lambda _checked=False, page=p: self.go_to_page(page))
            self.page_buttons_layout.addWidget(btn)