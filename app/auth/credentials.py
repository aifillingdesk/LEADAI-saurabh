"""Changing another account's sign-in credentials (email + password).

Used by the Organization Admin (for their own members) and by the Super Admin
(for any account). The routes decide WHO may act on WHOM; this module does
the change itself, the same way for everyone:

- passwords are validated (``validate_password``) and stored hashed only; a
  password is never shown again, logged or emailed
- an admin-set password is temporary by default: ``must_change_password``
  makes the person choose their own at the next sign-in
- every session of the account is revoked, the lockout counter is cleared and
  outstanding reset links are invalidated
- the person is emailed that an administrator changed their password / email
  (an email change notifies the old AND the new address)
- an email change keeps every copy of the address in sync: organization
  memberships and, for partners, the partner record and application

Two kinds of account exist: ``user`` (the ``users`` collection: organization
owners / admins / members, partners, platform staff with a users record) and
``staff`` (the ``admin_users`` collection: platform staff created in the
staff console). The env Super Admin (SUPERADMIN_EMAIL / SUPERADMIN_PASSWORD)
lives in the hosting environment and can never be changed here.
"""
import re
from typing import Any, Dict, Optional

from bson import ObjectId
from fastapi import HTTPException

from app.db.models import utcnow
from app.db.mongo import get_sync_db

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
KINDS = ("user", "staff")


def _db():
    db = get_sync_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return db


def _coll(kind: str) -> str:
    if kind not in KINDS:
        raise ValueError(f"unknown account kind {kind!r}")
    return "users" if kind == "user" else "admin_users"


def load_account(kind: str, account_id: str, db=None) -> Dict[str, Any]:
    db = db if db is not None else _db()
    oid = ObjectId(str(account_id)) if ObjectId.is_valid(str(account_id)) else None
    doc = db[_coll(kind)].find_one({"_id": oid}) if oid else None
    if not doc:
        raise HTTPException(status_code=404, detail="Account not found")
    return doc


def _guard(doc: Dict[str, Any], actor_email: str) -> None:
    from app.auth.superadmin import is_superadmin_email
    email = (doc.get("email") or "").lower()
    if is_superadmin_email(email):
        raise HTTPException(status_code=409, detail=(
            "The Super Admin's sign-in is set in the hosting environment "
            "(SUPERADMIN_EMAIL / SUPERADMIN_PASSWORD) and can't be changed here."))
    if email and email == (actor_email or "").lower():
        raise HTTPException(status_code=409, detail="Change your own sign-in from your account settings.")
    if doc.get("status") in ("archived", "deleted"):
        raise HTTPException(status_code=409, detail="This account is archived")


def _session_owner(kind: str, doc: Dict[str, Any]) -> str:
    # staff-console sessions carry no user id: they are stored as "env:<email>"
    return str(doc["_id"]) if kind == "user" else f"env:{(doc.get('email') or '').lower()}"


def _revoke_sessions(db, owner: str, by: str) -> int:
    res = db["user_sessions"].update_many({"user_id": owner, "revoked_at": None},
                                          {"$set": {"revoked_at": utcnow(), "revoked_by": by}})
    return int(res.modified_count or 0)


def email_in_use(email: str, db=None) -> bool:
    from app.auth.superadmin import is_superadmin_email
    db = db if db is not None else _db()
    email = (email or "").strip().lower()
    return bool(is_superadmin_email(email) or db["users"].find_one({"email": email}, {"_id": 1})
                or db["admin_users"].find_one({"email": email}, {"_id": 1}))


def set_password(kind: str, account_id: str, new_password: str, *, actor_email: str, by_label: str,
                 must_change: bool = True, notify: bool = True,
                 organization_name: Optional[str] = None) -> Dict[str, Any]:
    """Set an account's password (as an administrator). Returns
    {email, sessions_revoked, must_change_password, email_delivery}."""
    from app.auth.crypto import hash_password
    from app.auth.service import clear_account_failures
    from app.lifecycle.demo import validate_password
    db = _db()
    doc = load_account(kind, account_id, db)
    _guard(doc, actor_email)
    validate_password(new_password)
    now = utcnow()
    db[_coll(kind)].update_one({"_id": doc["_id"]}, {"$set": {
        "password_hash": hash_password(new_password), "password_changed_at": now,
        "password_set_by": actor_email, "must_change_password": bool(must_change), "updated_at": now}})
    if kind == "user":
        # an outstanding reset link must not undo the new password
        db["password_resets"].update_many({"user_id": str(doc["_id"]), "used_at": None},
                                          {"$set": {"used_at": now, "invalidated": True}})
    revoked = _revoke_sessions(db, _session_owner(kind, doc), f"password_set:{by_label}")
    email = (doc.get("email") or "").lower()
    clear_account_failures(email)
    delivery = "skipped"
    if notify and email:
        from app.events.email import absolute_url, send_email
        who = f"An administrator of {organization_name}" if organization_name else "LeadAI support"
        delivery = send_email(email, "Your LeadAI password was changed",
                              f"Hi {doc.get('name') or ''},\n\n{who} set a new password for your LeadAI "
                              "account and signed you out of every device. They will give you the "
                              "new password directly — it is never sent by email."
                              + (" You will be asked to choose your own password when you sign in."
                                 if must_change else "")
                              + f"\n\nSign in: {absolute_url('/login')}\n\n"
                              "If you didn't expect this, contact your administrator.",
                              kind="account_change")
    return {"email": email, "sessions_revoked": revoked, "must_change_password": bool(must_change),
            "email_delivery": delivery}


def change_email(kind: str, account_id: str, new_email: str, *, actor_email: str, by_label: str,
                 organization_name: Optional[str] = None) -> Dict[str, Any]:
    """Change an account's sign-in email everywhere it is stored. Returns
    {before, after, sessions_revoked, changed}."""
    from app.auth.service import clear_account_failures
    db = _db()
    doc = load_account(kind, account_id, db)
    _guard(doc, actor_email)
    new_email = (new_email or "").strip().lower()
    if not EMAIL_RE.match(new_email) or len(new_email) > 254:
        raise HTTPException(status_code=422, detail="Enter a valid email address")
    old_email = (doc.get("email") or "").lower()
    if new_email == old_email:
        return {"before": old_email, "after": old_email, "sessions_revoked": 0, "changed": False}
    if email_in_use(new_email, db):
        raise HTTPException(status_code=409, detail="That email is already in use")
    now = utcnow()
    owner_before = _session_owner(kind, doc)
    db[_coll(kind)].update_one({"_id": doc["_id"]}, {"$set": {
        "email": new_email, "updated_at": now, "email_changed_at": now, "email_changed_by": actor_email,
        # the new address hasn't been confirmed by its owner
        **({"email_verified": False, "email_verified_at": None} if kind == "user" else {})}})
    if kind == "user":
        uid = str(doc["_id"])
        db["organization_members"].update_many({"user_id": uid}, {"$set": {"email": new_email}})
        db["partners"].update_many({"user_id": uid}, {"$set": {"email": new_email, "updated_at": now}})
        db["partner_applications"].update_many({"user_id": uid}, {"$set": {"email": new_email}})
    revoked = _revoke_sessions(db, owner_before, f"email_change:{by_label}")
    clear_account_failures(old_email)
    from app.events.email import send_email
    who = f"an administrator of {organization_name}" if organization_name else "LeadAI support"
    for addr in (old_email, new_email):
        if addr:
            send_email(addr, "Your LeadAI sign-in email was changed",
                       f"The sign-in email of your LeadAI account was changed from {old_email} to "
                       f"{new_email} by {who}. Sign in with {new_email} from now on.\n\n"
                       "If you didn't expect this, contact your administrator.", kind="account_change")
    return {"before": old_email, "after": new_email, "sessions_revoked": revoked, "changed": True}


def must_change_password(user_claims: Dict[str, Any]) -> bool:
    """Whether the account behind fresh sign-in claims must pick a new password."""
    db = get_sync_db()
    if db is None:
        return False
    try:
        uid = user_claims.get("user_id")
        if uid and ObjectId.is_valid(str(uid)):
            doc = db["users"].find_one({"_id": ObjectId(str(uid))}, {"must_change_password": 1})
        else:
            email = (user_claims.get("email") or "").lower()
            doc = (db["admin_users"].find_one({"email": email}, {"must_change_password": 1})
                   or db["users"].find_one({"email": email}, {"must_change_password": 1}))
        return bool((doc or {}).get("must_change_password"))
    except Exception:
        return False


def own_account(user_claims: Dict[str, Any], db=None):
    """(collection, doc) of the signed-in account itself (users or admin_users)."""
    db = db if db is not None else _db()
    uid = user_claims.get("user_id")
    if uid and ObjectId.is_valid(str(uid)):
        doc = db["users"].find_one({"_id": ObjectId(str(uid))})
        return ("users", doc) if doc else (None, None)
    email = (user_claims.get("email") or "").lower()
    if user_claims.get("scope") == "admin" and email:
        doc = db["admin_users"].find_one({"email": email})
        if doc:
            return "admin_users", doc
        doc = db["users"].find_one({"email": email, "is_platform_admin": True})
        if doc:
            return "users", doc
    return None, None
