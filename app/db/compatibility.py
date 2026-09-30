"""
Social Collections Compatibility Layer
Phase 3: Data Model & Code Cleanup

Provides seamless compatibility between legacy `facebook_*` collections
and platform-neutral `social_*` collections (`social_pages`, `social_posts`,
`social_comments`), as well as field aliasing between `facebook_url` and `page_url`.
"""
from typing import Any, Dict, Optional

SOCIAL_COLLECTION_MAP: Dict[str, str] = {
    "facebook_pages": "social_pages",
    "facebook_posts": "social_posts",
    "facebook_comments": "social_comments",
}

REVERSE_SOCIAL_MAP: Dict[str, str] = {v: k for k, v in SOCIAL_COLLECTION_MAP.items()}


def get_canonical_collection_name(name: str) -> str:
    """Return the platform-neutral collection name for any social collection."""
    return SOCIAL_COLLECTION_MAP.get(name, name)


def normalize_page_doc(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Ensure both `page_url` and `facebook_url` are present on page documents."""
    if not doc or not isinstance(doc, dict):
        return doc
    url = doc.get("page_url") or doc.get("facebook_url") or ""
    if url:
        if not doc.get("page_url"):
            doc["page_url"] = url
        if not doc.get("facebook_url"):
            doc["facebook_url"] = url
    return doc


def normalize_query_for_page_url(query: Dict[str, Any]) -> Dict[str, Any]:
    """Expand a single url query to match either `page_url` or `facebook_url`."""
    if not query or not isinstance(query, dict):
        return query
    q = dict(query)
    if "facebook_url" in q and "page_url" not in q:
        val = q.pop("facebook_url")
        q["$or"] = [{"facebook_url": val}, {"page_url": val}]
    elif "page_url" in q and "facebook_url" not in q:
        val = q.pop("page_url")
        q["$or"] = [{"page_url": val}, {"facebook_url": val}]
    return q


# ---------------------------------------------------------------------------
# Public API used by tests and new call sites
# ---------------------------------------------------------------------------

def normalize_social_doc(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Normalize a social collection document:
      - Ensures both ``page_url`` and ``facebook_url`` fields are populated
        (whichever is present is mirrored to the other).
      - Ensures a ``platform`` field defaults to ``"facebook"`` when absent.
    """
    if not doc or not isinstance(doc, dict):
        return doc

    doc = dict(doc)  # Shallow copy – do not mutate caller's dict

    url = doc.get("page_url") or doc.get("facebook_url") or ""
    if url:
        doc.setdefault("page_url", url)
        doc.setdefault("facebook_url", url)

    doc.setdefault("platform", "facebook")
    return doc


def prepare_social_query(query: Dict[str, Any]) -> Dict[str, Any]:
    """
    Expand a Mongo query targeting a URL field so it matches documents that
    store the URL in either ``page_url`` or ``facebook_url``.

    Alias for ``normalize_query_for_page_url`` with a cleaner name.
    """
    return normalize_query_for_page_url(query)


def get_collection(db: Any, name: str) -> Any:
    """
    Return the appropriate PyMongo collection from *db*.

    Resolution order:
    1. If the canonical (``social_*``) collection exists, return it.
    2. Otherwise fall back to the legacy (``facebook_*``) collection.
    3. Otherwise return the canonical collection handle (may be empty/new).
    """
    existing = set(db.list_collection_names())

    if name in existing:
        return db[name]

    legacy = REVERSE_SOCIAL_MAP.get(name)
    if legacy and legacy in existing:
        return db[legacy]

    return db[name]
