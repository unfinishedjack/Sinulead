"""
exports.py

ExportsPage: the "Exports" nav tab -- KPI stat cards, an Export Summary
donut, a filterable/sortable/paginated table of every export (mirrors the
same search + pagination pattern as search_leads.SearchLeadsPage, just
simpler -- no detail panel, no per-cell unlock widgets), plus a right-hand
sidebar of static reference cards (File Formats / Export Tips / Need Help).

Backed by a real database table now (server/app/db.py's Export model,
behind GET/POST/PATCH/DELETE /exports -- see core.api_client's
list_exports/rename_export/delete_export), not the old in-memory
prototype list. self.exports is loaded from the server in __init__ (and
after every mutation) instead of copying data.EXPORTS:
  - Download re-fetches that export's leads from the account (GET
    /searches/{id}/leads, via the Export row's search_id) and writes a
    fresh file wherever the user picks, in whichever of CSV/Excel/
    JSON/HTML they choose in the save dialog -- it doesn't depend on
    the original file still being on this machine, so it works the
    same after a reinstall or on a different device the account is
    logged into. Legacy export rows from before search_id was tracked
    fall back to re-opening the original local file, same as before,
    since there's nothing server-side left to regenerate from.
  - Delete Export calls DELETE /exports/{id} -- a real, permanent removal.
  - Rename prompts for a new name and calls PATCH /exports/{id}.
  - Retry (Failed rows only) explains that the original leads aren't kept
    server-side, so it can't regenerate the file -- and points back to the
    Search Leads tab's Export button instead of faking success.
  - "Filters" (date range, custom leads range) still isn't built -- that
    dialog says so honestly rather than pretending it's live.

Depends on: config, data, core.api_client, dialogs (InfoDialog).
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QFrame, QScrollArea, QTableWidget, QTableWidgetItem, QFileDialog,
    QHeaderView, QAbstractItemView, QComboBox, QMenu,
)
from PySide6.QtCore import Qt, QPoint, QSize, QUrl, QObject, QRunnable, QThreadPool, Signal
from PySide6.QtGui import QAction, QDesktopServices
import os
import re
import qtawesome as qta

from core import config
from core.config import PALETTE, COLUMNS
from core.api_client import (
    list_exports, rename_export, delete_export, get_search_leads, ApiError,
    log_export_activity,
)
from data import FILE_FORMATS, EXPORT_TIPS, EXPORT_GUIDE_SECTIONS
from data.lead_exporter import (
    export_leads, QT_FILE_DIALOG_FILTER, format_id_from_filter, ext_for_format,
)
from ui.dialogs.dialogs import InfoDialog, PromptDialog, GuideDialog
from ui.components.widgets import status_badge, dash_card, StatCard, DonutChartWidget, SearchLineEdit

_COLOR_KEY_MAP = {
    "COLOR_MUTED": "text_muted", "COLOR_RED": "red",
    "COLOR_RED_SOLID": "red_solid", "COLOR_GREEN": "green",
    "COLOR_BLUE": "blue", "COLOR_YELLOW": "yellow",
    "COLOR_PURPLE": "purple",
}


def _c(name: str) -> str:
    """data/exports.py's FILE_FORMATS stores color *names* as legacy
    COLOR_* strings (e.g. "COLOR_GREEN"), not "PALETTE['green']" --
    this maps a legacy name to the live PALETTE color, looked up fresh
    on every call so it tracks theme switches instead of freezing at
    import time. (Bug fix: this previously used a "PALETTE['green']"-style
    key convention that never matched what FILE_FORMATS actually stores,
    so every lookup silently fell through to the raw COLOR_* string and
    qtawesome rendered a default black icon instead of the real color.
    See PROGRESS.md's "Post-refactor fixes" section.)"""
    key = _COLOR_KEY_MAP.get(name)
    return PALETTE[key] if key is not None else name


def _card(title: str = None):
    """Thin wrapper over widgets.dash_card() (see PROGRESS.md, Phase 1c)."""
    return dash_card(title)


def _donut_legend_row(label: str, count: int, pct: float, color: str) -> QWidget:
    wrap = QWidget()
    wrap.setStyleSheet("background: transparent;")
    row = QHBoxLayout(wrap)
    row.setContentsMargins(0, 3, 0, 3)
    row.setSpacing(6)

    dot = QLabel()
    dot.setStyleSheet("background: transparent;")
    dot.setPixmap(qta.icon('fa5s.circle', color=color).pixmap(8, 8))
    row.addWidget(dot)

    label_lbl = QLabel(label)
    label_lbl.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 11px; background: transparent;")
    row.addWidget(label_lbl, stretch=1)

    count_lbl = QLabel(f"{count} ({pct:.1f}%)")
    count_lbl.setStyleSheet(f"color: {PALETTE['text_muted']}; font-size: 10px; background: transparent;")
    row.addWidget(count_lbl)
    return wrap


def _status_colors() -> dict:
    """Looked up fresh on every call so it tracks theme switches -- same
    pattern as transactions._status_colors()."""
    return {
        "Completed": (PALETTE["green_solid"], PALETTE["green_soft"]),
        "Processing": (PALETTE["yellow_solid"], PALETTE["yellow_soft"]),
        "Failed": (PALETTE["red_solid"], PALETTE["red_soft"]),
    }


def _status_pill(status: str) -> QWidget:
    """Thin wrapper over widgets.status_badge() (see PROGRESS.md, Phase 1b).
    The "Processing" status keeps its spinning-icon dot via status_badge's
    `icon` param; _status_colors() above stays local since it's genuinely
    exports-specific status vocab."""
    icon = 'fa5s.circle-notch' if status == "Processing" else None
    return status_badge(status, _status_colors(), icon=icon)


def _format_icon() -> dict:
    """Looked up fresh on every call so it tracks theme switches. No PDF
    entry -- data/lead_exporter.py never wrote PDFs (its EXPORT_FORMATS
    is csv/xlsx/json/html only), so the old "PDF" row here never matched
    a real export; HTML replaces it."""
    return {
        "CSV": ("fa5s.file-csv", PALETTE["green"]), "Excel": ("fa5s.file-excel", PALETTE["green"]),
        "JSON": ("fa5s.file-code", PALETTE["blue"]), "HTML": ("fa5s.globe", PALETTE["purple"]),
    }


def _export_from_api(row: dict) -> dict:
    """Maps one ExportOut dict (server/app/schemas.py) from
    core.api_client.list_exports()/rename_export()/delete's create call
    into the {"name","source","leads","format","status","date","time",
    "file_path","search_id","id"} shape the table/actions code below
    already expects -- the same field names the old data.EXPORTS
    prototype rows used, so populate_table()/refresh_stats() didn't
    need to change.

    search_id is included so _download_export() can tell a real,
    regenerate-from-the-account export apart from a legacy row with no
    server-side leads to fall back on -- ExportOut always carries it
    (server/app/schemas.py), this was just never copied over here, so
    every export looked legacy regardless of what the server actually
    had on record."""
    date_str, time_str = "\u2014", ""
    raw = row.get("created_at")
    if raw:
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(raw.replace("Z", ""))
            date_str = dt.strftime("%b %d, %Y")
            time_str = dt.strftime("%I:%M %p").lstrip("0")
        except ValueError:
            date_str = raw
    return {
        "id": row["id"],
        "name": row["file_name"],
        "source": row["source"],
        "leads": row.get("leads_count"),
        "format": row["format"],
        "status": row["status"],
        "date": date_str,
        "time": time_str,
        "file_path": row.get("file_path"),
        "search_id": row.get("search_id"),
        "created_at": raw,
    }


def _filename_cell(name: str, fmt: str) -> QWidget:
    wrap = QWidget()
    wrap.setStyleSheet("background: transparent;")
    row = QHBoxLayout(wrap)
    row.setContentsMargins(6, 0, 6, 0)
    row.setSpacing(8)
    icon_name, color = _format_icon().get(fmt, ("fa5s.file", PALETTE["text_muted"]))
    icon_lbl = QLabel()
    icon_lbl.setStyleSheet("background: transparent;")
    icon_lbl.setPixmap(qta.icon(icon_name, color=color).pixmap(14, 14))
    row.addWidget(icon_lbl)
    text_lbl = QLabel(name)
    text_lbl.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 11px; background: transparent;")
    row.addWidget(text_lbl, stretch=1)
    return wrap


def _format_cell(fmt: str) -> QWidget:
    wrap = QWidget()
    wrap.setStyleSheet("background: transparent;")
    row = QHBoxLayout(wrap)
    row.setContentsMargins(6, 0, 6, 0)
    row.setSpacing(6)
    icon_name, color = _format_icon().get(fmt, ("fa5s.file", PALETTE["text_muted"]))
    icon_lbl = QLabel()
    icon_lbl.setStyleSheet("background: transparent;")
    icon_lbl.setPixmap(qta.icon(icon_name, color=color).pixmap(12, 12))
    row.addWidget(icon_lbl)
    text_lbl = QLabel(fmt)
    text_lbl.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 11px; background: transparent;")
    row.addWidget(text_lbl, stretch=1)
    return wrap


class _ExportsRefreshSignals(QObject):
    """QRunnable can't emit signals itself -- same pattern as
    search_leads.py's _FilterWorkerSignals / earn_credits.py's
    _RewardsRefreshSignals / overview.py's _OverviewRefreshSignals."""
    finished = Signal(int, object)  # generation, exports


class _ExportsRefreshWorker(QRunnable):
    """Runs the GET /exports showEvent()/reload_exports() used to call
    synchronously on a background thread instead of the GUI thread --
    that round trip blocking every single Exports nav click (and every
    rename/delete) is what made switching into this tab visibly freeze
    the app for a moment. Same fix as overview.py's
    _OverviewRefreshWorker and earn_credits.py's _RewardsRefreshWorker."""

    def __init__(self, generation: int, fetch_exports):
        super().__init__()
        self.generation = generation
        self._fetch_exports = fetch_exports
        self.signals = _ExportsRefreshSignals()

    def run(self):
        exports = self._fetch_exports()
        self.signals.finished.emit(self.generation, exports)


class ExportsPage(QScrollArea):
    """
    The "Exports" nav tab. Owns:
      - self.exports -- the caller's real export history, loaded from
        GET /exports (see _load_exports()); Delete Export (via the row
        "..." menu) calls DELETE /exports/{id} and then reloads this
        list, same pattern as search_leads.SearchLeadsPage.remove_search().
      - self.status_filter / self.search_text -- current pill-tab + search
        box state, recombined by apply_filter() into self.current_data.
      - pagination state (page_size / current_page), same pattern as
        search_leads.SearchLeadsPage.
    """
    def __init__(self, parent=None, user_role: str = "user"):
        super().__init__(parent)
        self.setObjectName("DashScroll")
        self.setWidgetResizable(True)

        # Needed to decide whether a re-downloaded file should include
        # locked phone/email fields -- mirrors the same admin bypass
        # search_leads.py's export_current_search() uses (~line 880),
        # so a re-download never hands out contact info nobody unlocked.
        self.user_role = user_role

        self.status_filter = "All Exports"
        self.search_text = ""
        self.page_size = 10
        self.current_page = 1
        self.exports = self._fetch_exports()
        self.current_data = list(self.exports)
        # Bumped on every reload_exports() dispatch; a worker only
        # applies its result if its generation still matches when it
        # finishes (see _on_reload_result), so a stale in-flight refresh
        # can't overwrite a newer one.
        self._refresh_generation = 0
        content = QWidget()
        outer = QVBoxLayout(content)
        outer.setContentsMargins(20, 16, 20, 20)
        outer.setSpacing(14)

        # --- Header ---
        header_col = QVBoxLayout()
        header_col.setSpacing(2)
        title_lbl = QLabel("Exports")
        title_lbl.setObjectName("HeaderTitle")
        header_col.addWidget(title_lbl)
        sub_lbl = QLabel("View, manage, and download your exported lead data.")
        sub_lbl.setObjectName("HeaderSub")
        header_col.addWidget(sub_lbl)
        outer.addLayout(header_col)

        # --- Main content: table (left, KPI row on top) + reference
        # sidebar (right) -- both columns start at the same top level so
        # "Export Summary" sits beside the KPI row instead of below it.
        content_row = QHBoxLayout()
        content_row.setSpacing(14)
        content_row.addWidget(self._build_table_column(), stretch=3)
        content_row.addWidget(self._build_sidebar_column(), stretch=1)
        outer.addLayout(content_row)

        self.setWidget(content)

        self.refresh_stats()
        self.apply_filter()

    def showEvent(self, event):
        """Pages are built once and cached (see dashboard.py's
        _ensure_page_built/_schedule_background_prefetch) -- a page can
        even be built quietly in the background well before the user
        ever visits it, so the data __init__ loaded can already be
        stale by the time it's actually shown. Reloading from the
        server on every real show (cheap -- one GET) means the table
        always reflects exports made from the Search Leads tab since
        the last visit, not just whatever existed at construction time."""
        super().showEvent(event)
        self.reload_exports()

    # ------------------------------------------------------------------
    # Server sync -- self.exports always reflects GET /exports, the real
    # per-user Export table (server/app/db.py), not an in-memory list.
    # ------------------------------------------------------------------
    def _fetch_exports(self) -> list:
        """GET /exports, mapped into the row shape the rest of this page
        expects (see _export_from_api()). Degrades to an empty list on
        a connection problem/logged-out session -- same "don't lose an
        already-working screen over one failed call" pattern
        search_leads.SearchLeadsPage._load_searches_from_server() uses --
        rather than blocking the tab from opening at all."""
        try:
            rows = list_exports()
        except ApiError:
            return []
        return [_export_from_api(row) for row in rows]

    def reload_exports(self):
        """Re-fetches from the server and refreshes every dependent
        piece of UI (table, KPI cards, donut, and the Source dropdown,
        since a newly-logged export can introduce a source that wasn't
        in the list before). Called after any mutation (rename/delete)
        and on every real show (see showEvent()).

        Dispatched to a background thread (see _ExportsRefreshWorker)
        rather than run here directly, so it doesn't block the GUI
        thread -- most visibly on a plain Exports nav click, but also
        after rename/delete."""
        self._refresh_generation += 1
        worker = _ExportsRefreshWorker(self._refresh_generation, self._fetch_exports)
        worker.signals.finished.connect(self._on_reload_result)
        QThreadPool.globalInstance().start(worker)

    def _on_reload_result(self, generation: int, exports: list):
        # A newer reload_exports() call already superseded this one --
        # drop it silently rather than showing stale data over fresher
        # data.
        if generation != self._refresh_generation:
            return
        self.exports = exports
        self._refresh_source_options()
        self.apply_filter()
        self.refresh_stats()

    def _refresh_source_options(self):
        if not hasattr(self, "source_combo"):
            return
        current = self.source_combo.currentText()
        self.source_combo.blockSignals(True)
        self.source_combo.clear()
        sources = ["All Sources"] + sorted({e["source"] for e in self.exports})
        self.source_combo.addItems(sources)
        idx = self.source_combo.findText(current)
        self.source_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.source_combo.blockSignals(False)

    # ------------------------------------------------------------------
    # Stat cards + donut (both derived from self.exports, so they stay
    # correct after a Delete Export)
    # ------------------------------------------------------------------
    def _trend_pct(self, values: list) -> str:
        """Real "vs last 7 days" comparison, computed from each export's
        actual created_at instead of a fixed placeholder string -- counts
        (or sums, if `values` is a list of numbers rather than dates)
        for the last 7 days vs. the 7 days before that. Returns "" if
        there isn't enough history yet (fewer than 7 days of data, or no
        activity in the prior window to compare against) rather than
        showing a misleading percentage."""
        from datetime import datetime, timedelta
        now = datetime.utcnow()
        this_week = now - timedelta(days=7)
        last_week = now - timedelta(days=14)

        this_count = prior_count = 0
        for raw, weight in values:
            if not raw:
                continue
            try:
                dt = datetime.fromisoformat(raw.replace("Z", ""))
            except ValueError:
                continue
            if dt >= this_week:
                this_count += weight
            elif dt >= last_week:
                prior_count += weight

        if prior_count == 0:
            return ""
        change = (this_count - prior_count) / prior_count * 100
        sign = "+" if change >= 0 else ""
        return f"{sign}{change:.1f}% vs last 7 days"

    def refresh_stats(self):
        while self.stats_row.count():
            item = self.stats_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        total = len(self.exports)
        completed = [e for e in self.exports if e["status"] == "Completed"]
        processing = [e for e in self.exports if e["status"] == "Processing"]
        failed = [e for e in self.exports if e["status"] == "Failed"]
        total_leads = sum(e["leads"] or 0 for e in self.exports)

        completed_pct = (len(completed) / total * 100) if total else 0
        failed_pct = (len(failed) / total * 100) if total else 0

        exports_trend = self._trend_pct([(e.get("created_at"), 1) for e in self.exports])
        leads_trend = self._trend_pct([(e.get("created_at"), e["leads"] or 0) for e in self.exports])

        cards = [
            ("fa5s.file-alt", config.rgba_from_hex(PALETTE['blue'], 0.15), PALETTE['blue'], "Total Exports",
             f"{total}", exports_trend or "No prior-week data yet", PALETTE['green'] if exports_trend.startswith("+") else PALETTE['text_dim']),
            ("fa5s.check-circle", config.rgba_from_hex(PALETTE['green'], 0.15), PALETTE['green'], "Completed",
             f"{len(completed)}", f"{completed_pct:.1f}% success rate", PALETTE['green']),
            ("fa5s.clock", config.rgba_from_hex(PALETTE['yellow'], 0.15), PALETTE['yellow'], "Processing",
             f"{len(processing)}", "In progress", PALETTE['text_dim']),
            ("fa5s.ban", config.rgba_from_hex(PALETTE['red_solid'], 0.15), PALETTE['red'], "Failed",
             f"{len(failed)}", f"{failed_pct:.1f}% failure rate", PALETTE['red']),
            ("fa5s.download", config.rgba_from_hex(PALETTE['purple'], 0.15), PALETTE['purple'], "Total Leads Exported",
             f"{total_leads:,}", leads_trend or "No prior-week data yet", PALETTE['green'] if leads_trend.startswith("+") else PALETTE['text_dim']),
        ]
        for icon, bg, color, label, value, sub_text, sub_color in cards:
            self.stats_row.addWidget(StatCard(icon, bg, color, label, value, sub_text, sub_color))

        # Also refresh the Export Summary donut, if it's been built yet.
        if hasattr(self, "donut_slot"):
            self._refresh_summary_card(total, len(completed), len(processing), len(failed))

    # ------------------------------------------------------------------
    # Right-hand sidebar: Export Summary / File Formats / Export Tips /
    # Need Help -- all static reference cards, no filtering.
    # ------------------------------------------------------------------
    def _build_sidebar_column(self) -> QWidget:
        col = QWidget()
        col.setMinimumWidth(300)
        col.setMaximumWidth(360)
        layout = QVBoxLayout(col)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        self.summary_card, summary_layout = _card("Export Summary")
        summary_row = QHBoxLayout()
        summary_row.setSpacing(14)
        self.donut_slot = QVBoxLayout()
        summary_row.addLayout(self.donut_slot)
        self.donut_legend_slot = QVBoxLayout()
        self.donut_legend_slot.setSpacing(2)
        summary_row.addLayout(self.donut_legend_slot, stretch=1)
        summary_layout.addLayout(summary_row)
        layout.addWidget(self.summary_card)

        formats_card, formats_layout = _card("File Formats")
        for fmt in FILE_FORMATS:
            row = QHBoxLayout()
            row.setSpacing(10)
            icon_box = QFrame()
            icon_box.setFixedSize(28, 28)
            icon_box.setStyleSheet(f"background-color: rgba(0,0,0,0.15); border-radius: 6px;")
            icon_box_layout = QHBoxLayout(icon_box)
            icon_box_layout.setContentsMargins(0, 0, 0, 0)
            icon_box_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            icon_lbl = QLabel()
            icon_lbl.setStyleSheet("background: transparent;")
            icon_lbl.setPixmap(qta.icon(fmt["icon"], color=_c(fmt["color"])).pixmap(14, 14))
            icon_box_layout.addWidget(icon_lbl)
            row.addWidget(icon_box)

            text_col = QVBoxLayout()
            text_col.setSpacing(1)
            name_lbl = QLabel(fmt["label"])
            name_lbl.setWordWrap(True)
            name_lbl.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 11px; font-weight: 600; background: transparent;")
            text_col.addWidget(name_lbl)
            desc_lbl = QLabel(fmt["desc"])
            desc_lbl.setWordWrap(True)
            desc_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 10px; background: transparent;")
            text_col.addWidget(desc_lbl)
            row.addLayout(text_col, stretch=1)

            formats_layout.addLayout(row)
        layout.addWidget(formats_card)

        tips_card, tips_layout = _card("Export Tips")
        for tip in EXPORT_TIPS:
            row = QHBoxLayout()
            row.setSpacing(8)
            check_lbl = QLabel()
            check_lbl.setStyleSheet("background: transparent;")
            check_lbl.setPixmap(qta.icon('fa5s.check-circle', color=PALETTE['green']).pixmap(11, 11))
            row.addWidget(check_lbl, alignment=Qt.AlignmentFlag.AlignTop)
            tip_lbl = QLabel(tip)
            tip_lbl.setWordWrap(True)
            tip_lbl.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 11px; background: transparent;")
            row.addWidget(tip_lbl, stretch=1)
            tips_layout.addLayout(row)
        layout.addWidget(tips_card)

        help_card, help_layout = _card()
        help_title = QLabel("Need help with exports?")
        help_title.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 12px; font-weight: 600; background: transparent;")
        help_layout.addWidget(help_title)
        guide_btn = QPushButton(qta.icon('fa5s.external-link-alt', color=PALETTE['red']), " View our guide")
        guide_btn.setObjectName("LinkBtn")
        guide_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        guide_btn.clicked.connect(self._open_guide)
        help_layout.addWidget(guide_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(help_card)

        layout.addStretch()
        return col

    def _refresh_summary_card(self, total, completed, processing, failed):
        while self.donut_slot.count():
            item = self.donut_slot.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        while self.donut_legend_slot.count():
            item = self.donut_legend_slot.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        segments = []
        legend = [
            ("Completed", completed, PALETTE['green']),
            ("Processing", processing, PALETTE['yellow']),
            ("Failed", failed, PALETTE['red']),
        ]
        for _, count, color in legend:
            pct = (count / total * 100) if total else 0
            if count:
                segments.append({"pct": pct, "color": color})

        self.donut_slot.addWidget(DonutChartWidget(
            segments, f"{total}", "Total",
            size=150, thickness=18, value_color="white", value_font_size=14,
        ))
        for label, count, color in legend:
            pct = (count / total * 100) if total else 0
            self.donut_legend_slot.addWidget(_donut_legend_row(label, count, pct, color))
        self.donut_legend_slot.addStretch()

    def _open_guide(self):
        GuideDialog.show(self, "Exports Guide", EXPORT_GUIDE_SECTIONS)

    # ------------------------------------------------------------------
    # Left column: pill tabs + search bar + table + pagination footer
    # ------------------------------------------------------------------
    def _build_table_column(self) -> QWidget:
        col = QWidget()
        layout = QVBoxLayout(col)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        # --- Stat cards row -- lives in this column (not the outer page
        # layout) so it lines up with the table, and the sidebar's "Export
        # Summary" card starts at this same top level instead of below it.
        self.stats_row = QHBoxLayout()
        self.stats_row.setSpacing(14)
        layout.addLayout(self.stats_row)

        # --- Pill tab bar ---
        tab_bar = QHBoxLayout()
        tab_bar.setSpacing(4)
        self.tab_buttons = {}
        for name in ("All Exports", "Completed", "Processing", "Failed"):
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

        # --- Search + date range + source + filters row ---
        search_row = QHBoxLayout()
        search_row.setSpacing(8)

        self.search_edit = SearchLineEdit()
        self.search_edit.setPlaceholderText("Search exports by filename or source... (press Enter)")
        # Search only runs on explicit action -- Enter, or clicking the
        # magnifying-glass icon -- not on every keystroke.
        self.search_edit.search_triggered.connect(self._trigger_search)
        self.search_edit.returnPressed.connect(self._trigger_search)
        search_row.addWidget(self.search_edit, stretch=1)

        self.source_combo = QComboBox()
        self.source_combo.setObjectName("RowModeCombo")
        sources = ["All Sources"] + sorted({e["source"] for e in self.exports})
        self.source_combo.addItems(sources)
        self.source_combo.setMinimumWidth(140)
        self.source_combo.currentTextChanged.connect(self.on_source_changed)
        search_row.addWidget(self.source_combo)

        filters_btn = QPushButton(qta.icon('fa5s.filter', color=PALETTE['text_muted']), " Filters")
        filters_btn.setObjectName("OutlineBtn")
        filters_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        filters_btn.clicked.connect(self._open_filters_not_wired_up)
        search_row.addWidget(filters_btn)

        layout.addLayout(search_row)

        # --- Table ---
        # Row-number column first, same pattern as search_leads.COL_ROWNUM,
        # so the two tabs' tables line up visually.
        self.columns = [
            {"label": "#", "key": None},
            {"label": "File Name", "key": "name"},
            {"label": "Source / Search", "key": "source"},
            {"label": "Leads", "key": "leads"},
            {"label": "Format", "key": "format"},
            {"label": "Status", "key": "status"},
            {"label": "Date Exported", "key": "date"},
            {"label": "Actions", "key": None},
        ]
        (self.COL_ROWNUM, self.COL_NAME, self.COL_SOURCE, self.COL_LEADS,
         self.COL_FORMAT, self.COL_STATUS, self.COL_DATE, self.COL_ACTIONS) = range(8)

        self.table = QTableWidget()
        self.table.setColumnCount(len(self.columns))
        self.table.setHorizontalHeaderLabels([c["label"] for c in self.columns])
        self.table.verticalHeader().setVisible(False)
        # Rows were reading as "compressed" because the default row height
        # only fits a single line, while the Date Exported cell wraps a
        # date + time onto two lines -- give every row breathing room.
        self.table.verticalHeader().setDefaultSectionSize(52)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(self.COL_ROWNUM, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(self.COL_ROWNUM, 36)
        for col_idx, width in [
            (self.COL_NAME, 220), (self.COL_LEADS, 80), (self.COL_FORMAT, 90),
            (self.COL_STATUS, 110), (self.COL_DATE, 150), (self.COL_ACTIONS, 90),
        ]:
            header.setSectionResizeMode(col_idx, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(col_idx, width)
        # Let "Source / Search" absorb the extra space instead of stretching
        # the last (Actions) column -- stretching Actions let it get
        # squeezed below the width its two icon buttons need whenever the
        # window was narrower than the sum of the other columns.
        header.setSectionResizeMode(self.COL_SOURCE, QHeaderView.ResizeMode.Stretch)
        header.setMinimumSectionSize(36)
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

        self.set_status_filter("All Exports")
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

    def on_source_changed(self, source: str):
        self.current_page = 1
        self.apply_filter()

    def apply_filter(self):
        filtered = list(self.exports)

        if self.status_filter != "All Exports":
            filtered = [e for e in filtered if e["status"] == self.status_filter]

        source = self.source_combo.currentText() if hasattr(self, "source_combo") else "All Sources"
        if source and source != "All Sources":
            filtered = [e for e in filtered if e["source"] == source]

        if self.search_text:
            filtered = [
                e for e in filtered
                if self.search_text in e["name"].lower() or self.search_text in e["source"].lower()
            ]

        self.current_data = filtered
        self.populate_table()

    def _open_filters_not_wired_up(self):
        InfoDialog.show(
            self, "Filters",
            "Advanced export filters (date range, custom leads range, etc.) "
            "aren't wired up to a backend yet -- use the tabs, search box, "
            "and source dropdown above for now.",
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

    def populate_table(self):
        self.current_page = max(1, min(self.current_page, self.total_pages()))
        start = (self.current_page - 1) * self.page_size
        page_data = self.current_data[start:start + self.page_size]
        self._current_page_data = page_data

        self.table.setRowCount(len(page_data))
        for row, export in enumerate(page_data):
            row_num_item = QTableWidgetItem(str(start + row + 1))
            row_num_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, self.COL_ROWNUM, row_num_item)

            self.table.setItem(row, self.COL_NAME, QTableWidgetItem())
            self.table.setCellWidget(row, self.COL_NAME, _filename_cell(export["name"], export["format"]))

            self.table.setItem(row, self.COL_SOURCE, QTableWidgetItem(export["source"]))

            leads_item = QTableWidgetItem(f'{export["leads"]:,}' if export["leads"] else "\u2014")
            leads_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, self.COL_LEADS, leads_item)

            self.table.setItem(row, self.COL_FORMAT, QTableWidgetItem())
            self.table.setCellWidget(row, self.COL_FORMAT, _format_cell(export["format"]))

            self.table.setItem(row, self.COL_STATUS, QTableWidgetItem())
            self.table.setCellWidget(row, self.COL_STATUS, _status_pill(export["status"]))

            date_item = QTableWidgetItem(f'{export["date"]}\n{export["time"]}')
            self.table.setItem(row, self.COL_DATE, date_item)

            self.table.setItem(row, self.COL_ACTIONS, QTableWidgetItem())
            self.table.setCellWidget(row, self.COL_ACTIONS, self._actions_cell(export))

        total_results = len(self.current_data)
        showing_from = start + 1 if page_data else 0
        showing_to = start + len(page_data)
        self.footer_label.setText(f"Showing {showing_from} to {showing_to} of {total_results} results")

        self.render_page_buttons()

    def _actions_cell(self, export: dict) -> QWidget:
        wrap = QWidget()
        wrap.setStyleSheet("background: transparent;")
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        row.setAlignment(Qt.AlignmentFlag.AlignCenter)

        if export["status"] == "Failed":
            primary = QPushButton(qta.icon('fa5s.redo', color=PALETTE['text_muted']), "")
            primary.clicked.connect(lambda _c=False, e=export: self._retry_export(e))
        elif export["status"] == "Processing":
            primary = QPushButton(qta.icon('fa5s.circle-notch', color=PALETTE['text_muted']), "")
            primary.setEnabled(False)
        else:
            primary = QPushButton(qta.icon('fa5s.download', color=PALETTE['text_muted']), "")
            primary.clicked.connect(lambda _c=False, e=export: self._download_export(e))
        primary.setObjectName("OutlineBtn")
        primary.setCursor(Qt.CursorShape.PointingHandCursor)
        primary.setFixedSize(28, 28)
        row.addWidget(primary)

        more_btn = QPushButton(qta.icon('fa5s.ellipsis-v', color=PALETTE['text_muted']), "")
        more_btn.setObjectName("OutlineBtn")
        more_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        more_btn.setFixedSize(28, 28)
        more_btn.clicked.connect(lambda _c=False, e=export, b=more_btn: self._open_row_menu(e, b))
        row.addWidget(more_btn)

        return wrap

    def _open_row_menu(self, export: dict, anchor_btn: QPushButton):
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background-color: {PALETTE['bg_surface']}; border: 1px solid {PALETTE['border']}; "
            f"border-radius: 8px; padding: 4px; color: {PALETTE['text_secondary']}; }}"
            "QMenu::item { padding: 6px 12px; border-radius: 4px; }"
            f"QMenu::item:selected {{ background-color: {PALETTE['bg_hover']}; }}"
        )
        rename_action = QAction("Rename", self)
        rename_action.triggered.connect(lambda: self._rename_export(export))
        menu.addAction(rename_action)

        delete_action = QAction("Delete Export", self)
        delete_action.triggered.connect(lambda: self._delete_export(export))
        menu.addAction(delete_action)

        pos = anchor_btn.mapToGlobal(QPoint(0, anchor_btn.height() + 4))
        menu.exec(pos)

    def _rename_export(self, export: dict):
        new_name, ok = PromptDialog.get_text(
            self, "Rename Export", "File name:", export["name"], confirm_text="Rename",
        )
        new_name = new_name.strip()
        if not ok or not new_name or new_name == export["name"]:
            return
        try:
            rename_export(export["id"], new_name)
        except ApiError as e:
            InfoDialog.show(self, "Rename failed", str(e), icon_name='fa5s.exclamation-triangle', success=False)
            return

        try:
            log_export_activity(export["id"], "Export renamed", f'"{export["name"]}" \u2192 "{new_name}"')
        except ApiError:
            pass

        # Best-effort: also rename the file on disk if it's still there
        # at the path it was saved to, so the on-disk name matches what
        # the Exports tab now shows -- silently skipped if the file's
        # been moved/deleted since, since the history row is still the
        # source of truth either way.
        old_path = export.get("file_path")
        if old_path and os.path.isfile(old_path):
            new_path = os.path.join(os.path.dirname(old_path), new_name)
            try:
                os.rename(old_path, new_path)
            except OSError:
                pass

        self.reload_exports()

    def _download_export(self, export: dict):
        """Regenerates this export from the leads stored on the
        account (GET /searches/{id}/leads, via the Export row's
        search_id) and writes a fresh file wherever the user picks, in
        whichever of CSV/Excel/JSON/HTML they choose in the save
        dialog -- not just whatever format it was originally exported
        as. Since the leads live server-side rather than on any one
        machine's disk, this works after a reinstall or on a different
        device the same account is logged into, unlike re-opening a
        local file path.

        Export rows created before search_id was tracked have nothing
        server-side to regenerate from, so those fall back to
        re-opening the original local file (the old "View" behavior),
        and say so honestly if it's since been moved or deleted.
        """
        search_id = export.get("search_id")
        if search_id is None:
            self._open_local_copy(export)
            return

        default_name = export["name"] or "leads"
        path, selected_filter = QFileDialog.getSaveFileName(
            self, f'Download {export["name"]}', default_name, QT_FILE_DIALOG_FILTER,
        )
        if not path:
            return

        fmt = format_id_from_filter(selected_filter)
        expected_ext = ext_for_format(fmt)
        if not path.lower().endswith(expected_ext):
            path = re.sub(r"\.[^.\\/]*$", "", path) + expected_ext

        try:
            result = get_search_leads(search_id)
        except ApiError as e:
            InfoDialog.show(
                self, "Download failed",
                f'Couldn\'t retrieve the leads behind "{export["name"]}" from your '
                f"account: {e}",
                icon_name='fa5s.exclamation-triangle', success=False,
            )
            return

        leads = result.get("leads", [])
        if not leads:
            InfoDialog.show(
                self, "Nothing to download",
                f'"{export["name"]}" has no leads on record for this search anymore.',
                icon_name='fa5s.exclamation-triangle', success=False,
            )
            return

        # Maps Link (admin_only in COLUMNS) is stripped out for
        # non-admins here too -- otherwise re-downloading a past export
        # from this tab would hand back a column the Columns menu never
        # let them turn on in the first place (see search_leads.py's
        # open_columns_menu / export_current_search).
        is_admin = self.user_role == "admin"
        visible_cols = [
            c for c in COLUMNS
            if c["key"] and not (c.get("admin_only") and not is_admin)
        ]
        fieldnames = [c["key"] for c in visible_cols]
        headers = [c["label"] for c in visible_cols]

        # Same lock rule export_current_search() uses (search_leads.py
        # ~line 880): admins get every field, everyone else only gets
        # phone_num/email_addr on a lead once it's actually unlocked --
        # otherwise a re-download would hand out contact info nobody
        # spent credits on. (is_admin already computed above.)

        def _masked(lead: dict) -> dict:
            row = dict(lead)
            if not is_admin and lead.get("phone_num") and not lead.get("unlocked_phone", False):
                row["phone_num"] = "Locked"
            if not is_admin and lead.get("email_addr") and not lead.get("unlocked_email", False):
                row["email_addr"] = "Locked"
            return row

        rows = [_masked(lead) for lead in leads]

        try:
            export_leads(rows, fieldnames, headers, path, fmt)
        except (OSError, RuntimeError, ValueError) as e:
            InfoDialog.show(self, "Download failed", str(e), icon_name='fa5s.exclamation-triangle', success=False)
            return

        try:
            log_export_activity(export["id"], "Downloaded again", f"{len(rows)} lead(s) re-saved as {fmt.upper()}")
        except ApiError:
            pass

        InfoDialog.show(
            self, "Download complete",
            f"Saved {len(rows)} leads to:\n{path}",
            icon_name='fa5s.check-circle', success=True,
        )

    def _open_local_copy(self, export: dict):
        """Legacy fallback for export rows with no search_id on record
        (created before that column existed) -- opens the file at its
        originally-saved local path in whatever the OS's default app
        for that type is, and says so honestly if it's since been
        moved, renamed outside SinuLead, or deleted, since there's no
        server-side copy to regenerate from instead."""
        path = export.get("file_path")
        if not path or not os.path.isfile(path):
            InfoDialog.show(
                self, "File not found",
                f'"{export["name"]}" isn\'t at the location it was saved to anymore, '
                "and this export predates account-linked lead storage, so it can't be "
                "regenerated -- it may have been moved, renamed outside SinuLead, or deleted.",
                icon_name='fa5s.exclamation-triangle', success=False,
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _retry_export(self, export: dict):
        try:
            log_export_activity(
                export["id"], "Retry attempted",
                "Couldn't regenerate -- original leads weren't kept server-side for this export.",
            )
        except ApiError:
            pass
        InfoDialog.show(
            self, "Retry Export",
            f'"{export["name"]}" can\'t be regenerated automatically -- SinuLead only '
            "keeps a record of the export, not the original leads that went into it. "
            "Head back to the Search Leads tab and use Export again to create a new file.",
            icon_name='fa5s.redo', success=True,
        )

    def _delete_export(self, export: dict):
        try:
            delete_export(export["id"])
        except ApiError as e:
            InfoDialog.show(self, "Delete failed", str(e), icon_name='fa5s.exclamation-triangle', success=False)
            return
        self.reload_exports()

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
            # NOTE: intentionally NOT using setObjectName("RedBtn"/"OutlineBtn")
            # here -- those global styles carry 6px/12-14px of padding meant
            # for icon+label buttons like "Filters"/"Export". Combined with
            # the fixed 28x28 size that padding left almost no room for the
            # digit, so it rendered as an unreadable sliver. Inline
            # stylesheets (no padding), same as search_leads.py, fix this.
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