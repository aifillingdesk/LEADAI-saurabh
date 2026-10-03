"""Partner Marketing Center (Super Admin managed).

Assets are a link, text (marketing copy / email templates with
``{referral_url}``, ``{referral_code}``, ``{partner_name}``, ``{company}``
placeholders) and/or an uploaded file. Files go through the existing
pluggable storage service: with the default local backend they are written
to a PRIVATE directory (never under /static) and are only served by the
permission-checked download endpoint. With S3 / Cloudinary the provider URL
is used (public object URLs — see docs/PARTNERS.md).

Visibility: published + partner type ("all" / affiliate / reseller) +
optional tiers + optional named partners. Every check is server side.
"""
import os
import re
import uuid
from typing import Any, Dict, Optional

from fastapi import HTTPException

from app.db.models import utcnow
from app.partners import constants as K
from app.partners.service import clean, oid, referral_url, text

CATEGORIES = {
    "logos": "Logos", "product_images": "Product images & screenshots", "brochures": "Brochures & PDFs",
    "videos": "Videos", "social": "Social media creatives", "banners": "Promotional banners",
    "email_templates": "Email templates", "copy": "Marketing copy", "campaign_materials": "Campaign materials",
}
# legacy "kind" values from the first version map onto categories
_KIND_TO_CATEGORY = {"banner": "banners", "logo": "logos", "copy": "copy", "email": "email_templates",
                     "social": "social", "video": "videos", "document": "brochures"}

PRIVATE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                           "data", "partner_assets")
MAX_BYTES = 50 * 1024 * 1024
# declared type -> (extension, magic prefixes or None = checked separately)
FILE_TYPES = {
    "image/png": (".png", (b"\x89PNG\r\n\x1a\n",)), "image/jpeg": (".jpg", (b"\xff\xd8\xff",)),
    "image/gif": (".gif", (b"GIF87a", b"GIF89a")), "image/webp": (".webp", (b"RIFF",)),
    "application/pdf": (".pdf", (b"%PDF-",)), "video/mp4": (".mp4", None), "video/webm": (".webm", (b"\x1a\x45\xdf\xa3",)),
    "application/zip": (".zip", (b"PK\x03\x04",)),
}


def category_of(a: Dict[str, Any]) -> str:
    return a.get("category") or _KIND_TO_CATEGORY.get(a.get("kind") or "", "campaign_materials")


def clean_asset(body: Dict[str, Any], db) -> Dict[str, Any]:
    title = text(body.get("title"), 120)
    if not title:
        raise HTTPException(status_code=422, detail="Title is required")
    category = body.get("category") or _KIND_TO_CATEGORY.get(body.get("kind") or "", "")
    if category not in CATEGORIES:
        raise HTTPException(status_code=422, detail="Unknown asset category")
    url = text(body.get("url"), 500)
    if url and not re.match(r"^https?://[^\s<>\"']+$", url, re.I):
        raise HTTPException(status_code=422, detail="Link must be an http(s) URL (upload files instead)")
    content = text(body.get("content"), 10000)
    types = [t for t in (body.get("partner_types") or ["all"]) if t in (*K.PARTNER_TYPES, "all")] or ["all"]
    partner_ids = [str(p) for p in (body.get("partner_ids") or []) if oid(p)]
    for pid in partner_ids:
        if not db[K.PARTNERS].find_one({"_id": oid(pid)}, {"_id": 1}):
            raise HTTPException(status_code=422, detail=f"Unknown partner {pid}")
    return {"title": title, "category": category, "kind": category, "url": url, "content": content,
            "subject": text(body.get("subject"), 200) if category == "email_templates" else None,
            "description": text(body.get("description"), 500), "partner_types": types,
            "tier_ids": [str(t) for t in (body.get("tier_ids") or []) if oid(t)], "partner_ids": partner_ids,
            "status": "published" if body.get("status", "published") == "published" else "draft",
            "updated_at": utcnow()}


def can_see(asset: Dict[str, Any], partner: Dict[str, Any]) -> bool:
    if asset.get("status") != "published":
        return False
    types = asset.get("partner_types") or ["all"]
    if "all" not in types and partner.get("partner_type") not in types:
        return False
    if asset.get("tier_ids") and str(partner.get("tier_id")) not in asset["tier_ids"]:
        return False
    if asset.get("partner_ids") and str(partner["_id"]) not in asset["partner_ids"]:
        return False
    return True


def personalise(value: Optional[str], partner: Dict[str, Any]) -> Optional[str]:
    if not value:
        return value
    return (value.replace("{referral_url}", referral_url(partner.get("referral_code")) or "")
                 .replace("{referral_code}", partner.get("referral_code") or "")
                 .replace("{partner_name}", partner.get("name") or "")
                 .replace("{company}", partner.get("company") or partner.get("name") or ""))


def asset_out(a: Dict[str, Any], partner: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out = clean({k: v for k, v in a.items() if k not in ("storage_key", "file_url")})
    out["category"] = category_of(a)
    out["category_label"] = CATEGORIES.get(out["category"], out["category"])
    out["has_file"] = bool(a.get("storage_key") or a.get("file_url"))
    if partner is not None:
        out["content"] = personalise(a.get("content"), partner)
        out["subject"] = personalise(a.get("subject"), partner)
        out.pop("partner_ids", None)
        out.pop("tier_ids", None)
        if out["has_file"]:
            out["file_path"] = f"/api/partner/v1/marketing-assets/{a['_id']}/file"
    elif out["has_file"]:
        out["file_path"] = f"/api/super-admin/partners/assets/{a['_id']}/file"
    return out


def sniff(content: bytes, declared: str) -> str:
    """Validate an upload; returns the file extension."""
    if declared not in FILE_TYPES:
        raise HTTPException(status_code=415, detail="Allowed: PNG, JPEG, GIF, WebP, PDF, MP4, WebM, ZIP (no SVG)")
    if not content:
        raise HTTPException(status_code=422, detail="Empty file")
    if len(content) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 50 MB)")
    ext, magics = FILE_TYPES[declared]
    ok = content[4:8] == b"ftyp" if declared == "video/mp4" else any(content.startswith(m) for m in magics)
    if not ok:
        raise HTTPException(status_code=400, detail="File content does not match its type")
    return ext


async def store_file(content: bytes, ext: str) -> Dict[str, Any]:
    """Existing storage service; private directory for the local backend."""
    from app.storage.service import LocalStorageProvider, get_storage_provider
    name = f"{uuid.uuid4().hex}{ext}"
    provider = get_storage_provider()
    if isinstance(provider, LocalStorageProvider):
        os.makedirs(PRIVATE_DIR, exist_ok=True)
        await LocalStorageProvider(base_dir=PRIVATE_DIR).upload_file(content, name, "", folder=".")
        return {"storage_key": name, "file_url": None}
    url = await provider.upload_file(content, name, "", folder="partner-assets")
    return {"storage_key": None, "file_url": url}


def local_path(asset: Dict[str, Any]) -> Optional[str]:
    key = os.path.basename(asset.get("storage_key") or "")
    if not key:
        return None
    path = os.path.join(PRIVATE_DIR, key)
    return path if os.path.isfile(path) else None
