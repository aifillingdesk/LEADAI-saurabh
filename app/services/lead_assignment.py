"""
LeadAI Lead Assignment Rules & SLA Timers Service (Phase 6 Product Feature).

Manages:
- Automated lead assignment (Round-Robin, Priority-based, Intent-based)
- SLA timers (e.g., 4-hour SLA for Hot leads, 24-hour SLA for standard leads)
- Follow-up reminder checking & alerting for overdue leads and tasks
"""
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from bson import ObjectId

from app.db.models import utcnow

logger = logging.getLogger(__name__)

COLL_ASSIGNMENT_RULES = "lead_assignment_rules"
COLL_LEADS = "ai_comments"
COLL_FOLLOWUPS = "lead_followups"


def create_assignment_rule(
    db,
    *,
    organization_id: str,
    name: str,
    rule_type: str = "round_robin",
    assignees: list[str],
    criteria: dict[str, Any] | None = None,
    sla_hours: int = 24,
    created_by: str | None = None,
) -> dict[str, Any]:
    """Create an automated lead assignment rule."""
    if not assignees:
        raise ValueError("At least one assignee is required")

    rule_id = f"rule_{uuid.uuid4().hex[:10]}"
    now = utcnow()

    doc = {
        "_id": ObjectId(),
        "rule_id": rule_id,
        "organization_id": str(organization_id),
        "name": name,
        "rule_type": rule_type,
        "assignees": assignees,
        "current_index": 0,
        "criteria": criteria or {},
        "sla_hours": max(1, int(sla_hours)),
        "is_active": True,
        "created_by": created_by,
        "created_at": now,
        "updated_at": now,
    }

    db[COLL_ASSIGNMENT_RULES].insert_one(doc)
    doc["id"] = str(doc["_id"])
    logger.info("Created lead assignment rule %s for org %s", rule_id, organization_id)
    return doc


def list_assignment_rules(db, organization_id: str) -> list[dict[str, Any]]:
    """List all assignment rules for an organization."""
    rules = list(db[COLL_ASSIGNMENT_RULES].find({"organization_id": str(organization_id)}).sort("created_at", 1))
    for r in rules:
        r["id"] = str(r["_id"])
    return rules


def delete_assignment_rule(db, rule_id: str, organization_id: str) -> bool:
    """Delete an assignment rule."""
    q: dict[str, Any] = {"organization_id": str(organization_id)}
    if ObjectId.is_valid(rule_id):
        q["$or"] = [{"_id": ObjectId(rule_id)}, {"rule_id": rule_id}]
    else:
        q["rule_id"] = rule_id

    res = db[COLL_ASSIGNMENT_RULES].delete_one(q)
    return res.deleted_count > 0


def assign_lead(
    db,
    lead_id: str,
    lead_doc: dict[str, Any],
    organization_id: str,
) -> dict[str, Any] | None:
    """Evaluate rules and assign a lead with an SLA timer."""
    rules = list(db[COLL_ASSIGNMENT_RULES].find({
        "organization_id": str(organization_id),
        "is_active": True,
    }).sort("created_at", 1))

    if not rules:
        return None

    now = utcnow()

    for rule in rules:
        assignees = rule.get("assignees", [])
        if not assignees:
            continue

        # Match criteria
        crit = rule.get("criteria", {})
        if "priority" in crit and crit["priority"] != lead_doc.get("priority"):
            continue
        if "intent" in crit and crit["intent"] != lead_doc.get("intent"):
            continue
        if "min_score" in crit and lead_doc.get("lead_score", 0) < crit["min_score"]:
            continue

        # Select assignee (Round Robin)
        idx = rule.get("current_index", 0) % len(assignees)
        selected_assignee = assignees[idx]
        next_idx = (idx + 1) % len(assignees)

        db[COLL_ASSIGNMENT_RULES].update_one(
            {"_id": rule["_id"]},
            {"$set": {"current_index": next_idx, "updated_at": now}},
        )

        sla_hours = rule.get("sla_hours", 24)
        sla_due_at = now + timedelta(hours=sla_hours)

        assignment_meta = {
            "assigned_user_id": selected_assignee,
            "assigned_at": now,
            "assignment_rule_id": rule.get("rule_id"),
            "sla_hours": sla_hours,
            "sla_due_at": sla_due_at,
            "sla_status": "on_track",
            "updated_at": now,
        }

        q: dict[str, Any] = {"organization_id": str(organization_id)}
        if ObjectId.is_valid(lead_id):
            q["_id"] = ObjectId(lead_id)
        else:
            q["lead_id"] = lead_id

        db[COLL_LEADS].update_one(q, {"$set": assignment_meta})
        logger.info("Lead %s assigned to user %s via rule '%s' (SLA: %dh)",
                    lead_id, selected_assignee, rule.get("name"), sla_hours)
        return assignment_meta

    return None


def check_sla_and_reminders(db, now: datetime | None = None) -> dict[str, int]:
    """Sweep for breached SLAs and overdue follow-up reminders."""
    current_time = now or utcnow()

    # 1. Check breached SLAs on uncontacted leads
    breached_cursor = db[COLL_LEADS].find({
        "sla_due_at": {"$lte": current_time},
        "sla_status": "on_track",
        "status": {"$in": ["new", "open", "pending"]},
    })

    breached_count = 0
    for lead in breached_cursor:
        db[COLL_LEADS].update_one(
            {"_id": lead["_id"]},
            {
                "$set": {
                    "sla_status": "breached",
                    "sla_breached_at": current_time,
                    "updated_at": current_time,
                }
            },
        )
        breached_count += 1
        logger.warning("Lead %s breached SLA timer (assignee: %s)",
                       lead.get("_id"), lead.get("assigned_user_id"))

    # 2. Check overdue follow-up reminders
    overdue_fu = db[COLL_FOLLOWUPS].update_many(
        {
            "follow_up_date": {"$lte": current_time},
            "status": "pending",
        },
        {
            "$set": {
                "status": "overdue",
                "updated_at": current_time,
            }
        },
    )

    return {
        "sla_breaches_flagged": breached_count,
        "overdue_followups_flagged": overdue_fu.modified_count,
    }
