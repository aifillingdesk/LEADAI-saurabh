# LeadAI Upgrade & Migration Notes

This document provides migration instructions and breaking change notes for operators upgrading LeadAI deployments to version 2.0.0.

---

## 1. Environment Variable Additions

Deployments updating from v1.x must verify the presence of the following new environment variables:

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `REDIS_URL` | Optional | `""` (in-memory) | Connection string for Redis job queue and distributed rate limiting. |
| `STORAGE_BACKEND` | Optional | `local` | Media storage driver (`local`, `s3`, `cloudinary`). |
| `AWS_ACCESS_KEY_ID` | Conditional | `None` | Required if `STORAGE_BACKEND=s3`. |
| `AWS_SECRET_ACCESS_KEY` | Conditional | `None` | Required if `STORAGE_BACKEND=s3`. |
| `AWS_S3_BUCKET` | Conditional | `None` | S3 bucket name. |
| `BILLING_PROVIDER` | Optional | `stripe` | Billing provider choice (`stripe` or `razorpay`). |
| `RAZORPAY_KEY_ID` | Conditional | `None` | Required if `BILLING_PROVIDER=razorpay`. |
| `RAZORPAY_KEY_SECRET` | Conditional | `None` | Required if `BILLING_PROVIDER=razorpay`. |
| `SENTRY_DSN` | Optional | `None` | Production error monitoring DSN. |

---

## 2. Database Schema & Index Migration

### Automated Indexes
LeadAI v2.0 introduces multi-tenant compound indexes for query performance and tenant boundary isolation.
Ensure indexes are created on startup by running:
```bash
python -m app.db.ensure_indexes
```

Key compound indexes created:
- `search_history`: `(organization_id, created_at)`
- `facebook_pages`: `(organization_id, search_run_id)`
- `facebook_posts`: `(organization_id, page_ref)`
- `facebook_comments`: `(organization_id, post_ref)`
- `ai_comments`: `(organization_id, is_lead, lead_score)`
- `scheduled_scans`: `(organization_id, status)`
- `outbound_webhooks`: `(organization_id, is_active)`
- `api_keys`: `(key_hash, is_active)`

### Data Model Compatibility
- Phase 3 introduced platform-neutral aliases (`social_pages`, `social_posts`, `social_comments`).
- Legacy collection names (`facebook_pages`, `facebook_posts`, `facebook_comments`) continue to be supported through compatibility shims in `app/db/mongo.py`.
- If running data migrations to consolidate legacy collections, use the dry-run migration script:
  ```bash
  python scripts/migrate_collections.py --dry-run
  ```

---

## 3. Worker Process Separation

In v1.x, scraping ran inside FastAPI daemon threads. In v2.0:
- Separate worker containers are recommended in production to handle Apify scraping and Gemini AI batch calls.
- Launch workers alongside the web application:
  ```bash
  python -m app.queue.service --worker
  ```
