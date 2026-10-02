"""
LeadAI MongoDB Restore Script (Phase 5: Disaster Recovery & Infrastructure).

Restores MongoDB collections from an verified backup directory produced by backup_mongodb.py.
Features:
- Mandatory SHA-256 checksum verification before applying any writes.
- Dry-run mode (--dry-run) to inspect document counts and verify integrity safely.
- Safety confirmation flag (--confirm) to prevent accidental production overwrite.
- Selective collection restore and drop/upsert modes.

Usage:
  python scripts/restore_mongodb.py --backup-dir ./backups/leadai_backup_... --dry-run
  python scripts/restore_mongodb.py --backup-dir ./backups/leadai_backup_... --confirm
  python scripts/restore_mongodb.py --backup-dir ./backups/leadai_backup_... --confirm --drop
"""
import argparse
import gzip
import hashlib
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bson import json_util
from pymongo import MongoClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("restore_mongodb")


def get_db(uri: str | None = None, db_name: str | None = None):
    from app.config import get_settings
    settings = get_settings()
    mongo_uri = uri or settings.mongo_uri
    database_name = db_name or settings.mongo_db_name
    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=10000)
    return client, client[database_name], database_name


def compute_sha256(filepath: str) -> str:
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def verify_manifest(backup_dir: str) -> dict[str, Any]:
    manifest_path = os.path.join(backup_dir, "manifest.json")
    if not os.path.isfile(manifest_path):
        raise FileNotFoundError(f"Manifest not found in backup directory: {manifest_path}")

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    logger.info(f"Loaded manifest for backup '{manifest.get('backup_id')}'. Verifying integrity...")

    collections = manifest.get("collections", {})
    for col_name, col_meta in collections.items():
        fname = col_meta.get("filename")
        expected_hash = col_meta.get("sha256")
        fpath = os.path.join(backup_dir, fname)

        if not os.path.isfile(fpath):
            raise FileNotFoundError(f"Missing file for collection '{col_name}': {fpath}")

        actual_hash = compute_sha256(fpath)
        if actual_hash != expected_hash:
            raise ValueError(
                f"Checksum mismatch for '{col_name}' ({fname})! "
                f"Expected {expected_hash}, got {actual_hash}. Backup may be corrupted."
            )

    logger.info(f"Integrity check passed: {len(collections)} collection dumps verified.")
    return manifest


def run_restore(
    backup_dir: str,
    dry_run: bool = False,
    drop: bool = False,
    collections_filter: list[str] | None = None,
    uri: str | None = None,
    db_name: str | None = None,
) -> dict[str, Any]:
    manifest = verify_manifest(backup_dir)
    target_collections = manifest.get("collections", {})

    if collections_filter:
        target_collections = {k: v for k, v in target_collections.items() if k in collections_filter}

    client, db, actual_db_name = get_db(uri, db_name)
    logger.info(f"Target database for restore: '{actual_db_name}' (Dry run: {dry_run}, Drop: {drop})")

    results = {}
    total_restored = 0

    for col_name, col_meta in target_collections.items():
        fname = col_meta["filename"]
        fpath = os.path.join(backup_dir, fname)
        compressed = col_meta.get("compressed", fname.endswith(".gz"))

        logger.info(f"Processing '{col_name}' from {fname}...")

        docs_to_insert: list[dict[str, Any]] = []
        doc_count = 0

        open_fn = gzip.open if compressed else open
        with open_fn(fpath, "rt", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                doc = json_util.loads(line)
                doc_count += 1
                if not dry_run:
                    docs_to_insert.append(doc)

        if dry_run:
            logger.info(f"[DRY-RUN] '{col_name}': {doc_count} documents verified (no changes applied)")
            results[col_name] = {"documents_in_dump": doc_count, "restored": 0, "status": "dry_run_verified"}
        else:
            col = db[col_name]
            if drop:
                logger.warning(f"Dropping collection '{col_name}' before restore...")
                col.drop()
                if docs_to_insert:
                    chunk_size = 1000
                    for i in range(0, len(docs_to_insert), chunk_size):
                        col.insert_many(docs_to_insert[i : i + chunk_size], ordered=False)
            else:
                for doc in docs_to_insert:
                    if "_id" in doc:
                        col.replace_one({"_id": doc["_id"]}, doc, upsert=True)
                    else:
                        col.insert_one(doc)

            total_restored += doc_count
            logger.info(f"Restored '{col_name}': {doc_count} documents applied")
            results[col_name] = {"documents_in_dump": doc_count, "restored": doc_count, "status": "restored"}

    client.close()
    return {
        "status": "success",
        "dry_run": dry_run,
        "database": actual_db_name,
        "total_documents": total_restored if not dry_run else 0,
        "collections": results,
    }


def main():
    parser = argparse.ArgumentParser(description="LeadAI MongoDB Restore Utility")
    parser.add_argument("--backup-dir", required=True, help="Directory of the backup containing manifest.json")
    parser.add_argument("--dry-run", action="store_true", help="Simulate restore and verify checksums without writing")
    parser.add_argument("--confirm", action="store_true", help="Explicit confirmation gate to apply live writes")
    parser.add_argument("--drop", action="store_true", help="Drop target collections before restore (default: upsert)")
    parser.add_argument("--collections", default="", help="Comma-separated collections to restore (default: all)")
    parser.add_argument("--uri", default=None, help="MongoDB connection URI (default: from app config)")
    parser.add_argument("--db", default=None, help="MongoDB database name (default: from app config)")

    args = parser.parse_args()

    if not args.dry_run and not args.confirm:
        print("ERROR: Safety gate triggered. You must supply --confirm or --dry-run to proceed.")
        sys.exit(2)

    cols = [c.strip() for c in args.collections.split(",") if c.strip()] if args.collections else None

    try:
        report = run_restore(
            backup_dir=args.backup_dir,
            dry_run=args.dry_run,
            drop=args.drop,
            collections_filter=cols,
            uri=args.uri,
            db_name=args.db,
        )
        print(json.dumps(report, indent=2))
    except Exception:
        logger.exception("Restore failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
