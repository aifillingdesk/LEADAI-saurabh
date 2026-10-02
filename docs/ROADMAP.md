# LeadAI Product Roadmap

This document outlines the current status of features across LeadAI with transparent, honest implementation statuses.

**Status Legend**:
- `[Implemented]`: Fully developed, tested in CI, and active in production.
- `[Partial]`: Implemented with certain components mocked by default or requiring enterprise API setup.
- `[Planned]`: Scheduled for upcoming milestone releases.

---

## 1. Social Scraping & Listening

| Feature | Status | Notes |
|---|---|---|
| YouTube Video Comments & Channel Scrapes | `[Implemented]` | Direct video & channel scraping with comment collector actor. |
| Facebook Pages & Posts Scraper | `[Implemented]` | Actor-based collection with post reaction counts and filters. |
| Instagram Profiles & Hashtags | `[Implemented]` | Media and comment collection for public profiles. |
| LinkedIn Posts & Comments | `[Partial]` | Actor integration defined; subject to LinkedIn enterprise cookie constraints. |
| X (Twitter) Scraper | `[Planned]` | Planned for future release under v2.2. |
| Bulk URL Submission | `[Implemented]` | Concurrent multi-URL batch ingestion. |
| Recurring / Scheduled Scans | `[Implemented]` | Automated hourly/daily scans fetching only new posts and comments. |

---

## 2. AI Intelligence & Lead Analysis

| Feature | Status | Notes |
|---|---|---|
| Gemini Intent Classification | `[Implemented]` | Multi-intent classification with prompt injection defense. |
| Contact Information Extraction | `[Implemented]` | Phone, email, WhatsApp, and website extraction from unstructured comments. |
| Budget & Requirement Parsing | `[Implemented]` | Identifies pricing queries, budget limits, and geographic locations. |
| Lead Scoring (0-100) & Quality Tiers | `[Implemented]` | Deterministic scoring with Hot (>=80), Warm (50-79), and Cold tiers. |
| Multi-Model Fallback (Claude/OpenAI) | `[Planned]` | Fallback to Anthropic Claude or OpenAI if Gemini experiences rate throttling. |

---

## 3. CRM & Integrations

| Feature | Status | Notes |
|---|---|---|
| CSV & Excel (.xlsx) Safe Exports | `[Implemented]` | UTF-8-BOM CSV and XML spreadsheet exports with formula escaping. |
| Outbound Webhooks | `[Implemented]` | Signed with HMAC-SHA256 (`X-LeadAI-Signature`). |
| HubSpot CRM Connector | `[Implemented]` | Formatter and sync adapter ready. |
| Zoho CRM Connector | `[Implemented]` | Formatter and sync adapter ready. |
| Google Sheets Export | `[Implemented]` | Multi-row batch payload formatter. |
| Automated WhatsApp Outreach | `[Planned]` | Direct integration with WhatsApp Business API / Twilio. |

---

## 4. Billing & Multi-Tenancy

| Feature | Status | Notes |
|---|---|---|
| Razorpay (UPI, NetBanking, Cards) | `[Implemented]` | Full INR billing provider with webhook signature verification. |
| Stripe Provider | `[Partial]` | Implemented; runs in mock checkout mode by default unless live Stripe credentials are provided. |
| Cross-Tenant Security Isolation | `[Implemented]` | 100% verified cross-tenant tamper protection across all routes and database collections. |
| Public REST API v1 | `[Implemented]` | SHA-256 hashed keys with per-org scopes and rate limits. |
| Unit Economics Dashboard | `[Implemented]` | Token costs, Apify costs, and margin analysis per plan tier. |
