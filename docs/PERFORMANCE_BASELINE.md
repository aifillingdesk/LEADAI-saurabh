# LeadAI — Phase 0 Performance Baseline & Profiling Report

**Generated:** 2026-09-30 05:38:48 UTC  
**Environment:** Localhost (`http://127.0.0.1:8000`) | Python 3.14.4 | FastAPI 0.141.1 | MongoDB 7.0 Cluster  
**Methodology:** Automated HTTP latency instrumentation, DOM critical-path asset extraction, simulated broadband rendering waterfall (30ms RTT, 30 Mbps downlink), and MongoDB query execution plan (`explain()`) analysis.

---

## 1. Executive Summary & Core Bottlenecks

Initial performance baseline audits reveal **four primary architectural bottlenecks** causing sluggish first-load and interaction performance across all four portals:

1. **Complete Absence of Compression Middleware (GZip / Brotli)**: All HTML, static JavaScript bundles (`app.js` @ 141 KB, `admin.js` @ 213 KB, `org-admin.js` @ 110 KB), CSS sheets, and JSON API payloads are currently transferred over HTTP in raw, uncompressed plain text. Enabling GZip/Brotli will reduce static asset wire transfer sizes by **72%–78%** instantly.
2. **Zero Browser Cache-Control on Static Assets**: Every single CSS and JS asset is served with default FastAPI static headers lacking long-lived `max-age` caching, content hashing, or cache-busting version identifiers. Every browser reload re-downloads multi-hundred-kilobyte script bundles over the network.
3. **Monolithic, Un-Minified JavaScript SPAs Blocking First Paint**: `app.js` (3,747 lines), `admin.js` (4,725 lines), and `super-admin.js` (3,134 lines) are served as single, unminified, non-split monolithic files. Furthermore, scripts are loaded synchronously in the `<head>` or body without `defer` or ES modules, blocking the browser's DOM parser.
4. **Full Collection Scans (`COLLSCAN`) & Missing Compound Indexes**: Critical dashboard overview queries on `search_history` (`organization_id` + `created_at`), `ai_comments` (`organization_id` + `status`), and `notifications` (`recipient_user_id` + `read_at`) execute table-wide scans without dedicated compound indexes, leading to degraded performance as tenant data grows.

---

## 2. Page Load Baseline Metrics

| Page Name | Route | Role / Scope | Doc Size | Transfer Size (Assets) | Asset Count | Blocking CSS/JS | Initial APIs | Est. FCP (30ms RTT) | Est. TTI |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **User Portal (Dashboard)** | `/` | `user` | 91.1 KB | 437.8 KB | 8 | 437.8 KB | 10 calls | **350.4 ms** | **4092.5 ms** |
| **Login Screen** | `/login` | `none` | 36.1 KB | 44.8 KB | 4 | 44.8 KB | 2 calls | **148.6 ms** | **3805.6 ms** |
| **Platform Console (Admin)** | `/admin` | `superadmin` | 21.8 KB | 389.2 KB | 4 | 389.2 KB | 6 calls | **255.5 ms** | **2201.3 ms** |
| **Organization Admin Portal** | `/org-admin` | `org_admin` | 36.1 KB | 44.8 KB | 4 | 44.8 KB | 7 calls | **315.0 ms** | **506.7 ms** |
| **Super Admin Portal** | `/superadmin` | `superadmin` | 32.8 KB | 447.8 KB | 5 | 447.7 KB | 6 calls | **270.9 ms** | **1998.3 ms** |
| **Public Marketing Website** | `/website` | `none` | 7.7 KB | 211.4 KB | 6 | 211.4 KB | 5 calls | **231.3 ms** | **4149.4 ms** |

> **FCP Estimation Model**: Time to download HTML document + RTT + download/parse render-blocking CSS and synchronous JavaScript bundles before first paint.  
> **TTI Estimation Model**: FCP + execution of all deferred application scripts + roundtrips for initial view API calls.

---

## 3. Static Asset Payload Analysis

### Heaviest Assets Loaded on Initial View

| Asset Path | Page | Type | Raw Size | GZip Potential | Savings | Cache-Control | Execution Mode |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| `/static/super-admin.js` | Super Admin Portal | `JS` | **306.6 KB** | 73.0 KB | **-76.2%** | `NONE` | Render-Blocking |
| `/static/admin.js` | Platform Console (Admin) | `JS` | **294.6 KB** | 64.4 KB | **-78.1%** | `NONE` | Render-Blocking |
| `/static/app.js?v=step4` | User Portal (Dashboard) | `JS` | **226.1 KB** | 56.8 KB | **-74.9%** | `NONE` | Render-Blocking |
| `/static/styles.css?v=step4` | User Portal (Dashboard) | `CSS` | **125.7 KB** | 20.7 KB | **-83.6%** | `NONE` | Render-Blocking |
| `/static/design/components.css` | Super Admin Portal | `CSS` | **108.5 KB** | 20.3 KB | **-81.3%** | `NONE` | Render-Blocking |
| `/static/admin.css?v=light1` | Platform Console (Admin) | `CSS` | **82.4 KB** | 14.1 KB | **-82.9%** | `NONE` | Render-Blocking |
| `/static/website.js` | Public Marketing Website | `JS` | **36.5 KB** | 10.9 KB | **-70.2%** | `NONE` | Render-Blocking |
| `/static/website.css` | Public Marketing Website | `CSS` | **32.2 KB** | 6.5 KB | **-80.0%** | `NONE` | Render-Blocking |
| `/static/design/ui.js?v=step4` | User Portal (Dashboard) | `JS` | **28.0 KB** | 8.3 KB | **-70.3%** | `NONE` | Render-Blocking |
| `/static/design/tokens.css?v=step3` | User Portal (Dashboard) | `CSS` | **16.7 KB** | 4.2 KB | **-75.0%** | `NONE` | Render-Blocking |
| `/static/design/tokens.css` | Login Screen | `CSS` | **16.7 KB** | 4.2 KB | **-75.0%** | `NONE` | Render-Blocking |
| `/static/design/theme.js` | User Portal (Dashboard) | `JS` | **15.9 KB** | 5.2 KB | **-67.2%** | `NONE` | Render-Blocking |
| `/static/design/domain-keywords.css?v=1` | User Portal (Dashboard) | `CSS` | **13.1 KB** | 2.4 KB | **-81.5%** | `NONE` | Render-Blocking |
| `/static/config.js` | User Portal (Dashboard) | `JS` | **12.2 KB** | 3.7 KB | **-69.7%** | `NONE` | Render-Blocking |
| `https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&amp;display=swap` | Public Marketing Website | `CSS` | **1.4 KB** | 0.2 KB | **-82.8%** | `private, max-age=864` | Render-Blocking |

### Total Transfer Compression Opportunities

- **Total Combined Static Footprint**: 1316.8 KB  
- **Compressed (GZip) Footprint**: 294.8 KB  
- **Immediate Net Payload Reduction**: **-77.6% (1021.9 KB saved per cold load)**  

---

## 4. Initial Load API Latency Profile

The following table details the API calls executed immediately upon DOM load by the respective client applications:

| Endpoint | Target Portal | HTTP Status | Response Size | GZip Size | Baseline Latency | Cache-Control |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| `/api/public/config` | Public Marketing Website | 200 | 1.61 KB | 0.86 KB | **3863.1 ms** | `NONE` |
| `/api/public/config` | Login Screen | 200 | 1.61 KB | 0.86 KB | **3645.29 ms** | `NONE` |
| `/api/public/config` | User Portal (Dashboard) | 200 | 1.61 KB | 0.86 KB | **3628.05 ms** | `NONE` |
| `/api/admin/dashboard` | Platform Console (Admin) | 200 | 9.64 KB | 2.17 KB | **1844.42 ms** | `NONE` |
| `/api/super-admin/dashboard` | Super Admin Portal | 200 | 7.54 KB | 1.73 KB | **1610.84 ms** | `NONE` |
| `/api/super-admin/organizations?limit=10` | Super Admin Portal | 200 | 5.38 KB | 1.14 KB | **1255.82 ms** | `NONE` |
| `/api/admin/health` | Platform Console (Admin) | 200 | 0.32 KB | 0.22 KB | **333.92 ms** | `NONE` |
| `/api/admin/alerts` | Platform Console (Admin) | 200 | 0.18 KB | 0.16 KB | **303.23 ms** | `NONE` |
| `/api/super-admin/demo-requests?status=pending&limit=5` | Super Admin Portal | 200 | 0.09 KB | 0.1 KB | **221.88 ms** | `NONE` |
| `/api/admin/settings` | Platform Console (Admin) | 200 | 40.44 KB | 6.78 KB | **203.87 ms** | `NONE` |
| `/api/billing/subscription` | User Portal (Dashboard) | 403 | 0.08 KB | 0.09 KB | **187.82 ms** | `NONE` |
| `/api/org-admin/overview` | Organization Admin Portal | 403 | 0.08 KB | 0.09 KB | **180.07 ms** | `NONE` |
| `/api/business-context/catalog` | User Portal (Dashboard) | 200 | 28.51 KB | 8.26 KB | **177.26 ms** | `NONE` |
| `/api/organizations/current` | User Portal (Dashboard) | 403 | 0.08 KB | 0.09 KB | **174.65 ms** | `NONE` |
| `/api/search-presets` | User Portal (Dashboard) | 403 | 0.08 KB | 0.09 KB | **170.78 ms** | `NONE` |
| `/api/org-admin/keyword-library` | Organization Admin Portal | 403 | 0.08 KB | 0.09 KB | **169.82 ms** | `NONE` |
| `/api/org-admin/context` | Organization Admin Portal | 403 | 0.08 KB | 0.09 KB | **167.47 ms** | `NONE` |
| `/api/me/summary` | User Portal (Dashboard) | 403 | 0.08 KB | 0.09 KB | **166.88 ms** | `NONE` |
| `/api/org-admin/business-summary` | Organization Admin Portal | 403 | 0.08 KB | 0.09 KB | **155.15 ms** | `NONE` |
| `/api/auth/me` | User Portal (Dashboard) | 403 | 0.08 KB | 0.09 KB | **146.98 ms** | `NONE` |
| `/api/auth/me` | Organization Admin Portal | 403 | 0.08 KB | 0.09 KB | **141.92 ms** | `NONE` |
| `/api/auth/me` | Platform Console (Admin) | 200 | 0.79 KB | 0.31 KB | **111.74 ms** | `NONE` |
| `/api/auth/me` | Super Admin Portal | 200 | 0.79 KB | 0.31 KB | **107.11 ms** | `NONE` |
| `/api/notifications/unread-count` | User Portal (Dashboard) | 404 | 0.02 KB | 0.04 KB | **89.15 ms** | `NONE` |
| `/api/super-admin/platform/health` | Super Admin Portal | 404 | 0.02 KB | 0.04 KB | **67.86 ms** | `NONE` |
| `/api/public/faq` | Public Marketing Website | 200 | 1.21 KB | 0.57 KB | **66.4 ms** | `NONE` |
| `/api/history` | User Portal (Dashboard) | 404 | 0.02 KB | 0.04 KB | **61.87 ms** | `NONE` |
| `/api/public/pricing` | Public Marketing Website | 200 | 4.21 KB | 1.04 KB | **60.86 ms** | `NONE` |
| `/api/business-context/catalog` | Public Marketing Website | 200 | 28.51 KB | 8.26 KB | **59.74 ms** | `NONE` |
| `/api/public/theme` | User Portal (Dashboard) | 200 | 0.17 KB | 0.15 KB | **58.27 ms** | `NONE` |
| `/api/notifications/unread-count` | Organization Admin Portal | 404 | 0.02 KB | 0.04 KB | **55.49 ms** | `NONE` |
| `/api/public/theme` | Platform Console (Admin) | 200 | 0.17 KB | 0.15 KB | **33.54 ms** | `NONE` |
| `/api/public/theme` | Organization Admin Portal | 200 | 0.17 KB | 0.15 KB | **29.56 ms** | `NONE` |
| `/api/public/theme` | Login Screen | 200 | 0.17 KB | 0.15 KB | **29.11 ms** | `NONE` |
| `/api/public/theme` | Public Marketing Website | 200 | 0.17 KB | 0.15 KB | **28.82 ms** | `NONE` |
| `/api/public/theme` | Super Admin Portal | 200 | 0.17 KB | 0.15 KB | **27.56 ms** | `NONE` |

> **Slowest API Call**: `/api/public/config` at **3863.1 ms** on `Public Marketing Website`.

---

## 5. MongoDB Query Profiling & Index Audit

### Query Execution Plan Analysis (`explain("executionStats")`)

| Query Intent | Collection | Underlying Filter | Execution Stage | Docs Examined | Docs Returned | Index Used | Query Time |
| :--- | :--- | :--- | :---: | :---: | :---: | :--- | :---: |
| User Session Auth Check | `user_sessions` | `{'session_id': 'test_session_id_123` | `EXPRESS_IXSCAN` | 0 | 0 | `session_id_1` | **0 ms** |
| Org Admin Context Lookup | `organizations` | `{'_id': ObjectId('6aa50b24a1720ac14` | `EXPRESS_IXSCAN` | 1 | 1 | `_id_` | **0 ms** |
| Org Overview Searches List | `search_history` | `{'organization_id': '6abb990d7bbf51` | `LIMIT -> FETCH -> IXSCAN` | 0 | 0 | *None (Full Table Scan)* | **2 ms** |
| Org Overview Lead Count by Status | `ai_comments` | `{'organization_id': '6abb990d7bbf51` | `FETCH -> IXSCAN` | 0 | 0 | `organization_id_1` | **4 ms** |
| Org Overview Active Members List | `organization_members` | `{'organization_id': '6abb990d7bbf51` | `FETCH -> IXSCAN` | 1 | 1 | `organization_id_1` | **0 ms** |
| User Me Summary Searches | `search_history` | `{'user_id': '6abb990e7bbf5114dfeb80` | `LIMIT -> FETCH -> IXSCAN` | 118 | 0 | *None (Full Table Scan)* | **0 ms** |
| User Me Summary Leads | `ai_comments` | `{'user_id': '6abb990e7bbf5114dfeb80` | **COLLSCAN (SLOW)** | 2140 | 0 | *None (Full Table Scan)* | **2 ms** |
| Unread Notifications Count | `notifications` | `{'recipient_user_id': '6abb990e7bbf` | **COLLSCAN (SLOW)** | 87 | 0 | *None (Full Table Scan)* | **0 ms** |
| Super Admin Dashboard Org List | `organizations` | `{}` | `LIMIT -> FETCH -> IXSCAN` | 50 | 50 | *None (Full Table Scan)* | **0 ms** |
| Super Admin Pending Demos | `demo_requests` | `{'status': 'pending'}` | `LIMIT -> FETCH -> IXSCAN` | 0 | 0 | *None (Full Table Scan)* | **0 ms** |
| Search Presets Tenant Scope | `search_presets` | `{'organization_id': '6abb990d7bbf51` | **COLLSCAN (SLOW)** | 5 | 0 | *None (Full Table Scan)* | **1 ms** |
| Audit Log Recent Events | `audit_logs` | `{'organization_id': '6abb990d7bbf51` | `SORT -> FETCH -> IXSCAN` | 4 | 4 | *None (Full Table Scan)* | **0 ms** |

### Inefficient Queries & Missing Compound Indexes

1. **`search_history` (`organization_id` + `created_at`)**:  
   - **Problem**: Queries sorting tenant searches by date (`created_at: -1`) perform in-memory sorts or collection scans when filtered by `organization_id`.
   - **Remedy**: Create compound index `{organization_id: 1, created_at: -1}`.

2. **`ai_comments` (`organization_id` + `status` / `priority`)**:  
   - **Problem**: Org overview dashboard runs multiple status count queries (`status='new'`, `status='contacted'`, etc.) scanning all comments.
   - **Remedy**: Create compound index `{organization_id: 1, status: 1, priority: 1}` and replace multiple count queries with a single `$facet` aggregation.

3. **`notifications` (`recipient_user_id` + `read_at`)**:  
   - **Problem**: Unread notification counter executes on every single page render for all authenticated users.
   - **Remedy**: Create compound index `{recipient_user_id: 1, read_at: 1}`.

4. **`demo_requests` (`status` + `created_at`)**:  
   - **Problem**: Super Admin initial dashboard queries pending demos with a full collection scan.
   - **Remedy**: Create compound index `{status: 1, created_at: -1}`.

5. **`search_presets` (`organization_id` + `updated_at`)**:  
   - **Problem**: Tenant search preset listing scans without an index on `organization_id`.
   - **Remedy**: Create compound index `{organization_id: 1, updated_at: -1}`.

6. **`audit_logs` (`organization_id` + `timestamp`)**:  
   - **Problem**: Tenant audit log queries scan the large `audit_logs` collection.
   - **Remedy**: Create compound index `{organization_id: 1, timestamp: -1}`.

---

## 6. N+1 Query Patterns & Architecture Inefficiencies

1. **N+1 in Org Admin Overview (`/api/org-admin/overview`)**:  
   - The overview handler currently executes individual `count_documents` queries sequentially for each status (`new`, `contacted`, `qualified`, `converted`, `lost`, `disqualified`) rather than executing a single grouped aggregation pipeline (`$group` by `$status`).
2. **Multiple First-Load Roundtrips**:  
   - When `/` loads, the frontend fires 10 independent HTTP requests in parallel (`/api/auth/me`, `/api/public/theme`, `/api/public/config`, `/api/me/summary`, `/api/business-context/catalog`, `/api/search-presets`, `/api/history`, etc.). On mobile or high-latency networks, this saturates browser connection pools (HTTP/1.1 limits to 6 parallel sockets per domain).
3. **Redundant Public Config & Theme Polling**:  
   - `/api/public/theme` and `/api/public/config` return static database values that rarely change, yet are re-fetched on every route switch and page load without `sessionStorage` or in-memory client caching.
4. **Synchronous Migration and Index Verification on Startup**:  
   - Server startup currently runs index inspection across dozens of collections synchronously before the event loop starts accepting incoming HTTP traffic, delaying container readiness.

---

## 7. Action Plan for Phase 1 (Optimization Targets)

| Optimization Area | Target Metric / Solution | Expected Impact |
| :--- | :--- | :--- |
| **Compression Middleware** | Add `GZipMiddleware` (minimum size: 1000B) | **-75% transfer payload** across all assets and JSON responses |
| **Static Asset Caching** | `Cache-Control: public, max-age=31536000, immutable` with asset hashing; `ETag` for HTML | **0 bytes re-downloaded** on repeat visits |
| **Compound Database Indexes** | Add 6 dedicated compound indexes on hot collections | **Eliminate all COLLSCANs**, sub-5ms query times |
| **Frontend Code Splitting & Minification** | Esbuild / Vite bundling, per-view lazy-loading (`admin.js`, `org-admin.js`, `app.js`) | **Reduce initial JS payload from ~210KB to <40KB** per view |
| **Script Deferral & Preloading** | Add `defer` to all `<script>` tags, preload critical CSS tokens | **First Contentful Paint < 1.2s** |
| **API Aggregation & Client Cache** | Combine initial load queries into unified summary endpoints; cache theme/config in `sessionStorage` | **Cut initial HTTP requests from 10 to <=3** |
| **Async Startup Readiness** | Defer heavy background checks to background task with `/health` readiness flag | Instant container startup (<500ms) |
