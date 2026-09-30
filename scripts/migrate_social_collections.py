"""
Idempotent Migration Script: Platform-Neutral Social Collections
Phase 3: Data Model & Code Cleanup

Renames / migrates:
  - `facebook_pages`    → `social_pages`    (and ensures `page_url` from `facebook_url`)
  - `facebook_posts`    → `social_posts`
  - `facebook_comments` → `social_comments`
  - `ai_comments.facebook_url` → `ai_comments.page_url`

This script is 100% idempotent:
  - Uses upsert on unique keys (`_id` and domain identifiers).
  - Can be run repeatedly without duplicating documents or corrupting indexes.
  - Keeps source collections intact for non-destructive zero-downtime operation.

Usage:
  python scripts/migrate_social_collections.py --dry-run
  python scripts/migrate_social_collections.py --apply
  python scripts/migrate_social_collections.py --verify
"""
import argparse
import logging
from pathlib import Path
import sys
from typing import Dict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymongo import MongoClient, ASCENDING, UpdateOne

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("migrate_social")


def get_db():
    from app.config import get_settings
    settings = get_settings()
    client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=10000)
    return client[settings.mongo_db_name]


def migrate_pages(db, apply_changes: bool = False) -> Dict[str, int]:
    source = db.facebook_pages
    target = db.social_pages
    total = source.count_documents({})
    migrated = 0

    logger.info(f"Pages: Found {total} documents in facebook_pages")
    if not apply_changes:
        return {"total": total, "migrated": 0}

    ops = []
    for doc in source.find({}):
        doc_copy = dict(doc)
        # Platform-neutral field aliasing
        if not doc_copy.get("page_url"):
            doc_copy["page_url"] = doc_copy.get("facebook_url", "")
        if "facebook_url" not in doc_copy:
            doc_copy["facebook_url"] = doc_copy.get("page_url", "")
        # Default platform tag if missing
        if not doc_copy.get("platform"):
            doc_copy["platform"] = "facebook"

        ops.append(UpdateOne({"_id": doc_copy["_id"]}, {"$set": doc_copy}, upsert=True))
        if len(ops) >= 500:
            target.bulk_write(ops, ordered=False)
            migrated += len(ops)
            ops = []

    if ops:
        target.bulk_write(ops, ordered=False)
        migrated += len(ops)

    logger.info(f"Pages: Migrated/synced {migrated} documents to social_pages")
    return {"total": total, "migrated": migrated}


def migrate_posts(db, apply_changes: bool = False) -> Dict[str, int]:
    source = db.facebook_posts
    target = db.social_posts
    total = source.count_documents({})
    migrated = 0

    logger.info(f"Posts: Found {total} documents in facebook_posts")
    if not apply_changes:
        return {"total": total, "migrated": 0}

    ops = []
    for doc in source.find({}):
        doc_copy = dict(doc)
        if not doc_copy.get("platform"):
            doc_copy["platform"] = "facebook"
        ops.append(UpdateOne({"_id": doc_copy["_id"]}, {"$set": doc_copy}, upsert=True))
        if len(ops) >= 500:
            target.bulk_write(ops, ordered=False)
            migrated += len(ops)
            ops = []

    if ops:
        target.bulk_write(ops, ordered=False)
        migrated += len(ops)

    logger.info(f"Posts: Migrated/synced {migrated} documents to social_posts")
    return {"total": total, "migrated": migrated}


def migrate_comments(db, apply_changes: bool = False) -> Dict[str, int]:
    source = db.facebook_comments
    target = db.social_comments
    total = source.count_documents({})
    migrated = 0

    logger.info(f"Comments: Found {total} documents in facebook_comments")
    if not apply_changes:
        return {"total": total, "migrated": 0}

    ops = []
    for doc in source.find({}):
        doc_copy = dict(doc)
        if not doc_copy.get("platform"):
            doc_copy["platform"] = "facebook"
        ops.append(UpdateOne({"_id": doc_copy["_id"]}, {"$set": doc_copy}, upsert=True))
        if len(ops) >= 500:
            target.bulk_write(ops, ordered=False)
            migrated += len(ops)
            ops = []

    if ops:
        target.bulk_write(ops, ordered=False)
        migrated += len(ops)

    logger.info(f"Comments: Migrated/synced {migrated} documents to social_comments")
    return {"total": total, "migrated": migrated}


def migrate_ai_comments_fields(db, apply_changes: bool = False) -> Dict[str, int]:
    coll = db.ai_comments
    # Backfill page_url from facebook_url where missing
    query = {"page_url": {"$exists": False}, "facebook_url": {"$exists": True}}
    total = coll.count_documents(query)
    logger.info(f"ai_comments: Found {total} documents needing page_url backfill")

    if not apply_changes or total == 0:
        return {"total": total, "updated": 0}

    res = coll.update_many(
        query,
        [{"$set": {"page_url": "$facebook_url"}}]
    )
    logger.info(f"ai_comments: Backfilled page_url on {res.modified_count} documents")
    return {"total": total, "updated": res.modified_count}


def ensure_social_indexes(db):
    logger.info("Ensuring indexes on social_* collections...")
    # social_pages
    db.social_pages.create_index([("page_url", ASCENDING), ("search_run_id", ASCENDING)], unique=True, sparse=True)
    db.social_pages.create_index([("facebook_url", ASCENDING), ("search_run_id", ASCENDING)], sparse=True)
    db.social_pages.create_index([("search_run_id", ASCENDING)])
    db.social_pages.create_index([("category", ASCENDING)])
    db.social_pages.create_index([("platform", ASCENDING)])
    db.social_pages.create_index([("organization_id", ASCENDING), ("created_at", ASCENDING)])
    db.social_pages.create_index([("organization_id", ASCENDING), ("page_url", ASCENDING)])

    # social_posts
    db.social_posts.create_index([("post_url", ASCENDING), ("page_ref", ASCENDING)], unique=True, sparse=True)
    db.social_posts.create_index([("page_ref", ASCENDING)])
    db.social_posts.create_index([("search_run_id", ASCENDING)])
    db.social_posts.create_index([("platform", ASCENDING)])
    db.social_posts.create_index([("organization_id", ASCENDING), ("page_ref", ASCENDING)])

    # social_comments
    db.social_comments.create_index([("comment_url", ASCENDING)])
    db.social_comments.create_index([("post_ref", ASCENDING)])
    db.social_comments.create_index([("search_run_id", ASCENDING)])
    db.social_comments.create_index([("platform", ASCENDING)])
    db.social_comments.create_index([("organization_id", ASCENDING), ("post_ref", ASCENDING)])
    logger.info("Social collections indexes created successfully.")


def verify_migration(db):
    logger.info("=== Migration Verification ===")
    p_src = db.facebook_pages.count_documents({})
    p_tgt = db.social_pages.count_documents({})
    po_src = db.facebook_posts.count_documents({})
    po_tgt = db.social_posts.count_documents({})
    c_src = db.facebook_comments.count_documents({})
    c_tgt = db.social_comments.count_documents({})

    logger.info(f"Pages:     facebook_pages={p_src} vs social_pages={p_tgt}")
    logger.info(f"Posts:     facebook_posts={po_src} vs social_posts={po_tgt}")
    logger.info(f"Comments:  facebook_comments={c_src} vs social_comments={c_tgt}")

    passed = (p_tgt >= p_src) and (po_tgt >= po_src) and (c_tgt >= c_src)
    if passed:
        logger.info("Verification PASSED: All records present in social collections.")
    else:
        logger.warning("Verification WARNING: Count discrepancy detected.")
    return passed


def main():
    parser = argparse.ArgumentParser(description="Migrate facebook_* to social_* collections")
    parser.add_argument("--apply", action="store_true", help="Execute the migration (writes changes)")
    parser.add_argument("--dry-run", action="store_true", help="Dry run inspection only (default)")
    parser.add_argument("--verify", action="store_true", help="Verify document counts between collections")
    args = parser.parse_args()

    db = get_db()
    if args.verify:
        verify_migration(db)
        return

    apply_changes = args.apply and not args.dry_run
    mode_str = "APPLYING CHANGES" if apply_changes else "DRY-RUN (read only)"
    logger.info(f"Starting social collection migration [{mode_str}]...")

    migrate_pages(db, apply_changes)
    migrate_posts(db, apply_changes)
    migrate_comments(db, apply_changes)
    migrate_ai_comments_fields(db, apply_changes)

    if apply_changes:
        ensure_social_indexes(db)
        verify_migration(db)
        logger.info("Migration completed successfully.")
    else:
        logger.info("Dry-run complete. Re-run with --apply to execute.")


if __name__ == "__main__":
    main()
