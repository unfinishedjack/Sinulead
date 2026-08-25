"""
dashboard.py

Dashboard: the main application window shell. Owns the sidebar (nav +
credits box + user box) and a QStackedWidget that switches between the
actual page content:
  - search_leads.SearchLeadsPage ("Search Leads" nav item) -- the default
    landing page for non-"user" roles, so it's built immediately.
  - overview.OverviewPage ("Dashboard" nav item) -- only reachable for
    user_role == "user" (the default landing page for that role, so it's
    built immediately too); admins can't navigate into it at all, not
    just hidden.
  - every other page (Billing, Exports, Settings, Users, Transactions,
    Packages, Rewards, ...) is built right after, in the background, one
    page per event-loop tick -- see build_pages()/_ensure_page_built()/
    _schedule_background_prefetch(). This keeps the very first paint
    (startup, and a theme toggle's in-place rebuild) down to just the one
    page actually on screen, while still landing on an already-built page
    for every nav click shortly after -- instead of blocking the whole UI
    thread building all ~6-10 pages up front the way the old eager
    build_pages() used to.

This is the file to touch when adding new sidebar nav items or new pages
-- see build_sidebar() for the nav button list and build_pages() for how
each page's constructor gets registered.

Depends on: config, data, widgets, search_leads, overview.
"""

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QProgressBar, QFrame, QSplitter, QStackedWidget, QApplication,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
import qtawesome as qta

from core import config
from core.config import PALETTE, LOGO_PATH, CURRENT_USER_NAME, CURRENT_USER_EMAIL, INITIAL_CREDITS
from core.theme_state import THEME_STATE
from core.api_client import get_credits, get_me, claim_pending_bonus, ApiError
from ui.components.widgets import UserAccountBox
from ui.pages.search_leads import SearchLeadsPage
from ui.pages.overview import OverviewPage
from ui.pages.billing import BillingPage
from ui.pages.exports import ExportsPage
from ui.pages.settings_page import SettingsPage
from ui.pages.help_support_page import HelpSupportPage
from ui.pages.users_page import UsersPage
from ui.pages.transactions import TransactionsPage
from ui.pages.packages_page import PackagesPage
from ui.pages.earn_credits import EarnCreditsPage
from ui.pages.rewards_page import RewardsPage
from core.maintenance_state import get_maintenance_state
from ui.pages.maintenance_screen import MaintenanceScreen

class Dashboard(QMainWindow):
    def __init__(self, user_email: str = CURRENT_USER_EMAIL,
                 user_name: str = CURRENT_USER_NAME, user_role: str = "user"):
        super().__init__()
        self.setWindowIcon(qta.icon('fa5s.map-marker-alt', color=PALETTE["red_solid"]))
        self.resize(1600, 780)

        # Who's currently logged in (set by the Login screen in main.py).
        self.user_email = user_email
        self.user_name = user_name
        self.is_superadmin = (user_role == "superadmin")
        self.user_role = "admin" if self.is_superadmin else user_role

        # If maintenance mode is on and this isn't an admin, swap the whole
        # window for the maintenance screen and skip building the rest of
        # the UI. A QTimer re-checks the flag so a user who is already
        # sitting on this screen mid-session drops back to normal use the
        # moment an admin flips it off (and vice versa if flipped on).
        state = get_maintenance_state()
        if not (self.is_superadmin or (self.user_role == "admin" and state.get("allow_admin_access", True))):
            self._maintenance_check_timer = QTimer(self)
            self._maintenance_check_timer.timeout.connect(self._sync_maintenance_state)
            self._maintenance_check_timer.start(2000)
            if get_maintenance_state()["enabled"]:
                self.setWindowTitle("SinuLead - Maintenance")
                self.setCentralWidget(MaintenanceScreen())
                self.adjustSize()
                self._center_on_screen()
                return

        # Credits balance shown in the sidebar -- actually owned/spent by
        # SearchLeadsPage; kept mirrored here via its credits_changed signal.
        # Phase 5: no local DB access left here -- GET /me (id/
        # referral_code/pending_bonus_credits) and POST
        # /me/claim-pending-bonus (Phase 6, atomic server-side cash-in)
        # replace the old get_user_by_email()/update_user() pair. A
        # connection failure here means the account genuinely can't be
        # resolved (there's no local row to fall back to anymore), so it's
        # treated the same as "no user" rather than silently zeroing the
        # sidebar with no explanation.
        try:
            user = get_me()
        except ApiError:
            user = None

        if user:
            try:
                credits_balance = get_credits()["credits"]
            except ApiError:
                # Offline/server down after GET /me somehow still worked
                # (e.g. token expired in between) -- fall back to the
                # profile's own balance so the sidebar still shows
                # something rather than erroring out of dashboard
                # construction.
                credits_balance = user["credits"]

            self.credits_total = credits_balance
            self.credits_remaining = credits_balance
            self.referral_code = user["referral_code"]
            self.user_id = user["id"]

            if user["pending_bonus_credits"]:
                try:
                    claimed = claim_pending_bonus()
                    self.credits_total = claimed["credits"]
                    self.credits_remaining = claimed["credits"]
                except ApiError:
                    # Couldn't reach the server to cash it in this time --
                    # leave it pending; it'll be offered again next login
                    # rather than silently dropping the bonus.
                    pass
        else:
            self.credits_total = 0
            self.credits_remaining = 0
            self.referral_code = ""
            self.user_id = None

        # credits_total/credits_remaining are only ever initialized here,
        # in __init__ -- NOT in _build_ui() below. _build_ui() gets called
        # again on every theme toggle (see handle_theme_toggle()), and it
        # must NOT reset the user's credit balance or referral state each
        # time; only the widgets get rebuilt, never this session's data.
        self._build_ui()

    def _build_ui(self, restore_page_name: str = None):
        """Builds the sidebar + page stack against the currently active
        PALETTE. Called once from __init__, and called again (on the same
        Dashboard instance, same window) by handle_theme_toggle() after
        the palette has been swapped -- that's what lets a theme toggle
        restyle the app WITHOUT closing/reopening the window: only the
        page you're landing on (usually wherever you already were) gets
        freshly constructed here, so it naturally picks up whatever
        PALETTE currently holds -- no live-recolor plumbing needed, and no
        need to rebuild every other page you aren't even looking at (see
        build_pages()/_ensure_page_built()).

        restore_page_name -- which page to land on. None means "use the
        normal first-launch default" (Overview for user role, else Search
        Leads). On a rebuild, handle_theme_toggle() passes in whatever
        page was on screen a moment ago so the toggle doesn't bounce the
        user back to a different tab.
        """
        # Window icon color depends on PALETTE too (differs between the
        # dark/light red_solid), so refresh it on every rebuild, not just
        # the first one.
        self.setWindowIcon(qta.icon('fa5s.map-marker-alt', color=PALETTE["red_solid"]))

        # On a rebuild (not the first call) there's an existing splitter
        # (sidebar + stack + every page) to tear down first. deleteLater()
        # on it cascades to every child widget it owns, including the old
        # SettingsPage -- so its old theme_toggle_requested connection
        # goes away with it; build_pages() below makes a fresh one.
        old_central = self.takeCentralWidget()
        if old_central is not None:
            old_central.deleteLater()

        self.setWindowTitle("SinuLead")

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(3)
        self.splitter.setChildrenCollapsible(False)
        self.setCentralWidget(self.splitter)

        self.stack = QStackedWidget()
        self.build_pages()

        sidebar = self.build_sidebar()

        self.splitter.addWidget(sidebar)
        self.splitter.addWidget(self.stack)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([190, 1410])

        # Land on whichever page was requested (a rebuild restoring the
        # pre-toggle tab); fall back to the normal first-launch default --
        # Search Leads, unless Overview exists for this user role. Checked
        # against self._page_builders (registered for the whole session),
        # NOT self.pages -- build_pages() just reset self.pages to empty,
        # since pages are now constructed lazily on first visit.
        target_name = restore_page_name if restore_page_name in self._page_builders else None
        if target_name is None:
            target_name = "Dashboard" if self.user_role == "user" else "Search Leads"

        self.set_active_nav(target_name)
        if target_name == "Dashboard":
            self.setWindowTitle("SinuLead - Dashboard")
        elif target_name == "Search Leads":
            self.setWindowTitle(f"SinuLead - {self.search_leads_page.window_title_suffix}")

        # The page above is built and on screen already -- now quietly
        # build every OTHER page this role can reach, one at a time in
        # the background, so a first click on any nav item is instant
        # instead of building on demand. See _schedule_background_prefetch().
        self._rebuild_generation = getattr(self, "_rebuild_generation", 0) + 1
        self._schedule_background_prefetch(self._rebuild_generation)

    def showEvent(self, event):
        """Centering before show() (what we tried previously) doesn't
        survive many Linux window managers (GNOME/Mutter and friends):
        they ignore move()/setGeometry() requests made before a window is
        first mapped and apply their own initial-placement policy instead,
        so the pre-show position gets silently overridden the moment
        show() actually maps the window. The WM only starts respecting
        move() calls once the window is truly on screen.

        So instead we let the WM place it however it wants, then correct
        the position on the very next event-loop tick via
        QTimer.singleShot(0, ...) -- by that point the window is mapped,
        so our move() is no longer competing with the WM's own placement
        and reliably wins. Guarded to run once so it doesn't fight the
        maintenance-mode resizes, which call _center_on_screen() directly.
        """
        super().showEvent(event)
        if not getattr(self, "_centered_once", False):
            self._centered_once = True
            QTimer.singleShot(0, self._center_on_screen)

    def _sync_maintenance_state(self):
        is_maintenance_screen = isinstance(self.centralWidget(), MaintenanceScreen)
        enabled = get_maintenance_state()["enabled"]
        if enabled and not is_maintenance_screen:
            self.setCentralWidget(MaintenanceScreen())
            self.setWindowTitle("SinuLead - Maintenance")
            self.adjustSize()
            self._center_on_screen()
        elif not enabled and is_maintenance_screen:
            self.setCentralWidget(self.splitter)
            self.setWindowTitle("SinuLead")
            self.resize(1600, 780)
            self._center_on_screen()

    def _center_on_screen(self):
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        frame = self.frameGeometry()
        frame.moveCenter(available.center())
        self.move(frame.topLeft())

    def build_pages(self):
        """Registers a zero-arg constructor for every page this user role
        can reach, in self._page_builders -- but builds NONE of them yet.
        _build_ui() builds just the landing page synchronously (instant
        first paint), then _schedule_background_prefetch() builds every
        other registered page in the background, one per event-loop tick,
        via _ensure_page_built().

        This is what keeps a theme toggle's rebuild from freezing the UI:
        the eager version of this method used to construct all ~6-10
        pages up front (tables, cards, and every qtawesome icon in them)
        in one single blocking call, even though only one page is ever
        visible at a time. Now only the landing page blocks; the rest
        trickle in afterward without ever locking up the event loop for
        more than one page's worth of work at a time.
        """
        self.pages = {}
        self._page_builders = {}

        self.search_leads_page = None

        def make_search_leads():
            page = SearchLeadsPage(
                user_role=self.user_role, user_id=self.user_id,
                credits_total=self.credits_total,
                credits_remaining=self.credits_remaining,
            )
            page.credits_changed.connect(self.on_credits_changed)
            page.active_search_changed.connect(
                lambda header: self.setWindowTitle(f"SinuLead - {header}")
            )
            self.search_leads_page = page
            return page

        self._page_builders["Search Leads"] = make_search_leads

        self.overview_page = None
        if self.user_role == "user":
            def make_overview():
                page = OverviewPage()
                self.overview_page = page
                return page
            self._page_builders["Dashboard"] = make_overview

        self.billing_page = None
        if self.user_role != "admin":
            def make_billing():
                page = BillingPage()
                self.billing_page = page
                return page
            self._page_builders["Billing"] = make_billing

        self.earn_credits_page = None
        if self.user_role != "admin":
            def make_earn_credits():
                page = EarnCreditsPage(user_id=self.user_id)
                page.credits_changed.connect(self.on_credits_changed)
                self.earn_credits_page = page
                return page
            self._page_builders["Earn Credits"] = make_earn_credits

        def make_exports():
            page = ExportsPage(user_role=self.user_role)
            self.exports_page = page
            return page
        self._page_builders["Exports"] = make_exports

        def make_settings():
            page = SettingsPage(self.referral_code, self.user_email, user_role=self.user_role)
            page.theme_toggle_requested.connect(self.handle_theme_toggle)
            self.settings_page = page
            return page
        self._page_builders["Settings"] = make_settings

        self.help_support_page = None
        if self.user_role != "admin":
            def make_help_support():
                page = HelpSupportPage(user_role=self.user_role)
                self.help_support_page = page
                return page
            self._page_builders["Help and Support"] = make_help_support

        self.users_page = None
        self.transactions_page = None
        self.packages_page = None
        self.rewards_page = None
        if self.user_role == "admin":
            def make_users():
                page = UsersPage(current_admin_email=self.user_email)
                self.users_page = page
                return page
            self._page_builders["Users"] = make_users

            def make_transactions():
                page = TransactionsPage()
                self.transactions_page = page
                return page
            self._page_builders["Transactions"] = make_transactions

            def make_packages():
                page = PackagesPage()
                self.packages_page = page
                return page
            self._page_builders["Packages"] = make_packages

            def make_rewards():
                page = RewardsPage()
                self.rewards_page = page
                return page
            self._page_builders["Rewards"] = make_rewards

    def _ensure_page_built(self, page_name: str):
        """Returns the widget for page_name, constructing it on first use
        via self._page_builders and caching it in self.pages. Safe to
        call repeatedly -- a page already built is just returned as-is,
        never rebuilt until the next full _build_ui() rebuild (theme
        toggle)."""
        page = self.pages.get(page_name)
        if page is not None:
            return page
        builder = self._page_builders.get(page_name)
        if builder is None:
            return None
        page = builder()
        self.stack.addWidget(page)
        self.pages[page_name] = page
        return page

    def _schedule_background_prefetch(self, generation: int):
        """Queues up every page this role can reach that ISN'T already
        built (i.e. every page except the one just landed on), and kicks
        off building them one at a time in the background via
        _prefetch_next_page().

        `generation` is a stamp of which _build_ui() call this belongs to
        -- if another rebuild happens (another theme toggle) before this
        queue finishes, _prefetch_next_page() sees the mismatch and quietly
        stops, instead of building pages into a splitter/stack that's
        already been torn down."""
        self._prefetch_queue = [
            name for name in self._page_builders if name not in self.pages
        ]
        if self._prefetch_queue:
            QTimer.singleShot(0, lambda: self._prefetch_next_page(generation))

    def _prefetch_next_page(self, generation: int):
        """Builds exactly one queued page, then -- if the queue isn't
        empty and no newer rebuild has started -- schedules itself again
        for the next event-loop tick. One page per tick keeps each chunk
        of work small enough that the UI (and any clicks the user makes
        in the meantime) stays responsive throughout, instead of freezing
        for the entire batch the way building everything up front used to.
        """
        if generation != getattr(self, "_rebuild_generation", None):
            return  # a newer rebuild superseded this queue; stop silently
        if not self._prefetch_queue:
            return
        name = self._prefetch_queue.pop(0)
        if name not in self.pages:  # a real nav click may have built it already
            self._ensure_page_built(name)
        if self._prefetch_queue:
            QTimer.singleShot(0, lambda: self._prefetch_next_page(generation))

        def on_credits_changed(self, remaining: int, total: int):
            self.credits_remaining = remaining
            self.credits_total = total
            # Phase 2: the spend is already persisted server-side by whichever
            # api_client.spend_credits() call in SearchLeadsPage triggered this
            # signal (see search_leads.py's on_unlock_row/on_bulk_unlock) --
            # `remaining` here is the balance the server itself returned, not
            # a locally-computed guess, so there's nothing left to write here.
            #
            # SearchLeadsPage keeps its own local credits_remaining/total
            # (used by on_unlock_row's affordability check) instead of reading
            # this Dashboard's copy -- so a balance change that originates
            # elsewhere (Earn Credits claim, admin grant, billing/purchase)
            # has to be pushed back into it explicitly, or unlocking stays
            # stuck comparing against a stale (e.g. pre-claim) balance until
            # the app is restarted and SearchLeadsPage is rebuilt from scratch.
            if self.search_leads_page is not None:
                self.search_leads_page.credits_remaining = remaining
                self.search_leads_page.credits_total = total
            self.update_credits_display()

    def set_active_nav(self, page_name: str):
        """Switches self.stack to the requested page AND moves the red
        active indicator to that page's nav button (unstyling whichever
        button was previously active). Builds the page if it isn't
        already built (see _ensure_page_built()) -- normally that's only
        the very first page landed on right after a rebuild; every other
        page gets built ahead of time by the background prefetch (see
        _schedule_background_prefetch()), so most clicks just switch to
        an already-built page instantly."""
        page = self._ensure_page_built(page_name)
        if page is not None:
            self.stack.setCurrentWidget(page)
            # Earn Credits' progress can change from actions taken on other
            # tabs (a search, an export, a Contact Us submission) while
            # this page sat cached in the background -- refresh it live on
            # every visit instead of only at first build/after a claim, so
            # a completed task shows as claimable without needing a fresh
            # login to force the whole app (and this page) to rebuild.
            if page_name == "Earn Credits" and hasattr(page, "refresh"):
                page.refresh()

        for name, btn in self.nav_buttons.items():
            is_active = name == page_name
            btn.setObjectName("NavActive" if is_active else "NavItem")
            btn.setIcon(qta.icon(self.nav_icon_names[name], color=PALETTE["red"] if is_active else PALETTE["text_muted"]))
            # QSS #NavActive/#NavItem only takes effect after a style refresh
            # since we changed objectName after the widget was already shown.
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setMinimumWidth(160)
        sidebar.setMaximumWidth(340)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(14, 14, 14, 14)

        title_icon = QLabel()
        title_icon.setStyleSheet("background: transparent;")
        title_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_pixmap = QPixmap(LOGO_PATH)
        if not logo_pixmap.isNull():
            # Your logo file, scaled to fit the sidebar header
            title_icon.setPixmap(
                logo_pixmap.scaledToHeight(112, Qt.TransformationMode.SmoothTransformation)
            )
        else:
            # Fallback placeholder icon if assets/logo.png isn't found
            title_icon.setPixmap(qta.icon('fa5s.map-marker-alt', color=PALETTE["red_solid"]).pixmap(48, 48))
        layout.addWidget(title_icon)
        layout.addSpacing(24)

        # --- Sidebar nav items ---
        # To add a new tab (e.g. "Billing"), add an entry here, then wire
        # its click handler the same way "Dashboard"/"Search Leads" are
        # wired below (self.stack.setCurrentWidget(...)).
        nav_items_top = [
            ("fa5s.th-large", "Dashboard", False),
            ("fa5s.search", "Search Leads", True),
            ("fa5s.file-export", "Exports", False),
        ]
        if self.user_role != "admin":
            nav_items_top.append(("fa5s.credit-card", "Billing", False))
            nav_items_top.append(("fa5s.gift", "Earn Credits", False))
        if self.user_role == "admin":
            nav_items_top.append(("fa5s.users", "Users", False))
            nav_items_top.append(("fa5s.exchange-alt", "Transactions", False))
            nav_items_top.append(("fa5s.box-open", "Packages", False))
            nav_items_top.append(("fa5s.trophy", "Rewards", False))

        # page_name -> QPushButton / icon name, so set_active_nav() can
        # restyle (and re-tint the icon glyph of) whichever button
        # corresponds to the page just switched to.
        self.nav_buttons = {}
        self.nav_icon_names = {
            "Dashboard": "fa5s.th-large", "Search Leads": "fa5s.search",
            "Billing": "fa5s.credit-card", "Earn Credits": "fa5s.gift", "Exports": "fa5s.file-export",
            "Settings": "fa5s.cog", "Users": "fa5s.users",
            "Transactions": "fa5s.exchange-alt", "Packages": "fa5s.box-open",
            "Rewards": "fa5s.trophy", "Help and Support": "fa5s.life-ring",
        }

        for icon_name, text, active in nav_items_top:
            # Initial active state: Dashboard if it exists (default landing
            # page), else Search Leads. Checked by role, not by whether
            # overview_page has been built yet -- pages are now lazy, so
            # it stays None until first visited even when this role has one.
            is_default_active = (
                text == "Dashboard" if self.user_role == "user" else text == "Search Leads"
            )
            color = PALETTE["red"] if is_default_active else PALETTE["text_muted"]
            btn = QPushButton(qta.icon(icon_name, color=color), " " + text)
            btn.setObjectName("NavActive" if is_default_active else "NavItem")
            if text == "Dashboard":
                if self.user_role == "user":
                    btn.clicked.connect(lambda: self.set_active_nav("Dashboard"))
                    self.nav_buttons["Dashboard"] = btn
                else:
                    btn.setEnabled(False)  # no Overview page for this role
            elif text == "Search Leads":
                btn.clicked.connect(lambda: self.set_active_nav("Search Leads"))
                self.nav_buttons["Search Leads"] = btn
            elif text == "Billing":
                btn.clicked.connect(lambda: self.set_active_nav("Billing"))
                self.nav_buttons["Billing"] = btn
            elif text == "Earn Credits":
                btn.clicked.connect(lambda: self.set_active_nav("Earn Credits"))
                self.nav_buttons["Earn Credits"] = btn
            elif text == "Exports":
                btn.clicked.connect(lambda: self.set_active_nav("Exports"))
                self.nav_buttons["Exports"] = btn
            elif text == "Users":   
                btn.clicked.connect(lambda: self.set_active_nav("Users"))
                self.nav_buttons["Users"] = btn
            elif text == "Transactions":
                btn.clicked.connect(lambda: self.set_active_nav("Transactions"))
                self.nav_buttons["Transactions"] = btn
            elif text == "Packages":
                btn.clicked.connect(lambda: self.set_active_nav("Packages"))
                self.nav_buttons["Packages"] = btn
            elif text == "Rewards":
                btn.clicked.connect(lambda: self.set_active_nav("Rewards"))
                self.nav_buttons["Rewards"] = btn
            layout.addWidget(btn)

        # Divider between the search-related nav items and the settings/support group
        divider = QFrame()
        divider.setObjectName("NavDivider")
        divider.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(divider)

        nav_items_bottom = [
            ("fa5s.cog", "Settings", False),
        ]
        if self.user_role != "admin":
            nav_items_bottom.append(("fa5s.life-ring", "Help and Support", False))
        for icon_name, text, active in nav_items_bottom:
            color = PALETTE["red"] if active else PALETTE["text_muted"]
            btn = QPushButton(qta.icon(icon_name, color=color), " " + text)
            btn.setObjectName("NavActive" if active else "NavItem")
            if text == "Settings":
                btn.clicked.connect(lambda: self.set_active_nav("Settings"))
                self.nav_buttons["Settings"] = btn
            elif text == "Help and Support":
                btn.clicked.connect(lambda: self.set_active_nav("Help and Support"))
                self.nav_buttons["Help and Support"] = btn
            layout.addWidget(btn)

        layout.addStretch()

        credits_box = QFrame()
        credits_box.setObjectName("CreditsBox")
        cb_layout = QVBoxLayout(credits_box)
        cb_label_row = QHBoxLayout()
        cb_icon = QLabel()
        cb_icon.setPixmap(qta.icon('fa5s.coins', color=PALETTE["yellow"]).pixmap(11, 11))
        cb_label_row.addWidget(cb_icon)
        cb_label = QLabel("Credits Remaining")
        cb_label.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 10px;")
        cb_label_row.addWidget(cb_label)
        cb_label_row.addStretch()
        self.credits_value_lbl = QLabel("Unlimited" if self.user_role == "admin" else f"{self.credits_remaining:,}")
        self.credits_value_lbl.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 16px; font-weight: bold;")
        self.credits_bar = QProgressBar()
        self.credits_bar.setValue(100)
        self.credits_bar.setTextVisible(False)
        cb_layout.addLayout(cb_label_row)
        cb_layout.addWidget(self.credits_value_lbl)
        cb_layout.addWidget(self.credits_bar)
        layout.addWidget(credits_box)

        layout.addSpacing(8)
        role_label = "Administrator" if self.user_role == "admin" else "Lead Generation Specialist"
        self.user_box = UserAccountBox(
            self.user_name, self.user_email, role=role_label, referral_code=self.referral_code,
            credits=self.credits_remaining, unlimited=(self.user_role == "admin"),
        )
        self.user_box.logout_requested.connect(self.handle_logout)
        layout.addWidget(self.user_box)

        return sidebar
    
    def update_credits_display(self):
        if self.user_role == "admin":
            self.credits_value_lbl.setText("Unlimited")
            self.credits_bar.setValue(100)
        else:
            self.credits_value_lbl.setText(f"{self.credits_remaining:,}")
            pct = int(self.credits_remaining / self.credits_total * 100) if self.credits_total else 0
            self.credits_bar.setValue(max(0, min(100, pct)))
        self.user_box.update_credits(self.credits_remaining, unlimited=(self.user_role == "admin"))

    def handle_logout(self):
        """Closes this dashboard window; main.py's loop notices the flag
        and re-shows the login screen."""
        self.logged_out = True
        self.close()

    def handle_theme_toggle(self):
        """Applies the palette that settings_page just flipped in
        THEME_STATE, then rebuilds this window's sidebar + page stack in
        place -- WITHOUT closing or reopening the window. Same window,
        same session, same nav tab the user was already on.

        This works because the page you land on is freshly constructed by
        _build_ui() (via build_pages()/_ensure_page_built()), so it never
        needs to "live recolor" itself against the new PALETTE -- it's
        simply rebuilt against it, same as any first-time page visit.
        Every other page gets rebuilt too, right after, in the background
        one at a time (see _schedule_background_prefetch()) -- so nav
        clicks are instant again shortly after the toggle, without ever
        blocking the UI for the whole batch at once."""
        # Remember what was on screen so the toggle doesn't bounce the
        # user to a different tab.
        active_page_name = None
        old_pages = getattr(self, "pages", None)
        if old_pages:
            current_widget = self.stack.currentWidget()
            for name, page in old_pages.items():
                if page is current_widget:
                    active_page_name = name
                    break

        # Flip the actual palette + app-wide QSS BEFORE rebuilding -- every
        # widget below reads PALETTE at construction time, so this has to
        # happen first or the rebuild would just reconstruct the old theme.
        config.set_mode(THEME_STATE["mode"])
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(config.qss())

        # settings_page already swapped in a wait cursor before this ran;
        # `finally` guarantees it's cleared even if the rebuild below
        # raises, so a broken rebuild can't leave the cursor stuck busy.
        try:
            self._build_ui(restore_page_name=active_page_name)
        finally:
            QApplication.restoreOverrideCursor()

    def closeEvent(self, event):
        """Make sure the maintenance-mode polling timer (if any) stops
        the moment this window closes -- for any reason, not just logout
        (the X button counts too). Without this it keeps firing in the
        background against a half-destroyed window and crashes the app."""
        timer = getattr(self, "_maintenance_check_timer", None)
        if timer is not None:
            timer.stop()
            timer.timeout.disconnect(self._sync_maintenance_state)
            timer.deleteLater()
            self._maintenance_check_timer = None
        super().closeEvent(event)