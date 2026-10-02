"""
LeadAI Public REST API v1 (Phase 6 Product Feature).

Endpoints authenticated via `X-API-Key: lai_live_...` or Bearer token:
- GET  /api/v1/leads
- POST /api/v1/leads
- POST /api/v1/search
- GET  /api/v1/search/{run_id}
"""
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from app.db.mongo import get_async_db
from app.pipeline.deduplication import deduplicate_lead
from app.queue.service import enqueue_job
from app.services.api_keys import authenticate_api_key
from app.social.url_detector import UrlError, detect_social_url

router = APIRouter(prefix="/api/v1", tags=["public_api_v1"])


async def get_api_auth(
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    raw_key = x_api_key
    if not raw_key and authorization and authorization.lower().startswith("bearer "):
        raw_key = authorization.split(" ", 1)[1].strip()

    if not raw_key:
        raise HTTPException(status_code=401, detail="Missing API key. Provide 'X-API-Key' header.")

    db = await get_async_db()
    # Synchronous auth call
    return authenticate_api_key(db.delegate, raw_key)


class IngestLeadPayload(BaseModel):
    author_name: str
    phone: str | None = None
    email: str | None = None
    comment_text: str = ""
    platform: str = "social"
    intent: str = "purchase_inquiry"
    priority: str = "warm"
    lead_score: int = 50


class StartSearchPayload(BaseModel):
    url: str
    max_posts: int = Field(20, ge=1, le=100)
    max_comments_per_post: int = Field(30, ge=1, le=200)


@router.get("/leads")
async def list_leads_v1(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    platform: str | None = None,
    priority: str | None = None,
    auth: dict[str, Any] = Depends(get_api_auth),  # noqa: B008
):
    if "leads:read" not in auth.get("scopes", []) and "*" not in auth.get("scopes", []):
        raise HTTPException(status_code=403, detail="API key lacks 'leads:read' scope")

    db = await get_async_db()
    org_id = auth["organization_id"]
    query: dict[str, Any] = {"organization_id": org_id}
    if platform:
        query["platform"] = platform
    if priority:
        query["priority"] = priority

    cursor = db.ai_comments.find(query).sort("created_at", -1).skip(offset).limit(limit)
    items = []
    async for doc in cursor:
        doc["id"] = str(doc.pop("_id"))
        items.append(doc)

    total = await db.ai_comments.count_documents(query)
    return {"items": items, "total": total, "offset": offset, "limit": limit}


@router.post("/leads")
async def ingest_lead_v1(
    payload: IngestLeadPayload,
    auth: dict[str, Any] = Depends(get_api_auth),  # noqa: B008
):
    if "leads:write" not in auth.get("scopes", []) and "*" not in auth.get("scopes", []):
        raise HTTPException(status_code=403, detail="API key lacks 'leads:write' scope")

    db = await get_async_db()
    org_id = auth["organization_id"]

    lead_doc = payload.model_dump()
    lead_id, is_dup, dup_count = deduplicate_lead(db.delegate, lead_doc, org_id)

    return {
        "lead_id": lead_id,
        "is_duplicate": is_dup,
        "duplicate_count": dup_count,
        "status": "ingested",
    }


@router.post("/search")
async def create_search_v1(
    payload: StartSearchPayload,
    auth: dict[str, Any] = Depends(get_api_auth),  # noqa: B008
):
    if "search:create" not in auth.get("scopes", []) and "*" not in auth.get("scopes", []):
        raise HTTPException(status_code=403, detail="API key lacks 'search:create' scope")

    try:
        platform, canonical_url = detect_social_url(payload.url)
    except UrlError as e:
        raise HTTPException(status_code=400, detail=f"Invalid URL: {e.message}")

    import uuid
    run_id = f"URL_API_{uuid.uuid4().hex[:12]}"
    org_id = auth["organization_id"]

    enqueue_job(
        task_name="url_search",
        payload={
            "run_id": run_id,
            "url": canonical_url,
            "max_posts": payload.max_posts,
            "max_comments_per_post": payload.max_comments_per_post,
            "organization_id": org_id,
            "created_by": f"api_key:{auth.get('key_id')}",
        },
        run_id=run_id,
        organization_id=org_id,
        created_by=f"api_key:{auth.get('key_id')}",
    )

    return {
        "run_id": run_id,
        "url": canonical_url,
        "platform": platform,
        "status": "queued",
    }


@router.get("/search/{run_id}")
async def get_search_v1(
    run_id: str,
    auth: dict[str, Any] = Depends(get_api_auth),  # noqa: B008
):
    db = await get_async_db()
    org_id = auth["organization_id"]
    run = await db.search_history.find_one({"run_id": run_id, "organization_id": org_id})
    if not run:
        job = await db.job_queue.find_one({"run_id": run_id, "organization_id": org_id})
        if not job:
            raise HTTPException(status_code=404, detail="Search run not found")
        return {
            "run_id": run_id,
            "status": job.get("status", "queued"),
            "created_at": job.get("created_at"),
        }

    run["id"] = str(run.pop("_id"))
    return run
