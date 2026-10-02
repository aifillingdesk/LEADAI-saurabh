"""
Tests for Phase 5: Database Backup and Restore Utilities.
"""
import os
import tempfile
from unittest.mock import patch

import mongomock
import pytest
from bson import ObjectId

from scripts.backup_mongodb import run_backup
from scripts.restore_mongodb import run_restore, verify_manifest


@pytest.fixture
def mock_source_db():
    client = mongomock.MongoClient()
    db = client["leadai_prod"]

    # Seed test data
    db.users.insert_many([
        {"_id": ObjectId(), "email": "admin@leadai.com", "role": "super_admin", "org": "org_1"},
        {"_id": ObjectId(), "email": "user@client.com", "role": "user", "org": "org_2"},
    ])
    db.leads.insert_many([
        {"_id": ObjectId(), "name": "John Doe", "phone": "1234567890", "organization_id": "org_1"},
        {"_id": ObjectId(), "name": "Jane Smith", "phone": "0987654321", "organization_id": "org_2"},
    ])
    return client, db


def test_backup_and_restore_cycle(mock_source_db):
    source_client, source_db = mock_source_db

    with tempfile.TemporaryDirectory() as temp_backup_dir:
        # 1. Run backup
        with patch("scripts.backup_mongodb.get_db", return_value=(source_client, source_db, "leadai_prod")):
            manifest = run_backup(
                output_dir=temp_backup_dir,
                retention_days=7,
                compress=True,
                collections_filter=["users", "leads"],
            )

        assert manifest["database"] == "leadai_prod"
        assert manifest["total_documents"] == 4
        assert "users" in manifest["collections"]
        assert "leads" in manifest["collections"]
        assert manifest["collections"]["users"]["document_count"] == 2
        assert manifest["collections"]["leads"]["document_count"] == 2

        backup_id = manifest["backup_id"]
        backup_path = os.path.join(temp_backup_dir, backup_id)
        assert os.path.isdir(backup_path)

        # 2. Verify manifest integrity
        verified_manifest = verify_manifest(backup_path)
        assert verified_manifest["backup_id"] == backup_id

        # 3. Test integrity error on tampered backup
        users_file = os.path.join(backup_path, manifest["collections"]["users"]["filename"])
        with open(users_file, "ab") as f:
            f.write(b"tampered_corrupt_data")

        with pytest.raises(ValueError, match="Checksum mismatch"):
            verify_manifest(backup_path)

        # Re-run clean backup for restore testing
        with patch("scripts.backup_mongodb.get_db", return_value=(source_client, source_db, "leadai_prod")):
            clean_manifest = run_backup(
                output_dir=temp_backup_dir,
                retention_days=7,
                compress=True,
                collections_filter=["users", "leads"],
            )
        clean_backup_path = os.path.join(temp_backup_dir, clean_manifest["backup_id"])

        # 4. Dry-run restore into a target database
        target_client = mongomock.MongoClient()
        target_db = target_client["leadai_restored"]

        with patch("scripts.restore_mongodb.get_db", return_value=(target_client, target_db, "leadai_restored")):
            dry_report = run_restore(clean_backup_path, dry_run=True)
            assert dry_report["dry_run"] is True
            assert target_db.users.count_documents({}) == 0
            assert target_db.leads.count_documents({}) == 0

            # 5. Live restore with drop=True
            live_report = run_restore(clean_backup_path, dry_run=False, drop=True)
            assert live_report["status"] == "success"
            assert live_report["total_documents"] == 4

            # Verify target db has identical documents
            assert target_db.users.count_documents({}) == 2
            assert target_db.leads.count_documents({}) == 2

            user_doc = target_db.users.find_one({"email": "admin@leadai.com"})
            assert user_doc is not None
            assert user_doc["role"] == "super_admin"
