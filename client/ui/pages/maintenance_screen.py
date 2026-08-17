"""
maintenance_screen.py

MaintenanceScreen: full-window page shown to a logged-in *user* (not
admin) whenever MAINTENANCE_STATE["enabled"] is True. Mirrors the
"We're Currently Under Maintenance" mockup -- logo, wrench icon, title,
description, live countdown card, and a footer.

Depends on: config (colors, LOGO_PATH), maintenance_state.
"""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame
from PySide6.QtCore import Qt, QTimer, QDateTime
from PySide6.QtGui import QPixmap
import qtawesome as qta

from core.config import PALETTE, LOGO_PATH
from core import config
from core.maintenance_state import get_maintenance_state

class MaintenanceScreen(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background-color: {PALETTE['bg_app']};")

        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.setContentsMargins(40, 40, 40, 40)
        outer.setSpacing(18)

        # Logo
        logo_lbl = QLabel()
        logo_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_pixmap = QPixmap(LOGO_PATH)
        if not logo_pixmap.isNull():
            logo_lbl.setPixmap(
                logo_pixmap.scaledToHeight(90, Qt.TransformationMode.SmoothTransformation)
            )
        logo_lbl.setStyleSheet("background: transparent;")
        outer.addWidget(logo_lbl)

        # Wrench icon circle
        icon_wrap = QLabel()
        icon_wrap.setFixedSize(64, 64)
        icon_wrap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_wrap.setStyleSheet(f"background-color: {PALETTE['accent_soft']}; border-radius: 32px;")
        icon_wrap.setPixmap(qta.icon('fa5s.tools', color=PALETTE["red_solid"]).pixmap(28, 28))
        outer.addWidget(icon_wrap, alignment=Qt.AlignmentFlag.AlignCenter)

        # Title + description (read from shared state)
        self.title_lbl = QLabel(get_maintenance_state()["title"])
        self.title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_lbl.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 20px; font-weight: 700; background: transparent;")
        outer.addWidget(self.title_lbl)

        self.desc_lbl = QLabel(get_maintenance_state()["description"])
        self.desc_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.desc_lbl.setWordWrap(True)
        self.desc_lbl.setStyleSheet(f"color: {PALETTE['text_muted']}; font-size: 12px; background: transparent;")
        outer.addWidget(self.desc_lbl)

        # Countdown card
        card = QFrame()
        card.setObjectName("MaintCard")
        card.setFixedWidth(380)
        card.setStyleSheet(
            f"QFrame#MaintCard {{ background-color: {PALETTE['bg_surface']}; "
            f"border: 1px solid {PALETTE['border']}; border-radius: 10px; }}"
        )
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 18, 20, 18)
        card_layout.setSpacing(12)

        row = QHBoxLayout()
        self.cal_lbl = QLabel()
        self.cal_lbl.setPixmap(qta.icon('fa5s.calendar-alt', color=PALETTE["red"]).pixmap(16, 16))
        self.cal_lbl.setStyleSheet("background: transparent;")
        row.addWidget(self.cal_lbl)
        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        self.est_lbl = QLabel("Estimated Completion")
        self.est_lbl.setStyleSheet(f"color: {PALETTE['text_muted']}; font-size: 10px; background: transparent;")
        text_col.addWidget(self.est_lbl)
        self.countdown_lbl = QLabel("--")
        self.countdown_lbl.setStyleSheet(f"color: {PALETTE['red_solid']}; font-size: 20px; font-weight: 700; background: transparent;")
        text_col.addWidget(self.countdown_lbl)
        row.addLayout(text_col)
        row.addStretch()
        card_layout.addLayout(row)

        self.end_date_lbl = QLabel("")
        self.end_date_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 11px; background: transparent;")
        card_layout.addWidget(self.end_date_lbl)

        self.divider = QFrame()
        self.divider.setFixedHeight(1)
        self.divider.setStyleSheet(f"background-color: {PALETTE['border']};")
        card_layout.addWidget(self.divider)

        whats_title = QLabel("What's happening?")
        whats_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        whats_title.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 12px; font-weight: 600; background: transparent;")
        card_layout.addWidget(whats_title)

        whats_sub = QLabel("We're performing scheduled system updates to serve you better.")
        whats_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        whats_sub.setWordWrap(True)
        whats_sub.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 11px; background: transparent;")
        card_layout.addWidget(whats_sub)

        banner = QFrame()
        banner.setObjectName("MaintBanner")
        banner.setStyleSheet(
            f"QFrame#MaintBanner {{ background-color: {config.rgba_from_hex(PALETTE['accent'], 0.06)}; "
            f"border: 1px solid {config.rgba_from_hex(PALETTE['accent'], 0.3)}; border-radius: 8px; }}"
        )
        banner_row = QHBoxLayout(banner)
        banner_row.setContentsMargins(12, 8, 12, 8)
        info_icon = QLabel()
        info_icon.setPixmap(qta.icon('fa5s.info-circle', color=PALETTE["red"]).pixmap(12, 12))
        info_icon.setStyleSheet("background: transparent;")
        banner_row.addWidget(info_icon)
        thanks_lbl = QLabel("Thank you for your patience and understanding.")
        thanks_lbl.setStyleSheet(f"color: {PALETTE['accent_text_hover']}; font-size: 11px; background: transparent;")
        banner_row.addWidget(thanks_lbl)
        card_layout.addWidget(banner)

        outer.addWidget(card, alignment=Qt.AlignmentFlag.AlignCenter)

        footer = QLabel("© 2026 Sinulead. All rights reserved.")
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        footer.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 10px; background: transparent;")
        outer.addWidget(footer)

        # Live countdown, ticks every second
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(1000)
        self._refresh()

    def _refresh(self):
        state = get_maintenance_state()
        end_dt = state.get("end_datetime")
        if not end_dt:
            self.countdown_lbl.setText("--")
            return
        show_countdown = state.get("show_countdown", True)
        self.countdown_lbl.setVisible(show_countdown)
        self.end_date_lbl.setVisible(show_countdown)
        self.cal_lbl.setVisible(show_countdown)
        self.est_lbl.setVisible(show_countdown)
        self.divider.setVisible(show_countdown)
        if show_countdown:
            secs = QDateTime.currentDateTime().secsTo(end_dt)
            fmt = state.get("countdown_format", "Hours & Minutes")
            if secs <= 0:
                self.countdown_lbl.setText("Any moment now")
            elif fmt == "Minutes only":
                total_m = secs // 60
                self.countdown_lbl.setText(f"{total_m}m")
            elif fmt == "Days, Hours & Minutes":
                d, rem = divmod(secs, 86400)
                h, rem = divmod(rem, 3600)
                m, s = divmod(rem, 60)
                self.countdown_lbl.setText(f"{d}d {h}h {m}m")
            else:  # "Hours & Minutes"
                h, rem = divmod(secs, 3600)
                m, s = divmod(rem, 60)
                self.countdown_lbl.setText(f"{h}h {m}m {s}s")
            self.end_date_lbl.setText(end_dt.toString("MMM d, yyyy hh:mm AP"))
        # keep title/description live in case admin edits them mid-outage
        self.title_lbl.setText(state["title"])
        self.desc_lbl.setText(state["description"])