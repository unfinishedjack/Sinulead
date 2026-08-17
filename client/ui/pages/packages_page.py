"""
packages_page.py

PackagesPage: the "Packages" nav tab -- admin-only. Two sub-tabs (same
pill-tab + internal QStackedWidget pattern as billing.BillingPage):

  - Credit Packages -- table of every credit package users can buy
    (name/badge, credits, price, bonus, total credits, popular star,
    enabled/disabled status, edit/delete actions), plus Add Package /
    Reorder Packages / Activity Log buttons.
  - Payment Methods  -- one card per payment method (GCash / Maya /
    PayPal) with its QR code, account details, notes, enabled toggle
    and an Edit dialog, plus a General Payment Settings card (approval
    mode, receipt requirements, accepted file formats, payment expiry).

Like billing.py / users_page.py, this is a prototype: all mutations
just update the in-memory data.CREDIT_PACKAGES / data.PAYMENT_METHODS /
data.PAYMENT_SETTINGS lists/dicts (shared with billing.py, so changes
made here show up there too) rather than hitting a real backend.
Actions with no real backend yet (Reorder Packages, Add Payment Method,
Activity Log) show a themed "not wired up" dialog via dialogs.InfoDialog.

Depends on: config, data (CREDIT_PACKAGES, PAYMENT_METHODS,
PAYMENT_SETTINGS), dialogs (ModernDialog, InfoDialog, ConfirmDialog,
labeled_field), billing (QRCodeWidget, reused as-is for the QR display).
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QPushButton, QFrame, QScrollArea, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QComboBox,
    QStackedWidget, QDialog, QCheckBox, QRadioButton, QButtonGroup,
    QMenu,
)
from PySide6.QtCore import Qt, QSize, QRectF
from PySide6.QtGui import QPainter, QColor, QBrush, QPen, QPixmap, QPainterPath
import qtawesome as qta

from core import config
from core.config import PALETTE
from data import CREDIT_PACKAGES, PAYMENT_METHODS, PAYMENT_SETTINGS
from ui.dialogs.dialogs import ModernDialog, InfoDialog, ConfirmDialog, labeled_field
from ui.pages.billing import QRCodeWidget
from ui.components.widgets import pill as _shared_pill

def _badge_colors() -> dict:
    """Fresh dict on every call (not a module-level constant) so it always
    reflects the currently active PALETTE instead of freezing colors from
    whichever theme was active at import time."""
    return {
        "Basic": (PALETTE["text_muted"], config.rgba_from_hex(PALETTE["text_muted"], 0.12)),
        "Most Popular": (PALETTE["red"], config.rgba_from_hex(PALETTE["accent"], 0.15)),
        "Best Value": (PALETTE["green"], config.rgba_from_hex(PALETTE["green"], 0.15)),
        "Maximum": (PALETTE["yellow"], config.rgba_from_hex(PALETTE["yellow"], 0.15)),
    }

_ALL_FILE_FORMATS = ["JPG", "PNG", "JPEG", "PDF", "WEBP"]


def _pill(text: str, accent: str, bg: str) -> QWidget:
    """Thin wrapper over widgets.pill() (see PROGRESS.md, Phase 1e)."""
    return _shared_pill(text, accent, bg)


def _badge_pill(badge: str) -> QWidget:
    accent, bg = _badge_colors().get(
        badge, (PALETTE["text_muted"], config.rgba_from_hex(PALETTE["text_muted"], 0.12))
    )
    return _pill(badge, accent, bg)


def _package_name_cell(pkg: dict) -> QWidget:
    wrap = QWidget()
    wrap.setStyleSheet("background: transparent;")
    row = QHBoxLayout(wrap)
    row.setContentsMargins(6, 4, 6, 4)
    row.setSpacing(8)
    name_lbl = QLabel(pkg["name"])
    name_lbl.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 12px; font-weight: 600; background: transparent;")
    row.addWidget(name_lbl)
    if pkg.get("badge"):
        row.addWidget(_badge_pill(pkg["badge"]))
    row.addStretch()
    return wrap


def _bonus_cell(pkg: dict) -> QWidget:
    pct = pkg.get("bonus_pct", 0)
    text = f"+{pct}%" if pct else "0%"
    if pct:
        return _pill(text, PALETTE['green'], "rgba(74,222,128,0.15)")
    return _pill(text, PALETTE['text_muted'], "rgba(156,163,175,0.12)")


def _total_credits_cell(pkg: dict) -> QWidget:
    wrap = QWidget()
    wrap.setStyleSheet("background: transparent;")
    row = QHBoxLayout(wrap)
    row.setContentsMargins(0, 0, 0, 0)
    row.setAlignment(Qt.AlignmentFlag.AlignCenter)
    pct = pkg.get("bonus_pct", 0)
    color = PALETTE['green'] if pct else PALETTE['text_muted']
    lbl = QLabel(f'{pkg["total_credits"]:,}')
    lbl.setStyleSheet(f"color: {color}; font-size: 12px; font-weight: 600; background: transparent;")
    row.addWidget(lbl)
    return wrap

class _StarToggle(QPushButton):
    """Clickable star used for the 'Popular' column -- filled yellow star
    when this package is marked popular, hollow outline otherwise."""
    def __init__(self, pkg: dict, on_toggle, parent=None):
        super().__init__(parent)
        self.pkg = pkg
        self._on_toggle = on_toggle
        self.setFixedSize(24, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("QPushButton { background: transparent; border: none; }")
        self.clicked.connect(self._toggle)
        self.refresh()

    def refresh(self):
        # Only fa5s (solid) icons are bundled in this project (no fa5r
        # regular-style font), so "off" is shown as a dim solid star
        # rather than a hollow outline star.
        color = PALETTE['yellow'] if self.pkg.get("popular") else "#3a3f4b"  # "off" dim gray has no palette match, left as-is
        self.setIcon(qta.icon('fa5s.star', color=color))
        self.setIconSize(QSize(14, 14))

    def _toggle(self):
        self.pkg["popular"] = not self.pkg.get("popular")
        self.refresh()
        self._on_toggle(self.pkg)


class _StatusToggleBtn(QPushButton):
    """Clickable 'Enabled'/'Disabled' pill button used in the Status
    column -- click asks for confirmation, then flips status in place."""
    def __init__(self, pkg: dict, on_toggle, parent=None):
        super().__init__(parent)
        self.pkg = pkg
        self._on_toggle = on_toggle
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(26)
        self.clicked.connect(self._confirm_toggle)
        self.refresh()

    def refresh(self):
        enabled = self.pkg.get("status", "Enabled") == "Enabled"
        self.setText("Enabled" if enabled else "Disabled")
        if enabled:
            self.setStyleSheet(
                f"QPushButton {{ background-color: {config.rgba_from_hex(PALETTE['green'], 0.15)}; color: {PALETTE['green']}; }}"
                f"QPushButton {{ border: 1px solid {config.rgba_from_hex(PALETTE['green'], 0.35)}; border-radius: 5px; "
                f"font-size: 10px; font-weight: 600; padding: 3px 10px; }}"
                f"QPushButton:hover {{ background-color: {config.rgba_from_hex(PALETTE['green'], 0.25)}; }}"
            )
        else:
            self.setStyleSheet(
                f"QPushButton {{ background-color: {config.rgba_from_hex(PALETTE['text_muted'], 0.12)}; color: {PALETTE['text_muted']}; }}"
                f"QPushButton {{ border: 1px solid {config.rgba_from_hex(PALETTE['text_muted'], 0.3)}; border-radius: 5px; "
                f"font-size: 10px; font-weight: 600; padding: 3px 10px; }}"
                f"QPushButton:hover {{ background-color: {config.rgba_from_hex(PALETTE['text_muted'], 0.22)}; }}"
            )

    def _confirm_toggle(self):
        enabling = self.pkg.get("status") != "Enabled"
        confirmed = ConfirmDialog.ask(
            self, "Enable Package" if enabling else "Disable Package",
            f'{"Enable" if enabling else "Disable"} "{self.pkg["name"]}"? '
            f'{"Users will be able to purchase it again." if enabling else "Users will no longer see this package."}',
            confirm_text="Enable" if enabling else "Disable",
            icon_name='fa5s.toggle-on' if enabling else 'fa5s.toggle-off', danger=not enabling,
        )
        if not confirmed:
            return
        self.pkg["status"] = "Enabled" if enabling else "Disabled"
        self.refresh()
        self._on_toggle(self.pkg)


class PackageDialog(ModernDialog):
    """Add/Edit Package form. Same shape whether creating a new package
    or editing an existing one -- pass an existing dict to pre-fill."""
    def __init__(self, pkg: dict = None, parent=None):
        super().__init__(parent)
        self.setFixedWidth(360)
        self._editing = pkg is not None

        layout = self.card_layout()
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)
        self.add_header(layout, "Edit Package" if self._editing else "Add Package")

        self.name_edit = QLineEdit(pkg["name"] if pkg else "")
        self.name_edit.setPlaceholderText("e.g. Starter Pack")
        layout.addLayout(labeled_field("Package Name", self.name_edit))

        self.badge_combo = QComboBox()
        self.badge_combo.setObjectName("RowModeCombo")
        self.badge_combo.addItems(["None", "Basic", "Most Popular", "Best Value", "Maximum"])
        if pkg and pkg.get("badge"):
            self.badge_combo.setCurrentText(pkg["badge"])
        layout.addLayout(labeled_field("Badge / Tag", self.badge_combo))

        row1 = QHBoxLayout()
        row1.setSpacing(10)
        self.credits_edit = QLineEdit(str(pkg["credits"]) if pkg else "")
        self.credits_edit.setPlaceholderText("e.g. 500")
        row1.addLayout(labeled_field("Credits", self.credits_edit))
        self.price_edit = QLineEdit(str(pkg["price_value"]) if pkg else "")
        self.price_edit.setPlaceholderText("e.g. 50")
        row1.addLayout(labeled_field("Price (PHP)", self.price_edit))
        layout.addLayout(row1)

        self.bonus_edit = QLineEdit(str(pkg.get("bonus_pct", 0)) if pkg else "0")
        self.bonus_edit.setPlaceholderText("e.g. 20")
        layout.addLayout(labeled_field("Bonus (%)", self.bonus_edit))

        self.popular_check = QCheckBox("Mark as Popular (highlighted with a star)")
        self.popular_check.setChecked(bool(pkg.get("popular")) if pkg else False)
        layout.addWidget(self.popular_check)

        self.enabled_check = QCheckBox("Enabled (visible to users)")
        self.enabled_check.setChecked((pkg.get("status", "Enabled") == "Enabled") if pkg else True)
        layout.addWidget(self.enabled_check)

        self.error_lbl = QLabel("")
        self.error_lbl.setStyleSheet(f"color: {PALETTE['red']}; font-size: 10px;")
        self.error_lbl.setWordWrap(True)
        self.error_lbl.hide()
        layout.addWidget(self.error_lbl)

        layout.addSpacing(4)
        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("OutlineBtn")
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)
        confirm_btn = QPushButton(
            qta.icon('fa5s.check', color="white"), " Save Changes" if self._editing else " Add Package"
        )
        confirm_btn.setObjectName("RedBtn")
        confirm_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        confirm_btn.clicked.connect(self._on_confirm)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(confirm_btn)
        layout.addLayout(btn_row)

    def _on_confirm(self):
        name = self.name_edit.text().strip()
        credits_txt = self.credits_edit.text().strip().replace(",", "")
        price_txt = self.price_edit.text().strip().replace(",", "")
        bonus_txt = self.bonus_edit.text().strip() or "0"

        if not name:
            return self._error("Package name is required.")
        if not credits_txt.isdigit() or int(credits_txt) <= 0:
            return self._error("Credits must be a whole number greater than 0.")
        if not price_txt.replace(".", "", 1).isdigit():
            return self._error("Price must be a valid number.")
        if not bonus_txt.isdigit():
            return self._error("Bonus % must be a whole number.")

        self.accept()

    def _error(self, message: str):
        self.error_lbl.setText(message)
        self.error_lbl.show()

    def get_values(self) -> dict:
        credits = int(self.credits_edit.text().strip().replace(",", ""))
        price_value = float(self.price_edit.text().strip().replace(",", ""))
        bonus_pct = int(self.bonus_edit.text().strip() or 0)
        badge = self.badge_combo.currentText()
        total_credits = int(credits + credits * bonus_pct / 100)
        price_str = f"{price_value:,.2f}"
        if price_str.endswith(".00"):
            price_str = price_str[:-3]
        return {
            "name": self.name_edit.text().strip(),
            "badge": None if badge == "None" else badge,
            "credits": credits,
            "bonus_pct": bonus_pct,
            "bonus": f"+{bonus_pct}% Bonus" if bonus_pct else None,
            "price_value": price_value,
            "price": "\u20b1" + price_str,
            "total_credits": total_credits,
            "popular": self.popular_check.isChecked(),
            "status": "Enabled" if self.enabled_check.isChecked() else "Disabled",
        }

    @staticmethod
    def ask(parent, pkg: dict = None):
        dlg = PackageDialog(pkg, parent)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            return dlg.get_values()
        return None


class PaymentMethodDialog(ModernDialog):
    """Edit form for one payment method's account details / notes /
    enabled state -- there are always exactly 3 methods in this
    prototype (GCash / Maya / PayPal), so this is edit-only."""
    def __init__(self, method: dict, parent=None):
        super().__init__(parent)
        self.setFixedWidth(360)
        self.method = method

        layout = self.card_layout()
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)
        self.add_header(layout, f'Edit {method["label"]}')

        self.account_name_edit = QLineEdit(method.get("account_name", ""))
        layout.addLayout(labeled_field("Account Name", self.account_name_edit))

        self.account_value_edit = QLineEdit(method.get("account_value", ""))
        layout.addLayout(labeled_field(method.get("account_label", "Account Value"), self.account_value_edit))

        self.notes_edit = QLineEdit(method.get("notes", "").replace("\n", " "))
        self.notes_edit.setPlaceholderText("e.g. Please send exact amount.")
        layout.addLayout(labeled_field("Notes", self.notes_edit))

        self.enabled_check = QCheckBox("Enabled (shown to users on the billing page)")
        self.enabled_check.setChecked(method.get("enabled", True))
        layout.addWidget(self.enabled_check)

        layout.addSpacing(4)
        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("OutlineBtn")
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)
        confirm_btn = QPushButton(qta.icon('fa5s.check', color="white"), " Save Changes")
        confirm_btn.setObjectName("RedBtn")
        confirm_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        confirm_btn.clicked.connect(self.accept)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(confirm_btn)
        layout.addLayout(btn_row)

    def get_values(self) -> dict:
        return {
            "account_name": self.account_name_edit.text().strip(),
            "account_value": self.account_value_edit.text().strip(),
            "notes": self.notes_edit.text().strip(),
            "enabled": self.enabled_check.isChecked(),
        }

    @staticmethod
    def ask(parent, method: dict):
        dlg = PaymentMethodDialog(method, parent)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            return dlg.get_values()
        return None


def _rounded_pixmap(pix: QPixmap, size: int, radius: int = 8) -> QPixmap:
    scaled = pix.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    canvas = QPixmap(size, size)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0, 0, size, size, radius, radius)
    painter.setClipPath(path)
    x = (size - scaled.width()) // 2
    y = (size - scaled.height()) // 2
    painter.drawPixmap(x, y, scaled)
    painter.end()
    return canvas


def _method_logo_pixmap(method: dict, size: int = 40) -> QPixmap:
    logo_path = method.get("logo")
    pix = QPixmap(logo_path) if logo_path else QPixmap()
    if not pix.isNull():
        return _rounded_pixmap(pix, size, radius=8)
    # Fallback: colored square with the method's letter
    fallback = QPixmap(size, size)
    fallback.fill(Qt.GlobalColor.transparent)
    painter = QPainter(fallback)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(QColor(method.get("color", PALETTE['text_muted']))))
    painter.drawRoundedRect(0, 0, size, size, 8, 8)
    font = painter.font()
    font.setBold(True)
    font.setPointSize(max(8, size // 3))
    painter.setFont(font)
    painter.setPen(QPen(QColor("white")))
    painter.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, method.get("letter", "?"))
    painter.end()
    return fallback


class PaymentMethodCard(QFrame):
    """One payment-method card: logo/label + Enabled pill + kebab menu,
    QR code, account details, notes, last-updated + Edit button."""
    def __init__(self, method: dict, on_change, parent=None):
        super().__init__(parent)
        self.method = method
        self._on_change = on_change
        self.setObjectName("DashCard")
        self.setMinimumWidth(280)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(8)
        logo_lbl = QLabel()
        logo_lbl.setStyleSheet("background: transparent;")
        logo_lbl.setPixmap(_method_logo_pixmap(method, 28))
        header.addWidget(logo_lbl)
        label_lbl = QLabel(method["label"])
        label_lbl.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 13px; font-weight: 600; background: transparent;")
        header.addWidget(label_lbl)
        header.addStretch()

        self.status_pill_slot = QHBoxLayout()
        header.addLayout(self.status_pill_slot)

        kebab_btn = QPushButton()
        kebab_btn.setIcon(qta.icon('fa5s.ellipsis-v', color=PALETTE['text_muted']))
        kebab_btn.setFixedSize(24, 24)
        kebab_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        kebab_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; border-radius: 4px; }}"
            f"QPushButton:hover {{ background-color: {PALETTE['bg_hover']}; }}"
        )
        kebab_btn.clicked.connect(lambda: self._open_kebab_menu(kebab_btn))
        header.addWidget(kebab_btn)
        outer.addLayout(header)

        content_row = QHBoxLayout()
        content_row.setSpacing(16)

        qr_col = QVBoxLayout()
        qr_col.setSpacing(4)
        qr_col.addWidget(QRCodeWidget(method["id"]), alignment=Qt.AlignmentFlag.AlignCenter)
        scan_lbl = QLabel("SCAN TO PAY")
        scan_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scan_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 9px; font-weight: 600; background: transparent;")
        qr_col.addWidget(scan_lbl)
        content_row.addLayout(qr_col)

        details_col = QVBoxLayout()
        details_col.setSpacing(8)
        name_row, self.account_name_lbl = self._detail_field("Account Name")
        details_col.addLayout(name_row)
        value_row, self.account_value_lbl = self._detail_field(method.get("account_label", "Account Value"))
        details_col.addLayout(value_row)

        notes_label = QLabel("Notes")
        notes_label.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 10px; background: transparent;")
        details_col.addWidget(notes_label)
        self.notes_lbl = QLabel()
        self.notes_lbl.setWordWrap(True)
        self.notes_lbl.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 11px; background: transparent;")
        details_col.addWidget(self.notes_lbl)
        details_col.addStretch()

        content_row.addLayout(details_col, stretch=1)
        outer.addLayout(content_row)

        footer = QHBoxLayout()
        self.updated_lbl = QLabel()
        # #4b5563 has no exact match in PALETTE (between border_strong and
        # text_dim); left hardcoded rather than guessing a shade.
        self.updated_lbl.setStyleSheet("color: #4b5563; font-size: 9px; background: transparent;")
        footer.addWidget(self.updated_lbl)
        footer.addStretch()
        edit_btn = QPushButton(qta.icon('fa5s.pen', color="white"), " Edit")
        edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        # #4f46e5/#4338ca (indigo) is a one-off accent not in PALETTE --
        # distinct from the app's red accent, left hardcoded rather than
        # remapped to a color that would change its hue.
        edit_btn.setStyleSheet(
            "QPushButton { background-color: #4f46e5; color: white; border: none; "
            "border-radius: 6px; font-size: 11px; font-weight: 600; padding: 6px 14px; }"
            "QPushButton:hover { background-color: #4338ca; }"
        )
        edit_btn.clicked.connect(self._edit)
        footer.addWidget(edit_btn)
        outer.addLayout(footer)

        self.refresh()

    def _detail_field(self, label_text: str):
        """Returns (layout, value_label) for one label+value block --
        caller adds the layout into its own row."""
        col = QVBoxLayout()
        col.setSpacing(1)
        lbl = QLabel(label_text)
        lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 10px; background: transparent;")
        col.addWidget(lbl)
        value_lbl = QLabel()
        value_lbl.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 12px; font-weight: 600; background: transparent;")
        col.addWidget(value_lbl)
        return col, value_lbl

    def _open_kebab_menu(self, anchor_btn: QPushButton):
        menu = QMenu(self)
        toggle_action = menu.addAction(
            "Disable" if self.method.get("enabled", True) else "Enable"
        )
        toggle_action.triggered.connect(self._toggle_enabled)
        activity_action = menu.addAction("View Activity Log")
        activity_action.triggered.connect(self._view_activity_not_wired_up)
        menu.exec(anchor_btn.mapToGlobal(anchor_btn.rect().bottomRight()))

    def _toggle_enabled(self):
        enabling = not self.method.get("enabled", True)
        confirmed = ConfirmDialog.ask(
            self, "Enable Payment Method" if enabling else "Disable Payment Method",
            f'{"Enable" if enabling else "Disable"} {self.method["label"]}? '
            f'{"Users will see it on the billing page again." if enabling else "Users will no longer see it as an option."}',
            confirm_text="Enable" if enabling else "Disable",
            icon_name='fa5s.toggle-on' if enabling else 'fa5s.toggle-off', danger=not enabling,
        )
        if not confirmed:
            return
        self.method["enabled"] = enabling
        self.refresh()
        self._on_change()

    def _view_activity_not_wired_up(self):
        InfoDialog.show(
            self, "Activity Log",
            "A detailed per-method activity log isn't wired up to a "
            "backend yet -- this is a prototype-only Packages tab.",
            icon_name='fa5s.history', success=True,
        )

    def _edit(self):
        values = PaymentMethodDialog.ask(self, self.method)
        if values is None:
            return
        self.method.update(values)
        import datetime
        self.method["updated"] = datetime.datetime.now().strftime("%b %d, %Y %I:%M %p")
        self.refresh()
        self._on_change()

    def refresh(self):
        while self.status_pill_slot.count():
            item = self.status_pill_slot.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        enabled = self.method.get("enabled", True)
        self.status_pill_slot.addWidget(
            _pill("Enabled", PALETTE['green'], "rgba(74,222,128,0.12)") if enabled
            else _pill("Disabled", PALETTE['text_muted'], "rgba(156,163,175,0.12)")
        )

        self.account_name_lbl.setText(self.method.get("account_name", "\u2014"))
        self.account_value_lbl.setText(self.method.get("account_value", "\u2014"))
        self.notes_lbl.setText(self.method.get("notes", ""))
        updated_text = self.method.get("updated", "\u2014")
        self.updated_lbl.setText("Last updated: " + updated_text)


class PackagesPage(QScrollArea):
    """
    The "Packages" nav tab (admin-only). Two pill sub-tabs sharing one
    header (Activity Log / Add Package buttons stay visible on both):
      - "Credit Packages" -- table + Add/Edit/Delete/Reorder.
      - "Payment Methods" -- cards + General Payment Settings.
    Mutates data.CREDIT_PACKAGES / data.PAYMENT_METHODS / data.PAYMENT_SETTINGS
    in place, same in-memory-prototype pattern as the rest of the app.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DashScroll")
        self.setWidgetResizable(True)

        self.packages = CREDIT_PACKAGES  # shared list with billing.py -- edits here show up there
        self.methods = PAYMENT_METHODS
        self.settings = PAYMENT_SETTINGS

        content = QWidget()
        outer = QVBoxLayout(content)
        outer.setContentsMargins(20, 16, 20, 20)
        outer.setSpacing(14)

        # --- Header ---
        header_row = QHBoxLayout()
        header_col = QVBoxLayout()
        header_col.setSpacing(2)
        title_lbl = QLabel("Packages")
        title_lbl.setObjectName("HeaderTitle")
        header_col.addWidget(title_lbl)
        sub_lbl = QLabel("Manage credit packages and payment methods.")
        sub_lbl.setObjectName("HeaderSub")
        header_col.addWidget(sub_lbl)
        header_row.addLayout(header_col)
        header_row.addStretch()

        activity_btn = QPushButton(qta.icon('fa5s.file-alt', color=PALETTE['text_muted']), " Activity Log")
        activity_btn.setObjectName("OutlineBtn")
        activity_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        activity_btn.clicked.connect(self._activity_log_not_wired_up)
        header_row.addWidget(activity_btn)

        add_pkg_btn = QPushButton(qta.icon('fa5s.plus', color="white"), " Add Package")
        add_pkg_btn.setObjectName("RedBtn")
        add_pkg_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_pkg_btn.clicked.connect(self._add_package)
        header_row.addWidget(add_pkg_btn)

        outer.addLayout(header_row)

        # --- Sub-tab bar ---
        tabbar_col = QVBoxLayout()
        tabbar_col.setSpacing(0)
        tab_bar = QHBoxLayout()
        tab_bar.setSpacing(4)
        self.subtab_buttons = {}
        for icon_name, name in [("fa5s.box", "Credit Packages"), ("fa5s.credit-card", "Payment Methods")]:
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
            "Credit Packages": self._build_packages_tab(),
            "Payment Methods": self._build_payment_methods_tab(),
        }
        for page in self.subtab_pages.values():
            self.stack.addWidget(page)
        outer.addWidget(self.stack)

        self.setWidget(content)
        self.set_active_subtab("Credit Packages")
        self.populate_table()

    def set_active_subtab(self, name: str):
        self.stack.setCurrentWidget(self.subtab_pages[name])
        icon_names = {"Credit Packages": "fa5s.box", "Payment Methods": "fa5s.credit-card"}
        for btn_name, btn in self.subtab_buttons.items():
            is_active = btn_name == name
            btn.setObjectName("BillingTabActive" if is_active else "BillingTabItem")
            btn.setIcon(qta.icon(icon_names[btn_name], color=PALETTE['red'] if is_active else PALETTE['text_muted']))
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _activity_log_not_wired_up(self):
        InfoDialog.show(
            self, "Activity Log",
            "A full package/payment-method activity log isn't wired up "
            "to a backend yet -- this is a prototype-only Packages tab.",
            icon_name='fa5s.file-alt', success=True,
        )

    # ------------------------------------------------------------------
    # Credit Packages tab
    # ------------------------------------------------------------------
    def _build_packages_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(14)

        card = QFrame()
        card.setObjectName("DashCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(10)

        card_header = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title_lbl = QLabel("Credit Packages")
        title_lbl.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 14px; font-weight: 600; background: transparent;")
        title_col.addWidget(title_lbl)
        sub_lbl = QLabel("Create and manage credit packages that users can purchase.")
        sub_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 11px; background: transparent;")
        title_col.addWidget(sub_lbl)
        card_header.addLayout(title_col)
        card_header.addStretch()

        reorder_btn = QPushButton(qta.icon('fa5s.sort', color=PALETTE['text_muted']), " Reorder Packages")
        reorder_btn.setObjectName("OutlineBtn")
        reorder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        reorder_btn.clicked.connect(self._reorder_not_wired_up)
        card_header.addWidget(reorder_btn)
        card_layout.addLayout(card_header)

        self.columns = [
            {"label": "#"}, {"label": "Package Name"}, {"label": "Credits"},
            {"label": "Price (PHP)"}, {"label": "Bonus"}, {"label": "Total Credits"},
            {"label": "Popular"}, {"label": "Status"}, {"label": "Actions"},
        ]
        (self.COL_NUM, self.COL_NAME, self.COL_CREDITS, self.COL_PRICE, self.COL_BONUS,
         self.COL_TOTAL, self.COL_POPULAR, self.COL_STATUS, self.COL_ACTIONS) = range(9)

        self.table = QTableWidget()
        self.table.setColumnCount(len(self.columns))
        self.table.setHorizontalHeaderLabels([c["label"] for c in self.columns])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(52)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(self.COL_NUM, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(self.COL_NUM, 36)
        for col_idx, width in [
            (self.COL_CREDITS, 90), (self.COL_PRICE, 100), (self.COL_BONUS, 80),
            (self.COL_TOTAL, 110), (self.COL_POPULAR, 70), (self.COL_STATUS, 100),
            (self.COL_ACTIONS, 90),
        ]:
            header.setSectionResizeMode(col_idx, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(col_idx, width)
        header.setSectionResizeMode(self.COL_NAME, QHeaderView.ResizeMode.Stretch)
        header.setMinimumSectionSize(30)
        card_layout.addWidget(self.table)

        layout.addWidget(card)

        self.info_box, self._packages_info_label = self._info_box("")
        layout.addWidget(self.info_box)
        layout.addStretch()
        return page

    def _info_box(self, text: str) -> tuple[QFrame, QLabel]:
        box = QFrame()
        box.setObjectName("DashCard")
        box.setStyleSheet(f"QFrame#DashCard {{ background-color: {config.rgba_from_hex(PALETTE['blue'], 0.06)}; border: 1px solid {config.rgba_from_hex(PALETTE['blue'], 0.25)}; }}")
        row = QHBoxLayout(box)
        row.setContentsMargins(14, 10, 14, 10)
        row.setSpacing(10)
        icon_lbl = QLabel()
        icon_lbl.setStyleSheet("background: transparent;")
        icon_lbl.setPixmap(qta.icon('fa5s.info-circle', color=PALETTE['blue']).pixmap(16, 16))
        row.addWidget(icon_lbl, alignment=Qt.AlignmentFlag.AlignVCenter)
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 11px; background: transparent;")
        row.addWidget(label, stretch=1)
        return box, label

    def _reorder_not_wired_up(self):
        InfoDialog.show(
            self, "Reorder Packages",
            "Drag-and-drop reordering isn't wired up to a backend yet -- "
            "this is a prototype-only Packages tab.",
            icon_name='fa5s.sort', success=True,
        )

    def populate_table(self):
        self.table.setRowCount(len(self.packages))
        for row, pkg in enumerate(self.packages):
            num_item = QTableWidgetItem(str(row + 1))
            num_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            num_item.setForeground(QColor(PALETTE['text_muted']))
            self.table.setItem(row, self.COL_NUM, num_item)

            self.table.setItem(row, self.COL_NAME, QTableWidgetItem())
            self.table.setCellWidget(row, self.COL_NAME, _package_name_cell(pkg))

            credits_item = QTableWidgetItem(f'{pkg["credits"]:,}')
            credits_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, self.COL_CREDITS, credits_item)

            price_item = QTableWidgetItem(pkg["price"])
            price_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, self.COL_PRICE, price_item)

            self.table.setItem(row, self.COL_BONUS, QTableWidgetItem())
            self.table.setCellWidget(row, self.COL_BONUS, _bonus_cell(pkg))

            self.table.setItem(row, self.COL_TOTAL, QTableWidgetItem())
            self.table.setCellWidget(row, self.COL_TOTAL, _total_credits_cell(pkg))

            self.table.setItem(row, self.COL_POPULAR, QTableWidgetItem())
            star_wrap = QWidget()
            star_wrap.setStyleSheet("background: transparent;")
            star_row = QHBoxLayout(star_wrap)
            star_row.setContentsMargins(0, 0, 0, 0)
            star_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
            star_row.addWidget(_StarToggle(pkg, lambda p: None))
            self.table.setCellWidget(row, self.COL_POPULAR, star_wrap)

            self.table.setItem(row, self.COL_STATUS, QTableWidgetItem())
            status_wrap = QWidget()
            status_wrap.setStyleSheet("background: transparent;")
            status_row = QHBoxLayout(status_wrap)
            status_row.setContentsMargins(0, 0, 0, 0)
            status_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
            status_row.addWidget(_StatusToggleBtn(pkg, lambda p: None))
            self.table.setCellWidget(row, self.COL_STATUS, status_wrap)

            self.table.setItem(row, self.COL_ACTIONS, QTableWidgetItem())
            self.table.setCellWidget(row, self.COL_ACTIONS, self._actions_cell(pkg))

        self._packages_info_label.setText(
            f"Total Packages: {len(self.packages)}\n"
            "All packages are displayed to users in the order shown above."
        )

    def _actions_cell(self, pkg: dict) -> QWidget:
        wrap = QWidget()
        wrap.setStyleSheet("background: transparent;")
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.setAlignment(Qt.AlignmentFlag.AlignCenter)

        edit_btn = QPushButton()
        edit_btn.setIcon(qta.icon('fa5s.pen', color="white"))
        edit_btn.setFixedSize(26, 26)
        edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        # #2563eb/#1d4ed8 don't exactly match PALETTE's blue/blue_solid
        # (#60a5fa/#3b82f6 in dark mode) -- left hardcoded rather than
        # shifting this button's shade.
        edit_btn.setStyleSheet(
            "QPushButton { background-color: #2563eb; border: none; border-radius: 5px; }"
            "QPushButton:hover { background-color: #1d4ed8; }"
        )
        edit_btn.clicked.connect(lambda: self._edit_package(pkg))
        row.addWidget(edit_btn)

        delete_btn = QPushButton()
        delete_btn.setIcon(qta.icon('fa5s.trash', color="white"))
        delete_btn.setFixedSize(26, 26)
        delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        delete_btn.setStyleSheet(
            f"QPushButton {{ background-color: {PALETTE['red_solid']}; border: none; border-radius: 5px; }}"
            f"QPushButton:hover {{ background-color: {PALETTE['accent_hover']}; }}"
        )
        delete_btn.clicked.connect(lambda: self._delete_package(pkg))
        row.addWidget(delete_btn)
        return wrap

    def _add_package(self):
        values = PackageDialog.ask(self)
        if values is None:
            return
        values["id"] = f"pkg_{len(self.packages) + 1}_{values['credits']}"
        self.packages.append(values)
        self.populate_table()
        InfoDialog.show(self, "Package Added", f'"{values["name"]}" was added.', icon_name='fa5s.check-circle', success=True)

    def _edit_package(self, pkg: dict):
        values = PackageDialog.ask(self, pkg)
        if values is None:
            return
        pkg.update(values)
        self.populate_table()
        InfoDialog.show(self, "Package Updated", f'"{pkg["name"]}" was updated.', icon_name='fa5s.check-circle', success=True)

    def _delete_package(self, pkg: dict):
        confirmed = ConfirmDialog.ask(
            self, "Delete Package", f'Delete "{pkg["name"]}"? This cannot be undone.',
            confirm_text="Delete Package", icon_name='fa5s.trash', danger=True,
        )
        if not confirmed:
            return
        self.packages.remove(pkg)
        self.populate_table()

    # ------------------------------------------------------------------
    # Payment Methods tab
    # ------------------------------------------------------------------
    def _build_payment_methods_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(14)

        section_header = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title_lbl = QLabel("Payment Methods")
        title_lbl.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 14px; font-weight: 600; background: transparent;")
        title_col.addWidget(title_lbl)
        sub_lbl = QLabel("Configure payment methods available for users to purchase credits.")
        sub_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 11px; background: transparent;")
        title_col.addWidget(sub_lbl)
        section_header.addLayout(title_col)
        section_header.addStretch()

        add_method_btn = QPushButton(qta.icon('fa5s.plus', color=PALETTE['text_muted']), " Add Payment Method")
        add_method_btn.setObjectName("OutlineBtn")
        add_method_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_method_btn.clicked.connect(self._add_method_not_wired_up)
        section_header.addWidget(add_method_btn)
        layout.addLayout(section_header)

        cards_row = QHBoxLayout()
        cards_row.setSpacing(14)
        for method in self.methods:
            cards_row.addWidget(PaymentMethodCard(method, self._on_method_changed))
        layout.addLayout(cards_row)

        tips_box, _tips_label = self._info_box(
            "Tips: Enable only the payment methods you want to allow. "
            "Users will see only the enabled methods on the billing page."
        )
        layout.addWidget(tips_box)

        layout.addWidget(self._build_general_settings_card())
        layout.addStretch()
        return page

    def _on_method_changed(self):
        pass  # cards refresh themselves; hook kept for future summary widgets

    def _add_method_not_wired_up(self):
        InfoDialog.show(
            self, "Add Payment Method",
            "Adding a brand-new payment gateway isn't wired up to a "
            "backend yet -- this is a prototype-only Packages tab. Edit "
            "one of the existing methods instead.",
            icon_name='fa5s.plus', success=True,
        )

    def _build_general_settings_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("DashCard")
        outer = QVBoxLayout(card)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(12)

        title_lbl = QLabel("General Payment Settings")
        title_lbl.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 14px; font-weight: 600; background: transparent;")
        outer.addWidget(title_lbl)
        sub_lbl = QLabel("Configure general settings for payments and credit purchases.")
        sub_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 11px; background: transparent;")
        outer.addWidget(sub_lbl)

        h_divider_top = QFrame()
        h_divider_top.setFixedHeight(1)
        # #333a45 doesn't exactly match PALETTE['border_strong'] (#30363d)
        # -- close, but left hardcoded rather than nudging the shade.
        h_divider_top.setStyleSheet("background-color: #333a45;")
        outer.addWidget(h_divider_top)

        grid = QGridLayout()
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(8)

        def _v_divider() -> QFrame:
            line = QFrame()
            line.setFixedWidth(1)
            line.setStyleSheet("background-color: #333a45;")  # see h_divider_top note above
            return line

        # --- Approval Mode ---
        approval_col = QVBoxLayout()
        approval_col.setSpacing(4)
        approval_col.addWidget(self._field_label("Approval Mode", "Choose how payments are processed."))
        self.approval_group = QButtonGroup(self)
        manual_radio = QRadioButton("Manual Approval")
        auto_radio = QRadioButton("Automatic Approval")
        self.approval_group.addButton(manual_radio, 0)
        self.approval_group.addButton(auto_radio, 1)
        if self.settings.get("approval_mode") == "Automatic Approval":
            auto_radio.setChecked(True)
        else:
            manual_radio.setChecked(True)
        approval_col.addWidget(self._radio_option(manual_radio, "Admin must review and approve each payment."))
        approval_col.addWidget(self._radio_option(auto_radio, "Payments will be auto-approved."))
        grid.addLayout(approval_col, 0, 0)
        grid.addWidget(_v_divider(), 0, 1)

        # --- Receipt Required ---
        receipt_col = QVBoxLayout()
        receipt_col.setSpacing(4)
        receipt_col.addWidget(self._field_label("Receipt Required", "Require users to upload proof of payment."))
        self.receipt_group = QButtonGroup(self)
        yes_radio = QRadioButton("Yes, require receipt")
        no_radio = QRadioButton("No, optional")
        self.receipt_group.addButton(yes_radio, 0)
        self.receipt_group.addButton(no_radio, 1)
        if self.settings.get("receipt_required", True):
            yes_radio.setChecked(True)
        else:
            no_radio.setChecked(True)
        receipt_col.addWidget(self._radio_option(yes_radio, "Users must upload proof of payment."))
        receipt_col.addWidget(self._radio_option(no_radio, "Receipt upload is optional."))
        grid.addLayout(receipt_col, 0, 2)
        grid.addWidget(_v_divider(), 0, 3)

        # --- Max Receipt Size ---
        size_col = QVBoxLayout()
        size_col.setSpacing(4)
        size_col.addWidget(self._field_label("Max Receipt Size", "Maximum allowed file size for uploads."))
        self.max_size_combo = QComboBox()
        self.max_size_combo.setObjectName("RowModeCombo")
        self.max_size_combo.addItems(["2 MB", "5 MB", "10 MB", "20 MB"])
        self.max_size_combo.setCurrentText(f'{self.settings.get("max_receipt_size_mb", 5)} MB')
        size_col.addWidget(self.max_size_combo)
        size_col.addStretch()
        grid.addLayout(size_col, 0, 4)
        grid.addWidget(_v_divider(), 0, 5)

        # --- Payment Expiry ---
        # --- Accepted File Formats ---
        formats_col = QVBoxLayout()
        formats_col.setSpacing(4)
        formats_col.addWidget(self._field_label("Accepted File Formats", "Allowed file types for proof of payment."))
        formats_wrap = QWidget()
        formats_wrap.setStyleSheet("background: transparent;")
        self.formats_row = QHBoxLayout(formats_wrap)
        self.formats_row.setContentsMargins(0, 0, 0, 0)
        self.formats_row.setSpacing(8)
        self._selected_formats = list(self.settings.get("accepted_formats", _ALL_FILE_FORMATS))
        self._render_format_chips()
        formats_col.addWidget(formats_wrap)
        formats_col.addStretch()
        grid.addLayout(formats_col, 0, 6)
        grid.addWidget(_v_divider(), 0, 7)

        # --- Payment Expiry ---
        expiry_col = QVBoxLayout()
        expiry_col.setSpacing(4)
        expiry_col.addWidget(self._field_label("Payment Expiry", "How long before a pending payment expires."))
        self.expiry_combo = QComboBox()
        self.expiry_combo.setObjectName("RowModeCombo")
        self.expiry_combo.addItems(["24 hours", "48 hours", "72 hours", "96 hours"])
        self.expiry_combo.setCurrentText(f'{self.settings.get("payment_expiry_hours", 72)} hours')
        expiry_col.addWidget(self.expiry_combo)
        expiry_col.addStretch()
        grid.addLayout(expiry_col, 0, 8)

        for col_idx in (0, 2, 4, 6, 8):
            grid.setColumnStretch(col_idx, 1)

        outer.addLayout(grid)

        save_row = QHBoxLayout()
        save_row.addStretch()
        save_btn = QPushButton(qta.icon('fa5s.save', color="white"), " Save Settings")
        save_btn.setObjectName("RedBtn")
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.clicked.connect(self._save_settings)
        save_row.addWidget(save_btn)
        outer.addLayout(save_row)

        return card

    def _field_label(self, title: str, sub: str) -> QWidget:
        wrap = QWidget()
        wrap.setStyleSheet("background: transparent;")
        col = QVBoxLayout(wrap)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(1)
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 11px; font-weight: 600; background: transparent;")
        col.addWidget(title_lbl)
        sub_lbl = QLabel(sub)
        sub_lbl.setWordWrap(True)
        sub_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 9px; background: transparent;")
        col.addWidget(sub_lbl)
        return wrap

    def _radio_option(self, radio: QRadioButton, sub_text: str) -> QWidget:
        wrap = QWidget()
        wrap.setStyleSheet("background: transparent;")
        col = QVBoxLayout(wrap)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(1)
        radio.setStyleSheet(
            f"QRadioButton {{ color: {PALETTE['text_secondary']}; font-size: 11px; background: transparent; spacing: 8px; }}"
            f"QRadioButton::indicator {{ width: 16px; height: 16px; border-radius: 10px; "
            f"border: 2px solid #4b5563; background-color: {PALETTE['bg_surface']}; }}"  # #4b5563 has no palette match, left as-is
            "QRadioButton::indicator:checked { border: 2px solid #4f46e5; "  # indigo accent, no palette match, left as-is
            f"background-color: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5, "
            f"stop:0 #4f46e5, stop:0.5 #4f46e5, stop:0.6 {PALETTE['bg_surface']}, stop:1 {PALETTE['bg_surface']}); }}"
            f"QRadioButton::indicator:hover {{ border: 2px solid {PALETTE['text_dim']}; }}"
        )
        col.addWidget(radio)
        sub_lbl = QLabel(sub_text)
        sub_lbl.setWordWrap(True)
        sub_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 9px; background: transparent; margin-left: 22px;")
        col.addWidget(sub_lbl)
        return wrap
    
    def _render_format_chips(self):
        while self.formats_row.count():
            item = self.formats_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for fmt in self._selected_formats:
            chip = QFrame()
            chip.setObjectName("FormatChip")
            chip.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            chip.setStyleSheet(
                f"QFrame#FormatChip {{ background-color: {PALETTE['bg_hover']}; border: none; border-radius: 6px; }}"
            )
            chip_row = QHBoxLayout(chip)
            chip_row.setContentsMargins(10, 5, 8, 5)
            chip_row.setSpacing(6)
            lbl = QLabel(fmt)
            lbl.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 10px; font-weight: 600; background: transparent; border: none;")
            chip_row.addWidget(lbl)
            remove_btn = QPushButton("\u00d7")
            remove_btn.setFixedSize(14, 14)
            remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            remove_btn.setStyleSheet(
                f"QPushButton {{ background: transparent; border: none; color: {PALETTE['text_dim']}; font-weight: bold; padding: 0px; }}"
                f"QPushButton:hover {{ color: {PALETTE['red']}; background: transparent; border: none; }}"
            )
            remove_btn.clicked.connect(lambda _checked=False, f=fmt: self._remove_format(f))
            chip_row.addWidget(remove_btn)
            self.formats_row.addWidget(chip)

        remaining = [f for f in _ALL_FILE_FORMATS if f not in self._selected_formats]
        if remaining:
            add_combo = QComboBox()
            add_combo.setObjectName("RowModeCombo")
            add_combo.addItem("+ Add format")
            add_combo.addItems(remaining)
            add_combo.setFixedWidth(110)
            add_combo.setStyleSheet("background: transparent; border: none;")
            add_combo.currentIndexChanged.connect(
                lambda idx, c=add_combo: self._add_format(c.currentText()) if idx > 0 else None
            )
            self.formats_row.addWidget(add_combo)
        self.formats_row.addStretch()

    def _remove_format(self, fmt: str):
        if fmt in self._selected_formats:
            self._selected_formats.remove(fmt)
        self._render_format_chips()

    def _add_format(self, fmt: str):
        if fmt and fmt not in self._selected_formats:
            self._selected_formats.append(fmt)
        self._render_format_chips()

    def _save_settings(self):
        self.settings["approval_mode"] = "Manual Approval" if self.approval_group.checkedId() == 0 else "Automatic Approval"
        self.settings["receipt_required"] = self.receipt_group.checkedId() == 0
        self.settings["max_receipt_size_mb"] = int(self.max_size_combo.currentText().split()[0])
        self.settings["payment_expiry_hours"] = int(self.expiry_combo.currentText().split()[0])
        self.settings["accepted_formats"] = list(self._selected_formats)
        InfoDialog.show(
            self, "Settings Saved", "Your payment settings were saved.",
            icon_name='fa5s.check-circle', success=True,
        )