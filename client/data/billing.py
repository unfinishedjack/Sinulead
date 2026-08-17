"""
data/billing.py

Billing (prototype only): payment methods, package pricing, and invoices.

Manual "upload proof of payment" flow: pick a payment method, scan/pay
with the shown account, then submit a screenshot for manual review.
Nothing here touches a real payment gateway -- swap PAYMENT_METHODS'
account details and wire submit_payment() (billing.py) to a real backend
once one exists.

Moved out of the old flat data.py as part of splitting it into a data/
package (see PROGRESS.md, Phase 2d). See data/accounts.py's docstring
(Phase 2a) for why this directory has no __init__.py yet -- `import data`
still resolves to the original data.py unchanged until Phase 2f's cutover.

Note: REWARDS and TRANSACTIONS are interleaved with this block in the old
data.py (REWARDS sits between CREDIT_PACKAGES and BILLING_TRANSACTIONS;
TRANSACTIONS sits between BILLING_TRANSACTIONS and INVOICES) but are
deliberately NOT moved here -- they're their own domains, headed to
data/rewards.py and data/transactions.py respectively (Phase 2e).
"""

import os

_ASSETS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets"
)  # one level deeper than the old data.py, so go up two dirs, not one

PAYMENT_METHODS = [
    {
        "id": "gcash", "label": "GCash", "letter": "Gi", "color": "#0072CE",
        "logo": os.path.join(_ASSETS_DIR, "gcash.png"),
        "account_name": "SINULEAD SOLUTIONS", "account_label": "Mobile Number",
        "account_value": "0917 123 4567", "enabled": True,
        "notes": "Please send exact amount. Payments are verified within 1-24 hours.",
        "updated": "Jul 30, 2026 02:15 PM",
    },
    {
        "id": "maya", "label": "Maya (PayMaya)", "letter": "M", "color": "#00b389",
        "logo": os.path.join(_ASSETS_DIR, "maya.png"),
        "account_name": "SINULEAD SOLUTIONS", "account_label": "Mobile Number",
        "account_value": "0917 123 4567", "enabled": True,
        "notes": "Please send exact amount. Payments are verified within 1-24 hours.",
        "updated": "Jul 30, 2026 02:16 PM",
    },
    {
        "id": "paypal", "label": "PayPal", "letter": "P", "color": "#003087",
        "logo": os.path.join(_ASSETS_DIR, "paypal.png"),
        "account_name": "SINULEAD SOLUTIONS", "account_label": "PayPal Email",
        "account_value": "payments@sinulead.com", "enabled": True,
        "notes": "Payments via PayPal. Payments are verified within 1-24 hours.",
        "updated": "Jul 30, 2026 02:16 PM",
    },
]

# General payment settings (admin-editable, "Packages" > "Payment Methods" tab).
PAYMENT_SETTINGS = {
    "approval_mode": "Manual Approval",  # or "Automatic Approval"
    "receipt_required": True,
    "max_receipt_size_mb": 5,
    "accepted_formats": ["JPG", "PNG", "JPEG", "PDF"],
    "payment_expiry_hours": 72,
}

CREDIT_PACKAGES = [
    {"id": "pkg_500", "name": "Starter Pack", "badge": "Basic",
     "credits": 500, "bonus": None, "bonus_pct": 0, "price": "₱50",
     "price_value": 50, "total_credits": 500, "popular": False, "status": "Enabled"},
    {"id": "pkg_1200", "name": "Most Popular", "badge": "Most Popular",
     "credits": 1200, "bonus": "+20% Bonus", "bonus_pct": 20, "price": "₱100",
     "price_value": 100, "total_credits": 1440, "popular": True, "status": "Enabled"},
    {"id": "pkg_3500", "name": "Pro Pack", "badge": "Best Value",
     "credits": 3500, "bonus": "+40% Bonus", "bonus_pct": 40, "price": "₱250",
     "price_value": 250, "total_credits": 4900, "popular": False, "status": "Enabled"},
    {"id": "pkg_8000", "name": "Ultimate Pack", "badge": "Maximum",
     "credits": 8000, "bonus": "+60% Bonus", "bonus_pct": 60, "price": "₱500",
     "price_value": 500, "total_credits": 12800, "popular": False, "status": "Enabled"},
]

# Shown in the "Recent Transactions" card on the Buy Credits tab (short
# list) and, in full, on the Payment History tab.
BILLING_TRANSACTIONS = [
    {"method": "GCash", "credits": "1,200 Credits", "amount": "\u20b1100",
     "status": "Pending", "date": "Jul 30, 2026", "time": "10:42 AM"},
    {"method": "PayPal", "credits": "3,500 Credits", "amount": "\u20b1250",
     "status": "Completed", "date": "Jul 28, 2026", "time": "02:15 PM"},
    {"method": "Maya (PayMaya)", "credits": "500 Credits", "amount": "\u20b150",
     "status": "Completed", "date": "Jul 25, 2026", "time": "11:30 AM"},
    {"method": "GCash", "credits": "8,000 Credits", "amount": "\u20b1500",
     "status": "Completed", "date": "Jul 18, 2026", "time": "09:05 AM"},
    {"method": "Maya (PayMaya)", "credits": "1,200 Credits", "amount": "\u20b1100",
     "status": "Failed", "date": "Jul 10, 2026", "time": "04:47 PM"},
    {"method": "GCash", "credits": "500 Credits", "amount": "\u20b150",
     "status": "Completed", "date": "Jul 3, 2026", "time": "01:20 PM"},
]

INVOICES = [
    {"number": "INV-2026-0042", "date": "Jul 28, 2026", "amount": "\u20b1250", "status": "Paid"},
    {"number": "INV-2026-0037", "date": "Jul 18, 2026", "amount": "\u20b1500", "status": "Paid"},
    {"number": "INV-2026-0029", "date": "Jul 3, 2026", "amount": "\u20b150", "status": "Paid"},
    {"number": "INV-2026-0021", "date": "Jun 22, 2026", "amount": "\u20b1100", "status": "Paid"},
]
