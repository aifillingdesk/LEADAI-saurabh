"""
Partner program — referral funnel, commission engine, refunds, wallet,
payouts, coupons, partner pricing, tiers and reseller onboarding, on the
real routes and the real billing lifecycle (in-memory MongoDB, mock provider).

  Referral -> Signup -> Demo -> Subscription -> Payment -> Commission -> Wallet -> Payout
  Reseller -> Customer -> Organization -> Subscription -> Commission
"""
import hashlib
import hmac
import json
from datetime import timedelta
from unittest.mock import patch

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from tests.conftest import TEST_SUPERADMIN_PASSWORD, as_superadmin

SUPER = "platform-owner@leadai.example"
PW = "Partner-Pass-2026"
CUST_PW = "Customer-Pass-2026"


@pytest.fixture
def env():
    as_superadmin(SUPER)
    from app.billing.provider import reset_billing_provider
    reset_billing_provider()
    with patch("app.admin.settings.is_maintenance_enabled", return_value=False):
        from app.main import app
        with TestClient(app) as client:
            from app.db.mongo import get_sync_db
            yield client, get_sync_db()


def _login(client, email, password, scope="site"):
    c = TestClient(client.app)
    r = c.post("/api/auth/login", json={"email": email, "password": password, "scope": scope})
    assert r.status_code == 200, (email, scope, r.text)
    return c


def _sa(client):
    return _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")


def _settings(sa, **v):
    r = sa.put("/api/super-admin/partners/settings", json=v)
    assert r.status_code == 200, r.text


def _partner(client, sa, email, *, ptype="affiliate", name="P", company=None, permissions=None):
    body = {"name": name, "email": email, "password": PW, "company": company or name + " Co",
            "partner_type": ptype, "experience": "Ten years of SaaS sales",
            "promotion_plan": "Newsletter and webinars", "accepted_terms": True,
            "payout_info": {"method": "paypal", "paypal_email": "pay@" + email.split("@")[1]}}
    r = client.post("/api/public/partners/apply", json=body)
    assert r.status_code == 200, r.text
    approve = {"partner_type": ptype}
    if permissions is not None:
        approve["permissions"] = permissions
    r = sa.post(f"/api/super-admin/partners/applications/{r.json()['application']['id']}/approve", json=approve)
    assert r.status_code == 200, r.text
    return r.json()["partner"], _login(client, email, PW, scope="partner")


def _paid_plan(owner):
    plans = owner.get("/api/billing/plans").json()["plans"]
    return next(p for p in plans if (p.get("price_monthly") or 0) > 0 and p["slug"] != "enterprise")


def _referred_customer(client, sa, db, partner, email, company, *, via="link", coupon=None, pay=True,
                       visitor=None):
    """Website signup through the partner's link (or typed code) -> demo approval
    -> checkout -> verified payment -> Super Admin confirmation."""
    v = visitor or TestClient(client.app)
    body = {"email": email, "password": CUST_PW, "name": company + " Owner", "company": company,
            "accepted_terms": True}
    if via == "link":
        v.get(f"/r/{partner['referral_code']}", follow_redirects=False)
    else:
        body["ref_code"] = partner["referral_code"]
    r = v.post("/api/auth/signup", json=body)
    assert r.status_code == 200, r.text
    org_id = db.users.find_one({"email": email})["default_organization_id"]
    assert sa.post(f"/api/super-admin/demo-requests/{r.json()['demo_request_id']}/approve", json={}).status_code == 200
    owner = _login(client, email, CUST_PW)
    plan = _paid_plan(owner)
    out = {"org_id": org_id, "owner": owner, "plan": plan}
    if not pay:
        return out
    r = owner.post("/api/billing/checkout", json={"plan_slug": plan["slug"], **({"coupon_code": coupon} if coupon else {})})
    assert r.status_code == 200, r.text
    out["checkout"] = r.json()["checkout"]
    sid = out["checkout"]["session_id"]
    assert owner.post(f"/api/billing/checkout/{sid}/mock-pay", json={"succeed": True}).status_code == 200
    sub = db.subscriptions.find_one({"checkout_session_id": sid})
    assert sa.post(f"/api/super-admin/subscriptions/{sub['_id']}/confirm").status_code == 200
    out["sub"] = db.subscriptions.find_one({"_id": sub["_id"]})
    out["commission"] = db.partner_commissions.find_one({"subscription_id": str(sub["_id"]), "kind": "commission"})
    return out


def _clean_ip_referral(db, org_id):
    """One test client = one IP, so referrals look suspicious; clear the flag
    for flows that test the automatic lifecycle (the review path is tested separately)."""
    db.partner_referrals.update_one({"organization_id": org_id}, {"$set": {"suspicious": False}})
    db.partner_commissions.update_many({"organization_id": org_id}, {"$set": {"requires_manual_approval": False}})


# ── funnel + commission + wallet + payout + refund ───────────────────────────

def test_full_funnel_commission_wallet_payout_and_refunds(env):
    client, db = env
    sa = _sa(client)
    _settings(sa, commission_hold_days=0, min_payout=1)
    p, portal = _partner(client, sa, "ana@aff.example", name="Ana", company="Ana Media")
    visitor = TestClient(client.app)
    visitor.get(f"/r/{p['referral_code']}", follow_redirects=False)
    r = visitor.post("/api/auth/signup", json={"email": "own@cafe.example", "password": CUST_PW, "name": "O",
                                               "company": "Blue Cafe", "accepted_terms": True})
    org_id = db.users.find_one({"email": "own@cafe.example"})["default_organization_id"]
    ref = db.partner_referrals.find_one({"organization_id": org_id})
    assert ref["stage"] == "signed_up"
    sa.post(f"/api/super-admin/demo-requests/{r.json()['demo_request_id']}/approve", json={})
    assert db.partner_referrals.find_one({"organization_id": org_id})["stage"] == "demo"
    owner = _login(client, "own@cafe.example", CUST_PW)
    plan = _paid_plan(owner)
    co = owner.post("/api/billing/checkout", json={"plan_slug": plan["slug"]}).json()["checkout"]
    assert db.partner_referrals.find_one({"organization_id": org_id})["stage"] == "subscription"
    owner.post(f"/api/billing/checkout/{co['session_id']}/mock-pay", json={"succeed": True})
    assert db.partner_referrals.find_one({"organization_id": org_id})["stage"] == "payment"
    assert db.partner_commissions.count_documents({"organization_id": org_id}) == 0   # not before confirmation
    sub = db.subscriptions.find_one({"checkout_session_id": co["session_id"]})
    sa.post(f"/api/super-admin/subscriptions/{sub['_id']}/confirm")
    ref = db.partner_referrals.find_one({"organization_id": org_id})
    assert ref["stage"] == "customer" and ref["converted_at"]
    assert [e["stage"] for e in ref["events"]][:5] == ["signed_up", "demo", "subscription", "payment", "customer"]
    com = db.partner_commissions.find_one({"organization_id": org_id})
    inv = db.invoices.find_one({"subscription_id": str(sub["_id"])})
    assert com["invoice_id"] == str(inv["_id"]) and com["status"] == "pending"
    price = float(plan["price_monthly"])
    assert com["amount"] == round(price * 0.2, 2)

    # partner sees the conversion history, not the customer's contact data
    detail = portal.get(f"/api/partner/v1/referrals/{ref['_id']}").json()["referral"]
    assert [e["stage"] for e in detail["events"]][:5] == ["signed_up", "demo", "subscription", "payment", "customer"]
    assert "own@cafe.example" not in json.dumps(detail)

    # lifecycle: pending -> qualified -> (flagged) approved by Super Admin -> payable
    from app.partners.commissions import release_matured
    release_matured()
    assert db.partner_commissions.find_one({"_id": com["_id"]})["status"] == "qualified"
    sa.post(f"/api/super-admin/partners/commissions/{com['_id']}/approve")
    assert db.partner_commissions.find_one({"_id": com["_id"]})["status"] == "payable"

    # payout fails -> the money is payable again; second payout is paid
    po = portal.post("/api/partner/v1/payouts", json={}).json()["payout"]
    url = f"/api/super-admin/partners/payouts/{po['id']}"
    sa.post(url + "/approve", json={}); sa.post(url + "/process", json={})
    assert sa.post(url + "/fail", json={"reason": ""}).status_code == 422
    assert sa.post(url + "/fail", json={"reason": "Bank rejected"}).json()["payout"]["status"] == "failed"
    assert db.partner_commissions.find_one({"_id": com["_id"]})["status"] == "payable"
    po = portal.post("/api/partner/v1/payouts", json={}).json()["payout"]
    url = f"/api/super-admin/partners/payouts/{po['id']}"
    sa.post(url + "/approve", json={})
    assert sa.post(url + "/mark_paid", json={"reference": "UTR-1"}).json()["payout"]["status"] == "paid"
    assert db.partner_commissions.find_one({"_id": com["_id"]})["status"] == "paid"

    # full refund of a PAID commission's payment -> clawback (paid history intact)
    r = sa.post(f"/api/super-admin/invoices/{inv['_id']}/refund", json={"reason": "Customer refund"})
    assert r.status_code == 200, r.text
    assert r.json()["refund"]["partner_commission"]["clawback"] == com["amount"]
    assert db.invoices.find_one({"_id": inv["_id"]})["status"] == "refunded"
    assert db.payments.find_one({"subscription_id": str(sub["_id"])})["status"] == "refunded"
    assert db.partner_commissions.find_one({"_id": com["_id"]})["status"] == "paid"
    claw = db.partner_commissions.find_one({"kind": "clawback", "clawback_of": str(com["_id"])})
    assert claw["amount"] == -com["amount"] and claw["status"] == "payable"
    w = portal.get("/api/partner/v1/wallet").json()["wallet"]["balances"][com["currency"]]
    assert w["available"] == -com["amount"] and w["reversed"] == com["amount"] and w["paid"] == com["amount"]
    assert sa.post(f"/api/super-admin/invoices/{inv['_id']}/refund", json={"reason": "again"}).status_code == 409
    # the clawback is deducted: a negative balance can never be paid out
    assert portal.post("/api/partner/v1/payouts", json={}).status_code == 422

    # renewal -> recurring commission; PARTIAL refund before payout -> proportional reduction
    from app.db.models import utcnow
    from app.lifecycle.maintenance import renew_subscription
    db.subscriptions.update_one({"_id": sub["_id"]}, {"$set": {"current_period_end": utcnow() + timedelta(days=1)}})
    renew_subscription(db, db.subscriptions.find_one({"_id": sub["_id"]}), source="test", amount=price)
    ren = db.partner_commissions.find_one({"subscription_id": str(sub["_id"]), "event": "renewal"})
    ren_inv = db.invoices.find_one({"subscription_id": str(sub["_id"]), "source": "test"})
    assert ren["invoice_id"] == str(ren_inv["_id"])
    r = sa.post(f"/api/super-admin/invoices/{ren_inv['_id']}/refund", json={"amount": price / 2, "reason": "half"})
    assert r.status_code == 200
    ren = db.partner_commissions.find_one({"_id": ren["_id"]})
    assert ren["status"] == "pending" and ren["amount"] == round(ren["original_amount"] / 2, 2)
    assert db.invoices.find_one({"_id": ren_inv["_id"]})["status"] == "partially_refunded"

    # ledger carries customer / subscription / invoice references; super admin views reconcile
    led = sa.get(f"/api/super-admin/partners/{p['id']}/ledger").json()
    assert any(t["type"] == "clawback" and t["invoice_id"] == str(inv["_id"]) for t in led["items"])
    assert led["balances"][com["currency"]]["available"] == -com["amount"]
    assert sa.get("/api/super-admin/partners/reversals").json()["total"] >= 2
    wallets = sa.get("/api/super-admin/partners/wallets").json()["items"]
    assert any(x["id"] == p["id"] for x in wallets)
    # every sensitive step is in the audit trail
    acts = {a["action"] for a in sa.get(f"/api/super-admin/partners/{p['id']}/audit?limit=200").json()["items"]}
    assert {"partner.commission.approved", "partner.payout.fail", "partner.payout.mark_paid",
            "partner.commission.refund_reversal"} <= acts
    assert db.audit_logs.find_one({"action": "payment.refunded"})


def test_provider_refund_and_chargeback_webhooks(env, monkeypatch):
    client, db = env
    sa = _sa(client)
    _settings(sa, commission_hold_days=0)
    p, _ = _partner(client, sa, "web@aff.example", name="Web")
    c = _referred_customer(client, sa, db, p, "own@hook.example", "Hook Co")
    from app.admin import envvars
    monkeypatch.setattr(envvars, "get_envvar_str",
                        lambda name, fallback="": "whsec_test" if name == "BILLING_WEBHOOK_SECRET" else fallback)
    pay = db.payments.find_one({"subscription_id": str(c["sub"]["_id"])})
    paid_minor = int(round(c["sub"]["amount"] * 100))

    def send(event):
        body = json.dumps(event).encode()
        sig = hmac.new(b"whsec_test", body, hashlib.sha256).hexdigest()
        return client.post("/api/billing/webhook", content=body, headers={"x-leadai-signature": sig,
                                                                          "content-type": "application/json"})
    r = send({"id": "evt_ref_1", "type": "charge.refunded",
              "data": {"object": {"payment_intent": pay["provider_payment_id"], "amount_refunded": paid_minor // 4}}})
    assert r.status_code == 200, r.text
    com = db.partner_commissions.find_one({"_id": c["commission"]["_id"]})
    assert com["reversed_amount"] == round(c["commission"]["amount"] / 4, 2)
    assert send({"id": "evt_ref_1", "type": "charge.refunded",
                 "data": {"object": {"payment_intent": pay["provider_payment_id"],
                                     "amount_refunded": paid_minor // 4}}}).json()["status"] == "duplicate_skipped"
    r = send({"id": "evt_dsp_1", "type": "charge.dispute.created",
              "data": {"object": {"payment_intent": pay["provider_payment_id"], "amount": paid_minor * 3 // 4}}})
    assert r.status_code == 200, r.text
    com = db.partner_commissions.find_one({"_id": c["commission"]["_id"]})
    assert com["status"] == "reversed" and com["chargeback"] is True
    assert db.partner_referrals.find_one({"organization_id": c["org_id"]})["suspicious"] is True
    assert db.invoices.find_one({"subscription_id": str(c["sub"]["_id"])})["status"] == "refunded"


def test_cancellation_during_qualification_reverses(env):
    client, db = env
    sa = _sa(client)
    _settings(sa, commission_hold_days=30)
    p, _ = _partner(client, sa, "can@aff.example", name="Can")
    c = _referred_customer(client, sa, db, p, "own@cancel.example", "Cancel Co")
    r = sa.post(f"/api/super-admin/subscriptions/{c['sub']['_id']}/status", json={"status": "cancelled", "reason": "x"})
    assert r.status_code == 200, r.text
    com = db.partner_commissions.find_one({"_id": c["commission"]["_id"]})
    assert com["status"] == "reversed" and com["reversal_source"] == "cancellation"
    ref = db.partner_referrals.find_one({"organization_id": c["org_id"]})
    assert ref["churned_at"] and ref["events"][-1]["stage"] == "churned"


# ── commission rules ─────────────────────────────────────────────────────────

def test_hybrid_one_time_plan_specific_and_caps(env):
    client, db = env
    sa = _sa(client)
    _settings(sa, commission_hold_days=0)
    p, _ = _partner(client, sa, "rule@aff.example", name="Rule")
    owner_probe = _referred_customer(client, sa, db, p, "probe@rule.example", "Probe Co", pay=False)
    plan = owner_probe["plan"]
    price = float(plan["price_monthly"])
    # hybrid partner rule for this plan only: 10% + 5, max 7 per payment, one-time
    r = sa.post("/api/super-admin/partners/commission-rules", json={
        "name": "VIP", "scope": "partner", "partner_id": p["id"], "commission_type": "hybrid", "value": 10,
        "fixed_amount": 5, "max_per_payment": 7, "recurring": False, "plan_slugs": [plan["slug"]]})
    assert r.status_code == 200, r.text
    assert sa.post("/api/super-admin/partners/commission-rules", json={
        "scope": "global", "commission_type": "hybrid", "value": 0, "fixed_amount": 0}).status_code == 422
    c = _referred_customer(client, sa, db, p, "own@rule.example", "Rule Co")
    expected = round(min(price * 0.10 + 5, 7), 2)
    assert c["commission"]["amount"] == expected and c["commission"]["rate_type"] == "hybrid"
    from app.db.models import utcnow
    from app.lifecycle.maintenance import renew_subscription
    db.subscriptions.update_one({"_id": c["sub"]["_id"]}, {"$set": {"current_period_end": utcnow() + timedelta(days=1)}})
    renew_subscription(db, db.subscriptions.find_one({"_id": c["sub"]["_id"]}), source="t", amount=price)
    assert db.partner_commissions.count_documents({"subscription_id": str(c["sub"]["_id"]), "kind": "commission"}) == 1
    assert db.audit_logs.find_one({"action": "partner.commission.skipped", "details.reason": "one_time_rule"})


def test_weekly_payout_schedule_holds_approved(env):
    client, db = env
    sa = _sa(client)
    from app.db.models import utcnow
    other_day = (utcnow().weekday() + 3) % 7
    _settings(sa, commission_hold_days=0, payout_schedule="weekly", payout_day=other_day)
    p, _ = _partner(client, sa, "wk@aff.example", name="Week")
    c = _referred_customer(client, sa, db, p, "own@week.example", "Week Co")
    _clean_ip_referral(db, c["org_id"])
    from app.partners.commissions import release_matured
    release_matured()
    assert db.partner_commissions.find_one({"_id": c["commission"]["_id"]})["status"] == "approved"
    # Super Admin can release it before the payout day
    assert sa.post(f"/api/super-admin/partners/commissions/{c['commission']['_id']}/payable").status_code == 200
    assert db.partner_commissions.find_one({"_id": c["commission"]["_id"]})["status"] == "payable"
    assert sa.put("/api/super-admin/partners/settings", json={"payout_schedule": "daily"}).status_code == 422


# ── coupons & partner pricing ────────────────────────────────────────────────

def test_coupon_eligibility_and_commission_basis(env):
    client, db = env
    sa = _sa(client)
    _settings(sa, commission_hold_days=0)
    p, _ = _partner(client, sa, "cp@aff.example", name="Cp")
    other, _ = _partner(client, sa, "cp2@aff.example", name="Other")
    assert sa.post("/api/super-admin/partners/coupons", json={
        "partner_id": p["id"], "code": "LISTBASIS", "discount_value": 50, "commission_basis": "list"}).status_code == 200
    assert sa.post("/api/super-admin/partners/coupons", json={
        "partner_id": p["id"], "code": "NOCOMM", "discount_value": 10, "commission_basis": "none"}).status_code == 200
    assert sa.post("/api/super-admin/partners/coupons", json={
        "partner_id": other["id"], "code": "MINEONLY", "discount_value": 10,
        "eligibility": "referred_customers"}).status_code == 200
    c = _referred_customer(client, sa, db, p, "own@list.example", "List Co", coupon="LISTBASIS")
    price = float(c["plan"]["price_monthly"])
    assert c["checkout"]["amount"] == round(price / 2, 2)
    assert c["commission"]["base_amount"] == round(price, 2)                  # commission on the list price
    assert c["commission"]["amount"] == round(price * 0.2, 2)
    c2 = _referred_customer(client, sa, db, p, "own@none.example", "None Co", coupon="NOCOMM")
    assert c2["commission"] is None
    assert db.audit_logs.find_one({"action": "partner.commission.skipped", "details.reason": "coupon_excludes_commission"})
    # referred-customers-only coupon of ANOTHER partner is refused for p's customer
    c3 = _referred_customer(client, sa, db, p, "own@elig.example", "Elig Co", pay=False)
    r = c3["owner"].post("/api/billing/checkout", json={"plan_slug": c3["plan"]["slug"], "coupon_code": "MINEONLY"})
    assert r.status_code == 422 and "referred" in r.text
    # new-customers-only coupon is refused after a confirmed subscription
    assert sa.post("/api/super-admin/partners/coupons", json={
        "partner_id": p["id"], "code": "NEWONLY", "discount_value": 5, "eligibility": "new_customers"}).status_code == 200
    r = c["owner"].post("/api/billing/checkout", json={"plan_slug": c["plan"]["slug"], "coupon_code": "NEWONLY"})
    assert r.status_code == 422 and "new customers" in r.text


def test_partner_pricing_applies_best_single_discount(env):
    client, db = env
    sa = _sa(client)
    p, _ = _partner(client, sa, "pr@aff.example", name="Pricing")
    r = sa.post("/api/super-admin/partners/pricing", json={"name": "Partner deal", "scope": "partner",
                                                          "partner_id": p["id"], "discount_type": "percentage",
                                                          "discount_value": 15})
    assert r.status_code == 200, r.text
    sa.post("/api/super-admin/partners/coupons", json={"partner_id": p["id"], "code": "SMALL5", "discount_value": 5})
    c = _referred_customer(client, sa, db, p, "own@price.example", "Price Co", pay=False)
    price = float(c["plan"]["price_monthly"])
    co = c["owner"].post("/api/billing/checkout", json={"plan_slug": c["plan"]["slug"], "coupon_code": "SMALL5"}).json()["checkout"]
    assert co["amount"] == round(price * 0.85, 2)        # 15% pricing beats the 5% coupon, never both
    sub = db.subscriptions.find_one({"checkout_session_id": co["session_id"]})
    assert sub["partner_pricing"]["name"] == "Partner deal" and sub["coupon"] is None
    # a non-referred organization gets the list price
    from tests.conftest import seed_site_account
    seed_site_account("plain@org.example", org_status="demo")
    from app.auth.crypto import hash_password
    db.users.update_one({"email": "plain@org.example"}, {"$set": {"password_hash": hash_password(CUST_PW)}})
    plain = _login(client, "plain@org.example", CUST_PW)
    assert plain.post("/api/billing/checkout", json={"plan_slug": c["plan"]["slug"]}).json()["checkout"]["amount"] == price


# ── tiers ────────────────────────────────────────────────────────────────────

def test_tier_requirements_upgrade_downgrade_lock_and_permissions(env):
    client, db = env
    sa = _sa(client)
    _settings(sa, commission_hold_days=0, auto_tiering=True, partner_coupons_enabled=True,
              max_partner_coupon_percent=30)
    gold = sa.post("/api/super-admin/partners/tiers", json={
        "name": "Gold", "order": 3, "requirements": {"min_customers": 1}, "benefits": ["Priority support"],
        "max_customers": 1, "can_create_coupons": True, "max_coupon_percent": 25}).json()["tier"]
    bronze = sa.post("/api/super-admin/partners/tiers", json={
        "name": "Bronze", "order": 2, "requirements": {"min_referrals": 1}, "can_create_coupons": False}).json()["tier"]
    p, portal = _partner(client, sa, "tier@res.example", ptype="reseller", name="Tier",
                         permissions=["dashboard.view", "referral_links.create", "customers.view", "customers.create",
                                      "reseller.customers.manage", "coupons.manage", "profile.manage"])
    from app.partners.tiers import evaluate_tiers
    assert evaluate_tiers() == []                                  # nothing earned yet
    _referred_customer(client, sa, db, p, "own@t1.example", "T1 Co", pay=False)
    ch = sa.post("/api/super-admin/partners/tiers/evaluate").json()["changes"]
    assert ch and ch[0]["tier"] == "Bronze" and ch[0]["direction"] == "upgrade"
    # Bronze tier forbids partner coupons
    r = portal.post("/api/partner/v1/coupons", json={"code": "TIERX", "discount_value": 10})
    assert r.status_code == 403
    me = portal.get("/api/partner/v1/me").json()["me"]
    assert me["tier"]["name"] == "Bronze" and me["next_tier"]["name"] == "Gold"
    c = _referred_customer(client, sa, db, p, "own@t2.example", "T2 Co")
    assert c["commission"]
    assert sa.post("/api/super-admin/partners/tiers/evaluate").json()["changes"][0]["tier"] == "Gold"
    # Gold: coupons up to 25% (program cap 30) and at most 1 managed customer
    assert portal.post("/api/partner/v1/coupons", json={"code": "GOLD30", "discount_value": 30}).status_code == 422
    assert portal.post("/api/partner/v1/coupons", json={"code": "GOLD25", "discount_value": 25}).status_code == 200
    assert portal.post("/api/partner/v1/customers", json={"company": "M1", "name": "A", "email": "m1@m.example"}).status_code == 200
    r = portal.post("/api/partner/v1/customers", json={"company": "M2", "name": "B", "email": "m2@m.example"})
    assert r.status_code == 422 and "limit" in r.text
    # downgrade only when allowed; a hand-picked tier is locked
    db.partner_commissions.update_many({"partner_id": p["id"]}, {"$set": {"status": "reversed"}})
    db.partner_referrals.update_many({"partner_id": p["id"], "stage": "customer"}, {"$set": {"stage": "demo"}})
    assert sa.post("/api/super-admin/partners/tiers/evaluate").json()["changes"] == []
    _settings(sa, allow_tier_downgrade=True)
    assert sa.post("/api/super-admin/partners/tiers/evaluate").json()["changes"][0]["tier"] == "Bronze"
    sa.patch(f"/api/super-admin/partners/{p['id']}", json={"tier_id": gold["id"]})
    assert db.partners.find_one({"_id": ObjectId(p["id"])})["tier_locked"] is True
    assert sa.post("/api/super-admin/partners/tiers/evaluate").json()["changes"] == []
    assert bronze["requirements"]["min_referrals"] == 1


# ── reseller onboarding links ────────────────────────────────────────────────

def test_reseller_onboarding_link_creates_managed_customer(env):
    client, db = env
    sa = _sa(client)
    _settings(sa, commission_hold_days=0)
    p, portal = _partner(client, sa, "res@onb.example", ptype="reseller", name="Res")
    camp = portal.post("/api/partner/v1/campaigns", json={"name": "Client onboarding", "kind": "onboarding"}).json()["campaign"]
    assert camp["kind"] == "onboarding" and camp["landing_path"] == "/request-demo"
    _, aff = _partner(client, sa, "aff@onb.example", name="Aff")
    assert aff.post("/api/partner/v1/campaigns", json={"name": "x", "kind": "onboarding"}).status_code == 403
    v = TestClient(client.app)
    v.get(f"/r/{p['referral_code']}/{camp['slug']}", follow_redirects=False)
    v.post("/api/auth/signup", json={"email": "own@client.example", "password": CUST_PW, "name": "C",
                                     "company": "Client Co", "accepted_terms": True})
    org_id = db.users.find_one({"email": "own@client.example"})["default_organization_id"]
    ref = db.partner_referrals.find_one({"organization_id": org_id})
    assert ref["managed"] is True and ref["source"] == "onboarding_link"
    lst = portal.get("/api/partner/v1/customers?managed=true").json()
    assert lst["total"] == 1 and lst["items"][0]["company"] == "Client Co"


# ── Super Admin relationship & fraud management ──────────────────────────────

def test_reassign_and_invalidate_referrals(env):
    client, db = env
    sa = _sa(client)
    _settings(sa, commission_hold_days=0)
    a, portal_a = _partner(client, sa, "a@rel.example", name="A")
    b, portal_b = _partner(client, sa, "b@rel.example", name="B")
    c = _referred_customer(client, sa, db, a, "own@rel.example", "Rel Co")
    ref = db.partner_referrals.find_one({"organization_id": c["org_id"]})
    fraud = sa.get("/api/super-admin/partners/fraud").json()
    assert any(x["id"] == str(ref["_id"]) for x in fraud["flagged_referrals"])
    assert sa.post(f"/api/super-admin/partners/referrals/{ref['_id']}/reassign",
                   json={"partner_id": b["id"], "reason": ""}).status_code == 422
    r = sa.post(f"/api/super-admin/partners/referrals/{ref['_id']}/reassign",
                json={"partner_id": b["id"], "reason": "Customer confirmed B introduced them"})
    assert r.status_code == 200, r.text
    assert db.partner_commissions.find_one({"_id": c["commission"]["_id"]})["partner_id"] == b["id"]
    assert portal_a.get("/api/partner/v1/referrals").json()["total"] == 0
    assert portal_b.get("/api/partner/v1/referrals").json()["total"] == 1
    # partner A can no longer open it
    assert portal_a.get(f"/api/partner/v1/referrals/{ref['_id']}").status_code == 404
    # fraud review: invalidate -> every commission reversed, org detached
    r = sa.post(f"/api/super-admin/partners/referrals/{ref['_id']}/review", json={"decision": "invalidate",
                                                                                  "reason": "Fake signup"})
    assert r.status_code == 200 and r.json()["referral"]["status"] == "invalid"
    assert db.partner_commissions.find_one({"_id": c["commission"]["_id"]})["status"] == "reversed"
    assert "referred_by_partner_id" not in db.organizations.find_one({"_id": ObjectId(c["org_id"])})
    rel = sa.get("/api/super-admin/partners/referrals?status=invalid").json()
    assert rel["total"] == 1


# ── isolation across every portal ────────────────────────────────────────────

def test_partner_data_never_leaks_to_org_admins_users_or_other_partners(env):
    client, db = env
    sa = _sa(client)
    a, portal_a = _partner(client, sa, "iso@a.example", ptype="reseller", name="Iso A")
    _, portal_b = _partner(client, sa, "iso@b.example", ptype="reseller", name="Iso B")
    c = _referred_customer(client, sa, db, a, "own@iso.example", "Iso Co")
    ref = db.partner_referrals.find_one({"organization_id": c["org_id"]})
    # Partner B: no referral detail, customer, commission or org data of A
    assert portal_b.get(f"/api/partner/v1/referrals/{ref['_id']}").status_code == 404
    assert portal_b.get(f"/api/partner/v1/customers/{c['org_id']}").status_code == 404
    assert portal_b.get("/api/partner/v1/commissions").json()["total"] == 0
    # resellers can't reach Super Admin or customer-org data
    for path in ("/api/super-admin/partners/wallets", "/api/super-admin/invoices", "/api/org-admin/overview",
                 "/api/leads", "/api/billing/subscription"):
        assert portal_a.get(path).status_code in (401, 403), path
    assert portal_a.post(f"/api/super-admin/invoices/{ObjectId()}/refund", json={"reason": "x"}).status_code == 401
    # partners can never change commissions or balances
    assert portal_a.post(f"/api/super-admin/partners/commissions/{c['commission']['_id']}/approve").status_code == 401
    assert portal_a.post(f"/api/super-admin/partners/{a['id']}/adjustments",
                         json={"amount": 100, "reason": "x"}).status_code == 401
    # the referred organization's owner/admin sees nothing about the partner program
    owner = c["owner"]
    db.organizations.update_one({"_id": ObjectId(c["org_id"])}, {"$set": {"admin_portal_enabled": True}})
    owner = _login(client, "own@iso.example", CUST_PW)
    from app.main import app
    from fastapi.routing import APIRoute
    secrets_ = [a["id"], a["referral_code"], a["partner_code"], "iso@a.example", str(ref["_id"])]
    def walk(routes):
        for rt in routes:
            if isinstance(rt, APIRoute):
                yield rt
            elif hasattr(rt, "original_router"):
                yield from walk(rt.original_router.routes)
    checked = 0
    for route in walk(app.routes):
        if isinstance(route, APIRoute) and "GET" in route.methods and "{" not in route.path and (
                route.path.startswith("/api/org-admin/") or route.path in ("/api/auth/me", "/api/billing/subscription",
                                                                       "/api/billing/invoices", "/api/notifications")):
            r = owner.get(route.path)
            if r.status_code == 200:
                checked += 1
                for sec in secrets_:
                    assert sec not in r.text, (route.path, sec)
    assert checked >= 10
    # nor can the org owner reach the partner portal / super admin partner APIs
    assert owner.get("/api/partner/v1/dashboard").status_code == 401
    assert owner.get("/api/super-admin/partners").status_code == 401
