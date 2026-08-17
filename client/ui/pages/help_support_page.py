"""
ui/pages/help_support_page.py

HelpSupportPage: the "Help and Support" nav tab. Two things:

  - An FAQ accordion covering the questions users actually run into in
    this app (credits/unlocking, searches, exports, account/referrals)
    -- static content, no backend needed for this half.
  - A "Contact Us" card: subject + message, submitted via
    core.api_client.create_support_ticket() (POST /support/tickets).

Was previously a dead sidebar button (see dashboard.py's nav_items_bottom
-- "Help and Support" had an icon/label but no clicked.connect and no
entry in nav_buttons/self._page_builders). This is the page + wiring
that fills that in.

Depends on: core.config (PALETTE), core.api_client (create_support_ticket,
list_support_tickets, ApiError), ui.components.widgets (dash_card).
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QScrollArea,
    QPushButton, QLineEdit, QTextEdit, QSizePolicy,
)
from PySide6.QtCore import Qt
import qtawesome as qta

from core.config import PALETTE
from core.api_client import create_support_ticket, ApiError
from ui.components.widgets import dash_card
from ui.dialogs.dialogs import InfoDialog


def _card(title: str = None):
    return dash_card(title, margins=(18, 16, 18, 16))


FAQ_ITEMS = [
    (
        "How do credits and unlocking work?",
        "Each search costs a flat number of credits to run. Once you have "
        "results, unlocking a lead's phone number or email costs credits "
        "per field -- unlocking a field that's already unlocked, or one "
        "the lead doesn't have, is always free. Your balance and spend "
        "history are visible in Billing.",
    ),
    (
        "A search finished but I didn't get any results.",
        "Some queries just don't have matching businesses in the area you "
        "searched, especially for very specific or niche categories. Try "
        "broadening the location or the search term. If you believe "
        "credits were deducted for a search that clearly malfunctioned, "
        "use the contact form below and include the search's date/time.",
    ),
    (
        "I unlocked a contact but it shows locked again after restarting.",
        "This should not happen -- unlocked fields are saved on our "
        "servers as soon as you unlock them. If you see a previously "
        "unlocked phone or email revert to locked, please contact us with "
        "the lead's name and the search it came from so we can look into it.",
    ),
    (
        "Where do my exported files go?",
        "Exports are listed under the Exports tab with their format and "
        "creation date. From there you can re-download or delete them. If "
        "an export looks empty or incomplete, check the source search "
        "still has leads before reporting it.",
    ),
    (
        "How does the referral program work?",
        "Your referral code and link are on the Settings page. When "
        "someone signs up with your code and completes a qualifying "
        "action, you both receive bonus credits. Pending bonus credits "
        "are cashed in automatically the next time you log in.",
    ),
    (
        "The app says it's under maintenance -- what do I do?",
        "That screen means the app is temporarily offline for updates. It "
        "clears on its own once maintenance ends; there's usually a "
        "countdown shown. No action is needed on your end.",
    ),
    (
        "How do I reset my password or change my email?",
        "Open the account menu at the bottom of the sidebar to edit your "
        "profile, including email and password. If you're locked out "
        "entirely, use the contact form below.",
    ),
]


class _FaqItem(QFrame):
    """One collapsible question/answer row. Plain QPushButton + QLabel
    toggle rather than a custom expand/collapse widget -- this app has
    no existing accordion component to reuse, and a single-select-per-
    click toggle is all an FAQ list needs."""

    def __init__(self, question: str, answer: str, parent=None):
        super().__init__(parent)
        self.setObjectName("DashCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(0)

        row = QHBoxLayout()
        self.toggle_btn = QPushButton(question)
        self.toggle_btn.setObjectName("FaqToggle")
        self.toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_btn.setStyleSheet(
            f"QPushButton {{ text-align: left; border: none; background: transparent; "
            f"color: {PALETTE['text_primary']}; font-size: 13px; font-weight: 600; padding: 0; }}"
        )
        row.addWidget(self.toggle_btn, stretch=1)

        self.chevron_lbl = QLabel()
        self.chevron_lbl.setStyleSheet("background: transparent;")
        self._set_chevron(expanded=False)
        row.addWidget(self.chevron_lbl)
        outer.addLayout(row)

        self.answer_lbl = QLabel(answer)
        self.answer_lbl.setWordWrap(True)
        self.answer_lbl.setStyleSheet(
            f"color: {PALETTE['text_dim']}; font-size: 12px; background: transparent; padding-top: 8px;"
        )
        self.answer_lbl.setVisible(False)
        outer.addWidget(self.answer_lbl)

        self.toggle_btn.clicked.connect(self._toggle)
        self._expanded = False

    def _set_chevron(self, expanded: bool):
        icon_name = "fa5s.chevron-up" if expanded else "fa5s.chevron-down"
        self.chevron_lbl.setPixmap(qta.icon(icon_name, color=PALETTE["text_muted"]).pixmap(11, 11))

    def _toggle(self):
        self._expanded = not self._expanded
        self.answer_lbl.setVisible(self._expanded)
        self._set_chevron(self._expanded)


class HelpSupportPage(QScrollArea):
    def __init__(self, parent=None, user_role: str = "user"):
        super().__init__(parent)
        self.setObjectName("DashScroll")
        self.setWidgetResizable(True)
        self.user_role = user_role

        content = QWidget()
        outer = QVBoxLayout(content)
        outer.setContentsMargins(20, 16, 20, 20)
        outer.setSpacing(14)

        header_col = QVBoxLayout()
        header_col.setSpacing(2)
        title_lbl = QLabel("Help and Support")
        title_lbl.setObjectName("HeaderTitle")
        header_col.addWidget(title_lbl)
        sub_lbl = QLabel("Find answers to common questions or reach out to us directly.")
        sub_lbl.setObjectName("HeaderSub")
        header_col.addWidget(sub_lbl)
        outer.addLayout(header_col)

        outer.addWidget(self._build_faq_card())
        outer.addWidget(self._build_contact_card())
        outer.addStretch()

        self.setWidget(content)

    def _build_faq_card(self) -> QWidget:
        card, layout = _card("Frequently Asked Questions")
        layout.setSpacing(8)
        for question, answer in FAQ_ITEMS:
            layout.addWidget(_FaqItem(question, answer))
        return card

    def _build_contact_card(self) -> QWidget:
        card, layout = _card("Contact Us")
        layout.setSpacing(8)

        sub_lbl = QLabel("Can't find what you're looking for? Send us a message and we'll get back to you by email.")
        sub_lbl.setWordWrap(True)
        sub_lbl.setStyleSheet(f"color: {PALETTE['text_dim']}; font-size: 11px; background: transparent;")
        layout.addWidget(sub_lbl)

        layout.addSpacing(4)
        subject_lbl = QLabel("Subject")
        subject_lbl.setStyleSheet(f"color: {PALETTE['text_muted']}; font-size: 11px; background: transparent;")
        layout.addWidget(subject_lbl)
        self.subject_edit = QLineEdit()
        self.subject_edit.setPlaceholderText("e.g. Unlocked contact reverted to locked")
        layout.addWidget(self.subject_edit)

        layout.addSpacing(4)
        message_lbl = QLabel("Message")
        message_lbl.setStyleSheet(f"color: {PALETTE['text_muted']}; font-size: 11px; background: transparent;")
        layout.addWidget(message_lbl)
        self.message_edit = QTextEdit()
        self.message_edit.setObjectName("NoteEdit")
        self.message_edit.setPlaceholderText("Describe the issue -- include the search or lead name if relevant.")
        self.message_edit.setFixedHeight(90)
        layout.addWidget(self.message_edit)

        self.submit_btn = QPushButton(qta.icon("fa5s.paper-plane", color="white"), " Send Message")
        self.submit_btn.setObjectName("RedBtn")
        self.submit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.submit_btn.setMinimumHeight(34)
        self.submit_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.submit_btn.clicked.connect(self._on_submit)
        layout.addWidget(self.submit_btn)

        return card

    def _on_submit(self):
        subject = self.subject_edit.text().strip()
        message = self.message_edit.toPlainText().strip()
        if not subject or not message:
            InfoDialog.show(self, "Missing information", "Please fill in both a subject and a message.",
                             icon_name='fa5s.exclamation-triangle', success=False)
            return

        self.submit_btn.setEnabled(False)
        try:
            create_support_ticket(subject, message)
        except ApiError as e:
            InfoDialog.show(self, "Couldn't send your message", str(e),
                             icon_name='fa5s.exclamation-triangle', success=False)
            return
        finally:
            self.submit_btn.setEnabled(True)

        self.subject_edit.clear()
        self.message_edit.clear()
        InfoDialog.show(
            self, "Message sent",
            "Thanks -- we've received your message and will get back to you by email.",
        )