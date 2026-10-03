"""
LeadAI Cross-Run and Cross-Platform Lead Deduplication Engine (Phase 6 Product Feature).

Matches and consolidates leads across different search runs, dates, and social platforms
within the same tenant organization by:
1. Normalized Phone Number
2. Normalized Email Address
3. Normalized Social Author Profile URL / Handle
4. Author Name + Platform match

Features:
- Preserves multi-tenant organization boundary (`organization_id`).
- Merges run history (`source_runs`), increments `duplicate_count`.
- Keeps the highest quality lead score and freshest signals.
"""
import logging
from typing import Any

from bson import ObjectId

from app.db.models import utcnow

logger = logging.getLogger(__name__)

COLL_LEADS = "ai_comments"


def _clean_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = "".join(c for c in str(phone) if c.isdigit())
    return digits if len(digits) >= 7 else None


def _clean_email(email: str | None) -> str | None:
    if not email:
        return None
    clean = str(email).strip().lower()
    return clean if "@" in clean and "." in clean else None


def find_duplicate_lead(
    db,
    *,
    organization_id: str,
    phone: str | None = None,
    email: str | None = None,
    author_profile_url: str | None = None,
    author_name: str | None = None,
    platform: str | None = None,
) -> dict[str, Any] | None:
    """Look for an existing matching lead in the same organization."""
    if not organization_id:
        return None

    org_filter = {"organization_id": str(organization_id)}

    # 1. Match on phone
    norm_phone = _clean_phone(phone)
    if norm_phone:
        existing = db[COLL_LEADS].find_one({**org_filter, "phone": {"$regex": f"{norm_phone}$"}})
        if existing:
            return existing

    # 2. Match on email
    norm_email = _clean_email(email)
    if norm_email:
        existing = db[COLL_LEADS].find_one({**org_filter, "email": norm_email})
        if existing:
            return existing

    # 3. Match on social profile URL (cross-run on same account)
    if author_profile_url and len(author_profile_url) > 10:
        clean_url = author_profile_url.rstrip("/").lower()
        existing = db[COLL_LEADS].find_one({**org_filter, "author_profile_url": clean_url})
        if existing:
            return existing

    # 4. Match on author_name + platform (if author name is distinctive, >= 4 chars)
    if author_name and platform and len(author_name.strip()) >= 4:
        clean_name = author_name.strip().lower()
        existing = db[COLL_LEADS].find_one({
            **org_filter,
            "author_name": {"$regex": f"^{clean_name}$", "$options": "i"},
            "platform": platform,
        })
        if existing:
            return existing

    return None


def deduplicate_lead(
    db,
    lead_doc: dict[str, Any],
    organization_id: str,
    run_id: str | None = None,
) -> tuple[str, bool, int]:
    """
    Deduplicate a prospective lead against existing leads in the organization.
    Returns: (lead_id, is_duplicate, duplicate_count)
    """
    now = utcnow()
    existing = find_duplicate_lead(
        db,
        organization_id=organization_id,
        phone=lead_doc.get("phone"),
        email=lead_doc.get("email"),
        author_profile_url=lead_doc.get("author_profile_url"),
        author_name=lead_doc.get("author_name"),
        platform=lead_doc.get("platform"),
    )

    if existing:
        lead_id = str(existing["_id"])
        current_runs = existing.get("source_runs", [])
        if run_id and run_id not in current_runs:
            current_runs.append(run_id)

        update_fields: dict[str, Any] = {
            "source_runs": current_runs,
            "last_seen_at": now,
            "updated_at": now,
        }

        # Keep higher score if incoming is better
        new_score = lead_doc.get("lead_score", 0)
        old_score = existing.get("lead_score", 0)
        if new_score > old_score:
            update_fields["lead_score"] = new_score
            update_fields["priority"] = lead_doc.get("priority", existing.get("priority"))
            update_fields["confidence_score"] = lead_doc.get("confidence_score", existing.get("confidence_score"))

        # Merge contact fields if existing lacked them
        if not existing.get("phone") and lead_doc.get("phone"):
            update_fields["phone"] = lead_doc["phone"]
        if not existing.get("email") and lead_doc.get("email"):
            update_fields["email"] = lead_doc["email"]

        db[COLL_LEADS].update_one(
            {"_id": existing["_id"]},
            {
                "$set": update_fields,
                "$inc": {"duplicate_count": 1},
            },
        )
        new_count = existing.get("duplicate_count", 1) + 1
        logger.info("Consolidated duplicate lead %s in org %s (seen %d times across runs %s)",
                    lead_id, organization_id, new_count, current_runs)
        return lead_id, True, new_count

    # New unique lead
    doc = dict(lead_doc)
    doc["organization_id"] = str(organization_id)
    doc["source_runs"] = [run_id] if run_id else []
    doc["duplicate_count"] = 1
    doc["first_seen_at"] = now
    doc["last_seen_at"] = now
    doc.setdefault("created_at", now)
    doc["updated_at"] = now

    if "_id" not in doc or not doc["_id"]:
        doc["_id"] = ObjectId()
    doc.setdefault("comment_ref", str(ObjectId()))

    db[COLL_LEADS].insert_one(doc)
    lead_id = str(doc["_id"])
    logger.info("Created new unique lead %s in org %s", lead_id, organization_id)
    return lead_id, False, 1
