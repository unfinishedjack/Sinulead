"""
transactions.py

TransactionsPage: the "Transactions" nav tab -- admin-only. Same shape as
users_page.UsersPage: KPI stat cards, pill-tab + search/method filtered +
paginated table of every credit-purchase transaction, plus a right-hand
Transaction Details panel with Approve Payment / Reject Payment quick
actions and an admin notes box.

Like billing.py / users_page.py, this is a prototype: Approve/Reject just
flip the in-memory status on data.TRANSACTIONS rather than touching a real
payments backend.

Depends on: config, data (TRANSACTIONS), dialogs (InfoDialog, ConfirmDialog),
overview (StatCard), users_page (_initials_pixmap, reused for the avatar).
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QPushButton, QFrame, QScrollArea, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QComboBox, QSplitter,
    QTextEdit,
)
from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QColor
import qtawesome as qta

from core import config
from core.config import PALETTE
from data import TRANSACTIONS
from ui.dialogs.dialogs import InfoDialog, ConfirmDialog
from ui.components.widgets import status_badge, SearchLineEdit
from ui.pages.overview import StatCard
from ui.pages.users_page import _initials_pixmap
from ui.pages.billing import _method_pixmap


def _status_colors() -> dict:
    """Looked up fresh on every call (not a frozen module-level dict) so it
    tracks theme switches -- same tinting logic as users_page._status_colors."""
    return {
        "Pending": (PALETTE["yellow_solid"], PALETTE["yellow_soft"]),
        "Completed": (PALETTE["green_solid"], PALETTE["green_soft"]),
        "Rejected": (PALETTE["red_solid"], PALETTE["red_soft"]),
        "Refunded": (PALETTE["blue_solid"], PALETTE["blue_soft"]),
    }


def _status_pill(status: str) -> QWidget:
    """Thin wrapper over the shared widgets.status_badge (see PROGRESS.md,
    Phase 1a) -- kept as a local name so call sites below didn't need to
    change."""
    return status_badge(status, _status_colors())


def _user_cell(txn: dict) -> QWidget:
    """Avatar + name (bold) / email (muted) stacked, same look as the
    Users table's User column."""
    wrap = QWidget()
    wrap.setStyleSheet("background: transparent;")
    row = QHBoxLayout(wrap)
    row.setContentsMargins(6, 4, 6, 4)
    row.setSpacing(10)

    avatar = QLabel()
    avatar.setStyleSheet("background: transparent;")
    avatar.setPixmap(_initials_pixmap(txn["name"], 32))
    row.addWidget(avatar)

    col = QVBoxLayout()
    col.setSpacing(1)
    name_lbl = QLabel(txn["name"])
    name_lbl.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 12px; font-weight: 600; background: transparent;")
    col.addWidget(name_lbl)
    email_lbl = QLabel(txn["email"])
    email_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 11px; background: transparent;")
    col.addWidget(email_lbl)
    row.addLayout(col, stretch=1)
    return wrap


def _package_cell(txn: dict) -> QWidget:
    """Credits amount, with a small green bonus pill underneath when
    the package includes one (matches the reference screenshot)."""
    wrap = QWidget()
    wrap.setStyleSheet("background: transparent;")
    col = QVBoxLayout(wrap)
    col.setContentsMargins(6, 4, 6, 4)
    col.setSpacing(2)
    credits_lbl = QLabel(f'{txn["credits"]:,} Credits')
    credits_lbl.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 12px; background: transparent;")
    col.addWidget(credits_lbl)
    if txn.get("bonus"):
        bonus_lbl = QLabel(txn["bonus"])
        bonus_lbl.setStyleSheet(
            f"color: {PALETTE['green']}; font-size: 10px; font-weight: 600; background: transparent;"
        )
        col.addWidget(bonus_lbl)
    return wrap


class TransactionDetailPanel(QFrame):
    """
    Right-hand "Transaction Details" panel -- shows the currently-selected
    transaction, with Approve Payment / Reject Payment quick actions and an
    admin notes box. Same rebuild-once/update-in-place pattern as
    users_page.UserDetailPanel.
    """
    transaction_changed = Signal()  # emitted after Approve/Reject/note-save mutates the transaction
    close_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DetailPanel")
        self.setMinimumWidth(300)
        self.setMaximumWidth(400)
        self._current_txn = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)

        header_row = QHBoxLayout()
        header = QLabel("Transaction Details")
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

        self.placeholder = QLabel("Select a transaction to see details")
        self.placeholder.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 11px;")
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(self.placeholder)

        scroll = QScrollArea()
        scroll.setObjectName("DashScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.content = QFrame()
        content_layout = QVBoxLayout(self.content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(14)

        # --- Status banner ---
        self.status_banner = QFrame()
        banner_layout = QHBoxLayout(self.status_banner)
        banner_layout.setContentsMargins(12, 10, 12, 10)
        banner_layout.setSpacing(8)
        self.status_icon_lbl = QLabel()
        self.status_icon_lbl.setStyleSheet("background: transparent;")
        banner_layout.addWidget(self.status_icon_lbl)
        status_text_col = QVBoxLayout()
        status_text_col.setSpacing(0)
        self.status_title_lbl = QLabel()
        self.status_title_lbl.setStyleSheet("font-size: 12px; font-weight: 600; background: transparent;")
        status_text_col.addWidget(self.status_title_lbl)
        self.status_sub_lbl = QLabel()
        self.status_sub_lbl.setStyleSheet(f"color: {PALETTE['text_muted']}; font-size: 10px; background: transparent;")
        status_text_col.addWidget(self.status_sub_lbl)
        banner_layout.addLayout(status_text_col, stretch=1)
        content_layout.addWidget(self.status_banner)

        # --- Reference ID ---
        ref_card, ref_layout = self._card(None)
        ref_row = QHBoxLayout()
        ref_col = QVBoxLayout()
        ref_col.setSpacing(2)
        ref_label = QLabel("Reference ID")
        ref_label.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 10px; background: transparent;")
        ref_col.addWidget(ref_label)
        self.ref_value_lbl = QLabel()
        self.ref_value_lbl.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 12px; font-weight: 600; background: transparent;")
        ref_col.addWidget(self.ref_value_lbl)
        ref_row.addLayout(ref_col, stretch=1)
        ref_layout.addLayout(ref_row)
        content_layout.addWidget(ref_card)

        # --- User ---
        user_card, user_layout = self._card("User")
        user_row = QHBoxLayout()
        self.user_avatar_lbl = QLabel()
        self.user_avatar_lbl.setStyleSheet("background: transparent;")
        user_row.addWidget(self.user_avatar_lbl)
        user_col = QVBoxLayout()
        user_col.setSpacing(2)
        self.user_name_lbl = QLabel()
        self.user_name_lbl.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 12px; font-weight: 600; background: transparent;")
        user_col.addWidget(self.user_name_lbl)
        self.user_email_lbl = QLabel()
        self.user_email_lbl.setStyleSheet(f"color: {PALETTE['text_muted']}; font-size: 11px; background: transparent;")
        user_col.addWidget(self.user_email_lbl)
        user_row.addLayout(user_col, stretch=1)
        view_profile_btn = QPushButton("View Profile")
        view_profile_btn.setObjectName("OutlineBtn")
        view_profile_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        view_profile_btn.clicked.connect(self._view_profile_not_wired_up)
        user_row.addWidget(view_profile_btn)
        user_layout.addLayout(user_row)
        content_layout.addWidget(user_card)

        # --- Payment / Package / Amount grid ---
        info_card, info_layout = self._card(None)
        grid = QGridLayout()
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(10)
        self.method_icon_lbl, self.method_value_lbl = self._grid_method_field(grid, 0, 0, "Payment Method")
        self.package_value_lbl = self._grid_field(grid, 0, 1, "Package")
        self.amount_value_lbl = self._grid_field(grid, 1, 0, "Amount Paid")
        self.credits_value_lbl = self._grid_field(grid, 1, 1, "Credits to Add")
        self.submitted_value_lbl = self._grid_field(grid, 2, 0, "Submitted")
        info_layout.addLayout(grid)
        content_layout.addWidget(info_card)

        # --- Receipt ---
        receipt_card, receipt_layout = self._card("Receipt (Proof of Payment)")
        self.receipt_name_lbl = QLabel()
        self.receipt_name_lbl.setStyleSheet(f"color: {PALETTE['text_muted']}; font-size: 11px; background: transparent;")
        receipt_layout.addWidget(self.receipt_name_lbl)
        view_full_btn = QPushButton(qta.icon('fa5s.expand', color=PALETTE['text_muted']), " View Full Size")
        view_full_btn.setObjectName("OutlineBtn")
        view_full_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        view_full_btn.clicked.connect(self._view_receipt_not_wired_up)
        receipt_layout.addWidget(view_full_btn)
        content_layout.addWidget(receipt_card)

        # --- User note ---
        note_card, note_layout = self._card("User Note")
        self.user_note_lbl = QLabel()
        self.user_note_lbl.setWordWrap(True)
        self.user_note_lbl.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 11px; background: transparent;")
        note_layout.addWidget(self.user_note_lbl)
        content_layout.addWidget(note_card)
        self.note_card = note_card

        # --- Admin notes ---
        admin_notes_card, admin_notes_layout = self._card("Admin Notes (optional)")
        self.notes_edit = QTextEdit()
        self.notes_edit.setObjectName("NoteEdit")
        self.notes_edit.setPlaceholderText("Add a note about this transaction...")
        self.notes_edit.setFixedHeight(70)
        admin_notes_layout.addWidget(self.notes_edit)
        save_note_btn = QPushButton("Save Note")
        save_note_btn.setObjectName("OutlineBtn")
        save_note_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_note_btn.clicked.connect(self._save_note)
        admin_notes_layout.addWidget(save_note_btn, alignment=Qt.AlignmentFlag.AlignRight)
        content_layout.addWidget(admin_notes_card)

        # --- Approve / Reject ---
        actions_row = QHBoxLayout()
        actions_row.setSpacing(8)
        self.approve_btn = QPushButton(qta.icon('fa5s.check', color="#ffffff"), " Approve Payment")
        self.approve_btn.setObjectName("RedBtn")
        self.approve_btn.setStyleSheet(
            f"QPushButton#RedBtn {{ background-color: {PALETTE['green_solid']}; }}"
            f"QPushButton#RedBtn:hover {{ background-color: {PALETTE['green']}; }}"
        )
        self.approve_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.approve_btn.clicked.connect(self._approve)
        actions_row.addWidget(self.approve_btn)

        self.reject_btn = QPushButton(qta.icon('fa5s.times', color="#ffffff"), " Reject Payment")
        self.reject_btn.setObjectName("RedBtn")
        self.reject_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reject_btn.clicked.connect(self._reject)
        actions_row.addWidget(self.reject_btn)
        content_layout.addLayout(actions_row)

        download_btn = QPushButton(qta.icon('fa5s.download', color=PALETTE['text_muted']), " Download Receipt")
        download_btn.setObjectName("OutlineBtn")
        download_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        download_btn.clicked.connect(self._download_not_wired_up)
        content_layout.addWidget(download_btn)

        content_layout.addStretch()
        scroll.setWidget(self.content)
        outer.addWidget(scroll)

        self.content.hide()

    # ------------------------------------------------------------------
    # Small widget builders
    # ------------------------------------------------------------------
    def _card(self, title):
        frame = QFrame()
        frame.setObjectName("DashCard")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)
        if title:
            title_lbl = QLabel(title)
            title_lbl.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 12px; font-weight: 600; background: transparent;")
            layout.addWidget(title_lbl)
        return frame, layout

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

    def _grid_method_field(self, grid: QGridLayout, row: int, col: int, label: str):
        """Same as _grid_field but with a small payment-method logo/icon
        to the left of the value text."""
        cell = QVBoxLayout()
        cell.setSpacing(2)
        label_lbl = QLabel(label)
        label_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 10px; background: transparent;")
        cell.addWidget(label_lbl)

        value_row = QHBoxLayout()
        value_row.setSpacing(6)
        icon_lbl = QLabel()
        icon_lbl.setFixedSize(16, 16)
        icon_lbl.setStyleSheet("background: transparent;")
        value_row.addWidget(icon_lbl)
        value_lbl = QLabel("")
        value_lbl.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 11px; font-weight: 500; background: transparent;")
        value_lbl.setWordWrap(True)
        value_row.addWidget(value_lbl, stretch=1)
        cell.addLayout(value_row)

        grid.addLayout(cell, row, col)
        return icon_lbl, value_lbl

    # ------------------------------------------------------------------
    # Show / close
    # ------------------------------------------------------------------
    def show_transaction(self, txn: dict):
        self._current_txn = txn
        self.placeholder.hide()
        self.content.show()

        accent, bg = _status_colors().get(txn["status"], (PALETTE["text_muted"], config.rgba_from_hex(PALETTE["text_muted"], 0.12)))
        icon_name = {
            "Pending": "fa5s.clock", "Completed": "fa5s.check-circle",
            "Rejected": "fa5s.times-circle", "Refunded": "fa5s.undo",
        }.get(txn["status"], "fa5s.info-circle")
        self.status_banner.setStyleSheet(f"background-color: {bg}; border-radius: 8px;")
        self.status_icon_lbl.setPixmap(qta.icon(icon_name, color=accent).pixmap(20, 20))
        self.status_title_lbl.setText(txn["status"])
        self.status_title_lbl.setStyleSheet(f"color: {accent}; font-size: 12px; font-weight: 600; background: transparent;")
        sub = {
            "Pending": "Waiting for admin review", "Completed": "Approved and credits added",
            "Rejected": "Payment was rejected", "Refunded": "Payment was refunded",
        }.get(txn["status"], "")
        self.status_sub_lbl.setText(sub)

        self.ref_value_lbl.setText(txn["ref"])

        self.user_avatar_lbl.setPixmap(_initials_pixmap(txn["name"], 36))
        self.user_name_lbl.setText(txn["name"])
        self.user_email_lbl.setText(txn["email"])

        self.method_value_lbl.setText(txn["method"])
        self.method_icon_lbl.setPixmap(_method_pixmap(txn["method"], 16))
        package_text = f'{txn["credits"]:,} Credits'
        if txn.get("bonus"):
            package_text += f'  ({txn["bonus"]})'
        self.package_value_lbl.setText(package_text)
        self.amount_value_lbl.setText(f'\u20b1{txn["amount"]:,.2f}')
        self.credits_value_lbl.setText(f'+{txn["credits"]:,} Credits')
        self.credits_value_lbl.setStyleSheet(f"color: {PALETTE['green']}; font-size: 11px; font-weight: 600; background: transparent;")
        self.submitted_value_lbl.setText(txn["submitted"])

        self.receipt_name_lbl.setText(txn.get("receipt", "\u2014"))

        note = txn.get("note", "")
        self.user_note_lbl.setText(note if note else "No note was left by the user.")
        self.note_card.setVisible(True)

        self.notes_edit.blockSignals(True)
        self.notes_edit.setPlainText(txn.get("admin_notes", ""))
        self.notes_edit.blockSignals(False)

        is_pending = txn["status"] == "Pending"
        self.approve_btn.setEnabled(is_pending)
        self.reject_btn.setEnabled(is_pending)
        self.approve_btn.setVisible(is_pending)
        self.reject_btn.setVisible(is_pending)

    def close_panel(self):
        self._current_txn = None
        self.content.hide()
        self.placeholder.show()
        self.close_requested.emit()

    # ------------------------------------------------------------------
    # Quick actions
    # ------------------------------------------------------------------
    def _approve(self):
        if self._current_txn is None:
            return
        confirmed = ConfirmDialog.ask(
            self, "Approve Payment",
            f'Approve {self._current_txn["ref"]} and add '
            f'{self._current_txn["credits"]:,} credits to {self._current_txn["name"]}\'s account?',
            confirm_text="Approve Payment", icon_name='fa5s.check', danger=False,
        )
        if not confirmed:
            return
        self._current_txn["status"] = "Completed"
        self.show_transaction(self._current_txn)
        self.transaction_changed.emit()

    def _reject(self):
        if self._current_txn is None:
            return
        confirmed = ConfirmDialog.ask(
            self, "Reject Payment",
            f'Reject {self._current_txn["ref"]}? No credits will be added to '
            f'{self._current_txn["name"]}\'s account.',
            confirm_text="Reject Payment", icon_name='fa5s.times', danger=True,
        )
        if not confirmed:
            return
        self._current_txn["status"] = "Rejected"
        self.show_transaction(self._current_txn)
        self.transaction_changed.emit()

    def _save_note(self):
        if self._current_txn is None:
            return
        self._current_txn["admin_notes"] = self.notes_edit.toPlainText()
        InfoDialog.show(
            self, "Note Saved", f'Your note about {self._current_txn["ref"]} was saved.',
            icon_name='fa5s.check-circle', success=True,
        )

    def _view_profile_not_wired_up(self):
        InfoDialog.show(
            self, "View Profile",
            "Jumping to the full user profile isn't wired up to a backend "
            "yet -- this is a prototype-only Transactions tab.",
            icon_name='fa5s.user', success=True,
        )

    def _view_receipt_not_wired_up(self):
        InfoDialog.show(
            self, "View Full Size",
            "Opening the full-size receipt image isn't wired up to a "
            "backend yet -- this is a prototype-only Transactions tab.",
            icon_name='fa5s.expand', success=True,
        )

    def _download_not_wired_up(self):
        InfoDialog.show(
            self, "Download Receipt",
            "Downloading the receipt file isn't wired up to a backend yet "
            "-- this is a prototype-only Transactions tab.",
            icon_name='fa5s.download', success=True,
        )


class TransactionsPage(QSplitter):
    """
    The "Transactions" nav tab (admin-only). Splitter of [table column,
    TransactionDetailPanel] -- same shape as users_page.UsersPage. Owns:
      - self.transactions -- in-memory list (data.TRANSACTIONS copy);
        Approve/Reject mutate these same dict objects, so the table and
        detail panel stay in sync without extra plumbing.
      - self.status_filter / self.method_filter / self.search_text --
        current pill-tab + dropdown + search box state, recombined by
        apply_filter() into self.current_data.
      - pagination state (page_size / current_page), same pattern as
        users_page.UsersPage.
    """
    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setHandleWidth(3)
        self.setChildrenCollapsible(False)

        self.transactions = [dict(t) for t in TRANSACTIONS]
        self.status_filter = "All"
        self.method_filter = "All Payment Methods"
        self.search_text = ""
        self.current_data = list(self.transactions)
        self.page_size = 10
        self.current_page = 1

        self.detail_panel = TransactionDetailPanel()
        self.detail_panel.transaction_changed.connect(self._on_transaction_changed)
        self.detail_panel.close_requested.connect(self._close_detail_panel)

        main = self._build_main()

        self.addWidget(main)
        self.addWidget(self.detail_panel)
        self.setStretchFactor(0, 1)
        self.setStretchFactor(1, 0)
        self.setSizes([1100, 340])
        self.detail_panel.hide()

        self.refresh_stats()
        self.apply_filter()

    def _open_detail_panel(self, txn: dict):
        self.detail_panel.show_transaction(txn)
        self.detail_panel.show()

    def _close_detail_panel(self):
        self.detail_panel.hide()

    def _on_transaction_changed(self):
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

        header_row = QHBoxLayout()
        header_col = QVBoxLayout()
        header_col.setSpacing(2)
        title_lbl = QLabel("Transactions")
        title_lbl.setObjectName("HeaderTitle")
        header_col.addWidget(title_lbl)
        sub_lbl = QLabel("Review and manage user payment transactions.")
        sub_lbl.setObjectName("HeaderSub")
        header_col.addWidget(sub_lbl)
        header_row.addLayout(header_col)
        header_row.addStretch()

        export_btn = QPushButton(qta.icon('fa5s.file-export', color=PALETTE['text_muted']), " Export CSV")
        export_btn.setObjectName("OutlineBtn")
        export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        export_btn.clicked.connect(self._export_not_wired_up)
        header_row.addWidget(export_btn)

        refresh_btn = QPushButton(qta.icon('fa5s.sync', color=PALETTE['text_muted']), " Refresh")
        refresh_btn.setObjectName("OutlineBtn")
        refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_btn.clicked.connect(self._refresh)
        header_row.addWidget(refresh_btn)

        outer.addLayout(header_row)
        outer.addWidget(self._build_table_column())

        scroll.setWidget(content)
        return scroll

    # ------------------------------------------------------------------
    # Stat cards (derived from self.transactions, so they stay correct
    # after an Approve/Reject)
    # ------------------------------------------------------------------
    def refresh_stats(self):
        while self.stats_row.count():
            item = self.stats_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        total = len(self.transactions)
        total_amount = sum(t["amount"] for t in self.transactions)
        total_credits = sum(t["credits"] for t in self.transactions)
        pending = [t for t in self.transactions if t["status"] == "Pending"]
        completed = [t for t in self.transactions if t["status"] == "Completed"]
        rejected = [t for t in self.transactions if t["status"] == "Rejected"]

        pending_pct = (len(pending) / total * 100) if total else 0
        completed_pct = (len(completed) / total * 100) if total else 0
        rejected_pct = (len(rejected) / total * 100) if total else 0

        cards = [
            ("fa5s.exchange-alt", config.rgba_from_hex(PALETTE['blue'], 0.15), PALETTE['blue'], "Total Transactions",
             f"{total:,}", "+18 vs last 7 days"),
            ("fa5s.wallet", config.rgba_from_hex(PALETTE['blue'], 0.15), PALETTE['blue'], "Total Amount",
             f"\u20b1{total_amount:,.2f}", "+24.5% vs last 7 days"),
            ("fa5s.coins", config.rgba_from_hex(PALETTE['purple'], 0.15), PALETTE['purple'], "Total Credits Sold",
             f"{total_credits:,}", "+21.3% vs last 7 days"),
            ("fa5s.hourglass-half", config.rgba_from_hex(PALETTE['yellow'], 0.15), PALETTE['yellow'], "Pending",
             f"{len(pending):,}", f"{pending_pct:.1f}% of total"),
            ("fa5s.check-circle", config.rgba_from_hex(PALETTE['green'], 0.15), PALETTE['green'], "Completed",
             f"{len(completed):,}", f"{completed_pct:.1f}% of total"),
            ("fa5s.times-circle", config.rgba_from_hex(PALETTE['red_solid'], 0.15), PALETTE['red'], "Rejected",
             f"{len(rejected):,}", f"{rejected_pct:.1f}% of total"),
        ]
        for icon, bg, color, label, value, delta in cards:
            self.stats_row.addWidget(StatCard(icon, bg, color, label, value, delta))

    def _export_not_wired_up(self):
        InfoDialog.show(
            self, "Export CSV",
            "Exporting the transaction list isn't wired up to a backend "
            "yet -- this is a prototype-only Transactions tab.",
            icon_name='fa5s.file-export', success=True,
        )

    def _refresh(self):
        self.apply_filter()
        self.refresh_stats()

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
        for name in ("All", "Pending", "Completed", "Rejected", "Refunded"):
            btn = QPushButton(name)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _checked=False, n=name: self.set_status_filter(n))
            self.tab_buttons[name] = btn
            tab_bar.addWidget(btn)
        tab_bar.addStretch()
        layout.addLayout(tab_bar)

        divider = QFrame()
        divider.setStyleSheet(f"background-color: {PALETTE['border']}; max-height: 1px; min-height: 1px;")
        layout.addWidget(divider)

        # --- Search + payment-method filter + Filters button row ---
        search_row = QHBoxLayout()
        search_row.setSpacing(8)

        self.search_edit = SearchLineEdit()
        self.search_edit.setPlaceholderText("Search by reference ID, user or email... (press Enter)")
        # Search only runs on explicit action -- Enter, or clicking the
        # magnifying-glass icon -- not on every keystroke.
        self.search_edit.search_triggered.connect(self._trigger_search)
        self.search_edit.returnPressed.connect(self._trigger_search)
        search_row.addWidget(self.search_edit, stretch=1)

        self.method_combo = QComboBox()
        self.method_combo.setObjectName("RowModeCombo")
        methods = ["All Payment Methods"] + sorted({t["method"] for t in self.transactions})
        self.method_combo.addItems(methods)
        self.method_combo.setMinimumWidth(160)
        self.method_combo.currentTextChanged.connect(self.on_method_changed)
        search_row.addWidget(self.method_combo)

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
            {"label": "Reference ID", "key": "ref"},
            {"label": "User", "key": "name"},
            {"label": "Payment Method", "key": "method"},
            {"label": "Package", "key": "credits"},
            {"label": "Amount", "key": "amount"},
            {"label": "Status", "key": "status"},
            {"label": "Submitted", "key": "submitted"},
        ]
        (self.COL_CHECK, self.COL_NUM, self.COL_REF, self.COL_USER, self.COL_METHOD,
         self.COL_PACKAGE, self.COL_AMOUNT, self.COL_STATUS, self.COL_SUBMITTED,
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
        self.table.setColumnWidth(self.COL_NUM, 34)
        for col_idx, width in [
            (self.COL_REF, 150), (self.COL_METHOD, 130), (self.COL_PACKAGE, 130),
            (self.COL_AMOUNT, 90), (self.COL_STATUS, 100), (self.COL_SUBMITTED, 150),
        ]:
            header.setSectionResizeMode(col_idx, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(col_idx, width)
        header.setSectionResizeMode(self.COL_USER, QHeaderView.ResizeMode.Stretch)
        header.setMinimumSectionSize(30)
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

        self.set_status_filter("All")
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
        """Runs the search: only called on Enter (returnPressed) or a
        click on the search-icon action -- not on every keystroke."""
        self.search_text = self.search_edit.text().strip().lower()
        self.current_page = 1
        self.apply_filter()

    def on_method_changed(self, method: str):
        self.method_filter = method
        self.current_page = 1
        self.apply_filter()

    def apply_filter(self):
        filtered = list(self.transactions)

        if self.status_filter != "All":
            filtered = [t for t in filtered if t["status"] == self.status_filter]

        if self.method_filter and self.method_filter != "All Payment Methods":
            filtered = [t for t in filtered if t["method"] == self.method_filter]

        if self.search_text:
            filtered = [
                t for t in filtered
                if self.search_text in t["ref"].lower()
                or self.search_text in t["name"].lower()
                or self.search_text in t["email"].lower()
            ]

        self.current_data = filtered
        self.populate_table()

    def _open_filters_not_wired_up(self):
        InfoDialog.show(
            self, "Filters",
            "Advanced transaction filters (date range, amount range, etc.) "
            "aren't wired up to a backend yet -- use the tabs, search box, "
            "and payment-method dropdown above for now.",
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
        for row, txn in enumerate(page_data):
            self.table.setItem(row, self.COL_CHECK, QTableWidgetItem())

            num_item = QTableWidgetItem(str(start + row + 1))
            num_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            num_item.setForeground(QColor(PALETTE['text_muted']))
            self.table.setItem(row, self.COL_NUM, num_item)

            ref_item = QTableWidgetItem(txn["ref"])
            ref_item.setForeground(QColor(PALETTE['text_primary']))
            self.table.setItem(row, self.COL_REF, ref_item)

            self.table.setItem(row, self.COL_USER, QTableWidgetItem())
            self.table.setCellWidget(row, self.COL_USER, _user_cell(txn))

            self.table.setItem(row, self.COL_METHOD, QTableWidgetItem(txn["method"]))

            self.table.setItem(row, self.COL_PACKAGE, QTableWidgetItem())
            self.table.setCellWidget(row, self.COL_PACKAGE, _package_cell(txn))

            amount_item = QTableWidgetItem(f'\u20b1{txn["amount"]:,.2f}')
            amount_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, self.COL_AMOUNT, amount_item)

            self.table.setItem(row, self.COL_STATUS, QTableWidgetItem())
            self.table.setCellWidget(row, self.COL_STATUS, _status_pill(txn["status"]))

            self.table.setItem(row, self.COL_SUBMITTED, QTableWidgetItem(txn["submitted"]))

        total_results = len(self.current_data)
        showing_from = start + 1 if page_data else 0
        showing_to = start + len(page_data)
        self.footer_label.setText(
            f"Showing {showing_from} to {showing_to} of {total_results} transactions"
        )

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