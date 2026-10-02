"""
Tests for Phase 4 Compliance, Privacy, Blocklist, and PII Retention.
"""
from datetime import timedelta

import pytest

from app.compliance import service as cs
from app.db.models import utcnow


@pytest.fixture
def mock_db():
    import mongomock
    client = mongomock.MongoClient()
    return client["lead_ai_test"]


def test_normalize_phone():
    assert cs._normalize_phone("+1 (555) 123-4567") == "15551234567"
    assert cs._normalize_phone("5551234") == "5551234"
    assert cs._normalize_phone("123") is None
    assert cs._normalize_phone(None) is None


def test_normalize_email():
    assert cs._normalize_email(" User@Example.COM ") == "user@example.com"
    assert cs._normalize_email("invalid-email") is None
    assert cs._normalize_email(None) is None


def test_blocklist_lifecycle(mock_db):
    # 1. Add phone to blocklist
    res = cs.add_to_blocklist(
        mock_db,
        contact_type="phone",
        contact_value="+1 555-987-6543",
        reason="Customer requested DNC",
        organization_id="org_123",
        actor_email="admin@test.com",
    )
    assert res["success"] is True
    assert res["value"] == "15559876543"

    # 2. Check is_blocked
    assert cs.is_blocked(mock_db, phone="+1 (555) 987-6543", organization_id="org_123") is True
    assert cs.is_blocked(mock_db, phone="+1 (555) 000-0000", organization_id="org_123") is False
    # Cross-tenant check: org_999 does not match org_123 private block
    assert cs.is_blocked(mock_db, phone="+1 (555) 987-6543", organization_id="org_999") is False

    # 3. Add platform-wide email block (organization_id=None)
    cs.add_to_blocklist(
        mock_db,
        contact_type="email",
        contact_value="spammer@example.com",
        organization_id=None,
    )
    assert cs.is_blocked(mock_db, email="spammer@example.com", organization_id="org_999") is True
    assert cs.is_blocked(mock_db, email="other@example.com", organization_id="org_999") is False

    # 4. List blocklist
    listing = cs.list_blocklist(mock_db, organization_id="org_123")
    assert listing["total"] >= 2  # includes private block and platform-wide block
    # Find org-specific entry to remove
    org_entries = [it for it in listing["items"] if it.get("organization_id") == "org_123"]
    assert len(org_entries) > 0
    entry_id = org_entries[0]["id"]

    # 5. Remove from blocklist
    removed = cs.remove_from_blocklist(mock_db, blocklist_id=entry_id, organization_id="org_123")
    assert removed is True


def test_blocklist_purges_existing_leads(mock_db):
    from bson import ObjectId
    lead1_id = ObjectId()
    lead2_id = ObjectId()

    # Insert existing leads
    mock_db["ai_comments"].insert_many([
        {
            "_id": lead1_id,
            "organization_id": "org_abc",
            "phone": "15551112222",
            "email": "lead1@example.com",
            "is_lead": True,
            "lead_quality": "high",
        },
        {
            "_id": lead2_id,
            "organization_id": "org_abc",
            "phone": "15553334444",
            "email": "lead2@example.com",
            "is_lead": True,
            "lead_quality": "high",
        },
    ])

    # Block phone 15551112222
    res = cs.add_to_blocklist(
        mock_db,
        contact_type="phone",
        contact_value="15551112222",
        organization_id="org_abc",
    )
    assert res["leads_purged"] == 1

    # Verify lead 1 was redacted and deactivated
    doc = mock_db["ai_comments"].find_one({"_id": lead1_id})
    assert doc is not None
    assert doc.get("is_lead") is False
    assert doc.get("compliance_blocked") is True
    assert "phone" not in doc
    assert "email" not in doc

    # Verify lead 2 is unchanged
    doc2 = mock_db["ai_comments"].find_one({"email": "lead2@example.com"})
    assert doc2 is not None
    assert doc2.get("is_lead") is True
    assert doc2.get("phone") == "15553334444"


def test_delete_prospect_data(mock_db):
    mock_db["ai_comments"].insert_one({
        "organization_id": "org_del",
        "phone": "15557778888",
        "email": "target@domain.com",
        "is_lead": True,
    })

    res = cs.delete_prospect_data(
        mock_db,
        organization_id="org_del",
        identifier="15557778888",
        identifier_type="phone",
        reason="GDPR Article 17 erasure request",
    )
    assert res["success"] is True
    assert res["leads_purged"] == 1
    assert cs.is_blocked(mock_db, phone="15557778888", organization_id="org_del") is True


def test_purge_expired_pii(mock_db):
    now = utcnow()
    old_date = now - timedelta(days=95)
    recent_date = now - timedelta(days=10)

    from bson import ObjectId
    org_id = ObjectId()
    mock_db["organizations"].insert_one({
        "_id": org_id,
        "name": "Acme Corp",
        "status": "active",
        "settings": {"pii_retention_days": 90},
    })

    # Insert old record and recent record
    mock_db["ai_comments"].insert_many([
        {
            "_id": ObjectId(),
            "organization_id": str(org_id),
            "phone": "15559990000",
            "email": "old@example.com",
            "created_at": old_date,
            "is_lead": True,
        },
        {
            "_id": ObjectId(),
            "organization_id": str(org_id),
            "phone": "15558880000",
            "email": "recent@example.com",
            "created_at": recent_date,
            "is_lead": True,
        },
    ])

    res = cs.purge_expired_pii(mock_db, organization_id=str(org_id))
    assert res["success"] is True
    assert res["purged_records"] == 1

    old_doc = mock_db["ai_comments"].find_one({"email": "old@example.com"})
    assert old_doc is None  # email unset!
    purged_doc = mock_db["ai_comments"].find_one({"pii_purged": True})
    assert purged_doc is not None
    assert "phone" not in purged_doc
    assert "email" not in purged_doc

    recent_doc = mock_db["ai_comments"].find_one({"email": "recent@example.com"})
    assert recent_doc is not None
    assert recent_doc.get("phone") == "15558880000"


def test_compliance_notice():
    notice = cs.get_compliance_notice()
    assert "notice_title" in notice
    assert "terms_of_service_disclaimer" in notice
    assert len(notice["rights_supported"]) >= 4
