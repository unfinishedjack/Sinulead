"""
earn_credits.py

EarnCreditsPage: the "Earn Credits" nav tab. Shows a "Welcome Rewards" /
"Daily Rewards" / "Referral Rewards" / "Usage Rewards" checklist (each row
built by build_sections() from real api_client.list_rewards() + per-user
progress/claim state -- see PROGRESS_REWARDS.md Phase 3) plus a right-hand
column with a donut progress ring, a "How It Works" list, and a tips card.

Claiming a task now calls api_client.claim_reward(), which credits the
real `users.credits` balance server-side and records the claim in
`user_rewards` -- nothing here is in-memory-only anymore. After a
successful claim, EarnCreditsPage emits credits_changed(remaining, total)
the same way search_leads.py does, so Dashboard.on_credits_changed() keeps
the sidebar balance in sync.

Depends on: config, core.api_client (see PROGRESS.md Phase 5; used to hit
data.db directly).
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QProgressBar, QScrollArea, QPushButton, QGraphicsOpacityEffect,
)
from PySide6.QtCore import (
    Qt, QRectF, QTimer, QPropertyAnimation, QEasingCurve, Signal,
    QObject, QRunnable, QThreadPool,
)
from PySide6.QtGui import QPainter, QPen, QColor
import qtawesome as qta

from core import config
from core.config import PALETTE
from core.api_client import (
    get_my_reward, list_my_referrals, get_my_progress, list_rewards,
    get_reward, get_my_credits_earned, claim_reward, ApiError,
)
from ui.components.widgets import dash_card
from ui.pages.rewards_page import _type_colors


# Which reward "type" (rewards_page.py's _REWARD_TYPES) feeds which
# checklist section on this page. Only these six types are shown here --
# "Special" rewards exist in the admin Rewards tab but aren't surfaced
# as an earnable checklist item (yet).
_SECTION_TYPE_MAP = {
    "Welcome Rewards": "Welcome",
    "Daily Rewards": "Daily",
    "Referral Rewards": "Referral",
    "Usage Rewards": "Usage",
    "Milestone Rewards": "Milestone",
    "Engagement Rewards": "Engagement",
}


def _bar_color_by_section() -> dict:
    """Per-section accent for the progress bar, sourced live from the
    admin Rewards tab's own type colors (rewards_page._type_colors())
    instead of a second hardcoded copy here -- admin is the single
    source of truth for what color each reward type gets, so a color
    changed there (or a theme switch) shows up here automatically
    without the two ever drifting apart again."""
    type_colors = _type_colors()
    return {
        section_name: type_colors[reward_type][0]
        for section_name, reward_type in _SECTION_TYPE_MAP.items()
        if reward_type in type_colors
    }

# Display unit for a reward's progress bar, keyed off Reward.progress_key
# (see rewards_page.py's _PROGRESS_KEYS).
_PROGRESS_UNITS = {
    "LOGIN": "days", "REFERRAL": "friends", "SEARCH": "searches",
    "EXPORT": "exports", "PROFILE_COMPLETED": "fields",
    "PURCHASE_CREDITS": "purchases", "SPEND_CREDITS": "credits",
    "UNLOCK_CONTACT": "contacts", "CONTACT_US": "messages",
}


def _is_reward_claimed(user_id: int, reward_id: str) -> bool:
    try:
        ur = get_my_reward(reward_id)
    except ApiError:
        return False
    return bool(ur and ur["completed"] and ur["claim_count"] > 0)


def _referral_task_status(user_id: int, reward: dict) -> tuple:
    """Only rwd_refer_instant uses this -- it pays out automatically the
    moment try_reward_referral() fires server-side (see
    PROGRESS_REWARDS.md Phase 0/1), so there's no manual "Claim" step and
    this never returns "claimable". Progress is real REWARDED-status
    referral rows, not UserProgress. Milestone referral rewards (Refer
    3/10 Friends) go through _generic_task_status() instead, same as
    every other section -- see PROGRESS_REWARDS.md Phase 5 for why they
    can't share this function: they need an actual manual claim_reward()
    call to credit anything, and this status function never triggers
    one."""
    try:
        referrals = list_my_referrals()
    except ApiError:
        referrals = []
    rewarded = sum(1 for r in referrals if r["status"] == "REWARDED")
    target = reward.get("target_value", 1)
    current = min(rewarded, target)
    if rewarded >= target:
        return "claimed", current
    if rewarded > 0:
        return "in_progress", current
    return "locked", current


def _generic_task_status(user_id: int, reward: dict) -> tuple:
    """Non-referral rewards: driven by UserProgress (via
    get_my_progress) for the "how close are they" number, and
    UserReward (via _is_reward_claimed) for whether it's already been
    cashed in. A prerequisite reward gates this one to "locked" until
    it's claimed, regardless of this reward's own progress."""
    prereq_id = reward.get("prerequisite_reward_id")
    if prereq_id and not _is_reward_claimed(user_id, prereq_id):
        return "locked", 0

    progress_key = reward.get("progress_key")
    if not progress_key:
        return "locked", 0

    try:
        current = get_my_progress(progress_key, reward.get("progress_type", "COUNT"))
    except ApiError:
        current = 0
    target = reward.get("target_value", 1)

    if _is_reward_claimed(user_id, reward["id"]) and reward.get("reset_interval", "NONE") == "NONE":
        return "claimed", current
    if current >= target:
        return "claimable", current
    if current > 0:
        return "in_progress", current
    return "locked", current


def build_sections(user_id: int | None) -> dict:
    """Builds the same {section_name: [task_dict, ...]} shape the old
    hardcoded EARN_CREDITS_SECTIONS used, from list_rewards() plus this
    user's real progress/claim state. Rewards with no progress_key set
    can't be tracked yet -- an admin has to set one via rewards_page.py's
    RewardDialog first (Phase 2 landed that field) -- so they're left off
    this page instead of showing as a permanently-dead "locked" row.
    Returns all-empty sections (no crash) if user_id is None, e.g. the
    dashboard's user lookup failed, or if the server can't be reached."""
    sections = {name: [] for name in _SECTION_TYPE_MAP}
    if not user_id:
        return sections

    try:
        rewards = [r for r in list_rewards() if r["status"] == "Active" and r.get("progress_key")]
    except ApiError:
        return sections

    bar_colors = _bar_color_by_section()
    for section_name, reward_type in _SECTION_TYPE_MAP.items():
        bar_color = bar_colors.get(section_name)
        for reward in (r for r in rewards if r["type"] == reward_type):
            # Only the auto-paid instant bonus uses the referral-specific
            # status function -- the milestone rewards (Refer 3/10
            # Friends) go through the generic claim flow now that they
            # have real UserProgress to check (see PROGRESS_REWARDS.md
            # Phase 5; previously ALL progress_key=="REFERRAL" rewards
            # hit _referral_task_status, which is why "Refer 3 Friends"
            # could show "Claimed" the moment 3 friends joined without
            # ever actually crediting the 30 credits).
            if reward["id"] == "rwd_refer_instant":
                status, current = _referral_task_status(user_id, reward)
            else:
                status, current = _generic_task_status(user_id, reward)

            task = {
                "id": reward["id"],
                "icon": reward.get("icon", "fa5s.gift"),
                "title": reward["name"],
                "subtitle": reward.get("description", ""),
                "credits": reward["value"],
                "status": status,
            }
            target = reward.get("target_value", 1)
            if target > 1:
                # Display-clamp: `current` is a raw, ever-incrementing
                # counter (e.g. total contacts ever unlocked), which can
                # keep climbing past a completed reward's target -- 151
                # real unlocks against a target of 50 shouldn't render as
                # "151 / 50". Cap what's *shown* at the target; the real
                # counter in the DB is untouched, this only affects the
                # bar/label here.
                task["progress"] = (min(current, target), target)
                task["progress_unit"] = _PROGRESS_UNITS.get(reward["progress_key"], "")
            if bar_color:
                task["bar_color"] = bar_color

            if status == "claimed":
                try:
                    ur = get_my_reward(reward["id"])
                except ApiError:
                    ur = None
                if ur and ur.get("last_claimed_at"):
                    task["claimed_note"] = f'Claimed on {ur["last_claimed_at"][:10]}'

            prereq_id = reward.get("prerequisite_reward_id")
            if status == "locked" and prereq_id:
                prereq = get_reward(prereq_id)
                if prereq:
                    task["unlock_hint"] = f'Unlocks after you complete "{prereq["name"]}"'

            sections[section_name].append(task)

    for tasks in sections.values():
        tasks.sort(key=_task_sort_key)

    return sections


# Status priority for the sort below: whatever the user could still act
# on (claimable, then in_progress) stays pinned at the top of its
# section, locked comes next since it's at least a visible goal, and
# claimed sinks to the bottom -- newest claim first -- so a section with
# a lot of claim history doesn't bury this week's actionable rewards
# under it. See PROGRESS_REWARDS.md "give way to the new" note.
_STATUS_ORDER = {"claimable": 0, "in_progress": 1, "locked": 2, "claimed": 3}


def _task_sort_key(task: dict):
    rank = _STATUS_ORDER.get(task["status"], 99)
    if task["status"] != "claimed":
        return (rank, "")
    # claimed_note is "Claimed on YYYY-MM-DD" or absent; sort those dates
    # descending (newest first). A note-less claimed row (shouldn't
    # normally happen) sorts to the very end of the claimed group
    # instead of crashing, since "" inverts to "" and sorts last.
    note = task.get("claimed_note", "")
    date_str = note.replace("Claimed on ", "") if note else ""
    return (rank, _invert_date(date_str) if date_str else "~")


def _invert_date(date_str: str) -> str:
    """YYYY-MM-DD sorts ascending as a plain string, but we want newest
    claims first -- flipping each digit (9 - d) on a fixed-width string
    preserves lexicographic order while reversing it, without needing to
    parse the string into an actual date object."""
    return "".join(str(9 - int(ch)) if ch.isdigit() else ch for ch in date_str)


def _card(title: str = None):
    """Thin wrapper over widgets.dash_card() (see PROGRESS.md, Phase 1c)."""
    return dash_card(title)


def _status_color(task: dict) -> str:
    """Fallback accent color for a task row when it has no explicit
    'bar_color' of its own -- single source of truth for the icon chip,
    progress bar, and credits label so the left column speaks the same
    green/red/gray language as the progress ring and status pills."""
    status = task["status"]
    if status == "claimed":
        return PALETTE['green_solid']
    if status in ("in_progress", "claimable"):
        return PALETTE['red_solid']
    return PALETTE['text_muted']  # locked


def _task_accent(task: dict) -> str:
    """The accent color actually used to paint a task row. Sections carry
    their own 'bar_color' in data.py (purple for Referral Rewards, blue for
    Usage Rewards) so each category reads as visually distinct instead of
    every row defaulting to the same red/green/gray. Claimed tasks always
    render green regardless of bar_color, since "done" should look the
    same everywhere."""
    if task["status"] == "claimed":
        return PALETTE['green']
    return task.get("bar_color") or _status_color(task)


# Row accents come from PALETTE's "light" shades (blue/purple/green/
# yellow/red -- see rewards_page._type_colors()), which read fine as a
# ~15%-opacity pill fill but are too bright as a full solid button.
# PALETTE already defines a matching "_solid" (darker) shade for each
# of those -- reuse it here instead of inventing new button colors, so
# every section's Claim button gets the same treatment, not just one.
# Milestone's "#e879f9" isn't a PALETTE entry (it's rewards_page.py's
# hardcoded brand/tier color, kept intentionally distinct from the
# theme), so it has no "_solid" counterpart of its own -- falls back to
# purple_solid, the closest in-theme shade.
_CLAIM_BUTTON_COLOR_OVERRIDES = {
    PALETTE["blue"].lower(): PALETTE["blue_solid"],
    PALETTE["purple"].lower(): PALETTE["purple_solid"],
    PALETTE["green"].lower(): PALETTE["green_solid"],
    PALETTE["yellow"].lower(): PALETTE["yellow_solid"],
    PALETTE["red"].lower(): PALETTE["red_solid"],
    "#e879f9": PALETTE["purple_solid"],
}

_STATUS_PILL_WIDTH = 104


def _status_widget(task: dict, on_claim) -> QWidget:
    """Right-hand status control for a task row: a green 'Claimed' pill, a
    red/accent 'In Progress' pill (or a 'Claim' button if it's actually
    claimable right now), or a gray 'Locked' pill with a lock glyph and a
    tooltip explaining what unlocks it. All four are forced to the same
    fixed width so the column lines up regardless of label length, and
    frameShape is explicitly set to NoFrame so the OS style can't draw its
    own bevel underneath our QSS border (that extra native bevel is what
    was reading as a "double border" on the 'In Progress' pill, since it's
    the only variant using a border instead of a flat fill)."""
    status = task["status"]
    accent = _task_accent(task)

    if status == "claimed":
        pill = QFrame()
        pill.setFrameShape(QFrame.Shape.NoFrame)
        pill.setFixedWidth(_STATUS_PILL_WIDTH)
        pill.setStyleSheet(f"background-color: {config.rgba_from_hex(PALETTE['green'], 0.15)}; border: none; border-radius: 5px;")
        row = QHBoxLayout(pill)
        row.setContentsMargins(0, 5, 0, 5)
        row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl = QLabel("Claimed")
        lbl.setStyleSheet(f"color: {PALETTE['green']}; font-size: 12px; font-weight: 600; background: transparent;")
        row.addWidget(lbl)
        return pill

    if status == "claimable":
        btn = QPushButton("Claim")
        btn.setObjectName("RedBtn")
        # This button lives inside _task_row()'s `wrap`, which has its own
        # local setStyleSheet("background: transparent;"). That local
        # stylesheet on an ancestor breaks the normal objectName-based
        # #RedBtn cascade from the app-wide stylesheet -- same issue
        # search_leads.py's Search button hit (see its comment). Style it
        # directly off the theme palette instead of depending on the
        # cascade, so it's guaranteed to render red regardless of ancestor
        # styling.
        #
        # Color now follows the row's own accent (same `accent` used for
        # the icon chip, progress bar, and "+N Credits" label above)
        # instead of being hardcoded green -- so a purple Referral row
        # gets a purple Claim button, a blue Usage row gets a blue one,
        # a red Daily row gets a red one, etc. Hover just fades the same
        # accent color rather than swapping to a second named color,
        # since we don't have a lighter/darker variant defined for every
        # palette accent. The button itself renders the "_solid" (darker)
        # shade of that accent via _CLAIM_BUTTON_COLOR_OVERRIDES above --
        # the raw PALETTE light shades (and Milestone's brand color) are
        # tuned for a low-opacity pill fill, not a full solid button.
        button_color = _CLAIM_BUTTON_COLOR_OVERRIDES.get(accent.lower(), accent)
        btn.setStyleSheet(
            f"QPushButton#RedBtn {{ background-color: {button_color}; border: none; "
            f"border-radius: 5px; padding: 6px 14px; color: white; font-weight: 500; }}"
            f"QPushButton#RedBtn:hover {{ background-color: {config.rgba_from_hex(button_color, 0.8)}; }}"
        )
        btn.setFixedWidth(_STATUS_PILL_WIDTH)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(lambda checked=False, b=btn, tid=task["id"]: on_claim(tid, b))
        return btn

    if status == "in_progress":
        pill = QFrame()
        pill.setFrameShape(QFrame.Shape.NoFrame)
        pill.setFixedWidth(_STATUS_PILL_WIDTH)
        pill.setStyleSheet(f"background-color: transparent; border: 1px solid {accent}; border-radius: 5px;")
        row = QHBoxLayout(pill)
        row.setContentsMargins(0, 5, 0, 5)
        row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl = QLabel("In Progress")
        lbl.setStyleSheet(f"color: {accent}; font-size: 12px; font-weight: 600; background: transparent; border: none;")
        row.addWidget(lbl)
        return pill

    # locked
    pill = QFrame()
    pill.setFrameShape(QFrame.Shape.NoFrame)
    pill.setFixedWidth(_STATUS_PILL_WIDTH)
    pill.setStyleSheet(f"background-color: {config.rgba_from_hex(PALETTE['text_muted'], 0.12)}; border: none; border-radius: 5px;")
    hint = task.get("unlock_hint")
    if hint:
        pill.setToolTip(hint)
        pill.setCursor(Qt.CursorShape.WhatsThisCursor)
    row = QHBoxLayout(pill)
    row.setContentsMargins(0, 5, 0, 5)
    row.setSpacing(5)
    row.setAlignment(Qt.AlignmentFlag.AlignCenter)
    icon_lbl = QLabel()
    icon_lbl.setStyleSheet("background: transparent;")
    icon_lbl.setPixmap(qta.icon('fa5s.lock', color=PALETTE['text_muted']).pixmap(9, 9))
    row.addWidget(icon_lbl)
    lbl = QLabel("Locked")
    lbl.setStyleSheet(f"color: {PALETTE['text_muted']}; font-size: 12px; font-weight: 600; background: transparent;")
    row.addWidget(lbl)
    return pill

def _task_row(task: dict, on_claim) -> QWidget:
    wrap = QWidget()
    wrap.setStyleSheet("background: transparent;")
    outer = QVBoxLayout(wrap)
    outer.setContentsMargins(0, 8, 0, 8)
    outer.setSpacing(8)

    top = QHBoxLayout()
    top.setSpacing(12)

    accent = _task_accent(task)
    done = task["status"] == "claimed"
    icon_bg = config.rgba_from_hex(accent, 0.15)
    icon_color = accent
    icon_name = 'fa5s.check-circle' if done else task["icon"]

    icon_box = QFrame()
    icon_box.setFixedSize(38, 38)
    icon_box.setStyleSheet(f"background-color: {icon_bg}; border-radius: 19px;")
    icon_box_layout = QHBoxLayout(icon_box)
    icon_box_layout.setContentsMargins(0, 0, 0, 0)
    icon_box_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
    icon_lbl = QLabel()
    icon_lbl.setStyleSheet("background: transparent;")
    icon_lbl.setPixmap(qta.icon(icon_name, color=icon_color).pixmap(16, 16))
    icon_box_layout.addWidget(icon_lbl)
    top.addWidget(icon_box, alignment=Qt.AlignmentFlag.AlignVCenter)

    text_col = QVBoxLayout()
    text_col.setSpacing(2)
    title_lbl = QLabel(task["title"])
    title_lbl.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 12px; font-weight: 600; background: transparent;")
    text_col.addWidget(title_lbl)
    sub_text = task.get("claimed_note") or task.get("unlock_hint") or task["subtitle"]
    sub_lbl = QLabel(sub_text)
    sub_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 11px; background: transparent;")
    text_col.addWidget(sub_lbl)
    top.addLayout(text_col, stretch=1)

    if task.get("progress"):
        current, total = task["progress"]
        prog_row = QHBoxLayout()
        prog_row.setSpacing(10)
        bar = QProgressBar()
        bar.setFixedWidth(220)
        bar.setRange(0, total)
        bar.setValue(current)
        bar.setTextVisible(False)
        bar.setStyleSheet(
            f"QProgressBar {{ background-color: {PALETTE['border']}; border-radius: 4px; border: none; min-height: 8px; max-height: 8px; }}"
            f"QProgressBar::chunk {{ background-color: {accent}; border-radius: 2px; }}"
        )
        prog_row.addWidget(bar, alignment=Qt.AlignmentFlag.AlignVCenter)
        prog_lbl = QLabel(f"{current} / {total} {task.get('progress_unit', '')}".strip())
        prog_lbl.setFixedWidth(70)
        prog_lbl.setStyleSheet(f"color: {PALETTE['text_muted']}; font-size: 11px; background: transparent;")
        prog_row.addWidget(prog_lbl, alignment=Qt.AlignmentFlag.AlignVCenter)
        top.addStretch()
        top.addLayout(prog_row)
        top.addStretch()

    credit_lbl = QLabel(f"+{task['credits']} Credits")
    credit_lbl.setFixedWidth(90)
    credit_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    credit_lbl.setStyleSheet(f"color: {accent}; font-size: 12px; font-weight: 600; background: transparent;")
    top.addWidget(credit_lbl, alignment=Qt.AlignmentFlag.AlignVCenter)

    top.addWidget(_status_widget(task, on_claim), alignment=Qt.AlignmentFlag.AlignVCenter)

    outer.addLayout(top)

    divider = QFrame()
    divider.setStyleSheet(f"background-color: {PALETTE['border']}; max-height: 1px; min-height: 1px;")
    outer.addWidget(divider)

    return wrap


class ProgressRing(QWidget):
    """Hand-drawn two-tone donut: red slice = completed, green slice =
    in-progress, remaining track = locked. Same technique as
    overview.DonutChartWidget, kept local so this page has no dependency
    on overview.py."""
    def __init__(self, completed: int, in_progress: int, total: int, parent=None):
        super().__init__(parent)
        self.completed = completed
        self.in_progress = in_progress
        self.total = max(1, total)
        self.setFixedSize(150, 150)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        thickness = 16
        rect = QRectF(thickness / 2 + 2, thickness / 2 + 2,
                       self.width() - thickness - 4, self.height() - thickness - 4)

        track_pen = QPen(QColor(PALETTE['border']), thickness)
        track_pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        painter.setPen(track_pen)
        painter.drawArc(rect, 0, 360 * 16)

        start_angle = 90 * 16
        for value, color in ((self.completed, PALETTE['green_solid']), (self.in_progress, PALETTE['red_solid'])):
            span = -value / self.total * 360 * 16
            pen = QPen(QColor(color), thickness)
            pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            painter.setPen(pen)
            painter.drawArc(rect, int(start_angle), int(span))
            start_angle += span

        value_font = painter.font()
        value_font.setPointSize(15)
        value_font.setBold(True)
        painter.setFont(value_font)
        painter.setPen(QPen(QColor(PALETTE['text_primary'])))
        painter.drawText(rect.adjusted(0, -8, 0, -8), Qt.AlignmentFlag.AlignCenter,
                          f"{self.completed} / {self.total}")

        label_font = painter.font()
        label_font.setPointSize(8)
        label_font.setBold(False)
        painter.setFont(label_font)
        painter.setPen(QPen(QColor(PALETTE['text_muted'])))
        painter.drawText(rect.adjusted(0, 14, 0, 14), Qt.AlignmentFlag.AlignCenter, "Completed")

        painter.end()


def _legend_row(dot_color: str, label: str, value: int) -> QWidget:
    wrap = QWidget()
    wrap.setStyleSheet("background: transparent;")
    row = QHBoxLayout(wrap)
    row.setContentsMargins(0, 3, 0, 3)
    row.setSpacing(8)
    dot = QLabel()
    dot.setStyleSheet("background: transparent;")
    dot.setPixmap(qta.icon('fa5s.circle', color=dot_color).pixmap(8, 8))
    row.addWidget(dot)
    lbl = QLabel(label)
    lbl.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 12px; background: transparent;")
    row.addWidget(lbl, stretch=1)
    val_lbl = QLabel(str(value))
    val_lbl.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 12px; font-weight: 600; background: transparent;")
    row.addWidget(val_lbl)
    return wrap


def _how_it_works_row(number: str, icon_name: str, title: str, desc: str) -> QWidget:
    wrap = QWidget()
    wrap.setStyleSheet("background: transparent;")
    row = QHBoxLayout(wrap)
    row.setContentsMargins(0, 4, 0, 4)
    row.setSpacing(12)

    badge = QFrame()
    badge.setFixedSize(30, 30)
    badge.setStyleSheet(f"background-color: {config.rgba_from_hex(PALETTE['accent'], 0.28)}; border-radius: 15px;")
    badge_layout = QHBoxLayout(badge)
    badge_layout.setContentsMargins(0, 0, 0, 0)
    badge_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
    badge_lbl = QLabel(number)
    badge_lbl.setStyleSheet(f"color: {PALETTE['red_solid']}; font-size: 11px; font-weight: bold; background: transparent;")
    badge_layout.addWidget(badge_lbl)
    row.addWidget(badge, alignment=Qt.AlignmentFlag.AlignTop)

    text_col = QVBoxLayout()
    text_col.setSpacing(2)
    title_lbl = QLabel(title)
    title_lbl.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 13px; font-weight: 600; background: transparent;")
    text_col.addWidget(title_lbl)
    desc_lbl = QLabel(desc)
    desc_lbl.setWordWrap(True)
    desc_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 12px; background: transparent;")
    text_col.addWidget(desc_lbl)
    row.addLayout(text_col, stretch=1)

    return wrap


# Lifetime-earned tiers used for the small badge next to "Total Credits
# Earned" in the banner -- purely cosmetic progression on top of the same
# TOTAL_CREDITS_EARNED counter, ordered highest-threshold first so the
# first match wins.
def _tiers() -> list:
    """Looked up fresh on every call so it tracks theme switches. "Bronze
    Earner"'s #cd7f32 is a metal/brand color (like rewards_page.py's
    "Milestone" tier) and intentionally stays hardcoded."""
    return [
        (5000, "Gold Earner", PALETTE["yellow"]),
        (2000, "Silver Earner", PALETTE["text_muted"]),
        (500, "Bronze Earner", "#cd7f32"),
        (0, "New Earner", PALETTE["text_dim"]),
    ]


def _tier_for(total: int) -> tuple:
    tiers = _tiers()
    for threshold, label, color in tiers:
        if total >= threshold:
            return label, color
    return tiers[-1][1], tiers[-1][2]


class _RewardsRefreshSignals(QObject):
    """QRunnable can't emit signals itself (it isn't a QObject) -- same
    reason search_leads.py's _FilterWorkerSignals exists. Carries a
    generation number so a slow-to-finish refresh from an earlier nav
    click can't clobber a newer one that already landed (e.g. rapid
    tab-tab-tab-back clicking)."""
    finished = Signal(int, object, object)  # generation, sections, total_credits_earned
    failed = Signal(int)


class _RewardsRefreshWorker(QRunnable):
    """Runs build_sections()/get_my_credits_earned() -- build_sections
    alone is a list_rewards() call plus a get_my_progress()/get_my_reward()
    round trip per tracked reward, so it's a dozen-plus blocking HTTP
    requests. Doing that synchronously on the GUI thread (as EarnCreditsPage
    used to, both at construction and on every subsequent nav-triggered
    refresh) is what made clicking into this tab visibly freeze the app for
    a moment each time. Running it here on a QThreadPool worker thread
    instead keeps the GUI thread free the whole time; the result comes back
    via `signals.finished` and gets applied on the GUI thread from there."""

    def __init__(self, generation: int, user_id: int):
        super().__init__()
        self.generation = generation
        self.user_id = user_id
        self.signals = _RewardsRefreshSignals()

    def run(self):
        try:
            sections = build_sections(self.user_id)
            total_credits_earned = get_my_credits_earned()
        except ApiError:
            self.signals.failed.emit(self.generation)
            return
        self.signals.finished.emit(self.generation, sections, total_credits_earned)


class _ClaimResultSignal(QObject):
    # generation, claim_reward() result (None if invalid/unreachable),
    # refreshed sections (None if that part failed), total_credits_earned
    finished = Signal(int, object, object, object)


class _ClaimWorker(QRunnable):
    """Same idea as _RewardsRefreshWorker, but for the Claim button:
    claim_reward() itself is one request, but on_claim used to follow it
    with the same build_sections()/get_my_credits_earned() resync as
    refresh() -- all blocking the GUI thread, which is what made clicking
    Claim visibly freeze the app for a moment. Runs the whole
    claim-then-resync sequence here instead; the button's own busy state
    (see _status_widget's claimable branch) is what gives the user
    feedback in the meantime, not a frozen UI."""

    def __init__(self, generation: int, user_id: int, task_id: str):
        super().__init__()
        self.generation = generation
        self.user_id = user_id
        self.task_id = task_id
        self.claim_result_signal = _ClaimResultSignal()

    def run(self):
        try:
            result = claim_reward(self.task_id)
        except ApiError:
            # Couldn't reach the server / claim rejected -- nothing was
            # claimed server-side, leave the page as-is (same as the old
            # synchronous behavior's "return silently").
            self.claim_result_signal.finished.emit(self.generation, None, None, None)
            return
        try:
            sections = build_sections(self.user_id)
        except ApiError:
            sections = None
        total_credits_earned = None
        if result is not None:
            try:
                total_credits_earned = get_my_credits_earned()
            except ApiError:
                pass
        self.claim_result_signal.finished.emit(self.generation, result, sections, total_credits_earned)


class EarnCreditsPage(QScrollArea):
    """The "Earn Credits" nav tab -- task checklist (left) + progress ring /
    how-it-works / tips (right).

    self.sections / self.total_credits_earned are loaded from the server
    on init and reloaded after every claim -- see build_sections() and
    get_my_credits_earned() (PROGRESS_REWARDS.md Phase 3, PROGRESS.md
    Phase 5)."""

    credits_changed = Signal(int, int)  # (remaining, total) -- same shape as search_leads.py's

    def __init__(self, user_id: int = None, parent=None):
        super().__init__(parent)
        self.setObjectName("DashScroll")
        self.setWidgetResizable(True)

        self.user_id = user_id
        self.sections = build_sections(self.user_id)
        try:
            self.total_credits_earned = get_my_credits_earned() if self.user_id else 0
        except ApiError:
            self.total_credits_earned = 0

        # Bumped on every refresh() call; a worker only applies its result
        # if its generation still matches when it finishes (see
        # _on_refresh_result), so a stale in-flight refresh from an earlier
        # nav click can't overwrite a newer one.
        self._refresh_generation = 0

        content = QWidget()
        outer = QVBoxLayout(content)
        outer.setContentsMargins(20, 16, 20, 20)
        outer.setSpacing(14)

        # --- Header ---
        header_row = QHBoxLayout()
        header_col = QVBoxLayout()
        header_col.setSpacing(2)
        title_lbl = QLabel("Earn Credits")
        title_lbl.setObjectName("HeaderTitle")
        header_col.addWidget(title_lbl)
        sub_lbl = QLabel("Complete tasks and achievements to earn free credits.")
        sub_lbl.setObjectName("HeaderSub")
        header_col.addWidget(sub_lbl)
        header_row.addLayout(header_col)
        header_row.addStretch()
        history_btn = QPushButton(qta.icon('fa5s.history', color=PALETTE['text_muted']), " History")
        history_btn.setObjectName("OutlineBtn")
        history_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        header_row.addWidget(history_btn, alignment=Qt.AlignmentFlag.AlignTop)
        outer.addLayout(header_row)

        # --- Banner: total credits earned + tier badge ---
        banner = QFrame()
        banner.setObjectName("DashCard")
        banner.setStyleSheet(
            f"#DashCard {{ background-color: {config.rgba_from_hex(PALETTE['accent'], 0.07)}; "
            f"border: 1px solid {config.rgba_from_hex(PALETTE['accent'], 0.25)}; border-radius: 10px; }}"
        )
        banner_row = QHBoxLayout(banner)
        banner_row.setContentsMargins(18, 16, 18, 16)
        gift_lbl = QLabel("🎁")
        gift_lbl.setStyleSheet("font-size: 48px; background: transparent;")
        banner_row.addWidget(gift_lbl)
        banner_text = QVBoxLayout()
        banner_text.setSpacing(2)
        b_title = QLabel("Complete tasks. Earn credits. Unlock more leads.")
        b_title.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 14px; font-weight: 600; background: transparent;")
        banner_text.addWidget(b_title)
        b_sub = QLabel("More activity means more credits. Take advantage of all the ways to earn!")
        b_sub.setStyleSheet(f"color: {PALETTE['text_muted']}; font-size: 12px; background: transparent;")
        banner_text.addWidget(b_sub)
        banner_row.addLayout(banner_text, stretch=1)

        earned_col = QVBoxLayout()
        earned_col.setSpacing(2)
        earned_label_row = QHBoxLayout()
        earned_label_row.setSpacing(6)
        earned_label_row.addStretch()
        earned_label = QLabel("Total Credits Earned")
        earned_label.setStyleSheet(f"color: {PALETTE['text_muted']}; font-size: 10px; background: transparent;")
        earned_label_row.addWidget(earned_label)
        self.tier_badge = QLabel()
        self.tier_badge.setStyleSheet("font-size: 9px; font-weight: 700; padding: 1px 6px; border-radius: 6px; background: transparent;")
        earned_label_row.addWidget(self.tier_badge)
        earned_col.addLayout(earned_label_row)
        earned_row = QHBoxLayout()
        earned_row.addStretch()
        coins_lbl = QLabel()
        coins_lbl.setStyleSheet("background: transparent;")
        coins_lbl.setPixmap(qta.icon('fa5s.coins', color=PALETTE['yellow']).pixmap(16, 16))
        earned_row.addWidget(coins_lbl)
        self.earned_value_lbl = QLabel(f"{self.total_credits_earned:,}")
        self.earned_value_lbl.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 20px; font-weight: bold; background: transparent;")
        earned_row.addWidget(self.earned_value_lbl)
        earned_col.addLayout(earned_row)
        lifetime_lbl = QLabel("Lifetime")
        lifetime_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        lifetime_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 9px; background: transparent;")
        earned_col.addWidget(lifetime_lbl)
        banner_row.addLayout(earned_col)
        outer.addWidget(banner)
        self._update_tier_badge()

        # --- Body: two columns ---
        body = QHBoxLayout()
        body.setSpacing(14)

        left_col = QVBoxLayout()
        left_col.setSpacing(14)
        self.section_layouts = {}
        self.streak_labels = {}
        for section_name, tasks in self.sections.items():
            icon = {
                "Welcome Rewards": "fa5s.birthday-cake", "Daily Rewards": "fa5s.calendar-check",
                "Referral Rewards": "fa5s.user-friends", "Usage Rewards": "fa5s.rocket",
                "Milestone Rewards": "fa5s.lock", "Engagement Rewards": "fa5s.headset",
            }.get(section_name, "fa5s.star")
            card, body_layout = _card()
            head = QHBoxLayout()
            icon_lbl = QLabel()
            icon_lbl.setStyleSheet("background: transparent;")
            icon_lbl.setPixmap(qta.icon(icon, color=PALETTE['yellow']).pixmap(14, 14))
            head.addWidget(icon_lbl)
            head_title = QLabel(section_name)
            head_title.setStyleSheet(f"color: {PALETTE['text_primary']}; font-size: 13px; font-weight: 600; background: transparent;")
            head.addWidget(head_title)
            head.addStretch()

            # Daily Rewards gets a streak-flame indicator next to its
            # header, driven by the "login_3" task's current progress --
            # makes the daily-login habit loop visible at a glance instead
            # of buried in a progress bar.
            if section_name == "Daily Rewards":
                streak_wrap = QHBoxLayout()
                streak_wrap.setSpacing(5)
                flame_lbl = QLabel()
                flame_lbl.setStyleSheet("background: transparent;")
                flame_lbl.setPixmap(qta.icon('fa5s.fire', color='#f97316').pixmap(13, 13))
                streak_wrap.addWidget(flame_lbl)
                streak_text = QLabel()
                streak_text.setStyleSheet("color: #f97316; font-size: 11px; font-weight: 700; background: transparent;")
                streak_wrap.addWidget(streak_text)
                head.addLayout(streak_wrap)
                self.streak_labels[section_name] = streak_text

            body_layout.addLayout(head)

            rows_wrap = QVBoxLayout()
            rows_wrap.setSpacing(0)
            body_layout.addLayout(rows_wrap)
            self.section_layouts[section_name] = (card, rows_wrap)
            left_col.addWidget(card)

        self._render_sections()
        left_col.addStretch()
        body.addLayout(left_col, stretch=2)

        # --- Right column ---
        right_col = QVBoxLayout()
        right_col.setSpacing(14)

        progress_card, progress_layout = _card("Your Progress")
        ring_row = QHBoxLayout()
        ring_row.addStretch()
        self.ring = ProgressRing(0, 0, 1)
        ring_row.addWidget(self.ring)
        ring_row.addStretch()
        progress_layout.addLayout(ring_row)
        self.legend_col = QVBoxLayout()
        self.legend_col.setSpacing(2)
        progress_layout.addLayout(self.legend_col)
        right_col.addWidget(progress_card)

        how_card, how_layout = _card("How It Works")
        how_layout.addWidget(_how_it_works_row("1", "fa5s.clipboard-list", "Complete Tasks",
                                                "Finish tasks and achievements listed on this page."))
        how_layout.addWidget(_how_it_works_row("2", "fa5s.gift", "Earn Credits",
                                                "Get free credits instantly when you complete a task."))
        how_layout.addWidget(_how_it_works_row("3", "fa5s.bolt", "Use Your Credits",
                                                "Use your credits to unlock contacts and export leads."))
        right_col.addWidget(how_card)

        tips_card, tips_layout = _card("Tips")
        for tip in ("Log in daily to maintain your streak.",
                    "Invite more friends to earn bigger rewards.",
                    "Check back often for new tasks and promotions."):
            tip_row = QHBoxLayout()
            check_lbl = QLabel()
            check_lbl.setStyleSheet("background: transparent; margin-top: 3px;")
            check_lbl.setPixmap(qta.icon('fa5s.check', color=PALETTE['green']).pixmap(12, 12))
            tip_row.addWidget(check_lbl, alignment=Qt.AlignmentFlag.AlignTop)
            tip_lbl = QLabel(tip)
            tip_lbl.setWordWrap(True)
            tip_lbl.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 12px; background: transparent;")
            tip_row.addWidget(tip_lbl, stretch=1)
            tips_layout.addLayout(tip_row)
        right_col.addWidget(tips_card)

        more_card = QFrame()
        more_card.setObjectName("DashCard")
        more_card.setStyleSheet(
            f"#DashCard {{ background-color: {config.rgba_from_hex(PALETTE['yellow'], 0.06)}; "
            f"border: 1px solid {config.rgba_from_hex(PALETTE['yellow'], 0.25)}; border-radius: 10px; }}"
        )
        more_row = QHBoxLayout(more_card)
        more_row.setContentsMargins(14, 12, 14, 12)
        star_lbl = QLabel()
        star_lbl.setStyleSheet("background: transparent;")
        star_lbl.setPixmap(qta.icon('fa5s.star', color=PALETTE['yellow']).pixmap(16, 16))
        more_row.addWidget(star_lbl, alignment=Qt.AlignmentFlag.AlignTop)
        more_text = QVBoxLayout()
        more_text.setSpacing(2)
        more_title = QLabel("More tasks coming soon!")
        more_title.setStyleSheet(f"color: {PALETTE['yellow']}; font-size: 11px; font-weight: 600; background: transparent;")
        more_text.addWidget(more_title)
        more_sub = QLabel("We're always adding new ways for you to earn more credits.")
        more_sub.setWordWrap(True)
        more_sub.setStyleSheet(f"color: {PALETTE['text_muted']}; font-size: 10px; background: transparent;")
        more_text.addWidget(more_sub)
        more_row.addLayout(more_text, stretch=1)
        right_col.addWidget(more_card)

        right_col.addStretch()
        right_wrap = QWidget()
        right_wrap.setLayout(right_col)
        right_wrap.setFixedWidth(300)
        body.addWidget(right_wrap)

        outer.addLayout(body)
        self.setWidget(content)
        self._update_progress()
        self._update_streak_labels()

    def _render_sections(self):
        for section_name, (card, rows_wrap) in self.section_layouts.items():
            while rows_wrap.count():
                item = rows_wrap.takeAt(0)
                w = item.widget()
                if w:
                    w.deleteLater()
            tasks = self.sections[section_name]
            card.setVisible(bool(tasks))
            for task in tasks:
                rows_wrap.addWidget(_task_row(task, self.on_claim))

    def _update_progress(self):
        all_tasks = [t for tasks in self.sections.values() for t in tasks]
        completed = sum(1 for t in all_tasks if t["status"] == "claimed")
        in_progress = sum(1 for t in all_tasks if t["status"] in ("in_progress", "claimable"))
        locked = sum(1 for t in all_tasks if t["status"] == "locked")
        total = len(all_tasks)

        self.ring.completed = completed
        self.ring.in_progress = in_progress
        self.ring.total = max(1, total)
        self.ring.update()

        while self.legend_col.count():
            item = self.legend_col.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self.legend_col.addWidget(_legend_row(PALETTE['green_solid'], "Completed", completed))
        self.legend_col.addWidget(_legend_row(PALETTE['red_solid'], "In Progress", in_progress))
        self.legend_col.addWidget(_legend_row(PALETTE['text_muted'], "Locked", locked))

    def _update_streak_labels(self):
        """Refresh the flame/streak text next to the Daily Rewards header
        from the shortest login-streak task's live progress (e.g. "Login
        Streak (3 Days)" over "Login Streak (7 Days)"), so the streak
        count never drifts out of sync with the task list below it."""
        daily = self.sections.get("Daily Rewards", [])
        streak_lbl = self.streak_labels.get("Daily Rewards")
        if not streak_lbl:
            return
        streak_tasks = [t for t in daily if t.get("progress")]
        streak_task = min(streak_tasks, key=lambda t: t["progress"][1]) if streak_tasks else None
        days = streak_task["progress"][0] if streak_task else 0
        streak_lbl.setText(f"{days} day streak")

    def _update_tier_badge(self):
        label, color = _tier_for(self.total_credits_earned)
        self.tier_badge.setText(label)
        self.tier_badge.setStyleSheet(
            f"font-size: 9px; font-weight: 700; padding: 1px 6px; border-radius: 6px; "
            f"color: {color}; background-color: {config.rgba_from_hex(color, 0.15)};"
        )

    def _celebrate_claim(self, task: dict):
        """Small toast that fades in/out over the page when a task is
        claimed -- the only bit of feedback a "Claim" click had before was
        the pill silently swapping label, which didn't read as a reward at
        all. Purely visual, no state changes here."""
        toast = QLabel(f"🎉  +{task['credits']} credits earned!", self)
        toast.setStyleSheet(
            f"background-color: {PALETTE['green']}; color: #0d1117; font-size: 13px; "
            f"font-weight: 700; padding: 10px 18px; border-radius: 8px;"
        )
        toast.adjustSize()
        toast.move(max(0, self.width() // 2 - toast.width() // 2), 12)
        toast.show()
        toast.raise_()

        effect = QGraphicsOpacityEffect(toast)
        toast.setGraphicsEffect(effect)
        effect.setOpacity(1.0)

        fade = QPropertyAnimation(effect, b"opacity", toast)
        fade.setDuration(900)
        fade.setStartValue(1.0)
        fade.setEndValue(0.0)
        fade.setEasingCurve(QEasingCurve.Type.InQuad)
        fade.finished.connect(toast.deleteLater)
        toast._fade_anim = fade  # keep a reference so it isn't garbage-collected mid-animation
        QTimer.singleShot(900, fade.start)

    def refresh(self):
        """Re-pulls sections/progress from the server and re-renders --
        called whenever the user navigates TO this page (see dashboard.py's
        set_active_nav), not just right after a claim. Without this, a
        reward completed elsewhere (e.g. submitting the Contact Us form on
        Help and Support) wouldn't show as claimable here until the whole
        app rebuilt itself (theme toggle or a fresh login), since this
        page is built once and cached, not rebuilt on every nav click.

        Dispatches the actual fetch to a background thread (see
        _RewardsRefreshWorker) instead of calling build_sections() here
        directly -- that's a dozen-plus blocking HTTP requests, and running
        them on the GUI thread on every single nav click into this tab is
        what made switching to/from it visibly freeze each time."""
        if not self.user_id:
            return
        self._refresh_generation += 1
        worker = _RewardsRefreshWorker(self._refresh_generation, self.user_id)
        worker.signals.finished.connect(self._on_refresh_result)
        QThreadPool.globalInstance().start(worker)

    def _on_refresh_result(self, generation: int, sections: dict, total_credits_earned: int):
        # A newer refresh() call already superseded this one -- drop it
        # silently rather than showing stale data over fresher data.
        if generation != self._refresh_generation:
            return
        self.sections = sections
        self.total_credits_earned = total_credits_earned
        self.earned_value_lbl.setText(f"{self.total_credits_earned:,}")
        self._render_sections()
        self._update_progress()
        self._update_streak_labels()
        self._update_tier_badge()

    def on_claim(self, task_id: str, btn: QPushButton = None):
        """Cashes in a claimable task via api_client.claim_reward() --
        credits the user's real balance server-side, records the claim,
        and reloads self.sections from the server so this row (and any
        row it unlocks as a prerequisite) reflects the new state
        immediately.

        Dispatches the claim-then-resync sequence to a background thread
        (see _ClaimWorker) instead of blocking the GUI thread with it --
        that sequence is a claim_reward() call plus the same dozen-plus-
        request resync refresh() does, and running all of that
        synchronously is what made clicking Claim visibly freeze the app.
        Puts the clicked button into a disabled "Claiming..." busy state
        immediately (with a wait cursor) so the click still feels
        acknowledged right away even though the actual result lands a
        moment later -- _render_sections() rebuilds this row fresh once
        the result comes back, which naturally clears the busy state
        whether the claim succeeded, was rejected, or the server was
        unreachable."""
        if not self.user_id:
            return
        if btn is not None:
            btn.setEnabled(False)
            btn.setText("Claiming...")
            btn.setCursor(Qt.CursorShape.WaitCursor)

        self._refresh_generation += 1
        worker = _ClaimWorker(self._refresh_generation, self.user_id, task_id)
        worker.claim_result_signal.finished.connect(self._on_claim_result)
        QThreadPool.globalInstance().start(worker)

    def _on_claim_result(self, generation: int, result, sections, total_credits_earned):
        # A newer refresh()/on_claim() call already superseded this one --
        # drop it silently. (Also covers the claim_reward()-unreachable
        # case, where result/sections/total_credits_earned all arrive as
        # None -- there's nothing to apply, _render_sections() below with
        # the untouched self.sections just restores the button to normal.)
        if generation != self._refresh_generation:
            return
        if sections is not None:
            self.sections = sections
        if total_credits_earned is not None:
            self.total_credits_earned = total_credits_earned
            self.earned_value_lbl.setText(f"{self.total_credits_earned:,}")

        self._render_sections()
        self._update_progress()
        self._update_streak_labels()
        self._update_tier_badge()

        if result is None:
            return

        claimed_task = next(
            (t for tasks in self.sections.values() for t in tasks if t["id"] == result["user_reward"]["reward_id"]),
            None,
        )
        if claimed_task:
            self._celebrate_claim(claimed_task)

        # claim_reward()'s response already carries the post-claim balance
        # ({"user_reward", "credits", "spent", "reward_value"}) -- no
        # separate user lookup needed to emit credits_changed.
        self.credits_changed.emit(result["credits"], result["credits"])