# LeadAI — Phase 1 Load-Time Optimization & Performance Report

**Generated:** 2026-09-30  
**Environment:** Localhost (`http://127.0.0.1:8000`) | Python 3.14.4 | FastAPI 0.141.1 | MongoDB 7.0 Cluster  
**Git Branch:** `chore/hardening-and-perf`  
**Test Suite:** `tests/test_phase1_perf_hardening.py` (7/7 Passed)  
**Measurement Dataset:** `docs/phase1_measurements.json` (captured via `scratch/measure_phase1.py`)

---

## 1. Executive Summary

Phase 1 implemented systemic load-time and runtime performance hardening across the entire LeadAI stack. By introducing response compression, multi-tier caching, asynchronous startup orchestration, pooled network connections, and compound database indexes, we eliminated the core bottlenecks identified in Phase 0:

- **Public Config Latency**: Reduced from **3,991.0 ms** to **69.8 ms** cold (**-98.3%**) and **43.9 ms** warm via single-query batch retrieval (`aget_all_settings()`) and a 60-second in-memory TTL cache.
- **Static Asset Wire Payload**: Dropped by **76.5%** across all major JavaScript and CSS bundles (from **1,184.3 KB** raw to **278.4 KB** compressed with Starlette `GZipMiddleware(minimum_size=1000)`).
- **HTTP Caching**: All static assets (.js, .css, fonts, images) are served with `Cache-Control: public, max-age=31536000, immutable`, dropping repeat page asset transfer to **0 bytes**.
- **HTML ETag Revalidation**: HTML shells (`/`, `/login`, `/admin`, `/superadmin`, `/website`) compute cryptographic MD5 ETags and return `304 Not Modified` on cache hits (`Cache-Control: no-cache, must-revalidate`).
- **Database Query Plans**: Converted full collection scans (`COLLSCAN`) to index scans (`IXSCAN` / `FETCH`) for hot queries on `ai_comments`, `search_presets`, `search_history`, and `notifications`.
- **Org-Admin Overview Concurrency**: Parallelized 13 sequential count and aggregation queries using `asyncio.gather()`.
- **Fast Startup & Health Readiness**: Asynchronous background initialization (`_run_startup_tasks`) decoupled from server boot; exposed container readiness flag (`ready: bool`) on `GET /health`.
- **Connection Pools**: Configured connection pooling on Motor and PyMongo (`maxPoolSize=50, minPoolSize=5, maxIdleTimeMS=45000`) and pooled `httpx.Client` for Gemini AI.

---

## 2. Before vs After Core Performance Metrics

| Metric | Phase 0 Baseline | Phase 1 Hardened | Change / Improvement |
| :--- | :---: | :---: | :---: |
| **`/api/public/config` (Cold Latency)** | 3,991.0 ms | **69.8 ms** | **-98.3% (57x faster)** |
| **`/api/public/config` (Warm Latency)** | 3,991.0 ms | **43.9 ms** | **-98.9% (91x faster)** |
| **Combined Static Asset Transfer (6 core files)** | 1,184.3 KB | **278.4 KB** | **-76.5% wire size (-905.9 KB)** |
| **Repeat Visit Static Transfer** | 1,184.3 KB | **0.0 KB** | **100% saved (Disk Cache)** |
| **Repeat Visit HTML Revalidation** | 200 OK (Full Body) | **304 Not Modified** | **0 KB body transfer** |
| **Static Cache-Control Header** | Missing / default | `public, max-age=31536000, immutable` | Configured on all static endpoints |
| **Authenticated API Cache-Control** | Unspecified | `no-store, no-cache, must-revalidate, private` | Enforced via `SecurityHeadersMiddleware` |
| **MongoDB Atlas Hot Query Plans** | `COLLSCAN` (2,100+ docs) | `FETCH` / `IXSCAN` | Sub-5ms index lookups |
| **Org-Admin Overview Query Execution** | Sequential (13 queries) | Concurrent (`asyncio.gather`) | Executed concurrently in single roundtrip |
| **Server Startup Block** | Blocking synchronous | Asynchronous background task | Instant event loop readiness |

---

## 3. Static Asset Payload Compression & Caching

Wire size measurements across key bundles before and after GZip compression (`minimum_size=1000`):

| Asset Path | Type | Raw Payload Size | GZip Compressed Size | Bandwidth Reduction | Cache-Control Header |
| :--- | :---: | :---: | :---: | :---: | :--- |
| `static/super-admin.js` | JS | 306.6 KB | **73.0 KB** | **-76.2%** | `public, max-age=31536000, immutable` |
| `static/admin.js` | JS | 294.6 KB | **64.4 KB** | **-78.1%** | `public, max-age=31536000, immutable` |
| `static/app.js` | JS | 226.1 KB | **56.8 KB** | **-74.9%** | `public, max-age=31536000, immutable` |
| `static/org-admin.js` | JS | 231.8 KB | **59.7 KB** | **-74.3%** | `public, max-age=31536000, immutable` |
| `static/design/components.css` | CSS | 108.5 KB | **20.3 KB** | **-81.3%** | `public, max-age=31536000, immutable` |
| `static/design/tokens.css` | CSS | 16.7 KB | **4.2 KB** | **-75.0%** | `public, max-age=31536000, immutable` |
| **Total Combined Transfer** | — | **1,184.3 KB** | **278.4 KB** | **-76.5%** | **905.9 KB saved per user** |

### Client-Side Preloading & Script Deferral
- Added `<link rel="preload" href="/static/design/tokens.css" as="style">` and `<link rel="preload" href="/static/design/components.css" as="style">` to `<head>` on all portal entrypoints (`index.html`, `login.html`, `admin.html`, `org-admin.html`, `super-admin.html`, `website.html`).
- Added `defer` to all `<script>` tags across all HTML templates to unblock DOM parsing and eliminate render-blocking script delays.
- Configured 5-minute `sessionStorage` caching in `app/static/config.js` and `app/static/design/theme.js` to eliminate redundant network fetches during in-app navigation.

---

## 4. HTML ETag & 304 Revalidation Results

All HTML document routes now calculate content digests and support conditional `If-None-Match` HTTP headers:

| Route | Shell Template | First Visit (Cold) | Second Visit (`If-None-Match`) | Cache-Control Header |
| :--- | :--- | :---: | :---: | :--- |
| `/login` | `login.html` | 200 OK | **304 Not Modified** | `no-cache, must-revalidate` |
| `/website` | `website.html` | 200 OK | **304 Not Modified** | `no-cache, must-revalidate` |
| `/` | `index.html` | 200 OK | **304 Not Modified** | `no-cache, must-revalidate` |
| `/admin` | `admin.html` | 200 OK | **304 Not Modified** | `no-cache, must-revalidate` |
| `/superadmin` | `super-admin.html` | 200 OK | **304 Not Modified** | `no-cache, must-revalidate` |

---

## 5. MongoDB Index & Query Execution Plans

Added compound indexes in `app/db/mongo.py` (`ensure_indexes`) targeting multi-tenant and sorted queries:

| Collection | Underlying Query Filter & Sort | Baseline Stage | Phase 1 Stage | Winning Index |
| :--- | :--- | :---: | :---: | :--- |
| `ai_comments` | `{"user_id": ..., "created_at": -1}` | `COLLSCAN` (2,140 docs) | **`FETCH` -> `IXSCAN`** | `user_id_1_created_at_-1` |
| `ai_comments` | `{"organization_id": ..., "status": ...}` | `COLLSCAN` (2,140 docs) | **`FETCH` -> `IXSCAN`** | `org_status_prio` |
| `search_presets` | `{"organization_id": ..., "updated_at": -1}` | `COLLSCAN` | **`FETCH` -> `IXSCAN`** | `org_updated_idx` |
| `search_history` | `{"organization_id": ..., "created_at": -1}` | `SORT` (in-memory) | **`FETCH` -> `IXSCAN`** | `org_created_idx` |
| `notifications` | `{"audience": ..., "user_id": ..., "created_at": -1}`| `COLLSCAN` | **`FETCH` -> `IXSCAN`** | `audience_user_created` |
| `token_balances` | `{"organization_id": 1}` | `COLLSCAN` | **`FETCH` -> `IXSCAN`** | `org_unique_balance` |

---

## 6. Connection Pooling & HTTP Client Reuse

1. **MongoDB Driver Pooling**:
   - `AsyncIOMotorClient` and `MongoClient` instantiated with `maxPoolSize=50, minPoolSize=5, maxIdleTimeMS=45000, connectTimeoutMS=5000`.
   - Prevents socket starvation and high-concurrency connection thrashing.
2. **Gemini AI Pooled Client**:
   - Replaced per-request `httpx.Client()` instantiations in `app/pipeline/comment_ai.py` with a singleton connection pool:
     `httpx.Client(timeout=60.0, limits=httpx.Limits(max_keepalive_connections=20, max_connections=50))`.
   - Eliminates redundant TCP and TLS handshakes on every comment analysis request.

---

## 7. Verification & Test Evidence

- **Unit and Performance Hardening Suite**: `tests/test_phase1_perf_hardening.py`
  - `test_health_readiness_flag`: Passed
  - `test_gzip_compression_middleware`: Passed
  - `test_static_cache_headers`: Passed
  - `test_html_etag_and_304`: Passed
  - `test_api_cache_control_headers`: Passed
  - `test_public_config_batch_caching`: Passed
  - `test_mongo_connection_pool_sizing`: Passed
- **Regression Suites**:
  - `tests/test_org_admin.py`: 26 passed
  - `tests/test_auth.py`: 23 passed
  - `tests/test_public_website2.py`: 14 passed
- **Total passing tests verified in Phase 1**: 70 tests passed with 0 regressions.
