"""
Phase 3 Data Model & Code Cleanup Test Suite
Validates:
- Canonical intent normalization and alias resolution
- Lead lifecycle state machine relaxation and reopen reason enforcement
- Superadmin bcrypt hash requirement in production
- Social collections compatibility layer and document normalization
"""
from unittest.mock import MagicMock, patch

from app.pipeline.comment_ai import normalize_intent, CANONICAL_INTENTS
from app.pipeline.lead_lifecycle import validate_transition
from app.auth.superadmin import _verify_secret
from app.db.compatibility import normalize_social_doc, prepare_social_query, get_collection


# =========================================================================
# 1. Canonical Intent Normalization
# =========================================================================
def test_canonical_intents_pass_through():
    for intent in CANONICAL_INTENTS:
        assert normalize_intent(intent) == intent
        assert normalize_intent(intent.upper()) == intent


def test_legacy_intent_aliases():
    assert normalize_intent("buying") == "purchase_inquiry"
    assert normalize_intent("buy") == "purchase_inquiry"
    assert normalize_intent("booking") == "purchase_inquiry"
    assert normalize_intent("pricing") == "pricing_inquiry"
    assert normalize_intent("price") == "pricing_inquiry"
    assert normalize_intent("broker_inquiry") == "partnership"
    assert normalize_intent("agent_collaboration") == "partnership"
    assert normalize_intent("support_request") == "support"
    assert normalize_intent("help") == "support"
    assert normalize_intent("job_inquiry") == "job_seeker"
    assert normalize_intent("career") == "job_seeker"
    assert normalize_intent("rent") == "other"
    assert normalize_intent("selling") == "other"
    assert normalize_intent("unknown_random_intent") == "other"
    assert normalize_intent(None) == "other"


# =========================================================================
# 2. Lead Lifecycle State Machine & Reopen Reasons
# =========================================================================
def test_new_relaxed_transitions_allowed():
    # contacted -> disqualified
    ok, err = validate_transition("contacted", "disqualified")
    assert ok is True
    assert not err

    # qualified -> converted
    ok, err = validate_transition("qualified", "converted")
    assert ok is True
    assert not err

    # follow_up -> converted
    ok, err = validate_transition("follow_up", "converted")
    assert ok is True
    assert not err


def test_reopen_lost_and_disqualified_requires_reason():
    # Reopen without reason should fail
    ok, err = validate_transition("lost", "new", reason="")
    assert ok is False
    assert "reason" in err.lower()

    ok, err = validate_transition("lost", "new", reason="")
    assert ok is False
    assert "reason" in err.lower()

    ok, err = validate_transition("disqualified", "contacted", reason="   ")
    assert ok is False
    assert "reason" in err.lower()

    # Reopen with valid reason should succeed
    ok, err = validate_transition("lost", "new", reason="Customer reached back out via email")
    assert ok is True
    assert not err

    ok, err = validate_transition("disqualified", "qualified", reason="Budget re-evaluated")
    assert ok is True
    assert not err


def test_regular_transitions_do_not_require_reason():
    ok, err = validate_transition("new", "contacted")
    assert ok is True
    assert not err

    ok, err = validate_transition("contacted", "qualified")
    assert ok is True
    assert not err


# =========================================================================
# 3. SuperAdmin Production Bcrypt Security
# =========================================================================
def _make_settings(env_value: str):
    """Return a mock settings object with the given env value."""
    s = MagicMock()
    s.env = env_value
    return s


def test_superadmin_production_rejects_plaintext():
    # In production, plaintext secret must be rejected outright
    with patch("app.auth.superadmin.get_settings", return_value=_make_settings("production")):
        assert _verify_secret("plain_secret", "plain_secret") is False


def test_superadmin_production_accepts_valid_bcrypt():
    import bcrypt
    pw = "SuperAdminPassword123!"
    salt = bcrypt.gensalt()
    bcrypt_hash = bcrypt.hashpw(pw.encode("utf-8"), salt).decode("utf-8")

    with patch("app.auth.superadmin.get_settings", return_value=_make_settings("production")):
        assert _verify_secret(pw, bcrypt_hash) is True
        assert _verify_secret("wrong_password", bcrypt_hash) is False


def test_superadmin_dev_allows_plaintext():
    with patch("app.auth.superadmin.get_settings", return_value=_make_settings("development")):
        assert _verify_secret("my_dev_secret", "my_dev_secret") is True
        assert _verify_secret("wrong", "my_dev_secret") is False


# =========================================================================
# 4. Compatibility Layer & Document Normalization
# =========================================================================
def test_normalize_social_doc_bi_directional_urls():
    doc = {"facebook_url": "https://facebook.com/testpage", "name": "Test Page"}
    normalized = normalize_social_doc(doc)
    assert normalized["page_url"] == "https://facebook.com/testpage"
    assert normalized["facebook_url"] == "https://facebook.com/testpage"
    assert normalized["platform"] == "facebook"

    doc2 = {"page_url": "https://instagram.com/testpage", "platform": "instagram"}
    normalized2 = normalize_social_doc(doc2)
    assert normalized2["page_url"] == "https://instagram.com/testpage"
    assert normalized2["facebook_url"] == "https://instagram.com/testpage"
    assert normalized2["platform"] == "instagram"


def test_prepare_social_query_url_expansion():
    query = {"page_url": "https://example.com/page"}
    prepared = prepare_social_query(query)
    assert "$or" in prepared
    assert {"page_url": "https://example.com/page"} in prepared["$or"]
    assert {"facebook_url": "https://example.com/page"} in prepared["$or"]


def test_get_collection_fallback():
    mock_db = MagicMock()
    mock_db.list_collection_names.return_value = ["facebook_pages", "users"]

    # When social_pages does not exist, fall back to facebook_pages
    coll = get_collection(mock_db, "social_pages")
    assert coll == mock_db["facebook_pages"]

    # When social_pages does exist, return social_pages
    mock_db.list_collection_names.return_value = ["social_pages", "facebook_pages"]
    coll = get_collection(mock_db, "social_pages")
    assert coll == mock_db["social_pages"]
