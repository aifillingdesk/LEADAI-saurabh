# Changelog

All notable changes to LeadAI are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added
- **Partner / Reseller / Affiliate Program** (see `docs/PARTNERS.md`): public `/partners` application page; Super Admin Partner Management (applications, partners, commissions, payouts, rules, tiers, coupons, marketing assets, program settings); Partner Portal at `/partner` with permission-based modules; `partner` session scope; referral links and campaigns with signed first- and last-touch attribution; reseller onboarding through the existing demo flow; partner coupons applied at checkout; commissions from subscription confirmation and renewal; derived wallet with a ledger; payouts; read-only partner API keys; new platform permissions `partners.view` / `partners.manage`.

- **Partner commission engine v2**:
  - Commission lifecycle `pending → qualified → approved → payable → processing → paid`, plus `reversed`. Hybrid commissions, a cap per payment, and a qualification period per rule.
  - Proportional reversal on refund or chargeback; clawback of commissions that were already paid; reversal on cancellation during the qualification period.
  - Payout workflow with review, processing and failed states; payout schedule.
  - Coupon eligibility and commission basis; partner pricing; tier requirements, limits, permissions and automatic upgrade/downgrade.
  - Reseller onboarding links and a managed-customer limit.
  - Full referral funnel (checkout and payment stages) with conversion history.
  - Super Admin screens: customers & referrals (reassign, review), refunds & reversals, wallets, fraud review, partner pricing, and each partner's financial history.
- **Partner platform**:
  - Analytics reconciled with invoices (visitors, demos, revenue, commission by status, payouts, campaigns), with CSV export, a Super Admin analytics tab with partner and campaign filters, a leaderboard and a billing reconciliation check.
  - Fraud-flag review queue: flags hold commissions and never change them.
  - Marketing center with private file uploads, categories, per-partner visibility, email templates and download tracking.
  - Coupon and fraud notifications.
  - Partner API: an index endpoint, notifications readable with an API key, and key usage tracking.
- **Partner tasks**:
  - The Super Admin assigns tasks to one partner or to every active partner, affiliate or reseller, with details, a due date, a priority and a link to a Partner Portal page.
  - Partners see them under *Tasks*, with a count in the menu and a dashboard banner. They start a task and submit it with a note.
  - The Super Admin approves it, sends it back with feedback, edits it or cancels it.
  - Notified both ways and audited (`partner.task.*`); readable with a partner API key.
  - New collection: `partner_tasks`.
- **Partners link in the website's top menu** (desktop and mobile), linking to the `/partners` application page. It's added once to existing sites at startup and is never re-added after an operator removes it.
- **Super Admin full access**:
  - Organization impersonation acts as Owner with every organization permission, ignoring the organization's role restrictions.
  - "View as partner" Partner Portal impersonation, fully attributed.
  - A platform-wide API keys & webhooks page with revoke, disable and enable.
  - The Super Admin can issue Customer API and Partner API keys for any organization or partner.
- **Partner selling and oversight**:
  - A Sell LeadAI kit: live plans, customer price, commission per plan, plan share links.
  - Deal registration with Super Admin review and claim protection.
  - A Super Admin partner activity log (sign-ins, every portal and API request, actions) with CSV export, partner session control, and per-partner activity, sessions, deals and audit tabs.
  - New partner permissions `sales.view` and `deals.manage`, granted to existing partners once.
- **Fixed**: partner screens showed UTC times as local time (several hours off in non-UTC timezones).
- **Billing refunds**: `POST /api/super-admin/invoices/{id}/refund` and `GET /api/super-admin/invoices`, plus provider refund/dispute webhooks. Invoices and payments now record full and partial refunds and chargebacks.

### Fixed
- TOTP two-factor authentication was never enforced at login (the flag was read from session claims). The login page now asks for the code.
- The public REST API (`/api/v1`) was unreachable: the auth gate required a session, and the handlers awaited a non-coroutine. The per-key rate limit is now enforced.
- Razorpay checkout overwrote the subscription amount with `plan.price_cents` (minor units, or 0 when unset), breaking payment verification.

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
