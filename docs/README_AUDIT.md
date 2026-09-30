# LeadAI — Phase 2 README Audit & Code Ground Truth Report

**Generated:** 2026-09-30  
**Git Branch:** `chore/hardening-and-perf`  
**Purpose:** Reconcile all contradictions between `README.md` documentation and the actual LeadAI production codebase.

---

## Executive Summary

Per the project rules: **The README was written by hand and is NOT a source of truth. The code is.**

Phase 2 performed a line-by-line verification of the 16 contradiction areas identified across the project. Every discrepancy was investigated against the active Python backend, MongoDB schemas, and vanilla JS frontend SPAs. This document catalogs each item, contrasting the outdated README claim with the code ground truth and the exact changes applied.

---

## Audit of 16 Contradiction Areas

### 1. Hot/Warm Lead Score Thresholds
- **Outdated README Claim**: Section 2 described score thresholds as `80/50` in one paragraph, and `hot_min=70`, `warm_min=40` in another.
- **Code Ground Truth**: The single source of truth is:
  - `scoring.hot_min = 80`
  - `scoring.warm_min = 50`
  - Verified in `app/pipeline/comment_ai.py` (lines 909–913), `app/admin/settings.py` (lines 101–102), and `app/settings/registry.py` (lines 278–281).
  - Lead Quality Classification:
    - **HOT**: Score ≥ 80
    - **WARM**: Score 50–79
    - **COLD**: Score < 50
- **Changes Applied**: Corrected all documentation to 80/50 across `README.md`.

---

### 2. API Rate Limiting Status
- **Outdated README Claim**: Marked as `⬜ Not Implemented` in the Key Features table, but described as active in Section 31.
- **Code Ground Truth**: **Fully implemented** across two distinct layers:
  1. *Product Search API*: In-memory sliding window enforcing 30 requests per minute per user/IP (`app/api/routes/search.py`, lines 70–89).
  2. *Authentication & Public Forms*: Distributed, persistent rate limiting backed by MongoDB collection `rate_limits` with compound index `(bucket, key, window_start)` and TTL auto-expiry index on `expires_at` (`app/auth/rate_limit.py`, `app/db/mongo.py:383`). Governs login IP brute-force (5 attempts / 300s), account lockouts (5 failures -> 15 min lock), signup attempts (5 / hour), password resets (3 / 15 min), and contact form submissions (3 / 10 min).
- **Changes Applied**: Updated status in `README.md` to `✅ Implemented` and clearly documented both rate limiting mechanisms.

---

### 3. MongoDB Collection Counts
- **Outdated README Claim**: Variously claimed "11 collections" (legacy prototype), "20+ collections" (architecture diagram), and "38/31/16" in different sections.
- **Code Ground Truth**:
  - **38 active collections** are actively queried and updated by the `app/` codebase:
    `admin_users`, `ai_comments`, `ai_models`, `ai_prompts`, `ai_requests`, `apify_jobs`, `audit_logs`, `comment_filter_results`, `contact_submissions`, `demo_requests`, `exports`, `facebook_comments`, `facebook_pages`, `facebook_posts`, `invoices`, `login_lockouts`, `notifications`, `organization_invitations`, `organization_members`, `organization_usage`, `organizations`, `password_resets`, `payment_events`, `payments`, `plans`, `processed_webhooks`, `search_history`, `search_presets`, `security_events`, `subscriptions`, `support_tickets`, `system_settings`, `temporary_entitlements`, `token_balances`, `token_ledger`, `usage_records`, `user_sessions`, `users`.
  - **44 collections** have explicit index schemas defined in `ensure_indexes()` (`app/db/mongo.py`).
  - 41 legacy/un-migrated collections exist on the live database cluster (scheduled for consolidation/cleanup in Phase 3).
- **Changes Applied**: Updated `README.md` with the exact 38 active / 44 indexed collection inventory and categorized them by domain.

---

### 4. Session Lifetime & Authentication Mechanism
- **Outdated README Claim**: Conflicted between 24 hours and 7 days; ambiguous on whether sessions use stateless HMAC cookies or database tokens in `user_sessions`.
- **Code Ground Truth**: **Hybrid Dual-Layer Architecture**:
  - *Lifetime*: Default is **7 days** (`settings.session_ttl_days: int = 7`, `SESSION_TTL_DAYS=7`). In hours, this is 168 hours (`security.session_timeout_hours`). The "24 hours" mention was outdated.
  - *Mechanism*:
    1. Client receives an `HMAC-SHA256` signed cookie `leadai_session` containing user claims, timestamp `iat`, `exp`, and `session_id` (`app/auth/service.py:538-548`).
    2. Server creates a stateful session record in MongoDB collection `user_sessions` (`create_tracked_session`) storing `session_id`, `user_id`, `created_at`, `expires_at`, and `revoked_at`.
    3. On every request, `parse_session_value` verifies cryptographic HMAC signature and expiry, then queries `user_sessions` to ensure the session has not been revoked server-side.
- **Changes Applied**: Documented the 7-day default and the dual-layer HMAC cookie + `user_sessions` revocation tracking in `README.md`.

---

### 5. Comments Per Post Defaults & Scrape Caps
- **Outdated README Claim**: Mixed up default 20 vs 30 comments per post; confused `MAX_COMMENTS_TO_COLLECT` with `global_max_comments`.
- **Code Ground Truth**:
  - Default Posts per Search: **20** (`limits.max_posts_default = 20`, hard ceiling 100).
  - Default Comments per Post: **30** (`limits.max_comments_per_post_default = 30` in `app/admin/settings.py:537`, `app/social/url_search.py:81`, `app/settings/registry.py:315`).
  - Hard Ceiling per Post: **500** (`limits.max_comments_per_post_cap = 500`).
  - Global Comment Budget: **100–500** (`limits.global_max_comments`), further constrained by plan caps (`caps["comments_per_post"]`). `MAX_COMMENTS_TO_COLLECT` in `app/config.py` (`500`) serves as the environment variable fallback.
- **Changes Applied**: Documented the exact scraping defaults (20 posts, 30 comments/post) and the hierarchy of runtime settings vs. plan caps.

---

### 6. Industry Selection
- **Outdated README Claim**: Stated that users select an industry from a dropdown on every search.
- **Code Ground Truth**: Industry selection is **not** performed per search. Searches accept social URL and keyword filter mode (`filter_mode`, `preset`, `include_keywords`, `exclude_keywords`). Industry context is **automatically inherited from the organization's business profile** (`app/pipeline/business_context.py:build_context`).
- **Changes Applied**: Documented that industry context is tenant-scoped and managed in Org Admin → Business Profile, while searches allow custom keyword rules.

---

### 7. Signup Flow & Entitlements
- **Outdated README Claim**: Stated that clicking "Start Free" creates an account and immediately redirects to the dashboard on a Free plan.
- **Code Ground Truth**: `POST /api/auth/signup` creates a **PENDING demo request** in `demo_requests` (`app/api/routes/auth.py:123-147`). Visitors get **no session and no product access** until approved by a Super Admin in `/superadmin#/demo`. Upon approval, user account, organization, and trial subscription with entitlements are provisioned.
- **Changes Applied**: Accurately documented the demo-request approval flow in `README.md`.

---

### 8. Login Scopes & Password Reset
- **Outdated README Claim**: Mentioned "three login scopes" and claimed forgot-password was not implemented.
- **Code Ground Truth**:
  - *Scopes*: Two primary session scopes: `scope="site"` (for organization members, admins, owners) and `scope="admin"` (for platform staff and superadmin). Platform permissions are governed by `platform_role` (`super_admin`, `admin`, `viewer`, `support`).
  - *Password Reset*: **Fully implemented**! Both `GET /forgot-password` and `GET /reset-password` serve `reset-password.html` (`app/main.py:754-757`). Reset tokens are hashed in `password_resets` (`POST /api/auth/forgot-password`) and validated on password update (`POST /api/auth/reset-password`).
- **Changes Applied**: Corrected scopes to two primary scopes with platform role hierarchy, and documented the complete password reset implementation.

---

### 9. Facebook Scraper Actors
- **Outdated README Claim**: Claimed Facebook Apify actors are "hardcoded".
- **Code Ground Truth**: **Fully configurable** at runtime! `app/connectors/apify_connector.py` calls `get_actor_id("facebook", "pages")`, `get_actor_id("facebook", "posts")`, and `get_actor_id("facebook", "comments")`, which read from `system_settings` (`actor.facebook.*`) with code defaults, editable at runtime via Platform Admin → Platforms.
- **Changes Applied**: Documented runtime configurability of Facebook actors alongside Instagram, YouTube, and LinkedIn.

---

### 10. Environment Overrides vs. System Settings
- **Outdated README Claim**: Conflated `system_settings` and `env_overrides`.
- **Code Ground Truth**: Two distinct database collections:
  1. `system_settings`: Managed product & tenant settings (typed, with defaults from `app/settings/registry.py`), such as scoring weights, scrape limits, and feature flags.
  2. `env_overrides`: Platform admin raw environment variable overrides (`app/admin/envvars.py`) managed from the guarded Environment panel.
- **Changes Applied**: Documented the separate roles of `system_settings` and `env_overrides` in `README.md`.

---

### 11. Portal Routing Disambiguation
- **Outdated README Claim**: Used `/admin` ambiguously for both the Platform Console and Organization Admin.
- **Code Ground Truth**: Distinct, dedicated URLs with automatic role-based redirects:
  - `/` & `/dashboard`: User Portal (`index.html` + `app.js`).
  - `/org-admin`: Organization Admin Portal (`org-admin.html` + `org-admin.js`).
  - `/admin`: Platform Staff Console (`admin.html` + `admin.js`).
  - `/superadmin`: Super Admin Portal (`super-admin.html` + `super-admin.js`).
  - In `app/main.py`, if an authenticated site user (org owner/admin) navigates to `/admin`, they are gracefully redirected (HTTP 303) to `/org-admin`.
- **Changes Applied**: Added a single, comprehensive portal disambiguation table in `README.md`.

---

### 12. Taxonomy & Hash Routes
- **Outdated README Claim**: Discrepancies between "Pro" and "Professional" plan names, and conflicting view counts across documentation.
- **Code Ground Truth**:
  - Plan Tier: Identifier is `slug: "pro"`, display name is `name: "Professional"`. "Pro" and "Professional" designate the exact same plan tier.
  - User Portal (`app.js`): 11 views (`PORTAL_VIEWS`).
  - Org Admin (`org-admin.js`): 21 routes in `ROUTES`.
  - Platform Console (`admin.html` / `admin.js`): 29 navigation items.
  - Super Admin (`super-admin.js`): 29 routes across 7 functional menu groups.
  - Total FastAPI OpenAPI Routes: 348 paths / 414 method endpoints.
- **Changes Applied**: Standardized plan naming and documented exact view and route counts.

---

### 13. Test Inventory
- **Outdated README Claim**: Listed outdated test counts (500–700 tests from legacy milestones).
- **Code Ground Truth**: Exactly **39 test files** and **1,400 collected tests** (`pytest --collect-only`).
- **Changes Applied**: Updated Section 33 of `README.md` to reflect the complete 39-file / 1,400-test suite inventory.

---

### 14. Installation Steps
- **Outdated README Claim**: `git clone https://github.com/saurabh95710/LEADAI.git` followed by `cd lead_apify` (which fails because Git clones into directory `LEADAI` by default).
- **Code Ground Truth**: Command caused directory not found errors.
- **Changes Applied**: Corrected installation instructions to:
  ```bash
  git clone https://github.com/saurabh95710/LEADAI.git lead_apify
  cd lead_apify
  ```

---

### 15. Retired Models & Stale Line Counts
- **Outdated README Claim**: Referenced retired Google Gemini models (`gemini-1.5-pro`) and claimed stale line counts (`app.js` 1,385 lines, `admin.js` 4,725 lines).
- **Code Ground Truth**:
  - Active Gemini model in production code is `gemini-2.5-flash` (`app/config.py:10`, `app/pipeline/comment_ai.py`).
  - Actual file sizes: `app.js` has 4,525 lines; `admin.js` has 5,922 lines; `org-admin.js` has 2,630 lines; `super-admin.js` has 3,134 lines.
- **Changes Applied**: Removed retired model names, documented Google's active `gemini-2.5-flash` and `gemini-2.5-pro` options, and replaced manual line count estimates with functional descriptions.

---

### 16. Status Legend & Commercial Feature Clarity
- **Outdated README Claim**: Marked Stripe billing, developer API access, and enterprise SLAs as fully implemented.
- **Code Ground Truth**:
  - *Billing*: Mock payment gateway is enabled by default (`MOCK_PAYMENTS_ENABLED=true`); Stripe webhook verification and checkout exist, but operate in mock mode without live keys. Status: `🟡 Partial (Mock by default)`.
  - *Developer REST API*: Public API keys, external HMAC authentication, and developer docs: `⬜ Planned` (scheduled for Phase 6).
  - *Priority Support & SLAs*: In-app support ticketing exists in Super Admin and Org Admin, but formal external SLAs and third-party CRM connectors: `🟡 Partial`.
- **Changes Applied**: Updated status legend and feature tables to provide an honest, accurate representation of commercial capabilities.
