"""
billing.py

BillingPage: the "Billing" nav tab.

Four sub-tabs (own internal QStackedWidget, switched by the pill buttons
under the page header):
  - Buy Credits      -- pick a payment method, scan the QR / pay the shown
                        account, upload a proof-of-payment screenshot, pick
                        a credit package, submit for manual review.
  - Payment History  -- full list of past top-ups (mock data).
  - Invoices         -- one row per paid top-up, with a (prototype-only)
                        Download button.
  - Subscription     -- current plan summary, pulled from
                        the local _PLAN_PLACEHOLDER constant (billing
                        is still mock data, see below).

This is a manual "upload proof of payment" flow, not a real payment
gateway integration: the QR codes are decorative placeholders (see
QRCodeWidget), and Submit Payment just appends an in-memory "Pending" row
to self.transactions and shows a themed confirmation dialog. Swap
submit_payment() for a real API call, and QRCodeWidget for a real
generated code, once there's a backend.

Depends on: config, data, dialogs (InfoDialog).
"""

import os
import random

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QPushButton,
    QScrollArea, QTextEdit, QFileDialog, QStackedWidget,
)
from PySide6.QtCore import Qt, Signal, QRectF
from PySide6.QtGui import QPainter, QColor, QBrush, QPixmap, QPen, QPainterPath
import qtawesome as qta

from core import config
from core.config import PALETTE
from data import PAYMENT_METHODS, CREDIT_PACKAGES, BILLING_TRANSACTIONS, INVOICES
from ui.dialogs.dialogs import InfoDialog
from ui.components.widgets import status_badge, dash_card

# Billing is still mock data (out of scope for the users-table migration --
# see PROGRESS.md's "Not this file's job" section), so "plan" has no real
# column to read from yet. Was CURRENT_USER_EXTRA["plan"] before Phase 6
# deleted that dict; widgets.py keeps its own identical copy of this same
# placeholder until billing gets its own migration pass.
_PLAN_PLACEHOLDER = "Pro Plan"

_METHOD_COLOR = {m["label"]: m["color"] for m in PAYMENT_METHODS}
_METHOD_LETTER = {m["label"]: m["letter"] for m in PAYMENT_METHODS}
_METHOD_LOGO = {m["label"]: m.get("logo") for m in PAYMENT_METHODS}


def _card(title: str = None):
    """Thin wrapper over widgets.dash_card() (see PROGRESS.md, Phase 1c)."""
    return dash_card(title)


def _status_colors() -> dict:
    """Fresh dict on every call (not a module-level constant) so it always
    reflects the currently active PALETTE instead of freezing colors from
    whichever theme was active at import time."""
    return {
        "Completed": (PALETTE["green_solid"], PALETTE["green_soft"]),
        "Paid": (PALETTE["green_solid"], PALETTE["green_soft"]),
        "Pending": (PALETTE["yellow_solid"], PALETTE["yellow_soft"]),
        "Failed": (PALETTE["red_solid"], PALETTE["red_soft"]),
    }


def _status_pill(status: str) -> QWidget:
    """Same small-badge look as widgets.status_pill, but with the extra
    colors billing statuses need (Pending/Failed) instead of just Open/Closed.
    Thin wrapper over the shared widgets.status_badge (see PROGRESS.md,
    Phase 1a) -- kept as a local name so call sites below didn't need to
    change."""
    return status_badge(status, _status_colors(), align="right")


def _letter_pixmap(label: str, size: int) -> QPixmap:
    """Colored-square-with-initial fallback, used when a method has no
    logo file (or it failed to load)."""
    color = _METHOD_COLOR.get(label, PALETTE["text_muted"])
    letter = _METHOD_LETTER.get(label, "?")
    radius = max(4, size // 5)

    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(QColor(color)))
    painter.drawRoundedRect(0, 0, size, size, radius, radius)

    font = painter.font()
    font.setBold(True)
    font.setPointSize(max(8, size // 3))
    painter.setFont(font)
    painter.setPen(QPen(QColor("white")))
    painter.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, letter)
    painter.end()
    return pix


def _rounded_pixmap(pix: QPixmap, size: int, radius: int) -> QPixmap:
    """Clip a pixmap to a rounded-rect mask, matching _letter_pixmap's look."""
    rounded = QPixmap(size, size)
    rounded.fill(Qt.GlobalColor.transparent)
    painter = QPainter(rounded)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size, size), radius, radius)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, pix)
    painter.end()
    return rounded


def _method_pixmap(label: str, size: int = 26) -> QPixmap:
    """
    Real logo file (data.PAYMENT_METHODS[...]["logo"]) if it exists and
    loads, otherwise the colored-letter placeholder square. This is the
    single place avatars are built -- drop gcash.png / maya.png / paypal.png
    into leadscout/assets/ and they'll show up everywhere (payment method
    list, QR panel header, transaction rows) with no other code changes.
    """
    logo_path = _METHOD_LOGO.get(label)
    if logo_path and os.path.exists(logo_path):
        pix = QPixmap(logo_path)
        if not pix.isNull():
            scaled = pix.scaled(
                size, size, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            radius = max(4, size // 5)
            return _rounded_pixmap(scaled, size, radius)
    return _letter_pixmap(label, size)


def _avatar_label(label: str, size=26) -> QLabel:
    """Small square logo/initial for a payment method -- real logo image
    if one's been dropped in assets/, else a colored-letter placeholder."""
    avatar = QLabel()
    avatar.setFixedSize(size, size)
    avatar.setStyleSheet("background: transparent;")
    avatar.setPixmap(_method_pixmap(label, size))
    return avatar


def _transaction_row(tx: dict, compact: bool = True, is_last: bool = False) -> QWidget:
    """One row for either the compact Recent Transactions card (Buy Credits
    tab) or the fuller Payment History list -- same content, second column
    shows time (compact) or amount (full list). `is_last` drops the
    bottom-border divider so the final row in a list doesn't leave a
    trailing line floating above the card's own bottom padding."""
    wrap = QWidget()
    wrap.setObjectName("TxRow")
    border = "none" if is_last else f"1px solid {PALETTE['divider']}"
    wrap.setStyleSheet(f"#TxRow {{ background: transparent; border-bottom: {border}; }}")
    row = QHBoxLayout(wrap)
    row.setContentsMargins(0, 8, 0, 8)
    row.setSpacing(10)

    row.addWidget(_avatar_label(tx["method"]))

    text_col = QVBoxLayout()
    text_col.setSpacing(1)
    method_lbl = QLabel(tx["method"])
    method_lbl.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 11px; font-weight: 600; background: transparent;")
    text_col.addWidget(method_lbl)
    credits_lbl = QLabel(tx["credits"])
    credits_lbl.setStyleSheet(f"color: {PALETTE['green']}; font-size: 10px; background: transparent;")
    text_col.addWidget(credits_lbl)
    row.addLayout(text_col, stretch=1)

    row.addWidget(_status_pill(tx["status"]))

    date_col = QVBoxLayout()
    date_col.setSpacing(1)
    date_lbl = QLabel(tx["date"])
    date_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
    date_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 10px; background: transparent;")
    date_col.addWidget(date_lbl)
    second_lbl = QLabel(tx["time"] if compact else tx["amount"])
    second_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
    second_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 10px; background: transparent;")
    date_col.addWidget(second_lbl)
    row.addLayout(date_col)

    return wrap


class QRCodeWidget(QWidget):
    """
    Decorative pseudo-QR pattern -- NOT a real, scannable code. There's no
    payment gateway behind this prototype, so this just needs to *read* as
    a QR code visually; the pattern is deterministic per payment-method id
    (via `seed`) so switching methods gives a different-looking placeholder
    instead of the same static image. Swap for a real generated code (e.g.
    from a `qrcode` lib call against a real payment URL) once there's a
    backend to point it at.
    """
    def __init__(self, seed: str, parent=None):
        super().__init__(parent)
        self.setFixedSize(148, 148)
        grid = 21
        self.grid = grid
        rng = random.Random(seed)
        self.cells = [[rng.random() < 0.45 for _ in range(grid)] for _ in range(grid)]
        for r0, c0 in ((0, 0), (0, grid - 7), (grid - 7, 0)):
            self._stamp_finder(r0, c0)

    def _stamp_finder(self, r0, c0):
        for r in range(7):
            for c in range(7):
                on_border = r in (0, 6) or c in (0, 6)
                on_core = 2 <= r <= 4 and 2 <= c <= 4
                self.cells[r0 + r][c0 + c] = on_border or on_core

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor("white")))
        painter.drawRect(0, 0, self.width(), self.height())

        cell = self.width() / self.grid
        # Intentionally fixed, not PALETTE-driven: a QR code needs a
        # light background + dark modules to read as "QR code" at all --
        # swapping to a light-mode-appropriate dark background would make
        # the white foreground modules disappear. Same category as the
        # payment-brand colors in data.py: a real-world visual constraint,
        # not a theme color.
        painter.setBrush(QBrush(QColor("#111722")))
        for r in range(self.grid):
            for c in range(self.grid):
                if self.cells[r][c]:
                    painter.drawRect(QRectF(c * cell, r * cell, cell + 0.6, cell + 0.6))
        painter.end()


class PaymentMethodRow(QFrame):
    """One selectable row in the "Select Payment Method" list."""
    clicked = Signal(str)

    def __init__(self, method: dict, parent=None):
        super().__init__(parent)
        self.method_id = method["id"]
        self.setObjectName("PayMethodRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)
        layout.addWidget(_avatar_label(method["label"], size=28))

        label = QLabel(method["label"])
        label.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 12px; font-weight: 500; background: transparent;")
        layout.addWidget(label, stretch=1)

        self.radio_lbl = QLabel()
        self.radio_lbl.setStyleSheet("background: transparent;")
        layout.addWidget(self.radio_lbl)

        self.set_selected(False)

    def set_selected(self, selected: bool):
        if selected:
            self.setStyleSheet(
                f"#PayMethodRow {{ background-color: {config.rgba_from_hex(PALETTE['accent'], 0.10)}; "
                f"border: 1px solid {config.rgba_from_hex(PALETTE['accent'], 0.45)}; border-radius: 8px; }}"
            )
            self.radio_lbl.setPixmap(qta.icon('fa5s.dot-circle', color=PALETTE['red']).pixmap(15, 15))
        else:
            self.setStyleSheet(
                f"#PayMethodRow {{ background-color: {PALETTE['bg_surface']}; border: 1px solid {PALETTE['border']}; border-radius: 8px; }}"
                f"#PayMethodRow:hover {{ border: 1px solid {PALETTE['border_strong']}; }}"
            )
            self.radio_lbl.setPixmap(qta.icon('fa5.circle', color=PALETTE['text_muted']).pixmap(15, 15))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.method_id)
        super().mousePressEvent(event)


class PackageRow(QFrame):
    """One row in the "Credit Packages" list, with its own Select button."""
    selected = Signal(str)

    def __init__(self, pkg: dict, parent=None):
        super().__init__(parent)
        self.pkg_id = pkg["id"]
        self.setObjectName("PackageRow")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        if pkg.get("popular"):
            badge_row = QHBoxLayout()
            badge = QLabel("Most Popular")
            badge.setStyleSheet(
                f"color: {PALETTE['accent_text']}; background-color: {config.rgba_from_hex(PALETTE['accent'], 0.15)}; "
                f"border-radius: 3px; padding: 1px 6px; font-size: 9px; font-weight: 600;"
            )
            badge_row.addWidget(badge)
            badge_row.addStretch()
            layout.addLayout(badge_row)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        credits_col = QVBoxLayout()
        credits_col.setSpacing(2)
        credits_row = QHBoxLayout()
        credits_row.setSpacing(6)
        credits_lbl = QLabel(f"{pkg['credits']:,} Credits")
        credits_lbl.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 13px; font-weight: 600; background: transparent;")
        credits_row.addWidget(credits_lbl)
        if pkg.get("bonus"):
            bonus_lbl = QLabel(pkg["bonus"])
            bonus_lbl.setStyleSheet(
                f"color: {PALETTE['green']}; background-color: {PALETTE['green_soft']}; "
                f"border-radius: 3px; padding: 1px 6px; font-size: 9px; font-weight: 600;"
            )
            credits_row.addWidget(bonus_lbl)
        credits_row.addStretch()
        credits_col.addLayout(credits_row)
        top_row.addLayout(credits_col, stretch=1)

        price_lbl = QLabel(pkg["price"])
        price_lbl.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 13px; font-weight: 700; background: transparent;")
        top_row.addWidget(price_lbl, alignment=Qt.AlignmentFlag.AlignVCenter)

        self.select_btn = QPushButton("Select")
        self.select_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.select_btn.setFixedWidth(70)
        self.select_btn.clicked.connect(lambda: self.selected.emit(self.pkg_id))
        top_row.addWidget(self.select_btn)

        layout.addLayout(top_row)
        self.set_selected(bool(pkg.get("popular")))

    def set_selected(self, is_selected: bool):
        if is_selected:
            self.setStyleSheet(
                f"#PackageRow {{ background-color: {config.rgba_from_hex(PALETTE['accent'], 0.07)}; "
                f"border: 1px solid {config.rgba_from_hex(PALETTE['accent'], 0.4)}; border-radius: 8px; }}"
            )
            self.select_btn.setObjectName("RedBtn")
        else:
            self.setStyleSheet(
                f"#PackageRow {{ background-color: {PALETTE['bg_surface']}; border: 1px solid {PALETTE['border']}; border-radius: 8px; }}"
            )
            self.select_btn.setObjectName("OutlineBtn")
        # QSS #RedBtn/#OutlineBtn only takes effect after a style refresh
        # since we're changing objectName after the widget already exists.
        self.select_btn.style().unpolish(self.select_btn)
        self.select_btn.style().polish(self.select_btn)


class DropZoneWidget(QFrame):
    """Drag-and-drop (or click-to-browse) image picker for the payment
    proof screenshot. Purely local -- nothing is uploaded anywhere yet."""
    file_chosen = Signal(str)

    _IDLE_QSS = f"#DropZone {{ background: transparent; border: 1px dashed {PALETTE['border_strong']}; border-radius: 8px; }}"
    _HOVER_QSS = f"#DropZone {{ background: {config.rgba_from_hex(PALETTE['accent'], 0.05)}; border: 1px dashed {PALETTE['accent']}; border-radius: 8px; }}"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self.setMinimumHeight(150)
        self.file_path = None

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(6)

        self.icon_lbl = QLabel()
        self.icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_lbl.setStyleSheet("background: transparent;")
        self.icon_lbl.setPixmap(qta.icon('fa5s.cloud-upload-alt', color=PALETTE['red']).pixmap(30, 30))
        layout.addWidget(self.icon_lbl)

        self.main_lbl = QLabel("Drag & drop your image here")
        self.main_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.main_lbl.setWordWrap(True)
        self.main_lbl.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 12px; background: transparent;")
        layout.addWidget(self.main_lbl)

        self.or_lbl = QLabel("or")
        self.or_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.or_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 11px; background: transparent;")
        layout.addWidget(self.or_lbl)

        self.browse_btn = QPushButton("Choose Image")
        self.browse_btn.setObjectName("OutlineBtn")
        self.browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.browse_btn.clicked.connect(self.browse)
        layout.addWidget(self.browse_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        self.setStyleSheet(self._IDLE_QSS)

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose payment screenshot", "", "Images (*.png *.jpg *.jpeg)"
        )
        if path:
            self._set_file(path)

    def reset(self):
        self.file_path = None
        self.icon_lbl.setPixmap(qta.icon('fa5s.cloud-upload-alt', color=PALETTE['red']).pixmap(30, 30))
        self.main_lbl.setText("Drag & drop your image here")
        self.or_lbl.setVisible(True)
        self.browse_btn.setText("Choose Image")

    def _set_file(self, path: str):
        self.file_path = path
        self.icon_lbl.setPixmap(qta.icon('fa5s.check-circle', color=PALETTE['green']).pixmap(30, 30))
        self.main_lbl.setText(os.path.basename(path))
        self.or_lbl.setVisible(False)
        self.browse_btn.setText("Choose a Different Image")
        self.file_chosen.emit(path)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setStyleSheet(self._HOVER_QSS)

    def dragLeaveEvent(self, event):
        self.setStyleSheet(self._IDLE_QSS)

    def dropEvent(self, event):
        self.setStyleSheet(self._IDLE_QSS)
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if path.lower().endswith((".png", ".jpg", ".jpeg")):
                self._set_file(path)


class BillingPage(QScrollArea):
    """
    The "Billing" nav tab. Owns:
      - self.transactions -- in-memory list (data.BILLING_TRANSACTIONS
        copy), shown on both Buy Credits (short) and Payment History (full).
      - self.selected_method_id / self.selected_package_id -- current
        picks on the Buy Credits tab.
      - self.dropzone.file_path -- path of the chosen proof-of-payment
        image, if any (optional -- Submit Payment doesn't require it here,
        matching the screenshot's "optional" note field, though a real
        backend would likely require the screenshot itself).
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DashScroll")
        self.setWidgetResizable(True)

        self.transactions = list(BILLING_TRANSACTIONS)
        self.selected_method_id = PAYMENT_METHODS[0]["id"]
        self.selected_package_id = next(
            (p["id"] for p in CREDIT_PACKAGES if p.get("popular")), CREDIT_PACKAGES[0]["id"]
        )

        content = QWidget()
        outer = QVBoxLayout(content)
        outer.setContentsMargins(20, 16, 20, 20)
        outer.setSpacing(14)

        # --- Header ---
        header_col = QVBoxLayout()
        header_col.setSpacing(2)
        title_lbl = QLabel("Billing")
        title_lbl.setObjectName("HeaderTitle")
        header_col.addWidget(title_lbl)
        self.breadcrumb_lbl = QLabel()
        self.breadcrumb_lbl.setObjectName("HeaderSub")
        header_col.addWidget(self.breadcrumb_lbl)
        outer.addLayout(header_col)

        # --- Sub-tab bar ---
        # tab_bar + divider live in their own zero-spacing column so the
        # active tab's red border-bottom sits flush on top of the divider
        # line instead of floating ~14px above it (that gap was making the
        # underline look like a stray floating dash instead of a clean
        # tab-underline). outer's normal 14px spacing still applies above
        # and below this combined block.
        tabbar_col = QVBoxLayout()
        tabbar_col.setSpacing(0)

        tab_bar = QHBoxLayout()
        tab_bar.setSpacing(4)
        self.subtab_buttons = {}
        subtabs = [
            ("fa5s.wallet", "Buy Credits"),
            ("fa5s.history", "Payment History"),
            ("fa5s.file-invoice", "Invoices"),
            ("fa5s.sync", "Subscription"),
        ]
        for icon_name, name in subtabs:
            btn = QPushButton(qta.icon(icon_name, color=PALETTE['text_muted']), " " + name)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _checked=False, n=name: self.set_active_subtab(n))
            self.subtab_buttons[name] = btn
            tab_bar.addWidget(btn)
        tab_bar.addStretch()
        tabbar_col.addLayout(tab_bar)

        divider = QFrame()
        divider.setStyleSheet(f"background-color: {PALETTE['divider']}; max-height: 1px; min-height: 1px;")
        tabbar_col.addWidget(divider)

        outer.addLayout(tabbar_col)

        # --- Sub-tab content stack ---
        self.stack = QStackedWidget()
        self.subtab_pages = {
            "Buy Credits": self._build_buy_credits_tab(),
            "Payment History": self._build_history_tab(),
            "Invoices": self._build_invoices_tab(),
            "Subscription": self._build_subscription_tab(),
        }
        for page in self.subtab_pages.values():
            self.stack.addWidget(page)
        outer.addWidget(self.stack)

        self.setWidget(content)
        self.set_active_subtab("Buy Credits")

    # ------------------------------------------------------------------
    # Sub-tab switching
    # ------------------------------------------------------------------
    def set_active_subtab(self, name: str):
        self.stack.setCurrentWidget(self.subtab_pages[name])
        self.breadcrumb_lbl.setText(f"Home  \u2022  Billing  \u2022  {name}")
        for btn_name, btn in self.subtab_buttons.items():
            is_active = btn_name == name
            btn.setObjectName("BillingTabActive" if is_active else "BillingTabItem")
            icon_name = {
                "Buy Credits": "fa5s.wallet", "Payment History": "fa5s.history",
                "Invoices": "fa5s.file-invoice", "Subscription": "fa5s.sync",
            }[btn_name]
            btn.setIcon(qta.icon(icon_name, color=PALETTE['red'] if is_active else PALETTE['text_muted']))
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    # ------------------------------------------------------------------
    # Buy Credits tab
    # ------------------------------------------------------------------
    def _build_buy_credits_tab(self) -> QWidget:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 12, 0, 0)
        page_layout.setSpacing(14)

        columns = QHBoxLayout()
        columns.setSpacing(14)
        columns.addWidget(self._build_payment_method_column(), stretch=3)
        columns.addWidget(self._build_upload_column(), stretch=3)
        columns.addWidget(self._build_packages_column(), stretch=3)
        page_layout.addLayout(columns)

        secure_card, secure_layout = _card()
        secure_row = QHBoxLayout()
        secure_row.setSpacing(10)
        shield_lbl = QLabel()
        shield_lbl.setStyleSheet("background: transparent;")
        shield_lbl.setPixmap(qta.icon('fa5s.shield-alt', color=PALETTE['text_muted']).pixmap(18, 18))
        secure_row.addWidget(shield_lbl)
        secure_text_col = QVBoxLayout()
        secure_text_col.setSpacing(1)
        secure_title = QLabel("Secure Payments")
        secure_title.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 12px; font-weight: 600; background: transparent;")
        secure_text_col.addWidget(secure_title)
        secure_sub = QLabel("Your payment information is protected with industry-standard encryption and security measures.")
        secure_sub.setStyleSheet(f"color: {PALETTE['text_muted']}; font-size: 10px; background: transparent;")
        secure_text_col.addWidget(secure_sub)
        secure_row.addLayout(secure_text_col, stretch=1)
        secure_layout.addLayout(secure_row)
        page_layout.addWidget(secure_card)

        return page

    def _build_payment_method_column(self) -> QWidget:
        col = QWidget()
        col.setObjectName("BuyCreditsCol")
        col.setStyleSheet("#BuyCreditsCol { background: transparent; }")
        layout = QVBoxLayout(col)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        method_card, method_layout = _card("1. Select Payment Method")
        sub_lbl = QLabel("Choose your preferred payment option")
        sub_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 10px; background: transparent;")
        method_layout.addWidget(sub_lbl)

        self.method_rows = {}
        for method in PAYMENT_METHODS:
            row = PaymentMethodRow(method)
            row.clicked.connect(self.on_method_selected)
            self.method_rows[method["id"]] = row
            method_layout.addWidget(row)
        layout.addWidget(method_card)

        # QR / account details panel -- rebuilt (well, relabeled) whenever
        # the selected payment method changes.
        self.qr_card, qr_layout = _card()
        qr_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        qr_layout.setSpacing(4)

        qr_header = QHBoxLayout()
        qr_header.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        qr_header.setSpacing(8)
        self.qr_avatar_lbl = QLabel()
        qr_header.addWidget(self.qr_avatar_lbl)
        self.qr_method_lbl = QLabel()
        self.qr_method_lbl.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 13px; font-weight: 600; background: transparent;")
        qr_header.addWidget(self.qr_method_lbl)
        qr_layout.addLayout(qr_header)

        scan_lbl = QLabel("SCAN TO PAY")
        scan_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scan_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 10px; font-weight: 600; letter-spacing: 1px; background: transparent;")
        qr_layout.addWidget(scan_lbl)
        qr_layout.addSpacing(4)

        self.qr_slot = QVBoxLayout()
        self.qr_slot.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        qr_layout.addLayout(self.qr_slot)
        qr_layout.addSpacing(6)

        self.account_name_title = QLabel("Account Name")
        self.account_name_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.account_name_title.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 9px; background: transparent;")
        qr_layout.addWidget(self.account_name_title)
        self.account_name_lbl = QLabel()
        self.account_name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.account_name_lbl.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 12px; font-weight: 600; background: transparent;")
        qr_layout.addWidget(self.account_name_lbl)
        qr_layout.addSpacing(4)

        self.account_label_title = QLabel()
        self.account_label_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.account_label_title.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 9px; background: transparent;")
        qr_layout.addWidget(self.account_label_title)
        self.account_value_lbl = QLabel()
        self.account_value_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.account_value_lbl.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 12px; font-weight: 600; background: transparent;")
        qr_layout.addWidget(self.account_value_lbl)
        qr_layout.addSpacing(8)

        note_row = QHBoxLayout()
        note_frame = QFrame()
        note_frame.setObjectName("NoteFrame")
        note_frame.setStyleSheet(f"#NoteFrame {{ background-color: {PALETTE['bg_app']}; border: 1px solid {PALETTE['border']}; border-radius: 6px; }}")
        note_inner = QHBoxLayout(note_frame)
        note_inner.setContentsMargins(8, 6, 8, 6)
        note_inner.setSpacing(6)
        info_icon = QLabel()
        info_icon.setStyleSheet("background: transparent;")
        info_icon.setPixmap(qta.icon('fa5s.info-circle', color=PALETTE['text_muted']).pixmap(11, 11))
        note_inner.addWidget(info_icon, alignment=Qt.AlignmentFlag.AlignTop)
        note_text = QLabel("Please make sure to send the exact amount.")
        note_text.setWordWrap(True)
        note_text.setStyleSheet(f"color: {PALETTE['text_muted']}; font-size: 10px; background: transparent;")
        note_inner.addWidget(note_text, stretch=1)
        note_row.addWidget(note_frame)
        qr_layout.addLayout(note_row)

        layout.addWidget(self.qr_card)
        layout.addStretch()

        self._refresh_qr_panel()
        self.method_rows[self.selected_method_id].set_selected(True)

        return col

    def on_method_selected(self, method_id: str):
        self.selected_method_id = method_id
        for mid, row in self.method_rows.items():
            row.set_selected(mid == method_id)
        self._refresh_qr_panel()

    def _refresh_qr_panel(self):
        method = next(m for m in PAYMENT_METHODS if m["id"] == self.selected_method_id)
        self.qr_avatar_lbl.setPixmap(_method_pixmap(method["label"], 30))
        self.qr_method_lbl.setText(method["label"])
        self.account_name_lbl.setText(method["account_name"])
        self.account_label_title.setText(method["account_label"])
        self.account_value_lbl.setText(method["account_value"])

        while self.qr_slot.count():
            item = self.qr_slot.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.qr_slot.addWidget(QRCodeWidget(self.selected_method_id))

    def _build_upload_column(self) -> QWidget:
        col = QWidget()
        col.setObjectName("BuyCreditsCol")
        col.setStyleSheet("#BuyCreditsCol { background: transparent; }")
        layout = QVBoxLayout(col)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        upload_card, upload_layout = _card("2. Upload Proof of Payment")
        sub_lbl = QLabel("Upload your payment receipt or screenshot")
        sub_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 10px; background: transparent;")
        upload_layout.addWidget(sub_lbl)

        self.dropzone = DropZoneWidget()
        upload_layout.addWidget(self.dropzone)

        formats_lbl = QLabel("Supported formats: JPG, PNG, JPEG\nMaximum size: 5MB")
        formats_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        formats_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 10px; background: transparent;")
        upload_layout.addWidget(formats_lbl)

        upload_layout.addSpacing(4)
        details_title = QLabel("3. Payment Details")
        details_title.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 12px; font-weight: 600; background: transparent;")
        upload_layout.addWidget(details_title)
        details_sub = QLabel("Add a note for your payment (optional)")
        details_sub.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 10px; background: transparent;")
        upload_layout.addWidget(details_sub)

        self.note_edit = QTextEdit()
        self.note_edit.setObjectName("NoteEdit")
        self.note_edit.setPlaceholderText("Enter your note here...")
        self.note_edit.setFixedHeight(70)
        self.note_edit.textChanged.connect(self._on_note_changed)
        upload_layout.addWidget(self.note_edit)

        self.char_count_lbl = QLabel("0 / 250")
        self.char_count_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.char_count_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 9px; background: transparent;")
        upload_layout.addWidget(self.char_count_lbl)

        self.submit_btn = QPushButton(qta.icon('fa5s.paper-plane', color="white"), " Submit Payment")
        self.submit_btn.setObjectName("RedBtn")
        self.submit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.submit_btn.setMinimumHeight(34)
        self.submit_btn.clicked.connect(self.submit_payment)
        upload_layout.addWidget(self.submit_btn)

        lock_row = QHBoxLayout()
        lock_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lock_row.setSpacing(5)
        lock_icon = QLabel()
        lock_icon.setStyleSheet("background: transparent;")
        lock_icon.setPixmap(qta.icon('fa5s.lock', color=PALETTE['text_muted']).pixmap(10, 10))
        lock_row.addWidget(lock_icon)
        lock_text = QLabel("Your payment information is secure and encrypted.")
        lock_text.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 9px; background: transparent;")
        lock_row.addWidget(lock_text)
        upload_layout.addLayout(lock_row)

        layout.addWidget(upload_card)
        layout.addStretch()
        return col

    def _on_note_changed(self):
        text = self.note_edit.toPlainText()
        if len(text) > 250:
            text = text[:250]
            cursor = self.note_edit.textCursor()
            self.note_edit.blockSignals(True)
            self.note_edit.setPlainText(text)
            self.note_edit.blockSignals(False)
            cursor.movePosition(cursor.MoveOperation.End)
            self.note_edit.setTextCursor(cursor)
        self.char_count_lbl.setText(f"{len(text)} / 250")

    def _build_packages_column(self) -> QWidget:
        col = QWidget()
        col.setObjectName("BuyCreditsCol")
        col.setStyleSheet("#BuyCreditsCol { background: transparent; }")
        layout = QVBoxLayout(col)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        pkg_card, pkg_layout = _card("Credit Packages")
        sub_lbl = QLabel("Choose a package that suits your needs")
        sub_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 10px; background: transparent;")
        pkg_layout.addWidget(sub_lbl)

        self.package_rows = {}
        for pkg in CREDIT_PACKAGES:
            row = PackageRow(pkg)
            row.selected.connect(self.on_package_selected)
            self.package_rows[pkg["id"]] = row
            pkg_layout.addWidget(row)
        layout.addWidget(pkg_card)

        notes_card, notes_layout = _card()
        notes_layout.setSpacing(6)
        notes_header = QHBoxLayout()
        notes_header.setSpacing(6)
        warn_icon = QLabel()
        warn_icon.setStyleSheet("background: transparent;")
        warn_icon.setPixmap(qta.icon('fa5s.exclamation-circle', color=PALETTE['yellow']).pixmap(13, 13))
        notes_header.addWidget(warn_icon)
        notes_title = QLabel("Important Notes")
        notes_title.setStyleSheet(f"color: {PALETTE['yellow']}; font-size: 12px; font-weight: 600; background: transparent;")
        notes_header.addWidget(notes_title)
        notes_header.addStretch()
        notes_layout.addLayout(notes_header)
        for note in [
            "Please upload a clear screenshot of your payment.",
            "Payments are verified within 1-24 hours.",
            "You will receive an email once your credits are added.",
            "For any questions, contact our support team.",
        ]:
            note_lbl = QLabel(f"\u2022  {note}")
            note_lbl.setWordWrap(True)
            note_lbl.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 10px; background: transparent;")
            notes_layout.addWidget(note_lbl)
        notes_card.setStyleSheet(f"#DashCard {{ background-color: {config.rgba_from_hex(PALETTE['yellow'], 0.06)}; border: 1px solid {config.rgba_from_hex(PALETTE['yellow'], 0.25)}; border-radius: 10px; }}")
        layout.addWidget(notes_card)

        recent_card, recent_layout = _card()
        recent_header = QHBoxLayout()
        recent_title = QLabel("Recent Transactions")
        recent_title.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 13px; font-weight: 600; background: transparent;")
        recent_header.addWidget(recent_title)
        recent_header.addStretch()
        view_all_btn = QPushButton("View all")
        view_all_btn.setObjectName("LinkBtn")
        view_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        view_all_btn.clicked.connect(lambda: self.set_active_subtab("Payment History"))
        recent_header.addWidget(view_all_btn)
        recent_layout.addLayout(recent_header)

        self.recent_list_layout = QVBoxLayout()
        self.recent_list_layout.setSpacing(0)
        recent_layout.addLayout(self.recent_list_layout)
        layout.addWidget(recent_card)
        self._refresh_recent_list()

        layout.addStretch()
        return col

    def _refresh_recent_list(self):
        while self.recent_list_layout.count():
            item = self.recent_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        recent_txs = self.transactions[:3]
        for i, tx in enumerate(recent_txs):
            self.recent_list_layout.addWidget(
                _transaction_row(tx, compact=True, is_last=(i == len(recent_txs) - 1))
            )

    def on_package_selected(self, pkg_id: str):
        self.selected_package_id = pkg_id
        for pid, row in self.package_rows.items():
            row.set_selected(pid == pkg_id)

    def submit_payment(self):
        method = next(m for m in PAYMENT_METHODS if m["id"] == self.selected_method_id)
        pkg = next(p for p in CREDIT_PACKAGES if p["id"] == self.selected_package_id)

        new_tx = {
            "method": method["label"],
            "credits": f"{pkg['credits']:,} Credits",
            "amount": pkg["price"],
            "status": "Pending",
            "date": "Just now",
            "time": "",
        }
        self.transactions.insert(0, new_tx)
        self._refresh_recent_list()
        self._refresh_history_list()

        self.note_edit.clear()
        self.dropzone.reset()

        InfoDialog.show(
            self, "Payment Submitted",
            f"Your payment of {pkg['price']} for {pkg['credits']:,} credits via "
            f"{method['label']} has been submitted for review. You'll get an email "
            "once it's verified (usually within 1-24 hours).",
            icon_name='fa5s.paper-plane',
        )

    # ------------------------------------------------------------------
    # Payment History tab
    # ------------------------------------------------------------------
    def _build_history_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 0)

        card, card_layout = _card("All Transactions")
        sub_lbl = QLabel("Every credit top-up you've submitted, most recent first.")
        sub_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 10px; background: transparent;")
        card_layout.addWidget(sub_lbl)

        self.history_list_layout = QVBoxLayout()
        self.history_list_layout.setSpacing(0)
        card_layout.addLayout(self.history_list_layout)
        layout.addWidget(card)
        layout.addStretch()

        self._refresh_history_list()
        return page

    def _refresh_history_list(self):
        if not hasattr(self, "history_list_layout"):
            return
        while self.history_list_layout.count():
            item = self.history_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for i, tx in enumerate(self.transactions):
            self.history_list_layout.addWidget(
                _transaction_row(tx, compact=False, is_last=(i == len(self.transactions) - 1))
            )

    # ------------------------------------------------------------------
    # Invoices tab
    # ------------------------------------------------------------------
    def _build_invoices_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 0)

        card, card_layout = _card("Invoices")
        sub_lbl = QLabel("Download a copy of any past invoice for your records.")
        sub_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 10px; background: transparent;")
        card_layout.addWidget(sub_lbl)

        for idx, inv in enumerate(INVOICES):
            row = QWidget()
            row.setObjectName("InvoiceRow")
            border = "none" if idx == len(INVOICES) - 1 else f"1px solid {PALETTE['divider']}"
            row.setStyleSheet(f"#InvoiceRow {{ background: transparent; border-bottom: {border}; }}")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 8, 0, 8)

            icon_lbl = QLabel()
            icon_lbl.setStyleSheet("background: transparent;")
            icon_lbl.setPixmap(qta.icon('fa5s.file-invoice-dollar', color=PALETTE['text_muted']).pixmap(16, 16))
            row_layout.addWidget(icon_lbl)

            text_col = QVBoxLayout()
            text_col.setSpacing(1)
            num_lbl = QLabel(inv["number"])
            num_lbl.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 11px; font-weight: 600; background: transparent;")
            text_col.addWidget(num_lbl)
            date_lbl = QLabel(inv["date"])
            date_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 10px; background: transparent;")
            text_col.addWidget(date_lbl)
            row_layout.addLayout(text_col, stretch=1)

            amount_lbl = QLabel(inv["amount"])
            amount_lbl.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 11px; font-weight: 600; background: transparent;")
            row_layout.addWidget(amount_lbl)

            row_layout.addWidget(_status_pill(inv["status"]))

            download_btn = QPushButton(qta.icon('fa5s.download', color=PALETTE['text_muted']), "")
            download_btn.setObjectName("OutlineBtn")
            download_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            download_btn.setToolTip(f"Download {inv['number']}")
            download_btn.clicked.connect(lambda _c=False, n=inv["number"]: self._download_invoice(n))
            row_layout.addWidget(download_btn)

            card_layout.addWidget(row)

        layout.addWidget(card)
        layout.addStretch()
        return page

    def _download_invoice(self, invoice_number: str):
        InfoDialog.show(
            self, "Download Invoice",
            f"Downloading '{invoice_number}' isn't wired up to a backend yet -- "
            "there's no real invoice file behind this prototype.",
            icon_name='fa5s.file-invoice', success=True,
        )

    # ------------------------------------------------------------------
    # Subscription tab
    # ------------------------------------------------------------------
    def _build_subscription_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(14)

        card, card_layout = _card()
        top_row = QHBoxLayout()
        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        plan_lbl = QLabel(_PLAN_PLACEHOLDER)
        plan_lbl.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 16px; font-weight: bold; background: transparent;")
        text_col.addWidget(plan_lbl)
        sub_lbl = QLabel("Your current subscription plan")
        sub_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 11px; background: transparent;")
        text_col.addWidget(sub_lbl)
        top_row.addLayout(text_col, stretch=1)
        top_row.addWidget(_status_pill("Completed"))
        card_layout.addLayout(top_row)

        card_layout.addSpacing(4)
        for feature in [
            "Unlimited saved searches",
            "Priority lead enrichment queue",
            "Bulk CSV export",
            "Email + chat support",
        ]:
            row = QHBoxLayout()
            row.setSpacing(8)
            check_lbl = QLabel()
            check_lbl.setStyleSheet("background: transparent;")
            check_lbl.setPixmap(qta.icon('fa5s.check', color=PALETTE['green']).pixmap(11, 11))
            row.addWidget(check_lbl)
            feature_lbl = QLabel(feature)
            feature_lbl.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 11px; background: transparent;")
            row.addWidget(feature_lbl, stretch=1)
            card_layout.addLayout(row)

        card_layout.addSpacing(6)
        manage_btn = QPushButton(qta.icon('fa5s.cog', color="white"), " Manage Subscription")
        manage_btn.setObjectName("RedBtn")
        manage_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        manage_btn.clicked.connect(self._manage_subscription)
        card_layout.addWidget(manage_btn)

        layout.addWidget(card)
        layout.addStretch()
        return page

    def _manage_subscription(self):
        InfoDialog.show(
            self, "Manage Subscription",
            "Plan changes and cancellation aren't wired up to a backend yet -- "
            "this is a prototype-only Billing tab.",
            icon_name='fa5s.cog', success=True,
        )