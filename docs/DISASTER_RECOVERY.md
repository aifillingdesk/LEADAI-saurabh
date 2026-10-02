# LeadAI Disaster Recovery & Business Continuity Plan (BCP)

*Document Version:* 1.0.0  
*Phase 5 Deliverable: Reliability and Infrastructure*  
*Last Updated:* October 2026  
*Status:* Approved

---

## 1. Objectives & SLA Commitments

LeadAI defines stringent Recovery Time Objectives (RTO) and Recovery Point Objectives (RPO) to guarantee business continuity, data protection, and minimal downtime across SaaS tenants.

| Metric | Target | Description |
|---|---|---|
| **RPO (Recovery Point Objective)** | **< 1 Hour** | Maximum acceptable data loss window during a disaster scenario. Achieved via continuous replica sync and hourly/daily automated backups. |
| **RTO (Recovery Time Objective)** | **< 15 Minutes** | Maximum acceptable duration to restore service availability following an infrastructure outage or database failure. |
| **Integrity SLA** | **100% Verifiable** | Every backup artifact contains a cryptographic SHA-256 manifest to guarantee tamper-proof restoration. |

---

## 2. Infrastructure Architecture & Redundancy

```mermaid
flowchart TD
    subgraph Primary Region
        Worker1[LeadAI Worker Node 1]
        Worker2[LeadAI Worker Node 2]
        AppServer[FastAPI App Gateway]
        MongoPrimary[(MongoDB Primary)]
        Redis[(Redis Cache & Rate Limiting)]
    end

    subgraph Durable Backup & Secondary
        MongoSecondary[(MongoDB Secondary / Replica)]
        BackupRunner[Automated Backup Job]
        S3Backup[(Encrypted S3 / Cold Storage)]
    end

    AppServer --> MongoPrimary
    Worker1 --> MongoPrimary
    Worker2 --> MongoPrimary
    AppServer --> Redis
    Worker1 --> Redis
    MongoPrimary -.->|Replica Set Sync| MongoSecondary
    BackupRunner -->|Daily/Hourly Export| MongoSecondary
    BackupRunner -->|Compress & Sign| S3Backup
```

### 2.1 Multi-Tenant Isolation Protection
All restoration operations preserve tenant multi-tenancy. Organization boundaries (`organization_id`) are immutable and indexed across all persistent collections (`users`, `leads`, `search_history`, `audit_logs`, `social_*`).

### 2.2 Worker Crash Recovery & Job Queue Resilience
Long-running operations (such as Apify social discovery runs) are orchestrated through the **Durable Mongo Queue** (`app/queue/service.py`).
- Jobs carry lease locks with heartbeat expiration (`locked_until`).
- If an application node crashes mid-execution, orphan worker jobs are automatically recovered (`recover_crashed_jobs()`) and reassigned without duplicate lead generation.

---

## 3. Automated Backup Procedures

LeadAI provides production-grade backup scripts located in [`scripts/backup_mongodb.py`](file:///c:/SG/lead_apify/scripts/backup_mongodb.py).

### 3.1 Backup Engine Capabilities
- **Portability:** Uses streaming native BSON serialization (`bson.json_util`), allowing seamless backups across Linux, Windows, macOS, and container environments without requiring external binary tools.
- **Compression:** High-ratio gzip stream compression enabled by default (`.json.gz`).
- **Cryptographic Manifest:** Produces `manifest.json` recording backup timestamp, collection count, document counts, and SHA-256 checksums per collection.
- **Automated Retention Rotation:** Prunes backups exceeding the retention threshold (default: 7 days).

### 3.2 Scheduled Backup Execution
Add the following cron task or Kubernetes CronJob to execute scheduled backups:

```bash
# Daily full backup at 02:00 UTC with 14-day retention
0 2 * * * python /app/scripts/backup_mongodb.py --output-dir /var/backups/leadai --retention-days 14 --compress
```

---

## 4. Disaster Recovery Runbooks

LeadAI provides an automated, safety-gated restore script in [`scripts/restore_mongodb.py`](file:///c:/SG/lead_apify/scripts/restore_mongodb.py).

### Runbook A: Accidental Data Corruption or Collection Deletion
**Scenario:** A rogue query or operational error corrupted the `leads` or `settings` collection.

1. **Locate the latest verified backup:**
   ```bash
   ls -la /var/backups/leadai/
   ```
2. **Execute dry-run to verify checksums and inspect document counts:**
   ```bash
   python scripts/restore_mongodb.py \
     --backup-dir /var/backups/leadai/leadai_backup_leadai_production_20261002_020000 \
     --dry-run \
     --collections leads
   ```
3. **Apply non-destructive upsert restoration:**
   ```bash
   python scripts/restore_mongodb.py \
     --backup-dir /var/backups/leadai/leadai_backup_leadai_production_20261002_020000 \
     --confirm \
     --collections leads
   ```

---

### Runbook B: Complete Primary Cluster Loss
**Scenario:** Primary database cluster or cloud instance is irrevocably destroyed.

1. **Provision replacement MongoDB instance / Replica set.**
2. **Point application connection strings via environment variables:**
   ```env
   MONGO_URI="mongodb://user:password@new-cluster.internal:27017/leadai"
   MONGO_DB_NAME="leadai"
   ```
3. **Perform dry-run verification of the entire backup archive:**
   ```bash
   python scripts/restore_mongodb.py \
     --backup-dir /var/backups/leadai/leadai_backup_leadai_production_20261002_020000 \
     --dry-run
   ```
4. **Execute clean restoration with drop-and-rebuild:**
   ```bash
   python scripts/restore_mongodb.py \
     --backup-dir /var/backups/leadai/leadai_backup_leadai_production_20261002_020000 \
     --confirm \
     --drop
   ```
5. **Verify system health:**
   ```bash
   curl -f http://localhost:8000/api/health/db
   ```

---

### Runbook C: Media & File Storage Failover
LeadAI supports pluggable storage providers (`local`, `s3`, `cloudinary`) managed via `STORAGE_BACKEND`.

- **Local Storage:** Static assets stored under `static/uploads` and `static/media`. Replicate these volumes via rsync or EBS snapshots.
- **S3 Storage:** Versioning and Cross-Region Replication (CRR) should be enabled on the target S3 bucket (`AWS_S3_BUCKET`).
- **Cloudinary:** Media assets are globally distributed via Cloudinary's multi-CDN infrastructure.

To fail over storage backend during an AWS regional outage:
1. Update environment configuration:
   ```env
   STORAGE_BACKEND="cloudinary"
   CLOUDINARY_CLOUD_NAME="..."
   CLOUDINARY_API_KEY="..."
   CLOUDINARY_API_SECRET="..."
   ```
2. Restart application pods. The storage factory automatically activates the fallback provider.

---

## 5. Verification & Disaster Drill Schedule

To guarantee that backup archives are reliable when an emergency strikes, the following testing schedule is enforced:

| Frequency | Drill Description | Responsible Team |
|---|---|---|
| **Weekly** | Automated backup script runs with immediate manifest checksum check | Automated CI/Cron |
| **Monthly** | Restore latest backup into isolated staging database and verify test suite passes | DevOps / Platform |
| **Quarterly** | Simulated primary node termination & failover to replica set | Engineering Lead |

---
