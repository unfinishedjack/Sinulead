"""
pricing_tab.py

PricingTab: the admin-only "Pricing" tab inside Settings. Lets an admin
retune the credit costs that used to be fixed constants
(SEARCH_COST / LEAD_UNLOCK_FIELD_COST in the old server/app/config.py) --
now backed by GET/PATCH /pricing and /admin/pricing (see
server/app/routers/pricing.py, db.PricingSettings).

Unlocking a phone and unlocking an email used to share one price
(lead_unlock_field_cost); that's now two independent, independently
admin-editable prices (lead_unlock_email_cost / lead_unlock_phone_cost),
each with its own spinbox below.

Same load-on-open / edit / Save button shape as maintenance_tab.py, just
with a single form instead of several cards.

Depends on: config (colors), widgets (dash_card), core.api_client
(get_pricing_settings/update_pricing_settings), dialogs (InfoDialog).
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFormLayout, QSpinBox,
    QPushButton,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QShortcut, QKeySequence
import qtawesome as qta

from core.config import PALETTE
from core.api_client import get_pricing_settings, update_pricing_settings, ApiError
from ui.components.widgets import dash_card
from ui.dialogs.dialogs import InfoDialog


def _card(title: str = None):
    return dash_card(title, margins=(18, 16, 18, 16), explicit_style=True)


def _field_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(f"color: {PALETTE['text_muted']}; font-size: 10px; background: transparent;")
    return lbl


class PricingTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._loaded_ok = True
        try:
            self._state = get_pricing_settings()
        except ApiError as e:
            # Same "don't crash the tab, just show 0/placeholder + a
            # banner" fallback used elsewhere in Settings when a GET
            # fails on open (e.g. server unreachable) -- Save still
            # attempts a fresh PATCH rather than blindly trusting stale
            # local values.
            self._state = {
                "search_cost": 0,
                "lead_unlock_email_cost": 0,
                "lead_unlock_phone_cost": 0,
                "export_cost": 0,
            }
            self._loaded_ok = False
            self._load_error = str(e)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 12, 0, 0)
        outer.setSpacing(14)

        if not self._loaded_ok:
            outer.addWidget(self._build_error_banner())

        outer.addWidget(self._build_info_banner())
        outer.addWidget(self._build_pricing_card())
        outer.addStretch()

    # ------------------------------------------------------------------
    def _build_error_banner(self) -> QWidget:
        card, layout = _card()
        row = QHBoxLayout()
        icon_lbl = QLabel()
        icon_lbl.setStyleSheet("background: transparent;")
        icon_lbl.setPixmap(qta.icon('fa5s.exclamation-triangle', color=PALETTE["red"]).pixmap(16, 16))
        row.addWidget(icon_lbl)
        msg = QLabel(f"Couldn't load current prices: {self._load_error}")
        msg.setWordWrap(True)
        msg.setStyleSheet(f"color: {PALETTE['red']}; font-size: 11px; background: transparent;")
        row.addWidget(msg, stretch=1)
        layout.addLayout(row)
        return card

    def _build_info_banner(self) -> QWidget:
        card, layout = _card()
        row = QHBoxLayout()
        row.setSpacing(12)

        icon_wrap = QLabel()
        icon_wrap.setFixedSize(36, 36)
        icon_wrap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_wrap.setStyleSheet(f"background-color: {PALETTE['yellow_soft']}; border-radius: 18px;")
        icon_wrap.setPixmap(qta.icon('fa5s.coins', color=PALETTE["yellow"]).pixmap(16, 16))
        row.addWidget(icon_wrap)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        title = QLabel("Credit Pricing")
        title.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 13px; font-weight: 600; background: transparent;")
        text_col.addWidget(title)
        sub = QLabel(
            "Controls how many credits a search or a contact unlock costs, "
            "for every non-admin account. Takes effect immediately on Save "
            "-- no restart needed."
        )
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color: {PALETTE['text_muted']}; font-size: 11px; background: transparent;")
        text_col.addWidget(sub)
        row.addLayout(text_col, stretch=1)

        layout.addLayout(row)
        return card

    def _build_pricing_card(self) -> QWidget:
        card, layout = _card("Costs")

        form = QFormLayout()
        form.setSpacing(10)

        self.search_cost_spin = QSpinBox()
        self.search_cost_spin.setRange(0, 100000)
        self.search_cost_spin.setSuffix(" credits")
        self.search_cost_spin.setValue(self._state["search_cost"])
        form.addRow(_field_label("Cost to run a search"), self.search_cost_spin)

        self.email_cost_spin = QSpinBox()
        self.email_cost_spin.setRange(0, 100000)
        self.email_cost_spin.setSuffix(" credits")
        self.email_cost_spin.setValue(self._state["lead_unlock_email_cost"])
        form.addRow(_field_label("Cost to unlock an email"), self.email_cost_spin)

        self.phone_cost_spin = QSpinBox()
        self.phone_cost_spin.setRange(0, 100000)
        self.phone_cost_spin.setSuffix(" credits")
        self.phone_cost_spin.setValue(self._state["lead_unlock_phone_cost"])
        form.addRow(_field_label("Cost to unlock a phone"), self.phone_cost_spin)

        self.export_cost_spin = QSpinBox()
        self.export_cost_spin.setRange(0, 100000)
        self.export_cost_spin.setSuffix(" credits")
        self.export_cost_spin.setValue(self._state["export_cost"])
        form.addRow(_field_label("Cost to export leads to a file"), self.export_cost_spin)

        layout.addLayout(form)

        hint = QLabel(
            "Admin and superadmin accounts always run searches, unlock "
            "contacts, and export for free, regardless of these values. "
            "A search that finds nothing, or an export that fails to "
            "write, is never charged either way."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 11px; background: transparent;")
        layout.addWidget(hint)

        save_row = QHBoxLayout()
        save_row.addStretch()
        save_btn = QPushButton(qta.icon('fa5s.save', color="#ffffff"), " Save Pricing")
        save_btn.setObjectName("RedBtn")
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.clicked.connect(self._on_save)
        save_row.addWidget(save_btn)
        layout.addLayout(save_row)

        # Enter/Return anywhere on this card (e.g. while a spin box has
        # focus) saves too, same as clicking "Save Pricing".
        for key in (QKeySequence(Qt.Key.Key_Return), QKeySequence(Qt.Key.Key_Enter)):
            shortcut = QShortcut(key, card)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(self._on_save)

        return card

    # ------------------------------------------------------------------
    def _on_save(self):
        try:
            self._state = update_pricing_settings(
                search_cost=self.search_cost_spin.value(),
                lead_unlock_email_cost=self.email_cost_spin.value(),
                lead_unlock_phone_cost=self.phone_cost_spin.value(),
                export_cost=self.export_cost_spin.value(),
            )
        except ApiError as e:
            InfoDialog.show(self, "Save failed", str(e), icon_name='fa5s.times-circle', success=False)
            return
        InfoDialog.show(self, "Saved", "Pricing updated. New searches, unlocks, and exports will use these values.")