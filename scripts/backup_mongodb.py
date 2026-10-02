"""
LeadAI MongoDB Backup Script (Phase 5: Disaster Recovery & Infrastructure).

Performs durable, verifiable backups of LeadAI MongoDB collections.
Supports native BSON-compatible streaming (bson.json_util) with gzip compression
so it executes cleanly on any environment without external binary dependencies,
with automatic rotation based on retention policy and SHA-256 checksum manifests.

Usage:
  python scripts/backup_mongodb.py
  python scripts/backup_mongodb.py --output-dir ./backups --retention-days 7 --compress
  python scripts/backup_mongodb.py --collections users,leads,settings
"""
import argparse
import datetime
import gzip
import hashlib
import json
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bson import json_util
from pymongo import MongoClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backup_mongodb")


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


def run_backup(
    output_dir: str = "./backups",
    retention_days: int = 7,
    compress: bool = True,
    collections_filter: list[str] | None = None,
    uri: str | None = None,
    db_name: str | None = None,
) -> dict[str, Any]:
    client, db, actual_db_name = get_db(uri, db_name)
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    backup_id = f"leadai_backup_{actual_db_name}_{now_utc.strftime('%Y%m%d_%H%M%S')}"
    target_dir = os.path.join(output_dir, backup_id)
    os.makedirs(target_dir, exist_ok=True)

    available_cols = sorted(db.list_collection_names())
    # Exclude system collections
    cols_to_dump = [c for c in available_cols if not c.startswith("system.")]
    if collections_filter:
        cols_to_dump = [c for c in cols_to_dump if c in collections_filter]

    logger.info(f"Starting backup for database '{actual_db_name}' -> {target_dir}")
    logger.info(f"Targeting {len(cols_to_dump)} collections: {', '.join(cols_to_dump)}")

    manifest_collections = {}
    total_docs = 0
    total_bytes = 0

    for col_name in cols_to_dump:
        col = db[col_name]
        doc_count = col.count_documents({})
        ext = ".json.gz" if compress else ".json"
        out_filename = f"{col_name}{ext}"
        out_filepath = os.path.join(target_dir, out_filename)

        logger.info(f"Dumping collection '{col_name}' ({doc_count} documents)...")

        written_count = 0
        if compress:
            with gzip.open(out_filepath, "wt", encoding="utf-8") as gf:
                for doc in col.find({}):
                    gf.write(json_util.dumps(doc) + "\n")
                    written_count += 1
        else:
            with open(out_filepath, "w", encoding="utf-8") as f:
                for doc in col.find({}):
                    f.write(json_util.dumps(doc) + "\n")
                    written_count += 1

        file_size = os.path.getsize(out_filepath)
        checksum = compute_sha256(out_filepath)
        total_docs += written_count
        total_bytes += file_size

        manifest_collections[col_name] = {
            "document_count": written_count,
            "filename": out_filename,
            "size_bytes": file_size,
            "sha256": checksum,
            "compressed": compress,
        }

    manifest = {
        "backup_id": backup_id,
        "database": actual_db_name,
        "created_at": now_utc.isoformat(),
        "leadai_version": "1.0.0",
        "format": "json_util_stream",
        "total_collections": len(manifest_collections),
        "total_documents": total_docs,
        "total_bytes": total_bytes,
        "collections": manifest_collections,
    }

    manifest_path = os.path.join(target_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as mf:
        json.dump(manifest, mf, indent=2)

    logger.info(f"Backup completed successfully: {total_docs} docs, {total_bytes} bytes in {manifest_path}")

    # Retention rotation
    if retention_days > 0 and os.path.isdir(output_dir):
        cutoff = now_utc - datetime.timedelta(days=retention_days)
        logger.info(f"Checking for backups older than {retention_days} days (cutoff: {cutoff.isoformat()})...")
        for entry in os.listdir(output_dir):
            entry_path = os.path.join(output_dir, entry)
            if os.path.isdir(entry_path) and entry.startswith("leadai_backup_"):
                mf_check = os.path.join(entry_path, "manifest.json")
                if os.path.exists(mf_check):
                    try:
                        with open(mf_check, "r", encoding="utf-8") as cf:
                            data = json.load(cf)
                        c_time = datetime.datetime.fromisoformat(data["created_at"])
                        if c_time < cutoff:
                            logger.info(f"Rotating/removing old backup: {entry}")
                            shutil.rmtree(entry_path, ignore_errors=True)
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"Failed to check manifest for rotation on {entry}: {e}")

    client.close()
    return manifest


def main():
    parser = argparse.ArgumentParser(description="LeadAI MongoDB Backup Utility")
    parser.add_argument("--output-dir", default="./backups", help="Target directory for backups")
    parser.add_argument("--retention-days", type=int, default=7, help="Days of backups to keep (0 = keep all)")
    parser.add_argument("--no-compress", action="store_true", help="Disable gzip compression")
    parser.add_argument("--collections", default="", help="Comma-separated collections to backup (default: all)")
    parser.add_argument("--uri", default=None, help="MongoDB connection URI (default: from app config)")
    parser.add_argument("--db", default=None, help="MongoDB database name (default: from app config)")

    args = parser.parse_args()
    cols = [c.strip() for c in args.collections.split(",") if c.strip()] if args.collections else None

    try:
        manifest = run_backup(
            output_dir=args.output_dir,
            retention_days=args.retention_days,
            compress=not args.no_compress,
            collections_filter=cols,
            uri=args.uri,
            db_name=args.db,
        )
        print(json.dumps(manifest, indent=2))
    except Exception:
        logger.exception("Backup failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
