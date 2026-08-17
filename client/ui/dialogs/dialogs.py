"""
dialogs.py

Every QDialog subclass in the app:
  - ModernDialog        base class (frameless, rounded, draggable card)
  - ConfirmDialog / InfoDialog   themed replacements for QMessageBox
  - LoginDialog / SignupDialog / OTPDialog   auth screens
  - ViewProfileDialog / EditProfileDialog    session-only profile editing
  - FilterDialog        min rating / min reviews / status / category filters

Plus run_auth_flow(), the small state machine that loops between fresh
Login/Signup dialogs until the user logs in or gives up.

Depends on: config (colors, logo path), core.api_client + core.session
(Login/SignupDialog authenticate over HTTP against the backend,
including the pre-OTP email/referral-code existence checks and, as of
Phase 8, OTP generation/delivery/verification itself -- see
PROGRESS.md Phases 1, 5, and 8; no local data.db access, and no local
SMTP/OTP code, left in this file), widgets (icon_label, for
ViewProfileDialog's field rows).
"""

import time

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QLineEdit, QPushButton,
    QFrame, QDialog, QFormLayout, QComboBox, QSpinBox,
    QScrollArea, QProgressBar, QButtonGroup,
)
from PySide6.QtCore import Qt, Signal, QTimer, QSize, QThread
from PySide6.QtGui import QPixmap, QAction, QPainter, QPen, QColor, QIcon
import qtawesome as qta

from core.config import PALETTE, LOGO_PATH, REFERRAL_BONUS_CREDITS, REFERRAL_PROGRAM_ENABLED, AVATAR_CHOICES, DEFAULT_AVATAR
from core.api_client import login as api_login, signup as api_signup, ApiError
from core.session import set_session
from core.api_client import check_email_exists, check_referral_code_valid
from core.api_client import request_otp as api_request_otp, verify_otp_code as api_verify_otp_code
from ui.components.widgets import icon_label, build_referral_copy_field, circular_avatar_pixmap, LeadCheckBox


def labeled_field(label_text: str, widget: QWidget) -> QVBoxLayout:
    """Caption label stacked directly above its input, instead of the
    side-by-side label/input columns QFormLayout produces."""
    col = QVBoxLayout()
    col.setSpacing(4)
    lbl = QLabel(label_text)
    lbl.setStyleSheet(f"color: {PALETTE['text_muted']}; font-size: 11px;")
    col.addWidget(lbl)
    col.addWidget(widget)
    return col


def _avatar_btn_style(selected: bool) -> str:
    border_color = PALETTE["accent"] if selected else "transparent"
    return (
        f"QPushButton {{ border-radius: 24px; border: 2px solid {border_color}; "
        "background: transparent; padding: 0px; }"
        f"QPushButton:hover {{ border: 2px solid {PALETTE['accent_hover']}; }}"
    )


def build_avatar_picker(dialog: QWidget, initial_choice: str) -> QHBoxLayout:
    """Row of clickable avatar1-4.png thumbnails, shared by SignupDialog
    and EditProfileDialog so both pick from the same AVATAR_CHOICES list
    and render identically. Sets `dialog.selected_avatar` (starts at
    initial_choice) and `dialog._avatar_buttons`; picking a thumbnail
    updates both and restyles the selected border -- callers just read
    dialog.selected_avatar back out whenever they build their result."""
    dialog.selected_avatar = initial_choice
    dialog._avatar_buttons = {}

    def on_pick(choice: str):
        dialog.selected_avatar = choice
        for c, b in dialog._avatar_buttons.items():
            is_selected = c == choice
            b.setChecked(is_selected)
            b.setStyleSheet(_avatar_btn_style(is_selected))

    row = QHBoxLayout()
    row.setSpacing(10)
    for choice in AVATAR_CHOICES:
        btn = QPushButton()
        btn.setCheckable(True)
        btn.setFixedSize(48, 48)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setIcon(QIcon(circular_avatar_pixmap(choice, 44)))
        btn.setIconSize(QSize(44, 44))
        btn.setChecked(choice == initial_choice)
        btn.setStyleSheet(_avatar_btn_style(choice == initial_choice))
        btn.clicked.connect(lambda checked=False, c=choice: on_pick(c))
        dialog._avatar_buttons[choice] = btn
        row.addWidget(btn)
    row.addStretch()
    return row


class ModernDialog(QDialog):
    """
    Frameless modal base: no native OS title bar (no minimize/maximize/
    duplicate title), rounded card look, draggable by its header row,
    single top-right close (X) button -- matches modern web-app modals
    (Linear/Stripe/Notion style) instead of a nested desktop window.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # Frameless + translucent windows that get accept()/reject()-ed
        # (i.e. hidden, not destroyed) can leave a stale native window
        # behind on some Linux compositors -- it's invisible at the Qt
        # level but the compositor can redraw its last frame back into
        # view later when a *different* nearby window repaints (e.g. the
        # Signup card re-showing itself after an OTP cancel resurrecting
        # a Login screen that was "closed" moments earlier). Every dialog
        # here is already created fresh and thrown away per use (see
        # run_auth_flow), so there's no reason to keep the underlying
        # window around after close -- WA_DeleteOnClose makes Qt actually
        # tear it down instead of just hiding it.
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._drag_pos = None

        # The QDialog itself stays fully transparent -- combining
        # WA_TranslucentBackground with a border-radius stylesheet directly
        # on the QDialog is unreliable across platforms (corners render
        # square, or the fill doesn't paint at all). Instead, all visible
        # styling (rounded corners, border, shadow) lives on this inner
        # "card" QFrame, and the dialog is just an invisible wrapper around it.
        self.setStyleSheet("QDialog { background: transparent; }")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.card = QFrame(self)
        self.card.setObjectName("ModernDialogCard")
        self.card.setStyleSheet(
            f"QFrame#ModernDialogCard {{ background-color: {PALETTE['bg_dialog']}; "
            f"border: 1px solid {PALETTE['border']}; border-radius: 10px; }}"
        )
        outer.addWidget(self.card)

    def card_layout(self) -> QVBoxLayout:
        """Returns (creating if needed) the QVBoxLayout that subclasses
        should add their content to -- this lives on the rounded card
        frame, not directly on the transparent QDialog."""
        if self.card.layout() is None:
            QVBoxLayout(self.card)
        return self.card.layout()

    def add_header(self, layout, title_text):
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        title = QLabel(title_text)
        title.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 15px; font-weight: bold;")
        header_row.addWidget(title)
        header_row.addStretch()
        close_btn = QPushButton()
        close_btn.setIcon(qta.icon('fa5s.times', color=PALETTE["text_muted"]))
        close_btn.setFixedSize(22, 22)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; border-radius: 4px; }"
            f"QPushButton:hover {{ background-color: {PALETTE['border']}; }}"
        )
        close_btn.clicked.connect(self.reject)
        header_row.addWidget(close_btn)
        layout.addLayout(header_row)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_pos and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(event)

    def showEvent(self, event):
        """Frameless dialogs aren't auto-centered by the window manager the
        way framed/native dialogs are, so without this Qt just drops the
        window at its default top-left-ish position.

        Moving directly here (right after super().showEvent()) isn't
        actually reliable on Linux: WMs like GNOME/Mutter ignore
        move()/setGeometry() calls made before a window is fully mapped
        and apply their own initial-placement policy for the first show,
        so a move made "too early" in the show sequence gets silently
        overridden. Deferring the move to the next event-loop tick via
        QTimer.singleShot(0, ...) lets the WM finish mapping the window
        first, so our move() is no longer competing with it and actually
        sticks.
        """
        super().showEvent(event)
        if not getattr(self, "_centered_once", False):
            self._centered_once = True
            QTimer.singleShot(0, self._center_on_screen)

    def _center_on_screen(self):
        screen = self.screen()
        if screen is None:
            from PySide6.QtWidgets import QApplication
            screen = QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        self.move(
            geo.center().x() - self.width() // 2,
            geo.center().y() - self.height() // 2,
        )


class ConfirmDialog(ModernDialog):
    """
    Themed yes/no confirmation, styled like the rest of the app's modals
    instead of falling back to the OS-native QMessageBox look. Use
    ConfirmDialog.ask(...) and check the return value.
    """
    def __init__(self, title: str, message: str, confirm_text="Confirm",
                 icon_name='fa5s.exclamation-triangle', danger=True, parent=None):
        super().__init__(parent)
        self.setFixedWidth(320)

        layout = self.card_layout()
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)

        icon_row = QHBoxLayout()
        icon_row.addStretch()
        icon_lbl = QLabel()
        icon_color = PALETTE["red"] if danger else PALETTE["blue"]
        icon_lbl.setPixmap(qta.icon(icon_name, color=icon_color).pixmap(28, 28))
        icon_row.addWidget(icon_lbl)
        icon_row.addStretch()
        layout.addLayout(icon_row)
        layout.addSpacing(4)

        title_lbl = QLabel(title)
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_lbl.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 14px; font-weight: 600;")
        layout.addWidget(title_lbl)

        msg_lbl = QLabel(message)
        msg_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        msg_lbl.setWordWrap(True)
        msg_lbl.setStyleSheet(f"color: {PALETTE['text_muted']}; font-size: 11px;")
        layout.addWidget(msg_lbl)
        layout.addSpacing(6)

        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("OutlineBtn")
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)
        confirm_btn = QPushButton(qta.icon(icon_name, color="white"), " " + confirm_text)
        confirm_btn.setObjectName("RedBtn")
        confirm_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        confirm_btn.clicked.connect(self.accept)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(confirm_btn)
        layout.addLayout(btn_row)

    @staticmethod
    def ask(parent, title: str, message: str, confirm_text="Confirm",
            icon_name='fa5s.exclamation-triangle', danger=True) -> bool:
        dlg = ConfirmDialog(title, message, confirm_text, icon_name, danger, parent)
        return dlg.exec() == QDialog.DialogCode.Accepted


class PromptDialog(ModernDialog):
    """
    Themed single-line text-entry dialog, styled like the rest of the
    app's modals -- use in place of QInputDialog.getText() so renaming/
    naming prompts don't pop a native OS dialog on top of a custom card.
    """
    def __init__(self, title: str, label: str, initial_text: str = "",
                 confirm_text="Save", icon_name='fa5s.edit', parent=None):
        super().__init__(parent)
        self.setFixedWidth(320)

        layout = self.card_layout()
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)

        icon_row = QHBoxLayout()
        icon_row.addStretch()
        icon_lbl = QLabel()
        icon_lbl.setPixmap(qta.icon(icon_name, color=PALETTE["blue"]).pixmap(28, 28))
        icon_row.addWidget(icon_lbl)
        icon_row.addStretch()
        layout.addLayout(icon_row)
        layout.addSpacing(4)

        title_lbl = QLabel(title)
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_lbl.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 14px; font-weight: 600;")
        layout.addWidget(title_lbl)
        layout.addSpacing(2)

        layout.addLayout(labeled_field(label, self._build_input(initial_text)))
        layout.addSpacing(6)

        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("OutlineBtn")
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)
        confirm_btn = QPushButton(qta.icon('fa5s.check', color="white"), " " + confirm_text)
        confirm_btn.setObjectName("RedBtn")
        confirm_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        confirm_btn.clicked.connect(self.accept)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(confirm_btn)
        layout.addLayout(btn_row)

    def _build_input(self, initial_text: str) -> QLineEdit:
        self.input_edit = QLineEdit(initial_text)
        self.input_edit.selectAll()
        self.input_edit.returnPressed.connect(self.accept)
        return self.input_edit

    def accept(self):
        """Grab the text as a plain Python string the instant accept()
        fires, before calling through to QDialog.accept() -- ModernDialog
        sets WA_DeleteOnClose, so the C++ QLineEdit can be gone by the
        time exec() returns to get_text() below; reading it here first
        (this fires while input_edit is still very much alive) and
        stashing it as a plain attribute sidesteps that, since a plain
        Python attribute survives on the Python-side dialog object even
        after its underlying C++ widgets are torn down."""
        self._entered_text = self.input_edit.text()
        super().accept()

    def showEvent(self, event):
        """Deferred to the next event-loop tick for the same reason
        _center_on_screen() above is deferred: a frameless dialog isn't
        guaranteed to have real OS-level window focus the instant
        showEvent() fires, even though setFocus() happily marks the
        QLineEdit as Qt's focus widget -- on some Linux WMs that leaves
        the field *looking* focused (cursor blinking, visibly
        highlighted) while keypresses like Enter never actually reach
        the app until the user clicks inside the window first, which
        hands the WM real focus as a side effect. activateWindow()
        first, then focus once the WM's had a tick to finish mapping
        the window, makes Enter-to-save work on first try instead of
        only after a click.

        The try/except guards the rare case where the dialog was
        already accepted/rejected (and, per WA_DeleteOnClose, its
        widgets torn down) before this deferred tick got to run --
        without it, setFocus() on an already-deleted QLineEdit raises
        instead of harmlessly no-op'ing."""
        super().showEvent(event)
        self.activateWindow()
        QTimer.singleShot(0, self._focus_input)

    def _focus_input(self):
        try:
            self.input_edit.setFocus()
        except RuntimeError:
            pass

    @staticmethod
    def get_text(parent, title: str, label: str, initial_text: str = "",
                 confirm_text="Save", icon_name='fa5s.edit'):
        """Drop-in themed replacement for QInputDialog.getText -- returns
        (text, ok) just like the original so callers don't need to change
        their unpacking. Reads the text captured by accept() above
        instead of dlg.input_edit.text() -- that widget may already be
        deleted (WA_DeleteOnClose) by the time exec() returns here."""
        dlg = PromptDialog(title, label, initial_text, confirm_text, icon_name, parent)
        ok = dlg.exec() == QDialog.DialogCode.Accepted
        text = dlg._entered_text if ok else initial_text
        return text, ok


class InfoDialog(ModernDialog):
    """
    Themed single-button acknowledgement, styled like the rest of the app's
    modals -- use in place of QMessageBox.information()/warning() so success/
    error messages don't pop a native OS dialog on top of a custom card.
    """
    def __init__(self, title: str, message: str, icon_name='fa5s.check-circle',
                 success=True, parent=None):
        super().__init__(parent)
        self.setFixedWidth(320)

        layout = self.card_layout()
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)

        icon_row = QHBoxLayout()
        icon_row.addStretch()
        icon_lbl = QLabel()
        icon_color = PALETTE["green"] if success else PALETTE["red"]
        icon_lbl.setPixmap(qta.icon(icon_name, color=icon_color).pixmap(28, 28))
        icon_row.addWidget(icon_lbl)
        icon_row.addStretch()
        layout.addLayout(icon_row)
        layout.addSpacing(4)

        title_lbl = QLabel(title)
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_lbl.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 14px; font-weight: 600;")
        layout.addWidget(title_lbl)

        msg_lbl = QLabel(message)
        msg_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        msg_lbl.setWordWrap(True)
        msg_lbl.setStyleSheet(f"color: {PALETTE['text_muted']}; font-size: 11px;")
        layout.addWidget(msg_lbl)
        layout.addSpacing(6)

        ok_btn = QPushButton(qta.icon('fa5s.check', color="white"), " OK")
        ok_btn.setObjectName("RedBtn")
        ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ok_btn.clicked.connect(self.accept)
        layout.addWidget(ok_btn)

    @staticmethod
    def show(parent, title: str, message: str, icon_name='fa5s.check-circle', success=True):
        dlg = InfoDialog(title, message, icon_name, success, parent)
        dlg.exec()


class ActivityLogDialog(ModernDialog):
    """
    "View all logs" popup for the Search Leads Activity Log card. Shows
    every entry for the given search (not just the handful that fit in
    the inline card) in a scrollable list.
    """
    def __init__(self, search_title: str, entries: list, parent=None):
        super().__init__(parent)
        self.setFixedWidth(420)

        layout = self.card_layout()
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        self.add_header(layout, "Activity Log")

        subtitle = QLabel(search_title)
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(f"color: {PALETTE['text_muted']}; font-size: 11px;")
        layout.addWidget(subtitle)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFixedHeight(min(320, max(80, 44 * max(len(entries), 1))))
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        rows_container = QWidget()
        rows_container.setStyleSheet("background: transparent;")
        rows_layout = QVBoxLayout(rows_container)
        rows_layout.setContentsMargins(0, 0, 0, 0)
        rows_layout.setSpacing(4)

        if entries:
            for entry in entries:
                rows_layout.addWidget(self._row_widget(entry))
        else:
            empty_lbl = QLabel("No activity recorded for this search yet.")
            empty_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 11px;")
            empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            rows_layout.addWidget(empty_lbl)
        rows_layout.addStretch()

        scroll.setWidget(rows_container)
        layout.addWidget(scroll)

        close_btn = QPushButton("Close")
        close_btn.setObjectName("OutlineBtn")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

    @staticmethod
    def _row_widget(entry: dict) -> QWidget:
        row_wrap = QWidget()
        row_wrap.setStyleSheet(f"border-bottom: 1px solid {PALETTE['divider']}; background: transparent;")
        row = QVBoxLayout(row_wrap)
        row.setContentsMargins(0, 4, 0, 4)
        row.setSpacing(2)

        top_row = QHBoxLayout()
        top_row.setSpacing(6)
        icon_lbl = QLabel()
        icon_lbl.setStyleSheet("background: transparent; border: none;")
        icon_lbl.setPixmap(qta.icon('fa5s.check-circle', color=PALETTE['green']).pixmap(11, 11))
        top_row.addWidget(icon_lbl)
        text_lbl = QLabel(entry.get("text", ""))
        text_lbl.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 11px; background: transparent; border: none;")
        top_row.addWidget(text_lbl)
        top_row.addStretch()
        time_lbl = QLabel(entry.get("time", ""))
        time_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 10px; background: transparent; border: none;")
        top_row.addWidget(time_lbl)
        row.addLayout(top_row)

        meta_text = entry.get("meta", "")
        if meta_text:
            meta_lbl = QLabel(meta_text)
            meta_lbl.setWordWrap(True)
            meta_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 10px; background: transparent; border: none; margin-left: 17px;")
            row.addWidget(meta_lbl)

        return row_wrap

    @staticmethod
    def show_log(parent, search_title: str, entries: list):
        dlg = ActivityLogDialog(search_title, entries, parent)
        dlg.exec()


class GuideDialog(ModernDialog):
    """
    Themed scrollable help guide -- generic replacement for a single-line
    InfoDialog when there's actually enough content to walk through (e.g.
    the Exports tab's "View our guide" link). Takes a flat list of
    (heading, body) tuples and renders them as stacked sections inside a
    scroll area, same card chrome as ActivityLogDialog.
    """
    def __init__(self, title: str, sections: list, parent=None):
        super().__init__(parent)
        self.setFixedWidth(440)

        layout = self.card_layout()
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        self.add_header(layout, title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFixedHeight(420)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        body = QWidget()
        body.setStyleSheet("background: transparent;")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 4, 0)
        body_layout.setSpacing(14)

        for heading, text in sections:
            section = QVBoxLayout()
            section.setSpacing(4)
            heading_lbl = QLabel(heading)
            heading_lbl.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 12px; font-weight: 600;")
            section.addWidget(heading_lbl)
            body_lbl = QLabel(text)
            body_lbl.setWordWrap(True)
            body_lbl.setStyleSheet(f"color: {PALETTE['text_muted']}; font-size: 11px;")
            section.addWidget(body_lbl)
            body_layout.addLayout(section)
        body_layout.addStretch()

        scroll.setWidget(body)
        layout.addWidget(scroll)

        close_btn = QPushButton("Close")
        close_btn.setObjectName("OutlineBtn")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

    @staticmethod
    def show(parent, title: str, sections: list):
        dlg = GuideDialog(title, sections, parent)
        dlg.exec()


class _SpinnerWidget(QWidget):
    """Small rotating-arc "loading" indicator (self-painted, no external
    icon-font animation dependency) used by SearchProgressDialog."""

    def __init__(self, diameter: int = 26, parent=None):
        super().__init__(parent)
        self._diameter = diameter
        self._angle = 0
        self.setFixedSize(diameter, diameter)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)
        self._timer.start(45)

    def _advance(self):
        self._angle = (self._angle + 12) % 360
        self.update()

    def stop(self):
        self._timer.stop()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(PALETTE["red"]))
        pen.setWidth(3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        rect = self.rect().adjusted(2, 2, -2, -2)
        painter.drawArc(rect, -self._angle * 16, 100 * 16)
        painter.end()


class SearchProgressDialog(ModernDialog):
    """
    "Running search" progress modal -- 5-step tracker (Preparing ->
    Searching Google Maps -> Collecting Data -> Enriching Leads ->
    Completed) + a live status card (spinner, businesses-found/processed/
    elapsed-time counters, Stop Search, progress bar).

    Replaces the old bare QProgressDialog, which had no phase awareness
    and kept resizing itself to whatever the label text happened to be.

    Usage: build one, connect ScraperWorker's signals to
    update_query_progress / update_enrich_progress / add_found /
    mark_completed, connect stop_requested to worker.request_stop(), then
    setModal(True) + show() (non-blocking, same as the old dialog).
    """
    stop_requested = Signal()
    # Emitted every time the phase status changes (title, subtitle) -- the
    # page listens for this to mirror each transition into the Activity Log
    # card at the bottom of the page instead of showing it inside the popup.
    status_changed = Signal(str, str)

    STEP_NAMES = ["Preparing", "Searching Google Maps", "Collecting Data", "Enriching Leads", "Completed"]

    def __init__(self, enrichment_enabled: bool = False, parent=None):
        super().__init__(parent)
        self.setFixedWidth(640)
        self._enrichment_enabled = enrichment_enabled
        self._elapsed_seconds = 0
        self._found_count = 0
        self._phase = "preparing"

        layout = self.card_layout()
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(18)

        layout.addWidget(self._build_steps_row())
        layout.addWidget(self._build_status_card())

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

        for i in range(1, len(self.STEP_NAMES)):
            self._set_step_state(i, "pending")
        self._set_step_state(0, "active")

    # -- layout builders --------------------------------------------------

    def _build_steps_row(self) -> QWidget:
        row_widget = QWidget()
        row_widget.setStyleSheet("background: transparent;")
        row = QHBoxLayout(row_widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        self._step_circles = []
        self._step_labels = []
        self._step_lines = []

        for i, name in enumerate(self.STEP_NAMES):
            node = QWidget()
            node.setStyleSheet("background: transparent;")
            node_v = QVBoxLayout(node)
            node_v.setContentsMargins(0, 0, 0, 0)
            node_v.setSpacing(6)

            circle = QLabel()
            circle.setFixedSize(28, 28)
            circle.setAlignment(Qt.AlignmentFlag.AlignCenter)
            node_v.addWidget(circle, 0, Qt.AlignmentFlag.AlignHCenter)
            self._step_circles.append(circle)

            label = QLabel(name)
            label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            label.setWordWrap(True)
            label.setFixedWidth(110)
            node_v.addWidget(label)
            self._step_labels.append(label)

            row.addWidget(node)

            if i < len(self.STEP_NAMES) - 1:
                line_wrap = QWidget()
                line_wrap.setStyleSheet("background: transparent;")
                lw = QVBoxLayout(line_wrap)
                lw.setContentsMargins(0, 13, 0, 0)
                lw.setSpacing(0)
                line = QFrame()
                line.setFixedHeight(2)
                lw.addWidget(line)
                lw.addStretch()
                self._step_lines.append(line)
                row.addWidget(line_wrap, 1)

        return row_widget

    def _build_status_card(self) -> QFrame:
        card = QFrame()
        card.setStyleSheet(
            f"QFrame {{ background-color: {PALETTE['bg_surface']}; "
            f"border: none; border-radius: 10px; }}"
        )
        outer = QVBoxLayout(card)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(12)

        top_row = QHBoxLayout()
        top_row.setSpacing(14)

        self._spinner = _SpinnerWidget(26)
        top_row.addWidget(self._spinner)

        status_col = QVBoxLayout()
        status_col.setSpacing(2)
        self.status_title_lbl = QLabel("Preparing…")
        self.status_title_lbl.setStyleSheet(
            f"color: {PALETTE['text_primary']}; font-size: 12px; font-weight: 600; background: transparent;"
        )
        status_col.addWidget(self.status_title_lbl)
        top_row.addLayout(status_col, 1)
        top_row.setAlignment(status_col, Qt.AlignmentFlag.AlignVCenter)

        top_row.addWidget(self._stat_block("found_lbl", "0", "Businesses found", PALETTE["red"]))
        top_row.addWidget(self._stat_block("processed_lbl", "0", "Processed", PALETTE["text_primary"]))
        top_row.addWidget(self._stat_block("elapsed_lbl", "00:00", "Elapsed Time", PALETTE["text_primary"]))

        self.stop_btn = QPushButton(qta.icon('fa5s.stop', color=PALETTE["red"]), " Stop Search")
        self.stop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.stop_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: 1px solid {PALETTE['red']}; "
            f"border-radius: 6px; color: {PALETTE['red']}; padding: 6px 14px; "
            f"font-size: 11px; font-weight: 600; }}"
            f"QPushButton:hover {{ background-color: {PALETTE['red_soft']}; }}"
            f"QPushButton:disabled {{ color: {PALETTE['text_dim']}; border-color: {PALETTE['border_strong']}; }}"
        )
        self.stop_btn.clicked.connect(self._on_stop_clicked)
        top_row.addWidget(self.stop_btn)

        outer.addLayout(top_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setStyleSheet(
            f"QProgressBar {{ background-color: {PALETTE['border']}; border: none; border-radius: 3px; }}"
            f"QProgressBar::chunk {{ background-color: {PALETTE['red_solid']}; border-radius: 3px; }}"
        )
        outer.addWidget(self.progress_bar)

        pct_row = QHBoxLayout()
        pct_row.addStretch()
        self.pct_lbl = QLabel("0% complete")
        self.pct_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 10px; background: transparent;")
        pct_row.addWidget(self.pct_lbl)
        outer.addLayout(pct_row)

        return card

    def _stat_block(self, attr_name: str, initial_value: str, caption: str, color: str) -> QWidget:
        block = QWidget()
        block.setStyleSheet("background: transparent;")
        v = QVBoxLayout(block)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(1)
        value_lbl = QLabel(initial_value)
        value_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        value_lbl.setStyleSheet(f"color: {color}; font-size: 15px; font-weight: 700; background: transparent;")
        caption_lbl = QLabel(caption)
        caption_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        caption_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 9px; background: transparent;")
        v.addWidget(value_lbl)
        v.addWidget(caption_lbl)
        setattr(self, attr_name, value_lbl)
        return block

    # -- step-tracker state -------------------------------------------------

    def _set_step_state(self, index: int, state: str):
        """state is one of 'pending' / 'active' / 'done'."""
        circle = self._step_circles[index]
        label = self._step_labels[index]
        circle.clear()

        if state == "done":
            circle.setStyleSheet(
                f"background-color: {PALETTE['red_solid']}; border-radius: 14px; border: none;"
            )
            circle.setPixmap(qta.icon('fa5s.check', color="white").pixmap(12, 12))
            label.setStyleSheet(
                f"color: {PALETTE['text_primary']}; font-size: 11px; font-weight: 600; background: transparent;"
            )
        elif state == "active":
            circle.setText(str(index + 1))
            circle.setStyleSheet(
                f"background-color: {PALETTE['red_solid']}; border-radius: 14px; border: none; "
                f"color: white; font-weight: 700; font-size: 12px;"
            )
            label.setStyleSheet(
                f"color: {PALETTE['text_primary']}; font-size: 11px; font-weight: 700; background: transparent;"
            )
        else:  # pending
            circle.setText(str(index + 1))
            circle.setStyleSheet(
                f"background-color: transparent; border-radius: 14px; "
                f"border: 2px solid {PALETTE['border_strong']}; "
                f"color: {PALETTE['text_dim']}; font-weight: 600; font-size: 12px;"
            )
            label.setStyleSheet(
                f"color: {PALETTE['text_dim']}; font-size: 11px; font-weight: 500; background: transparent;"
            )

        if index < len(self._step_lines):
            line_color = PALETTE['red_solid'] if state == "done" else PALETTE['border_strong']
            self._step_lines[index].setStyleSheet(f"background-color: {line_color}; border: none;")

    # -- phase transitions (each idempotent -- safe to call repeatedly) ----

    def enter_searching(self):
        if self._phase != "preparing":
            return
        self._phase = "searching"
        self._set_step_state(0, "done")
        self._set_step_state(1, "active")
        self.set_status("Searching Google Maps…", "Opening tabs and loading results for each query.")

    def enter_collecting(self):
        if self._phase not in ("preparing", "searching"):
            return
        self.enter_searching()
        self._phase = "collecting"
        self._set_step_state(1, "done")
        self._set_step_state(2, "active")
        self.set_status("Collecting business details…", "This may take a few minutes.")

    def enter_enriching(self):
        if self._phase in ("enriching", "completed"):
            return
        if self._phase != "collecting":
            self.enter_collecting()
        self._phase = "enriching"
        self._set_step_state(2, "done")
        if self._enrichment_enabled:
            self._set_step_state(3, "active")
            self.set_status("Enriching leads…", "Looking up websites and emails.")
        else:
            self._set_step_state(3, "done")

    def mark_completed(self):
        self._phase = "completed"
        for i in range(len(self.STEP_NAMES)):
            self._set_step_state(i, "done")
        self._spinner.stop()
        self.set_status("Completed", "Search finished successfully.")
        self.set_progress(100)

    def mark_failed(self, message: str = ""):
        self._spinner.stop()
        self.set_status("Search failed", message or "Something went wrong -- check the details and try again.")

    # -- live data updates ---------------------------------------------------

    def add_found(self, count: int = 1):
        self._found_count += count
        self.found_lbl.setText(str(self._found_count))

    def set_processed(self, completed: int, total: int = None):
        self.processed_lbl.setText(str(completed) if total is None else f"{completed}/{total}")

    def set_status(self, title: str, subtitle: str = ""):
        # subtitle is kept in the method signature for backward compatibility
        # with existing call sites, but is no longer shown here -- that kind
        # of detail belongs in the Activity Log at the bottom of the page,
        # not duplicated inside this popup.
        self.status_title_lbl.setText(title)
        self.status_changed.emit(title, subtitle)

    def set_progress(self, percent: int):
        percent = max(0, min(100, int(percent)))
        self.progress_bar.setValue(percent)
        self.pct_lbl.setText(f"{percent}% complete")

    def update_query_progress(self, completed: int, total: int):
        self.enter_collecting()
        total = max(total, 1)
        self.set_processed(completed, total)
        span = 70 if self._enrichment_enabled else 100
        self.set_progress(completed / total * span)

    def update_enrich_progress(self, completed: int, total: int):
        self.enter_enriching()
        total = max(total, 1)
        self.set_processed(completed, total)
        self.set_progress(70 + (completed / total * 30))

    # -- misc -----------------------------------------------------------------

    def _on_stop_clicked(self):
        self.stop_btn.setEnabled(False)
        self.stop_btn.setText("  Stopping…")
        self.set_status("Stopping…", "Finishing the current step, then wrapping up.")
        self.stop_requested.emit()

    def showEvent(self, event):
        super().showEvent(event)
        if not self._timer.isActive():
            self._timer.start(1000)

    def _tick(self):
        self._elapsed_seconds += 1
        m, s = divmod(self._elapsed_seconds, 60)
        self.elapsed_lbl.setText(f"{m:02d}:{s:02d}")

    def closeEvent(self, event):
        self._timer.stop()
        self._spinner.stop()
        super().closeEvent(event)


class _RequestOtpThread(QThread):
    """Runs the POST /auth/request-otp call off the UI thread, so a slow
    server round-trip (which itself may be doing a slow SMTP send --
    see server/app/otp.py) doesn't freeze the dialog. Replaces the old
    _SendOtpEmailThread, which ran smtplib directly on this thread; now
    the client makes one HTTP call and the server does the rest,
    including owning the resend-cooldown length (see
    core.api_client.request_otp's docstring).
    """
    succeeded = Signal(int)   # resend_after_seconds
    failed = Signal(str)      # message safe to show directly

    def __init__(self, email: str, parent=None):
        super().__init__(parent)
        self.email = email

    def run(self):
        try:
            result = api_request_otp(self.email)
        except ApiError as exc:
            self.failed.emit(str(exc))
            return
        self.succeeded.emit(result.get("resend_after_seconds", 30))


class OTPDialog(ModernDialog):
    """
    Real OTP step shown during signup (see server/app/otp.py) -- a fresh
    6-digit code is generated and sent server-side the moment this
    dialog opens (POST /auth/request-otp), checked via
    POST /auth/verify-otp (single-use, expires, rate-limited on both
    wrong guesses and resends), instead of the old prototype's
    always-accept-'000000' stand-in. As of Phase 8 this dialog holds no
    OTP state itself -- no code, no store, no SMTP -- it's just two API
    calls; the server is the sole source of truth for whether a code is
    still pending, expired, or already used.
    """
    def __init__(self, email: str, parent=None):
        super().__init__(parent)
        self.setFixedWidth(320)
        self.verified = False
        self.email = email

        layout = self.card_layout()
        layout.setContentsMargins(24, 18, 24, 20)
        layout.setSpacing(10)
        self.add_header(layout, "Verify Your Email")

        info = QLabel(f"We've sent a 6-digit code to {email}.")
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {PALETTE['text_muted']}; font-size: 11px;")
        layout.addWidget(info)

        hint = QLabel(
            "Check your inbox. The code expires in a "
            "few minutes."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 10px;")
        layout.addWidget(hint)
        layout.addSpacing(6)

        self.otp_edit = QLineEdit()
        self.otp_edit.setPlaceholderText("Enter 6-digit code")
        self.otp_edit.setMaxLength(6)
        self.otp_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.otp_edit.returnPressed.connect(self._on_verify_clicked)
        layout.addWidget(self.otp_edit)

        self.error_lbl = QLabel("")
        self.error_lbl.setWordWrap(True)
        self.error_lbl.setStyleSheet(f"color: {PALETTE['red']}; font-size: 10px;")
        self.error_lbl.setVisible(False)
        layout.addWidget(self.error_lbl)

        resend_row = QHBoxLayout()
        resend_row.addStretch()
        self.resend_link = QPushButton("Resend code")
        self.resend_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.resend_link.setStyleSheet(
            "QPushButton { background: transparent; border: none; "
            f"color: {PALETTE['accent_text']}; font-size: 10px; font-weight: 600; }}"
            f"QPushButton:disabled {{ color: {PALETTE['text_dim']}; }}"
            f"QPushButton:hover:!disabled {{ color: {PALETTE['accent_text_hover']}; }}"
        )
        self.resend_link.clicked.connect(self._on_resend_clicked)
        resend_row.addWidget(self.resend_link)
        resend_row.addStretch()
        layout.addLayout(resend_row)

        layout.addSpacing(6)
        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("OutlineBtn")
        # Explicitly NOT the default/auto-default button -- without this,
        # Cancel (added first, left as Qt's default autoDefault=True) could
        # end up sharing the Enter-key "default button" role with Verify,
        # so a wrong code + Enter could fire reject() as well as/instead of
        # the verify handler, incorrectly bouncing back to Signup on a
        # simple incorrect-code entry instead of just showing the error.
        cancel_btn.setAutoDefault(False)
        cancel_btn.setDefault(False)
        cancel_btn.clicked.connect(self.reject)
        verify_btn = QPushButton(qta.icon('fa5s.check', color="white"), " Verify")
        verify_btn.setObjectName("RedBtn")
        # Verify is the one true default button -- Enter (whether via
        # otp_edit.returnPressed below or the dialog's own default-button
        # handling) should only ever trigger this.
        verify_btn.setAutoDefault(True)
        verify_btn.setDefault(True)
        verify_btn.clicked.connect(self._on_verify_clicked)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(verify_btn)
        layout.addLayout(btn_row)

        # Request the first code as soon as the dialog is constructed,
        # and start the resend-cooldown timer ticking right away.
        self._request_thread = None
        self._sending = False
        self._resend_after_seconds = 30  # placeholder until the server's first response confirms it
        self._cooldown_until = time.monotonic() + self._resend_after_seconds
        self._resend_timer = QTimer(self)
        self._resend_timer.timeout.connect(self._tick_resend_cooldown)
        self._resend_timer.start(1000)
        self._tick_resend_cooldown()
        self._start_request_thread()

    def _start_request_thread(self):
        """Kicks off POST /auth/request-otp on a background thread so
        the UI never blocks on it (that call may itself be waiting on a
        slow SMTP send server-side -- see server/app/otp.py), and shows
        a 'Sending...' state on the resend button in the meantime
        instead of looking frozen."""
        self._sending = True
        self.resend_link.setEnabled(False)
        self.resend_link.setText("Sending...")
        self._request_thread = _RequestOtpThread(self.email, self)
        self._request_thread.succeeded.connect(self._on_request_succeeded)
        self._request_thread.failed.connect(self._on_request_failed)
        self._request_thread.start()

    def _on_request_succeeded(self, resend_after_seconds: int):
        self._sending = False
        self._resend_after_seconds = resend_after_seconds
        self._cooldown_until = time.monotonic() + resend_after_seconds
        self._tick_resend_cooldown()

    def _on_request_failed(self, message: str):
        self._sending = False
        # A failed send (e.g. a 429 raced from double-clicking Resend, or
        # a connection problem) shouldn't leave Resend permanently
        # disabled -- show the error and let the existing cooldown (or
        # lack of one) govern whether Resend is immediately clickable.
        self.error_lbl.setText(message)
        self.error_lbl.setVisible(True)
        self.adjustSize()
        self._tick_resend_cooldown()

    def _tick_resend_cooldown(self):
        if self._sending:
            # Don't stomp on the "Sending..." label with a stale countdown
            # tick that fires while the background request is still in flight.
            return
        remaining = max(0, int(self._cooldown_until - time.monotonic() + 0.999))
        if remaining > 0:
            self.resend_link.setEnabled(False)
            self.resend_link.setText(f"Resend code ({remaining}s)")
        else:
            self.resend_link.setEnabled(True)
            self.resend_link.setText("Resend code")

    def _on_resend_clicked(self):
        if self._sending or time.monotonic() < self._cooldown_until:
            return  # button should already be disabled, but don't trust UI state alone
        self.otp_edit.clear()
        self.error_lbl.setVisible(False)
        self._start_request_thread()
        self.adjustSize()

    def _on_verify_clicked(self):
        code = self.otp_edit.text().strip()
        try:
            api_verify_otp_code(self.email, code)
        except ApiError as exc:
            self.error_lbl.setText(str(exc))
            self.error_lbl.setVisible(True)
            self.adjustSize()
            return

        self.verified = True
        self.accept()

    def closeEvent(self, event):
        self._resend_timer.stop()
        if self._request_thread is not None and self._request_thread.isRunning():
            self._request_thread.wait(3000)  # let the in-flight request finish rather than killing mid-send
        super().closeEvent(event)


class SignupDialog(ModernDialog):
    """
    Sign-up form. Creates a "user" role row in the `users` table via
    POST /auth/signup (core.api_client.signup) after a real OTP step
    handled entirely server-side (see server/app/otp.py) -- a fresh code
    is generated and emailed (or logged to the server's console if SMTP
    isn't configured there) each time the OTP dialog opens. Admin
    accounts can't be self-created here -- only the seeded
    admin@gmail.com has the admin role.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(340)
        self.created_email = None
        self.result = None

        layout = self.card_layout()
        layout.setContentsMargins(24, 18, 24, 20)
        layout.setSpacing(10)
        self.add_header(layout, "Create Account")

        layout.addSpacing(8)

        avatar_lbl = QLabel("Avatar")
        avatar_lbl.setWordWrap(True)
        avatar_lbl.setStyleSheet(f"color: {PALETTE['text_muted']}; font-size: 11px;")
        layout.addWidget(avatar_lbl)
        layout.addSpacing(4)
        layout.addLayout(build_avatar_picker(self, DEFAULT_AVATAR))
        layout.addSpacing(4)

        form = QVBoxLayout()
        form.setSpacing(12)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Juan Dela Cruz")
        form.addLayout(labeled_field("Full Name", self.name_edit))

        self.email_edit = QLineEdit()
        self.email_edit.setPlaceholderText("you@example.com")
        form.addLayout(labeled_field("Email", self.email_edit))

        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setPlaceholderText("At least 8 characters")
        form.addLayout(labeled_field("Password", self.password_edit))

        self.confirm_edit = QLineEdit()
        self.confirm_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm_edit.setPlaceholderText("At least 8 characters")
        form.addLayout(labeled_field("Confirm Password", self.confirm_edit))

        self.referral_edit = QLineEdit()
        self.referral_edit.setPlaceholderText("Optional")
        form.addLayout(labeled_field("Referral Code", self.referral_edit))

        layout.addLayout(form)

        referral_hint = QLabel("Referral rewards aren't active yet.")
        referral_hint.setWordWrap(True)
        referral_hint.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 10px;")
        layout.addWidget(referral_hint)

        self.error_lbl = QLabel("")
        self.error_lbl.setWordWrap(True)
        self.error_lbl.setStyleSheet(f"color: {PALETTE['red']}; font-size: 10px;")
        self.error_lbl.setVisible(False)
        layout.addWidget(self.error_lbl)

        layout.addSpacing(6)
        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("OutlineBtn")
        cancel_btn.setAutoDefault(False)
        cancel_btn.setDefault(False)
        cancel_btn.clicked.connect(self.reject)
        signup_btn = QPushButton(qta.icon('fa5s.user-plus', color="white"), " Sign Up")
        signup_btn.setObjectName("RedBtn")
        signup_btn.setAutoDefault(True)
        signup_btn.setDefault(True)
        signup_btn.clicked.connect(self._on_signup_clicked)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(signup_btn)
        layout.addLayout(btn_row)

        login_row = QHBoxLayout()
        login_row.addStretch()
        login_lbl = QLabel("Already have an account?")
        login_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 11px;")
        login_row.addWidget(login_lbl)
        login_link = QPushButton("Log In")
        login_link.setCursor(Qt.CursorShape.PointingHandCursor)
        login_link.setAutoDefault(False)
        login_link.setDefault(False)
        login_link.setStyleSheet(
            "QPushButton { background: transparent; border: none; "
            f"color: {PALETTE['accent_text']}; "
            "font-size: 11px; font-weight: 600; }"
            f"QPushButton:hover {{ color: {PALETTE['accent_text_hover']}; }}"
        )
        login_link.clicked.connect(self._on_login_link_clicked)
        login_row.addWidget(login_link)
        login_row.addStretch()
        layout.addLayout(login_row)

    def _show_error(self, text: str):
        self.error_lbl.setText(text)
        self.error_lbl.setVisible(True)
        self.adjustSize()

    def _on_login_link_clicked(self):
        # Explicit -- no account was created. Just close; the auth-flow
        # controller (run_auth_flow, below) creates a fresh LoginDialog on
        # its own.
        self.result = None
        self.created_email = None
        self.reject()

    def _on_signup_clicked(self):
        name = self.name_edit.text().strip()
        email = self.email_edit.text().strip().lower()
        password = self.password_edit.text()
        confirm = self.confirm_edit.text()

        if not name or not email or not password:
            self._show_error("Please fill in all fields.")
            return
        if "@" not in email or "." not in email.split("@")[-1]:
            self._show_error("Enter a valid email address.")
            return
        try:
            email_taken = check_email_exists(email)
        except ApiError as exc:
            self._show_error(str(exc))
            return
        if email_taken:
            self._show_error("An account with this email already exists.")
            return
        if len(password) < 8:
            self._show_error("Password must be at least 8 characters.")
            return
        if password != confirm:
            self._show_error("Passwords don't match.")
            return

        referral_code = self.referral_edit.text().strip()
        if referral_code:
            try:
                referral_valid = check_referral_code_valid(referral_code)
            except ApiError:
                referral_valid = True  # same "don't block on a pre-check failure" reasoning as above
            if not referral_valid:
                self._show_error("That referral code doesn't match any account.")
                return

        # All validation passed -- clear any stale error from a previous
        # failed attempt (e.g. "Passwords don't match") before opening the
        # OTP dialog, so it doesn't linger and get shown again below if
        # OTP fails.
        self.error_lbl.setVisible(False)

        # Hide this card while the OTP step is up -- only one card visible
        # at a time. IMPORTANT: this must NOT be a real hide()/show().
        # SignupDialog is itself running inside its own exec() (started by
        # run_auth_flow()), and QDialog.setVisible(False) -- which is what
        # hide() calls -- makes Qt exit that dialog's modal event loop
        # immediately (see QDialog::setVisible in the Qt source). That exit
        # doesn't fire mid-statement, but as soon as this handler returns
        # control to Qt -- which happens right after the user cancels the
        # OTP dialog, even though we call self.show() again below first.
        # By then dlg.exec() back in run_auth_flow() has already returned
        # (with the default Rejected result, since neither accept() nor
        # reject() was ever called on `self`), so run_auth_flow() falls
        # through to `screen = "login"` and spawns a brand-new, empty
        # LoginDialog on top -- discarding the signup fields even though
        # this card had just been re-shown. That was the "Cancel/X on OTP
        # sends me to Login instead of back to Signup" bug.
        #
        # setWindowOpacity() sidesteps this entirely: the card is fully
        # invisible (opacity 0) but Qt still considers it "visible", so
        # the dialog's own exec() loop is never disturbed, and it also
        # avoids the earlier Linux-compositor stale-paint issue that a
        # real hide()/show() could cause.
        from PySide6.QtWidgets import QApplication
        self.setWindowOpacity(0)
        QApplication.processEvents()

        otp_dlg = OTPDialog(email, self)
        otp_accepted = otp_dlg.exec() == QDialog.DialogCode.Accepted and otp_dlg.verified

        if not otp_accepted:
            # OTP cancelled/failed -- bring Create Account back so the user
            # can retry or edit their info, with everything they'd typed
            # still in the fields. Force an immediate full repaint (rather
            # than waiting for the next natural paint event) so nothing
            # stale from before shows through.
            self.setWindowOpacity(1)
            self.raise_()
            self.activateWindow()
            self.card.update()
            self.repaint()
            QApplication.processEvents()
            return

        # OTP succeeded -- stay invisible. Account Created will show next,
        # then self.accept() closes this card for good.

        try:
            auth = api_signup(
                email, password, name,
                referred_by_code=referral_code or None,
                avatar=self.selected_avatar,
            )
        except ApiError as exc:
            # Covers the email-uniqueness race the check_email_exists
            # pre-check above can't fully close (server returns 409 for
            # that -- see app/routers/auth.py), plus any other server-side
            # rejection or connection problem. Bring the card back so they
            # can retry or pick a different email.
            self.setWindowOpacity(1)
            self.raise_()
            self.activateWindow()
            self.card.update()
            self.repaint()
            QApplication.processEvents()
            self._show_error(str(exc))
            return

        user = auth["user"]

        # The server already recorded ACCOUNT_CREATED progress, resolved
        # the referral code, and credited both sides' pending_bonus_credits
        # in one transaction if it matched (see app/routers/auth.py) --
        # nothing left to do here except reflect that in the message.

        bonus_note = ""
        if REFERRAL_PROGRAM_ENABLED and user["referred_by_user_id"] is not None:
            bonus_note = " You and your referrer will both earn bonus credits once you complete your first search."

        set_session(auth["access_token"], user)
        self.created_email = email
        self.result = {"email": user["email"], "name": user["full_name"], "role": user["role"]}
        # Signing up logs them straight in ("You're now logged in" below) --
        # the server already counted this as day 1 of the Login Streak.
        InfoDialog.show(
            self, "Account Created",
            f"Welcome, {name}! You're now logged in.{bonus_note}",
        )
        self.accept()


class LoginDialog(ModernDialog):
    """
    Entry-point login screen. Validates via POST /auth/login
    (core.api_client.login) against the backend's `users` table --
    seeded user@gmail.com / admin@gmail.com plus anything created via
    Sign Up. On success, the JWT + full user dict are stashed in
    core/session.py and self.result holds {"email", "name", "role"}.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(340)
        self.result = None
        self.go_to_signup = False  # set True when "Sign Up" is clicked; run_auth_flow checks this

        layout = self.card_layout()
        layout.setContentsMargins(24, 16, 24, 22)
        layout.setSpacing(10)

        close_row = QHBoxLayout()
        close_row.setContentsMargins(0, 0, 0, 0)
        close_row.addStretch()
        close_btn = QPushButton()
        close_btn.setIcon(qta.icon('fa5s.times', color=PALETTE["text_muted"]))
        close_btn.setFixedSize(22, 22)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setAutoDefault(False)
        close_btn.setDefault(False)
        close_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; border-radius: 4px; }"
            f"QPushButton:hover {{ background-color: {PALETTE['border']}; }}"
        )
        close_btn.clicked.connect(self.reject)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)

        logo_lbl = QLabel()
        logo_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_pixmap = QPixmap(LOGO_PATH)
        if not logo_pixmap.isNull():
            logo_lbl.setPixmap(
                logo_pixmap.scaledToHeight(112, Qt.TransformationMode.SmoothTransformation)
            )
        else:
            logo_lbl.setPixmap(qta.icon('fa5s.map-marker-alt', color=PALETTE["red_solid"]).pixmap(40, 40))
        layout.addWidget(logo_lbl)

        layout.addSpacing(24)

        form = QVBoxLayout()
        form.setSpacing(12)

        self.email_edit = QLineEdit()
        self.email_edit.setPlaceholderText("you@example.com")
        form.addLayout(labeled_field("Email", self.email_edit))

        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setPlaceholderText("Password")
        self.password_edit.returnPressed.connect(self._on_login_clicked)
        form.addLayout(labeled_field("Password", self.password_edit))

        layout.addLayout(form)

        self.error_lbl = QLabel("")
        self.error_lbl.setWordWrap(True)
        self.error_lbl.setStyleSheet(f"color: {PALETTE['red']}; font-size: 10px;")
        self.error_lbl.setVisible(False)
        layout.addWidget(self.error_lbl)

        layout.addSpacing(4)
        login_btn = QPushButton(qta.icon('fa5s.sign-in-alt', color="white"), " Log In")
        login_btn.setObjectName("RedBtn")
        login_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        login_btn.setAutoDefault(True)
        login_btn.setDefault(True)
        login_btn.clicked.connect(self._on_login_clicked)
        layout.addWidget(login_btn)

        signup_row = QHBoxLayout()
        signup_row.addStretch()
        signup_lbl = QLabel("Don't have an account?")
        signup_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 11px;")
        signup_row.addWidget(signup_lbl)
        signup_link = QPushButton("Sign Up")
        signup_link.setCursor(Qt.CursorShape.PointingHandCursor)
        signup_link.setAutoDefault(False)
        signup_link.setDefault(False)
        signup_link.setStyleSheet(
            "QPushButton { background: transparent; border: none; "
            f"color: {PALETTE['accent_text']}; "
            "font-size: 11px; font-weight: 600; }"
            f"QPushButton:hover {{ color: {PALETTE['accent_text_hover']}; }}"
        )
        signup_link.clicked.connect(self._on_signup_clicked)
        signup_row.addWidget(signup_link)
        signup_row.addStretch()
        layout.addLayout(signup_row)

    def _show_error(self, text: str):
        self.error_lbl.setText(text)
        self.error_lbl.setVisible(True)
        self.adjustSize()

    def _on_login_clicked(self):
        email = self.email_edit.text().strip().lower()
        password = self.password_edit.text()

        if not email or not password:
            self._show_error("Please fill in all fields.")
            return

        try:
            auth = api_login(email, password)
        except ApiError as exc:
            # Covers wrong credentials, a suspended account (both come
            # back as a message-carrying error from the server -- see
            # app/routers/auth.py), and connection problems.
            self._show_error(str(exc))
            return

        user = auth["user"]

        # last_active_at and the Login Streak bump are both handled
        # server-side now, in the same request (see app/routers/auth.py).

        set_session(auth["access_token"], user)

        # Note: the DB column is `full_name`, not `name` -- ACCOUNTS used
        # "name" but the User row doesn't, so this can't just be a
        # find/replace of the dict key.
        self.result = {"email": user["email"], "name": user["full_name"], "role": user["role"]}
        self.accept()

    def _on_signup_clicked(self):
        # Just flag the intent and close -- run_auth_flow creates a
        # brand-new SignupDialog on its own. We never hide/reshow this
        # same instance; that's what was causing the "clicking Log In
        # doesn't bring anything back" bug.
        self.go_to_signup = True
        self.accept()


class ViewProfileDialog(ModernDialog):
    """
    Read-only profile summary. Dummy/prototype only -- pulls whatever is
    currently in the UserAccountBox's in-memory `profile` dict. Has an
    "Edit Profile" button that closes this dialog and opens the edit one.
    """
    edit_requested = Signal()

    def __init__(self, profile: dict, parent=None):
        super().__init__(parent)
        self.setFixedWidth(340)

        layout = self.card_layout()
        layout.setContentsMargins(24, 18, 20, 20)
        layout.setSpacing(4)
        self.add_header(layout, "Profile")
        layout.addSpacing(6)

        avatar = QLabel()
        avatar.setFixedSize(64, 64)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setStyleSheet("background: transparent;")
        avatar.setPixmap(circular_avatar_pixmap(profile.get("avatar"), 64))
        avatar_row = QHBoxLayout()
        avatar_row.addStretch()
        avatar_row.addWidget(avatar)
        avatar_row.addStretch()
        layout.addLayout(avatar_row)
        layout.addSpacing(12)

        name_lbl = QLabel(profile["name"])
        name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name_lbl.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 16px; font-weight: bold;")
        layout.addWidget(name_lbl)

        role_lbl = QLabel(profile.get("role", ""))
        role_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        role_lbl.setStyleSheet(f"color: {PALETTE['text_muted']}; font-size: 11px;")
        layout.addWidget(role_lbl)
        layout.addSpacing(14)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet(f"background-color: {PALETTE['divider']}; max-height: 1px; min-height: 1px;")
        layout.addWidget(divider)
        layout.addSpacing(10)

        credits_text = (
            "Unlimited credits" if profile.get("credits_unlimited")
            else f'{profile.get("credits", 0):,} credits remaining'
        )
        fields = [
            ('fa5s.envelope', profile.get("email", "")),
            ('fa5s.phone-alt', profile.get("phone", "")),
            ('fa5s.building', profile.get("company", "")),
            ('fa5s.calendar-alt', f'Joined {profile.get("joined", "")}'),
            ('fa5s.coins', credits_text),
        ]
        for icon_name, text in fields:
            layout.addWidget(icon_label(icon_name, text, color=PALETTE["text_muted"], size=12))

        referral_code = profile.get("referral_code", "")
        if referral_code:
            layout.addSpacing(10)
            divider2 = QFrame()
            divider2.setFrameShape(QFrame.Shape.HLine)
            divider2.setStyleSheet(f"background-color: {PALETTE['divider']}; max-height: 1px; min-height: 1px;")
            layout.addWidget(divider2)
            layout.addSpacing(10)

            referral_lbl = QLabel("Your Referral Code")
            referral_lbl.setStyleSheet(f"color: {PALETTE['text_muted']}; font-size: 11px;")
            layout.addWidget(referral_lbl)
            layout.addWidget(build_referral_copy_field(referral_code))

        layout.addSpacing(16)
        btn_row = QHBoxLayout()
        close_btn = QPushButton("Close")
        close_btn.setObjectName("OutlineBtn")
        close_btn.clicked.connect(self.accept)
        edit_btn = QPushButton(qta.icon('fa5s.user-edit', color="white"), " Edit Profile")
        edit_btn.setObjectName("RedBtn")
        edit_btn.clicked.connect(self._on_edit_clicked)
        btn_row.addWidget(close_btn)
        btn_row.addWidget(edit_btn)
        layout.addLayout(btn_row)

    def _on_edit_clicked(self):
        self.edit_requested.emit()
        self.accept()


class EditProfileDialog(ModernDialog):
    """
    Edit form. Stays a dumb widget on purpose -- everything typed here
    just gets handed back via get_values(); it's UserAccountBox
    (widgets.py, open_edit_profile) that calls api_client.update_me with
    those values, so this class itself never touches the server directly
    (keeps it usable/testable without a live session, e.g. the defensive
    fallback path if user_id is ever missing).
    """
    def __init__(self, profile: dict, parent=None):
        super().__init__(parent)
        self.setFixedWidth(360)
        # Populated by _on_save_clicked, right before accept() -- see that
        # method's comment for why this can't be deferred to get_values()
        # being called by the caller after exec() returns.
        self.values = None

        layout = self.card_layout()
        layout.setContentsMargins(24, 18, 24, 20)
        layout.setSpacing(8)
        self.add_header(layout, "Edit Profile")

        note = QLabel("Changes are saved to your account and will still "
                       "be here next launch.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 10px;")
        layout.addWidget(note)
        layout.addSpacing(10)

        avatar_lbl = QLabel("Avatar")
        avatar_lbl.setStyleSheet(f"color: {PALETTE['text_muted']}; font-size: 11px;")
        layout.addWidget(avatar_lbl)
        layout.addSpacing(4)

        # Falls back to DEFAULT_AVATAR the same way the sidebar/View
        # Profile do, so a legacy row with no avatar yet still opens on a
        # valid selection instead of nothing highlighted.
        layout.addLayout(build_avatar_picker(self, profile.get("avatar") or DEFAULT_AVATAR))
        layout.addSpacing(10)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        self.name_edit = QLineEdit(profile.get("name", ""))
        self.email_edit = QLineEdit(profile.get("email", ""))
        self.phone_edit = QLineEdit(profile.get("phone", ""))
        self.company_edit = QLineEdit(profile.get("company", ""))
        self.role_edit = QLineEdit(profile.get("role", ""))

        def field_label(text):
            lbl = QLabel(text)
            lbl.setStyleSheet(f"color: {PALETTE['text_muted']}; font-size: 11px;")
            return lbl

        form.addRow(field_label("Full Name"), self.name_edit)
        form.addRow(field_label("Email"), self.email_edit)
        form.addRow(field_label("Phone"), self.phone_edit)
        form.addRow(field_label("Company"), self.company_edit)
        form.addRow(field_label("Role"), self.role_edit)
        layout.addLayout(form)
        layout.addSpacing(10)

        pw_divider = QFrame()
        pw_divider.setFrameShape(QFrame.Shape.HLine)
        pw_divider.setStyleSheet(f"background-color: {PALETTE['divider']}; max-height: 1px; min-height: 1px;")
        layout.addWidget(pw_divider)
        layout.addSpacing(8)

        pw_title = QLabel("Change Password")
        pw_title.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 12px; font-weight: 600;")
        layout.addWidget(pw_title)

        pw_note = QLabel("Leave blank to keep your current password.")
        pw_note.setWordWrap(True)
        pw_note.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 10px;")
        layout.addWidget(pw_note)
        layout.addSpacing(6)

        pw_form = QFormLayout()
        pw_form.setSpacing(10)
        pw_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        self.current_password_edit = self._make_password_field()
        self.new_password_edit = self._make_password_field()
        self.confirm_password_edit = self._make_password_field()

        pw_form.addRow(field_label("Current Password"), self.current_password_edit)
        pw_form.addRow(field_label("New Password"), self.new_password_edit)
        pw_form.addRow(field_label("Confirm Password"), self.confirm_password_edit)
        layout.addLayout(pw_form)
        layout.addSpacing(12)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("OutlineBtn")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton(qta.icon('fa5s.check', color="white"), " Save Changes")
        save_btn.setObjectName("RedBtn")
        save_btn.setDefault(True)
        save_btn.setAutoDefault(True)
        save_btn.clicked.connect(self._on_save_clicked)

        for edit in (
            self.name_edit,
            self.email_edit,
            self.phone_edit,
            self.company_edit,
            self.role_edit,
            self.current_password_edit,
            self.new_password_edit,
            self.confirm_password_edit,
        ):
            edit.returnPressed.connect(self._on_save_clicked)

        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    def _make_password_field(self) -> QLineEdit:
        """Password QLineEdit with a clickable eye icon to toggle visibility."""
        edit = QLineEdit()
        edit.setEchoMode(QLineEdit.EchoMode.Password)
        edit.setPlaceholderText("\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022")
        toggle_action = edit.addAction(
            qta.icon('fa5s.eye', color=PALETTE["text_muted"]),
            QLineEdit.ActionPosition.TrailingPosition
        )
        toggle_action.setToolTip("Show password")
        toggle_action.triggered.connect(
            lambda checked=False, e=edit, a=toggle_action: self._toggle_password_visibility(e, a)
        )
        return edit

    @staticmethod
    def _toggle_password_visibility(edit: QLineEdit, action: QAction):
        if edit.echoMode() == QLineEdit.EchoMode.Password:
            edit.setEchoMode(QLineEdit.EchoMode.Normal)
            action.setIcon(qta.icon('fa5s.eye-slash', color=PALETTE["text_muted"]))
            action.setToolTip("Hide password")
        else:
            edit.setEchoMode(QLineEdit.EchoMode.Password)
            action.setIcon(qta.icon('fa5s.eye', color=PALETTE["text_muted"]))
            action.setToolTip("Show password")

    def _on_save_clicked(self):
        if not self.name_edit.text().strip() or not self.email_edit.text().strip():
            InfoDialog.show(self, "Missing info", "Name and email can't be empty.",
                             icon_name='fa5s.exclamation-triangle', success=False)
            return

        current_pw = self.current_password_edit.text()
        new_pw = self.new_password_edit.text()
        confirm_pw = self.confirm_password_edit.text()
        if current_pw or new_pw or confirm_pw:
            if not current_pw:
                InfoDialog.show(self, "Missing info", "Enter your current password to change it.",
                                 icon_name='fa5s.exclamation-triangle', success=False)
                return
            if len(new_pw) < 8:
                InfoDialog.show(self, "Weak password", "New password must be at least 8 characters.",
                                 icon_name='fa5s.exclamation-triangle', success=False)
                return
            if new_pw != confirm_pw:
                InfoDialog.show(self, "Password mismatch", "New password and confirmation don't match.",
                                 icon_name='fa5s.exclamation-triangle', success=False)
                return
            # This dialog still doesn't verify current_pw against the
            # real stored password -- see PROGRESS.md's "Not this file's
            # job" section (password hashing/verification is a separate,
            # deliberate phase). api_client.update_me() in widgets.py's
            # open_edit_profile persists new_pw regardless of whether
            # current_pw was actually correct; the server itself doesn't
            # re-check current_pw either (see UpdateMeRequest server-side).
            # Once that lands, check it here first, e.g.:
            #   if not api_client.verify_current_password(current_pw): ...

        # Read everything out of the widgets NOW, while the dialog is
        # still fully alive, and stash it as a plain dict on self.
        # ModernDialog sets WA_DeleteOnClose -- once accept() below runs,
        # Qt schedules this dialog's C++ object (and every child widget,
        # including name_edit et al.) for real deletion, which can land
        # before a caller waiting on dlg.exec() gets around to calling
        # get_values() or touching dlg.new_password_edit directly. That's
        # the "libshiboken: Internal C++ object ... already deleted"
        # crash -- reading widgets is only safe before accept(), never
        # after exec() returns. new_password is included here (rather
        # than the caller reaching into dlg.new_password_edit.text()
        # itself) for the same reason.
        values = {
            "name": self.name_edit.text().strip(),
            "email": self.email_edit.text().strip(),
            "phone": self.phone_edit.text().strip(),
            "company": self.company_edit.text().strip(),
            "role": self.role_edit.text().strip(),
            "avatar": self.selected_avatar,
        }
        if new_pw:
            values["password_changed"] = True
            values["new_password"] = new_pw
        self.values = values

        self.accept()

    def get_values(self) -> dict:
        """Returns the dict captured by _on_save_clicked just before
        accept() -- does NOT touch any widgets, since by the time a
        caller gets here (after dlg.exec() has returned) they may already
        be gone. Only meaningful when exec() returned Accepted."""
        return self.values


class FilterDialog(ModernDialog):
    """
    Threshold-style filters (min rating, min reviews, status, category) --
    complements the free-text search box, which only does substring
    matching. Values are read back via get_values() and applied in
    Dashboard.apply_filter().
    """
    def __init__(self, categories: list, current: dict, parent=None):
        super().__init__(parent)
        self.setFixedWidth(320)

        layout = self.card_layout()
        layout.setContentsMargins(24, 18, 24, 20)
        layout.setSpacing(10)
        self.add_header(layout, "Filters")

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        def field_label(text):
            lbl = QLabel(text)
            lbl.setStyleSheet(f"color: {PALETTE['text_muted']}; font-size: 11px;")
            return lbl

        # Every field in this panel (2 combos + the spinbox) shares one
        # explicit style so they render pixel-identical -- QComboBox was
        # previously left unstyled here and only inherited the app-wide
        # QWidget{background:bg_app} rule, which is a shade darker than
        # QLineEdit/bg_surface elsewhere; give all three the same surface,
        # border, radius and padding instead of relying on two different
        # implicit defaults sitting side by side.
        field_qss = (
            f"QComboBox, QSpinBox {{ background-color: {PALETTE['bg_surface']}; "
            f"border: 1px solid {PALETTE['border']}; border-radius: 6px; "
            f"padding: 0px 10px; min-height: 32px; max-height: 32px; "
            f"color: {PALETTE['text_secondary']}; font-size: 12px; }}"
            f"QComboBox:hover, QSpinBox:hover {{ border: 1px solid {PALETTE['text_dim']}; }}"
            f"QComboBox:focus, QSpinBox:focus {{ border: 1px solid {PALETTE['accent']}; }}"
            f"QComboBox::drop-down {{ border: none; width: 20px; }}"
            f"QComboBox QAbstractItemView {{ background-color: {PALETTE['bg_surface']}; "
            f"border: 1px solid {PALETTE['border']}; selection-background-color: {PALETTE['bg_hover']}; "
            f"color: {PALETTE['text_secondary']}; outline: none; padding: 0px; }}"
            f"QComboBox QAbstractItemView::item {{ padding: 0px; min-height: 22px; }}"
        )

        self.min_rating_spin = QComboBox()
        self.min_rating_spin.addItems(["Any", "3.0+", "3.5+", "4.0+", "4.5+"])
        self.min_rating_spin.setCurrentText(current.get("min_rating_label", "Any"))
        self.min_rating_spin.setStyleSheet(field_qss)
        form.addRow(field_label("Minimum rating"), self.min_rating_spin)

        self.min_reviews_spin = QSpinBox()
        self.min_reviews_spin.setRange(0, 100000)
        self.min_reviews_spin.setSingleStep(50)
        self.min_reviews_spin.setValue(current.get("min_reviews", 0))
        self.min_reviews_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.min_reviews_spin.setStyleSheet(field_qss)
        form.addRow(field_label("Minimum reviews"), self.min_reviews_spin)

        self.status_combo = QComboBox()
        self.status_combo.addItems(["Any", "Open", "Closed"])
        self.status_combo.setCurrentText(current.get("status", "Any"))
        self.status_combo.setStyleSheet(field_qss)
        form.addRow(field_label("Status"), self.status_combo)

        self.category_combo = QComboBox()
        self.category_combo.addItems(["Any"] + categories)
        self.category_combo.setCurrentText(current.get("category", "Any"))
        self.category_combo.setStyleSheet(field_qss)
        form.addRow(field_label("Category"), self.category_combo)

        layout.addLayout(form)
        layout.addSpacing(4)

        # Contact-info checkboxes -- independent, so checking both is how
        # you get "with phone AND email" (leaving one unchecked means
        # "don't care" about that field, not "must be missing").
        contact_lbl = field_label("Contact info")
        layout.addWidget(contact_lbl)

        contact_col = QVBoxLayout()
        contact_col.setSpacing(6)

        phone_row = QHBoxLayout()
        phone_row.setSpacing(8)
        self.has_phone_check = LeadCheckBox(current.get("has_phone", False))
        phone_row.addWidget(self.has_phone_check)
        phone_lbl = QLabel("Has phone number")
        phone_lbl.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 12px;")
        phone_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        phone_lbl.mousePressEvent = lambda _e: self.has_phone_check.toggle()
        phone_row.addWidget(phone_lbl)
        phone_row.addStretch()
        contact_col.addLayout(phone_row)

        email_row = QHBoxLayout()
        email_row.setSpacing(8)
        self.has_email_check = LeadCheckBox(current.get("has_email", False))
        email_row.addWidget(self.has_email_check)
        email_lbl = QLabel("Has email address")
        email_lbl.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 12px;")
        email_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        email_lbl.mousePressEvent = lambda _e: self.has_email_check.toggle()
        email_row.addWidget(email_lbl)
        email_row.addStretch()
        contact_col.addLayout(email_row)

        layout.addLayout(contact_col)
        layout.addSpacing(8)

        btn_row = QHBoxLayout()
        clear_btn = QPushButton("Clear")
        clear_btn.setObjectName("OutlineBtn")
        clear_btn.clicked.connect(self._on_clear_clicked)
        apply_btn = QPushButton(qta.icon('fa5s.filter', color="white"), " Apply Filters")
        apply_btn.setObjectName("RedBtn")
        apply_btn.clicked.connect(self.accept)
        btn_row.addWidget(clear_btn)
        btn_row.addWidget(apply_btn)
        layout.addLayout(btn_row)

    def _on_clear_clicked(self):
        self.min_rating_spin.setCurrentText("Any")
        self.min_reviews_spin.setValue(0)
        self.status_combo.setCurrentText("Any")
        self.category_combo.setCurrentText("Any")
        self.has_phone_check.setChecked(False)
        self.has_email_check.setChecked(False)
        self.accept()

    def accept(self):
        label = self.min_rating_spin.currentText()
        min_rating = 0.0 if label == "Any" else float(label.rstrip("+"))
        self._captured_values = {
            "min_rating_label": label,
            "min_rating": min_rating,
            "min_reviews": self.min_reviews_spin.value(),
            "status": self.status_combo.currentText(),
            "category": self.category_combo.currentText(),
            "has_phone": self.has_phone_check.isChecked(),
            "has_email": self.has_email_check.isChecked(),
        }
        super().accept()

    def get_values(self) -> dict:
        return self._captured_values


def run_auth_flow():
    """
    Loops between fresh Login/Signup dialogs until the user logs in
    (returns a result dict) or gives up entirely (returns None).

    Always creates a brand-new dialog instance for each screen -- never
    reuses/hides one across the Login<->Signup transition. Toggling
    visibility on a single frameless/translucent dialog instance while a
    child dialog is modal is what caused the earlier bug where clicking
    back to Login (or Log In from Signup) sometimes didn't bring anything
    back on screen.
    """
    screen = "login"
    while True:
        if screen == "login":
            dlg = LoginDialog()
            dlg.exec()
            if dlg.result:
                return dlg.result
            if dlg.go_to_signup:
                screen = "signup"
                continue
            return None  # closed Login directly (X button) -- quit

        else:  # screen == "signup"
            dlg = SignupDialog()
            dlg.exec()
            if dlg.result:
                return dlg.result  # signed up -- log straight in
            screen = "login"  # cancelled, closed, or clicked "Log In" -- back to a fresh Login