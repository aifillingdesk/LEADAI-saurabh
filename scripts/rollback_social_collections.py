"""
Rollback Script: Revert Social Collections to Legacy facebook_*
Phase 3: Data Model & Code Cleanup

Copies / syncs:
  - `social_pages`    → `facebook_pages`
  - `social_posts`    → `facebook_posts`
  - `social_comments` → `facebook_comments`

This ensures that any documents created while running in platform-neutral
mode can be cleanly restored to legacy collections if a rollback is triggered.

Usage:
  python scripts/rollback_social_collections.py --dry-run
  python scripts/rollback_social_collections.py --apply
"""
import argparse
import logging
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymongo import MongoClient, UpdateOne

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("rollback_social")


def get_db():
    from app.config import get_settings
    settings = get_settings()
    client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=10000)
    return client[settings.mongo_db_name]


def rollback_collection(db, source_name: str, target_name: str, apply_changes: bool = False):
    source = db[source_name]
    target = db[target_name]
    total = source.count_documents({})
    logger.info(f"Rollback {source_name} → {target_name}: Found {total} documents")

    if not apply_changes:
        return total

    ops = []
    synced = 0
    for doc in source.find({}):
        doc_copy = dict(doc)
        if "page_url" in doc_copy and not doc_copy.get("facebook_url"):
            doc_copy["facebook_url"] = doc_copy["page_url"]
        ops.append(UpdateOne({"_id": doc_copy["_id"]}, {"$set": doc_copy}, upsert=True))
        if len(ops) >= 500:
            target.bulk_write(ops, ordered=False)
            synced += len(ops)
            ops = []

    if ops:
        target.bulk_write(ops, ordered=False)
        synced += len(ops)

    logger.info(f"Synced {synced} documents to {target_name}")
    return synced


def main():
    parser = argparse.ArgumentParser(description="Rollback social_* collections to facebook_*")
    parser.add_argument("--apply", action="store_true", help="Execute the rollback sync")
    parser.add_argument("--dry-run", action="store_true", help="Dry run inspection only (default)")
    args = parser.parse_args()

    db = get_db()
    apply_changes = args.apply and not args.dry_run
    mode = "APPLYING ROLLBACK" if apply_changes else "DRY-RUN (read only)"
    logger.info(f"Starting rollback sync [{mode}]...")

    rollback_collection(db, "social_pages", "facebook_pages", apply_changes)
    rollback_collection(db, "social_posts", "facebook_posts", apply_changes)
    rollback_collection(db, "social_comments", "facebook_comments", apply_changes)

    if apply_changes:
        logger.info("Rollback synchronization complete.")
    else:
        logger.info("Dry-run complete. Run with --apply to execute.")


if __name__ == "__main__":
    main()
