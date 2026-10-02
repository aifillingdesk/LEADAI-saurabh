# LeadAI Known Limitations & Architectural Boundaries

This document details intentional architectural trade-offs, default fallbacks, and operational limits across the LeadAI platform.

---

## 1. Billing & Payment Gateway Defaults

### Mock Stripe Provider
- By default, when `BILLING_PROVIDER=stripe` is selected without live `STRIPE_SECRET_KEY` credentials in `.env`, the system runs the **Mock Stripe Billing Provider**.
- In mock mode:
  - Checkout sessions succeed immediately using test mock payment webhooks.
  - Subscriptions simulate payment confirmations without charging credit cards.
- **Production Resolution**: For real payment processing, set `BILLING_PROVIDER=razorpay` with real Razorpay credentials (supporting UPI and Indian cards) or set valid `STRIPE_SECRET_KEY` and `BILLING_WEBHOOK_SECRET` for Stripe.

---

## 2. In-Process & In-Memory Fallbacks

### Queue Worker Fallback
- When `REDIS_URL` is not provided in `.env`, LeadAI automatically falls back to an **in-memory thread pool** for background jobs.
- **Limitation**: In-memory jobs do not survive physical server restarts. If the application process terminates while a scrape is in progress, the job status remains `running` until marked failed by the supervisor.
- **Production Resolution**: Always configure a dedicated Redis instance (`REDIS_URL=redis://...`) in production environments.

### Rate Limiting Fallback
- When Redis is unavailable, API rate limiting operates in-process per application replica using sliding-window memory buffers.
- **Limitation**: Multi-node horizontal deployments without Redis will track rate limits separately per node.

---

## 3. Platform & Third-Party API Limits

### Social Media Scraping (Apify Actors)
- Social platforms (Meta, YouTube, LinkedIn) periodically update their web DOM and anti-bot challenges.
- Scraping runs rely on Apify actors. If an actor experiences upstream network blocks or platform layout changes, Apify may return empty datasets or actor run errors.
- LeadAI surfaces these upstream errors as `scrape_failed` with the exact Apify actor run link for administrative inspection.

### AI Token & API Rate Limits
- Calls to Google Gemini are subject to per-minute request limits (RPM) and token limits (TPM) imposed by Google Cloud project quotas.
- For high-volume organizations running multiple simultaneous scans, batch classification jobs will wait for retry backoff if rate limits are exceeded.

### Concurrent Export Limits
- To prevent denial-of-service via massive spreadsheet generation, concurrent export generation is capped at 3 simultaneous exports per server worker (`_EXPORT_MAX_CONCURRENT = 3`).
- Additional export requests return HTTP `429 Too Many Requests` until an ongoing export completes.
