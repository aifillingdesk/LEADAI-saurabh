# LeadAI System Architecture

LeadAI is built on an enterprise multi-tenant asynchronous architecture designed for high throughput, strict security isolation, and reliable social listening at scale.

```
                      +---------------------------------------+
                      |   Client Web Browser / Public API     |
                      +-------------------+-------------------+
                                          |
                                    HTTPS / WSS
                                          |
                      +-------------------v-------------------+
                      |         Reverse Proxy / CDN           |
                      |  (Gzip/Brotli, Static Caching, SSL)   |
                      +-------------------+-------------------+
                                          |
                      +-------------------v-------------------+
                      |        FastAPI Backend Cluster        |
                      |  - Session / API Key Authentication   |
                      |  - Multi-tenant Isolation Gate        |
                      |  - Rate Limiter (Redis / In-memory)   |
                      +---------+-------------------+---------+
                                |                   |
               +----------------v-----+       +-----v----------------+
               | MongoDB (Async/Sync) |       |  Redis Job Queue     |
               | - Multi-tenant DB    |       |  (Durable Background |
               | - Compound Indexes   |       |   Task Supervisor)   |
               +----------------------+       +-----+----------------+
                                                    |
                                      +-------------v----------------+
                                      |   Async Worker Pool          |
                                      |   - Apify Scraping Actors    |
                                      |   - Gemini AI Pipeline       |
                                      |   - Lead Deduplication       |
                                      |   - Webhooks & CRM Sync      |
                                      +------------------------------+
```

---

## 1. Core Technology Stack

- **Application Server**: FastAPI (Python 3.11+) with Uvicorn ASGI server.
- **Database**: MongoDB (Motor async driver for API endpoints, PyMongo sync driver for authentication and session middleware).
- **Task Queue**: Redis Queue / Arq with Mongo-backed persistent queue fallback (`app/queue/service.py`).
- **Scraping Engine**: Apify Actor Integrations (`apify-client`) targeting Facebook, Instagram, and YouTube.
- **AI Intent Engine**: Google Gemini API (`google-genai` / REST) with structured JSON schemas and prompt-injection guardrails.
- **Storage Layer**: Pluggable media service (`LocalFileSystem`, `Amazon S3`, `Cloudinary`).

---

## 2. Multi-Tenancy & Security Isolation

### Fail-Closed Tenant Context
Every authenticated request is passed through tenant resolution middleware:
1. Session cookie or Bearer token is verified.
2. Tenant membership is confirmed from `organization_members` collection.
3. A frozen `TenantContext` object is injected into route handlers with:
   - `organization_id`: Current active organization.
   - `user_id`: Authenticated user ID.
   - `role`: Organization role (`owner`, `admin`, `manager`, `member`, `viewer`).
   - `permissions`: Active set of granular permissions.

### Scoped Queries (`scope_query` & `find_scoped_or_404`)
- All database read, write, update, and delete queries MUST pass through `scope_query` or `find_scoped_or_404`.
- For standard members, queries automatically append `user_id: ctx.user_id` or `assigned_user_id: ctx.user_id`.
- For organization owners/admins (holding `data.view_all`), queries are scoped strictly to `organization_id: ctx.organization_id`.
- Attempts to query or mutate an ID belonging to another tenant raise `404 Not Found` (to prevent ID enumeration) and log a high-severity `cross_tenant_access` security event to `security_events`.

---

## 3. Asynchronous Worker & Durable Queue

To prevent web workers from blocking during external network operations (Apify scraping and Gemini LLM calls):
- Scraping runs and heavy AI batches are offloaded to `app/queue/service.py`.
- Jobs survive application restarts through durable queue persistence.
- Workers periodically poll for cancellation requests (`cancel_requested: true`) at checkpoints to avoid wasting compute units.
- Automatic retries with exponential backoff are enforced for transient upstream API failures.

---

## 4. AI Prompting & Comment Classification Pipeline

LeadAI analyzes raw social comments to extract high-intent commercial leads:

1. **Untrusted Data Boundary**:
   Raw comment text is wrapped in delimited `<untrusted_user_content>` tags. The system prompt instructs Gemini to treat all user comments strictly as text data and ignore instructions contained within them.
2. **Classification Attributes**:
   - `is_lead`: Boolean flag.
   - `lead_score`: Integer (0 to 100).
   - `lead_quality`: `hot` (>=80), `warm` (50-79), `cold` (<50).
   - `intent`: `purchase_inquiry`, `pricing_inquiry`, `partnership`, `support`, `job_seeker`, or `other`.
   - Contact Info Extraction: Extracted phone numbers, email addresses, and WhatsApp contact handles.
   - Buyer Budget & Requirements: Extracted budget figures, locations, and desired specifications.
3. **Structured Validation**:
   Outputs are parsed into strict Pydantic models before persistence to `ai_comments`.

---

## 5. Integrations & Outbound Connectors

- **Deduplication Engine**: Cross-run and cross-platform deduplication (`app/pipeline/deduplication.py`) merges duplicate inquiries across multiple posts or platforms.
- **Lead Assignment & SLA**: Automated assignment rules evaluate incoming leads and route them to team members via round-robin or priority criteria with SLA expiration timers.
- **Outbound Webhooks**: Real-time event notifications (`app/services/outbound_webhooks.py`) dispatched with HMAC-SHA256 signatures (`X-LeadAI-Signature`).
- **CRM Connectors**: Built-in adapters for HubSpot, Zoho CRM, Google Sheets, and formula-safe Excel spreadsheet XML.
