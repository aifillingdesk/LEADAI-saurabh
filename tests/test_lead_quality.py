"""
Lead qualification quality on real-world comment shapes (Hinglish / Hindi
real-estate comments, the page's own replies, competing brokers' ads) —
rule stage only, in-memory MongoDB, no AI calls.

Proves:
  * price / location questions in English, Hinglish and Devanagari are
    leads with a pricing / inquiry intent;
  * the page's own comments (replies to customers, its listings) and
    promotional comments with a number to call are never leads, and are
    dropped before any AI call;
  * a comment judged not useful is never a lead, whatever it contains;
  * an active keyword filter that matches nothing analyzes nothing;
  * secrets in log lines (API keys in URLs) are masked.
"""
import logging

import pytest

from app.pipeline import comment_ai as ca

PAGE = "Gharenium"


def _flat(text, author="Some Buyer", page=PAGE):
    analysis = ca.rule_based_classify(text, author, page)
    return analysis, ca._flat_extract(analysis, text)


@pytest.mark.parametrize("text,intent", [
    ("Price kya hai", "pricing"),
    ("Price bhi bolo", "pricing"),
    ("Deepak Gujjar rate", "pricing"),
    ("9829140382 इसकी रेट कितनी है", "pricing"),
    ("इसकी कीमत क्या है?", "pricing"),
    ("Location batay khaper h", "inquiry"),
    ("ye ghar kahan hai", "inquiry"),
    ("What is the price of this flat?", "pricing"),
])
def test_price_and_location_questions_are_leads(text, intent):
    analysis, flat = _flat(text)
    assert analysis["is_useful"] is True
    assert flat["intent"] in (intent, ca.normalize_intent(intent))
    assert flat["is_lead"] is True and flat["lead_score"] > 0


def test_contact_number_with_price_question_scores_higher():
    _a, with_phone = _flat("9829140382 इसकी रेट कितनी है")
    _b, without = _flat("इसकी रेट कितनी है")
    assert with_phone["lead_score"] > without["lead_score"]
    assert with_phone["phone"] == "9829140382"


@pytest.mark.parametrize("text", [
    "Deepak Gujjar 🏡घर की सारी details आपको video के नीचे caption और first comment में मिल जाएगी",
    "🏡Deal Code:- G-0434\n🥳Details:- 25'x40'(2bhk) House in Gated Colony, price 45 lakh",
    "Call 9876543210 for price and site visit",
])
def test_page_owner_comments_are_never_leads(text):
    analysis, flat = _flat(text, author="Gharenium")
    assert analysis["is_useful"] is False and "page itself" in analysis["reason"]
    assert flat["is_lead"] is False and flat["lead_score"] == 0
    # name variants of the page are recognised too
    assert ca.is_page_owner_comment("GHARENIUM", "Gharenium")
    assert ca.is_page_owner_comment("Bharat Landmark Realty", "Bharat Landmark Realty  Reels") is False
    assert ca.is_page_owner_comment("Bharat Landmark Realty Reels", "Bharat Landmark Realty  Reels")


@pytest.mark.parametrize("text", [
    "Saste aur prime location k makan lene k liye call kare \n\n8559888886 Anil",
    "Best price flats available, call now 9812345678",
    "सस्ते प्लॉट उपलब्ध हैं संपर्क करें 9812345678",
])
def test_promotional_comments_are_not_leads(text):
    analysis, flat = _flat(text)
    assert analysis["is_useful"] is False and "Promotional" in analysis["reason"]
    assert flat["is_lead"] is False


@pytest.mark.parametrize("text", [
    "Mujhe 2bhk chahiye, call kare 9812345678",
    "Is this available? call 9812345678",
    "I want this house, my number 9812345678",
])
def test_buyers_sharing_a_number_are_not_mistaken_for_promotions(text):
    analysis, flat = _flat(text)
    assert analysis["is_useful"] is True
    assert flat["is_lead"] is True and flat["phone"]


def test_owner_and_promo_comments_skip_the_ai_call(monkeypatch):
    calls = []
    monkeypatch.setattr(ca, "_call_gemini", lambda *a, **kw: calls.append(1) or ({}, {}))
    monkeypatch.setattr(ca, "get_envvar_str", lambda *a, **kw: "test-key")
    r1 = ca.analyze_comment_ai("Meena Ji 🏡घर की सारी details आपको video के नीचे", "Gharenium",
                               page_name="Gharenium")
    r2 = ca.analyze_comment_ai("Saste makan lene k liye call kare 8559888886", "Anil Varma",
                               page_name="Gharenium")
    assert r1["analyzed_by"] == "rules" and r2["analyzed_by"] == "rules"
    assert calls == []


def test_empty_keyword_match_analyzes_nothing():
    from app.db.mongo import get_sync_db
    db = get_sync_db()
    page = db.facebook_pages.insert_one({"page_name": PAGE}).inserted_id
    post = db.facebook_posts.insert_one({"page_ref": str(page), "platform": "facebook",
                                         "post_url": "https://facebook.com/p/1"}).inserted_id
    db.facebook_comments.insert_one({"post_ref": str(post), "text": "Price kya hai",
                                     "author_name": "Buyer"})
    none_matched = ca.analyze_comments_for_post(str(post), comment_refs=[])
    assert none_matched["analyzed"] == 0 and db.ai_comments.count_documents({}) == 0
    unfiltered = ca.analyze_comments_for_post(str(post), comment_refs=None)
    assert unfiltered["analyzed"] == 1 and unfiltered["useful"] == 1


def test_page_replies_inside_a_real_post_batch():
    """End to end through analyze_comments_for_post: the page name comes from
    the stored page, so its replies on its own post are not leads."""
    from app.db.mongo import get_sync_db
    db = get_sync_db()
    page = db.facebook_pages.insert_one({"page_name": PAGE}).inserted_id
    post = db.facebook_posts.insert_one({"page_ref": str(page), "platform": "facebook",
                                         "post_url": "https://facebook.com/p/2"}).inserted_id
    for author, text in (("Ruby Mittal", "Price kya hai"),
                         ("Gharenium", "Ruby Mittal 🏡घर की सारी details आपको video के नीचे"),
                         ("Anil Varma", "Saste makan lene k liye call kare 8559888886"),
                         ("Madan Gupta", "")):
        db.facebook_comments.insert_one({"post_ref": str(post), "text": text, "author_name": author})
    summary = ca.analyze_comments_for_post(str(post))
    assert summary["analyzed"] == 4 and summary["useful"] == 1
    leads = list(db.ai_comments.find({"post_ref": str(post), "is_lead": True}))
    assert [d["commenter_name"] for d in leads] == ["Ruby Mittal"]


def test_log_lines_never_contain_api_keys():
    from app.main import _SecretRedactingFilter
    record = logging.LogRecord("httpx", logging.INFO, __file__, 1,
                               'HTTP Request: POST https://x.googleapis.com/v1/m:generate?key=%s "200"',
                               ("AQ.SECRETSECRET123",), None)
    assert _SecretRedactingFilter().filter(record) is True
    assert "SECRETSECRET" not in record.getMessage() and "key=***" in record.getMessage()
    from app.log_parser import redact_secrets
    assert "SECRET" not in redact_secrets("GET https://api.example.com/a?key=SECRET999&x=1")


def test_link_with_a_space_is_rejected_before_scraping():
    from app.social.url_detector import UrlError, detect_social_url
    with pytest.raises(UrlError) as exc:
        detect_social_url("https://www.facebook.com/KOTA PROPERTY")
    assert exc.value.kind == "invalid" and "space" in exc.value.message
    # trailing whitespace around a real link is still fine
    assert detect_social_url(" facebook.com/Gharenium ") == ("facebook", "https://www.facebook.com/Gharenium")
