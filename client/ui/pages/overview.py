"""
overview.py

OverviewPage: the "Dashboard" nav tab -- stat cards, a leads-over-time area
chart, a leads-by-category donut, lead sources / recent exports / leads-by-
rating, top-rated businesses, and a recent-activity feed. All hand-drawn
with QPainter (no extra charting dependency).

Backed by real data now: GET /leads (every lead the account owns, across
every search), GET /exports, and GET /searches (see core.api_client),
not the old client/data/overview_stats.py mock lists. Everything below is
computed client-side from those three collections -- same "fetch the raw
rows, aggregate in Python" pattern exports.py already uses for its KPI
row and Export Summary donut.

One real gap the schema doesn't cover yet: Lead has no per-row `source`
column (the scraper only ever produces Google Maps leads today), so the
"Lead Sources" card can't do a real GROUP BY -- it shows Google Maps at
100% of whatever's actually been found and 0% for sources (LinkedIn,
etc.) that aren't wired up to a scraper yet. Swap that block for a real
breakdown once Lead gets a `source` field and a second scraper exists.

This page is only ever built for user_role == "user" -- see dashboard.py's
build_pages(), which skips constructing this class entirely for admins
rather than building-then-hiding it.

Depends on: config, core.api_client.
"""

from datetime import datetime, timedelta

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QProgressBar, QScrollArea, QPushButton, QMenu,
)
from PySide6.QtCore import Qt, QRectF, QPointF, QPoint, QObject, QRunnable, QThreadPool, Signal
from PySide6.QtGui import QPainter, QPen, QBrush, QPainterPath, QColor, QLinearGradient, QAction
import qtawesome as qta

from core import config
from core.config import PALETTE
from core.api_client import list_all_leads, list_exports, list_searches, ApiError
from ui.components.widgets import dash_card, DonutChartWidget

# data.py stores color references as name strings (e.g. "COLOR_RED") so it
# doesn't need to import Qt-adjacent config -- resolve them to the live
# PALETTE here. Looked up at call time (not snapshotted) so these track
# theme switches correctly, same as everywhere else post-migration.
_COLOR_KEY_MAP = {
    "COLOR_MUTED": "text_muted", "COLOR_RED": "red",
    "COLOR_RED_SOLID": "red_solid", "COLOR_GREEN": "green",
    "COLOR_BLUE": "blue", "COLOR_YELLOW": "yellow",
    "COLOR_PURPLE": "purple",
}


def _c(name: str) -> str:
    key = _COLOR_KEY_MAP.get(name)
    return PALETTE[key] if key is not None else name


def _parse_dt(raw):
    """Best-effort parse of the "YYYY-MM-DD HH:MM:SS"-ish strings the
    server sends for created_at (SQLite's func.datetime('now')) or an
    ISO string with a trailing 'Z'. Returns None on anything else
    rather than raising, since a malformed/missing timestamp shouldn't
    crash the whole dashboard -- the row is just excluded from
    date-based aggregation."""
    if not raw:
        return None
    try:
        return datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(raw.replace("Z", ""))
    except ValueError:
        return None


class StatCard(QFrame):
    """One KPI card (icon chip on the left, label + big value + delta
    stacked in a column to its right)."""
    def __init__(self, icon, bg, color, label, value, delta=None, parent=None):
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

        # delta is optional -- only render the "+x% vs last 7 days" row
        # when there's real prior-week data to compare against (see
        # OverviewPage._trend_pct). Passing None skips the row entirely
        # instead of displaying a fake trend next to real data.
        if delta:
            delta_row = QHBoxLayout()
            delta_row.setSpacing(4)
            delta_lbl = QLabel(delta)
            delta_lbl.setStyleSheet(f"color: {PALETTE['green']}; font-size: 10px; font-weight: 600; background: transparent;")
            delta_row.addWidget(delta_lbl)
            sub_lbl = QLabel("vs last 7 days")
            sub_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 10px; background: transparent;")
            delta_row.addWidget(sub_lbl)
            delta_row.addStretch()
            text_col.addLayout(delta_row)

        outer.addLayout(text_col, stretch=1)


class AreaChartWidget(QWidget):
    """Hand-drawn line + gradient-fill area chart (no external chart lib)."""
    def __init__(self, data, parent=None):
        super().__init__(parent)
        self.data = data  # list[(label, value)]
        self.setMinimumHeight(210)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        # margin_right needs to leave room for the LAST x-axis label
        # (e.g. "Aug 09"), which is centered on a point sitting right at
        # the chart's right edge -- too small a margin here and that
        # label's tail gets clipped by the widget boundary (it did, at
        # the old value of 12: "Aug 09" rendered as "Aug 0").
        margin_left, margin_right = 42, 30
        margin_top, margin_bottom = 10, 26
        chart_w = max(1, w - margin_left - margin_right)
        chart_h = max(1, h - margin_top - margin_bottom)

        values = [v for _, v in self.data]
        max_v = max(values) * 1.15 if values and max(values) > 0 else 1

        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)

        steps = 4
        for i in range(steps + 1):
            y = margin_top + chart_h - (chart_h * i / steps)
            painter.setPen(QPen(QColor(PALETTE["divider"]), 1))
            painter.drawLine(margin_left, int(y), w - margin_right, int(y))
            painter.setPen(QPen(QColor(PALETTE["text_dim"])))
            painter.drawText(
                QRectF(0, y - 8, margin_left - 8, 16),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                str(int(max_v * i / steps)),
            )

        n = len(self.data)
        if n < 2:
            painter.end()
            return
        step_x = chart_w / (n - 1)

        points = []
        for i, (_, v) in enumerate(self.data):
            x = margin_left + i * step_x
            y = margin_top + chart_h - (chart_h * v / max_v)
            points.append(QPointF(x, y))

        path = QPainterPath()
        path.moveTo(points[0].x(), margin_top + chart_h)
        for p in points:
            path.lineTo(p)
        path.lineTo(points[-1].x(), margin_top + chart_h)
        path.closeSubpath()
        area_fill_top = QColor(PALETTE["red_solid"])
        area_fill_top.setAlpha(110)
        area_fill_bottom = QColor(PALETTE["red_solid"])
        area_fill_bottom.setAlpha(0)
        gradient = QLinearGradient(0, margin_top, 0, margin_top + chart_h)
        gradient.setColorAt(0, area_fill_top)
        gradient.setColorAt(1, area_fill_bottom)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(gradient))
        painter.drawPath(path)

        painter.setPen(QPen(QColor(PALETTE["red_solid"]), 2))
        for i in range(len(points) - 1):
            painter.drawLine(points[i], points[i + 1])

        painter.setPen(QPen(QColor(PALETTE["bg_app"]), 2))
        painter.setBrush(QBrush(QColor(PALETTE["red_solid"])))
        for p in points:
            painter.drawEllipse(p, 4, 4)

        painter.setPen(QPen(QColor(PALETTE["text_dim"])))
        for i, (label, _) in enumerate(self.data):
            x = margin_left + i * step_x
            # Clamp the label's rect to the widget's own width so a
            # first/last point sitting near the edge never has its
            # centered text pushed (and clipped) past the boundary.
            rect_left = max(0, x - 25)
            rect_right = min(w, x + 25)
            painter.drawText(QRectF(rect_left, h - margin_bottom + 6, rect_right - rect_left, 16),
                              Qt.AlignmentFlag.AlignCenter, label)

        painter.end()


class VerticalBarChart(QWidget):
    """Hand-drawn vertical bar chart, used for the Leads-by-Rating card."""
    def __init__(self, data, colors=None, suffix="", parent=None):
        super().__init__(parent)
        self.data = data  # list[(label, value)]
        self.colors = colors or [PALETTE["green"]]
        self.suffix = suffix
        self.setMinimumHeight(190)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        margin_top, margin_bottom = 22, 28
        chart_h = max(1, h - margin_top - margin_bottom)
        vals = [v for _, v in self.data]
        max_v = max(vals) * 1.2 if vals and max(vals) > 0 else 1

        n = len(self.data)
        if n == 0:
            painter.end()
            return
        gap = 14
        bar_w = max(4, (w - gap * (n + 1)) / n)

        font = painter.font()
        font.setPointSize(8)

        for i, (label, v) in enumerate(self.data):
            bar_h = chart_h * v / max_v
            x = gap + i * (bar_w + gap)
            y = margin_top + (chart_h - bar_h)
            color = self.colors[i % len(self.colors)]

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(color)))
            painter.drawRoundedRect(QRectF(x, y, bar_w, bar_h), 4, 4)

            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QPen(QColor(PALETTE["text_secondary"])))
            painter.drawText(QRectF(x - 6, y - 18, bar_w + 12, 16),
                              Qt.AlignmentFlag.AlignCenter, f"{v:,}")

            font.setBold(False)
            painter.setFont(font)
            painter.setPen(QPen(QColor(PALETTE["text_dim"])))
            painter.drawText(QRectF(x - 6, h - margin_bottom + 6, bar_w + 12, 16),
                              Qt.AlignmentFlag.AlignCenter, f"{label}{self.suffix}")

        painter.end()


def _dash_legend_row(label: str, value: int, pct: int, color: str) -> QWidget:
    wrap = QWidget()
    wrap.setStyleSheet("background: transparent;")
    row = QHBoxLayout(wrap)
    row.setContentsMargins(0, 3, 0, 3)
    row.setSpacing(8)

    dot = QLabel()
    dot.setStyleSheet("background: transparent;")
    dot.setPixmap(qta.icon('fa5s.circle', color=color).pixmap(8, 8))
    row.addWidget(dot)

    label_lbl = QLabel(label)
    label_lbl.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 11px; background: transparent;")
    row.addWidget(label_lbl, stretch=1)

    value_lbl = QLabel(f"{value:,}")
    value_lbl.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 11px; background: transparent;")
    row.addWidget(value_lbl)

    pct_lbl = QLabel(f"({pct}%)")
    pct_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 11px; background: transparent;")
    row.addWidget(pct_lbl)

    return wrap


def _dash_progress_row(label: str, value_text: str, pct: int, color: str) -> QWidget:
    wrap = QWidget()
    wrap.setStyleSheet("background: transparent;")
    v = QVBoxLayout(wrap)
    v.setContentsMargins(0, 5, 0, 5)
    v.setSpacing(5)

    top = QHBoxLayout()
    lbl = QLabel(label)
    lbl.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 11px; background: transparent;")
    top.addWidget(lbl)
    top.addStretch()
    val_lbl = QLabel(f"{value_text} ({pct}%)")
    val_lbl.setStyleSheet(f"color: {PALETTE['text_muted']}; font-size: 11px; background: transparent;")
    top.addWidget(val_lbl)
    v.addLayout(top)

    bar = QProgressBar()
    bar.setRange(0, 100)
    bar.setValue(pct)
    bar.setTextVisible(False)
    bar.setFixedHeight(8)
    bar.setStyleSheet(
        f"QProgressBar {{ background-color: {PALETTE['border']}; border-radius: 4px; border: none; }}"
        f"QProgressBar::chunk {{ background-color: {color}; border-radius: 4px; }}"
    )
    v.addWidget(bar)

    return wrap


def _dash_top_rated_row(rank: int, biz: dict) -> QWidget:
    wrap = QWidget()
    wrap.setStyleSheet("background: transparent;")
    row = QHBoxLayout(wrap)
    row.setContentsMargins(0, 6, 0, 6)
    row.setSpacing(10)

    rank_lbl = QLabel(str(rank))
    rank_lbl.setFixedWidth(14)
    rank_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 11px; background: transparent;")
    row.addWidget(rank_lbl)

    # No business-photo thumbnail here on purpose -- leads don't carry a
    # real image (the scraper never collects one), so the old decorative
    # gradient placeholder box was standing in for a photo that doesn't
    # exist. Removed rather than faked.
    text_col = QVBoxLayout()
    text_col.setSpacing(2)
    name_lbl = QLabel(biz["name"])
    name_lbl.setWordWrap(True)
    name_lbl.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 11px; font-weight: 600; background: transparent;")
    text_col.addWidget(name_lbl)

    meta_row = QHBoxLayout()
    meta_row.setContentsMargins(0, 0, 0, 0)
    meta_row.setSpacing(4)
    star = QLabel()
    star.setStyleSheet("background: transparent;")
    star.setPixmap(qta.icon('fa5s.star', color=PALETTE['yellow']).pixmap(9, 9))
    meta_row.addWidget(star)
    meta_lbl = QLabel(f'{biz["rating"]}   {biz["reviews"]:,} reviews')
    meta_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 10px; background: transparent;")
    meta_row.addWidget(meta_lbl)
    meta_row.addStretch()
    text_col.addLayout(meta_row)
    row.addLayout(text_col, stretch=1)

    is_open = biz["status"] == "Open"
    status_lbl = QLabel("Open now" if is_open else biz["status"])
    status_lbl.setStyleSheet(
        f"color: {PALETTE['green'] if is_open else PALETTE['red']}; font-size: 10px; background: transparent;"
    )
    row.addWidget(status_lbl, alignment=Qt.AlignmentFlag.AlignVCenter)

    return wrap


def _dash_export_row(item: dict) -> QWidget:
    wrap = QWidget()
    wrap.setStyleSheet("background: transparent;")
    row = QHBoxLayout(wrap)
    row.setContentsMargins(0, 6, 0, 6)
    row.setSpacing(10)

    icon_lbl = QLabel()
    icon_lbl.setStyleSheet("background: transparent;")
    icon_lbl.setPixmap(qta.icon('fa5s.file-alt', color=PALETTE['text_muted']).pixmap(14, 14))
    row.addWidget(icon_lbl)

    text_col = QVBoxLayout()
    text_col.setSpacing(2)
    name_lbl = QLabel(item["name"])
    name_lbl.setWordWrap(True)
    name_lbl.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 11px; background: transparent;")
    text_col.addWidget(name_lbl)
    meta_lbl = QLabel(item["meta"])
    meta_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 10px; background: transparent;")
    text_col.addWidget(meta_lbl)
    row.addLayout(text_col, stretch=1)

    badge = QLabel(item["count"])
    badge.setStyleSheet(
        f"background-color: {PALETTE['green_soft']}; color: {PALETTE['green']}; "
        "font-size: 10px; padding: 3px 8px; border-radius: 4px;"
    )
    row.addWidget(badge)

    dl_btn = QPushButton()
    dl_btn.setIcon(qta.icon('fa5s.download', color=PALETTE['text_muted']))
    dl_btn.setFixedSize(24, 24)
    dl_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    dl_btn.setStyleSheet(
        "QPushButton { background: transparent; border: none; border-radius: 4px; }"
        f"QPushButton:hover {{ background-color: {PALETTE['bg_hover']}; }}"
    )
    row.addWidget(dl_btn)

    return wrap


def _dash_activity_row(entry: dict) -> QWidget:
    wrap = QWidget()
    wrap.setStyleSheet(f"background: transparent; border-bottom: 1px solid {PALETTE['divider']};")
    row = QHBoxLayout(wrap)
    row.setContentsMargins(0, 8, 0, 8)
    row.setSpacing(10)

    icon_color = _c(entry["color"])
    icon_box = QFrame()
    icon_box.setFixedSize(30, 30)
    icon_box.setStyleSheet(
        f"background-color: {config.rgba_from_hex(icon_color, 0.15)}; border-radius: 8px;"
    )
    icon_box_layout = QHBoxLayout(icon_box)
    icon_box_layout.setContentsMargins(0, 0, 0, 0)
    icon_box_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
    icon_lbl = QLabel()
    icon_lbl.setStyleSheet("background: transparent; border: none;")
    icon_lbl.setPixmap(qta.icon(entry["icon"], color=icon_color).pixmap(13, 13))
    icon_box_layout.addWidget(icon_lbl)
    row.addWidget(icon_box, alignment=Qt.AlignmentFlag.AlignVCenter)

    text_col = QVBoxLayout()
    text_col.setSpacing(2)
    text_lbl = QLabel(entry["text"])
    text_lbl.setWordWrap(True)
    text_lbl.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 11px; background: transparent; border: none;")
    text_col.addWidget(text_lbl)
    meta_lbl = QLabel(entry["meta"])
    meta_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 10px; background: transparent; border: none;")
    text_col.addWidget(meta_lbl)
    row.addLayout(text_col, stretch=1)

    time_lbl = QLabel(entry["time"])
    time_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 10px; background: transparent; border: none;")
    row.addWidget(time_lbl, alignment=Qt.AlignmentFlag.AlignTop)

    return wrap


def _empty_row(text: str) -> QWidget:
    """Shown in a card body instead of an empty list -- an account with
    no leads/exports yet is a normal state (brand-new users, or "Today"
    picked on a quiet day), not an error."""
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 11px; background: transparent; padding: 10px 2px;")
    return lbl


def _card(title: str) -> tuple:
    """Thin wrapper over widgets.dash_card() (see PROGRESS.md, Phase 1c).
    This page's original _card() made `title` required (no default) --
    kept that call signature since every call site here does pass one."""
    return dash_card(title)


def _clear_layout(layout):
    """Recursively empties a layout (widgets deleted, nested layouts
    cleared too) so a card body can be rebuilt in place -- e.g. after
    the date-range filter changes or reload_data() re-fetches from the
    server -- without leaking widgets or re-nesting layouts on top of
    the old ones."""
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.deleteLater()
            continue
        child_layout = item.layout()
        if child_layout is not None:
            _clear_layout(child_layout)


def _clear_card_body(layout):
    """Same as _clear_layout(), except it leaves index 0 alone. Every
    card layout here comes from dash_card(title), which already put
    the card's title QLabel in as that layout's first item (see
    ui.components.widgets.dash_card) -- clearing the whole layout on
    every rebuild would delete the title itself the first time
    apply_range()/reload_data() ran, which is exactly why it was
    disappearing. Use this (not _clear_layout) for any card body that
    was created via _card()/dash_card()."""
    while layout.count() > 1:
        item = layout.takeAt(1)
        w = item.widget()
        if w is not None:
            w.deleteLater()
            continue
        child_layout = item.layout()
        if child_layout is not None:
            _clear_layout(child_layout)


class _OverviewRefreshSignals(QObject):
    """QRunnable can't emit signals itself -- same pattern as
    search_leads.py's _FilterWorkerSignals / earn_credits.py's
    _RewardsRefreshSignals."""
    finished = Signal(int, object, object, object)  # generation, leads, exports, searches


class _OverviewRefreshWorker(QRunnable):
    """Runs the three GETs showEvent() used to call synchronously (GET
    /leads, /exports, /searches) on a background thread instead of the
    GUI thread. Those three round trips blocking on_click of the
    Dashboard nav item every single time is what made switching into this
    tab (and, before this, Earn Credits) visibly freeze the app for a
    moment -- see earn_credits.py's _RewardsRefreshWorker for the same
    fix applied there first."""

    def __init__(self, generation: int, fetch_leads, fetch_exports, fetch_searches):
        super().__init__()
        self.generation = generation
        self._fetch_leads = fetch_leads
        self._fetch_exports = fetch_exports
        self._fetch_searches = fetch_searches
        self.signals = _OverviewRefreshSignals()

    def run(self):
        leads = self._fetch_leads()
        exports = self._fetch_exports()
        searches = self._fetch_searches()
        self.signals.finished.emit(self.generation, leads, exports, searches)


class OverviewPage(QScrollArea):
    """
    The "Dashboard" nav tab: KPI stat cards up top, then a grid of chart
    cards below. Only ever constructed for user_role == "user" -- see
    dashboard.py.

    self.all_leads / self.all_exports / self.all_searches are the raw,
    unfiltered collections fetched from the server (GET /leads,
    GET /exports, GET /searches); every card on the page is derived from
    those three lists by apply_range(), which re-slices them by the
    header's date-range dropdown and rebuilds each card body in place.
    """

    RANGE_PRESETS = ("Today", "Last 7 Days", "Last 30 Days", "This Month", "This Year")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DashScroll")
        self.setWidgetResizable(True)

        self.all_leads = self._fetch_leads()
        self.all_exports = self._fetch_exports()
        self.all_searches = self._fetch_searches()
        self._last_range = "Last 7 Days"
        # Bumped on every background refresh dispatch; a worker only
        # applies its result if its generation still matches when it
        # finishes (see _on_reload_result), so a stale in-flight refresh
        # from an earlier show can't overwrite a newer one.
        self._refresh_generation = 0

        content = QWidget()
        outer = QVBoxLayout(content)
        outer.setContentsMargins(20, 16, 20, 20)
        outer.setSpacing(16)

        header_row = QHBoxLayout()
        header_col = QVBoxLayout()
        title_lbl = QLabel("Dashboard")
        title_lbl.setObjectName("HeaderTitle")
        header_col.addWidget(title_lbl)
        sub_lbl = QLabel("Overview of your lead generation activity")
        sub_lbl.setObjectName("HeaderSub")
        header_col.addWidget(sub_lbl)
        header_row.addLayout(header_col)
        header_row.addStretch()

        date_filter_wrap = QWidget()
        date_filter_wrap.setStyleSheet("background: transparent;")
        date_filter_row = QHBoxLayout(date_filter_wrap)
        date_filter_row.setContentsMargins(0, 0, 0, 0)
        date_filter_row.setSpacing(6)

        calendar_icon = QLabel()
        calendar_icon.setStyleSheet("background: transparent;")
        calendar_icon.setPixmap(qta.icon('fa5s.calendar-alt', color=PALETTE['text_muted']).pixmap(12, 12))
        date_filter_row.addWidget(calendar_icon)

        self.date_range_btn = QPushButton("Last 7 Days")
        self.date_range_btn.setObjectName("RowModeCombo")
        self.date_range_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.date_range_btn.setMinimumWidth(130)
        self.date_range_btn.setFixedHeight(30)
        self.date_range_btn.clicked.connect(self.open_date_range_menu)
        date_filter_row.addWidget(self.date_range_btn)

        header_row.addWidget(date_filter_wrap, alignment=Qt.AlignmentFlag.AlignVCenter)

        outer.addLayout(header_row)

        # --- Stat cards row ---
        self.stats_row = QHBoxLayout()
        self.stats_row.setSpacing(14)
        outer.addLayout(self.stats_row)

        # --- Leads over time + Leads by category row ---
        row1 = QHBoxLayout()
        row1.setSpacing(14)

        self.area_card, self.area_layout = _card("Leads Over Time")
        row1.addWidget(self.area_card, stretch=2)

        self.donut_card, self.donut_layout = _card("Leads by Category")
        row1.addWidget(self.donut_card, stretch=1)

        outer.addLayout(row1)

        # --- Lead sources / Recent exports / Leads by rating row ---
        # No AlignTop here -- these three should sit at equal *container*
        # height (a QHBoxLayout stretches each QFrame to the row's tallest
        # sibling by default). The content inside each card stays packed
        # at its own natural size instead of being stretched to fill that
        # extra height: each _rebuild_* method below ends with
        # layout.addStretch() on the card's own QVBoxLayout, so the card
        # frame grows but the rows inside it (e.g. "Recent Exports" with
        # zero rows next to "Leads by Rating"'s chart) don't get pulled
        # apart to fill the gap.
        row2 = QHBoxLayout()
        row2.setSpacing(14)

        self.sources_card, self.sources_layout = _card("Lead Sources")
        row2.addWidget(self.sources_card, stretch=1)

        self.exports_card, self.exports_layout = _card("Recent Exports")
        row2.addWidget(self.exports_card, stretch=1)

        self.rating_card, self.rating_layout = _card("Leads by Rating")
        row2.addWidget(self.rating_card, stretch=1)

        outer.addLayout(row2)

        # --- Top rated / Recent activity row -- same reasoning as row2
        # above: equal container height, content kept unstretched via
        # addStretch() in each _rebuild_* method. ---
        row3 = QHBoxLayout()
        row3.setSpacing(14)

        self.top_card, self.top_layout = _card("Top Rated Businesses")
        row3.addWidget(self.top_card, stretch=1)

        self.activity_card, self.activity_layout = _card("Recent Activity")
        row3.addWidget(self.activity_card, stretch=1)

        outer.addLayout(row3)

        outer.addStretch()
        self.setWidget(content)

        # Populate everything for the default range before wiring the
        # menu's signal, so the first fill doesn't round-trip through
        # on_date_range_changed()'s Custom-Range guard.
        self.apply_range(self.date_range_btn.text())

    def showEvent(self, event):
        """Pages are built once and cached (see dashboard.py's
        _ensure_page_built) -- reloading from the server on every real
        show (three cheap GETs) keeps this tab in sync with searches,
        unlocks, and exports made elsewhere since the last visit,
        instead of freezing at whatever existed when the tab was first
        built. Dispatched to a background thread (see
        _OverviewRefreshWorker) rather than run here directly, so
        clicking the Dashboard nav item doesn't block the GUI thread for
        those three round trips."""
        super().showEvent(event)
        self.reload_data()

    # ------------------------------------------------------------------
    # Server sync
    # ------------------------------------------------------------------
    def _fetch_leads(self) -> list:
        try:
            return list_all_leads()
        except ApiError:
            return []

    def _fetch_exports(self) -> list:
        try:
            return list_exports()
        except ApiError:
            return []

    def _fetch_searches(self) -> list:
        try:
            return list_searches()
        except ApiError:
            return []

    def reload_data(self):
        self._refresh_generation += 1
        worker = _OverviewRefreshWorker(
            self._refresh_generation, self._fetch_leads, self._fetch_exports, self._fetch_searches,
        )
        worker.signals.finished.connect(self._on_reload_result)
        QThreadPool.globalInstance().start(worker)

    def _on_reload_result(self, generation: int, leads: list, exports: list, searches: list):
        # A newer reload_data() call already superseded this one -- drop
        # it silently rather than showing stale data over fresher data.
        if generation != self._refresh_generation:
            return
        self.all_leads = leads
        self.all_exports = exports
        self.all_searches = searches
        self.apply_range(self._last_range)

    # ------------------------------------------------------------------
    # Date-range filter
    # ------------------------------------------------------------------
    def open_date_range_menu(self):
        """Popup menu replacing the old QComboBox -- same preset list,
        but as a real dropdown menu anchored under the button instead of
        a combo box (avoids fighting Qt's combo box padding/height
        quirks just to show five fixed options)."""
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background-color: {PALETTE['bg_surface']}; border: 1px solid {PALETTE['border']}; "
            f"border-radius: 8px; padding: 4px; color: {PALETTE['text_secondary']}; }}"
            "QMenu::item { padding: 6px 12px; border-radius: 4px; }"
            f"QMenu::item:selected {{ background-color: {PALETTE['bg_hover']}; }}"
        )
        for label in self.RANGE_PRESETS:
            action = QAction(label, self)
            action.triggered.connect(lambda _checked=False, lbl=label: self.on_date_range_changed(lbl))
            menu.addAction(action)

        pos = self.date_range_btn.mapToGlobal(QPoint(0, self.date_range_btn.height() + 4))
        menu.exec(pos)

    def on_date_range_changed(self, range_label: str):
        self.date_range_btn.setText(range_label)
        self.apply_range(range_label)

    @staticmethod
    def _range_cutoff(range_label: str):
        now = datetime.utcnow()
        if range_label == "Today":
            return datetime(now.year, now.month, now.day)
        if range_label == "Last 7 Days":
            return now - timedelta(days=7)
        if range_label == "Last 30 Days":
            return now - timedelta(days=30)
        if range_label == "This Month":
            return datetime(now.year, now.month, 1)
        if range_label == "This Year":
            return datetime(now.year, 1, 1)
        return None

    def _filter_since(self, items: list, cutoff) -> list:
        if cutoff is None:
            return list(items)
        out = []
        for item in items:
            d = _parse_dt(item.get("created_at"))
            if d is not None and d >= cutoff:
                out.append(item)
        return out

    def apply_range(self, range_label: str):
        self._last_range = range_label
        cutoff = self._range_cutoff(range_label)
        leads = self._filter_since(self.all_leads, cutoff)
        exports = self._filter_since(self.all_exports, cutoff)
        searches = self._filter_since(self.all_searches, cutoff)

        self._rebuild_stats(leads, exports)
        self._rebuild_leads_over_time(leads, range_label)
        self._rebuild_category_donut(leads)
        self._rebuild_lead_sources(leads)
        self._rebuild_recent_exports(exports)
        self._rebuild_leads_by_rating(leads)
        self._rebuild_top_rated(leads)
        self._rebuild_recent_activity(searches, exports)

    # ------------------------------------------------------------------
    # "+x% vs last 7 days" -- always computed off the full, unfiltered
    # history (not whatever range is selected), same as
    # exports.py._trend_pct: it's a fixed week-over-week comparison, not
    # something the date-range dropdown should also be slicing.
    # ------------------------------------------------------------------
    @staticmethod
    def _trend_pct(values: list) -> str | None:
        now = datetime.utcnow()
        this_week = now - timedelta(days=7)
        last_week = now - timedelta(days=14)

        this_count = prior_count = 0
        for raw in values:
            d = _parse_dt(raw)
            if d is None:
                continue
            if d >= this_week:
                this_count += 1
            elif d >= last_week:
                prior_count += 1

        if prior_count == 0:
            return None
        change = (this_count - prior_count) / prior_count * 100
        sign = "+" if change >= 0 else ""
        return f"{sign}{change:.1f}%"

    # ------------------------------------------------------------------
    # Card rebuilders -- each clears its layout and repopulates from the
    # (already range-filtered) leads/exports/searches passed in.
    # ------------------------------------------------------------------
    def _rebuild_stats(self, leads: list, exports: list):
        _clear_layout(self.stats_row)

        total_leads = len(leads)
        rated = [l["rating"] for l in leads if l.get("rating") is not None]
        avg_rating = (sum(rated) / len(rated)) if rated else None
        unlocked = sum(1 for l in leads if l.get("unlocked_phone") or l.get("unlocked_email"))
        total_reviews = sum(l.get("reviews") or 0 for l in leads)
        exports_count = len(exports)

        leads_delta = self._trend_pct([l.get("created_at") for l in self.all_leads])
        exports_delta = self._trend_pct([e.get("created_at") for e in self.all_exports])

        cards = [
            ("fa5s.users", "rgba(239,68,68,0.15)", "COLOR_RED",
             "Total Leads Found", f"{total_leads:,}", leads_delta),
            ("fa5s.star", "rgba(250,204,21,0.15)", "COLOR_YELLOW",
             "Average Rating", f"{avg_rating:.2f}" if avg_rating is not None else "—", None),
            ("fa5s.lock", "rgba(34,197,94,0.15)", "COLOR_GREEN",
             "Unlocked Contacts", f"{unlocked:,}", None),
            ("fa5s.file-alt", "rgba(96,165,250,0.15)", "COLOR_BLUE",
             "Exports", f"{exports_count:,}", exports_delta),
            ("fa5s.star", "rgba(168,85,247,0.15)", "COLOR_PURPLE",
             "Total Reviews", f"{total_reviews:,}", None),
        ]
        for icon, bg, color, label, value, delta in cards:
            self.stats_row.addWidget(StatCard(icon, bg, _c(color), label, value, delta))

    def _rebuild_leads_over_time(self, leads: list, range_label: str = "Last 7 Days"):
        _clear_card_body(self.area_layout)
        now = datetime.utcnow()

        if range_label == "This Year":
            month_counts = {}
            for l in leads:
                d = _parse_dt(l.get("created_at"))
                if d is not None:
                    month_counts[d.month] = month_counts.get(d.month, 0) + 1
            series = []
            for m in range(1, now.month + 1):
                label = datetime(now.year, m, 1).strftime("%b")
                series.append((label, month_counts.get(m, 0)))
        else:
            if range_label == "Today":
                window_days = 1
            elif range_label == "Last 30 Days":
                window_days = 30
            elif range_label == "This Month":
                window_days = now.day
            else:  # "Last 7 Days" (and fallback default)
                window_days = 7

            day_counts = {}
            for l in leads:
                d = _parse_dt(l.get("created_at"))
                if d is not None:
                    day_counts[d.date()] = day_counts.get(d.date(), 0) + 1
            series = []
            for i in range(window_days - 1, -1, -1):
                day = (now - timedelta(days=i)).date()
                series.append((day.strftime("%b %d"), day_counts.get(day, 0)))

        self.area_layout.addWidget(AreaChartWidget(series))

    def _rebuild_category_donut(self, leads: list):
        _clear_card_body(self.donut_layout)

        total = len(leads)
        counts: dict[str, int] = {}
        for l in leads:
            cat = l.get("category") or "Uncategorized"
            counts[cat] = counts.get(cat, 0) + 1
        ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
        top, rest = ranked[:3], ranked[3:]
        other = sum(c for _, c in rest)

        palette_cycle = ["COLOR_RED_SOLID", "COLOR_BLUE", "COLOR_GREEN", "COLOR_YELLOW"]
        entries = [(name, count, palette_cycle[i % len(palette_cycle)]) for i, (name, count) in enumerate(top)]
        if other > 0:
            entries.append(("Other", other, palette_cycle[len(top) % len(palette_cycle)]))

        donut_row = QHBoxLayout()
        segments = [{"pct": round(c / total * 100) if total else 0, "color": _c(color)} for _, c, color in entries]
        donut_row.addWidget(DonutChartWidget(segments, f"{total:,}", "Total Leads"))
        legend_col = QVBoxLayout()
        if entries:
            for name, count, color in entries:
                pct = round(count / total * 100) if total else 0
                legend_col.addWidget(_dash_legend_row(name, count, pct, _c(color)))
        else:
            legend_col.addWidget(_empty_row("No leads yet -- run a search to populate this."))
        donut_row.addLayout(legend_col, stretch=1)
        donut_row.setAlignment(legend_col, Qt.AlignmentFlag.AlignVCenter)
        self.donut_layout.addLayout(donut_row)

    def _rebuild_lead_sources(self, leads: list):
        _clear_card_body(self.sources_layout)
        total = len(leads)
        # Lead has no per-row `source` column yet -- the scraper only
        # ever produces Google Maps leads today, so this is a fixed
        # split rather than a real GROUP BY: Google Maps gets 100% of
        # whatever's actually been found, and sources that aren't wired
        # up to a scraper yet (LinkedIn, others) show 0% rather than a
        # made-up number. Swap for a real per-source breakdown once
        # Lead gets a `source` field and a second scraper exists.
        sources = [
            ("Google Maps", total, 100 if total else 0, "COLOR_RED_SOLID"),
            ("LinkedIn", 0, 0, "COLOR_BLUE"),
            ("Other", 0, 0, "COLOR_GREEN"),
        ]
        for label, value, pct, color in sources:
            self.sources_layout.addWidget(_dash_progress_row(label, f"{value:,}", pct, _c(color)))
        self.sources_layout.addStretch()

    def _rebuild_recent_exports(self, exports: list):
        _clear_card_body(self.exports_layout)
        ordered = sorted(exports, key=lambda e: e.get("created_at") or "", reverse=True)[:3]
        if not ordered:
            self.exports_layout.addWidget(_empty_row("No exports yet."))
            self.exports_layout.addStretch()
            return
        for e in ordered:
            row_item = {
                "name": e["file_name"],
                "meta": e.get("created_at", ""),
                "count": f'{e["leads_count"]:,} leads' if e.get("leads_count") is not None else "—",
            }
            self.exports_layout.addWidget(_dash_export_row(row_item))
        self.exports_layout.addStretch()

    def _rebuild_leads_by_rating(self, leads: list):
        _clear_card_body(self.rating_layout)
        buckets = {5: 0, 4: 0, 3: 0, 2: 0, 1: 0}
        for l in leads:
            r = l.get("rating")
            if r is None:
                continue
            bucket = min(5, max(1, round(r)))
            buckets[bucket] += 1
        series = [(str(k), buckets[k]) for k in sorted(buckets, reverse=True)]
        self.rating_layout.addWidget(VerticalBarChart(series, colors=[PALETTE['green']], suffix=" star"))
        self.rating_layout.addStretch()

    def _rebuild_top_rated(self, leads: list):
        _clear_card_body(self.top_layout)
        rated = [l for l in leads if l.get("rating") is not None]
        rated.sort(key=lambda l: (l["rating"], l.get("reviews") or 0), reverse=True)
        top5 = rated[:5]
        if not top5:
            self.top_layout.addWidget(_empty_row("No rated leads yet -- run a search to populate this."))
            self.top_layout.addStretch()
            return
        for i, biz in enumerate(top5, start=1):
            entry = {
                "name": biz["name"],
                "rating": biz["rating"],
                "reviews": biz.get("reviews") or 0,
                "status": biz.get("status") or "Unknown",
            }
            self.top_layout.addWidget(_dash_top_rated_row(i, entry))
        self.top_layout.addStretch()

    def _rebuild_recent_activity(self, searches: list, exports: list):
        _clear_card_body(self.activity_layout)
        events = []
        for s in searches:
            events.append({
                "icon": "fa5s.check-circle", "color": "COLOR_GREEN",
                "text": "Search completed",
                "meta": s.get("title") or s.get("query_text") or "Untitled search",
                "sort_key": s.get("created_at") or "",
            })
        for e in exports:
            events.append({
                "icon": "fa5s.download", "color": "COLOR_GREEN",
                "text": "Export completed",
                "meta": e.get("file_name", ""),
                "sort_key": e.get("created_at") or "",
            })
        events.sort(key=lambda ev: ev["sort_key"], reverse=True)
        events = events[:5]

        if not events:
            self.activity_layout.addWidget(_empty_row("No activity yet."))
            self.activity_layout.addStretch()
            return

        for ev in events:
            d = _parse_dt(ev["sort_key"])
            entry = {
                "icon": ev["icon"],
                "color": ev["color"],
                "text": ev["text"],
                "meta": ev["meta"],
                "time": d.strftime("%b %d, %I:%M %p").replace(" 0", " ") if d else "",
            }
            self.activity_layout.addWidget(_dash_activity_row(entry))
        self.activity_layout.addStretch()