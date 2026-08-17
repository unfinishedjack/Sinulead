"""
settings_page.py

SettingsPage: the "Settings" nav tab. For now this just houses the
referral program -- your own code (with a working clipboard-copy button),
how many friends have joined with it, and a quick "how it works" recap.
Everything else a real Settings page would have (notifications, password
change, etc.) can slot in here later as additional cards.

Depends on: config (colors, REFERRAL_PROGRAM_ENABLED), core.api_client
(for the real "friends joined" count and real "credits earned" total --
see PROGRESS.md Phase 5; used to read data.db directly), widgets
(build_referral_copy_field).
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QScrollArea,
    QPushButton, QStackedWidget, QApplication, QFormLayout, QSpinBox,
    QCheckBox, QLineEdit, QComboBox,
)
from PySide6.QtCore import Qt, Signal, QObject, QRunnable, QThreadPool
from PySide6.QtGui import QShortcut, QKeySequence
import qtawesome as qta

from core.config import PALETTE, REFERRAL_PROGRAM_ENABLED
from core.api_client import (
    list_my_referrals, get_my_referral_credits_earned, ApiError,
    get_global_scraper_settings, update_my_admin_scraper_settings,
    update_global_scraper_settings,
)
from core.theme_state import THEME_STATE
from core.scraper_settings import (
    load_scraper_config, save_local_scraper_config, redetect_chrome,
    default_local_config, _DEFAULT_SYNCED_CONFIG,
)
from ui.components.widgets import build_referral_copy_field, dash_card
from ui.pages.maintenance_tab import MaintenanceTab
from ui.pages.pricing_tab import PricingTab
from ui.dialogs.dialogs import InfoDialog


def _card(title: str = None):
    """Thin wrapper over widgets.dash_card() (see PROGRESS.md, Phase 1c)."""
    return dash_card(title, margins=(18, 16, 18, 16))


def _field_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(f"color: {PALETTE['text_muted']}; font-size: 11px; background: transparent;")
    return lbl


class _ScraperSettingsLoadSignals(QObject):
    """QRunnable can't emit signals itself -- same pattern as
    overview.py's _OverviewRefreshSignals / earn_credits.py's
    _RewardsRefreshSignals."""
    finished = Signal(int, object, object)  # generation, cfg-dict-or-None, error-str-or-None


class _ScraperSettingsLoadWorker(QRunnable):
    """Runs load_fn() (load_scraper_config for the personal scope,
    get_global_scraper_settings for the global one) on a background
    thread instead of the GUI thread -- opening the Web Scraper tab
    now needs a network round trip (Phase 7), and doing that
    synchronously the way this page used to would freeze the app the
    same way OverviewPage's old showEvent() did before it moved to
    _OverviewRefreshWorker."""

    def __init__(self, generation: int, load_fn):
        super().__init__()
        self.generation = generation
        self._load_fn = load_fn
        self.signals = _ScraperSettingsLoadSignals()

    def run(self):
        try:
            cfg = self._load_fn()
            self.signals.finished.emit(self.generation, cfg, None)
        except (ApiError, OSError) as e:
            self.signals.finished.emit(self.generation, None, str(e))


class _ScraperSettingsPanel(QScrollArea):
    """
    One scope's worth of the Settings > Web Scraper form: either an
    admin's own "My Settings" (scope="personal", backed by
    AdminScraperSettings via load_scraper_config()/
    update_my_admin_scraper_settings()) or the single shared "Default
    for Users" (scope="global", backed by GlobalScraperSettings via
    get_global_scraper_settings()/update_global_scraper_settings()).
    SettingsPage._build_scraper_settings_page() builds one of each and
    swaps between them with the same sub-tab pattern billing.py uses.

    Browser Behavior (headless, close-after-run) is a synced field
    like everything in the Behaviour card, so it appears for both
    scopes -- it's meaningful to set for "Default for Users" too,
    since it controls how a plain user's own machine runs the
    scraper. The Chrome/Browser card (executable path, profile dir,
    debug port) only ever appears for scope="personal" -- those
    describe *this* machine specifically, so there's nothing an admin
    can sensibly set there for every other machine.

    Every field starts out showing the hardcoded fallback defaults,
    disabled, while load_fn() runs on a background thread (see
    _ScraperSettingsLoadWorker); real values replace the placeholders
    and the form becomes editable once that resolves, whether it
    succeeded or not (a failed load still leaves something sane to
    edit -- Save just attempts a fresh write either way, same
    don't-brick-the-tab fallback PricingTab uses for GET /pricing).
    """

    def __init__(self, scope: str, load_fn, save_synced_fn, parent=None):
        super().__init__(parent)
        assert scope in ("personal", "global")
        self.scope = scope
        self.include_chrome = scope == "personal"
        self._load_fn = load_fn
        self._save_synced_fn = save_synced_fn
        self._loaded = False
        self._generation = 0
        self._form_widgets = []

        self.setObjectName("DashScroll")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)

        # Fallback starting values: local machine fields are always
        # real (auto-detected, no network needed); synced fields start
        # as the hardcoded defaults and get overwritten once load_fn()
        # returns -- see _on_load_finished.
        self._cfg = dict(_DEFAULT_SYNCED_CONFIG)
        self._cfg.update(default_local_config())

        content = QWidget()
        outer = QVBoxLayout(content)
        outer.setContentsMargins(0, 12, 0, 0)
        outer.setSpacing(14)

        self._status_lbl = QLabel("Loading settings\u2026")
        self._status_lbl.setWordWrap(True)
        self._status_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 11px; background: transparent;")
        outer.addWidget(self._status_lbl)

        if scope == "global":
            hint = QLabel("These are the settings every non-admin account's searches will run with.")
            hint.setWordWrap(True)
            hint.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 11px; background: transparent;")
            outer.addWidget(hint)

        outer.addWidget(self._build_behaviour_card())
        outer.addWidget(self._build_browser_behavior_card())
        if self.include_chrome:
            outer.addWidget(self._build_chrome_card())
        outer.addWidget(self._build_enrichment_card())
        outer.addWidget(self._build_output_card())

        save_row = QHBoxLayout()
        save_row.addStretch()
        self.save_btn = QPushButton(qta.icon('fa5s.save', color="#ffffff"), " Save Scraper Settings")
        self.save_btn.setObjectName("RedBtn")
        self.save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_btn.clicked.connect(self._on_save)
        self._form_widgets.append(self.save_btn)
        save_row.addWidget(self.save_btn)
        outer.addLayout(save_row)

        outer.addStretch()
        self.setWidget(content)

        # Let admins hit Enter/Return anywhere on this tab to save,
        # instead of forcing a click on the button.
        # WidgetWithChildrenShortcut means it fires as long as focus is
        # somewhere inside `content`, not just on the button itself.
        for key in (QKeySequence(Qt.Key.Key_Return), QKeySequence(Qt.Key.Key_Enter)):
            shortcut = QShortcut(key, content)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(self._on_save)

        self._set_form_enabled(False)
        self._start_load()

    # -- building blocks --------------------------------------------

    def _build_behaviour_card(self) -> QWidget:
        card, layout = _card("Scraping Behaviour")
        form = QFormLayout()
        form.setSpacing(10)

        self.max_scrolls_spin = QSpinBox()
        self.max_scrolls_spin.setRange(1, 200)
        self.max_scrolls_spin.setValue(self._cfg["max_scrolls_per_query"])
        form.addRow(_field_label("Max scrolls per query"), self.max_scrolls_spin)

        self.scroll_wait_spin = QSpinBox()
        self.scroll_wait_spin.setRange(100, 10000)
        self.scroll_wait_spin.setSingleStep(100)
        self.scroll_wait_spin.setSuffix(" ms")
        self.scroll_wait_spin.setValue(self._cfg["scroll_wait_ms"])
        form.addRow(_field_label("Wait between scrolls"), self.scroll_wait_spin)

        self.stale_rounds_spin = QSpinBox()
        self.stale_rounds_spin.setRange(1, 20)
        self.stale_rounds_spin.setValue(self._cfg["stale_rounds_threshold"])
        form.addRow(_field_label("Stale rounds before giving up"), self.stale_rounds_spin)

        self.max_concurrent_spin = QSpinBox()
        self.max_concurrent_spin.setRange(1, 10)
        self.max_concurrent_spin.setValue(self._cfg["max_concurrent_tabs"])
        form.addRow(_field_label("Max concurrent tabs (parallel queries)"), self.max_concurrent_spin)

        self.query_timeout_spin = QSpinBox()
        self.query_timeout_spin.setRange(10, 600)
        self.query_timeout_spin.setSuffix(" s")
        self.query_timeout_spin.setValue(self._cfg["query_timeout_seconds"])
        form.addRow(_field_label("Per-query timeout"), self.query_timeout_spin)

        layout.addLayout(form)
        self._form_widgets += [
            self.max_scrolls_spin, self.scroll_wait_spin, self.stale_rounds_spin,
            self.max_concurrent_spin, self.query_timeout_spin,
        ]
        return card

    def _build_browser_behavior_card(self) -> QWidget:
        """Headless / close-after-run -- these are two of the 11 synced
        fields (see scraper_settings._DEFAULT_SYNCED_CONFIG /
        GlobalScraperSettings columns), unlike the machine-local Chrome
        card below. They control run behaviour on whichever machine
        actually executes the search, so unlike the executable path /
        profile dir / debug port, they're meaningful to set centrally
        for "Default for Users" too -- shown for both scopes."""
        card, layout = _card("Browser Behavior")

        self.headless_check = QCheckBox("Run headless (no visible browser)")
        self.headless_check.setChecked(self._cfg["headless"])
        layout.addWidget(self.headless_check)

        self.close_after_check = QCheckBox("Close Chrome && tabs after each run")
        self.close_after_check.setChecked(self._cfg["close_after_run"])
        layout.addWidget(self.close_after_check)

        self._form_widgets += [self.headless_check, self.close_after_check]
        return card

    def _build_chrome_card(self) -> QWidget:
        card, layout = _card("Chrome / Browser (auto-detected, this machine)")

        form = QFormLayout()
        form.setSpacing(10)

        self.chrome_exe_edit = QLineEdit(self._cfg["chrome_executable"])
        self.chrome_exe_edit.setReadOnly(True)
        form.addRow(_field_label("Chrome executable"), self.chrome_exe_edit)

        self.profile_dir_edit = QLineEdit(self._cfg["user_data_dir"])
        self.profile_dir_edit.setReadOnly(True)
        form.addRow(_field_label("Chrome profile dir"), self.profile_dir_edit)

        self.debug_port_spin = QSpinBox()
        self.debug_port_spin.setRange(1024, 65535)
        self.debug_port_spin.setValue(self._cfg["chrome_debug_port"])
        form.addRow(_field_label("Debug port"), self.debug_port_spin)

        layout.addLayout(form)

        redetect_row = QHBoxLayout()
        self.redetect_btn = QPushButton(qta.icon('fa5s.sync', color=PALETTE['text_secondary']), " Re-detect")
        self.redetect_btn.setObjectName("OutlineBtn")
        self.redetect_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.redetect_btn.clicked.connect(self._on_redetect_chrome)
        redetect_row.addWidget(self.redetect_btn)
        redetect_row.addStretch()
        layout.addLayout(redetect_row)

        self._form_widgets += [
            self.chrome_exe_edit, self.profile_dir_edit, self.debug_port_spin, self.redetect_btn,
        ]
        return card

    def _build_enrichment_card(self) -> QWidget:
        card, layout = _card("Website / Email Enrichment")

        hint = QLabel(
            "For each lead, opens its Google Maps 'Website' link, then checks "
            "that site for a mailto: link or a visible email address (falling "
            "back to a Contact page if present). Enabled per-search from the "
            "New Search dialog; these two settings control how it behaves "
            "when it's on."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 11px; background: transparent;")
        layout.addWidget(hint)

        form = QFormLayout()
        form.setSpacing(10)

        self.email_concurrent_spin = QSpinBox()
        self.email_concurrent_spin.setRange(1, 50)
        self.email_concurrent_spin.setValue(self._cfg["email_max_concurrent_tabs"])
        form.addRow(_field_label("Max concurrent website visits"), self.email_concurrent_spin)

        self.website_timeout_spin = QSpinBox()
        self.website_timeout_spin.setRange(5, 120)
        self.website_timeout_spin.setSuffix(" s")
        self.website_timeout_spin.setValue(self._cfg["website_timeout_seconds"])
        form.addRow(_field_label("Per-website timeout"), self.website_timeout_spin)

        layout.addLayout(form)
        self._form_widgets += [self.email_concurrent_spin, self.website_timeout_spin]
        return card

    def _build_output_card(self) -> QWidget:
        card, layout = _card("Output / Parsing")
        form = QFormLayout()
        form.setSpacing(10)

        self.phone_region_combo = QComboBox()
        self.phone_region_combo.setEditable(True)
        self.phone_region_combo.addItems([
            "PH", "US", "IN", "GB", "AU", "CA", "SG", "MY", "ID", "TH",
            "VN", "AE", "SA", "NZ", "DE", "FR", "ES", "IT", "JP", "KR",
        ])
        self.phone_region_combo.setCurrentText(self._cfg["phone_default_region"])
        form.addRow(_field_label("Default phone region"), self.phone_region_combo)

        layout.addLayout(form)

        phone_hint = QLabel(
            "Used only for phone numbers written without a '+' country code. "
            "Numbers that already include '+1', '+91', etc. always parse "
            "correctly regardless of this setting."
        )
        phone_hint.setWordWrap(True)
        phone_hint.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 11px; background: transparent;")
        layout.addWidget(phone_hint)

        self._form_widgets.append(self.phone_region_combo)
        return card

    # -- load ---------------------------------------------------------

    def _set_form_enabled(self, enabled: bool):
        for w in self._form_widgets:
            w.setEnabled(enabled)

    def _start_load(self):
        self._generation += 1
        worker = _ScraperSettingsLoadWorker(self._generation, self._load_fn)
        worker.signals.finished.connect(self._on_load_finished)
        QThreadPool.globalInstance().start(worker)

    def _on_load_finished(self, generation: int, cfg, error):
        if generation != self._generation:
            return  # superseded by a newer load -- drop silently
        if cfg is not None:
            self._cfg.update({k: v for k, v in cfg.items() if k in self._cfg})
            self._status_lbl.hide()
        else:
            self._status_lbl.setText(
                f"Couldn't load current settings: {error}. Showing defaults -- "
                "Save will still attempt to write your changes."
            )
            self._status_lbl.setStyleSheet(f"color: {PALETTE['red']}; font-size: 11px; background: transparent;")
        self._apply_cfg_to_widgets()
        self._loaded = True
        self._set_form_enabled(True)

    def _apply_cfg_to_widgets(self):
        self.max_scrolls_spin.setValue(self._cfg["max_scrolls_per_query"])
        self.scroll_wait_spin.setValue(self._cfg["scroll_wait_ms"])
        self.stale_rounds_spin.setValue(self._cfg["stale_rounds_threshold"])
        self.max_concurrent_spin.setValue(self._cfg["max_concurrent_tabs"])
        self.query_timeout_spin.setValue(self._cfg["query_timeout_seconds"])
        self.email_concurrent_spin.setValue(self._cfg["email_max_concurrent_tabs"])
        self.website_timeout_spin.setValue(self._cfg["website_timeout_seconds"])
        self.phone_region_combo.setCurrentText(self._cfg["phone_default_region"])
        self.headless_check.setChecked(self._cfg["headless"])
        self.close_after_check.setChecked(self._cfg["close_after_run"])
        if self.include_chrome:
            self.chrome_exe_edit.setText(self._cfg["chrome_executable"])
            self.profile_dir_edit.setText(self._cfg["user_data_dir"])
            self.debug_port_spin.setValue(self._cfg["chrome_debug_port"])

    # -- actions --------------------------------------------------------

    def _on_redetect_chrome(self):
        fresh = redetect_chrome(dict(self._cfg))
        self._cfg.update(fresh)
        self.chrome_exe_edit.setText(self._cfg["chrome_executable"])
        self.profile_dir_edit.setText(self._cfg["user_data_dir"])
        self.debug_port_spin.setValue(self._cfg["chrome_debug_port"])

    def _on_save(self):
        if not self._loaded:
            return  # Enter/click fired before the initial load resolved

        synced = {
            "max_scrolls_per_query": self.max_scrolls_spin.value(),
            "scroll_wait_ms": self.scroll_wait_spin.value(),
            "stale_rounds_threshold": self.stale_rounds_spin.value(),
            "max_concurrent_tabs": self.max_concurrent_spin.value(),
            "query_timeout_seconds": self.query_timeout_spin.value(),
            "phone_default_region": self.phone_region_combo.currentText().strip().upper() or "PH",
            "email_max_concurrent_tabs": self.email_concurrent_spin.value(),
            "website_timeout_seconds": self.website_timeout_spin.value(),
            # enable_email_enrichment is chosen per-search in New Search,
            # but keep whatever was last loaded/saved here in case
            # anything reads it standalone.
            "enable_email_enrichment": self._cfg.get("enable_email_enrichment", False),
            "headless": self.headless_check.isChecked(),
            "close_after_run": self.close_after_check.isChecked(),
        }

        try:
            self._save_synced_fn(synced)
            if self.include_chrome:
                save_local_scraper_config({
                    "chrome_executable": self.chrome_exe_edit.text().strip(),
                    "user_data_dir": self.profile_dir_edit.text().strip(),
                    "chrome_debug_port": self.debug_port_spin.value(),
                })
        except (ApiError, OSError) as e:
            InfoDialog.show(self, "Save failed", str(e), icon_name='fa5s.times-circle', success=False)
            return

        self._cfg.update(synced)
        if self.include_chrome:
            self._cfg["chrome_executable"] = self.chrome_exe_edit.text().strip()
            self._cfg["user_data_dir"] = self.profile_dir_edit.text().strip()
            self._cfg["chrome_debug_port"] = self.debug_port_spin.value()

        InfoDialog.show(
            self, "Saved",
            "Scraper settings saved. New searches will use these values."
            if self.scope == "personal" else
            "Default settings saved. Every non-admin account's next search will use these values.",
        )


class SettingsPage(QScrollArea):
    """
    referral_code -- this account's own shareable code.
    user_email    -- used only to count how many other accounts were
                      referred by this one (data.db lookup); the
                      count is read fresh each time the page is shown, not
                      cached, so it stays correct without extra wiring.

    theme_toggle_requested -- emitted after THEME_STATE["mode"] has already
                      been flipped; Dashboard connects this the same way it
                      connects UserAccountBox.logout_requested, calling its
                      own handle_theme_toggle() to apply the new palette and
                      rebuild the sidebar/page stack in place -- the window
                      itself never closes or reopens.
    """
    theme_toggle_requested = Signal()

    def __init__(self, referral_code: str, user_email: str = None, parent=None, user_role: str = "user"):
        super().__init__(parent)
        self.setObjectName("DashScroll")
        self.setWidgetResizable(True)
        self.referral_code = referral_code
        self.user_email = user_email
        self.user_role = user_role

        content = QWidget()
        outer = QVBoxLayout(content)
        outer.setContentsMargins(20, 16, 20, 20)
        outer.setSpacing(14)

        header_col = QVBoxLayout()
        header_col.setSpacing(2)
        title_lbl = QLabel("Settings")
        title_lbl.setObjectName("HeaderTitle")
        header_col.addWidget(title_lbl)
        sub_lbl = QLabel(
            "Manage your account." if self.user_role == "admin"
            else "Manage your account and referral program."
        )
        sub_lbl.setObjectName("HeaderSub")
        header_col.addWidget(sub_lbl)
        outer.addLayout(header_col)

        if self.user_role == "admin":
            outer.addLayout(self._build_admin_tabbar())
            outer.addWidget(self._build_admin_stack())
        else:
            outer.addWidget(self._build_theme_card())
            outer.addWidget(self._build_referral_card())
            outer.addStretch()

        self.setWidget(content)

    def _build_theme_card(self) -> QWidget:
        card, layout = _card("Appearance")

        row = QHBoxLayout()
        label = QLabel("Theme")
        label.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 12px; background: transparent;")
        row.addWidget(label)
        row.addStretch()

        self.theme_toggle_btn = QPushButton()
        self.theme_toggle_btn.setObjectName("OutlineBtn")
        self.theme_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.theme_toggle_btn.clicked.connect(self._on_theme_toggle_clicked)
        self._refresh_theme_toggle_btn()
        row.addWidget(self.theme_toggle_btn)

        layout.addLayout(row)
        return card

    def _refresh_theme_toggle_btn(self):
        is_dark = THEME_STATE["mode"] == "dark"
        icon_name = "fa5s.moon" if is_dark else "fa5s.sun"
        label_text = "Dark" if is_dark else "Light"
        self.theme_toggle_btn.setIcon(qta.icon(icon_name, color=PALETTE["text_secondary"]))
        self.theme_toggle_btn.setText(f" {label_text} mode")

    def _on_theme_toggle_clicked(self):
        """Flip THEME_STATE, then let Dashboard do the actual rebuild
        (see class docstring) -- this widget gets torn down and freshly
        reconstructed as part of that rebuild, so it doesn't need to
        re-render itself.

        The rebuild happens synchronously on this same click, so without
        feedback the app would just look frozen for a moment. Swap in a
        busy cursor right away and force a repaint (processEvents) so it's
        actually visible before the (blocking) rebuild starts; Dashboard.
        handle_theme_toggle() restores the normal cursor once it's done."""
        THEME_STATE["mode"] = "light" if THEME_STATE["mode"] == "dark" else "dark"
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()
        self.theme_toggle_requested.emit()

    def _build_admin_tabbar(self) -> QHBoxLayout:
        """
        Same pill/underline tab pattern billing.py uses for its sub-tabs
        (QPushButton with objectName BillingTabActive/BillingTabItem, swapped
        on click). Add more entries here + a matching page in
        _build_admin_stack() to grow this the same way billing.py's four
        sub-tabs work.
        """
        tab_bar = QHBoxLayout()
        tab_bar.setSpacing(4)
        self.admin_tab_buttons = {}
        tabs = [
            ("fa5s.user-cog", "General"),
            ("fa5s.spider", "Web Scraper"),
            ("fa5s.coins", "Pricing"),
            ("fa5s.tools", "Maintenance"),
        ]
        for icon_name, name in tabs:
            btn = QPushButton(qta.icon(icon_name, color=PALETTE["text_muted"]), " " + name)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _checked=False, n=name: self._set_admin_tab(n))
            self.admin_tab_buttons[name] = btn
            tab_bar.addWidget(btn)
        tab_bar.addStretch()
        return tab_bar

    def _build_admin_stack(self) -> QStackedWidget:
        self.admin_stack = QStackedWidget()
        general_page = QWidget()
        general_layout = QVBoxLayout(general_page)
        general_layout.setContentsMargins(0, 12, 0, 0)
        general_layout.setSpacing(14)
        general_layout.addWidget(self._build_theme_card())
        placeholder = QLabel("Other general admin settings go here.")
        placeholder.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 12px;")
        general_layout.addWidget(placeholder)
        general_layout.addStretch()

        self.admin_pages = {
            "General": general_page,
            "Web Scraper": self._build_scraper_settings_page(),
            "Pricing": PricingTab(),
            "Maintenance": MaintenanceTab(),
        }
        for page in self.admin_pages.values():
            self.admin_stack.addWidget(page)
        self._set_admin_tab("General")
        return self.admin_stack

    def _set_admin_tab(self, name: str):
        self.admin_stack.setCurrentWidget(self.admin_pages[name])
        icon_map = {"General": "fa5s.user-cog", "Web Scraper": "fa5s.spider", "Pricing": "fa5s.coins", "Maintenance": "fa5s.tools"}
        for btn_name, btn in self.admin_tab_buttons.items():
            is_active = btn_name == name
            btn.setObjectName("BillingTabActive" if is_active else "BillingTabItem")
            btn.setIcon(qta.icon(icon_map[btn_name], color=PALETTE["red"] if is_active else PALETTE["text_muted"]))
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    # -- Web Scraper (admin) --------------------------------------------

    def _build_scraper_settings_page(self) -> QWidget:
        """Two sub-tabs, same pill/underline pattern _build_admin_tabbar()
        and billing.py's subtabs use, each backed by its own
        _ScraperSettingsPanel instance (that class owns the actual
        load/save story for one scope -- see its docstring):

        - "My Settings" (scope="personal") -- this admin's own
          AdminScraperSettings row, via load_scraper_config() (merges in
          the machine-local Chrome fields too) /
          update_my_admin_scraper_settings(). What New Search uses when
          *this* admin runs a search.
        - "Default for Users" (scope="global") -- the single shared
          GlobalScraperSettings row every plain `user` account's New
          Search reads, via get_global_scraper_settings() /
          update_global_scraper_settings(). No Chrome card -- nothing in
          it is meaningful for someone else's machine.

        Both panels build immediately and kick off their own background
        load on construction, so switching tabs is instant after first
        open (no rebuild-on-click).
        """
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        tabbar_col = QVBoxLayout()
        tabbar_col.setSpacing(0)

        tab_bar = QHBoxLayout()
        tab_bar.setSpacing(4)
        self.scraper_subtab_buttons = {}
        scraper_subtabs = [
            ("fa5s.user", "My Settings"),
            ("fa5s.users", "Default for Users"),
        ]
        for icon_name, name in scraper_subtabs:
            btn = QPushButton(qta.icon(icon_name, color=PALETTE['text_muted']), " " + name)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _checked=False, n=name: self._set_scraper_subtab(n))
            self.scraper_subtab_buttons[name] = btn
            tab_bar.addWidget(btn)
        tab_bar.addStretch()
        tabbar_col.addLayout(tab_bar)

        divider = QFrame()
        divider.setStyleSheet(f"background-color: {PALETTE['divider']}; max-height: 1px; min-height: 1px;")
        tabbar_col.addWidget(divider)

        outer.addLayout(tabbar_col)

        self.scraper_stack = QStackedWidget()
        self.scraper_subtab_pages = {
            "My Settings": _ScraperSettingsPanel(
                "personal", load_scraper_config, update_my_admin_scraper_settings,
            ),
            "Default for Users": _ScraperSettingsPanel(
                "global", get_global_scraper_settings, update_global_scraper_settings,
            ),
        }
        for subpage in self.scraper_subtab_pages.values():
            self.scraper_stack.addWidget(subpage)
        outer.addWidget(self.scraper_stack)

        self._set_scraper_subtab("My Settings")
        return page

    def _set_scraper_subtab(self, name: str):
        self.scraper_stack.setCurrentWidget(self.scraper_subtab_pages[name])
        icon_map = {"My Settings": "fa5s.user", "Default for Users": "fa5s.users"}
        for btn_name, btn in self.scraper_subtab_buttons.items():
            is_active = btn_name == name
            btn.setObjectName("BillingTabActive" if is_active else "BillingTabItem")
            btn.setIcon(qta.icon(icon_map[btn_name], color=PALETTE["red"] if is_active else PALETTE["text_muted"]))
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _build_referral_card(self) -> QWidget:
        card, layout = _card()

        head_row = QHBoxLayout()
        icon_lbl = QLabel()
        icon_lbl.setStyleSheet("background: transparent;")
        icon_lbl.setPixmap(qta.icon('fa5s.gift', color=PALETTE["yellow"]).pixmap(20, 20))
        head_row.addWidget(icon_lbl)
        head_title = QLabel("Refer a Friend")
        head_title.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 15px; font-weight: 600; background: transparent;")
        head_row.addWidget(head_title)
        head_row.addStretch()
        layout.addLayout(head_row)

        desc = QLabel(
            "Share your code -- you'll both earn credits once your friend "
            "completes their first search."
            if REFERRAL_PROGRAM_ENABLED else
            "Referral rewards are currently disabled. Your code is saved "
            "and ready for when the program goes live."
        )
        
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {PALETTE['text_muted']}; font-size: 11px; background: transparent;")
        layout.addWidget(desc)

        layout.addSpacing(4)
        code_lbl = QLabel("Your Referral Code")
        code_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 10px; background: transparent;")
        layout.addWidget(code_lbl)
        layout.addWidget(build_referral_copy_field(self.referral_code))

        layout.addSpacing(10)
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet(f"background-color: {PALETTE['divider']}; max-height: 1px; min-height: 1px;")
        layout.addWidget(divider)
        layout.addSpacing(10)

        # GET /me/referrals + GET /me/referral-credits-earned -- both
        # already user-scoped server-side via the bearer token, so there's
        # no need to look self up by email first the way the old
        # get_user_by_email() + list_users() + filter did (see PROGRESS.md
        # Phase 5). Real total actually paid out via try_reward_referral(),
        # not a guess multiplying friends_joined by a flat constant -- that
        # overcounted for anyone still PENDING and used the wrong
        # per-reward value (see PROGRESS_REWARDS.md Phase 5).
        friends_joined = 0
        credits_earned = 0
        if self.user_email:
            try:
                friends_joined = len(list_my_referrals())
                credits_earned = get_my_referral_credits_earned()
            except ApiError:
                # Offline/server down -- show zeros rather than erroring
                # out of Settings entirely; the page still renders.
                pass

        stats_row = QHBoxLayout()
        stats_row.addWidget(self._stat_block("Friends Joined", str(friends_joined)))
        stats_row.addWidget(self._stat_block(
            "Credits Earned", f"{credits_earned:,}"
        ))
        layout.addLayout(stats_row)

        return card

    @staticmethod
    def _stat_block(label: str, value: str) -> QWidget:
        wrap = QWidget()
        wrap.setStyleSheet("background: transparent;")
        col = QVBoxLayout(wrap)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        value_lbl = QLabel(value)
        value_lbl.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 18px; font-weight: bold; background: transparent;")
        col.addWidget(value_lbl)
        label_lbl = QLabel(label)
        label_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 10px; background: transparent;")
        col.addWidget(label_lbl)
        return wrap