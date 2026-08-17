"""
widgets.py

Small, reusable pieces used inside bigger things: table-cell builders
(checkbox, rating, website, email, locked-contact, status pill), the
hand-painted LeadCheckBox, icon_label helper, plus the two card-style
widgets used in the sidebar/queries panel (SearchQueryCard, UserAccountBox).

Depends on: config (colors), models (contact cost), dialogs (profile /
confirm dialogs opened from UserAccountBox).
"""

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QFrame,
    QAbstractButton, QMenu, QSizePolicy, QDialog, QLineEdit,
    QApplication, QToolButton, QStyle, QGraphicsOpacityEffect,
)
from PySide6.QtCore import (
    Qt, QRectF, QPoint, QSize, Signal, QTimer, QEvent,
    QPropertyAnimation, QEasingCurve,
)
from PySide6.QtGui import QColor, QPainter, QPen, QBrush, QPainterPath, QAction, QFontMetrics, QPixmap
import qtawesome as qta

from core import config
from core.config import PALETTE

# Billing is still mock data (out of scope for the users-table migration --
# see PROGRESS.md's "Not this file's job" section) -- billing.py still has
# its own separate "plan" placeholder for the Billing page. The sidebar/
# profile UserAccountBox itself no longer shows a plan field; it shows the
# real credits balance (passed in from Dashboard) instead.
from core.api_client import (
    get_me, update_me, ApiError, get_my_progress, record_my_progress,
)


class SearchLineEdit(QLineEdit):
    """QLineEdit with a real QToolButton embedded as its search icon,
    instead of a plain addAction() icon. addAction() icons render fine
    but can't have their own hover cursor or hover color -- a QToolButton
    can, so this swaps one in and keeps it pinned to the left edge as the
    line edit resizes.

    Deliberately does NOT filter-as-you-type -- emits search_triggered
    only when Enter is pressed or the icon is clicked, so pages with a
    live-filtered table (Search Leads, Exports, Transactions, Rewards,
    Users) only ever re-filter on an explicit action, not on every
    keystroke. Each page wires search_triggered + returnPressed to its
    own filter method."""

    search_triggered = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self._icon_normal = qta.icon('fa5s.search', color=PALETTE['text_dim'])
        self._icon_hover = qta.icon('fa5s.search', color=PALETTE['accent'])

        self.search_button = QToolButton(self)
        self.search_button.setIcon(self._icon_normal)
        self.search_button.setIconSize(QSize(13, 13))
        self.search_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.search_button.setStyleSheet(
            "QToolButton { border: none; background: transparent; padding: 0px; }"
        )
        self.search_button.setToolTip("Search")
        self.search_button.installEventFilter(self)
        self.search_button.clicked.connect(self.search_triggered.emit)

        frame_width = self.style().pixelMetric(QStyle.PixelMetric.PM_DefaultFrameWidth)
        btn_width = self.search_button.sizeHint().width()
        self.setTextMargins(btn_width + frame_width + 4, 0, 0, 0)

    def eventFilter(self, obj, event):
        if obj is self.search_button:
            if event.type() == QEvent.Type.Enter:
                self.search_button.setIcon(self._icon_hover)
            elif event.type() == QEvent.Type.Leave:
                self.search_button.setIcon(self._icon_normal)
        return super().eventFilter(obj, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        frame_width = self.style().pixelMetric(QStyle.PixelMetric.PM_DefaultFrameWidth)
        size = self.search_button.sizeHint()
        y = (self.rect().height() - size.height()) // 2
        self.search_button.move(frame_width + 4, y)


def circular_avatar_pixmap(avatar_filename: str | None, size: int) -> QPixmap:
    """Loads the given avatar (assets/avatarN.png -- config.avatar_path
    resolves None/unknown values to DEFAULT_AVATAR) and masks it into a
    circle at `size` px. Shared by the sidebar UserAccountBox,
    ViewProfileDialog, and EditProfileDialog's avatar picker so every
    place an avatar shows up renders it identically."""
    pix = QPixmap(config.avatar_path(avatar_filename))
    scaled = pix.scaled(
        size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
    )
    canvas = QPixmap(size, size)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addEllipse(0, 0, size, size)
    painter.setClipPath(path)
    x = (size - scaled.width()) // 2
    y = (size - scaled.height()) // 2
    painter.drawPixmap(x, y, scaled)
    painter.end()
    return canvas


def status_pill(status: str) -> QWidget:
    """
    Small status badge (colored background only around the text/icon,
    not the whole cell) centered inside a transparent outer wrapper.
    """
    is_open = status == "Open"
    accent = PALETTE["green"] if is_open else PALETTE["red"]
    bg = PALETTE["green_soft"] if is_open else PALETTE["red_soft"]

    dot_lbl = QLabel()
    dot_lbl.setStyleSheet("background: transparent;")
    dot_lbl.setPixmap(qta.icon('fa5s.circle', color=accent).pixmap(8, 8))

    text_lbl = QLabel(status)
    text_lbl.setStyleSheet(f"color: {accent}; font-size: 11px; background: transparent;")

    # Inner pill: sized to fit its content only, colored background + rounded corners
    pill = QFrame()
    pill.setStyleSheet(f"background-color: {bg}; border-radius: 4px;")
    pill.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    pill_layout = QHBoxLayout(pill)
    pill_layout.setContentsMargins(8, 3, 8, 3)
    pill_layout.setSpacing(5)
    pill_layout.addWidget(dot_lbl)
    pill_layout.addWidget(text_lbl)

    # Outer wrapper: fully transparent, just centers the pill in the cell
    outer = QWidget()
    outer.setStyleSheet("background: transparent;")
    outer_layout = QHBoxLayout(outer)
    outer_layout.setContentsMargins(0, 0, 0, 0)
    outer_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
    outer_layout.addWidget(pill)

    return outer


def status_badge(status: str, color_map: dict, align: str = "center", icon: str | None = None) -> QWidget:
    """
    Plain-text status pill (no leading dot) centered inside a transparent
    outer wrapper. Same visual recipe as `status_pill()` above, minus the
    dot icon -- this is the shape used on Billing/Exports/Rewards/
    Transactions/Users, each of which just supplies its own status->color
    lookup via `color_map` (e.g. {"Completed": (accent, bg), ...}).

    Consolidated here from five near-identical `_status_pill()` copies
    that used to live one per page file (see PROGRESS.md, Phase 1a).

    `icon`: optional qtawesome icon name (e.g. 'fa5s.circle-notch') shown
    before the label, in the pill's accent color. Added for exports.py's
    "Processing" status, which shows a spinning-icon dot the plain badge
    didn't support (see PROGRESS.md, Phase 1b).
    """
    accent, bg = color_map.get(
        status, (PALETTE["text_muted"], config.rgba_from_hex(PALETTE["text_muted"], 0.12))
    )

    pill = QFrame()
    pill.setStyleSheet(f"background-color: {bg}; border-radius: 4px;")
    pill.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    row = QHBoxLayout(pill)
    row.setContentsMargins(8, 3, 8, 3)
    row.setSpacing(5)
    if icon:
        dot = QLabel()
        dot.setStyleSheet("background: transparent;")
        dot.setPixmap(qta.icon(icon, color=accent).pixmap(9, 9))
        row.addWidget(dot)
    lbl = QLabel(status)
    lbl.setStyleSheet(f"color: {accent}; font-size: 10px; font-weight: 600; background: transparent;")
    row.addWidget(lbl)

    outer = QWidget()
    outer.setStyleSheet("background: transparent;")
    outer_row = QHBoxLayout(outer)
    outer_row.setContentsMargins(0, 0, 0, 0)
    if align == "right":
        outer_row.setAlignment(Qt.AlignmentFlag.AlignRight)
    elif align == "left":
        outer_row.setAlignment(Qt.AlignmentFlag.AlignLeft)
    else:
        outer_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
    outer_row.addWidget(pill)
    return outer


def dash_card(title: str = None, margins: tuple = (16, 14, 16, 14), explicit_style: bool = False) -> tuple[QFrame, QVBoxLayout]:
    """
    A "DashCard" frame (background/border/radius come from the #DashCard
    QSS rule in config.py), optionally with a title label already added,
    plus the QVBoxLayout body callers append their own content into.

    Consolidated here from six near-identical `_card()` copies that used
    to live one per page file (see PROGRESS.md, Phase 1c). Four of the
    six were byte-identical with margins (16,14,16,14); maintenance_tab.py
    and settings_page.py used (18,16,18,16) instead -- pass that via
    `margins`. overview.py's version made `title` required rather than
    optional; that's just a call-site choice, this signature still works
    for it (always pass a title string).

    `explicit_style`: maintenance_tab.py's original _card() explicitly
    called `setFrameShape(QFrame.Shape.NoFrame)` (a no-op -- that's
    already QFrame's default) and
    `setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)` (NOT a
    no-op -- WA_StyledBackground defaults to False, and without it this
    QFrame's #DashCard background-color/border-radius QSS may not paint
    correctly on some style engines). Confirmed via direct attribute
    inspection before consolidating, so this flag preserves that
    behavior for callers that need it instead of silently dropping it.
    """
    frame = QFrame()
    frame.setObjectName("DashCard")
    if explicit_style:
        frame.setFrameShape(QFrame.Shape.NoFrame)
        frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(*margins)
    layout.setSpacing(10)
    if title:
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 13px; font-weight: 600; background: transparent;")
        layout.addWidget(title_lbl)
    return frame, layout


class StatCard(QFrame):
    """One KPI card (icon chip on the left, label + big value + freeform
    sub-line stacked in a column to its right).

    Consolidated here from exports.py and rewards_page.py's byte-identical
    `StatCard` classes (see PROGRESS.md, Phase 1d). `overview.py` also has
    a class of this name, but its body is genuinely different -- fixed
    "<delta> vs last 7 days" text with no `sub_color`/freeform `sub_text`
    param -- so it was diffed and deliberately left local rather than
    folded in here.
    """
    def __init__(self, icon, bg, color, label, value, sub_text, sub_color=None, parent=None):
        super().__init__(parent)
        self.setObjectName("DashCard")
        outer = QHBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(12)

        icon_box = QFrame()
        icon_box.setFixedSize(46, 46)
        icon_box.setStyleSheet(f"background-color: {bg}; border-radius: 8px;")
        icon_box_layout = QHBoxLayout(icon_box)
        icon_box_layout.setContentsMargins(0, 0, 0, 0)
        icon_box_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_lbl = QLabel()
        icon_lbl.setStyleSheet("background: transparent;")
        icon_lbl.setPixmap(qta.icon(icon, color=color).pixmap(22, 22))
        icon_box_layout.addWidget(icon_lbl)
        outer.addWidget(icon_box, alignment=Qt.AlignmentFlag.AlignVCenter)

        text_col = QVBoxLayout()
        text_col.setSpacing(4)

        label_lbl = QLabel(label)
        label_lbl.setStyleSheet(f"color: {PALETTE['text_muted']}; font-size: 11px; background: transparent;")
        text_col.addWidget(label_lbl)

        value_lbl = QLabel(value)
        value_lbl.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 21px; font-weight: bold; background: transparent;")
        text_col.addWidget(value_lbl)

        sub_lbl = QLabel(sub_text)
        sub_lbl.setStyleSheet(f"color: {sub_color or PALETTE['text_dim']}; font-size: 10px; font-weight: 600; background: transparent;")
        text_col.addWidget(sub_lbl)

        outer.addLayout(text_col, stretch=1)


def pill(text: str, accent: str, bg: str) -> QWidget:
    """
    Generic 3-arg pill: caller supplies the exact accent/bg colors
    directly (no internal status->color lookup), unlike `status_badge()`
    which takes a `color_map` and does the lookup itself. Used for
    non-status badges (package tiers, reward types) that don't fit the
    status vocabulary shape.

    Consolidated here from `packages_page.py` and `rewards_page.py`'s
    byte-identical `_pill()` copies (see PROGRESS.md, Phase 1e).
    """
    frame = QFrame()
    frame.setStyleSheet(f"background-color: {bg}; border-radius: 4px;")
    frame.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    row = QHBoxLayout(frame)
    row.setContentsMargins(8, 3, 8, 3)
    lbl = QLabel(text)
    lbl.setStyleSheet(f"color: {accent}; font-size: 10px; font-weight: 600; background: transparent;")
    row.addWidget(lbl)

    outer = QWidget()
    outer.setStyleSheet("background: transparent;")
    outer_row = QHBoxLayout(outer)
    outer_row.setContentsMargins(0, 0, 0, 0)
    outer_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
    outer_row.addWidget(frame)
    return outer


class DonutChartWidget(QWidget):
    """
    Hand-drawn donut chart with a centered total label.

    Consolidated here from exports.py and overview.py's `DonutChartWidget`
    classes (see PROGRESS.md, Phase 1f) -- NOT byte-identical, so this
    takes `size`/`thickness`/`value_color` params to reproduce each
    caller's exact prior rendering rather than silently changing either:
      - overview.py's original: size=160, thickness=20, value_font_size=15,
        value text colored via the live `PALETTE["text_primary"]`
        (theme-aware) -- these are this class's defaults.
      - exports.py's original: size=150, thickness=18, value_font_size=14,
        value text hardcoded to literal "white" regardless of theme --
        pass `value_color="white"` explicitly to match (this hardcoding
        looks like a latent light-theme bug, flagged in PROGRESS.md but
        not fixed here per the "no behavior changes" rule for this phase).
    """
    def __init__(self, segments, center_value, center_label, size: int = 160,
                 thickness: int = 20, value_color: str | None = None,
                 value_font_size: int = 15, parent=None):
        super().__init__(parent)
        self.segments = segments  # list[{"pct":..,"color":..}]
        self.center_value = center_value
        self.center_label = center_label
        self.thickness = thickness
        self.value_color = value_color
        self.value_font_size = value_font_size
        self.setFixedSize(size, size)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        thickness = self.thickness
        rect = QRectF(thickness / 2 + 2, thickness / 2 + 2,
                       self.width() - thickness - 4, self.height() - thickness - 4)

        start_angle = 90 * 16
        for seg in self.segments:
            span = -seg["pct"] / 100 * 360 * 16
            pen = QPen(QColor(seg["color"]), thickness)
            pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            painter.setPen(pen)
            painter.drawArc(rect, int(start_angle), int(span))
            start_angle += span

        value_font = painter.font()
        value_font.setPointSize(self.value_font_size)
        value_font.setBold(True)
        painter.setFont(value_font)
        painter.setPen(QPen(QColor(self.value_color or PALETTE["text_primary"])))
        painter.drawText(rect.adjusted(0, -8, 0, -8), Qt.AlignmentFlag.AlignCenter, self.center_value)

        label_font = painter.font()
        label_font.setPointSize(8)
        label_font.setBold(False)
        painter.setFont(label_font)
        painter.setPen(QPen(QColor(PALETTE["text_muted"])))
        painter.drawText(rect.adjusted(0, 14, 0, 14), Qt.AlignmentFlag.AlignCenter, self.center_label)

        painter.end()


class LeadCheckBox(QAbstractButton):
    """
    Fully hand-painted checkbox. QPushButton + QSS was unreliable across
    platforms/native styles (some styles keep painting their own button
    background/frame underneath the stylesheet, leaving only the border
    color visibly overridden). Painting it ourselves with QPainter removes
    that ambiguity entirely -- this will look identical everywhere.
    """
    def __init__(self, checked=True, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedSize(16, 16)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = QRectF(1, 1, self.width() - 2, self.height() - 2)

        if self.isChecked():
            painter.setBrush(QBrush(QColor(PALETTE["accent"])))
            painter.setPen(QPen(QColor(PALETTE["accent"]), 1))
        else:
            painter.setBrush(QBrush(QColor(PALETTE["bg_hover"])))
            border_color = PALETTE["text_muted"] if self.underMouse() else PALETTE["text_dim"]
            painter.setPen(QPen(QColor(border_color), 1.2))

        painter.drawRoundedRect(rect, 4, 4)

        if self.isChecked():
            pen = QPen(QColor("white"), 1.6)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            path = QPainterPath()
            path.moveTo(4.2, 8.4)
            path.lineTo(6.8, 11.2)
            path.lineTo(11.8, 4.8)
            painter.drawPath(path)

        painter.end()

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)


class ToggleSwitch(QAbstractButton):
    """
    Hand-painted pill-style on/off switch (same reasoning as LeadCheckBox --
    painting it ourselves avoids native-style QSS quirks). Used for settings
    like Maintenance Mode. Connect to `.toggled` same as any checkable button.
    """
    def __init__(self, checked=False, on_color=None, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        # Resolved from PALETTE here (construction time), not as a default
        # argument value -- a default arg is evaluated once when this
        # module is first imported and would freeze on whatever theme was
        # active at app startup, ignoring later theme switches.
        self._on_color = on_color or PALETTE["accent"]
        self.setFixedSize(38, 20)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        track = QRectF(0, 0, self.width(), self.height())
        painter.setPen(Qt.PenStyle.NoPen)
        if self.isChecked():
            painter.setBrush(QBrush(QColor(self._on_color)))
        else:
            painter.setBrush(QBrush(QColor(PALETTE["border_strong"])))
        painter.drawRoundedRect(track, self.height() / 2, self.height() / 2)

        knob_d = self.height() - 4
        knob_x = self.width() - knob_d - 2 if self.isChecked() else 2
        painter.setBrush(QBrush(QColor("white")))
        painter.drawEllipse(QRectF(knob_x, 2, knob_d, knob_d))
        painter.end()


def checkbox_cell(checked=True) -> QWidget:
    """Centered wrapper so the checkbox sits nicely inside a table cell."""
    wrap = QWidget()
    wrap.setStyleSheet("background: transparent;")
    row = QHBoxLayout(wrap)
    row.setContentsMargins(0, 0, 0, 0)
    row.setAlignment(Qt.AlignmentFlag.AlignCenter)
    box = LeadCheckBox(checked)
    box.toggled.connect(lambda _checked, b=box: b.update())
    row.addWidget(box)
    return wrap


def rating_cell(rating: float, star_color=None) -> QWidget:
    """Rating number followed by a star icon to its right (used in the table)."""
    star_color = star_color or PALETTE["yellow"]
    wrap = QWidget()
    wrap.setStyleSheet("background: transparent;")
    row = QHBoxLayout(wrap)
    row.setContentsMargins(6, 0, 6, 0)
    row.setSpacing(5)
    num_lbl = QLabel(str(rating))
    num_lbl.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 12px; background: transparent;")
    row.addWidget(num_lbl)
    star_lbl = QLabel()
    star_lbl.setPixmap(qta.icon('fa5s.star', color=star_color).pixmap(11, 11))
    row.addWidget(star_lbl)
    row.addStretch()
    return wrap


def website_cell(text: str, color=None, size=12) -> QWidget:
    """Website shown as blue text with a small icon (the only blue contact field)."""
    color = color or PALETTE["blue"]
    wrap = QWidget()
    wrap.setStyleSheet("background: transparent;")
    row = QHBoxLayout(wrap)
    row.setContentsMargins(6, 0, 6, 0)
    row.setSpacing(6)
    icon_lbl = QLabel()
    icon_lbl.setStyleSheet("background: transparent;")
    icon_lbl.setPixmap(qta.icon('fa5s.globe', color=color).pixmap(size, size))
    row.addWidget(icon_lbl)
    text_lbl = QLabel(text)
    text_lbl.setStyleSheet(f"color: {color}; font-size: 11px; background: transparent;")
    text_lbl.setToolTip(text)
    row.addWidget(text_lbl, stretch=1)
    return wrap


def email_cell(text: str, color=None) -> QWidget:
    """Email shown as plain muted text, no icon -- matches the Phone
    column's plain-text style rather than Website's icon+text style."""
    color = color or PALETTE["text_muted"]
    wrap = QWidget()
    wrap.setStyleSheet("background: transparent;")
    row = QHBoxLayout(wrap)
    row.setContentsMargins(6, 0, 6, 0)
    text_lbl = QLabel(text)
    text_lbl.setStyleSheet(f"color: {color}; font-size: 11px; background: transparent;")
    text_lbl.setToolTip(text)
    row.addWidget(text_lbl, stretch=1, alignment=Qt.AlignmentFlag.AlignVCenter)
    return wrap


def locked_contact_cell(cost: int, on_click) -> QWidget:
    """Locked placeholder shown in Phone/Email columns until the row is
    unlocked. Clicking it fires on_click() -> Dashboard.on_unlock_row."""
    btn = QPushButton(f"  Unlock ({cost} cr)")
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setIcon(qta.icon('fa5s.lock', color=PALETTE["text_muted"]))
    btn.setIconSize(QSize(10, 10))
    btn.setStyleSheet(
        f"QPushButton {{ background: transparent; border: none; color: {PALETTE['text_dim']}; "
        "font-size: 10px; text-align: left; padding: 0 6px; }"
        f"QPushButton:hover {{ color: {PALETTE['text_muted']}; }}"
    )
    btn.clicked.connect(on_click)

    wrap = QWidget()
    wrap.setStyleSheet("background: transparent;")
    row = QHBoxLayout(wrap)
    row.setContentsMargins(0, 0, 0, 0)
    row.addWidget(btn)
    return wrap


def icon_label(icon_name: str, text: str, color=None, size=13) -> QWidget:
    """Small helper: icon + text row, used in the detail panel contact rows."""
    color = color or PALETTE["text_muted"]
    wrap = QWidget()
    wrap.setStyleSheet("background: transparent;")
    row = QHBoxLayout(wrap)
    row.setContentsMargins(0, 2, 0, 2)
    row.setSpacing(8)

    icon_lbl = QLabel()
    icon_lbl.setPixmap(qta.icon(icon_name, color=color).pixmap(size, size))
    icon_lbl.setFixedWidth(size)
    row.addWidget(icon_lbl)

    text_lbl = QLabel(text)
    text_lbl.setWordWrap(True)
    text_lbl.setStyleSheet(f"color: {color}; font-size: 11px;")
    row.addWidget(text_lbl, stretch=1)

    return wrap


def build_referral_copy_field(code: str) -> QWidget:
    """Read-only code field + a Copy button that actually copies to the
    system clipboard. Shared by ViewProfileDialog and SettingsPage so the
    referral code looks/behaves the same everywhere it shows up."""
    wrap = QWidget()
    wrap.setStyleSheet("background: transparent;")
    row = QHBoxLayout(wrap)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(6)

    code_edit = QLineEdit(code)
    code_edit.setReadOnly(True)
    code_edit.setStyleSheet(
        f"QLineEdit {{ color: {PALETTE['text_primary']}; font-size: 13px; font-weight: 600; "
        "letter-spacing: 1px; }"
    )
    row.addWidget(code_edit, stretch=1)

    # This icon/text sits on RedBtn's solid accent-red fill (see #RedBtn in
    # config.py's QSS, which also hardcodes color: white for the same
    # reason) -- stays literal white in both themes for contrast against
    # the red background, rather than following text_primary.
    copy_btn = QPushButton(qta.icon('fa5s.copy', color="white"), " Copy")
    copy_btn.setObjectName("RedBtn")
    copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)

    def _reset_copy_btn():
        copy_btn.setText(" Copy")
        copy_btn.setIcon(qta.icon('fa5s.copy', color="white"))

    def _on_copy():
        QApplication.clipboard().setText(code)
        copy_btn.setText(" Copied!")
        copy_btn.setIcon(qta.icon('fa5s.check', color="white"))
        QTimer.singleShot(1500, _reset_copy_btn)

    copy_btn.clicked.connect(_on_copy)
    row.addWidget(copy_btn)

    return wrap


class SearchQueryCard(QFrame):
    """
    One row in the "Search Queries" panel: a saved/past search that can be
    clicked to swap the whole table + detail panel over to that search's
    own dataset. This is prototype-only -- there's no backend re-running the
    query, each card just points at one of the canned datasets defined in
    data.py (SEARCHES).
    """
    clicked = Signal(str)
    remove_clicked = Signal(str)

    def __init__(self, search: dict, parent=None):
        super().__init__(parent)
        self.search_id = search["id"]
        self.setObjectName("QueryCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(6)
        self.title_lbl = QLabel(search["title"])
        self.title_lbl.setWordWrap(True)
        top_row.addWidget(self.title_lbl, stretch=1)
        self.count_lbl = QLabel(str(len(search["data"])))
        self.count_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 11px; background: transparent;")
        top_row.addWidget(self.count_lbl, alignment=Qt.AlignmentFlag.AlignTop)

        self.remove_btn = QPushButton()
        self.remove_btn.setIcon(qta.icon('fa5s.times', color=PALETTE["text_muted"]))
        self.remove_btn.setFixedSize(18, 18)
        self.remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.remove_btn.setToolTip("Remove this search")
        self.remove_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; border-radius: 4px; }"
            f"QPushButton:hover {{ background-color: {PALETTE['bg_hover']}; }}"
        )
        self.remove_btn.clicked.connect(lambda: self.remove_clicked.emit(self.search_id))
        top_row.addWidget(self.remove_btn, alignment=Qt.AlignmentFlag.AlignTop)

        layout.addLayout(top_row)

        self.time_lbl = QLabel(search["timestamp"])
        self.time_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 10px; background: transparent;")
        layout.addWidget(self.time_lbl)

        self.set_active(False)

    def set_active(self, active: bool):
        if active:
            self.setStyleSheet(
                f"#QueryCard {{ background-color: {PALETTE['accent_soft']}; "
                f"border: 1px solid {config.rgba_from_hex(PALETTE['accent'], 0.35)}; border-radius: 8px; }}"
            )
            self.title_lbl.setStyleSheet(
                f"color: {PALETTE['accent_text']}; font-size: 12px; font-weight: 600; background: transparent;"
            )
        else:
            self.setStyleSheet(
                f"#QueryCard {{ background-color: {PALETTE['bg_surface']}; border: 1px solid {PALETTE['border']}; border-radius: 8px; }}"
                f"#QueryCard:hover {{ border: 1px solid {PALETTE['border_strong']}; }}"
            )
            self.title_lbl.setStyleSheet(
                f"color: {PALETTE['text_secondary']}; font-size: 12px; font-weight: 600; background: transparent;"
            )

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.search_id)
        super().mousePressEvent(event)


class UserAccountBox(QFrame):
    """
    Clickable 'who's logged in' row for the bottom of the sidebar: avatar +
    name + email, opening a small menu with View Profile / Edit Account /
    Log Out.

    View Profile and Edit Account operate on `self.profile`, built from a
    real `users` table row (see PROGRESS.md Phase 5) -- edits made via
    Edit Account are persisted through core.api_client.update_me, not just
    held in memory for the current run.
    """
    logout_requested = Signal()

    def __init__(self, name: str, email: str, role: str = None, referral_code: str = "",
                 credits: int = 0, unlimited: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName("UserBox")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        # Real row from the `users` table now (see PROGRESS.md Phase 5) --
        # `name`/`email` args are still accepted as a fallback in case the
        # lookup somehow misses (shouldn't happen for anyone who came
        # through Login/Signup, but a missing row shouldn't crash the
        # sidebar over a display box). GET /me reads whichever account
        # the current session's bearer token belongs to -- `email` here
        # is just the display fallback, not used to look anyone up.
        try:
            user = get_me()
        except ApiError:
            user = None
        self.user_id = user["id"] if user else None

        self.profile = {
            "name": user["full_name"] if user else name,
            "email": user["email"] if user else email,
            "phone": (user["phone"] or "") if user else "",
            "company": (user["company"] or "") if user else "",
            "joined": (user["created_at"] or "") if user else "",
            # Raw DB value, may be None (e.g. legacy row from before
            # avatars existed) -- circular_avatar_pixmap/avatar_path both
            # fall back to config.DEFAULT_AVATAR for that case, so nothing
            # downstream needs to special-case a missing avatar.
            "avatar": user["avatar"] if user else config.DEFAULT_AVATAR,
        }
        # Real credits balance, not billing/plan info (that's a separate,
        # still-mock concept scoped to the Billing page only -- see
        # ui/pages/billing.py). Dashboard is the source of truth for the
        # live remaining balance (it owns credits_remaining/credits_total,
        # see dashboard.py's docstring on that), so it passes the current
        # numbers in here at construction and pushes updates through
        # update_credits() whenever a search spends credits -- this box
        # never computes a balance itself.
        self.profile["credits"] = credits
        self.profile["credits_unlimited"] = unlimited
        # "role" here is the profile-display job title shown in
        # ViewProfileDialog / editable in EditProfileDialog's "Role"
        # field -- e.g. "Lead Generation Specialist". This is NOT the
        # app permission level (this dict has no permission field at
        # all, same as before this migration -- permission gating reads
        # Dashboard.user_role, not this box). Maps to the DB's
        # `company_role` column, never `role` -- see PROGRESS.md's
        # gotcha note. Prefer whatever the person has actually saved via
        # Edit Account; until they've set one, fall back to the same
        # permission-based default label dashboard.py has always passed
        # in here (e.g. "Administrator" for admins).
        company_role = user.get("company_role") if user else None
        self.profile["role"] = company_role or role or ""
        self.profile["referral_code"] = referral_code

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        self.avatar = QLabel()
        self.avatar.setFixedSize(32, 32)
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar.setStyleSheet("background: transparent;")
        self.avatar.setPixmap(circular_avatar_pixmap(self.profile["avatar"], 32))
        layout.addWidget(self.avatar)

        text_col = QVBoxLayout()
        text_col.setSpacing(0)
        text_col.setContentsMargins(0, 0, 0, 0)

        # Full values are kept around so we can re-elide them whenever this
        # box gets resized (the sidebar is now a draggable splitter panel,
        # not a fixed width) -- see resizeEvent below.
        self._full_name = name
        self._full_email = email

        self.name_lbl = QLabel()
        self.name_lbl.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 12px; font-weight: 600; background: transparent;")
        self.email_lbl = QLabel()
        self.email_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 10px; background: transparent;")

        text_col.addWidget(self.name_lbl)
        text_col.addWidget(self.email_lbl)
        layout.addLayout(text_col, stretch=1)

        chevron_lbl = QLabel()
        chevron_lbl.setStyleSheet("background: transparent;")
        chevron_lbl.setPixmap(qta.icon('fa5s.chevron-up', color=PALETTE["text_muted"]).pixmap(10, 10))
        layout.addWidget(chevron_lbl)

        self.menu = QMenu(self)
        self.menu.setStyleSheet(
            f"QMenu {{ background-color: {PALETTE['bg_surface']}; border: 1px solid {PALETTE['border']}; "
            f"border-radius: 8px; padding: 4px; color: {PALETTE['text_secondary']}; }}"
            "QMenu::item { padding: 6px 12px; border-radius: 4px; }"
            f"QMenu::item:selected {{ background-color: {PALETTE['bg_hover']}; }}"
            f"QMenu::separator {{ height: 1px; background: {PALETTE['border']}; margin: 4px 6px; }}"
        )

        profile_action = QAction(qta.icon('fa5s.user', color=PALETTE["text_muted"]), "View Profile", self)
        profile_action.triggered.connect(self.open_view_profile)
        self.menu.addAction(profile_action)

        edit_action = QAction(qta.icon('fa5s.user-edit', color=PALETTE["text_muted"]), "Edit Account", self)
        edit_action.triggered.connect(self.open_edit_profile)
        self.menu.addAction(edit_action)

        self.menu.addSeparator()

        logout_action = QAction(qta.icon('fa5s.sign-out-alt', color=PALETTE["red"]), "Log Out", self)
        logout_action.triggered.connect(self._on_logout_clicked)
        self.menu.addAction(logout_action)

        self._update_elided_labels()

    def open_view_profile(self):
        # Imported here (not at module top) to avoid a circular import:
        # ui.dialogs.dialogs doesn't need to import this module, but this
        # method needs ui.dialogs.dialogs.
        from ui.dialogs.dialogs import ViewProfileDialog
        dlg = ViewProfileDialog(self.profile, self)
        dlg.edit_requested.connect(self.open_edit_profile)
        dlg.exec()

    def update_credits(self, remaining: int, unlimited: bool = False):
        """Called by Dashboard (on_credits_changed) whenever the credits
        balance changes, so View Profile always reflects the current
        number instead of whatever it was when the sidebar was built."""
        self.profile["credits"] = remaining
        self.profile["credits_unlimited"] = unlimited

    def open_edit_profile(self):
        from ui.dialogs.dialogs import EditProfileDialog
        dlg = EditProfileDialog(self.profile, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            values = dlg.get_values()
            if self.user_id is not None:
                update_fields = {
                    "full_name": values["name"],
                    "email": values["email"],
                    "phone": values["phone"],
                    "company": values["company"],
                    # `company_role` (job title), NOT `role` (permission
                    # level) -- see PROGRESS.md's gotcha note. Writing
                    # this to `role` would silently change what tabs/
                    # unlocks this account has access to.
                    "company_role": values["role"],
                    "avatar": values["avatar"],
                }
                if values.get("password_changed"):
                    # TODO: real current-password verification blocks on
                    # the password-hashing phase (see PROGRESS.md's
                    # "Not this file's job" section) -- EditProfileDialog
                    # itself doesn't check current_password_edit against
                    # anything real yet either, this just persists
                    # whatever new password was typed, same trust level
                    # the dialog already had.
                    #
                    # Read from values["new_password"] (captured by
                    # EditProfileDialog._on_save_clicked before it called
                    # accept()), NOT dlg.new_password_edit.text() -- by
                    # this point dlg.exec() has already returned, and
                    # ModernDialog's WA_DeleteOnClose means the dialog's
                    # widgets may already be gone (the
                    # "libshiboken: ... already deleted" crash).
                    update_fields["password"] = values["new_password"]
                try:
                    updated = update_me(**update_fields)
                except ApiError:
                    updated = None
                if updated is not None:
                    self.profile["name"] = updated["full_name"]
                    self.profile["email"] = updated["email"]
                    self.profile["phone"] = updated["phone"] or ""
                    self.profile["company"] = updated["company"] or ""
                    self.profile["role"] = updated["company_role"] or ""
                    self.profile["avatar"] = updated["avatar"]
                    self._maybe_record_profile_completed(updated)
            else:
                # No DB row to persist to -- shouldn't happen in practice,
                # but keep the box usable rather than silently dropping
                # the edit.
                self.profile.update(values)
            self._apply_profile_to_sidebar()

    def _maybe_record_profile_completed(self, updated: dict):
        """Feeds the "Complete Your Profile" reward (progress_key
        PROFILE_COMPLETED, target_value 1 -- set by the admin in
        rewards_page.py, same as First Search's SEARCH key). "All fields
        on your profile" means the full editable set from EditProfileDialog:
        name/email are always present, phone/company/company_role/avatar
        are the ones actually optional at signup.

        Guarded with a get_my_progress() check first so re-saving an
        already-complete profile doesn't keep incrementing progress_value
        past 1 every time -- record_progress() has no built-in dedupe
        (see its docstring in db.py), so the caller has to avoid firing
        it more than once per real completion.
        """
        required = ("full_name", "email", "phone", "company", "company_role", "avatar")
        if not all((updated.get(field) or "").strip() for field in required):
            return
        try:
            if get_my_progress("PROFILE_COMPLETED") > 0:
                return  # already recorded -- don't double-count re-saves
            record_my_progress("PROFILE_COMPLETED")
        except ApiError:
            pass  # couldn't reach the server -- next save will retry

    def _apply_profile_to_sidebar(self):
        """Pushes the profile dict's current name/email/avatar back onto
        the visible sidebar row."""
        self._full_name = self.profile["name"]
        self._full_email = self.profile["email"]
        self.avatar.setPixmap(circular_avatar_pixmap(self.profile["avatar"], 32))
        self._update_elided_labels()

    def _update_elided_labels(self):
        """Recomputes how much text fits based on the box's *current*
        width, since the sidebar can now be dragged wider/narrower."""
        consumed = 16 + 32 + 8 + 10 + 8
        available_width = max(40, self.width() - consumed)
        self._set_elided_text(self.name_lbl, self._full_name, available_width)
        self._set_elided_text(self.email_lbl, self._full_email, available_width)

    def resizeEvent(self, event):
        self._update_elided_labels()
        super().resizeEvent(event)

    @staticmethod
    def _set_elided_text(label: QLabel, full_text: str, max_width_px: int):
        """Truncates full_text with an ellipsis so it fits max_width_px,
        and always keeps the untruncated value available as a tooltip."""
        metrics = QFontMetrics(label.font())
        elided = metrics.elidedText(full_text, Qt.TextElideMode.ElideRight, max_width_px)
        label.setText(elided)
        label.setToolTip(full_text)

    def _not_wired_up(self, action_name: str):
        # Placeholder handler -- replace with real auth/profile logic.
        from ui.dialogs.dialogs import InfoDialog
        InfoDialog.show(self, action_name, f"'{action_name}' isn't wired up to a backend yet.")

    def _on_logout_clicked(self):
        from ui.dialogs.dialogs import ConfirmDialog
        confirmed = ConfirmDialog.ask(
            self, "Log Out",
            "Are you sure you want to log out of your account?",
            confirm_text="Log Out",
            icon_name='fa5s.sign-out-alt',
        )
        if confirmed:
            self.logout_requested.emit()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            # Open upward, anchored above this box, since it sits at the
            # bottom of the sidebar.
            menu_height = self.menu.sizeHint().height()
            pos = self.mapToGlobal(QPoint(0, -menu_height))
            self.menu.exec(pos)
        super().mousePressEvent(event)

class Toast(QFrame):
    """
    Small, non-blocking "it worked" notification that slides down and
    fades in at the top-center of the window, holds briefly, then lifts
    away and fades back out -- no button, no click required to dismiss
    it, and it never grabs a modal grip on the app the way ConfirmDialog/
    InfoDialog do.

    Top-center + a slide (not just a fade) is deliberate: the user is
    usually looking at a row or a confirm dialog, not the window's
    corner, when the toast appears, so it needs motion to catch their
    peripheral vision rather than relying on an opacity change alone.

    Meant for routine success feedback (e.g. "Unlocked!" after spending
    credits to reveal a contact) where popping a modal that demands an
    "OK" click would be overkill for something the user does over and
    over. InfoDialog is still the right tool for anything the user
    actually needs to read and acknowledge, especially errors.

    Use the show_toast() classmethod rather than constructing directly --
    it finds the top-level window for you (so the toast isn't clipped by
    whatever scroll area/splitter pane `parent` happens to live inside)
    and starts the slide-in/fade-in/hold/exit sequence.
    """

    def __init__(self, window: QWidget, title: str, message: str = "",
                 icon_name: str = 'fa5s.check-circle', success: bool = True):
        super().__init__(window)
        self.setObjectName("ToastCard")
        accent = PALETTE["green_solid"] if success else PALETTE["red"]
        self.setStyleSheet(
            f"QFrame#ToastCard {{ background-color: {PALETTE['bg_surface']}; "
            f"border: 1px solid {PALETTE['border']}; border-left: 3px solid {accent}; "
            f"border-radius: 8px; }}"
        )
        self.setFixedWidth(300)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(qta.icon(icon_name, color=accent).pixmap(20, 20))
        icon_lbl.setStyleSheet("background: transparent; border: none;")
        layout.addWidget(icon_lbl, 0, Qt.AlignmentFlag.AlignTop)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        title_lbl = QLabel(title)
        title_lbl.setWordWrap(True)
        title_lbl.setStyleSheet(
            f"color: {PALETTE['text_primary']}; font-size: 12px; font-weight: 600; "
            "background: transparent; border: none;"
        )
        text_col.addWidget(title_lbl)
        if message:
            msg_lbl = QLabel(message)
            msg_lbl.setWordWrap(True)
            msg_lbl.setStyleSheet(
                f"color: {PALETTE['text_muted']}; font-size: 11px; "
                "background: transparent; border: none;"
            )
            text_col.addWidget(msg_lbl)
        layout.addLayout(text_col, 1)

        # Fully transparent by default -- _play() fades it in. Doing this
        # in __init__ (rather than relying on show() ordering) means
        # there's never a one-frame flash of the fully-opaque card before
        # the fade-in animation takes over.
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity_effect)

        # Opacity: fades in on entrance, fades out on dismissal.
        self._fade_in = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        self._fade_in.setDuration(220)
        self._fade_in.setStartValue(0.0)
        self._fade_in.setEndValue(1.0)
        self._fade_in.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._fade_out = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        self._fade_out.setDuration(200)
        self._fade_out.setStartValue(1.0)
        self._fade_out.setEndValue(0.0)
        self._fade_out.setEasingCurve(QEasingCurve.Type.InCubic)
        self._fade_out.finished.connect(self.deleteLater)

        # Position: slides down into view on entrance, slides back up a
        # little on dismissal. Motion catches the eye from the corner of
        # your vision far better than an opacity change alone does --
        # important here since the toast sits top-center, away from
        # wherever the user was actually looking (the row, or a confirm
        # dialog) when they triggered the unlock. Actual start/end points
        # are computed in _reposition() since they depend on window size.
        self._slide_in = QPropertyAnimation(self, b"pos", self)
        self._slide_in.setDuration(280)
        self._slide_in.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._slide_out = QPropertyAnimation(self, b"pos", self)
        self._slide_out.setDuration(200)
        self._slide_out.setEasingCurve(QEasingCurve.Type.InCubic)

        # Single-shot rather than a running/recurring timer -- one toast,
        # one dismissal, no reason to keep ticking after that.
        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.timeout.connect(self._dismiss)

    def mousePressEvent(self, event):
        # Clicking it dismisses it early instead of making the user wait
        # out the hold -- skip straight to the exit animation.
        if event.button() == Qt.MouseButton.LeftButton and self._hold_timer.isActive():
            self._hold_timer.stop()
            self._dismiss()
        super().mousePressEvent(event)

    def _dismiss(self):
        self._fade_out.start()
        self._slide_out.start()

    def _reposition(self):
        """Computes the resting position (top-center of the window, just
        under the title bar) and points the slide animations at it --
        _slide_in comes from `settled_pos` minus a vertical offset (so it
        drops down into place), _slide_out goes back up by a smaller
        offset (so the exit reads as "lift away" rather than a full
        repeat of the entrance)."""
        window = self.window()
        self.adjustSize()
        top_margin = 24
        settled_x = (window.width() - self.width()) // 2
        settled_pos = QPoint(settled_x, top_margin)
        start_pos = QPoint(settled_x, top_margin - 30)
        exit_pos = QPoint(settled_x, top_margin - 14)

        self.move(start_pos)
        self._slide_in.setStartValue(start_pos)
        self._slide_in.setEndValue(settled_pos)
        self._slide_out.setStartValue(settled_pos)
        self._slide_out.setEndValue(exit_pos)

    def _play(self, hold_ms: int):
        self._reposition()
        self.raise_()
        self.show()
        self._fade_in.start()
        self._slide_in.start()
        self._hold_timer.start(hold_ms)

    @staticmethod
    def show_toast(parent: QWidget, title: str, message: str = "",
                    icon_name: str = 'fa5s.check-circle', success: bool = True,
                    duration_ms: int = 2600) -> "Toast":
        """Builds and shows a toast anchored to `parent`'s top-level window.
        Fire-and-forget -- the toast tears itself down once its exit
        animation finishes, nothing needs to hold onto the returned
        instance."""
        window = parent.window()
        toast = Toast(window, title, message, icon_name, success)
        toast._play(duration_ms)
        return toast