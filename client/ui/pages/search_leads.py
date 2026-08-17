"""
search_leads.py

SearchLeadsPage: the "Search Leads" nav tab -- Search Queries panel +
results table. This is everything that used to be inline in dashboard.py
before the Dashboard/Overview split; dashboard.py's Dashboard shell now
just builds the sidebar and switches between this page and
overview.OverviewPage in a QStackedWidget.

Owns its own credit balance (spent via on_unlock_row) and announces changes
via the credits_changed signal so the sidebar's credits box (built and
owned by Dashboard) can stay in sync without this page reaching back into
Dashboard directly.

Depends on: config, data, models, widgets, dialogs.
"""

import os
import random
import re

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QFrame, QAbstractItemView,
    QMenu, QSplitter, QDialog, QComboBox,
    QFileDialog, QPlainTextEdit, QCheckBox, QSizePolicy, QScrollArea,
    QApplication,
)
from PySide6.QtCore import Qt, QPoint, QSize, QEvent, Signal, QTimer, QObject, QRunnable, QThreadPool
from PySide6.QtGui import QAction, QCursor
import qtawesome as qta

from core.config import (
    PALETTE, INITIAL_CREDITS, COLUMNS,
    COL_ROWNUM, COL_CHECK, COL_NAME, COL_RATING, COL_REVIEWS, COL_CATEGORY,
    COL_PHONE, COL_WEBSITE, COL_EMAIL, COL_STATUS, COL_ADDRESS, COL_MAPS_LINK,
    DEFAULT_HIDDEN_COLUMNS,
)
from data.lead_exporter import export_leads, QT_FILE_DIALOG_FILTER, format_id_from_filter, ext_for_format
from core.models import (
    business_has_phone, business_has_email, business_contact_cost,
    business_field_cost, business_fully_unlocked, apply_unlock,
    ensure_unlock_state, bulk_unlock_target_businesses, bulk_unlock_cost,
)
from core.scraper_engine import ScraperWorker
from core.scraper_settings import load_scraper_config
from core.api_client import (
    spend_credits, unlock_lead, start_search, post_search_results, reward_referral,
    list_searches, get_search_leads, delete_search, create_export, ApiError,
    list_search_activity, log_search_activity, log_export_activity,
    get_pricing_settings, get_my_progress, record_my_progress,
)
from ui.components.widgets import (
    checkbox_cell, rating_cell, website_cell, email_cell, locked_contact_cell,
    status_pill, SearchQueryCard, LeadCheckBox, SearchLineEdit, Toast,
)
from ui.dialogs.dialogs import (
    FilterDialog, ActivityLogDialog, SearchProgressDialog, ConfirmDialog, InfoDialog,
)


def _contact_field_label(field: str) -> str:
    """Human-readable noun phrase for a `field`/`mode` value ("phone",
    "email", or "all") -- used in unlock confirm dialogs, toasts, and
    activity-log lines so a Phone-only or Email-only unlock reads as
    what it actually unlocked instead of the generic "contact info".
    "all" (the Both button / a full-row unlock) keeps "contact info"
    since that's the accurate umbrella term for phone + email together."""
    return {"phone": "phone number", "email": "email address"}.get(field, "contact info")


def _plural_business(count: int) -> tuple[str, str]:
    """Returns (plural suffix, possessive suffix) for `count` businesses,
    e.g. (1, "", "'s") -> "business's" and (5, "es", "'") -> "businesses'".
    A plain `+ "'s"` on "businesses" gives the ungrammatical "businesses's"
    seen in earlier activity-log entries -- this fixes that."""
    plural = "es" if count != 1 else ""
    possessive = "'" if plural else "'s"
    return plural, possessive


class _FilterWorkerSignals(QObject):
    """QRunnable can't emit signals itself (it isn't a QObject), so the
    worker below owns one of these and emits through it instead. Carries
    a generation number so a slow-to-finish worker from an old keystroke
    can't overwrite a newer result that already landed.

    IMPORTANT: the second argument is typed `object`, not `list`. Typing
    it `list` used to make PySide6 marshal the payload through Qt's
    QVariant machinery for this cross-thread (queued) connection, which
    reconstructs brand-new dict objects on arrival instead of handing back
    the *same* business dicts that were sent in. That silently broke
    identity: mutating a business dict pulled from self.current_data (e.g.
    on_unlock_row setting biz["unlocked_phone"] = True) no longer mutated the
    matching dict in self.all_data, so the unlock -- and row selection,
    which is tracked by id(biz) -- would quietly revert the next time any
    filter/search/sort ran. `object` tells PySide6 to pass the Python list
    through opaquely instead, which preserves the original dict objects
    (and therefore their identity) across the thread hop."""
    finished = Signal(int, object)


class _FilterWorker(QRunnable):
    """Runs the substring filter + threshold filters + sort on a
    QThreadPool worker thread instead of the GUI thread. Takes a plain
    snapshot of everything it needs (data reference, search text, active
    filters, sort key/direction) at dispatch time rather than reaching
    back into SearchLeadsPage -- self.all_data / self.active_filters /
    etc. could all be reassigned to new objects by the GUI thread while
    this is running, and reading a stale-but-intact list here is fine,
    whereas reaching into a live mutable self would not be.

    Only reads business dicts (never mutates), so this is safe to run
    concurrently with the GUI thread's on_unlock_row(), which only ever
    flips an 'unlocked_phone'/'unlocked_email' flag that this worker
    doesn't look at."""

    def __init__(self, generation: int, all_data: list, search_text: str,
                 active_filters: dict, sort_key, sort_ascending: bool):
        super().__init__()
        self.generation = generation
        self.all_data = all_data
        self.search_text = search_text
        self.active_filters = active_filters
        self.sort_key = sort_key
        self.sort_ascending = sort_ascending
        self.signals = _FilterWorkerSignals()

    def run(self):
        if self.search_text:
            filtered = [
                biz for biz in self.all_data
                if self.search_text in biz["_search_blob"]
            ]
        else:
            filtered = list(self.all_data)

        f = self.active_filters
        if f["min_rating"] > 0:
            filtered = [b for b in filtered if b.get("rating", 0) >= f["min_rating"]]
        if f["min_reviews"] > 0:
            filtered = [b for b in filtered if b.get("reviews", 0) >= f["min_reviews"]]
        if f["status"] != "Any":
            filtered = [b for b in filtered if b.get("status") == f["status"]]
        if f["category"] != "Any":
            filtered = [b for b in filtered if b.get("category") == f["category"]]
        if f.get("has_phone"):
            filtered = [b for b in filtered if business_has_phone(b)]
        if f.get("has_email"):
            filtered = [b for b in filtered if business_has_email(b)]

        if self.sort_key is not None:
            filtered = sorted(
                filtered,
                key=lambda biz: biz.get(self.sort_key, ""),
                reverse=not self.sort_ascending,
            )

        self.signals.finished.emit(self.generation, filtered)


class SearchLeadsPage(QSplitter):
    """Search Queries panel + results table + detail panel, laid out in
    their own internal splitter so Dashboard's outer splitter only ever
    needs to know about [sidebar, stack]."""

    # Emitted whenever credits are spent, so Dashboard can update the
    # sidebar's credits box without SearchLeadsPage reaching into it.
    credits_changed = Signal(int, int)  # (remaining, total)

    # Emitted whenever the active search changes (load_search/remove_search),
    # so Dashboard can keep the window title in sync.
    active_search_changed = Signal(str)  # header text

    def __init__(self, parent=None, user_role: str = "user", user_id: int = None,
                 credits_total: int = None, credits_remaining: int = None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.user_role = user_role
        self.user_id = user_id
        self.setHandleWidth(3)
        self.setChildrenCollapsible(False)

        self.searches = self._load_searches_from_server()
        # May be empty (brand-new account, offline, or an admin account --
        # see _load_searches_from_server's docstring) -- boot into the
        # same "no searches yet" empty state that remove_search() falls
        # back to when the last saved search is deleted.
        self.active_search_id = self.searches[0]["id"] if self.searches else None

        self.all_data = self._prep_data(self.searches[0]["data"]) if self.searches else []
        self.current_data = list(self.all_data)
        # Which rows are checked, tracked by python id(biz) rather than by
        # page/row index -- ids stay valid across pagination, sorting, and
        # filtering because _prep_data()/apply_filter() only ever filter or
        # sort the *same* dict objects, they never copy them. This is what
        # lets "N selected" / bulk unlock / export cover rows on pages you
        # haven't even visited yet, instead of just whatever's currently
        # rendered in the table. Reset (to "everything selected") whenever
        # a new dataset loads -- see _prep_data() callers.
        self.selected_ids = {id(biz) for biz in self.all_data}
        # Bumped on every apply_filter() dispatch; lets _on_filter_result()
        # discard results from a background worker that finishes after a
        # newer search/filter/sort has already superseded it.
        self._filter_generation = 0
        self.sort_column = None
        self.sort_ascending = True
        self.search_text = ""
        self.active_filters = {
            "min_rating_label": "Any", "min_rating": 0.0,
            "min_reviews": 0, "status": "Any", "category": "Any",
            "has_phone": False, "has_email": False,
        }

        # Credits balance -- seeded from the caller's DB-loaded values
        # (Dashboard passes the user's real `credits` row) so this page
        # doesn't drift from the sidebar. Falls back to INITIAL_CREDITS
        # only when no balance was supplied (e.g. page built standalone).
        self.credits_total = credits_total if credits_total is not None else INITIAL_CREDITS
        self.credits_remaining = credits_remaining if credits_remaining is not None else INITIAL_CREDITS

        # Live per-field unlock prices (admin-configurable -- see
        # pricing_tab.py / PricingSettings), fetched once on open so the
        # locked-contact-cell badges, confirm dialogs, and insufficient-
        # credits checks below show the real price instead of assuming
        # 1 credit each. Falls back to 1/1 on a failed fetch (offline,
        # server down) -- purely a display fallback, since unlock_lead()
        # server-side is always the actual authority on what gets charged.
        try:
            _pricing = get_pricing_settings()
            self.email_unlock_cost = _pricing["lead_unlock_email_cost"]
            self.phone_unlock_cost = _pricing["lead_unlock_phone_cost"]
        except ApiError:
            self.email_unlock_cost = 1
            self.phone_unlock_cost = 1

        # Pagination state -- page_size is adjustable via the "Rows per
        # page" dropdown in the footer; current_page is clamped back into
        # range automatically whenever populate_table() runs (e.g. after
        # switching to a smaller dataset).
        self.page_size = 50
        self.current_page = 1
        self._current_page_data = []

        queries_panel = self.build_search_queries_panel()
        main = self.build_main()

        self.addWidget(queries_panel)
        self.addWidget(main)

        self.setStretchFactor(0, 0)
        self.setStretchFactor(1, 1)
        self.setSizes([230, 1180])

    @property
    def window_title_suffix(self) -> str:
        """Header text Dashboard can use in the window title for whichever
        search is currently active."""
        search = next((s for s in self.searches if s["id"] == self.active_search_id), None)
        return search["header"] if search else "Search Leads"

    def build_search_queries_panel(self) -> QWidget:
        """
        The "Search Queries" panel.

        Admin accounts: this panel IS the query input -- a multiline box
        where each line is one query, a "New Query" button that just opens
        a fresh line to type into (it doesn't search anything by itself),
        and a "Search" button that actually runs every non-empty line as
        a parallel scrape (see _on_search_clicked / _start_real_search).
        Saved-search history cards sit below it so you can still jump back
        to a previous search's results.

        Everyone else: same as admins now -- the composer above runs the
        real parallel scrape for every role (see PROGRESS.md's "same
        search for everyone" change). The old "+ New Search" button that
        opened a placeholder-data dialog is gone; NewSearchDialog and
        open_new_search_dialog() were retired along with it.
        """
        panel = QFrame()
        panel.setObjectName("Sidebar")  # reuse the same dark bg + right border
        panel.setMinimumWidth(230)
        panel.setMaximumWidth(360)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 16, 14, 14)
        layout.setSpacing(10)
        # Without this, PySide6 can distribute any extra vertical space
        # (e.g. when the window is maximized/fullscreened) across the
        # existing rows instead of pushing it below the addStretch() at
        # the bottom -- that's what caused the composer to drift away
        # from the top with a big gap under the hint text.
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_lbl = QLabel("Search Queries")
        header_lbl.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 13px; font-weight: 600; background: transparent;")
        header_row.addWidget(header_lbl)
        header_row.addStretch()
        chevron_lbl = QLabel()
        chevron_lbl.setStyleSheet("background: transparent;")
        chevron_lbl.setPixmap(qta.icon('fa5s.chevron-down', color=PALETTE['text_muted']).pixmap(10, 10))
        header_row.addWidget(chevron_lbl)
        layout.addLayout(header_row)

        # Query composer (real parallel scrape) is now shown for every
        # role, not just admin -- see PROGRESS.md for the "same search
        # for everyone" change. Kept the `is_admin` name below (rather
        # than deleting it) since other admin-only behavior elsewhere in
        # this class (e.g. free contact unlocks) still legitimately
        # depends on role and isn't part of this change.
        is_admin = self.user_role == "admin"

        layout.addWidget(self._build_query_composer())

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet(f"background-color: {PALETTE['divider']}; max-height: 1px; min-height: 1px;")
        layout.addWidget(divider)

        history_lbl = QLabel("Recent Searches")
        history_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 10px; font-weight: 600; "
                                   "text-transform: uppercase; letter-spacing: 0.5px; background: transparent;")
        layout.addWidget(history_lbl)

        # One clickable card per saved search; kept around in a dict so
        # load_search() can flip which card is highlighted as "active".
        self.query_cards = {}
        self.queries_layout = layout
        for search in self.searches:
            card = SearchQueryCard(search)
            card.clicked.connect(self.load_search)
            card.remove_clicked.connect(self.remove_search)
            card.set_active(search["id"] == self.active_search_id)
            self.query_cards[search["id"]] = card
            layout.addWidget(card)

        layout.addStretch()

        # New cards get inserted just above whatever trailing widget(s)
        # follow the card list -- just the stretch now, for every role,
        # since the composer (not a dialog) is how every role starts a
        # search these days.
        self._card_insert_offset = 1

        return panel

    def _build_query_composer(self) -> QWidget:
        """Admin-only query composer: multiline box (one query per line) +
        "New Query" (adds a blank line, doesn't search) + "Search" (runs
        every non-empty line as one parallel, compiled scrape)."""
        wrap = QWidget()
        wrap.setStyleSheet("background: transparent;")
        wrap.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        col = QVBoxLayout(wrap)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(8)
        col.setAlignment(Qt.AlignmentFlag.AlignTop)

        hint = QLabel("One query per line \u2014 all lines search in parallel and compile into one result set.")
        hint.setWordWrap(True)
        hint.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        hint.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 10px; background: transparent;")
        col.addWidget(hint)

        self.query_composer = QPlainTextEdit()
        self.query_composer.setPlaceholderText("Gyms near me\nGyms near Cavite")
        self.query_composer.setFixedHeight(140)
        self.query_composer.setStyleSheet(
            f"QPlainTextEdit {{ background-color: {PALETTE['bg_app']}; border: 1px solid {PALETTE['border']}; "
            f"border-radius: 6px; color: {PALETTE['text_secondary']}; padding: 6px; font-size: 11px; }}"
        )
        col.addWidget(self.query_composer)

        # Plain QCheckBox's native indicator doesn't reliably paint on this
        # dark Fusion theme (same issue LeadCheckBox was already built to
        # solve elsewhere -- see checkbox_cell() in widgets.py), which is
        # why it was invisible next to "Also find emails". Use the same
        # hand-painted checkbox here instead of the native one.
        enrich_row = QHBoxLayout()
        enrich_row.setSpacing(8)
        enrich_row.setContentsMargins(0, 0, 0, 0)
        self.enrich_check = LeadCheckBox(False)
        enrich_row.addWidget(self.enrich_check)
        enrich_label = QLabel("Also find emails")
        enrich_label.setStyleSheet(f"color: {PALETTE['text_muted']}; font-size: 11px; background: transparent;")
        enrich_label.setCursor(Qt.CursorShape.PointingHandCursor)
        enrich_row.addWidget(enrich_label)
        enrich_row.addStretch()
        col.addLayout(enrich_row)

        # Clicking the label toggles the checkbox too, not just the 16x16 box.
        def _toggle_enrich(event):
            self.enrich_check.toggle()
        enrich_label.mousePressEvent = _toggle_enrich

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        # "New Query" button removed -- typing a new line in the composer
        # already counts as a new query, so a separate button was redundant.
        # "Search" is now the only action here.
        #
        # NOTE: this button lives inside `wrap`, which has its own local
        # setStyleSheet("background: transparent;") a few lines up. That
        # local stylesheet on an ancestor silently breaks the normal
        # objectName-based #RedBtn cascade from the app-wide stylesheet
        # (this is why Export, which isn't nested inside a locally-styled
        # container, rendered red fine but this one didn't). Rather than
        # depend on that cascade, style it directly off the theme palette
        # so it's guaranteed to render red regardless of ancestor styling.
        search_btn = QPushButton(qta.icon('fa5s.search', color="#ffffff"), " Search")
        search_btn.setObjectName("RedBtn")
        search_btn.setStyleSheet(
            f"QPushButton#RedBtn {{ background-color: {PALETTE['accent']}; border: none; "
            f"border-radius: 5px; padding: 6px 14px; color: white; font-weight: 500; }}"
            f"QPushButton#RedBtn:hover {{ background-color: {PALETTE['accent_hover']}; }}"
            f"QPushButton#RedBtn:disabled {{ background-color: {PALETTE['accent_hover']}; color: rgba(255,255,255,0.75); }}"
        )
        search_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        search_btn.clicked.connect(self._on_search_clicked)
        btn_row.addWidget(search_btn)
        self.search_btn = search_btn

        col.addLayout(btn_row)
        return wrap

    def _maybe_reward_referral(self):
        """Call this once a search actually completes (either flow below).
        A completed search is this app's qualifying action for referral
        payout -- cheapest signal that the referred account isn't a
        throwaway, per db.try_reward_referral's docstring server-side.
        Safe to call on every search: it's a no-op once the referral's
        already REWARDED, since try_reward_referral only matches PENDING
        rows. Phase 5: goes through POST /referrals/reward (scoped to the
        logged-in user via the bearer token, so no user_id needs to be
        passed) instead of a direct data.db call."""
        if self.user_id is not None:
            try:
                reward_referral("REFERRAL")
            except ApiError:
                # Offline/server down -- not worth surfacing to the user
                # over a background referral payout; it'll be retried on
                # the next completed search.
                pass

    def _on_search_clicked(self):
        """"Search" is the only button that actually runs anything --
        reads every non-empty line out of the composer and kicks off one
        parallel, compiled scrape across all of them."""
        queries = [line.strip() for line in self.query_composer.toPlainText().splitlines() if line.strip()]
        if not queries:
            InfoDialog.show(
                self, "No queries", "Type at least one search query first (one per line).",
                icon_name='fa5s.exclamation-triangle', success=False,
            )
            return
        self._start_real_search(queries, self.enrich_check.isChecked())

    # -- Real (admin) parallel scrape ---------------------------------------

    def _start_real_search(self, queries: list, enable_email_enrichment: bool):
        """Kicks off ScraperWorker on a background QThread for every query
        at once (bounded by max_concurrent_tabs from Settings > Web
        Scraper), with a cancelable progress dialog so the GUI stays
        responsive while Chrome does its thing.

        Refuses to start a second scrape while one is still alive. The
        Search button already disables itself while a scrape runs, but
        that alone isn't enough: finished_ok/failed fire *before* the
        worker's `finally` block finishes killing Chrome (see the note
        on worker.finished below), so there used to be a window where
        clicking Search again -- e.g. right after unchecking/checking
        'Also find emails' and re-submitting -- would create a brand new
        ScraperWorker and overwrite self._scrape_worker while the OLD
        one's OS thread was still alive tearing down Chrome. Losing the
        only Python reference to a still-running QThread like that is
        undefined behavior in Qt and is what was crashing the whole
        process. This guard closes that window entirely."""
        if getattr(self, "_scrape_worker", None) is not None:
            InfoDialog.show(
                self, "Search already running",
                "A search is still finishing up in the background (closing "
                "Chrome). Please wait a moment and try again.",
                icon_name='fa5s.hourglass-half', success=False,
            )
            return

        # Server is authoritative for whether this search is allowed to
        # run at all -- checks the account can afford SEARCH_COST and
        # creates the Search row (see app/db.py's start_search), but no
        # longer deducts credits here. The actual charge happens once
        # results are posted back (post_search_results below), and only
        # if the scrape actually found something -- a search that comes
        # back empty is free. Admin/superadmin accounts still get free,
        # ungated runs (server-side cost=0 in routers/searches.py), but
        # they now go through this same call so they get a real Search
        # row -- and therefore search history -- same as every other role.
        header = self._build_header(queries)
        title = ", ".join(queries)
        try:
            result = start_search(title, query_text="\n".join(queries), header=header)
        except ApiError as exc:
            InfoDialog.show(self, "Couldn't start search", str(exc), success=False)
            return

        search_id = result["search_id"]
        self.credits_remaining = result["credits"]
        self.credits_changed.emit(self.credits_remaining, self.credits_total)

        cfg = load_scraper_config()
        cfg["queries"] = queries
        cfg["enable_email_enrichment"] = enable_email_enrichment
        # Admin runs always get to see the browser and keep it tidy after,
        # regardless of what a stale saved config says.
        cfg.setdefault("close_after_run", True)

        self._pending_queries = queries
        self._pending_enrich_enabled = enable_email_enrichment
        # Stashed for the next Phase 3 step -- POSTing results back into
        # the Lead table on finished_ok needs to know which Search row
        # they belong to. None for admin runs (no server-side Search row
        # is created for them yet -- see the bypass above).
        self._pending_search_id = search_id

        if hasattr(self, "search_btn"):
            self.search_btn.setEnabled(False)
            self.search_btn.setText(" Searching…")
            self._repolish_search_btn()

        # Step-tracker progress modal (Preparing -> Searching Google Maps ->
        # Collecting Data -> Enriching Leads -> Completed) with live
        # businesses-found/processed/elapsed-time counters, a Stop Search
        # button, and a percent-complete bar -- replaces the old bare
        # QProgressDialog.
        progress = SearchProgressDialog(enrichment_enabled=enable_email_enrichment, parent=self)
        progress.setModal(True)
        self._scrape_progress = progress

        worker = ScraperWorker(cfg)
        self._scrape_worker = worker
        worker.log.connect(self._on_scrape_log)
        worker.card_found.connect(self._on_scrape_card_found)
        worker.query_progress.connect(self._on_scrape_query_progress)
        worker.enrich_progress.connect(self._on_scrape_enrich_progress)
        worker.finished_ok.connect(self._on_scrape_finished)
        worker.failed.connect(self._on_scrape_failed)
        progress.stop_requested.connect(worker.request_stop)
        # QThread's own `finished` signal only fires once run() has truly
        # returned -- unlike finished_ok/failed above, which are emitted
        # from inside run()'s try block, BEFORE its `finally` clause has
        # finished killing every Chrome tab process. self._scrape_worker
        # is only cleared here (not in _on_scrape_finished/_on_scrape_failed)
        # and the Search button only re-enables here too (see
        # _on_worker_thread_finished) -- both stay "busy" for that entire
        # window so a new search can never be started, and self._scrape_worker
        # can never be overwritten, while the old thread is still alive.
        worker.finished.connect(self._on_worker_thread_finished)

        worker.start()
        progress.show()

    def _on_worker_thread_finished(self):
        self._scrape_worker = None
        self._reset_search_button()

    def _on_scrape_log(self, text: str):
        """While still in the 'Preparing' phase (before the first query
        finishes), surface the raw log line as the status subtitle so the
        dialog isn't sitting static during Chrome launch. Once scraping is
        underway, update_query_progress()/update_enrich_progress() own the
        status text instead."""
        progress = getattr(self, "_scrape_progress", None)
        if progress is None or progress._phase != "preparing":
            return
        line = text.strip().splitlines()[-1] if text.strip() else text
        if line:
            progress.set_status("Preparing…", line)

    def _on_scrape_card_found(self, card: dict):
        if getattr(self, "_scrape_progress", None) is not None:
            self._scrape_progress.add_found()

    def _on_scrape_query_progress(self, completed: int, total: int, query: str):
        if getattr(self, "_scrape_progress", None) is None:
            return
        self._scrape_progress.update_query_progress(completed, total)

    def _on_scrape_enrich_progress(self, completed: int, total: int, name: str):
        if getattr(self, "_scrape_progress", None) is None:
            return
        self._scrape_progress.update_enrich_progress(completed, total)

    def _repolish_search_btn(self):
        """PySide6/Qt caches a widget's QSS-derived look per pseudo-state;
        toggling setEnabled() alone doesn't always trigger a repaint with
        the right #RedBtn colors, so the button can visually get stuck
        looking flat/grey instead of red even once it's enabled again.
        Force a style refresh so it reliably shows the theme's accent red."""
        if hasattr(self, "search_btn"):
            self.search_btn.style().unpolish(self.search_btn)
            self.search_btn.style().polish(self.search_btn)
            self.search_btn.update()

    def _reset_search_button(self):
        if hasattr(self, "search_btn"):
            self.search_btn.setEnabled(True)
            self.search_btn.setText(" Search")
            self._repolish_search_btn()

    def _build_header(self, queries: list, max_len: int = 60) -> str:
        """Joins queries with " + " for the search title. Long combined
        titles (many queries, or long query text) get truncated with an
        ellipsis so they don't blow up the header layout."""
        full = " + ".join(q.title() for q in queries) if len(queries) > 1 else queries[0].title()
        if len(full) <= max_len:
            return full
        return full[:max_len].rstrip() + "..."

    def _on_scrape_finished(self, records: list):
        if getattr(self, "_scrape_progress", None) is not None:
            # Flip every step to "done" and sit on 100% for a beat so the
            # user actually sees the tracker land on "Completed" instead of
            # the dialog just vanishing mid-animation.
            self._scrape_progress.mark_completed()
            QTimer.singleShot(500, self._close_scrape_progress)
        # NOTE: search_btn stays disabled here on purpose -- this fires
        # from inside worker.run()'s try block, BEFORE Chrome has actually
        # been torn down. It only re-enables in _on_worker_thread_finished,
        # once the background thread has truly exited. See the long
        # comment on worker.finished in _start_real_search.
        if hasattr(self, "query_composer"):
            self.query_composer.clear()

        queries = getattr(self, "_pending_queries", []) or ["search"]
        businesses = [self._scraped_card_to_business(r) for r in records]

        # Non-admin runs have a server-side Search row waiting from
        # _start_real_search's start_search() call -- write the scraped
        # rows into the Lead table now that we actually have them. Admin
        # runs never got a search_id (server bypass), so there's nothing
        # to POST for them; local-only display is still correct there.
        pending_search_id = getattr(self, "_pending_search_id", None)
        charged_cost = 0
        if pending_search_id is not None:
            try:
                result = post_search_results(pending_search_id, businesses)
                # Swap in the server's own Lead rows (real ids,
                # unlocked_phone/unlocked_email explicitly False) instead
                # of the client-built dicts -- keeps this session's view
                # byte-for-byte what _load_searches_from_server() would
                # rebuild from GET /searches/{id}/leads on the next launch.
                businesses = [self._lead_to_business(lead) for lead in result["leads"]]
                # The actual credit charge happens server-side here, not
                # at start_search -- see db.add_search_results's
                # docstring. An empty result set costs nothing, so only
                # refresh/emit if credits actually moved.
                if result.get("cost", 0) > 0:
                    charged_cost = result["cost"]
                    self.credits_remaining = result["credits"]
                    self.credits_changed.emit(self.credits_remaining, self.credits_total)
                try:
                    if get_my_progress("SEARCH") == 0:
                        record_my_progress("SEARCH")
                except ApiError:
                    pass
            except ApiError as exc:
                # Leads were already found -- don't throw the results
                # away over a failed POST, just surface that they didn't
                # make it to the server (and so weren't charged for
                # either) so the user isn't surprised when this search is
                # missing from another device later.
                InfoDialog.show(
                    self, "Results not saved to server",
                    f"The search finished and results are shown below, but "
                    f"saving them to your account failed:\n\n{exc}\n\n"
                    f"They're only available locally for now.",
                    success=False,
                )

        header = self._build_header(queries)
        title = ", ".join(queries)
        with_email = sum(1 for b in businesses if b.get("email_addr"))
        with_phone = sum(1 for b in businesses if b.get("phone_num"))

        activity_log = [
            {"text": "Search completed successfully",
             "meta": f"{len(businesses)} businesses found across {len(queries)} "
                     f"quer{'y' if len(queries) == 1 else 'ies'} (ran in parallel)",
             "time": "Just now"},
            {"text": "Website scan completed", "meta": f"{with_phone} phone numbers found", "time": "Just now"},
        ]
        if getattr(self, "_pending_enrich_enabled", False):
            activity_log.append(
                {"text": "Email enrichment completed", "meta": f"{with_email} emails found", "time": "Just now"}
            )
        if charged_cost > 0:
            activity_log.append(
                {"text": "Credits deducted", "meta": f"{charged_cost} credit(s) for this search", "time": "Just now"}
            )

        # Non-admin runs have a real server-side Search row now (the
        # start_search() call in _start_real_search) -- use its id
        # (stringified, so it matches the str ids _load_searches_from_server()
        # builds from the same GET /searches/{id} on a future launch)
        # instead of a throwaway local one. Admin runs still get a
        # random local id since there's no server row to point at.
        new_id = (
            str(pending_search_id) if pending_search_id is not None
            else f"scrape_{len(self.searches) + 1}_{random.randint(1000, 9999)}"
        )
        search = {
            "id": new_id,
            "title": title,
            "header": header,
            "timestamp": "Just now",
            "data": businesses,
            "activity_log": activity_log,
        }
        self.searches.append(search)

        # Persist this search's opening activity_log lines server-side too
        # (see db.log_activity) so a relaunch's _load_searches_from_server()
        # gets the real recorded log instead of only the shorter
        # aggregate-derived approximation it falls back to for older
        # searches. Admin runs have no server-side Search row (no
        # pending_search_id) -- nothing to log against, same as every
        # other server write this method already skips for them.
        if pending_search_id is not None:
            for entry in activity_log:
                try:
                    log_search_activity(pending_search_id, entry["text"], entry.get("meta") or None)
                except ApiError:
                    pass
        self.add_search_card(search)
        self.load_search(new_id)
        self._maybe_reward_referral()

    def _on_scrape_failed(self, message: str):
        if getattr(self, "_scrape_progress", None) is not None:
            self._scrape_progress.mark_failed(message)
            self._close_scrape_progress()
        # search_btn re-enables in _on_worker_thread_finished, not here --
        # same reasoning as _on_scrape_finished above.
        InfoDialog.show(
            self, "Search failed",
            f"The scrape couldn't finish:\n\n{message}\n\n"
            f"Check the Chrome path/profile in Settings \u2192 Web Scraper and try again.",
            icon_name='fa5s.times-circle', success=False,
        )

    def _close_scrape_progress(self):
        if getattr(self, "_scrape_progress", None) is not None:
            self._scrape_progress.close()
            self._scrape_progress = None

    def _load_searches_from_server(self) -> list:
        """Replaces the old `self.searches = SEARCHES` (data/leads.py's
        always-empty mock list) with the logged-in user's real search
        history -- GET /searches for the list, then GET
        /searches/{id}/leads for each one's actual results, so every
        history card has its lead data ready up front the same way the
        old in-memory list always did. (SearchQueryCard's count badge,
        window_title_suffix, and load_search() all assume `search["data"]`
        is already there, not lazily fetched on click -- fetching per
        search here keeps that contract instead of reworking every one
        of those call sites into an async/lazy-load flow.)

        Admin runs never get a server-side Search row (see
        _start_real_search's admin bypass below), so an admin account's
        list here is always empty -- the same "no history yet" experience
        admin accounts already had on every restart before this change,
        just now for a documented reason instead of "nothing was ever
        saved anywhere."

        Failure (logged out, offline, server down) degrades to an empty
        list rather than blocking the page from opening -- same
        "don't lose an already-working screen over one failed call"
        spirit as _on_scrape_finished's results-POST failure handling."""
        if self.user_id is None:
            return []
        try:
            shells = list_searches()
        except ApiError:
            return []

        searches = []
        for shell in shells:
            search_id = str(shell["id"])
            try:
                detail = get_search_leads(shell["id"])
                businesses = [self._lead_to_business(lead) for lead in detail["leads"]]
            except ApiError:
                # This one search's leads didn't load -- still show it as
                # a (temporarily empty) history card rather than dropping
                # it from the list entirely.
                businesses = []

            # Real, persisted log first (see db.log_activity /
            # GET /searches/{id}/activity) -- entries recorded as they
            # actually happened, in order, surviving this relaunch
            # instead of being re-derived from scratch. Falls back to
            # the old aggregate-derived reconstruction only when that
            # comes back empty, which is either a search created before
            # this feature existed (nothing was ever logged for it) or
            # the request failing outright (offline, etc.) -- either
            # way, showing the old approximate summary beats showing
            # "No activity yet" for a search that clearly did complete.
            try:
                activity_log = [
                    {"text": entry["text"], "meta": entry.get("meta") or "", "time": entry["created_at"]}
                    for entry in list_search_activity(shell["id"])
                ]
            except ApiError:
                activity_log = []

            if not activity_log:
                with_phone = sum(1 for b in businesses if b.get("phone_num"))
                with_email = sum(1 for b in businesses if b.get("email_addr"))
                activity_log = [
                    {"text": "Search completed successfully",
                     "meta": f"{len(businesses)} businesses found", "time": shell["created_at"]},
                    {"text": "Website scan completed",
                     "meta": f"{with_phone} phone numbers found", "time": shell["created_at"]},
                ]
                if with_email:
                    activity_log.append(
                        {"text": "Email enrichment completed",
                         "meta": f"{with_email} emails found", "time": shell["created_at"]}
                    )

            searches.append({
                "id": search_id,
                "title": shell["title"],
                "header": shell.get("header") or shell["title"],
                "timestamp": shell["created_at"],
                "data": businesses,
                "activity_log": activity_log,
            })
        return searches

    @staticmethod
    def _lead_to_business(lead: dict) -> dict:
        """Maps one LeadOut dict (from GET /searches/{id}/leads or a
        POST /searches/{id}/results response) onto this app's business
        dict schema -- same target shape _scraped_card_to_business()
        produces below, plus the real unlocked_phone/unlocked_email
        flags already carried on the server row. Passing those through
        (instead of leaving them absent for ensure_unlock_state() to
        default to False) is what makes an already-paid-for unlock stay
        unlocked after reloading a search instead of looking locked
        again.

        Also keeps the server-side `id` (as "lead_id") -- on_unlock_row
        needs it to call the real POST /leads/{lead_id}/unlock endpoint
        instead of just flipping a local-only flag."""
        return {
            "lead_id": lead.get("id"),
            "name": lead.get("name") or "Untitled listing",
            "rating": lead.get("rating") or 0.0,
            "reviews": lead.get("reviews") or 0,
            "category": lead.get("category") or "Uncategorized",
            "status": lead.get("status") or "Open",
            "desc": lead.get("desc") or "",
            "address": lead.get("address") or "",
            "hours": lead.get("hours") or "",
            "phone_num": lead.get("phone_num") or "",
            "site": lead.get("site") or "",
            "email_addr": lead.get("email_addr") or "",
            "maps_url": lead.get("maps_url") or "",
            "unlocked_phone": lead.get("unlocked_phone", False),
            "unlocked_email": lead.get("unlocked_email", False),
        }

    @staticmethod
    def _scraped_card_to_business(card: dict) -> dict:
        """Maps ScraperWorker's raw card schema (name/rating/reviews/
        category/address/phone/status/hours/website/email/maps_url) onto
        this app's business dict schema (phone_num/site/email_addr/etc.)."""
        rating_raw = str(card.get("rating", "")).strip()
        try:
            rating = float(rating_raw) if rating_raw else 0.0
        except ValueError:
            rating = 0.0

        reviews_digits = re.sub(r"[^\d]", "", str(card.get("reviews", "")))
        reviews = int(reviews_digits) if reviews_digits else 0

        status_raw = str(card.get("status", "")).strip()
        status = "Closed" if status_raw.lower().startswith("closed") else "Open"

        source_query = card.get("source_query", "")
        desc = (
            f"Found via Google Maps search for \"{source_query}\"."
            if source_query else "Found via Google Maps search."
        )

        return {
            "name": card.get("name", "").strip() or "Untitled listing",
            "rating": rating,
            "reviews": reviews,
            "category": card.get("category", "").strip() or "Uncategorized",
            "status": status,
            "desc": desc,
            "address": card.get("address", ""),
            "hours": card.get("hours", ""),
            "phone_num": card.get("phone", ""),
            "site": card.get("website", ""),
            "email_addr": card.get("email", ""),
            "maps_url": card.get("maps_url", ""),
        }

    def export_current_search(self):
        """Saves every *checked* row of the currently filtered/searched
        result set to a file -- across all pages, since selection is
        tracked globally by id() in self.selected_ids rather than by
        page/row index (see the note on self.selected_ids in __init__).

        Format (CSV / Excel / JSON / HTML) is whichever filter the user
        picks in the save dialog; the actual writing is delegated to
        data/lead_exporter.py so this method stays UI-only.
        """
        if not self.current_data:
            InfoDialog.show(
                self, "Nothing to export", "This search has no results yet.",
                icon_name='fa5s.info-circle', success=False,
            )
            return

        to_export = self._checked_rows()

        if not to_export:
            InfoDialog.show(
                self, "Nothing to export",
                "No leads are checked. Tick the rows you want, or use the header checkbox to select all.",
                icon_name='fa5s.info-circle', success=False,
            )
            return

        search = next((s for s in self.searches if s["id"] == self.active_search_id), None)
        default_name = (search["title"] if search else "leads").strip()
        default_name = re.sub(r"[^\w\-]+", "_", default_name).strip("_") or "leads"

        path, selected_filter = QFileDialog.getSaveFileName(
            self, "Export leads", default_name, QT_FILE_DIALOG_FILTER
        )
        if not path:
            return

        fmt = format_id_from_filter(selected_filter)

        # If the user typed/edited the filename such that its extension
        # doesn't match the chosen format's filter (e.g. left it as
        # "leads.csv" while "Excel Files (*.xlsx)" is selected), fix the
        # extension up so the file on disk matches what's actually inside it.
        expected_ext = ext_for_format(fmt)
        if not path.lower().endswith(expected_ext):
            path = re.sub(r"\.[^.\\/]*$", "", path) + expected_ext

        # Export mirrors whichever columns are currently checked in the
        # Columns menu (self.table.isColumnHidden reflects that toggle
        # state live) -- what's checked on-screen is what goes in the
        # file, nothing more.
        visible_cols = [
            c for col, c in enumerate(COLUMNS)
            if c["key"] and not self.table.isColumnHidden(col)
            and not (c.get("admin_only") and self.user_role != "admin")
        ]
        fieldnames = [c["key"] for c in visible_cols]
        headers = [c["label"] for c in visible_cols]

        # Same lock rule the table itself uses (populate_table's
        # COL_PHONE/COL_EMAIL branches, ~line 1692/1714): admins see
        # everything, everyone else only gets phone_num/email_addr on a
        # row once unlocked_phone/unlocked_email is actually True.
        # Without this, exporting checked-but-locked rows straight from
        # self.current_data would hand out contact info nobody paid
        # credits for -- CSV/Excel/JSON/HTML all went through
        # export_leads() with the raw dicts, unmasked.
        is_admin = self.user_role == "admin"

        def _masked(biz: dict) -> dict:
            row = dict(biz)
            if not is_admin and business_has_phone(biz) and not biz.get("unlocked_phone", False):
                row["phone_num"] = "Locked"
            if not is_admin and business_has_email(biz) and not biz.get("unlocked_email", False):
                row["email_addr"] = "Locked"
            return row

        to_export = [_masked(biz) for biz in to_export]

        FORMAT_LABELS = {"csv": "CSV", "xlsx": "Excel", "json": "JSON", "html": "HTML"}
        source_label = (search["title"] if search else "Baguio Leads (All)")

        try:
            export_leads(to_export, fieldnames, headers, path, fmt)
        except (OSError, RuntimeError, ValueError) as e:
            # Log the failed attempt to the Exports tab's history too --
            # nothing was written (leads_count=None, file_path=None,
            # same convention the old prototype list used for a Failed
            # row) -- best-effort: if the server call itself fails, the
            # user still gets the real error from the export attempt,
            # so that's swallowed rather than piled on top.
            try:
                failed_export = create_export(
                    file_name=os.path.basename(path), source=source_label,
                    format=FORMAT_LABELS.get(fmt, fmt.upper()),
                    leads_count=None, status="Failed", file_path=None,
                    search_id=self.active_search_id,
                )
                log_export_activity(failed_export["id"], "Export failed", str(e))
            except ApiError:
                pass
            # Also surface it in *this* search's own Activity Log (the
            # card under the table, not just the Exports tab's row-menu
            # dialog) -- exporting is something that happened to this
            # search, same reasoning as the "Credits deducted" line
            # on_unlock_row already adds there.
            self._log_activity(self.active_search_id, "Export failed", os.path.basename(path))
            InfoDialog.show(self, "Export failed", str(e), icon_name='fa5s.times-circle', success=False)
            return

        export_charged_cost = 0
        try:
            result = create_export(
                file_name=os.path.basename(path), source=source_label,
                format=FORMAT_LABELS.get(fmt, fmt.upper()),
                leads_count=len(to_export), status="Completed", file_path=path,
                search_id=self.active_search_id,
            )
            # File's already safely on disk at this point -- this only
            # charges credits (export_cost, admin-configurable) now that
            # the export is logged as Completed. See db.create_export's
            # docstring: a Failed status (the branch above) never charges.
            if result.get("cost", 0) > 0:
                export_charged_cost = result["cost"]
                self.credits_remaining = result["credits"]
                self.credits_changed.emit(self.credits_remaining, self.credits_total)
            # Feeds the "Export First CSV" reward (progress_key EXPORT,
            # target_value 1) -- same guarded-once pattern as SEARCH
            # above (_start_real_search's on_finished): only record the
            # first real export ever, so re-exporting doesn't keep
            # bumping a counter a target-1 reward has no use for.
            try:
                if get_my_progress("EXPORT") == 0:
                    record_my_progress("EXPORT")
            except ApiError:
                pass
            try:
                log_export_activity(
                    result["id"], "Export created",
                    f"{len(to_export)} lead(s) saved as {FORMAT_LABELS.get(fmt, fmt.upper())}",
                )
            except ApiError:
                pass
        except ApiError:
            # The file is already safely written to disk either way --
            # don't block a successful export just because the history
            # log couldn't reach the server. Nothing was charged either,
            # since the charge only happens inside that same call.
            pass

        # Same "surface it on this search too" reasoning as the failed
        # branch above -- runs regardless of whether the create_export()
        # call itself succeeded, since the export to disk already did.
        self._log_activity(
            self.active_search_id, "Export completed",
            f"{len(to_export)} lead(s) saved as {FORMAT_LABELS.get(fmt, fmt.upper())}",
        )
        if export_charged_cost > 0:
            self._log_activity(
                self.active_search_id, "Credits deducted",
                f"{export_charged_cost} credit(s) for exporting this search",
            )

        InfoDialog.show(
            self, "Export complete", f"Saved {len(to_export)} leads to:\n{path}",
            icon_name='fa5s.check-circle', success=True,
        )

    def add_search_card(self, search: dict):
        """Inserts one new SearchQueryCard above the stretch/'New Search'
        button so it appears alongside the existing saved-search cards."""
        card = SearchQueryCard(search)
        card.clicked.connect(self.load_search)
        card.remove_clicked.connect(self.remove_search)
        self.query_cards[search["id"]] = card
        insert_index = max(0, self.queries_layout.count() - self._card_insert_offset)
        self.queries_layout.insertWidget(insert_index, card)

    def build_main(self) -> QWidget:
        main = QWidget()
        outer_layout = QVBoxLayout(main)
        outer_layout.setContentsMargins(20, 16, 20, 16)
        outer_layout.setSpacing(0)

        # --- Vertical splitter: results (header/search/table/footer) on
        # top, Activity Log pinned to the bottom. The handle between them
        # is drawn as a thin red bar the user can drag to grow/shrink the
        # log panel's height.
        self.main_splitter = QSplitter(Qt.Orientation.Vertical)
        self.main_splitter.setHandleWidth(3)
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.setStyleSheet(
            "QSplitter::handle { background-color: transparent; }"
            "QSplitter::handle:hover { background-color: #ef4444; }"
            "QSplitter::handle:pressed { background-color: #dc2626; }"
        )
        outer_layout.addWidget(self.main_splitter)

        results_container = QWidget()
        layout = QVBoxLayout(results_container)
        layout.setContentsMargins(0, 0, 0, 10)
        layout.setSpacing(10)

        active_search = next((s for s in self.searches if s["id"] == self.active_search_id), None)

        header_row = QHBoxLayout()
        title_col = QVBoxLayout()
        self.title_lbl = QLabel(active_search["header"] if active_search else "No searches yet")
        self.title_lbl.setObjectName("HeaderTitle")
        self.title_lbl.setToolTip(active_search.get("title", "") if active_search else "")
        self.sub_lbl = QLabel(f"{len(active_search['data'])} businesses found" if active_search else "0 businesses found")
        self.sub_lbl.setObjectName("HeaderSub")
        title_col.addWidget(self.title_lbl)
        title_col.addWidget(self.sub_lbl)
        header_row.addLayout(title_col)
        header_row.addStretch()

        header_buttons = [
            ("fa5s.filter", "Filters", "OutlineBtn", PALETTE['text_muted']),
            ("fa5s.columns", "Columns", "OutlineBtn", PALETTE['text_muted']),
            ("fa5s.download", "Export", "RedBtn", "#ffffff"),
        ]
        for icon_name, label, obj_name, icon_color in header_buttons:
            btn = QPushButton(qta.icon(icon_name, color=icon_color), " " + label)
            btn.setObjectName(obj_name)
            if label == "Columns":
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.clicked.connect(self.open_columns_menu)
                self.columns_btn = btn
            elif label == "Filters":
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.clicked.connect(self.open_filters_dialog)
                self.filters_btn = btn
            elif label == "Export":
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.clicked.connect(self.export_current_search)
            header_row.addWidget(btn)
        layout.addLayout(header_row)

        self.search_edit = SearchLineEdit()
        self.search_edit.setPlaceholderText("Search in results... (press Enter)")
        # Search only runs on explicit action -- Enter, or clicking the
        # magnifying-glass icon -- not on every keystroke. Filtering the
        # full dataset is real work (scan + threshold filters + sort), so
        # tying it to textChanged meant every character typed re-ran it;
        # gating it behind returnPressed/click means it only runs once,
        # when you actually mean to search.
        self.search_edit.search_triggered.connect(self._trigger_search)
        self.search_edit.returnPressed.connect(self._trigger_search)
        layout.addWidget(self.search_edit)

        # Admins never see locked contact info in the first place (every
        # phone/email cell renders as plain text for them -- see the
        # `self.user_role == "admin"` check in populate_table's
        # phone/email cell logic), so there's nothing for a "spend
        # credits to unlock" bar to do. Only build it for non-admin roles.
        if self.user_role != "admin":
            layout.addWidget(self.build_bulk_unlock_bar())

        self.table = QTableWidget()
        self.table.setColumnCount(len(COLUMNS))
        self.table.setHorizontalHeaderLabels([c["label"] for c in COLUMNS])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setRowCount(len(self.current_data))

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_ROWNUM, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(COL_ROWNUM, 30)
        header.setSectionResizeMode(COL_CHECK, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(COL_CHECK, 30)
        for col, width in [
            (COL_NAME, 220),
            (COL_RATING, 70),
            (COL_REVIEWS, 90),
            (COL_CATEGORY, 140),
            (COL_PHONE, 150),
            (COL_WEBSITE, 190),
            (COL_EMAIL, 210),
            (COL_STATUS, 90),
            (COL_ADDRESS, 220),
            (COL_MAPS_LINK, 160),
        ]:
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(col, width)
        header.setStretchLastSection(True)

        # Address / Maps Link start hidden -- available from the Columns
        # menu (open_columns_menu) but not shown by default since most
        # searches don't need them taking up table width.
        for col in DEFAULT_HIDDEN_COLUMNS:
            self.table.setColumnHidden(col, True)

        # Maps Link is admin-only (see COLUMNS in config.py). Non-admins
        # can't reveal it via the Columns menu (open_columns_menu skips
        # admin_only entries for them), but force it hidden here too so
        # there's no window where it's visible before that menu is ever
        # opened.
        if self.user_role != "admin":
            self.table.setColumnHidden(COL_MAPS_LINK, True)

        # We handle sorting ourselves (see handle_sort) so that the custom
        # cell widgets (checkboxes, pills, icons) always follow their data
        # correctly -- QTableWidget's built-in sortItems() does not reliably
        # move cell widgets along with their rows.
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.sectionClicked.connect(self.handle_sort)

        # "Select all" checkbox painted into the header's checkbox column
        # (COL_CHECK, now the very first column -- see COLUMNS in config.py).
        # QTableWidget headers only render text via setHorizontalHeaderLabels,
        # so a real checkbox has to be a child widget floated on top of the
        # header and repositioned whenever that column's geometry changes.
        self.header_select_all = LeadCheckBox(True, parent=header)
        self.header_select_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header_select_all.toggled.connect(self._on_select_all_toggled)
        header.sectionResized.connect(lambda *_args: self._position_select_all_checkbox())
        header.installEventFilter(self)
        # Header geometry isn't final until after the first layout pass --
        # position once immediately and once more on the next event loop tick.
        self._position_select_all_checkbox()
        QTimer.singleShot(0, self._position_select_all_checkbox)

        layout.addWidget(self.table)

        # --- Footer: result count + pagination controls ---
        footer = QHBoxLayout()
        self.footer_label = QLabel("")
        self.footer_label.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 11px;")
        footer.addWidget(self.footer_label)
        footer.addStretch()

        page_size_lbl = QLabel("Rows per page:")
        page_size_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 11px;")
        footer.addWidget(page_size_lbl)

        self.page_size_combo = QComboBox()
        self.page_size_combo.addItems(["5", "10", "25", "50", "100"])
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

        self.main_splitter.addWidget(results_container)

        # --- Activity Log: its own pane in the splitter, pinned to the
        # bottom. Drag the red handle above it to grow/shrink its height.
        activity_log_widget = self.build_activity_log()
        self.main_splitter.addWidget(activity_log_widget)

        # Results pane gets first dibs on extra space; the log pane keeps
        # a modest default height but the user can drag it taller.
        self.main_splitter.setStretchFactor(0, 1)
        self.main_splitter.setStretchFactor(1, 0)
        activity_log_widget.setMinimumHeight(60)
        self.main_splitter.setSizes([600, 160])

        # Now that the table, footer/pagination widgets, and activity log
        # all exist, do the first render (populate_table touches all of them).
        self.populate_table()

        return main

    def open_filters_dialog(self):
        categories = sorted({biz.get("category", "") for biz in self.all_data if biz.get("category")})
        dlg = FilterDialog(categories, self.active_filters, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.active_filters = dlg.get_values()
            self.current_page = 1
            self.apply_filter()

    def open_columns_menu(self):
        """
        Prototype "Columns" button: a checkable dropdown that shows/hides
        table columns via QTableWidget.setColumnHidden(). Row #, checkbox,
        and Business Name stay locked visible since hiding them would
        leave the table without a usable identifying column.
        """
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background-color: {PALETTE['bg_surface']}; border: 1px solid {PALETTE['border']}; "
            f"border-radius: 8px; padding: 4px; color: {PALETTE['text_secondary']}; }}"
            "QMenu::item { padding: 6px 12px; border-radius: 4px; }"
            f"QMenu::item:selected {{ background-color: {PALETTE['bg_hover']}; }}"
            "QMenu::indicator { width: 13px; height: 13px; }"
        )

        locked_cols = {COL_ROWNUM, COL_CHECK, COL_NAME}
        is_admin = self.user_role == "admin"
        for col, column_def in enumerate(COLUMNS):
            if col in locked_cols:
                continue
            # Maps Link (and any other admin_only column) never gets a
            # toggle for non-admins -- there's nothing for them to turn
            # on, since it's force-hidden regardless of this menu.
            if column_def.get("admin_only") and not is_admin:
                continue
            action = QAction(column_def["label"], self)
            action.setCheckable(True)
            action.setChecked(not self.table.isColumnHidden(col))
            action.toggled.connect(
                lambda checked, c=col: self.table.setColumnHidden(c, not checked)
            )
            menu.addAction(action)

        pos = self.columns_btn.mapToGlobal(QPoint(0, self.columns_btn.height() + 4))
        menu.exec(pos)

    def build_bulk_unlock_bar(self) -> QWidget:
        """
        "Bulk Unlock" action card shown above the results table -- unlock
        contact info for every *checked* row in one go instead of clicking
        each row's own Unlock button. Styled off the app's own red accent
        (not the violet from the original mock) so it reads as the same
        brand as Search/Export/the row-level Unlock button rather than a
        one-off color.

        "Unlock Phone" / "Unlock Emails" / "Unlock Phone & Emails" unlock
        phone and email independently -- each field has its own
        'unlocked_phone' / 'unlocked_email' flag and its own 1-credit
        price (see core.models). "Unlock Phone" only reveals/charges for
        phone on the checked rows that still have it locked, "Unlock
        Emails" only email, and "Both" covers whichever of the two are
        still locked per row. "Unlock Selected" (Both) is the primary
        action and always targets every checked row with anything left
        to unlock.
        """
        frame = QFrame()
        frame.setObjectName("BulkUnlockBar")
        frame.setStyleSheet(
            f"QFrame#BulkUnlockBar {{ background-color: {PALETTE['bg_surface']}; "
            f"border: 1px solid {PALETTE['border_strong']}; border-radius: 10px; }}"
        )
        outer = QHBoxLayout(frame)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(14)

        # --- left: icon badge + title/subtitle -----------------------------
        icon_badge = QLabel()
        icon_badge.setFixedSize(32, 32)
        icon_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_badge.setStyleSheet(
            f"background-color: {PALETTE['accent_soft']}; border-radius: 16px;"
        )
        icon_badge.setPixmap(qta.icon('fa5s.bolt', color=PALETTE['accent']).pixmap(15, 15))
        outer.addWidget(icon_badge)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)
        title_lbl = QLabel("Bulk Unlock")
        title_lbl.setStyleSheet(
            f"color: {PALETTE['text_primary']}; font-size: 13px; font-weight: 600; background: transparent;"
        )
        text_col.addWidget(title_lbl)

        sub_row = QHBoxLayout()
        sub_row.setContentsMargins(0, 0, 0, 0)
        sub_row.setSpacing(6)
        self.bulk_selected_lbl = QLabel("0 selected")
        self.bulk_selected_lbl.setStyleSheet(
            f"color: {PALETTE['accent_text']}; font-size: 11px; font-weight: 600; background: transparent;"
        )
        sub_row.addWidget(self.bulk_selected_lbl)
        dot_lbl = QLabel("路")
        dot_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 11px; background: transparent;")
        sub_row.addWidget(dot_lbl)
        subtitle_lbl = QLabel("Unlock contact info for every checked business")
        subtitle_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 11px; background: transparent;")
        sub_row.addWidget(subtitle_lbl)
        sub_row.addStretch()
        text_col.addLayout(sub_row)
        outer.addLayout(text_col)

        outer.addStretch()

        self.bulk_clear_btn = QPushButton("Clear selection")
        self.bulk_clear_btn.setObjectName("LinkBtn")
        self.bulk_clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.bulk_clear_btn.clicked.connect(self.clear_bulk_selection)
        outer.addWidget(self.bulk_clear_btn)

        # --- divider ---------------------------------------------------------
        divider = QFrame()
        divider.setFixedWidth(1)
        divider.setFixedHeight(26)
        divider.setStyleSheet(f"background-color: {PALETTE['border_strong']};")
        outer.addWidget(divider)

        # --- right: segmented group (Phone / Email / Both) + primary CTA ----
        segment = QFrame()
        segment.setObjectName("BulkSegment")
        segment.setStyleSheet(
            f"QFrame#BulkSegment {{ background-color: {PALETTE['bg_app']}; "
            f"border: 1px solid {PALETTE['border_strong']}; border-radius: 7px; }}"
            f"QPushButton#SegmentBtn {{ background-color: transparent; border: none; "
            f"border-right: 1px solid {PALETTE['border_strong']}; border-radius: 0px; "
            f"padding: 7px 12px; color: {PALETTE['text_secondary']}; font-size: 12px; font-weight: 500; }}"
            f"QPushButton#SegmentBtn:hover {{ background-color: {PALETTE['bg_hover']}; color: {PALETTE['text_primary']}; }}"
            f"QPushButton#SegmentBtn:disabled {{ color: {PALETTE['text_dim']}; }}"
        )
        seg_row = QHBoxLayout(segment)
        seg_row.setContentsMargins(0, 0, 0, 0)
        seg_row.setSpacing(0)

        def _make_segment_btn(icon_name: str, label: str, mode: str, last: bool = False) -> QPushButton:
            btn = QPushButton(qta.icon(icon_name, color=PALETTE['text_muted']), " " + label)
            btn.setObjectName("SegmentBtn")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            if last:
                btn.setStyleSheet("border-right: none;")
            btn.clicked.connect(lambda _checked=False, m=mode: self.on_bulk_unlock(m))
            return btn

        self.bulk_phone_btn = _make_segment_btn('fa5s.phone-alt', "Phone", "phone")
        self.bulk_email_btn = _make_segment_btn('fa5s.envelope', "Email", "email")
        self.bulk_both_btn = _make_segment_btn('fa5s.address-book', "Both", "all", last=True)
        seg_row.addWidget(self.bulk_phone_btn)
        seg_row.addWidget(self.bulk_email_btn)
        seg_row.addWidget(self.bulk_both_btn)
        outer.addWidget(segment)

        self.bulk_unlock_bar = frame
        return frame

    def _checked_rows(self) -> list:
        """Every business in the current filtered/sorted result set
        (self.current_data) that's selected -- across *all* pages, not
        just whatever page is currently rendered. Bulk unlock and export
        both read from this."""
        return [biz for biz in self.current_data if id(biz) in self.selected_ids]

    def _make_row_check_handler(self, biz: dict):
        """Returns a toggled-signal handler for one row's checkbox that
        keeps self.selected_ids (the global, all-pages selection set) in
        sync with that specific box, then refreshes the bulk unlock card
        and the header 'select all' checkbox."""
        def _handler(checked: bool):
            if checked:
                self.selected_ids.add(id(biz))
            else:
                self.selected_ids.discard(id(biz))
            self._update_bulk_bar()
            self._sync_header_select_all()
        return _handler

    def _sync_header_select_all(self):
        """Re-checks/unchecks the floating header checkbox to reflect
        whether every row in the current filtered result set is selected,
        without emitting toggled (which would recurse into
        _on_select_all_toggled and stomp on a partial selection)."""
        if not hasattr(self, "header_select_all"):
            return
        all_selected = bool(self.current_data) and all(
            id(biz) in self.selected_ids for biz in self.current_data
        )
        if self.header_select_all.isChecked() != all_selected:
            self.header_select_all.blockSignals(True)
            self.header_select_all.setChecked(all_selected)
            self.header_select_all.blockSignals(False)
            self.header_select_all.update()

    def _update_bulk_bar(self):
        """Refreshes the bulk-unlock card's selection count, cost, and
        button enabled-state. Called after populate_table() redraws the
        checkboxes and every time a checkbox (row or 'select all') is
        toggled."""
        if not hasattr(self, "bulk_unlock_bar"):
            return

        checked = self._checked_rows()
        count = len(checked)
        self.bulk_selected_lbl.setText(f"{count} selected")
        self.bulk_clear_btn.setEnabled(count > 0)

        targets_phone = bulk_unlock_target_businesses(checked, "phone")
        targets_email = bulk_unlock_target_businesses(checked, "email")
        targets_all = bulk_unlock_target_businesses(checked, "all")

        self.bulk_phone_btn.setEnabled(bool(targets_phone))
        self.bulk_email_btn.setEnabled(bool(targets_email))
        self.bulk_both_btn.setEnabled(bool(targets_all))

    def clear_bulk_selection(self):
        """Deselects every row in the current filtered result set (all
        pages), not just the ones currently on screen."""
        self.selected_ids = set()

        for row in range(self.table.rowCount()):
            box = self._row_checkbox(row)
            if box is not None and box.isChecked():
                box.blockSignals(True)
                box.setChecked(False)
                box.blockSignals(False)
                box.update()

        if hasattr(self, "header_select_all"):
            self.header_select_all.blockSignals(True)
            self.header_select_all.setChecked(False)
            self.header_select_all.blockSignals(False)
            self.header_select_all.update()

        self._update_bulk_bar()

    def on_bulk_unlock(self, mode: str):
        """Unlocks every checked, still-locked row that `mode` applies to
        (see build_bulk_unlock_bar's docstring). Mirrors on_unlock_row's
        free-row/admin/insufficient-credits/confirm flow, just batched."""
        checked = self._checked_rows()
        targets = bulk_unlock_target_businesses(checked, mode)
        field_label = _contact_field_label(mode)

        if not targets:
            InfoDialog.show(
                self, "Nothing to unlock",
                "Check at least one business first." if not checked else
                f"None of the checked businesses have a locked {field_label} to unlock.",
                icon_name='fa5s.info-circle', success=False,
            )
            return

        if self.user_role == "admin":
            QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
            try:
                for biz in targets:
                    apply_unlock(biz, mode)
            finally:
                QApplication.restoreOverrideCursor()
            self.populate_table()
            plural_biz, _ = _plural_business(len(targets))
            Toast.show_toast(
                self, "Unlocked!",
                f"{field_label.capitalize()} for {len(targets)} business{plural_biz} is now visible.",
                icon_name='fa5s.unlock-alt', success=True,
            )
            return

        cost = bulk_unlock_cost(targets, mode, self.email_unlock_cost, self.phone_unlock_cost)
        count = len(targets)
        plural_biz, _ = _plural_business(count)
        plural_cr = "s" if cost != 1 else ""

        if self.credits_remaining < cost:
            InfoDialog.show(
                self, "Not enough credits",
                f"Unlocking the {field_label} for {count} business{plural_biz} costs {cost} "
                f"credit(s), but you only have {self.credits_remaining:,} remaining.",
                icon_name='fa5s.coins', success=False,
            )
            return

        confirmed = ConfirmDialog.ask(
            self, f"Bulk Unlock {field_label.title()}",
            f"Unlock the {field_label} for {count} business{plural_biz} for {cost} credit{plural_cr}?",
            confirm_text="Unlock", icon_name='fa5s.unlock-alt', danger=False,
        )
        if not confirmed:
            return

        # Same server-authoritative persistence as on_unlock_row -- no
        # bulk endpoint exists server-side, so this calls unlock_lead()
        # once per target lead instead of one spend_credits() covering
        # the whole batch. Slower and not a single transaction, but
        # each business's unlock actually gets saved this way instead
        # of reverting to locked on the next reload while credits stay
        # spent (the bug this whole change fixes).
        #
        # If a later item in the batch fails (e.g. another spend from
        # this account landed concurrently and drained the balance),
        # everything unlocked *before* that point already succeeded and
        # was paid for server-side -- so we keep those rather than
        # trying to roll them back, and just stop and report what broke.
        unlocked_count = 0
        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        for biz in targets:
            lead_id = biz.get("lead_id")
            if lead_id is None:
                apply_unlock(biz, mode)  # no server row to persist to -- local-only fallback
                unlocked_count += 1
                continue
            try:
                result = unlock_lead(lead_id, mode)
            except ApiError as exc:
                QApplication.restoreOverrideCursor()
                self.credits_changed.emit(self.credits_remaining, self.credits_total)
                self.populate_table()
                InfoDialog.show(
                    self, "Bulk unlock stopped",
                    f"Unlocked the {field_label} for {unlocked_count} of {count} "
                    f"business{plural_biz} before this error, then stopped:\n\n{exc}",
                    icon_name='fa5s.exclamation-triangle', success=False,
                )
                return
            self.credits_remaining = result["credits"]
            unlocked = result["lead"]
            biz["unlocked_phone"] = unlocked["unlocked_phone"]
            biz["unlocked_email"] = unlocked["unlocked_email"]
            unlocked_count += 1
        QApplication.restoreOverrideCursor()

        plural_biz, possessive_biz = _plural_business(unlocked_count)
        self._log_activity(
            self.active_search_id, "Credits deducted",
            f"{cost} credit(s) for bulk unlocking {unlocked_count} business{plural_biz}"
            f"{possessive_biz} {field_label}",
        )
        self.credits_changed.emit(self.credits_remaining, self.credits_total)
        self.populate_table()
        Toast.show_toast(
            self, "Unlocked!",
            f"{field_label.capitalize()} for {unlocked_count} business{plural_biz} is now visible.",
            icon_name='fa5s.unlock-alt', success=True,
        )

    def build_activity_log(self) -> QWidget:
        """
        "Activity Log" card shown under the table/pagination footer. Static/
        prototype only -- each saved search carries its own canned list of
        log lines (see the "activity_log" key on each entry in data.SEARCHES);
        refresh_activity_log() swaps them in when the active search changes.
        """
        frame = QFrame()
        frame.setStyleSheet(
            f"background-color: {PALETTE['bg_app']};"
        )
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(6)

        header_row = QHBoxLayout()
        title_lbl = QLabel("Activity Log")
        title_lbl.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 12px; font-weight: 600; background: transparent;")
        header_row.addWidget(title_lbl)
        header_row.addStretch()
        view_all_btn = QPushButton("View all logs")
        view_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        view_all_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; color: {PALETTE['blue']}; font-size: 11px; }}"
            f"QPushButton:hover {{ color: {PALETTE['blue_solid']}; }}"
            f"QPushButton:disabled {{ color: {PALETTE['text_dim']}; }}"
        )
        view_all_btn.clicked.connect(self._view_all_logs)
        self.view_all_logs_btn = view_all_btn
        header_row.addWidget(view_all_btn)
        layout.addLayout(header_row)

        self.activity_rows_container = QWidget()
        self.activity_rows_layout = QVBoxLayout(self.activity_rows_container)
        self.activity_rows_layout.setContentsMargins(0, 0, 0, 0)
        self.activity_rows_layout.setSpacing(4)
        self.activity_rows_layout.addStretch()  # keeps rows pinned to the top when the pane is taller than the content

        activity_scroll = QScrollArea()
        activity_scroll.setWidgetResizable(True)
        activity_scroll.setFrameShape(QFrame.Shape.NoFrame)
        activity_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        activity_scroll.viewport().setStyleSheet("background: transparent;")
        activity_scroll.setWidget(self.activity_rows_container)
        layout.addWidget(activity_scroll)

        active_search = next((s for s in self.searches if s["id"] == self.active_search_id), None)
        self.refresh_activity_log(active_search)

        return frame

    def refresh_activity_log(self, search: dict | None):
        """Rebuilds the Activity Log rows from search['activity_log'].
        Shows a "No activity yet" placeholder -- and disables "View all
        logs" -- when there's no active search or it has no log entries."""
        self._clear_layout(self.activity_rows_layout)
        entries = search.get("activity_log", []) if search else []

        if not entries:
            empty_lbl = QLabel("No activity yet")
            empty_lbl.setStyleSheet(
                f"color: {PALETTE['text_dim']}; font-size: 11px; background: transparent; border: none;"
            )
            self.activity_rows_layout.addWidget(empty_lbl)
        else:
            for entry in entries:
                self.activity_rows_layout.addWidget(self._activity_row_widget(entry))

        self.activity_rows_layout.addStretch()  # keeps rows pinned to the top when the pane is taller than the content
        self.view_all_logs_btn.setEnabled(bool(entries))

    def _log_activity(self, search_id, text: str, meta: str = ""):
        """Appends one entry to the given search's activity_log (str/int
        id-agnostic match, same as the id comparisons elsewhere in this
        file), refreshes the Activity Log pane immediately if that
        search happens to be the one currently on screen, and -- for a
        real server-backed search (numeric id, not an admin run's local
        "scrape_..." id) -- persists the same line via POST
        /searches/{id}/activity so it survives a relaunch instead of
        only living in self.searches for this session. Used for credit
        deductions (search/unlock/export) and the export-complete line
        -- anything that happens *after* a search's own log was already
        built in _on_scrape_finished/_load_searches_from_server.

        No-ops if `search_id` isn't found -- e.g. a lead_id-less business
        that only ever existed locally (on_unlock_row's lead_id is None
        fallback), which has no server-side Search row to log against.
        The server POST is best-effort: a failure there shouldn't undo
        or block the already-applied local change (same "don't punish a
        successful action over a failed history write" pattern as
        export_current_search()'s create_export() calls)."""
        if search_id is None:
            return
        target_id = str(search_id)
        search = next((s for s in self.searches if str(s["id"]) == target_id), None)
        if search is None:
            return
        search.setdefault("activity_log", []).append({"text": text, "meta": meta, "time": "Just now"})
        if str(self.active_search_id) == target_id:
            self.refresh_activity_log(search)

        try:
            numeric_id = int(target_id)
        except ValueError:
            return  # admin run's local-only id -- no server Search row to log against
        try:
            log_search_activity(numeric_id, text, meta or None)
        except ApiError:
            pass

    @staticmethod
    def _activity_row_widget(entry: dict) -> QWidget:
        row_wrap = QWidget()
        row_wrap.setStyleSheet(f"border-bottom: 1px solid {PALETTE['divider']};")
        row = QHBoxLayout(row_wrap)
        row.setContentsMargins(0, 3, 0, 3)
        row.setSpacing(8)

        icon_lbl = QLabel()
        icon_lbl.setFrameShape(QFrame.Shape.NoFrame)
        icon_lbl.setStyleSheet("background: transparent; border: none;")
        icon_lbl.setPixmap(qta.icon('fa5s.check-circle', color=PALETTE['green']).pixmap(12, 12))
        row.addWidget(icon_lbl)

        text_lbl = QLabel(entry.get("text", ""))
        text_lbl.setFrameShape(QFrame.Shape.NoFrame)
        text_lbl.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 11px; background: transparent; border: none;")
        row.addWidget(text_lbl)

        meta_lbl = QLabel(entry.get("meta", ""))
        meta_lbl.setFrameShape(QFrame.Shape.NoFrame)
        meta_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 11px; background: transparent; border: none;")
        row.addWidget(meta_lbl)

        row.addStretch()

        time_lbl = QLabel(entry.get("time", ""))
        time_lbl.setFrameShape(QFrame.Shape.NoFrame)
        time_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 10px; background: transparent; border: none;")
        row.addWidget(time_lbl)

        return row_wrap

    def _view_all_logs(self):
        """Opens the full activity log for the currently-active search in
        a scrollable dialog."""
        active_search = next((s for s in self.searches if s["id"] == self.active_search_id), None)
        if active_search is None:
            return
        ActivityLogDialog.show_log(
            self, active_search.get("header", active_search.get("title", "")),
            active_search.get("activity_log", []),
        )

    @staticmethod
    def _clear_layout(layout):
        """Removes and deletes every widget currently in a layout."""
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

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

    def render_page_buttons(self):
        """Rebuilds the numbered page buttons, collapsing long runs into
        '...' (e.g. 1 2 ... 8 9 10 ... 16 17) once there are many pages."""
        self._clear_layout(self.page_buttons_layout)

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
                # Mirrors #RedBtn's accent-bg / white-text styling (see
                # config.qss()) -- built by hand here since this button
                # isn't routed through the QSS object-name system.
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
            btn.clicked.connect(lambda _checked=False, pp=p: self.go_to_page(pp))
            self.page_buttons_layout.addWidget(btn)

        self.prev_page_btn.setEnabled(current > 1)
        self.next_page_btn.setEnabled(current < total)

    def eventFilter(self, obj, event):
        """Keeps the floating header 'select all' checkbox glued to the
        COL_CHECK section whenever the header itself resizes (window
        resize, splitter drag, column show/hide, etc.)."""
        if obj is self.table.horizontalHeader() and event.type() == QEvent.Type.Resize:
            self._position_select_all_checkbox()
        return super().eventFilter(obj, event)

    def _position_select_all_checkbox(self):
        header = self.table.horizontalHeader()
        x = header.sectionViewportPosition(COL_CHECK)
        w = header.sectionSize(COL_CHECK)
        cb = self.header_select_all
        cb.move(x + (w - cb.width()) // 2, (header.height() - cb.height()) // 2)
        cb.raise_()

    def _row_checkbox(self, row: int):
        """The hand-painted LeadCheckBox living inside a row's checkbox
        cell widget (checkbox_cell() wraps it in a centering QWidget)."""
        widget = self.table.cellWidget(row, COL_CHECK)
        return widget.findChild(LeadCheckBox) if widget else None

    def _on_select_all_toggled(self, checked: bool):
        """Header checkbox toggled -- applies to *every* row matching the
        current search/filters, not just the ones on the visible page
        (that's the whole point of tracking selection by id in
        self.selected_ids instead of per-row-widget state)."""
        if checked:
            self.selected_ids = {id(biz) for biz in self.current_data}
        else:
            self.selected_ids = set()

        # Reflect the new state on whichever checkboxes are actually
        # visible right now; populate_table() (via _row_checkbox's initial
        # `checked=`) handles every other page whenever it's drawn.
        for row in range(self.table.rowCount()):
            box = self._row_checkbox(row)
            if box and box.isChecked() != checked:
                box.blockSignals(True)
                box.setChecked(checked)
                box.blockSignals(False)
                box.update()
        self._update_bulk_bar()

    def populate_table(self):
        """
        (Re)draws the table from the *current page* of self.current_data.
        Called on initial load, after sorting, after switching searches,
        after changing the page-size dropdown, after go_to_page(), and
        after unlocking a row. Also keeps the footer text and pagination
        buttons in sync.
        """
        total_pages = self.total_pages()
        self.current_page = max(1, min(self.current_page, total_pages))

        start = (self.current_page - 1) * self.page_size
        end = start + self.page_size
        page_data = self.current_data[start:end]
        self._current_page_data = page_data

        self.table.setRowCount(len(page_data))

        for row, biz in enumerate(page_data):
            row_num_item = QTableWidgetItem(str(start + row + 1))
            row_num_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, COL_ROWNUM, row_num_item)

            # Empty QTableWidgetItems are added alongside the cell widgets
            # below for CHECK/RATING/PHONE/WEBSITE/EMAIL/STATUS. Without an
            # item, Qt's selection delegate has nothing to paint a highlight
            # onto for that cell, so selected rows showed the raw dark
            # viewport background (looked "black") in these columns while
            # the plain-text columns lit up gray as expected.
            self.table.setItem(row, COL_CHECK, QTableWidgetItem())
            self.table.setCellWidget(row, COL_CHECK, checkbox_cell(checked=id(biz) in self.selected_ids))
            # Row checkbox needs to update self.selected_ids (by id(biz),
            # so it survives pagination/sorting/filtering -- see the note
            # on self.selected_ids in __init__) and refresh the bulk
            # unlock card, not just repaint itself.
            row_box = self._row_checkbox(row)
            if row_box is not None:
                row_box.toggled.connect(self._make_row_check_handler(biz))

            self.table.setItem(row, COL_NAME, QTableWidgetItem(biz["name"]))

            # Rating: number first, star icon to its right
            self.table.setItem(row, COL_RATING, QTableWidgetItem())
            self.table.setCellWidget(row, COL_RATING, rating_cell(biz["rating"]))

            # Reviews: plain number, no icon, no thousands separator
            self.table.setItem(row, COL_REVIEWS, QTableWidgetItem(str(biz.get("reviews", 0))))

            self.table.setItem(row, COL_CATEGORY, QTableWidgetItem(biz["category"]))

            # Phone: locked behind credits until unlocked. "--" if the
            # business simply has no phone at all (nothing to unlock/pay for).
            self.table.setItem(row, COL_PHONE, QTableWidgetItem())
            if not business_has_phone(biz):
                phone_widget = email_cell("--", color=PALETTE['text_dim'])
            elif biz.get("unlocked_phone") or self.user_role == "admin":
                phone_widget = email_cell(biz.get("phone_num", "--"), color=PALETTE['text_muted'])
            else:
                phone_widget = locked_contact_cell(
                    business_field_cost(biz, "phone", self.email_unlock_cost, self.phone_unlock_cost),
                    lambda _checked=False, b=biz: self.on_unlock_row(b, "phone")
                )
            self.table.setCellWidget(row, COL_PHONE, phone_widget)

            # Website: the only contact field that keeps the icon + blue
            # color (never gated -- only phone/email cost credits)
            self.table.setItem(row, COL_WEBSITE, QTableWidgetItem())
            self.table.setCellWidget(
                row, COL_WEBSITE,
                website_cell(biz.get("site", "--"), color=PALETTE['blue'])
            )

            # Email: locked behind credits until unlocked. "--" if the
            # business simply has no email at all.
            self.table.setItem(row, COL_EMAIL, QTableWidgetItem())
            if not business_has_email(biz):
                email_widget = email_cell("--", color=PALETTE['text_dim'])
            elif biz.get("unlocked_email") or self.user_role == "admin":
                email_widget = email_cell(biz.get("email_addr", "--"), color=PALETTE['text_muted'])
            else:
                email_widget = locked_contact_cell(
                    business_field_cost(biz, "email", self.email_unlock_cost, self.phone_unlock_cost),
                    lambda _checked=False, b=biz: self.on_unlock_row(b, "email")
                )
            self.table.setCellWidget(row, COL_EMAIL, email_widget)

            self.table.setItem(row, COL_STATUS, QTableWidgetItem())
            self.table.setCellWidget(row, COL_STATUS, status_pill(biz["status"]))

            # Address / Maps Link: hidden by default (see
            # DEFAULT_HIDDEN_COLUMNS) but still populated so toggling them
            # on from the Columns menu -- or exporting with them checked --
            # shows real data instead of a blank column.
            self.table.setItem(row, COL_ADDRESS, QTableWidgetItem())
            self.table.setCellWidget(
                row, COL_ADDRESS,
                email_cell(biz.get("address") or "--", color=PALETTE['text_muted'])
            )

            self.table.setItem(row, COL_MAPS_LINK, QTableWidgetItem())
            self.table.setCellWidget(
                row, COL_MAPS_LINK,
                website_cell(biz.get("maps_url") or "--", color=PALETTE['blue'])
            )

        # Pre-select first row.
        if page_data:
            self.table.selectRow(0)

        if hasattr(self, "bulk_unlock_bar"):
            self.bulk_unlock_bar.setVisible(bool(page_data))

        # Footer text: "Showing X to Y of Z results"
        total_results = len(self.current_data)
        showing_from = start + 1 if page_data else 0
        showing_to = start + len(page_data)
        self.footer_label.setText(f"Showing {showing_from} to {showing_to} of {total_results} results")

        self.render_page_buttons()

        # Header 'select all' reflects whether *every* row across the whole
        # filtered result set (not just this page) is currently selected --
        # matches selected_ids being the global source of truth now.
        if hasattr(self, "header_select_all"):
            all_selected = bool(self.current_data) and all(
                id(biz) in self.selected_ids for biz in self.current_data
            )
            self.header_select_all.blockSignals(True)
            self.header_select_all.setChecked(all_selected)
            self.header_select_all.blockSignals(False)
            self.header_select_all.update()
            self._position_select_all_checkbox()

        self._update_bulk_bar()

    def handle_sort(self, column: int):
        key = COLUMNS[column]["key"]
        if key is None:
            return  # row-number / checkbox columns aren't sortable

        if self.sort_column == column:
            self.sort_ascending = not self.sort_ascending
        else:
            self.sort_column = column
            self.sort_ascending = True

        order = Qt.SortOrder.AscendingOrder if self.sort_ascending else Qt.SortOrder.DescendingOrder
        self.table.horizontalHeader().setSortIndicator(column, order)

        self.current_page = 1
        self.apply_filter()

    def _trigger_search(self):
        """Runs the search: only called on Enter (returnPressed) or a
        click on the search-icon action -- not on every keystroke. Matches
        name, category, phone, website, email, address, rating, reviews,
        and status (case-insensitive substring)."""
        self.search_text = self.search_edit.text().strip().lower()
        self.current_page = 1
        self.apply_filter()

    _SEARCH_FIELDS = (
        "name", "category", "phone_num", "site", "email_addr",
        "address", "maps_url", "rating", "reviews", "status",
    )

    def _prep_data(self, businesses: list) -> list:
        """Runs ensure_unlock_state() and, once per row, precomputes a
        single lowercased '_search_blob' string (all searchable fields
        joined together). apply_filter() used to rebuild that lowercase
        text from scratch on every keystroke-pause for every row -- fine
        for a handful of rows, but with a real (unbounded) scrape result
        set that repeated str()+lower() work across 9 fields per row is
        exactly what still made the debounced filter pass itself feel
        like a freeze. Doing it once here, when the data is loaded rather
        than every time it's filtered, turns each filter pass into a
        single substring check per row instead of nine."""
        prepped = ensure_unlock_state(list(businesses))
        for biz in prepped:
            biz["_search_blob"] = " ".join(
                str(biz.get(f, "")).lower() for f in self._SEARCH_FIELDS
            )
        return prepped

    def apply_filter(self):
        """Dispatches the current search text / threshold filters / sort
        to a background worker on the global QThreadPool instead of
        running them inline on the GUI thread. _on_filter_result() picks
        up the result and does the actual table redraw once it lands
        (redraws must stay on the GUI thread -- only the filtering/sorting
        computation itself is offloaded).

        A generation counter guards against out-of-order completions: if
        the user changes the search/filters again before an in-flight
        worker finishes, its eventual result is just discarded rather than
        overwriting a newer one."""
        self._filter_generation += 1

        sort_key = COLUMNS[self.sort_column]["key"] if self.sort_column is not None else None

        worker = _FilterWorker(
            generation=self._filter_generation,
            all_data=self.all_data,
            search_text=self.search_text,
            active_filters=dict(self.active_filters),
            sort_key=sort_key,
            sort_ascending=self.sort_ascending,
        )
        worker.signals.finished.connect(self._on_filter_result)
        QThreadPool.globalInstance().start(worker)

    def _on_filter_result(self, generation: int, filtered: list):
        if generation != self._filter_generation:
            return  # a newer filter/search/sort superseded this one -- drop it
        self.current_data = filtered
        self.populate_table()

    def on_unlock_row(self, biz: dict, field: str = "all"):
        """Spends credits to reveal a row's phone and/or email, independently.
        `field` is "phone", "email", or "all". Free fields (business has no
        phone / no email) are already auto-unlocked by ensure_unlock_state(),
        so this only ever spends on paid fields."""
        if field == "all" and business_fully_unlocked(biz):
            return
        if field == "phone" and biz.get("unlocked_phone"):
            return
        if field == "email" and biz.get("unlocked_email"):
            return

        field_label = _contact_field_label(field)
        biz_name = biz.get("name", "this business")

        cost = 0
        if field in ("phone", "all"):
            cost += business_field_cost(biz, "phone", self.email_unlock_cost, self.phone_unlock_cost)
        if field in ("email", "all"):
            cost += business_field_cost(biz, "email", self.email_unlock_cost, self.phone_unlock_cost)

        if cost == 0:
            apply_unlock(biz, field)
            self.populate_table()
            return

        if self.user_role == "admin":
            apply_unlock(biz, field)
            self.populate_table()
            Toast.show_toast(
                self, "Unlocked!",
                f"The {field_label} for \"{biz_name}\" is now visible.",
                icon_name='fa5s.unlock-alt', success=True,
            )
            return

        if self.credits_remaining < cost:
            InfoDialog.show(
                self, "Not enough credits",
                f"Unlocking the {field_label} costs {cost} credit(s), but you only "
                f"have {self.credits_remaining:,} remaining.",
                icon_name='fa5s.coins', success=False,
            )
            return

        confirmed = ConfirmDialog.ask(
            self, f"Unlock {field_label.title()}",
            f"Unlock this business's {field_label} for {cost} credit(s)?",
            confirm_text="Unlock", icon_name='fa5s.unlock-alt', danger=False,
        )
        if not confirmed:
            return

        # Server is authoritative: it checks-and-deducts credits AND
        # persists the unlocked_phone/unlocked_email flag(s) atomically
        # in one transaction (see app/routers/leads.py, app/db.py's
        # unlock_lead) -- unlike the old spend_credits()-only call,
        # this is what makes the unlock survive a logout/login or a
        # search history reload instead of reverting to locked while
        # the credits stay spent.
        #
        # lead_id is only absent for a business that was never posted
        # to the server (shouldn't happen for a real row the user can
        # already see/unlock, but fall back to the old local-only flip
        # rather than crash if it somehow is).
        lead_id = biz.get("lead_id")
        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        if lead_id is None:
            try:
                result = spend_credits(cost, reason="unlock_contact")
            except ApiError as exc:
                QApplication.restoreOverrideCursor()
                InfoDialog.show(self, "Couldn't unlock", str(exc), success=False)
                return
            self.credits_remaining = result["credits"]
            apply_unlock(biz, field)
        else:
            try:
                result = unlock_lead(lead_id, field)
            except ApiError as exc:
                QApplication.restoreOverrideCursor()
                InfoDialog.show(self, "Couldn't unlock", str(exc), success=False)
                return
            self.credits_remaining = result["credits"]
            unlocked = result["lead"]
            biz["unlocked_phone"] = unlocked["unlocked_phone"]
            biz["unlocked_email"] = unlocked["unlocked_email"]
            self._log_activity(
                self.active_search_id, "Credits deducted",
                f"{cost} credit(s) for unlocking \"{biz_name}\"'s {field_label}",
            )
        QApplication.restoreOverrideCursor()

        self.credits_changed.emit(self.credits_remaining, self.credits_total)
        self.populate_table()
        Toast.show_toast(
            self, "Unlocked!",
            f"The {field_label} for \"{biz_name}\" is now visible.",
            icon_name='fa5s.unlock-alt', success=True,
        )

    def load_search(self, search_id: str):
        """
        Called when a Search Queries card is clicked. Swaps the header
        title/count, table contents, sort state, and footer text
        panel over to the chosen search's own dataset.
        """
        if search_id == self.active_search_id:
            return

        search = next((s for s in self.searches if s["id"] == search_id), None)
        if search is None:
            return

        self.active_search_id = search_id

        for sid, card in self.query_cards.items():
            card.set_active(sid == search_id)

        self.title_lbl.setText(search["header"])
        self.title_lbl.setToolTip(search.get("title", ""))
        self.sub_lbl.setText(f"{len(search['data'])} businesses found")
        self.active_search_changed.emit(search["header"])

        self.all_data = self._prep_data(search["data"])
        # New dataset -- reset selection to "everything selected" (matches
        # a fresh search's default) rather than carrying over ids from the
        # previous search's now-gone dict objects.
        self.selected_ids = {id(biz) for biz in self.all_data}
        self.sort_column = None
        self.sort_ascending = True
        self.table.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        self.current_page = 1

        self.search_text = ""
        self.search_edit.blockSignals(True)
        self.search_edit.clear()
        self.search_edit.blockSignals(False)
        self.active_filters = {
            "min_rating_label": "Any", "min_rating": 0.0,
            "min_reviews": 0, "status": "Any", "category": "Any",
            "has_phone": False, "has_email": False,
        }

        self.apply_filter()
        self.refresh_activity_log(search)

    def remove_search(self, search_id: str):
        """
        Called when a card's '×' button is clicked. Soft-deletes the
        search server-side first (see api_client.delete_search /
        db.delete_search -- the row and its leads stay in the DB, it
        just stops coming back from GET /searches on next login), and
        only touches local UI state once that succeeds: removes it from
        self.searches, deletes its card widget, and -- if it was the
        active one -- switches to another remaining search (or clears
        the view if none are left).
        """
        try:
            delete_search(search_id)
        except ApiError as exc:
            InfoDialog.show(self, "Couldn't remove search", str(exc), success=False)
            return

        card = self.query_cards.pop(search_id, None)
        if card is not None:
            self.queries_layout.removeWidget(card)
            card.deleteLater()

        self.searches = [s for s in self.searches if s["id"] != search_id]

        if search_id == self.active_search_id:
            self.active_search_id = None  # so load_search doesn't early-return
            if self.searches:
                self.load_search(self.searches[0]["id"])
            else:
                self.all_data = []
                self.current_data = []
                self.selected_ids = set()
                self.current_page = 1
                self.search_text = ""
                self.search_edit.blockSignals(True)
                self.search_edit.clear()
                self.search_edit.blockSignals(False)
                self.active_filters = {
                    "min_rating_label": "Any", "min_rating": 0.0,
                    "min_reviews": 0, "status": "Any", "category": "Any",
                    "has_phone": False, "has_email": False,
                }
                self.title_lbl.setText("No searches yet")
                self.sub_lbl.setText("0 businesses found")
                self.active_search_changed.emit("No searches yet")
                self.populate_table()
                self._clear_layout(self.activity_rows_layout)