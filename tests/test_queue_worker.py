"""
Tests for Phase 5: Durable Job Queue & Worker Architecture.
"""
from datetime import timedelta
from unittest.mock import patch

import mongomock
import pytest

from app.db.models import utcnow
from app.queue import service as qs


@pytest.fixture
def mock_queue_db():
    client = mongomock.MongoClient()
    db = client["leadai_queue_test"]
    return db


def test_enqueue_job(mock_queue_db):
    with patch("app.queue.service.get_sync_db", return_value=mock_queue_db):
        job_id = qs.enqueue_job(
            task_name="url_search",
            payload={"url": "https://facebook.com/testpage", "limit": 10},
            run_id="run_101",
            organization_id="org_test",
            max_retries=3,
        )

        assert job_id == "run_101"
        job = mock_queue_db[qs.COLL_JOBS].find_one({"job_id": "run_101"})
        assert job is not None
        assert job["status"] == "queued"
        assert job["task_name"] == "url_search"
        assert job["organization_id"] == "org_test"
        assert job["payload"]["url"] == "https://facebook.com/testpage"
        assert job["retries"] == 0
        assert job["locked_by"] is None


def test_claim_job_lease_locking(mock_queue_db):
    with patch("app.queue.service.get_sync_db", return_value=mock_queue_db):
        qs.enqueue_job(
            task_name="test_task",
            payload={"x": 1},
            run_id="run_claim_1",
        )

        # Worker 1 claims job
        claimed = qs.claim_job(worker_id="worker_alpha", lock_duration_sec=60)
        assert claimed is not None
        assert claimed["job_id"] == "run_claim_1"
        assert claimed["status"] == "running"
        assert claimed["locked_by"] == "worker_alpha"
        assert claimed["retries"] == 1
        assert claimed["locked_until"] is not None

        # Worker 2 attempts to claim while locked -> none returned
        claimed_2 = qs.claim_job(worker_id="worker_beta", lock_duration_sec=60)
        assert claimed_2 is None


def test_heartbeat_and_complete(mock_queue_db):
    with patch("app.queue.service.get_sync_db", return_value=mock_queue_db):
        qs.enqueue_job(task_name="test_task", payload={}, run_id="run_hb_1")
        qs.claim_job(worker_id="worker_alpha", lock_duration_sec=30)

        # Extend lease
        extended = qs.heartbeat_job(job_id="run_hb_1", worker_id="worker_alpha", extend_sec=120)
        assert extended is True

        # Different worker cannot heartbeat
        failed_hb = qs.heartbeat_job(job_id="run_hb_1", worker_id="worker_imposter", extend_sec=120)
        assert failed_hb is False

        # Complete job
        completed = qs.complete_job(job_id="run_hb_1", result={"leads_found": 5})
        assert completed is True

        job = mock_queue_db[qs.COLL_JOBS].find_one({"job_id": "run_hb_1"})
        assert job["status"] == "completed"
        assert job["result"]["leads_found"] == 5
        assert "locked_by" not in job


def test_fail_job_retry_backoff(mock_queue_db):
    with patch("app.queue.service.get_sync_db", return_value=mock_queue_db):
        qs.enqueue_job(task_name="test_task", payload={}, run_id="run_fail_1", max_retries=2)
        qs.claim_job(worker_id="worker_alpha")

        # 1st failure (attempt 1/2) -> requeue
        requeued = qs.fail_job(job_id="run_fail_1", error="Transient network error", can_retry=True)
        assert requeued is True
        job = mock_queue_db[qs.COLL_JOBS].find_one({"job_id": "run_fail_1"})
        assert job["status"] == "queued"
        assert job["error"] == "Transient network error"

        # Claim 2nd time
        qs.claim_job(worker_id="worker_alpha")

        # 2nd failure (attempt 2/2) -> permanent failure
        qs.fail_job(job_id="run_fail_1", error="Persistent timeout", can_retry=True)
        job = mock_queue_db[qs.COLL_JOBS].find_one({"job_id": "run_fail_1"})
        assert job["status"] == "failed"


def test_cancellation_flow(mock_queue_db):
    with patch("app.queue.service.get_sync_db", return_value=mock_queue_db):
        qs.enqueue_job(task_name="url_search", payload={}, run_id="run_cancel_1")

        # Initial check
        assert qs.is_job_cancelled("run_cancel_1") is False

        # Request cancellation
        cancelled = qs.cancel_job("run_cancel_1", cancelled_by="admin@test.com")
        assert cancelled is True

        # Now is_job_cancelled returns True
        assert qs.is_job_cancelled("run_cancel_1") is True

        job = mock_queue_db[qs.COLL_JOBS].find_one({"run_id": "run_cancel_1"})
        assert job["status"] == "cancelled"


def test_recover_crashed_jobs(mock_queue_db):
    with patch("app.queue.service.get_sync_db", return_value=mock_queue_db):
        now = utcnow()
        expired_time = now - timedelta(seconds=300)

        # 1. Job that crashed with retries remaining
        mock_queue_db[qs.COLL_JOBS].insert_one({
            "job_id": "crashed_1",
            "status": "running",
            "retries": 1,
            "max_retries": 3,
            "locked_by": "dead_worker",
            "locked_until": expired_time,
            "updated_at": expired_time,
        })

        # 2. Job that crashed with retries exhausted
        mock_queue_db[qs.COLL_JOBS].insert_one({
            "job_id": "crashed_exhausted",
            "status": "running",
            "retries": 3,
            "max_retries": 3,
            "locked_by": "dead_worker",
            "locked_until": expired_time,
            "updated_at": expired_time,
        })

        # Recover
        recovered_count = qs.recover_crashed_jobs(max_lease_sec=60)
        assert recovered_count == 2

        j1 = mock_queue_db[qs.COLL_JOBS].find_one({"job_id": "crashed_1"})
        assert j1["status"] == "queued"
        assert "locked_by" not in j1

        j2 = mock_queue_db[qs.COLL_JOBS].find_one({"job_id": "crashed_exhausted"})
        assert j2["status"] == "failed"


def test_queue_stats(mock_queue_db):
    with patch("app.queue.service.get_sync_db", return_value=mock_queue_db):
        qs.enqueue_job("task_1", {}, run_id="q1")
        qs.enqueue_job("task_2", {}, run_id="q2")
        qs.claim_job("w1")

        stats = qs.get_queue_stats()
        assert stats["total"] == 2
        assert stats["running"] == 1
        assert stats["queued"] == 1
