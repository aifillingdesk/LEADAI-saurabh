"""Partner program constants: collections, statuses, permissions, defaults."""
from typing import Dict, FrozenSet, List

# ── Collections ──────────────────────────────────────────────────────────────
APPLICATIONS = "partner_applications"
PARTNERS = "partners"
TIERS = "partner_tiers"
RULES = "partner_commission_rules"
SETTINGS = "partner_settings"            # one document: _id "program"
CLICKS = "partner_referral_clicks"
REFERRALS = "partner_referrals"          # one per attributed organization (= partner customer)
CAMPAIGNS = "partner_campaigns"
COMMISSIONS = "partner_commissions"      # also holds manual adjustments (kind="adjustment")
WALLET_TX = "partner_wallet_transactions"
WALLETS = "partner_wallets"              # cached balances, recomputed from COMMISSIONS
PAYOUTS = "partner_payouts"
COUPONS = "partner_coupons"
COUPON_USAGES = "partner_coupon_usages"
ASSETS = "partner_marketing_assets"
PRICING = "partner_pricing"              # partner / tier customer price rules

# ── Types & statuses ─────────────────────────────────────────────────────────
AFFILIATE = "affiliate"
RESELLER = "reseller"
PARTNER_TYPES = (AFFILIATE, RESELLER)

APP_PENDING = "pending"
APP_CHANGES = "changes_requested"
APP_APPROVED = "approved"
APP_REJECTED = "rejected"
APPLICATION_STATUSES = (APP_PENDING, APP_CHANGES, APP_APPROVED, APP_REJECTED)

P_ACTIVE = "active"
P_SUSPENDED = "suspended"
PARTNER_STATUSES = (P_ACTIVE, P_SUSPENDED)

# referral (partner customer) funnel stages, in order. A referral only moves
# forward; churn is recorded as an event + ``churned_at`` (stage kept).
STAGE_SIGNED_UP = "signed_up"        # demo request created
STAGE_DEMO = "demo"                  # demo approved
STAGE_SUBSCRIPTION = "subscription"  # checkout started (pending payment)
STAGE_PAYMENT = "payment"            # payment verified, awaiting confirmation
STAGE_CUSTOMER = "customer"          # subscription confirmed = converted
STAGES = (STAGE_SIGNED_UP, STAGE_DEMO, STAGE_SUBSCRIPTION, STAGE_PAYMENT, STAGE_CUSTOMER)
STAGE_RANK = {s: i for i, s in enumerate(STAGES)}

# commission lifecycle:
#   pending (qualification period) -> qualified -> approved -> payable
#   -> processing (in a payout) -> paid;  reversed from any unpaid state.
#   Refunds of paid commissions create a "clawback" entry (negative, payable).
C_PENDING = "pending"
C_QUALIFIED = "qualified"
C_APPROVED = "approved"
C_PAYABLE = "payable"
C_PROCESSING = "processing"
C_PAID = "paid"
C_REVERSED = "reversed"
COMMISSION_STATUSES = (C_PENDING, C_QUALIFIED, C_APPROVED, C_PAYABLE, C_PROCESSING, C_PAID, C_REVERSED)
UNPAID_STATUSES = (C_PENDING, C_QUALIFIED, C_APPROVED, C_PAYABLE)

PO_REQUESTED = "requested"
PO_REVIEW = "under_review"
PO_APPROVED = "approved"
PO_PROCESSING = "processing"
PO_PAID = "paid"
PO_FAILED = "failed"
PO_REJECTED = "rejected"
PO_CANCELLED = "cancelled"
PAYOUT_STATUSES = (PO_REQUESTED, PO_REVIEW, PO_APPROVED, PO_PROCESSING, PO_PAID, PO_FAILED,
                   PO_REJECTED, PO_CANCELLED)
OPEN_PAYOUT_STATUSES = (PO_REQUESTED, PO_REVIEW, PO_APPROVED, PO_PROCESSING)
# allowed Super Admin payout actions: action -> (from statuses, to status)
PAYOUT_ACTIONS = {
    "review": ((PO_REQUESTED,), PO_REVIEW),
    "approve": ((PO_REQUESTED, PO_REVIEW), PO_APPROVED),
    "process": ((PO_APPROVED,), PO_PROCESSING),
    "mark_paid": ((PO_APPROVED, PO_PROCESSING), PO_PAID),
    "fail": ((PO_PROCESSING, PO_APPROVED), PO_FAILED),
    "reject": ((PO_REQUESTED, PO_REVIEW, PO_APPROVED), PO_REJECTED),
}

# ── Partner permissions (Super Admin assigns them per partner) ───────────────
DASHBOARD_VIEW = "dashboard.view"
PROFILE_MANAGE = "profile.manage"
LINKS_CREATE = "referral_links.create"
REFERRALS_VIEW = "referrals.view"
CUSTOMERS_VIEW = "customers.view"
CUSTOMERS_CREATE = "customers.create"
RESELLER_MANAGE = "reseller.customers.manage"
COMMISSIONS_VIEW = "commissions.view"
WALLET_VIEW = "wallet.view"
PAYOUTS_REQUEST = "payouts.request"
ANALYTICS_VIEW = "analytics.view"
CAMPAIGNS_MANAGE = "campaigns.manage"
COUPONS_MANAGE = "coupons.manage"
MARKETING_ACCESS = "marketing.access"
API_ACCESS = "api.access"
SALES_VIEW = "sales.view"          # product catalog, prices, commissions, share links
DEALS_MANAGE = "deals.manage"      # register prospects (deal registration)

PERMISSION_LABELS: Dict[str, str] = {
    SALES_VIEW: "Sell LeadAI (product & pricing kit)",
    DEALS_MANAGE: "Register deals",
    DASHBOARD_VIEW: "View dashboard",
    PROFILE_MANAGE: "Manage profile",
    LINKS_CREATE: "Create referral links",
    REFERRALS_VIEW: "View referrals",
    CUSTOMERS_VIEW: "View customers",
    CUSTOMERS_CREATE: "Create customers",
    RESELLER_MANAGE: "Manage reseller customers",
    COMMISSIONS_VIEW: "View commissions",
    WALLET_VIEW: "View wallet",
    PAYOUTS_REQUEST: "Request payout",
    ANALYTICS_VIEW: "View analytics",
    CAMPAIGNS_MANAGE: "Manage campaigns",
    COUPONS_MANAGE: "Manage coupons",
    MARKETING_ACCESS: "Access marketing assets",
    API_ACCESS: "Access API",
}
ALL_PERMISSIONS: FrozenSet[str] = frozenset(PERMISSION_LABELS)
# only meaningful for resellers: dropped from an affiliate's effective set
RESELLER_ONLY: FrozenSet[str] = frozenset({CUSTOMERS_CREATE, RESELLER_MANAGE})

_COMMON_DEFAULTS = [DASHBOARD_VIEW, PROFILE_MANAGE, LINKS_CREATE, REFERRALS_VIEW,
                    CUSTOMERS_VIEW, COMMISSIONS_VIEW, WALLET_VIEW, PAYOUTS_REQUEST,
                    ANALYTICS_VIEW, CAMPAIGNS_MANAGE, MARKETING_ACCESS, SALES_VIEW, DEALS_MANAGE]
DEFAULT_PERMISSIONS: Dict[str, List[str]] = {
    AFFILIATE: list(_COMMON_DEFAULTS),
    RESELLER: _COMMON_DEFAULTS + [CUSTOMERS_CREATE, RESELLER_MANAGE],
}

# ── Program defaults (Super Admin → Partners → Program settings) ─────────────
DEFAULT_SETTINGS = {
    "applications_open": True,
    "attribution_model": "first_touch",      # first_touch | last_touch
    "attribution_window_days": 30,
    "allow_code_entry": True,                 # ?ref=CODE on the signup form without a click
    "commission_hold_days": 14,               # qualification period (refund window), rules may override
    "auto_approve_commissions": True,         # qualified -> approved automatically (unless flagged)
    "reverse_on_cancellation": True,          # cancel during qualification reverses the commission
    "payout_schedule": "on_request",          # on_request | weekly | monthly: when approved -> payable
    "payout_day": 1,                          # weekly: 0=Mon..6=Sun · monthly: 1..28
    "min_payout": 50.0,
    "payout_methods": ["bank_transfer", "paypal", "upi"],
    "default_landing": "/request-demo",
    "partner_coupons_enabled": False,         # partners with coupons.manage may create coupons
    "max_partner_coupon_percent": 20.0,
    "max_clicks_per_ip_per_hour": 30,
    "block_self_referral": True,
    "hold_suspicious_commissions": True,      # suspicious referrals need manual approval
    "terms_version": "2026-10",
    "deal_protection_days": 90,               # an approved deal protects the partner's claim
    "activity_retention_days": 365,           # partner activity log retention
    "auto_tiering": False,                    # evaluate tier requirements automatically
    "allow_tier_downgrade": False,
}
PAYOUT_SCHEDULES = ("on_request", "weekly", "monthly")

ATTRIBUTION_MODELS = ("first_touch", "last_touch")
WINDOW_CHOICES = (7, 30, 60, 90)

REF_COOKIE = "leadai_ref"
VISITOR_COOKIE = "leadai_vid"
API_KEY_PREFIX = "lap_live_"
