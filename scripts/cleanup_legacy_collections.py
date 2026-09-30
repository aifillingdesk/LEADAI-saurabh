"""
Legacy Collections & User Consolidation Script
Phase 3: Data Model & Code Cleanup

Consolidates legacy collections and ensures single source of truth:
1. `admin_users` -> `users` consolidation:
   - Migrates any legacy admin records into the canonical `users` collection.
   - Sets `is_platform_admin=True` and sets proper `role`/`platform_role`.
   - Preserves password hashes and metadata.
2. Empty Collection Detection & Safe Cleanup:
   - Detects obsolete/empty collections.
   - Flags orphaned legacy collections (e.g. `facebook_*` once migrated).
   - Only drops collections when explicitly instructed via `--drop-legacy` or `--drop-empty`.

Defaults to `--dry-run` mode to prevent accidental data loss.

Usage:
  python scripts/cleanup_legacy_collections.py --dry-run
  python scripts/cleanup_legacy_collections.py --apply
  python scripts/cleanup_legacy_collections.py --apply --drop-empty
"""
import argparse
import datetime
import logging
from pathlib import Path
import sys
from typing import Dict, Any, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymongo import MongoClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("cleanup_legacy")


def get_db():
    from app.config import get_settings
    settings = get_settings()
    client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=10000)
    return client[settings.mongo_db_name]


def audit_collections(db) -> Dict[str, Any]:
    """Inspect all collections, document counts, and classification."""
    colls = db.list_collection_names()
    known_canonical = {
        "users", "organizations", "social_pages", "social_posts",
        "social_comments", "ai_comments", "search_history", "system_settings",
        "invitations", "audit_logs", "activity_logs", "api_keys", "rate_limits"
    }
    legacy_or_candidate = {
        "admin_users", "facebook_pages", "facebook_posts", "facebook_comments"
    }

    report = {
        "canonical": {},
        "legacy": {},
        "empty": [],
        "other": {}
    }

    for name in colls:
        if name.startswith("system."):
            continue
        count = db[name].count_documents({})
        if count == 0:
            report["empty"].append(name)

        if name in known_canonical:
            report["canonical"][name] = count
        elif name in legacy_or_candidate:
            report["legacy"][name] = count
        else:
            report["other"][name] = count

    return report


def consolidate_admin_users(db, apply_changes: bool = False) -> Dict[str, int]:
    """
    Consolidate legacy `admin_users` documents into `users` collection.
    Canonical schema for platform admin:
      - is_platform_admin: True
      - platform_role: 'operations_admin' | 'support_admin' | 'analyst' (defaults to 'operations_admin')
      - role: 'superadmin' | 'admin'
    """
    admin_users = db.admin_users
    users = db.users

    total_admins = admin_users.count_documents({})
    logger.info(f"admin_users: Found {total_admins} documents in legacy collection")

    if total_admins == 0:
        return {"total": 0, "migrated": 0, "already_existing": 0}

    migrated = 0
    already_existing = 0

    for doc in admin_users.find({}):
        email = doc.get("email") or doc.get("username")
        if not email:
            logger.warning(f"Skipping admin document without email/username: {doc.get('_id')}")
            continue

        existing = users.find_one({"$or": [{"email": email}, {"username": email}]})
        if existing:
            already_existing += 1
            if apply_changes and not existing.get("is_platform_admin"):
                users.update_one(
                    {"_id": existing["_id"]},
                    {"$set": {
                        "is_platform_admin": True,
                        "platform_role": existing.get("platform_role") or "operations_admin",
                        "updated_at": datetime.datetime.now(datetime.timezone.utc),
                    }}
                )
                logger.info(f"Updated existing user '{email}' with is_platform_admin=True")
            continue

        # Create new user in `users`
        new_user = {
            "email": email,
            "username": doc.get("username") or email,
            "hashed_password": doc.get("hashed_password") or doc.get("password_hash") or doc.get("password", ""),
            "full_name": doc.get("full_name") or doc.get("name") or "Platform Administrator",
            "is_active": doc.get("is_active", True),
            "is_platform_admin": True,
            "platform_role": doc.get("platform_role") or "operations_admin",
            "role": doc.get("role") or "superadmin",
            "organization_id": doc.get("organization_id") or "system",
            "created_at": doc.get("created_at") or datetime.datetime.now(datetime.timezone.utc),
            "updated_at": datetime.datetime.now(datetime.timezone.utc),
            "_legacy_source": "admin_users"
        }

        if apply_changes:
            users.insert_one(new_user)
            logger.info(f"Migrated admin user '{email}' to users collection")
        migrated += 1

    return {"total": total_admins, "migrated": migrated, "already_existing": already_existing}


def drop_empty_collections(db, empty_colls: List[str], apply_changes: bool = False) -> List[str]:
    """Drops collections that have zero documents."""
    dropped = []
    for coll_name in empty_colls:
        if coll_name in ("users", "organizations", "social_pages", "social_posts", "social_comments", "ai_comments"):
            logger.warning(f"Preserving core collection even though empty: {coll_name}")
            continue
        if apply_changes:
            db[coll_name].drop()
            logger.info(f"Dropped empty collection: {coll_name}")
            dropped.append(coll_name)
        else:
            logger.info(f"[DRY-RUN] Would drop empty collection: {coll_name}")
            dropped.append(coll_name)
    return dropped


def drop_legacy_collections(db, apply_changes: bool = False) -> List[str]:
    """
    Safely drops legacy collections (e.g., admin_users) after consolidation.
    """
    dropped = []
    # Check admin_users
    admin_count = db.admin_users.count_documents({})
    if admin_count == 0 or apply_changes:
        # Check if users collection has platform admins
        platform_admins = db.users.count_documents({"is_platform_admin": True})
        if platform_admins > 0:
            if apply_changes:
                db.admin_users.drop()
                logger.info("Dropped legacy 'admin_users' collection (consolidated into users)")
                dropped.append("admin_users")
            else:
                logger.info("[DRY-RUN] Would drop legacy 'admin_users' collection")
                dropped.append("admin_users")
        else:
            logger.warning("Refusing to drop 'admin_users': No platform admins found in 'users'")
    return dropped


def main():
    parser = argparse.ArgumentParser(description="Audit and clean up legacy collections and users")
    parser.add_argument("--dry-run", action="store_true", default=True, help="Simulate cleanup without modifying data (default)")
    parser.add_argument("--apply", action="store_true", help="Apply consolidation changes")
    parser.add_argument("--drop-empty", action="store_true", help="Drop confirmed empty non-core collections")
    parser.add_argument("--drop-legacy", action="store_true", help="Drop legacy admin_users after consolidation")
    args = parser.parse_args()

    apply_changes = args.apply
    if apply_changes:
        args.dry_run = False

    logger.info("Connecting to MongoDB...")
    db = get_db()

    logger.info("=== AUDIT OF ALL COLLECTIONS ===")
    audit = audit_collections(db)
    logger.info(f"Canonical collections: {audit['canonical']}")
    logger.info(f"Legacy collections:    {audit['legacy']}")
    logger.info(f"Other collections:     {audit['other']}")
    logger.info(f"Empty collections:     {audit['empty']}")

    logger.info("=== CONSOLIDATING ADMIN USERS ===")
    admin_result = consolidate_admin_users(db, apply_changes=apply_changes)
    logger.info(f"Admin Users Consolidation: {admin_result}")

    if args.drop_empty and audit["empty"]:
        logger.info("=== DROPPING EMPTY COLLECTIONS ===")
        dropped_empty = drop_empty_collections(db, audit["empty"], apply_changes=apply_changes)
        logger.info(f"Empty collections dropped: {dropped_empty}")

    if args.drop_legacy:
        logger.info("=== DROPPING LEGACY COLLECTIONS ===")
        dropped_legacy = drop_legacy_collections(db, apply_changes=apply_changes)
        logger.info(f"Legacy collections dropped: {dropped_legacy}")

    if not apply_changes:
        logger.info("DRY RUN COMPLETE. Pass --apply to execute changes.")
    else:
        logger.info("CLEANUP APPLIED SUCCESSFULLY.")


if __name__ == "__main__":
    main()
