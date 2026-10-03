"""Partner tasks: work the Super Admin assigns, the partner carries out.

The Super Admin assigns a task to one partner or to every active partner
(optionally only affiliates or resellers). The partner starts it, then
submits it with a note on what they did; the Super Admin approves it (done)
or sends it back with feedback. The Super Admin can edit or cancel an open
task. Every step is audited (``partner.task.*``) and notifies the other side.

    open ──start──▶ in_progress ──submit──▶ submitted ──approve──▶ done
      └────────────────submit──────────────▲     └──send_back──▶ in_progress
    open / in_progress / submitted ──cancel──▶ cancelled
"""
import uuid
from datetime import date, datetime, time, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from app.db.models import utcnow
from app.partners import constants as K
from app.partners.service import clean, db_or_503, notify_partner, oid, paudit, text

TASKS = "partner_tasks"
T_OPEN = "open"
T_IN_PROGRESS = "in_progress"
T_SUBMITTED = "submitted"
T_DONE = "done"
T_CANCELLED = "cancelled"
TASK_STATUSES = (T_OPEN, T_IN_PROGRESS, T_SUBMITTED, T_DONE, T_CANCELLED)
OPEN_TASK = (T_OPEN, T_IN_PROGRESS, T_SUBMITTED)
PRIORITIES = ("low", "normal", "high")
AUDIENCES = ("partner", "all", K.AFFILIATE, K.RESELLER)
# Partner Portal sections a task may point at ("Open in portal")
SECTIONS = ("sell", "deals", "referrals", "customers", "campaigns", "coupons", "marketing",
            "commissions", "wallet", "payouts", "analytics", "profile", "api", "settings")
MAX_RECIPIENTS = 2000


def _naive(d: Optional[datetime]) -> Optional[datetime]:
    """Mongo hands back naive UTC; compare like with like."""
    return d.astimezone(timezone.utc).replace(tzinfo=None) if d and d.tzinfo else d


def _due(value: Any) -> Optional[datetime]:
    """``YYYY-MM-DD`` (end of that day, UTC) or a full ISO timestamp; never in the past."""
    if value in (None, ""):
        return None
    s = str(value).strip()
    try:
        if len(s) == 10:
            d = datetime.combine(date.fromisoformat(s), time(23, 59, 59), tzinfo=timezone.utc)
        else:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
            d = d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(status_code=422, detail="Due date must be a date (YYYY-MM-DD)")
    if _naive(d) < _naive(utcnow()):
        raise HTTPException(status_code=422, detail="Due date can't be in the past")
    return d


def _fields(data: Dict[str, Any], *, partial: bool = False) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if not partial or "title" in data:
        t = text(data.get("title"), 160)
        if not t:
            raise HTTPException(status_code=422, detail="Title is required")
        out["title"] = t
    if not partial or "details" in data:
        out["details"] = text(data.get("details"), 4000)
    if not partial or "due_at" in data:
        out["due_at"] = _due(data.get("due_at"))
        # the calendar day the Super Admin picked, shown as-is in every timezone
        out["due_date"] = out["due_at"].date().isoformat() if out["due_at"] else None
    if not partial or "priority" in data:
        p = data.get("priority") or "normal"
        if p not in PRIORITIES:
            raise HTTPException(status_code=422, detail="priority must be low, normal or high")
        out["priority"] = p
    if not partial or "section" in data:
        sec = data.get("section") or None
        if sec and sec not in SECTIONS:
            raise HTTPException(status_code=422, detail="Unknown portal section")
        out["section"] = sec
    return out


def task_out(d: Dict[str, Any]) -> Dict[str, Any]:
    out = clean(d)
    due = _naive(d.get("due_at"))
    out["overdue"] = bool(due and d.get("status") in (T_OPEN, T_IN_PROGRESS) and due < _naive(utcnow()))
    return out


def _who(actor: Any) -> str:
    return actor.get("email") if isinstance(actor, dict) else str(actor)


def _load(db, task_id: str) -> Dict[str, Any]:
    t = db[TASKS].find_one({"_id": oid(task_id)}) if oid(task_id) else None
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    return t


def _move(db, t: Dict[str, Any], target: str, by: str, note: Optional[str], extra: Optional[Dict[str, Any]] = None):
    now = utcnow()
    res = db[TASKS].update_one({"_id": t["_id"], "status": t["status"]},
                               {"$set": {"status": target, "updated_at": now, **(extra or {})},
                                "$push": {"history": {"status": target, "at": now, "by": by, "note": note}}})
    if not res.modified_count:
        raise HTTPException(status_code=409, detail="The task changed meanwhile — reload and retry")
    return db[TASKS].find_one({"_id": t["_id"]})


# ── Super Admin ──────────────────────────────────────────────────────────────

def assign(data: Dict[str, Any], *, actor: Any, ip: Optional[str] = None) -> Dict[str, Any]:
    """Create one task per recipient. ``audience``: partner (needs
    ``partner_id``), all, affiliate or reseller (active partners only)."""
    db = db_or_503()
    fields = _fields(data)
    audience = data.get("audience") or "partner"
    if audience not in AUDIENCES:
        raise HTTPException(status_code=422, detail="audience must be partner, all, affiliate or reseller")
    if audience == "partner":
        p = db[K.PARTNERS].find_one({"_id": oid(data.get("partner_id"))}) if oid(data.get("partner_id")) else None
        if not p:
            raise HTTPException(status_code=404, detail="Partner not found")
        if p.get("status") != K.P_ACTIVE:
            raise HTTPException(status_code=409, detail="This partner is suspended — reactivate them first")
        partners: List[Dict[str, Any]] = [p]
    else:
        q: Dict[str, Any] = {"status": K.P_ACTIVE}
        if audience != "all":
            q["partner_type"] = audience
        if db[K.PARTNERS].count_documents(q) > MAX_RECIPIENTS:
            raise HTTPException(status_code=422, detail=f"More than {MAX_RECIPIENTS} partners — narrow the audience")
        partners = list(db[K.PARTNERS].find(q))
        if not partners:
            raise HTTPException(status_code=422, detail="No active partners match this audience")
    who, now = _who(actor), utcnow()
    batch = uuid.uuid4().hex[:12] if len(partners) > 1 else None
    docs = [{"partner_id": str(p["_id"]), **fields, "status": T_OPEN, "audience": audience, "batch_id": batch,
             "assigned_by": who, "submission_note": None, "review_note": None, "submitted_at": None,
             "completed_at": None, "created_at": now, "updated_at": now,
             "history": [{"status": T_OPEN, "at": now, "by": who, "note": None}]} for p in partners]
    ids = db[TASKS].insert_many(docs).inserted_ids
    email = data.get("email", True) is not False
    due = f" Due {fields['due_at']:%d %b %Y}." if fields.get("due_at") else ""
    for p, d, _id in zip(partners, docs, ids):
        d["_id"] = _id
        paudit("partner.task.assigned", d["partner_id"], actor=actor, ip=ip,
               details={"title": fields["title"], "due_at": fields.get("due_at") and fields["due_at"].isoformat(),
                        "priority": fields["priority"], "audience": audience, "batch_id": batch},
               resource_type="partner_task", resource_id=str(_id))
        notify_partner(p, "partner_task", "New task from LeadAI", f"{fields['title']}.{due}",
                       severity="warning" if fields["priority"] == "high" else "info",
                       link=f"/partner#/tasks/{_id}", email=email, data={"task_id": str(_id)})
    return {"created": len(docs), "batch_id": batch, "tasks": [task_out(d) for d in docs[:50]]}


def admin_edit(task_id: str, data: Dict[str, Any], *, actor: Any, ip: Optional[str] = None) -> Dict[str, Any]:
    db = db_or_503()
    t = _load(db, task_id)
    if t["status"] not in OPEN_TASK:
        raise HTTPException(status_code=409, detail="This task is closed")
    upd = _fields(data, partial=True)
    if not upd:
        raise HTTPException(status_code=422, detail="Nothing to change")
    db[TASKS].update_one({"_id": t["_id"]}, {"$set": {**upd, "updated_at": utcnow()}})
    paudit("partner.task.edited", t["partner_id"], actor=actor, ip=ip,
           details={"title": upd.get("title") or t["title"], "fields": sorted(upd)},
           resource_type="partner_task", resource_id=task_id)
    p = db[K.PARTNERS].find_one({"_id": oid(t["partner_id"])})
    if p:
        notify_partner(p, "partner_task", "Task updated", f"LeadAI updated the task “{upd.get('title') or t['title']}”.",
                       link=f"/partner#/tasks/{task_id}")
    return task_out(db[TASKS].find_one({"_id": t["_id"]}))


def review(task_id: str, decision: str, *, actor: Any, note: str = "", ip: Optional[str] = None) -> Dict[str, Any]:
    """approve (submitted → done), send_back (submitted → in_progress), cancel (any open → cancelled)."""
    db = db_or_503()
    t = _load(db, task_id)
    note = text(note, 2000)
    rules = {"approve": ((T_SUBMITTED,), T_DONE), "send_back": ((T_SUBMITTED,), T_IN_PROGRESS),
             "cancel": (OPEN_TASK, T_CANCELLED)}
    if decision not in rules:
        raise HTTPException(status_code=404, detail="Unknown action")
    allowed, target = rules[decision]
    if t["status"] not in allowed:
        raise HTTPException(status_code=409, detail=f"Task is '{t['status']}' — cannot {decision.replace('_', ' ')}")
    if decision in ("send_back", "cancel") and not note:
        raise HTTPException(status_code=422, detail="A reason is required")
    now = utcnow()
    extra: Dict[str, Any] = {"review_note": note, "reviewed_by": _who(actor), "reviewed_at": now}
    if target == T_DONE:
        extra["completed_at"] = now
    doc = _move(db, t, target, _who(actor), note, extra)
    action = {"approve": "approved", "send_back": "sent_back", "cancel": "cancelled"}[decision]
    paudit(f"partner.task.{action}", t["partner_id"], actor=actor, ip=ip,
           details={"title": t["title"], "note": note}, resource_type="partner_task", resource_id=task_id)
    p = db[K.PARTNERS].find_one({"_id": oid(t["partner_id"])})
    if p:
        title, msg, sev = {
            "approve": ("Task approved", f"LeadAI approved “{t['title']}”." + (f" {note}" if note else ""), "success"),
            "send_back": ("Task sent back", f"LeadAI needs more on “{t['title']}”: {note}", "warning"),
            "cancel": ("Task cancelled", f"“{t['title']}” was cancelled: {note}", "info"),
        }[decision]
        notify_partner(p, "partner_task", title, msg, severity=sev, link=f"/partner#/tasks/{task_id}",
                       email=decision == "send_back")
    return task_out(doc)


def list_query(status: Optional[str], q: Optional[str], overdue: Optional[bool] = None) -> Dict[str, Any]:
    import re
    query: Dict[str, Any] = {}
    if status:
        query["status"] = status
    if q:
        query["title"] = {"$regex": re.escape(q.strip()[:80]), "$options": "i"}
    if overdue:
        query["status"] = {"$in": [T_OPEN, T_IN_PROGRESS]}
        query["due_at"] = {"$lt": utcnow()}
    return query


# ── Partner ──────────────────────────────────────────────────────────────────

def partner_action(partner: Dict[str, Any], task_id: str, action: str, note: str = "", *,
                   ip: Optional[str] = None) -> Dict[str, Any]:
    """start (open → in_progress) or submit (open / in_progress → submitted, with a note)."""
    db = db_or_503()
    t = _load(db, task_id)
    pid = str(partner["_id"])
    if t["partner_id"] != pid:
        raise HTTPException(status_code=404, detail="Task not found")
    note = text(note, 4000)
    if action == "start":
        if t["status"] != T_OPEN:
            raise HTTPException(status_code=409, detail="Only a new task can be started")
        doc = _move(db, t, T_IN_PROGRESS, partner["email"], None)
    elif action == "submit":
        if t["status"] not in (T_OPEN, T_IN_PROGRESS):
            raise HTTPException(status_code=409, detail="This task can't be submitted now")
        if not note:
            raise HTTPException(status_code=422, detail="Tell LeadAI what you did")
        doc = _move(db, t, T_SUBMITTED, partner["email"], note, {"submission_note": note, "submitted_at": utcnow()})
    else:
        raise HTTPException(status_code=404, detail="Unknown action")
    paudit("partner.task.started" if action == "start" else "partner.task.submitted", pid, actor=partner["email"],
           ip=ip, details={"title": t["title"], "note": note}, resource_type="partner_task", resource_id=task_id)
    if action == "submit":
        from app.events.notifications import notify_super_admins
        notify_super_admins("partner_task", "Task ready for review",
                            f"{partner.get('company') or partner.get('name')}: {t['title']}",
                            link=f"/superadmin#/partners?tab=tasks&status={T_SUBMITTED}", data={"task_id": task_id})
    return task_out(doc)


def open_count(db, partner_id: str) -> int:
    return db[TASKS].count_documents({"partner_id": partner_id, "status": {"$in": [T_OPEN, T_IN_PROGRESS]}})
