"""
A post's comments screen shows every collected comment, qualified or not, and
collecting all of a post's comments later never pays for AI twice.

Run:  python -m pytest tests/test_comment_qualification.py -q -p no:cacheprovider
"""
from unittest.mock import patch

import mongomock
import mongomock_motor
import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

pytestmark = pytest.mark.own_db


@pytest.fixture(scope="module")
def env():
    mclient = mongomock.MongoClient()
    aclient = mongomock_motor.AsyncMongoMockClient(mock_mongo_client=mclient)
    patches = [
        patch("app.db.mongo.get_sync_client", return_value=mclient),
        patch("app.db.mongo.get_async_client", return_value=aclient),
        patch("app.admin.settings.is_maintenance_enabled", return_value=False),
        patch("app.main.ensure_indexes", lambda: None),
    ]
    for p in patches:
        p.start()
    try:
        from app.auth.permissions import invalidate_permission_cache
        from app.config import get_settings
        from app.main import app

        invalidate_permission_cache()
        db = mclient[get_settings().mongo_db_name]
        with TestClient(app) as client:
            yield {"client": client, "db": db, **_seed(db)}
    finally:
        for p in reversed(patches):
            p.stop()


def _seed(db):
    from app.auth.service import build_session_value, create_tracked_session
    from app.pipeline import comment_filter as cf

    org = ObjectId()
    db.organizations.insert_one({"_id": org, "name": "Org Q", "slug": "org-q", "status": "active"})
    uid = ObjectId()
    email = "owner@q.example"
    db.users.insert_one({"_id": uid, "email": email, "name": "owner", "status": "active",
                         "default_organization_id": str(org)})
    db.organization_members.insert_one({"user_id": str(uid), "organization_id": str(org),
                                        "role": "owner", "status": "active"})
    cookie = build_session_value(create_tracked_session({
        "user_id": str(uid), "email": email, "name": "owner", "scope": "site",
        "organization_id": str(org), "org_role": "owner"}))
    owner = {"organization_id": str(org), "user_id": str(uid), "created_by": email}
    post = str(db.facebook_posts.insert_one({
        "post_url": "https://instagram.com/p/q1", "platform": "instagram", "caption": "flat for sale",
        "total_comment_count": 35, "scraped_comment_count": 3, "comments_status": "completed",
        **owner}).inserted_id)
    rows = [("price kitna hai flat ka?", cf.STATUS_MATCHED, True),
            ("nice video", cf.STATUS_NOT_MATCHED, False),
            ("wow", cf.STATUS_NOT_MATCHED, False)]
    comments = []
    for text, status, analyzed in rows:
        cid = str(db.facebook_comments.insert_one({
            "text": text, "author_name": text[:4], "post_ref": post,
            "keyword_filter_status": status, **owner}).inserted_id)
        comments.append(cid)
        if analyzed:
            db.ai_comments.insert_one({"comment_ref": cid, "post_ref": post, "comment_text": text,
                                       "analyzed_by": "gemini", "is_lead": True, "lead_score": 72, **owner})
    return {"cookie": cookie, "post": post, "comments": comments}


def _get(env, **params):
    from app.auth.service import COOKIE_NAME
    c = env["client"]
    c.cookies.set(COOKIE_NAME, env["cookie"])
    return c.get(f"/api/posts/{env['post']}/comments", params=params).json()


def test_all_comments_are_listed_with_their_qualification(env):
    data = _get(env)
    assert data["all_count"] == 3 and data["total"] == 3
    assert data["qualification_counts"] == {"qualified": 1, "not_qualified": 2, "pending": 0}
    by_text = {c["comment_text"]: c["qualification"] for c in data["comments"]}
    assert by_text == {"price kitna hai flat ka?": "qualified", "nice video": "not_qualified",
                       "wow": "not_qualified"}
    # the post says 35 comments exist: the screen offers to collect the rest
    assert data["total_comment_count"] == 35


def test_qualification_filter(env):
    q = _get(env, qualification="qualified")
    assert [c["comment_text"] for c in q["comments"]] == ["price kitna hai flat ka?"]
    nq = _get(env, qualification="not_qualified")
    assert {c["comment_text"] for c in nq["comments"]} == {"nice video", "wow"}
    assert nq["qualification_counts"]["not_qualified"] == 2 and nq["all_count"] == 3


def test_collecting_again_does_not_analyze_twice(env):
    """'Collect all comments' re-runs analysis for the post: comments Gemini
    already analyzed (same text) are skipped, new or edited ones are analyzed."""
    from app.pipeline.comment_ai import analyze_comments_for_post
    db = env["db"]
    new_id = str(db.facebook_comments.insert_one({
        "text": "flat available? send details", "author_name": "new", "post_ref": env["post"],
        "organization_id": db.facebook_posts.find_one({"_id": ObjectId(env["post"])})["organization_id"],
    }).inserted_id)
    seen = []

    def fake(text, *a, **k):
        seen.append(text)
        return {"analyzed_by": "rules", "is_lead": True, "lead_score": 60, "intent": "inquiry"}

    with patch("app.pipeline.comment_ai.analyze_comment_ai", side_effect=fake):
        summary = analyze_comments_for_post(env["post"], comment_refs=[env["comments"][0], new_id])
    assert seen == ["flat available? send details"]
    assert summary["already_analyzed"] == 1 and summary["analyzed"] == 1


def test_second_collect_click_while_running_is_not_charged(env):
    from app.api.routes import search as search_routes
    from app.auth.service import COOKIE_NAME

    class _Running:
        def done(self):
            return False

    key = f"comments:{env['post']}"
    c = env["client"]
    c.cookies.set(COOKIE_NAME, env["cookie"])
    with patch.dict(search_routes._tasks, {key: _Running()}), \
            patch.object(search_routes, "_charge_tokens") as charge:
        r = c.post(f"/api/posts/{env['post']}/comments?max_comments=35")
    assert r.status_code == 200 and r.json()["status"] == "running"
    charge.assert_not_called()


def test_everything_fetched_hides_collect_all(env):
    db = env["db"]
    db.facebook_posts.update_one({"_id": ObjectId(env["post"])}, {"$set": {"comments_all_fetched": True}})
    try:
        assert _get(env)["comments_all_fetched"] is True
    finally:
        db.facebook_posts.update_one({"_id": ObjectId(env["post"])}, {"$unset": {"comments_all_fetched": ""}})
