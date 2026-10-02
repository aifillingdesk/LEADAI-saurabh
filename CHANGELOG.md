# Changelog

All notable changes to LeadAI are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [2.0.0] - 2026-10-02

### Added
- **Phase 7: Comprehensive Documentation Architecture**:
  - Restructured monolithic `README.md` into modular documentation in `docs/`.
  - Added `docs/ARCHITECTURE.md`, `docs/API.md`, `docs/ADMIN_PORTALS.md`, `docs/DEPLOYMENT.md`, `docs/SECURITY.md`, `docs/ROADMAP.md`, `docs/KNOWN_LIMITATIONS.md`, and `docs/UPGRADE_NOTES.md`.
  - Added `LICENSE` (MIT License) and `CONTRIBUTING.md`.

## [1.6.0] - 2026-10-02

### Added
- **Phase 6: Enterprise Product Features**:
  - YouTube comment scraping and video lead extraction pipeline.
  - Recurring / scheduled search scans (`scheduled_scans`) with interval hours and automated execution.
  - Bulk URL submission endpoint for multi-URL concurrent processing.
  - Cross-platform & cross-run lead deduplication engine (`app/pipeline/deduplication.py`) matching phone, email, and social handles.
  - Automated lead assignment rules with SLA timers, round-robin allocation, and reminder alerts.
  - Outbound webhook subsystem (`outbound_webhooks`) with HMAC-SHA256 signature verification.
  - CRM connectors: HubSpot, Zoho CRM, Google Sheets, and Excel (.xlsx) streaming export with formula injection sanitization.
  - Public REST API v1 (`/api/v1/leads`, `/api/v1/search`) authenticated via SHA-256 hashed API keys with per-org scopes and rate limiting.
  - Razorpay billing provider with UPI checkout, webhook verification, and INR pricing plans.
  - Unit-economics reporting: Gemini token costs, Apify compute costs, and per-tier margin tracking (`docs/UNIT_ECONOMICS.md` and `GET /api/super-admin/unit-economics`).

## [1.5.0] - 2026-10-01

### Added
- **Phase 5: Reliability & Scalability Infrastructure**:
  - Durable job queue abstraction (`app/queue/service.py`) supporting Redis RQ/Arq and MongoDB fallbacks.
  - Pluggable media storage service (`app/storage/service.py`) supporting Local filesystem, AWS S3, and Cloudinary.
  - Automated MongoDB backup and restore tools (`scripts/backup_mongodb.py`, `scripts/restore_mongodb.py`).
  - Disaster recovery runbook (`docs/DISASTER_RECOVERY.md`).
  - GitHub Actions CI workflow for automated linting, type-checking, and test execution.

## [1.4.0] - 2026-09-30

### Added
- **Phase 4: Compliance & Security Hardening**:
  - Prompt-injection defense: delimited untrusted-data boundaries and strict JSON schema validation.
  - GDPR/CCPA compliance engine (`docs/COMPLIANCE.md`): PII auto-purge retention policies, contact blocklists, and data deletion requests.
  - Sentry error tracking integration and correlation request IDs on every API request.
  - Docker container hardening with non-root security context and healthchecks.

## [1.3.0] - 2026-09-30

### Added
- **Phase 3: Data Model Consolidation**:
  - Industry-agnostic buyer intent classification (`purchase_inquiry`, `pricing_inquiry`, `partnership`, etc.).
  - Relaxed lead lifecycle state machine with reason-tracking for reopened or disqualified leads.
  - Platform-neutral collection taxonomy (`social_pages`, `social_posts`, `social_comments`).

## [1.2.0] - 2026-09-29

### Fixed
- **Phase 2: Single Source of Truth & Audit**:
  - Synchronized scoring thresholds (Hot >= 80, Warm 50-79, Cold < 50).
  - Reconciled session lifetime and token storage mechanisms.
  - Created `docs/README_AUDIT.md`.

## [1.1.0] - 2026-09-29

### Added
- **Phase 1: Performance & Caching**:
  - GZip/Brotli compression middleware.
  - Compound MongoDB index optimization for hot queries.
  - Static asset caching and frontend asset bundling.
  - Re-measured performance gains documented in `docs/PERFORMANCE_REPORT.md`.

## [1.0.0] - 2026-09-29

### Initial Release
- Baseline multi-tenant social listening application with Apify scraping and Gemini AI analysis.
