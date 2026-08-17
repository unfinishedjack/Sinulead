"""
rewards_page.py

RewardsPage: the "Rewards" nav tab -- admin-only. KPI stat cards (Total /
Active / Scheduled / Inactive / Total Claims), pill-tab + search + type/
status filters, and a table of every reward (name+description, type badge,
credit value, status pill, start/end date, claims, actions), plus a
"+ Create Reward" button that opens RewardDialog for add/edit.

Like packages_page.py / users_page.py, Add/Edit uses the same themed
modal (dialogs.ModernDialog) as every other admin form in this app
(Packages, Users, Payment Methods) -- if you want the slide-in side
panel shown in a reference design instead of a centered modal, that's a
bigger change (a QSplitter/side-QWidget that opens next to the table)
and isn't done here.

Depends on: config, core.api_client (Reward CRUD: list_rewards,
admin_create_reward, admin_update_reward, admin_delete_reward -- see
PROGRESS_REWARDS.md Phase 2 and PROGRESS.md Phase 5; used to hit
data.db directly), dialogs (ModernDialog, InfoDialog, ConfirmDialog,
labeled_field).
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QFrame, QScrollArea, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QComboBox, QTextEdit, QDialog,
)
from PySide6.QtCore import Qt, QSize
import qtawesome as qta

from core import config
from core.config import PALETTE
from core.api_client import (
    list_rewards, admin_create_reward, admin_update_reward, admin_delete_reward, ApiError,
)
from ui.dialogs.dialogs import ModernDialog, InfoDialog, ConfirmDialog, labeled_field
from ui.components.widgets import StatCard, pill as _shared_pill, SearchLineEdit, Toast

_REWARD_TYPES = ["Welcome", "Daily", "Referral", "Usage", "Milestone", "Engagement", "Special"]
_REWARD_STATUSES = ["Active", "Scheduled", "Inactive"]

# Mirrors the ProgressKey enum in the schema -- what actually triggers/
# tracks progress toward a reward. This is intentionally separate from
# _REWARD_TYPES above: type is a UI/filter label ("Referral"), progress_key
# is what the backend counts against (ACCOUNT_CREATED, REFERRAL, etc.).
_PROGRESS_KEYS = [
    "ACCOUNT_CREATED", "LOGIN", "REFERRAL", "PROFILE_COMPLETED",
    "SEARCH", "EXPORT", "CONTACT_US", "PURCHASE_CREDITS", "SPEND_CREDITS",
    "UNLOCK_CONTACT", "CUSTOM",
]

# Suggested progress_key per reward type -- narrows the dropdown to a
# sensible default when the admin picks a type, without hard-locking it
# (CUSTOM is always available as an escape hatch for one-off quests).
_SUGGESTED_KEYS_BY_TYPE = {
    "Welcome": ["ACCOUNT_CREATED", "PROFILE_COMPLETED", "CUSTOM"],
    "Daily": ["LOGIN"],
    "Referral": ["REFERRAL"],
    "Usage": ["SEARCH", "EXPORT"],
    "Milestone": ["UNLOCK_CONTACT", "SEARCH", "EXPORT", "SPEND_CREDITS", "PURCHASE_CREDITS"],
    "Engagement": ["CONTACT_US", "CUSTOM"],
    "Special": ["CUSTOM"],
}

_PROGRESS_TYPES = ["COUNT", "STREAK"]
_RESET_INTERVALS = ["NONE", "DAILY", "WEEKLY", "MONTHLY", "YEARLY"]

def _type_colors() -> dict:
    """Looked up fresh on every call so it tracks theme switches -- same
    pattern as transactions._status_colors() / users_page._status_colors().
    "Milestone" intentionally keeps its hardcoded brand/tier color
    (#e879f9) untouched per the README -- it's not a theme color."""
    return {
        "Welcome": (PALETTE["blue"], config.rgba_from_hex(PALETTE["blue"], 0.15)),
        "Daily": (PALETTE["blue"], config.rgba_from_hex(PALETTE["blue"], 0.15)),
        "Referral": (PALETTE["purple"], config.rgba_from_hex(PALETTE["purple"], 0.15)),
        "Usage": (PALETTE["red"], config.rgba_from_hex(PALETTE["red"], 0.15)),
        "Milestone": ("#e879f9", "rgba(232,121,249,0.15)"),
        "Engagement": (PALETTE["green"], config.rgba_from_hex(PALETTE["green"], 0.15)),
        "Special": (PALETTE["yellow"], config.rgba_from_hex(PALETTE["yellow"], 0.15)),
    }


def _status_colors() -> dict:
    """Looked up fresh on every call so it tracks theme switches."""
    return {
        "Active": (PALETTE["green_solid"], config.rgba_from_hex(PALETTE["green"], 0.15)),
        "Scheduled": (PALETTE["yellow_solid"], config.rgba_from_hex(PALETTE["yellow"], 0.15)),
        "Inactive": (PALETTE["red_solid"], config.rgba_from_hex(PALETTE["red"], 0.15)),
    }
_TYPE_DEFAULT_ICON = {
    "Welcome": "fa5s.gift", "Daily": "fa5s.calendar-check", "Referral": "fa5s.user-friends",
    "Usage": "fa5s.search", "Milestone": "fa5s.lock", "Engagement": "fa5s.star",
    "Special": "fa5s.trophy",
}


def _pill(text: str, accent: str, bg: str) -> QWidget:
    """Thin wrapper over widgets.pill() (see PROGRESS.md, Phase 1e)."""
    return _shared_pill(text, accent, bg)


def _type_pill(reward_type: str) -> QWidget:
    accent, bg = _type_colors().get(reward_type, (PALETTE["text_muted"], config.rgba_from_hex(PALETTE["text_muted"], 0.12)))
    return _pill(reward_type, accent, bg)


def _status_pill(status: str) -> QWidget:
    accent, bg = _status_colors().get(status, (PALETTE["text_muted"], config.rgba_from_hex(PALETTE["text_muted"], 0.12)))
    return _pill(status, accent, bg)


def _reward_name_cell(reward: dict) -> QWidget:
    wrap = QWidget()
    wrap.setStyleSheet("background: transparent;")
    row = QHBoxLayout(wrap)
    row.setContentsMargins(6, 4, 6, 4)
    row.setSpacing(10)

    accent, bg = _type_colors().get(reward["type"], (PALETTE["text_muted"], config.rgba_from_hex(PALETTE["text_muted"], 0.12)))
    icon_box = QFrame()
    icon_box.setFixedSize(32, 32)
    icon_box.setStyleSheet(f"background-color: {bg}; border-radius: 7px;")
    icon_box_layout = QHBoxLayout(icon_box)
    icon_box_layout.setContentsMargins(0, 0, 0, 0)
    icon_box_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
    icon_lbl = QLabel()
    icon_lbl.setStyleSheet("background: transparent;")
    icon_lbl.setPixmap(qta.icon(reward.get("icon", "fa5s.gift"), color=accent).pixmap(15, 15))
    icon_box_layout.addWidget(icon_lbl)
    row.addWidget(icon_box)

    text_col = QVBoxLayout()
    text_col.setSpacing(1)
    name_lbl = QLabel(reward["name"])
    name_lbl.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 12px; font-weight: 600; background: transparent;")
    text_col.addWidget(name_lbl)
    desc_lbl = QLabel(reward.get("description", ""))
    desc_lbl.setWordWrap(True)
    desc_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 10px; background: transparent;")
    text_col.addWidget(desc_lbl)
    row.addLayout(text_col, stretch=1)
    return wrap


class RewardDialog(ModernDialog):
    """Add/Edit Reward form. Same shape whether creating a new reward or
    editing an existing one -- pass an existing dict to pre-fill.

    `existing_rewards` is the current in-memory rewards list (RewardsPage.
    rewards), used only to populate the "Prerequisite Reward" dropdown so
    an admin can chain e.g. Refer 10 -> requires Refer 3 completed first.
    When editing, the reward being edited is excluded from its own
    prerequisite list (a reward can't require itself)."""
    def __init__(self, reward: dict = None, parent=None, existing_rewards: list = None):
        super().__init__(parent)
        # ModernDialog sets WA_DeleteOnClose for the (fire-and-forget) auth
        # dialogs it was originally written for. This dialog is used with
        # the classic exec()-then-read-back-widgets pattern (see ask()
        # below), and WA_DeleteOnClose + exec() means the C++ widgets get
        # torn down *inside* exec()'s nested event loop as soon as
        # accept()/reject() is called -- so get_values() below crashes
        # with "already deleted" as soon as exec() returns. Turn it back
        # off here; the dialog still gets garbage-collected normally once
        # ask() returns and drops its only reference to it.
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setFixedWidth(380)
        self._editing = reward is not None
        self._existing_rewards = [
            r for r in (existing_rewards or []) if not reward or r.get("id") != reward.get("id")
        ]

        layout = self.card_layout()
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)
        self.add_header(layout, "Edit Reward" if self._editing else "Create Reward")

        self.name_edit = QLineEdit(reward["name"] if reward else "")
        self.name_edit.setPlaceholderText("e.g. Welcome Bonus")
        layout.addLayout(labeled_field("Reward Name", self.name_edit))

        self.desc_edit = QTextEdit(reward.get("description", "") if reward else "")
        self.desc_edit.setObjectName("NoteEdit")
        self.desc_edit.setPlaceholderText("Describe the reward and how users can earn it...")
        self.desc_edit.setFixedHeight(60)
        layout.addLayout(labeled_field("Description", self.desc_edit))

        self.type_combo = QComboBox()
        self.type_combo.setObjectName("RowModeCombo")
        self.type_combo.addItems(_REWARD_TYPES)
        if reward:
            self.type_combo.setCurrentText(reward["type"])
        self.type_combo.currentTextChanged.connect(self._on_type_changed)
        layout.addLayout(labeled_field("Reward Type", self.type_combo))

        # --- Progress tracking fields ---
        # These are what actually make a quest functional (what to count,
        # how much is needed, how often it can re-trigger), as opposed to
        # the cosmetic fields above (name/type/value are just what the
        # user sees). See DBML: ProgressKey, ProgressType, ResetInterval.
        progress_row = QHBoxLayout()
        progress_row.setSpacing(10)

        self.progress_key_combo = QComboBox()
        self.progress_key_combo.setObjectName("RowModeCombo")
        self.progress_key_combo.addItems(_PROGRESS_KEYS)
        if reward and reward.get("progress_key"):
            self.progress_key_combo.setCurrentText(reward["progress_key"])
        progress_row.addLayout(labeled_field("Progress Key", self.progress_key_combo))

        self.progress_type_combo = QComboBox()
        self.progress_type_combo.setObjectName("RowModeCombo")
        self.progress_type_combo.addItems(_PROGRESS_TYPES)
        if reward and reward.get("progress_type"):
            self.progress_type_combo.setCurrentText(reward["progress_type"])
        progress_row.addLayout(labeled_field("Progress Type", self.progress_type_combo))
        layout.addLayout(progress_row)

        target_reset_row = QHBoxLayout()
        target_reset_row.setSpacing(10)

        self.target_value_edit = QLineEdit(str(reward.get("target_value", "")) if reward else "1")
        self.target_value_edit.setPlaceholderText("e.g. 3")
        target_reset_row.addLayout(labeled_field("Target Value", self.target_value_edit))

        self.reset_interval_combo = QComboBox()
        self.reset_interval_combo.setObjectName("RowModeCombo")
        self.reset_interval_combo.addItems(_RESET_INTERVALS)
        if reward and reward.get("reset_interval"):
            self.reset_interval_combo.setCurrentText(reward["reset_interval"])
        target_reset_row.addLayout(labeled_field("Reset Interval", self.reset_interval_combo))
        layout.addLayout(target_reset_row)

        self.prerequisite_combo = QComboBox()
        self.prerequisite_combo.setObjectName("RowModeCombo")
        self.prerequisite_combo.addItem("None", userData=None)
        for r in self._existing_rewards:
            self.prerequisite_combo.addItem(r["name"], userData=r["id"])
        if reward and reward.get("prerequisite_reward_id"):
            idx = self.prerequisite_combo.findData(reward["prerequisite_reward_id"])
            if idx >= 0:
                self.prerequisite_combo.setCurrentIndex(idx)
        layout.addLayout(labeled_field("Prerequisite Reward (Optional)", self.prerequisite_combo))

        if not reward:
            self._on_type_changed(self.type_combo.currentText())

        self.value_edit = QLineEdit(str(reward["value"]) if reward else "")
        self.value_edit.setPlaceholderText("e.g. 100")
        layout.addLayout(labeled_field("Credit Value", self.value_edit))

        self.status_combo = QComboBox()
        self.status_combo.setObjectName("RowModeCombo")
        self.status_combo.addItems(_REWARD_STATUSES)
        if reward:
            self.status_combo.setCurrentText(reward["status"])
        layout.addLayout(labeled_field("Status", self.status_combo))

        date_row = QHBoxLayout()
        date_row.setSpacing(10)
        self.start_date_edit = QLineEdit(reward.get("start_date", "") if reward else "")
        self.start_date_edit.setPlaceholderText("e.g. Jul 1, 2026")
        date_row.addLayout(labeled_field("Start Date", self.start_date_edit))
        self.end_date_edit = QLineEdit(reward.get("end_date", "") if reward else "")
        self.end_date_edit.setPlaceholderText("e.g. Dec 31, 2026")
        date_row.addLayout(labeled_field("End Date", self.end_date_edit))
        layout.addLayout(date_row)

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
        # autoDefault off on both buttons -- otherwise Qt's own built-in
        # "Enter clicks the default button" behavior fires *in addition
        # to* our keyPressEvent override below, double-calling
        # _on_confirm() (accept() twice -> the WA_DeleteOnClose dialog
        # gets torn down mid-second-call -> "already deleted" crash on
        # the second get_values()). Our override is now the only thing
        # that reacts to Enter/Return.
        cancel_btn.setAutoDefault(False)
        cancel_btn.setDefault(False)
        cancel_btn.clicked.connect(self.reject)
        confirm_btn = QPushButton(
            qta.icon('fa5s.check', color="#ffffff"), " Save Changes" if self._editing else " Save Reward"
        )
        confirm_btn.setObjectName("RedBtn")
        confirm_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        confirm_btn.setAutoDefault(False)
        confirm_btn.setDefault(False)
        confirm_btn.clicked.connect(self._on_confirm)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(confirm_btn)
        layout.addLayout(btn_row)

    def keyPressEvent(self, event):
        """Enter/Return submits the form (same as clicking Save), unless
        focus is in the multi-line Description box -- there, Enter should
        insert a newline like any normal text area instead of saving."""
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if isinstance(self.focusWidget(), QTextEdit):
                super().keyPressEvent(event)
                return
            self._on_confirm()
            return
        super().keyPressEvent(event)

    def _on_type_changed(self, reward_type: str):
        """New reward + type switched -> jump progress_key to the first
        suggested key for that type, so e.g. picking "Referral" defaults
        Progress Key to REFERRAL instead of leaving it on whatever it was."""
        suggestions = _SUGGESTED_KEYS_BY_TYPE.get(reward_type)
        if suggestions:
            self.progress_key_combo.setCurrentText(suggestions[0])

    def _on_confirm(self):
        name = self.name_edit.text().strip()
        desc = self.desc_edit.toPlainText().strip()
        value_txt = self.value_edit.text().strip().replace(",", "")
        start_date = self.start_date_edit.text().strip()
        end_date = self.end_date_edit.text().strip()
        target_txt = self.target_value_edit.text().strip()

        if not name:
            return self._error("Reward name is required.")
        if not desc:
            return self._error("Description is required.")
        if not value_txt.isdigit() or int(value_txt) <= 0:
            return self._error("Credit value must be a whole number greater than 0.")
        if not target_txt.isdigit() or int(target_txt) <= 0:
            return self._error("Target value must be a whole number greater than 0.")
        if not start_date:
            return self._error("Start date is required.")
        if not end_date:
            return self._error("End date is required.")

        self.accept()

    def _error(self, message: str):
        self.error_lbl.setText(message)
        self.error_lbl.show()

    def get_values(self) -> dict:
        reward_type = self.type_combo.currentText()
        return {
            "name": self.name_edit.text().strip(),
            "description": self.desc_edit.toPlainText().strip(),
            "type": reward_type,
            "value": int(self.value_edit.text().strip().replace(",", "")),
            "status": self.status_combo.currentText(),
            "start_date": self.start_date_edit.text().strip(),
            "end_date": self.end_date_edit.text().strip(),
            "icon": _TYPE_DEFAULT_ICON.get(reward_type, "fa5s.gift"),
            # Progress tracking -- what actually drives completion.
            "progress_key": self.progress_key_combo.currentText(),
            "progress_type": self.progress_type_combo.currentText(),
            "target_value": int(self.target_value_edit.text().strip()),
            "reset_interval": self.reset_interval_combo.currentText(),
            "prerequisite_reward_id": self.prerequisite_combo.currentData(),
        }

    @staticmethod
    def ask(parent, reward: dict = None, existing_rewards: list = None):
        dlg = RewardDialog(reward, parent, existing_rewards=existing_rewards)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            return dlg.get_values()
        return None


class RewardsPage(QScrollArea):
    """
    The "Rewards" nav tab -- admin-only. Owns:
      - self.rewards -- loaded from api_client.list_rewards() on init and
        after every mutation. Create/Edit/Duplicate/Delete all write
        through the server's `rewards` table via api_client, then reload
        this list -- no more in-memory-only state (see
        PROGRESS_REWARDS.md Phase 2 and PROGRESS.md Phase 5).
      - self.status_filter / self.search_text / self.type_filter -- current
        pill-tab + search box + type dropdown state, recombined by
        apply_filter() into self.current_data.
      - pagination state (page_size / current_page), same pattern as
        exports.ExportsPage.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DashScroll")
        self.setWidgetResizable(True)

        try:
            self.rewards = list_rewards()
        except ApiError:
            # Offline/server down -- start empty rather than erroring out
            # of RewardsPage construction; the table just shows nothing
            # until the next successful reload.
            self.rewards = []
        self.status_filter = "All Rewards"
        self.type_filter = "All Types"
        self.search_text = ""
        self.current_data = list(self.rewards)
        self.page_size = 10
        self.current_page = 1

        content = QWidget()
        outer = QVBoxLayout(content)
        outer.setContentsMargins(20, 16, 20, 20)
        outer.setSpacing(14)

        # --- Header ---
        header_row = QHBoxLayout()
        header_col = QVBoxLayout()
        header_col.setSpacing(2)
        title_lbl = QLabel("Rewards Management")
        title_lbl.setObjectName("HeaderTitle")
        header_col.addWidget(title_lbl)
        sub_lbl = QLabel("Create and manage rewards for user achievements and activities.")
        sub_lbl.setObjectName("HeaderSub")
        header_col.addWidget(sub_lbl)
        header_row.addLayout(header_col)
        header_row.addStretch()

        create_btn = QPushButton(qta.icon('fa5s.plus', color="#ffffff"), " Create Reward")
        create_btn.setObjectName("RedBtn")
        create_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        create_btn.clicked.connect(self._create_reward)
        header_row.addWidget(create_btn)
        outer.addLayout(header_row)

        # --- Stat cards ---
        self.stats_row = QHBoxLayout()
        self.stats_row.setSpacing(14)
        outer.addLayout(self.stats_row)

        # --- Pill tab bar ---
        tab_bar = QHBoxLayout()
        tab_bar.setSpacing(4)
        self.tab_buttons = {}
        for name in ("All Rewards", "Active", "Scheduled", "Inactive"):
            btn = QPushButton(name)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _checked=False, n=name: self.set_status_filter(n))
            self.tab_buttons[name] = btn
            tab_bar.addWidget(btn)
        tab_bar.addStretch()
        outer.addLayout(tab_bar)

        divider = QFrame()
        divider.setStyleSheet(f"background-color: {PALETTE['border']}; max-height: 1px; min-height: 1px;")
        outer.addWidget(divider)

        # --- Search + type filter + status filter row ---
        search_row = QHBoxLayout()
        search_row.setSpacing(8)

        self.search_edit = SearchLineEdit()
        self.search_edit.setPlaceholderText("Search rewards by name or description... (press Enter)")
        # Search only runs on explicit action -- Enter, or clicking the
        # magnifying-glass icon -- not on every keystroke.
        self.search_edit.search_triggered.connect(self._trigger_search)
        self.search_edit.returnPressed.connect(self._trigger_search)
        search_row.addWidget(self.search_edit, stretch=1)

        self.type_combo = QComboBox()
        self.type_combo.setObjectName("RowModeCombo")
        self.type_combo.addItems(["All Types"] + _REWARD_TYPES)
        self.type_combo.setMinimumWidth(130)
        self.type_combo.currentTextChanged.connect(self.on_type_changed)
        search_row.addWidget(self.type_combo)

        self.status_combo = QComboBox()
        self.status_combo.setObjectName("RowModeCombo")
        self.status_combo.addItems(["All Status"] + _REWARD_STATUSES)
        self.status_combo.setMinimumWidth(120)
        self.status_combo.currentTextChanged.connect(self.on_status_combo_changed)
        search_row.addWidget(self.status_combo)

        outer.addLayout(search_row)

        # --- Table ---
        self.columns = [
            {"label": "Reward"}, {"label": "Type"}, {"label": "Value"},
            {"label": "Status"}, {"label": "Start Date"}, {"label": "End Date"},
            {"label": "Claims"}, {"label": "Actions"},
        ]
        (self.COL_NAME, self.COL_TYPE, self.COL_VALUE, self.COL_STATUS,
         self.COL_START, self.COL_END, self.COL_CLAIMS, self.COL_ACTIONS) = range(8)

        self.table = QTableWidget()
        self.table.setColumnCount(len(self.columns))
        self.table.setHorizontalHeaderLabels([c["label"] for c in self.columns])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(52)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)

        header = self.table.horizontalHeader()
        for col_idx, width in [
            (self.COL_TYPE, 100), (self.COL_VALUE, 100), (self.COL_STATUS, 100),
            (self.COL_START, 110), (self.COL_END, 110), (self.COL_CLAIMS, 80),
            (self.COL_ACTIONS, 150),
        ]:
            header.setSectionResizeMode(col_idx, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(col_idx, width)
        header.setSectionResizeMode(self.COL_NAME, QHeaderView.ResizeMode.Stretch)
        header.setMinimumSectionSize(36)
        outer.addWidget(self.table)

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

        outer.addLayout(footer)

        self.setWidget(content)
        self.set_status_filter("All Rewards")
        self.refresh_stats()

    # ------------------------------------------------------------------
    # Stat cards (derived from self.rewards, so they stay correct after
    # Create/Edit/Delete)
    # ------------------------------------------------------------------
    def refresh_stats(self):
        while self.stats_row.count():
            item = self.stats_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        total = len(self.rewards)
        active = [r for r in self.rewards if r["status"] == "Active"]
        scheduled = [r for r in self.rewards if r["status"] == "Scheduled"]
        inactive = [r for r in self.rewards if r["status"] == "Inactive"]
        total_claims = sum(r.get("claims", 0) for r in self.rewards)
        active_pct = (len(active) / total * 100) if total else 0

        cards = [
            ("fa5s.gift", config.rgba_from_hex(PALETTE['blue'], 0.15), PALETTE['blue'], "Total Rewards",
             f"{total}", "Across all types", PALETTE['text_dim']),
            ("fa5s.check-circle", config.rgba_from_hex(PALETTE['green'], 0.15), PALETTE['green'], "Active Rewards",
             f"{len(active)}", f"{active_pct:.1f}% of total", PALETTE['green']),
            ("fa5s.clock", config.rgba_from_hex(PALETTE['yellow'], 0.15), PALETTE['yellow'], "Scheduled",
             f"{len(scheduled)}", "Upcoming rewards", PALETTE['text_dim']),
            ("fa5s.pause-circle", config.rgba_from_hex(PALETTE['purple'], 0.15), PALETTE['purple'], "Inactive",
             f"{len(inactive)}", "Paused rewards", PALETTE['text_dim']),
            ("fa5s.users", config.rgba_from_hex(PALETTE['red_solid'], 0.15), PALETTE['red'], "Total Claims",
             f"{total_claims:,}", "All-time claims", PALETTE['text_dim']),
        ]
        for icon, bg, color, label, value, sub_text, sub_color in cards:
            self.stats_row.addWidget(StatCard(icon, bg, color, label, value, sub_text, sub_color))

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

    def on_type_changed(self, value: str):
        self.type_filter = value
        self.current_page = 1
        self.apply_filter()

    def on_status_combo_changed(self, value: str):
        # The "All Status" dropdown mirrors the pill tabs -- keep them in
        # sync so either control can drive the filter.
        name = "All Rewards" if value == "All Status" else value
        self.set_status_filter(name)

    def apply_filter(self):
        filtered = list(self.rewards)

        if self.status_filter != "All Rewards":
            filtered = [r for r in filtered if r["status"] == self.status_filter]

        if self.type_filter and self.type_filter != "All Types":
            filtered = [r for r in filtered if r["type"] == self.type_filter]

        if self.search_text:
            filtered = [
                r for r in filtered
                if self.search_text in r["name"].lower()
                or self.search_text in r.get("description", "").lower()
            ]

        self.current_data = filtered
        self.populate_table()

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

        self.table.setRowCount(len(page_data))
        for row, reward in enumerate(page_data):
            self.table.setItem(row, self.COL_NAME, QTableWidgetItem())
            self.table.setCellWidget(row, self.COL_NAME, _reward_name_cell(reward))

            self.table.setItem(row, self.COL_TYPE, QTableWidgetItem())
            self.table.setCellWidget(row, self.COL_TYPE, _type_pill(reward["type"]))

            value_item = QTableWidgetItem(f'{reward["value"]:,} Credits')
            value_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, self.COL_VALUE, value_item)

            self.table.setItem(row, self.COL_STATUS, QTableWidgetItem())
            self.table.setCellWidget(row, self.COL_STATUS, _status_pill(reward["status"]))

            start_item = QTableWidgetItem(reward.get("start_date", ""))
            start_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, self.COL_START, start_item)

            end_item = QTableWidgetItem(reward.get("end_date", ""))
            end_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, self.COL_END, end_item)

            claims_item = QTableWidgetItem(f'{reward.get("claims", 0):,}')
            claims_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, self.COL_CLAIMS, claims_item)

            self.table.setItem(row, self.COL_ACTIONS, QTableWidgetItem())
            self.table.setCellWidget(row, self.COL_ACTIONS, self._actions_cell(reward))

        total_results = len(self.current_data)
        showing_from = start + 1 if page_data else 0
        showing_to = start + len(page_data)
        self.footer_label.setText(f"Showing {showing_from} to {showing_to} of {total_results} rewards")

        self.render_page_buttons()
        self.refresh_stats()

    def _actions_cell(self, reward: dict) -> QWidget:
        wrap = QWidget()
        wrap.setStyleSheet("background: transparent;")
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.setAlignment(Qt.AlignmentFlag.AlignCenter)

        def _icon_btn(icon_name, bg, hover_bg, on_click):
            btn = QPushButton()
            btn.setIcon(qta.icon(icon_name, color="#ffffff"))
            btn.setFixedSize(26, 26)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                f"QPushButton {{ background-color: {bg}; border: none; border-radius: 5px; }}"
                f"QPushButton:hover {{ background-color: {hover_bg}; }}"
            )
            btn.clicked.connect(on_click)
            return btn

        row.addWidget(_icon_btn('fa5s.pen', PALETTE['blue_solid'], PALETTE['blue'], lambda: self._edit_reward(reward)))
        row.addWidget(_icon_btn('fa5s.trash', PALETTE['red_solid'], PALETTE['accent_hover'], lambda: self._delete_reward(reward)))
        return wrap

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
            if p == current:
                btn.setStyleSheet(
                    f"QPushButton {{ background-color: {PALETTE['accent']}; border: none; border-radius: 5px; "
                    "color: white; font-weight: 600; padding: 0px; }"
                    f"QPushButton:hover {{ background-color: {PALETTE['accent_hover']}; }}"
                )
            else:
                btn.setStyleSheet(
                    f"QPushButton {{ background-color: transparent; border: 1px solid {PALETTE['border_strong']}; "
                    f"border-radius: 5px; color: {PALETTE['text_secondary']}; padding: 0px; }}"
                    f"QPushButton:hover {{ background-color: {PALETTE['bg_hover']}; }}"
                )
            btn.clicked.connect(lambda _checked=False, page=p: self.go_to_page(page))
            self.page_buttons_layout.addWidget(btn)

    # ------------------------------------------------------------------
    # Create / Edit / Duplicate / View / Delete
    # ------------------------------------------------------------------
    def _create_reward(self):
        values = RewardDialog.ask(self, existing_rewards=self.rewards)
        if values is None:
            return
        reward_id = f"rwd_{len(self.rewards) + 1}_{values['name'].lower().replace(' ', '_')}"
        try:
            admin_create_reward(reward_id, **values)
            self.rewards = list_rewards()
        except ApiError as exc:
            InfoDialog.show(self, "Couldn't create reward", str(exc), success=False)
            return
        self.apply_filter()
        Toast.show_toast(self, "Reward Created", f'"{values["name"]}" was created.')

    def _edit_reward(self, reward: dict):
        values = RewardDialog.ask(self, reward, existing_rewards=self.rewards)
        if values is None:
            return
        try:
            admin_update_reward(reward["id"], **values)
            self.rewards = list_rewards()
        except ApiError as exc:
            InfoDialog.show(self, "Couldn't update reward", str(exc), success=False)
            return
        self.apply_filter()
        Toast.show_toast(self, "Reward Updated", f'"{values["name"]}" was updated.')

    def _duplicate_reward(self, reward: dict):
        new_id = f"rwd_{len(self.rewards) + 1}_{reward['id']}"
        new_name = f'{reward["name"]} (Copy)'
        fields = {k: v for k, v in reward.items() if k not in ("id", "claims", "created_at", "updated_at")}
        fields["name"] = new_name
        try:
            admin_create_reward(new_id, **fields)
            self.rewards = list_rewards()
        except ApiError as exc:
            InfoDialog.show(self, "Couldn't duplicate reward", str(exc), success=False)
            return
        self.apply_filter()
        Toast.show_toast(self, "Reward Duplicated", f'"{new_name}" was created.')

    def _view_reward(self, reward: dict):
        details = (
            f'{reward.get("description", "")}\n\n'
            f'Type: {reward["type"]}\n'
            f'Value: {reward["value"]:,} Credits\n'
            f'Status: {reward["status"]}\n'
            f'Active: {reward.get("start_date", "")} - {reward.get("end_date", "")}\n'
            f'Claims: {reward.get("claims", 0):,}'
        )
        InfoDialog.show(self, reward["name"], details, icon_name='fa5s.gift', success=True)

    def _delete_reward(self, reward: dict):
        confirmed = ConfirmDialog.ask(
            self, "Delete Reward", f'Delete "{reward["name"]}"? This cannot be undone.',
            confirm_text="Delete Reward", icon_name='fa5s.trash', danger=True,
        )
        if not confirmed:
            return
        try:
            admin_delete_reward(reward["id"])
            self.rewards = list_rewards()
        except ApiError as exc:
            InfoDialog.show(self, "Couldn't delete reward", str(exc), success=False)
            return
        self.apply_filter()