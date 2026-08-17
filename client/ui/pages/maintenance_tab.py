"""
maintenance_tab.py

MaintenanceTab: the admin-only "Maintenance" tab inside Settings. Lets an
admin flip Maintenance Mode on/off, edit the message users see, set how
long a maintenance run lasts, set a few options (admin access, countdown
timer), and fire a one-off notification.

Everything here is in-memory / prototype-only (same as the rest of the
app) -- "Send Notification" doesn't send anything, etc. Wire these up to
a real backend/settings store later.

Depends on: config (colors), widgets (LeadCheckBox, ToggleSwitch).
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QLineEdit, QTextEdit, QPushButton, QComboBox,
    QSizePolicy,
)
from PySide6.QtCore import Qt, QDateTime
import qtawesome as qta

from core.config import PALETTE
from core import config
from ui.components.widgets import LeadCheckBox, ToggleSwitch, dash_card
from core.maintenance_state import get_maintenance_state, update_maintenance_state

def _card(title: str = None) -> tuple:
    """Thin wrapper over widgets.dash_card() (see PROGRESS.md, Phase 1c).
    Passes explicit_style=True to preserve the WA_StyledBackground=True
    this card originally set explicitly (confirmed load-bearing, unlike
    the NoFrame call alongside it which was already QFrame's default)."""
    return dash_card(title, margins=(18, 16, 18, 16), explicit_style=True)


def _field_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(f"color: {PALETTE['text_muted']}; font-size: 10px; background: transparent;")
    return lbl


class MaintenanceTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = get_maintenance_state()  # load persisted values on open

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 12, 0, 0)
        outer.setSpacing(14)

        outer.addWidget(self._build_mode_card())
        outer.addWidget(self._build_info_banner())

        top_row = QHBoxLayout()
        top_row.setSpacing(14)
        top_row.addWidget(self._build_message_card(), stretch=1)
        top_row.addWidget(self._build_duration_card(), stretch=1)
        outer.addLayout(top_row)

        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(14)
        bottom_row.addWidget(self._build_options_card(), stretch=3)
        bottom_row.addWidget(self._build_notify_card(), stretch=2)
        outer.addLayout(bottom_row)

    # ------------------------------------------------------------------
    def _build_mode_card(self) -> QWidget:
        card, layout = _card()
        row = QHBoxLayout()
        row.setSpacing(12)

        icon_wrap = QLabel()
        icon_wrap.setFixedSize(36, 36)
        icon_wrap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_wrap.setStyleSheet(f"background-color: {PALETTE['yellow_soft']}; border-radius: 18px;")
        icon_wrap.setPixmap(qta.icon('fa5s.tools', color=PALETTE["yellow"]).pixmap(16, 16))
        row.addWidget(icon_wrap)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        title_lbl = QLabel("Maintenance Mode")
        title_lbl.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 14px; font-weight: 600; background: transparent;")
        text_col.addWidget(title_lbl)
        desc_lbl = QLabel("Enable maintenance mode to temporarily disable access to the platform while you perform updates or system maintenance.")
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 11px; background: transparent;")
        text_col.addWidget(desc_lbl)
        row.addLayout(text_col, stretch=1)

        toggle_col = QVBoxLayout()
        toggle_col.setSpacing(4)
        toggle_col.setAlignment(Qt.AlignmentFlag.AlignRight)
        top_lbl = QLabel("Maintenance Mode")
        top_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        top_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 10px; background: transparent;")
        toggle_col.addWidget(top_lbl)

        toggle_row = QHBoxLayout()
        toggle_row.setSpacing(8)
        toggle_row.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.status_lbl = QLabel("Disabled")
        self.status_lbl.setStyleSheet(f"color: {PALETTE['text_muted']}; font-size: 11px; font-weight: 600; background: transparent;")
        self.mode_toggle = ToggleSwitch(checked=self._state["enabled"], on_color=PALETTE["yellow_solid"])
        self.mode_toggle.toggled.connect(self._on_mode_toggled)
        toggle_row.addWidget(self.status_lbl)
        toggle_row.addWidget(self.mode_toggle)
        toggle_col.addLayout(toggle_row)
        row.addLayout(toggle_col)

        layout.addLayout(row)
        return card

    def _on_mode_toggled(self, checked: bool):
        self.status_lbl.setText("Enabled" if checked else "Disabled")
        self.status_lbl.setStyleSheet(
            f"color: {PALETTE['yellow_solid'] if checked else PALETTE['text_muted']}; font-size: 11px; "
            "font-weight: 600; background: transparent;"
        )
        fields = {"enabled": checked}
        if checked:
            # Every time maintenance is switched ON, push end_datetime out
            # by the selected duration from *now* -- so it's never stuck
            # showing a stale fixed window from whenever it was last set.
            minutes = self._state.get("duration_minutes", 240)
            fields["end_datetime"] = QDateTime.currentDateTime().addSecs(minutes * 60)
        self._state = update_maintenance_state(**fields)
        if hasattr(self, "duration_hint_lbl"):
            self._update_duration_hint()

    def _build_info_banner(self) -> QWidget:
        banner = QFrame()
        banner.setObjectName("InfoBanner")
        banner.setStyleSheet(
            f"QFrame#InfoBanner {{ background-color: {config.rgba_from_hex(PALETTE['blue'], 0.06)}; "
            f"border: 1px solid {config.rgba_from_hex(PALETTE['blue'], 0.25)}; border-radius: 8px; }}"
        )
        row = QHBoxLayout(banner)
        row.setContentsMargins(14, 10, 14, 10)
        row.setSpacing(10)
        icon_lbl = QLabel()
        icon_lbl.setStyleSheet("background: transparent;")
        icon_lbl.setPixmap(qta.icon('fa5s.info-circle', color=PALETTE["blue"]).pixmap(13, 13))
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignTop)
        row.addWidget(icon_lbl)
        text_lbl = QLabel(
            "When maintenance mode is enabled, all users will see the maintenance "
            "page instead of the platform. Administrators will still be able to "
            "access the system."
        )
        text_lbl.setWordWrap(True)
        text_lbl.setStyleSheet(f"color: {PALETTE['blue']}; font-size: 11px; background: transparent;")
        row.addWidget(text_lbl, stretch=1)
        return banner

    # ------------------------------------------------------------------
    def _build_message_card(self) -> QWidget:
        card, layout = _card("Maintenance Message")

        layout.addWidget(_field_label("Message Title"))
        self.title_edit = QLineEdit(self._state["title"])
        self.title_edit.setMaxLength(60)
        layout.addWidget(self.title_edit)

        layout.addSpacing(6)
        layout.addWidget(_field_label("Message Description"))
        self.desc_edit = QTextEdit(self._state["description"])
        self.desc_edit.setFixedHeight(80)
        self.desc_edit.setStyleSheet(
            f"QTextEdit {{ background-color: {PALETTE['bg_surface']}; border: 1px solid {PALETTE['border']}; "
            f"border-radius: 6px; padding: 8px 10px; color: {PALETTE['text_secondary']}; font-size: 12px; }}"
            f"QTextEdit:focus {{ border: 1px solid {PALETTE['accent']}; }}"
        )
        layout.addWidget(self.desc_edit)
        layout.addStretch()
        self.title_edit.textChanged.connect(
            lambda t: update_maintenance_state(title=t)
        )
        self.desc_edit.textChanged.connect(
            lambda: update_maintenance_state(description=self.desc_edit.toPlainText())
        )
        return card

    # Preset choices shown in the duration dropdown -- (label, minutes).
    _DURATION_OPTIONS = [
        ("30 minutes", 30),
        ("1 hour", 60),
        ("2 hours", 120),
        ("4 hours", 240),
        ("8 hours", 480),
        ("12 hours", 720),
        ("24 hours", 1440),
    ]

    def _build_duration_card(self) -> QWidget:
        card, layout = _card("Maintenance Duration")
        sub = QLabel("How long maintenance mode stays active once you turn it on.")
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 10px; background: transparent;")
        layout.addWidget(sub)

        layout.addSpacing(4)
        layout.addWidget(_field_label("Duration"))
        self.duration_combo = QComboBox()
        self.duration_combo.addItems([label for label, _ in self._DURATION_OPTIONS])
        current_minutes = self._state.get("duration_minutes", 240)
        for label, minutes in self._DURATION_OPTIONS:
            if minutes == current_minutes:
                self.duration_combo.setCurrentText(label)
                break
        self.duration_combo.currentIndexChanged.connect(self._on_duration_changed)
        layout.addWidget(self.duration_combo)

        layout.addSpacing(10)
        self.duration_hint_lbl = QLabel()
        self.duration_hint_lbl.setWordWrap(True)
        self.duration_hint_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 10px; background: transparent;")
        layout.addWidget(self.duration_hint_lbl)
        self._update_duration_hint()

        layout.addStretch()
        return card

    def _on_duration_changed(self, index: int):
        _, minutes = self._DURATION_OPTIONS[index]
        self._state = update_maintenance_state(duration_minutes=minutes)
        # Maintenance is already running -- push the live end time out too,
        # instead of only applying the new duration on the *next* enable.
        if self._state.get("enabled"):
            self._state = update_maintenance_state(
                end_datetime=QDateTime.currentDateTime().addSecs(minutes * 60)
            )
        self._update_duration_hint()

    def _update_duration_hint(self):
        if self._state.get("enabled"):
            self.duration_hint_lbl.setText(
                "Maintenance is currently running -- changing this updates the countdown immediately."
            )
        else:
            self.duration_hint_lbl.setText(
                "Applied the moment you switch Maintenance Mode on above."
            )

    # ------------------------------------------------------------------
    def _build_options_card(self) -> QWidget:
        card, layout = _card("Maintenance Options")

        rows = [
            self._option_row(
                "Allow admin access during maintenance",
                "Admins will still be able to access the system",
                checked=self._state.get("allow_admin_access", True),
                state_key="allow_admin_access",
            ),
            self._option_row(
                "Show countdown timer",
                "Display remaining time to users",
                checked=self._state.get("show_countdown", True),
                trailing=self._countdown_combo(),
                state_key="show_countdown",
            ),
        ]
        for i, row in enumerate(rows):
            layout.addWidget(row)
            if i < len(rows) - 1:
                divider = QFrame()
                divider.setFixedHeight(1)
                divider.setStyleSheet(f"background-color: {PALETTE['border']}; border: none;")
                layout.addWidget(divider)
        layout.addStretch()
        return card

    def _option_row(self, title: str, sub: str, checked: bool, trailing: QWidget = None, state_key: str = None) -> QWidget:
        row_wrap = QFrame()
        row_wrap.setStyleSheet("background: transparent; border: none;")
        row = QHBoxLayout(row_wrap)
        row.setContentsMargins(0, 6, 0, 6)
        row.setSpacing(10)

        box = LeadCheckBox(checked=checked)
        if state_key:
            box.toggled.connect(lambda on: update_maintenance_state(**{state_key: on}))
        row.addWidget(box)

        col = QVBoxLayout()
        col.setSpacing(1)
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 11px; font-weight: 500; background: transparent;")
        col.addWidget(title_lbl)
        sub_lbl = QLabel(sub)
        sub_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 10px; background: transparent;")
        col.addWidget(sub_lbl)
        row.addLayout(col, stretch=1)

        if trailing is not None:
            row.addWidget(trailing)
        return row_wrap

    def _countdown_combo(self) -> QComboBox:
        combo = QComboBox()
        combo.addItems(["Hours & Minutes", "Days, Hours & Minutes", "Minutes only"])
        combo.setCurrentText(self._state.get("countdown_format", "Hours & Minutes"))
        combo.setFixedWidth(150)
        combo.currentTextChanged.connect(lambda text: update_maintenance_state(countdown_format=text))
        # The parent option row (row_wrap) sets a bare "background: transparent;
        # border: none;" stylesheet, which Qt cascades down to unstyled children --
        # that was stripping this combo's border/background even though it never
        # asked for it. Re-assert *exactly* the same rule config.py uses for every
        # other plain QComboBox (duration_combo included), down-arrow image and
        # all -- the earlier hand-rolled version above left out the drop-down
        # arrow image/width, which is what left a stray gray sliver along the
        # top/bottom of the arrow button (Qt falls back to native OS chrome for
        # any subcontrol piece it isn't explicitly told how to paint).
        combo.setStyleSheet(
            f"QComboBox {{ background-color: {PALETTE['bg_surface']}; "
            f"border: 1px solid {PALETTE['border']}; border-radius: 6px; "
            f"padding: 0px 10px; color: {PALETTE['text_secondary']}; font-size: 12px; "
            f"selection-background-color: {PALETTE['accent_soft']}; "
            f"selection-color: {PALETTE['accent_text']}; }}"
            f"QComboBox:focus {{ border: 1px solid {PALETTE['accent']}; "
            f"background-color: {PALETTE['bg_app']}; }}"
            f"QComboBox:hover {{ border: 1px solid {PALETTE['text_dim']}; }}"
            f"QComboBox::drop-down {{ border: none; width: 24px; }}"
            f"QComboBox::down-arrow {{ image: url({config._asset_url('arrow_combo.png')}); "
            f"width: 10px; height: 8px; margin-right: 8px; }}"
            f"QComboBox QAbstractItemView {{ background-color: {PALETTE['bg_surface']}; "
            f"border: 1px solid {PALETTE['border']}; "
            f"selection-background-color: {PALETTE['bg_hover']}; "
            f"color: {PALETTE['text_secondary']}; outline: none; }}"
        )
        return combo

    def _build_notify_card(self) -> QWidget:
        card, layout = _card("Notify Users (Optional)")
        sub = QLabel("Send a notification to all users about the maintenance.")
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 10px; background: transparent;")
        layout.addWidget(sub)
        layout.addSpacing(6)

        notify_btn = QPushButton(qta.icon('fa5s.paper-plane', color=PALETTE["text_secondary"]), " Send Notification")
        notify_btn.setObjectName("OutlineBtn")
        notify_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(notify_btn)
        layout.addStretch()
        return card