# LeadAI v2.5.0 — AI-Orchestrated Social Lead Intelligence Platform

An AI-powered, **industry-agnostic SaaS lead generation and management platform** for any legitimate business — real estate, automobiles, education, healthcare, finance, travel, hotels, restaurants, e-commerce, software/SaaS, agencies, professional services, construction, interior design, weddings, retail, manufacturing, B2B and more. It works from a **single social URL**: paste a Facebook page, Instagram profile, YouTube channel, or LinkedIn company link, and the platform fetches real data through **Apify** actors, drills into posts and comments, and uses a **rule-based + Google Gemini** pipeline to extract, qualify, and score high-intent leads — served through a FastAPI REST API with a browser dashboard, a full admin control center, and a public marketing website.

Paste **`https://www.facebook.com/somepage`** (or an Instagram / YouTube / LinkedIn profile) and the platform:

1. Detects + canonicalizes the URL (`app/social/url_detector.py`)
2. Fetches page details via the platform's Apify actor
3. Collects the latest posts and auto-analyzes each post for relevance
4. Collects comments (Facebook, Instagram & LinkedIn) from qualifying posts
5. AI-analyzes each comment for phone, email, WhatsApp, budget, requirement, urgency, and intent (buying / selling / rent / investment / other)
6. Scores leads 0–100 with a deterministic rank, manages lead lifecycle (new → contacted → qualified → follow-up → converted / lost), and lets you export pages, posts, and leads as CSV

Every organization chooses its **industry** and describes its business (Org Admin → Organization → Business profile). Lead analysis — the Gemini prompt, the rule-based vocabulary and the optional industry comment filter — always uses that organization's business context, so "a lead" means something different for a car dealer, a coaching institute or a SaaS company. See [Industries & business context](#industries--business-context).

The detected platform (facebook / instagram / youtube / linkedin) is propagated end-to-end — storage, API responses, UI labels, report page, and CSV exports — so an Instagram search always looks like Instagram and is **never** mislabeled as Facebook.

> **Stack:** FastAPI · MongoDB (Motor + PyMongo) · Apify actors · Google Gemini (optional — rule-based fallback) · Vanilla JS UI · Docker · bcrypt auth · Multi-tenant SaaS architecture

---

## Table of Contents

- [1. Project Overview](#1-project-overview)
- [2. Key Features](#2-key-features)
- [3. Technology Stack](#3-technology-stack)
- [4. System Architecture](#4-system-architecture)
- [5. Folder Structure](#5-folder-structure)
- [6. End-to-End Workflow & Customer Journey](#6-end-to-end-workflow--customer-journey)
- [7. UI Pages & Panels](#7-ui-pages--panels)
- [8. Admin Control Center (Platform Console)](#8-admin-control-center-platform-console)
- [9. Organization Admin Portal](#9-organization-admin-portal)
- [10. Super Admin Portal](#10-super-admin-portal)
- [11. Public Website](#11-public-website)
- [12. User Self-Service API (Me Endpoints)](#12-user-self-service-api-me-endpoints)
- [13. Notifications API](#13-notifications-api)
- [14. SaaS & Multi-Tenancy](#14-saas--multi-tenancy)
- [15. CMS (Content Management)](#15-cms-content-management)
- [16. Lead Data Model & Database Inventory](#16-lead-data-model--database-inventory)
- [17. API Architecture](#17-api-architecture)
- [18. Apify Architecture](#18-apify-architecture)
- [19. AI Architecture](#19-ai-architecture)
- [20. Comment Filter Pipeline](#20-comment-filter-pipeline)
- [21. Lead Lifecycle](#21-lead-lifecycle)
- [22. Data Flow](#22-data-flow)
- [23. Error Handling](#23-error-handling)
- [24. Environment Variables](#24-environment-variables)
- [25. System Settings](#25-system-settings)
- [26. Installation](#26-installation)
- [27. Running the Project](#27-running-the-project)
- [28. Authentication & Authorization](#28-authentication--authorization)
- [29. Cost and Resource Usage](#29-cost-and-resource-usage)
- [30. Security](#30-security)
- [31. Performance](#31-performance)
- [32. Logging and Monitoring](#32-logging-and-monitoring)
- [33. Testing](#33-testing)
- [34. Troubleshooting](#34-troubleshooting)
- [35. Developer Guide](#35-developer-guide)
- [36. Git Workflow](#36-git-workflow)
- [37. Architecture Summary](#37-architecture-summary)

---

## 1. Project Overview

### The Problem

Every business that sells a product or service — a car dealer, a coaching institute, a clinic, a SaaS company, a restaurant, a manufacturer, a real-estate developer — needs a constant stream of qualified buyers. Social media is full of buying signals — business pages post offers and interested people comment with phone numbers, budgets, and urgent requirements. Manually reading thousands of comments is impossible.

### What LeadAI Does

LeadAI automates the entire funnel from **a single social URL**:

- **Understands the target** — any Facebook page, Instagram profile, YouTube channel, or LinkedIn company URL is detected, validated, and canonicalized (`app/social/url_detector.py`). Groups, single posts/videos, and malformed links are rejected with a clear error type.
- **Finds real data** — the URL is handed to the platform's Apify actor (`app/social/scrapers.py`). The agent never scrapes social media itself and never fabricates data — every stored value comes from real actor output.
- **Scores the page** — posts are collected and qualified: a post is *relevant* when its text mentions the target page's context, and *qualifying* when it also has at least `MIN_COMMENTS` (default 10) platform-reported comments. Pages get a deterministic `lead_score` from qualifying-post volume, comment volume, and posting recency/activity.
- **Finds leads in comments** — comments on qualifying posts are collected and passed through a two-stage pipeline: a free, offline rule stage (filters filler/spam, regex-extracts phone/email/whatsapp/budget/location/urgency) and an optional Gemini stage (rich extraction of contact info, person context, and buyer signals). Every lead gets a 0–100 `lead_score`, a priority (high/medium/low), a lead quality (hot/warm/cold), and a value list of `is_lead` display signals.
- **Manages lead lifecycle** — leads move through a state machine (new → contacted → qualified → follow-up → converted / lost / disqualified / archived) with notes, follow-ups, and full status history.
- **Presents and exports** — a live dashboard lets you drill page → posts → comments → lead cards, a standalone report page opens automatically per run, any level can be exported as CSV, and a full admin control center provides system-wide management.

### What Makes It Different from a Simple Scraper

- Deterministic, explainable **qualification logic** (relevance tokens + comment-count threshold) instead of dumping everything.
- **One provider, well integrated** — all scraping flows through `ApifyConnector` with classified errors and cancellation support.
- **AI analysis with a rule-based fallback** — the product works end-to-end even without a Gemini key.
- **Classified errors** — failures are labeled (`BLOCKED`, `ACTOR_FAILED`, `NO_RESULTS`, `ACCESS_DENIED`, ...) with run/dataset IDs, so "no results" is never mistaken for a block.
- **Cancellable background jobs** with live status persisted in MongoDB.
- **Full admin control center** with 25+ views for managing platforms, AI, scoring, users, security, and system settings.
- **Lead lifecycle management** with state machine, notes, follow-ups, and audit trail.

### Who It Is For

Sales teams, agencies, and lead-generation businesses in **any industry** that already know which pages/profiles matter and want qualified buyers from social-media conversations. Rule-based extraction understands English, Hinglish and Hindi, and budgets in ₹/lakh/crore as well as $, €, £, AED and SAR.

### Industries & business context

| Piece | Where | What it does |
| --- | --- | --- |
| Industry catalog | `app/pipeline/business_context.py` (`BUILTIN_INDUSTRIES`) + `industries` collection | 22 built-in industries (General, Real Estate, Automobiles, Education, Healthcare, Finance, Travel, Hotels, Restaurants, E-commerce, Retail, Software/SaaS, Agencies, Professional Services, Construction, Interior Design, Events & Weddings, Home Services, Fitness & Beauty, Manufacturing, B2B Services, Recruitment). Each has suggested keywords, requirement terms, linked comment categories and "what a qualified lead is" guidance for the AI. |
| Super Admin | `/superadmin#/industries` · `/api/super-admin/industries` | Add custom industries, edit or disable built-in ones (overrides are stored, code defaults stay), choose the platform default industry. All changes audited. |
| Org Admin | `/org-admin#organization/business` · `/api/org-admin/business-profile` | Pick the industry and describe the business (what it does, offerings, ideal customers, what makes a qualified lead, extra requirement terms). Optional "only analyse comments relevant to my industry" filter. Audited. |
| Signup | `/signup` (optional *Industry* field) | The chosen industry is stored on the pending organization. |
| Users | User Portal search screen | Shows "Leads are analysed for your business: <industry>"; every search uses the organization's context automatically. |

How the context is used for every post of a search (`analyze_comments_for_post`):

1. **AI** — the business context is appended to the active prompt (whatever version is active) inside a delimited block marked as configuration data, never instructions, and is also sent as `business_category`. Gemini judges intent relative to *this* business.
2. **Rules** — the offline stage uses the industry's requirement terms plus the organization's own terms (a car dealer's "suv", a school's "admission"); the *General* industry keeps a conservative cross-industry list.
3. **Filter (optional)** — with `filter_by_industry` on and no custom lead keywords, comments are pre-filtered by the industry's categories and keywords plus the universal *High Purchase Intent* and *Information Request* presets.
4. Each analysed lead stores the `industry` it was analysed with.

Organizations that never chose an industry use the platform default (`business.default_industry`, default `general` = any business), so existing behaviour is unchanged.

---

## 2. Key Features

Status legend: ✅ Implemented · 🟡 Partially Implemented · ⬜ Planned

### Core Search & Pipeline

| Feature | Status | Location |
| --- | --- | --- |
| URL-based search: Facebook / Instagram / YouTube / LinkedIn | ✅ | `POST /api/url/search` → `app/social/` |
| URL detection + canonicalization (groups/posts/videos rejected) | ✅ | `app/social/url_detector.py` |
| Per-platform Apify actors (configurable actor IDs) | ✅ | `app/social/scrapers.py` + `app/config.py` |
| Graceful fallback page doc when details actor fails | ✅ | `_url_derived_page` in `app/social/url_search.py` |
| Platform propagation end-to-end (never mislabeled) | ✅ | `platform_from_url` / `_resolve_platform` + `platform` field everywhere |
| Configurable "Comments / Post" (1–500) | ✅ | UI setting synced with URL search (`memory.commentsPerPost`) |
| Comments loading progress bar (determinate + indeterminate) | ✅ | `showCommentsScrapeProgress` in `app.js` |
| Post qualification (relevant + ≥ MIN_COMMENTS) | ✅ | `_is_qualifying_post` in `app/agent/search.py` |
| Page lead ranking (qualifying posts, comments, activity) | ✅ | `_lead_score` in `app/agent/search.py` |
| Duplicate removal (unique indexes per run) | ✅ | MongoDB unique indexes |

### Data Collection

| Feature | Status | Location |
| --- | --- | --- |
| Facebook page detail extraction (followers, likes, category, about, phone, email, website, address, photos, verified) | ✅ | `apify/facebook-pages-scraper` + `map_page_item` |
| Facebook post collection (caption, images, videos, links, likes, comments, shares, date) | ✅ | `apify/facebook-posts-scraper` |
| Facebook comment collection (text, author, profile URL, date, reactions) | ✅ | `apify/facebook-comments-scraper` |
| Comments for Facebook, Instagram & LinkedIn; YouTube skips comments | ✅ | `app/social/scrapers.py` (`comments_supported`) |
| LinkedIn posts + comments (drill-down and URL search) | ✅ | `get_scraper(platform)` routes by platform |

### AI & Lead Analysis

| Feature | Status | Location |
| --- | --- | --- |
| Contact-info flagging (10-digit phone / email in comment) | ✅ | `has_contact_info` in `app/pipeline/comment_ai.py` |
| Rule-based comment screening (spam / filler / emoji / link-only) | ✅ | Stage 1 of `comment_ai.py` |
| Gemini comment analysis (contacts, budget, requirement, intent, urgency, quality) | ✅ (needs `GEMINI_API_KEY`) | Stage 2 of `comment_ai.py` |
| Buyer/seller/other intent classification | ✅ | Gemini `lead_type` + rule path |
| Lead quality (hot/warm/cold) and priority (high/medium/low) | ✅ | Gemini + `comment_lead_score` |
| Deterministic 0–100 lead score | ✅ | `comment_lead_score` in `comment_ai.py` |
| Keyword/comment filter pipeline (keyword/category/advanced rules) | ✅ | `app/pipeline/comment_filter.py` |
| Predefined business categories (real estate, automotive, etc.) | ✅ | `CATEGORIES` dict in `comment_filter.py` |
| Industry-aware analysis (per-organization industry + business profile drive AI, rules and filter) | ✅ | `app/pipeline/business_context.py` |
| Industry catalog managed by the Super Admin (22 built-in + custom, default industry) | ✅ | `/superadmin#/industries` |
| Lead lifecycle state machine (new → contacted → qualified → ...) | ✅ | `app/pipeline/lead_lifecycle.py` |
| Notes and follow-ups on leads | ✅ | `POST /api/leads/{id}/notes` + `/follow-ups` |

### UI & Dashboard

| Feature | Status | Location |
| --- | --- | --- |
| Live dashboard (URL search → pages → posts → comments/leads) | ✅ | `app/static/index.html` + `app.js` |
| Standalone URL-search report page | ✅ | `/static/url_report.html` |
| Lead detail modal (full dossier with status, notes, follow-ups, history) | ✅ | `openLeadDetail` in `app.js` |
| Page filters (category / city / name / has-contact) | ✅ | `GET /api/pages` |
| Comments filter (all / leads / contact / hot / pricing / inquiry) | ✅ | `GET /api/posts/{id}/comments` |
| Search bar with debounced input | ✅ | `commentsSearchInput` in `app.js` |
| Quality filter dropdown | ✅ | `commentsQualityFilter` |
| Sort by options | ✅ | `commentsSortBy` |
| CSV export (pages / posts / leads) | ✅ | `GET /api/export/*.csv` |
| Search history with session stats | ✅ | `GET /api/search/history` |
| Manual deletion of individual searches | ✅ | `DELETE /api/search/{run_id}` + ✕ button |
| Run cancellation (aborts in-flight Apify run) | ✅ | `POST /api/search/{run_id}/cancel` |
| Live progress polling (UI progress bar + status fields) | ✅ | `app.js` `pollSearchRun` |
| localStorage workflow memory (survives refreshes) | ✅ | `memory` object in `app.js` |
| Toast notifications (success / error / warning / info) | ✅ | `toast()` in `app.js` |
| Ambient background orbs + grid overlay | ✅ | CSS animations in `styles.css` |
| Responsive layout | ✅ | CSS media queries |

### Authentication & Admin

| Feature | Status | Location |
| --- | --- | --- |
| Site login (admin email/password, bcrypt, session cookie) | ✅ | `/login` + `POST /api/auth/login` |
| Admin portal login (separate credential set) | ✅ | `/login?admin=1` |
| Role-based access control (viewer / manager / super_admin) | ✅ | `app/auth/roles.py` |
| Brute-force throttle (5 failed attempts → lockout) | ✅ | `app/auth/service.py` |
| Password visibility toggle on login | ✅ | `login.html` eye button |
| Remember Me (persists email in localStorage) | ✅ | `login.html` |
| Full admin control center (30+ views) | ✅ | `/admin` → `admin.html` + `admin.js` |
| Audit logging of admin actions | ✅ | `app/admin/audit.py` |
| Maintenance mode | ✅ | Toggle in admin → `maintenance.html` |
| Feature flags (URL search, exports, per-platform) | ✅ | Admin Features page |
| Global settings (branding, appearance, localization) | ✅ | Admin Global Settings page |
| Environment variable management (3-layer model) | ✅ | Admin Environment page |
| User management (CRUD with role assignment) | ✅ | Admin Users page |
| Session revocation | ✅ | Admin Security page |

### Admin Control Center Views

| Feature | Status | Location |
| --- | --- | --- |
| Dashboard with configurable KPI widgets, charts, alerts | ✅ | Admin Dashboard |
| Jobs management (filter, cancel, retry, delete) | ✅ | Admin Jobs |
| Failed jobs with retry/bulk retry | ✅ | Admin Failed Jobs |
| Leads management (filter, bulk actions, detail modal) | ✅ | Admin Leads |
| Analytics (date ranges, charts, platform performance) | ✅ | Admin Analytics |
| Pages & Posts tables | ✅ | Admin Pages / Posts |
| Platform management (enable/disable, actor IDs) | ✅ | Admin Platforms |
| Apify integration (token, test, usage) | ✅ | Admin Apify |
| Actor management (grid, test, links) | ✅ | Admin Actors |
| Usage & cost tracking | ✅ | Admin Usage & Cost |
| AI / Gemini settings + live test | ✅ | Admin AI |
| Lead scoring tuning (weights, signals, thresholds) | ✅ | Admin Lead Scoring |
| Comment intelligence (detection toggles, stats, feed) | ✅ | Admin Comment Intelligence |
| Keyword rules (CRUD, activate, reapply, filtered comments) | ✅ | Admin Keyword Rules |
| Scrape limits + cost protection | ✅ | Admin Limits |
| Database stats | ✅ | Admin Database |
| Log viewer (filter, autoscroll) | ✅ | Admin Logs |
| Health checks (all subsystems) | ✅ | Admin Health |
| CSV exports (jobs, pages, posts, leads, follow-ups, logs) | ✅ | Admin Exports |
| Security settings (session timeout, login protection) | ✅ | Admin Security |
| Audit log (immutable admin action feed) | ✅ | Admin Audit Log |
| Global settings editor (tabs, version history, import/export) | ✅ | Admin Global Settings |
| Plans catalog management | ✅ | Admin Plans |
| Subscriptions management | ✅ | Admin Subscriptions |
| Invoices management | ✅ | Admin Invoices |
| CMS pages (CRUD, draft/publish/restore/version-history) | ✅ | Admin CMS Pages |
| CMS FAQ (CRUD, reorder) | ✅ | Admin CMS FAQ |
| CMS testimonials (CRUD) | ✅ | Admin CMS Testimonials |
| CMS navigation (header/footer) | ✅ | Admin CMS Navigation |
| CMS website settings (branding, SEO, social links) | ✅ | Admin CMS Settings |
| CMS media (upload, list, delete) | ✅ | Admin CMS Media |
| CMS contact submissions (list, mark read) | ✅ | Admin CMS Contact |
| AI prompts (CRUD, version control, rollback) | ✅ | Admin AI Prompts |
| AI models (config, pricing, defaults) | ✅ | Admin AI Models |

### SaaS & Multi-Tenancy

| Feature | Status | Location |
| --- | --- | --- |
| Multi-tenant organizations with Default Org auto-provisioning | ✅ | `app/db/migration.py` + `app/db/saas_models.py` |
| Organization membership with roles (owner/admin/manager/member/viewer) | ✅ | `app/api/routes/organizations.py` |
| Team invitation system (token-hashed, expiring, accept/revoke) | ✅ | `app/billing/invitations.py` |
| Subscription plans (Free/Starter/Pro/Business/Enterprise) with feature flags | ✅ | `app/billing/plans.py` |
| Trial provisioning, upgrade/downgrade, cancellation, reactivation | ✅ | `app/billing/subscriptions.py` |
| Quota enforcement with atomic counters (searches, AI, team, exports) | ✅ | `app/billing/entitlements.py` + `app/billing/usage.py` |
| Usage tracking with cost calculation per AI model | ✅ | `app/pipeline/ai_usage_service.py` |
| Customer billing modal (plans, quotas, invoices, subscription) | ✅ | Billing modal in `index.html` |
| Workspace settings modal (name, timezone, currency, branding) | ✅ | Workspace modal in `index.html` |
| Team management modal (invite, roles, remove, pending) | ✅ | Team modal in `index.html` |

### Public Website & CMS

| Feature | Status | Location |
| --- | --- | --- |
| Public marketing website (hero, features, how-it-works, pricing, FAQ, CTA) | ✅ | `app/static/website.html` |
| Self-service signup page | ✅ | `app/static/signup.html` |
| Contact page with form (rate-limited, validated) | ✅ | `app/static/contact.html` |
| CMS pages (CRUD, draft/publish/restore/version-history) | ✅ | `app/api/routes/admin_cms.py` |
| FAQ management (CRUD, reorder) | ✅ | CMS Admin |
| Testimonials management | ✅ | CMS Admin |
| Website settings (branding, SEO, social links, footer) | ✅ | CMS Admin |
| Media upload/management | ✅ | CMS Admin |
| Contact submission management | ✅ | CMS Admin |
| Dynamic pricing cards (from DB plans) | ✅ | `website.html` fetches `/api/public/pricing` |
| Dynamic FAQ accordion (from DB) | ✅ | `website.html` fetches `/api/public/faq` |
| Runtime branding (colors, name, favicon via `config.js`) | ✅ | `app/static/config.js` + `/api/public/config` |

### Admin AI & Prompt Management

| Feature | Status | Location |
| --- | --- | --- |
| AI model registry (Gemini 2.5 Flash/Pro, configurable) | ✅ | `app/pipeline/ai_models_service.py` |
| Version-controlled prompt management (edit, rollback, variables) | ✅ | `app/pipeline/ai_prompt_service.py` |
| AI usage & cost tracking per request | ✅ | `app/pipeline/ai_usage_service.py` |
| Admin AI routes (prompts CRUD, model config, live test) | ✅ | `app/api/routes/admin_ai.py` |
| Admin Apify routes (actor management, job retry/bulk) | ✅ | `app/api/routes/admin_apify.py` |
| Admin Leads routes (search, dossier, notes, bulk actions) | ✅ | `app/api/routes/admin_leads.py` |
| Admin Analytics routes (SaaS overview, funnel, platform comparison) | ✅ | `app/api/routes/admin_analytics.py` |

### Implementation Status Notes

| Feature | Status | Notes / Location |
| --- | :---: | --- |
| API Rate Limiting | ✅ | Dual-layer: in-memory sliding window (30 req/min) on search API (`app/api/routes/search.py`); MongoDB-backed persistent rate limiter (`rate_limits`) for auth, login brute-force, signups, and contact forms (`app/auth/rate_limit.py`). |
| Stripe Billing Integration | 🟡 Partial | Mock payment gateway active by default (`MOCK_PAYMENTS_ENABLED=true`); Stripe checkout session generation and HMAC webhook verification implemented (`app/billing/`). Live card charge requires live keys. |
| Priority Support & SLAs | 🟡 Partial | Full in-app ticketing and support center in Super Admin and Org Admin (`app/api/routes/org_admin.py`); external ticketing and formal enterprise SLA webhooks planned. |
| Public REST API & API Keys | ⬜ Planned | Scheduled for Phase 6 (API key generation, HMAC validation, developer portal, per-org quotas matching Business plan). |
| Background Job Queue (Celery/RQ/Arq) | ⬜ Planned | Current jobs run in managed background asyncio tasks/threads with live DB status; distributed Redis queue scheduled for Phase 5. |
| CRM Connectors (HubSpot, Zoho, Sheets) | ⬜ Planned | Direct export to CSV/JSON active; automated CRM push scheduled for Phase 6. |

---

## 3. Technology Stack

| Layer | Technology | Purpose |
| --- | --- | --- |
| Frontend (User) | Vanilla HTML5 / CSS3 / JavaScript (no framework, no build step) | Dashboard: URL search, pages, posts, comments, lead cards, CSV export, workspace/team/billing modals |
| Frontend (Admin) | Vanilla HTML5 / CSS3 / JavaScript (no framework, no build step) | Admin control center: 30+ views, charts, settings, user management |
| Frontend (Public) | Vanilla HTML5 / CSS3 / JavaScript (design system: tokens.css, components.css) | Marketing website, signup, contact, FAQ, pricing |
| Backend | FastAPI + Uvicorn (Python 3.11+) | REST API, static hosting, background task orchestration |
| AI Model | Google Gemini `gemini-2.5-flash` (configurable) | Comment analysis |
| AI Access | REST `generativelanguage.googleapis.com/v1beta` via `httpx` | No SDK dependency |
| Scraping | Apify (`apify-client`) — 3 Facebook actors + configurable IG/YT/LI actors | All social data collection |
| Database | MongoDB 7 (Motor async + PyMongo sync) | Persistence of pages/posts/comments/analysis/history/settings/audit/organizations/subscriptions |
| Auth | bcrypt (cost 12) + session cookies + role-based access control | All routes protected |
| Multi-tenancy | Organization-scoped data, membership roles, invitation tokens | SaaS workspace isolation |
| CMS | MongoDB-backed content (pages, FAQ, testimonials, navigation, media) | Dynamic public website content |
| Configuration | `pydantic-settings` reading `.env` + MongoDB `system_settings` collection | All settings |
| Deployment | Docker + docker-compose (API + Mongo) | Containerized run |
| Code quality | Ruff (lint) + mypy (type checks) | Dev workflow |
| Testing | `pytest`-style acceptance tests + integration checks | `tests/` |

Key libraries (`requirements.txt`): `fastapi`, `uvicorn[standard]`, `pydantic`, `pydantic-settings`, `motor`, `pymongo`, `httpx`, `apify-client`, `python-multipart`, `bcrypt`.

---

## 4. System Architecture

```mermaid
flowchart TD
    U[User] --> FE[Dashboard<br/>app/static: index.html + app.js]
    U --> ADM[Admin Control Center<br/>app/static: admin.html + admin.js]
    U --> WEB[Public Website<br/>app/static: website.html / signup / contact]

    FE -->|"POST /api/url/search<br/>(social URL)"| API[FastAPI app.main:app]
    FE -->|"poll GET /api/search/{run_id}"| API
    FE -->|"GET pages, posts, comments, export"| API
    FE -->|"Workspace, Team, Billing"| API

    ADM -->|"GET/POST /api/admin/*"| API
    ADM -->|"POST /api/auth/login"| AUTH[Auth Router<br/>app/api/routes/auth.py]
    ADM -->|"CMS, AI, Apify, Leads, Analytics"| API

    WEB -->|"GET /api/public/*"| API
    WEB -->|"POST /api/public/contact"| API

    API --> DB[(MongoDB 'LeadAI'<br/>38 collections)]
    API -->|in-process background thread| URS[URL Search Pipeline<br/>app/social/url_search.py]

    URS --> UDET[URL Detector<br/>app/social/url_detector.py]
    URS --> SCRAP[Platform Scrapers<br/>app/social/scrapers.py]
    SCRAP --> APIFY[ApifyConnector<br/>app/connectors/apify_connector.py]

    APIFY --> A2[facebook-pages-scraper]
    APIFY --> A3[facebook-posts-scraper]
    APIFY --> A4[facebook-comments-scraper]
    APIFY --> A5[Configurable actors<br/>instagram / youtube / linkedin]

    URS --> QT[Qualification + Scoring<br/>app/agent/search.py]
    QT --> CAAI[Comment AI Pipeline<br/>app/pipeline/comment_ai.py]
    CAAI -->|Stage 1: rule filter| CF[Comment Filter<br/>app/pipeline/comment_filter.py]
    CAAI -->|Stage 2: Gemini| GEMINI[Google Gemini API]
    CAAI -->|upsert analysis| DB

    URS -->|normalize + dedupe| DB

    API --> ADMIN[Admin Settings<br/>app/admin/settings.py]
    API --> AUDIT[Audit Logging<br/>app/admin/audit.py]
    API --> ENVVARS[Env Var Management<br/>app/admin/envvars.py]
    API --> LIFECYCLE[Lead Lifecycle<br/>app/pipeline/lead_lifecycle.py]

    API --> SAAS[SaaS Layer<br/>billing/ + organizations/ + usage/]
    SAAS --> DB
    API --> CMS[CMS Service<br/>app/cms/service.py]
    CMS --> DB
```

### Components

- **`app/main.py`** — FastAPI application (v2.5.0). Sets up configurable console + file logging, starts the Mongo index check at startup, reconciles stale running jobs, seeds default SaaS plans and CMS defaults, mounts the static frontend, and exposes `/health`, `/`, `/dashboard`, `/login`, `/admin`, `/website`, `/signup`, `/contact`, `/features`, `/pricing`, `/about`, `/faq`, `/privacy`, `/terms`. Includes security headers middleware, CSRF protection, auth gate (session validation per scope), and maintenance mode gate.

- **`app/log_parser.py`** — Structured log parser (364 lines). Normalizes raw log lines into structured dicts with level, module, source classification, event extraction, error type detection, structured field extraction, multiline stack trace grouping, and automatic secret redaction.

- **`app/api/routes/search.py`** — The entire product REST surface (15 `/api` routes + CSV exports). All long-running operations run as **in-process background tasks**; GET endpoints expose live status fields for polling. Includes per-user API rate limiting (30 req/min).

- **`app/api/routes/admin.py`** — The admin control center API (50+ endpoints). Every endpoint is role-protected.

- **`app/api/routes/admin_ai.py`** — Admin AI routes: prompt CRUD, model configuration, live AI test panel.

- **`app/api/routes/admin_apify.py`** — Admin Apify routes: actor management, job retry (max 3), bulk operations.

- **`app/api/routes/admin_leads.py`** — Admin Leads routes: search, dossier, notes, follow-ups, bulk actions, data quality dashboard.

- **`app/api/routes/admin_analytics.py`** — Admin Analytics routes: SaaS overview, lead funnel, platform comparison, category breakdown, plan analytics.

- **`app/api/routes/admin_cms.py`** — Admin CMS routes: pages (CRUD, draft/publish/restore/version-history), FAQ, testimonials, navigation, website settings, contact submissions, media upload.

- **`app/api/routes/auth.py`** — Login/logout/me endpoints for both site and admin scopes, plus signup endpoint.

- **`app/api/routes/comment_filters.py`** — Comment filter rules CRUD, activation, reapplication, and filtered comments feed.

- **`app/api/routes/settings.py`** — Global settings admin API + public `/api/public/config` for branding.

- **`app/api/routes/organizations.py`** — Organization & team management: workspace settings, member CRUD, invitation flow.

- **`app/api/routes/billing.py`** — SaaS billing: plans catalog, subscription management, usage tracking, checkout, invoices, webhooks.

- **`app/api/routes/public_website.py`** — Public CMS endpoints: theme, pricing, FAQ, contact form (rate-limited).

- **`app/social/url_search.py`** — The URL-search orchestrator. Runs in a daemon thread, writes live status to `search_history`, and drives page → posts → comments → AI analysis with cancellation checkpoints.

- **`app/social/url_detector.py`** — Platform detection + canonicalization for FB/IG/YT/LinkedIn, with classified `UrlError` (invalid / unsupported).

- **`app/social/scrapers.py`** — One `SocialMediaScraper` subclass per platform over `ApifyConnector`; normalizes platform-specific actor items into the shared document shapes. Also the router used by the drill-down collectors (`get_scraper(platform)`).

- **`app/agent/search.py`** — The shared lead-collection engine: `collect_page_posts`, `collect_post_comments`, normalizers (`map_page_item`, `map_post_item`, `map_comment_item`), relevance/qualification logic, page scoring (`_compute_page_stats`, `_lead_score`, `_activity_status`), and cancellation helpers.

- **`app/connectors/apify_connector.py`** — Thin wrapper over `apify-client` with classified error objects (`ScrapeError`), run timeouts, cancellation polling, exponential backoff retry (max 3), and request/response logging. Tracks active Apify runs for graceful shutdown abort.

- **`app/pipeline/comment_ai.py`** — The lead-analysis brain: Stage 1 rule filtering + regex extraction, Stage 2 Gemini structured JSON extraction, `comment_lead_score` (0–100), `extract_display_signals` / `is_lead`, and persistence into `ai_comments`.

- **`app/pipeline/comment_filter.py`** — Keyword/category matching pipeline with 4 modes (NO_FILTER, KEYWORD, CATEGORY, ADVANCED). 10+ predefined business categories (real estate, automotive, etc.) with curated keyword lists. Case-insensitive, Unicode-normalized, phrase/word-boundary matching.

- **`app/pipeline/lead_lifecycle.py`** — Lead state machine with 8 statuses, valid transitions, notes, follow-ups, and status history. Terminal states: converted, lost, disqualified, archived.

- **`app/auth/service.py`** — Session cookie management, bcrypt verification, SHA-256 migration for legacy hashes, brute-force throttle.

- **`app/auth/crypto.py`** — Password hashing (bcrypt cost 12) + SHA-256 legacy support.

- **`app/auth/roles.py`** — Role-based authorization (viewer < manager < super_admin). Checks DB `admin_users` collection on each request.

- **`app/auth/tenant.py`** — Multi-tenant context resolution: extracts organization_id from session, provides TenantContext for org-scoped queries.

- **`app/admin/settings.py`** — `system_settings` collection with TTL cache (5 min). `get_setting(key)`, `set_setting(key, value)`, maintenance mode, Apify token management.

- **`app/admin/audit.py`** — `audit_logs` collection. `audit(action, category, user, details, success)`. Secret redaction for sensitive keys.

- **`app/admin/envvars.py`** — `env_overrides` collection. Three-layer model: DB override → .env/Settings → documented default. `ENVVAR_REGISTRY` with kind/secret/restart flags.

- **`app/billing/plans.py`** — Plan tiers (Free/Starter/Professional/Business/Enterprise) with feature flags, limit definitions, default plan seeding, and caching.

- **`app/billing/subscriptions.py`** — Trial provisioning, plan upgrades/downgrades, cancellation scheduling, reactivation, period calculations.

- **`app/billing/usage.py`** — Atomic quota counters in `organization_usage`, immutable audit event logs in `usage_records`, calendar period tracking.

- **`app/billing/entitlements.py`** — Feature entitlement & quota enforcement. Raises `QUOTA_EXCEEDED` (HTTP 402) and `FEATURE_NOT_AVAILABLE` (HTTP 403) with upgrade guidance.

- **`app/billing/invoices.py`** — Invoice history per organization.

- **`app/billing/invitations.py`** — SHA-256 token-hashed invitations with configurable expiration, token validation, membership activation.

- **`app/billing/provider.py`** — Billing provider abstraction (Stripe-ready).

- **`app/cms/service.py`** — CMS CRUD, sanitization, draft/publish/restore/version-history, cache invalidation, audit logging.

- **`app/cms/models.py`** — CMS collection definitions: pages, FAQ, testimonials, navigation, media, settings, contact.

- **`app/db/saas_models.py`** — SaaS data models: organizations, memberships, invitations, subscriptions, usage, invoices, users with status/role enums.

- **`app/db/migration.py`** — Idempotent multi-tenant migration: ensures Default Organization, owner membership, backfills organization_id on existing documents.

- **`app/pipeline/ai_models_service.py`** — AI model registry: Gemini models with pricing, token limits, temperature, context limits.

- **`app/pipeline/ai_prompt_service.py`** — Version-controlled prompt management with rollback, mustache-style variable substitution, reproducibility tracking.

- **`app/pipeline/ai_usage_service.py`** — AI usage & cost tracking per request, aggregation metrics for billing and analytics.

- **`app/settings/registry.py`** — Settings schema registry: 30 admin view definitions, 8 dashboard widget definitions, font/theme options, validation rules (HEX_COLOR_RE, EMAIL_RE, URL_RE, JS_DANGER_RE).

- **`app/db/mongo.py`** — Motor async + PyMongo sync clients, DNS override for `mongodb+srv://`, `_create_index_safe()` wrapper for idempotent index creation (handles IndexKeySpecsConflict), `ensure_indexes()` creates all indexes at startup. Reconciles stale running jobs on startup.

- **`app/db/models.py`** — Pydantic models for all collections: `SearchHistory`, `FacebookPage`, `FacebookPost`, `FacebookComment`, `AICommentAnalysis`, plus admin models.

- **`app/static/index.html`** — Dashboard HTML shell with SaaS modals (workspace, team, billing, quota exceeded).

- **`app/static/app.js`** — Main dashboard SPA (1385 lines): URL search → poll run → pages → posts → comments → lead modal; workflow memory in `localStorage`; workspace/team/billing modal handlers.

- **`app/static/styles.css`** — Main UI design system (2432 lines).

- **`app/static/admin.html`** — Admin Control Center HTML shell (30+ views).

- **`app/static/admin.js`** — Admin SPA frontend (4725+ lines): 30+ views, sidebar, global search, notifications, health pill, structured log viewer with details modal.

- **`app/static/admin.css`** — Admin design system (2097 lines).

- **`app/static/login.html`** — Login page with two scopes (site / admin), password visibility toggle, Remember Me, Forgot Password info.

- **`app/static/signup.html`** — Self-service signup with first/last name, email, organization, password strength meter, terms consent.

- **`app/static/contact.html`** — Contact form with name, email, company, message, character counter, rate limiting, success state.

- **`app/static/website.html`** — Public marketing website: hero, platforms strip, features grid, how-it-works steps, dynamic pricing (from DB), dynamic FAQ (from DB), CTA banner, footer with dynamic links/copyright.

- **`app/static/url_report.html`** — Standalone report page for URL-search runs.

- **`app/static/config.js`** — Runtime branding/global settings provider (325 lines). Fetches `/api/public/config` and applies CSS vars, title, favicon, fonts dynamically.

- **`app/static/maintenance.html`** — Shown when maintenance mode is active.

- **`app/static/design/tokens.css`** — Design tokens: CSS custom properties for colors, spacing, typography, borders, shadows, transitions.

- **`app/static/design/components.css`** — Reusable component styles: buttons, cards, forms, badges, tables, alerts, modals.

- **`app/static/design/theme.js`** — Dynamic theme application from `/api/public/theme` (brand colors, name, logo, favicon).

---

## 5. Folder Structure

```text
lead_apify/
├── app/
│   ├── main.py                    # FastAPI application (v2.5.0): security headers, auth/maintenance gates, /health, page routes, lifespan tasks
│   ├── config.py                  # pydantic-settings: central configuration loaded from .env and environment variables
│   ├── log_parser.py              # Structured log parser: levels, modules, sources, secrets redaction, multiline grouping
│   ├── api/
│   │   ├── models.py              # Pydantic request/response schemas for APIs
│   │   └── routes/
│   │       ├── auth.py            # POST /api/auth/login, /signup, /logout, GET /me (multi-scope authentication)
│   │       ├── search.py          # Core lead generation REST endpoints: URL search, history, cancellation, pages/posts/comments, CSV export
│   │       ├── me.py              # User self-service: /api/me/* (profile, usage quotas, personal searches, assigned leads, export history)
│   │       ├── admin.py           # Platform Console API (50+ endpoints for jobs, platforms, AI, scoring, users, security, limits)
│   │       ├── admin_ai.py        # Admin AI routes: prompt template CRUD, model configuration, live AI test panel
│   │       ├── admin_apify.py     # Admin Apify routes: actor registry, job retries, bulk scraper management
│   │       ├── admin_leads.py     # Admin Leads routes: global search, lead dossier, notes, follow-ups, bulk status actions
│   │       ├── admin_analytics.py # Admin Analytics routes: SaaS overview, conversion funnels, platform comparison, MRR
│   │       ├── admin_cms.py       # Admin CMS routes: pages, FAQ, testimonials, navigation headers/footers, media uploads
│   │       ├── org_admin.py       # Org Admin Portal API: tenant overview, team, business profile, keyword library, scraped data (/data/{kind})
│   │       ├── super_admin.py     # Super Admin Portal API: platform overview, organizations, users, plan catalog, audit logs, impersonation
│   │       ├── super_admin_platform.py   # Super Admin Platform API: system health, integrations, platform analytics, security, feature flags
│   │       ├── super_admin_lifecycle.py  # Super Admin Lifecycle API: demo queue & approvals, subscription queue, payments, tokens, industries
│   │       ├── notifications.py   # In-app notifications: GET /api/notifications, POST /read, audience dispatch
│   │       ├── comment_filters.py # Comment filter rules CRUD, activation, reapplication, and filtered comments feed
│   │       ├── settings.py        # Global settings admin API + public /api/public/config for dynamic runtime branding
│   │       ├── organizations.py   # Organization & team management (workspace settings, member CRUD, role changes, invitations)
│   │       ├── billing.py         # SaaS billing: plans catalog, subscription management, usage tracking, checkout, invoices, webhooks
│   │       └── public_website.py  # Public CMS endpoints: dynamic theme, pricing plans, FAQ accordion, rate-limited contact form
│   ├── auth/
│   │   ├── service.py             # Session cookie management (HMAC-SHA256), bcrypt verification, brute-force throttle, revocation
│   │   ├── crypto.py              # Password hashing (bcrypt cost factor 12) + legacy SHA-256 migration
│   │   ├── roles.py               # Role-based authorization hierarchy (viewer < manager < super_admin)
│   │   ├── permissions.py         # Granular RBAC permission catalog (platform roles & organization roles matrices)
│   │   ├── superadmin.py          # Permanent Super Admin validator (SUPERADMIN_EMAIL / SUPERADMIN_PASSWORD in .env)
│   │   ├── rate_limit.py          # Per-IP login attempt throttling & lockout protection
│   │   └── tenant.py              # Multi-tenant context resolution (TenantContext, scope_query, find_scoped_or_404, IDOR protection)
│   ├── agent/
│   │   └── search.py              # Lead engine orchestrator: collect_page_posts, collect_post_comments, normalizers, qualification & scoring
│   ├── connectors/
│   │   └── apify_connector.py     # Production Apify wrapper: classified ScrapeError, run timeouts, cancellation polling, exponential backoff
│   ├── db/
│   │   ├── mongo.py               # Async Motor + Sync PyMongo clients, DNS SRV override, ensure_indexes(), stale running job reconciliation
│   │   ├── models.py              # Pydantic models for core collections: SearchHistory, FacebookPage, FacebookPost, FacebookComment, AICommentAnalysis
│   │   ├── saas_models.py         # SaaS data models: organizations, organization_members, invitations, subscriptions, token_balances, users
│   │   └── migration.py           # Idempotent multi-tenant migration & Default Organization backfill on startup
│   ├── pipeline/
│   │   ├── domain_intelligence.py # Domain & Keyword Intelligence: 22 built-in industry taxonomies, taxonomy catalog, recommendation engine
│   │   ├── business_context.py    # Business Profile resolution: builds AI prompt context, target customer types, offerings, and requirement terms
│   │   ├── org_lead_rules.py      # Organization-scoped lead rules: keyword matching, exclusions, and industry filter rules
│   │   ├── lead_assignment.py     # Lead assignment engine: round-robin, workload balancing, and explicit member assignment
│   │   ├── comment_ai.py          # Dual-stage lead analysis: Stage 1 rule filter/regex + Stage 2 Gemini extraction + dual-stage circuit breaker
│   │   ├── comment_filter.py      # Multi-mode comment filter pipeline (NO_FILTER, KEYWORD, CATEGORY, ADVANCED) with 10+ business categories
│   │   ├── lead_lifecycle.py      # Lead state machine (new → contacted → qualified → follow-up → converted/lost/disqualified/archived)
│   │   ├── ai_models_service.py   # AI model registry: Gemini 2.5 Flash/Pro with pricing, token limits, temperature, context limits
│   │   ├── ai_prompt_service.py   # Version-controlled prompt management with rollback, mustache-style variable substitution
│   │   └── ai_usage_service.py    # AI usage & cost tracking per request, aggregation metrics for billing and analytics
│   ├── social/
│   │   ├── url_detector.py        # Platform detection + canonicalization for Facebook, Instagram, YouTube, LinkedIn with classified UrlError
│   │   ├── url_search.py          # URL search background pipeline: page details → posts → comments → AI analysis with cancellation checkpoints
│   │   └── scrapers.py            # Platform scraper classes over ApifyConnector (FacebookScraper, InstagramScraper, YouTubeScraper, LinkedInScraper)
│   ├── admin/
│   │   ├── settings.py            # Admin settings registry (system_settings collection, TTL cache, effective limits, actor overrides)
│   │   ├── audit.py               # Immutable audit logging (audit_logs collection, automatic secret redaction)
│   │   └── envvars.py             # Three-layer env var management (DB override → .env/Settings → default) with restart flags
│   ├── events/
│   │   ├── email.py               # SMTP email delivery (invitations, password resets, system alerts) with local fallback
│   │   ├── notifications.py       # Event-driven in-app notifications (audiences: super_admin, org_admin, user) with read/unread tracking
│   │   └── security.py            # Security event logging (cross-tenant access, brute-force attempts, session revocation)
│   ├── lifecycle/
│   │   ├── config.py              # Lifecycle configuration: demo settings, token allowances, grace periods
│   │   ├── demo.py                # Demo status, expiry checks, and approval orchestration
│   │   └── maintenance.py         # Background sweeper (15-min interval): billing periods, cancellations, demo expiry
│   ├── billing/
│   │   ├── plans.py               # Plan tiers (Free, Starter, Pro, Business, Enterprise), feature flags, limits
│   │   ├── subscriptions.py       # Trial provisioning, upgrades, cancellations, reactivations
│   │   ├── tokens.py              # Token balance management: grants, allocations, consumption, top-ups
│   │   ├── usage.py               # Atomic quota counters, usage records, calendar period tracking
│   │   ├── entitlements.py        # Feature entitlement & quota enforcement (QUOTA_EXCEEDED, FEATURE_NOT_AVAILABLE)
│   │   ├── invoices.py            # Invoice history per organization
│   │   ├── invitations.py         # SHA-256 token-hashed team invitations with expiration
│   │   └── provider.py            # Billing provider abstraction (Stripe-ready with HMAC webhook verification)
│   ├── cms/
│   │   ├── models.py              # CMS collections: pages, FAQ, testimonials, navigation, media, settings, contact
│   │   └── service.py             # CMS CRUD, sanitization, draft/publish/restore/version-history, cache
│   ├── settings/
│   │   └── registry.py            # Settings schema registry (30 admin view definitions, validation, font/theme options)
│   └── static/
│       ├── super-admin.html       # Super Admin Portal shell (/superadmin, GLOBAL badge)
│       ├── super-admin.js         # Super Admin Portal SPA frontend (29 routes across 7 groups): org management, demo approvals, health, CMS, plans
│       ├── super-admin.css        # Super Admin design system (dark modern theme with global control accents)
│       ├── org-admin.html         # Organization Admin Portal shell (/org-admin)
│       ├── org-admin.js           # Org Admin Portal SPA frontend (21 routes): overview, team, business profile, keyword library, scraped data
│       ├── org-admin.css          # Org Admin Portal design system (tenant branding support)
│       ├── admin.html             # Platform Console shell (/admin, 29 navigation views)
│       ├── admin.js               # Platform Console SPA frontend: jobs, platforms, Apify, AI, logs, security
│       ├── admin.css              # Platform Console design system
│       ├── index.html             # User Portal / Workspace shell (/dashboard or /): search, pages, posts, comments/leads, SaaS modals
│       ├── app.js                 # User Portal SPA frontend (11 views): URL search polling, memory, breadcrumbs, lead dossier, presets
│       ├── styles.css             # User Portal design system: glassmorphic accents, ambient orbs, responsive layouts
│       ├── website.html           # Public marketing website: hero, features, how-it-works, dynamic pricing, dynamic FAQ, CTA, footer
│       ├── website.js             # Public website client: dynamic DB pricing & FAQ fetch, interactive demo request modal, smooth scroll
│       ├── website.css            # Public website responsive styling
│       ├── login.html             # Multi-scope login page (site / admin / superadmin scopes, password toggle, Remember Me)
│       ├── signup.html            # Self-service demo signup with password strength meter, industry selector, terms consent
│       ├── contact.html           # Public contact form with client-side validation and IP rate limiting
│       ├── invite.html            # Team invitation acceptance portal (/invite/{token})
│       ├── reset-password.html    # Password reset page (one-time cryptographic token)
│       ├── demo-pending.html      # "Demo pending review" waiting room page
│       ├── billing-status.html    # Hosted billing checkout status return page
│       ├── url_report.html        # Standalone printable report for completed URL-search runs
│       ├── config.js              # Runtime branding & settings provider (dynamically applies colors, logo, and title from API)
│       ├── maintenance.html       # Maintenance mode holding page
│       ├── 403.html / 404.html    # Standard HTTP error pages
│       └── design/
│           ├── tokens.css         # CSS design tokens (colors, typography, spacing, elevations, transitions)
│           ├── components.css     # Reusable UI component styles (buttons, cards, forms, badges, tables, alerts, modals)
│           ├── domain-keywords.css# Domain & keyword recommendation chips, taxonomy cards, and badge styling
│           └── theme.js           # Dynamic runtime theme application from API settings
├── tests/
│   ├── test_auth.py               # Admin login, bcrypt hashing, throttle, session lifecycle
│   ├── test_admin_panel.py        # Platform Console views & API integration
│   ├── test_admin_panel_hardening.py # Input validation, injection prevention, role security
│   ├── test_admin_data.py         # Scraped pages/posts/comments data endpoints
│   ├── test_org_admin.py          # Org Admin Portal: tenant isolation, permissions, exports, tickets, lead rules
│   ├── test_superadmin_portal.py  # Super Admin Portal: dashboard, org lifecycle, impersonation
│   ├── test_super_admin_portal2.py# Super Admin extended lifecycle: demo approvals, token allocations
│   ├── test_superadmin_env.py     # Super Admin environment variables & secret management
│   ├── test_user_portal.py        # User Portal: search presets, URL search execution, lead lifecycle
│   ├── test_saas_multitenancy.py  # Tenant isolation: cross-tenant access rejection, data segregation
│   ├── test_saas_billing_entitlements.py # Plan limits, quota meters, entitlement rejection
│   ├── test_url_search.py         # URL detection, canonicalization, and platform error classification
│   ├── test_qualification.py      # Lead qualification rules, comment relevance, contact detection
│   ├── test_security.py           # CSRF, security headers, XSS prevention, password strength
│   ├── test_comment_filter.py     # Comment filter pipeline (4 modes, keyword & category matching)
│   ├── test_ai_intelligence.py    # Dual-stage AI analysis, Gemini prompt formatting, rule fallback
│   ├── test_industry_context.py   # Industry taxonomies, business context injection, keyword recommendations
│   ├── test_scalability_production.py # High-load concurrency, connection pooling, graceful shutdowns
│   ├── test_settings.py           # System settings CRUD, TTL caching, overrides
│   ├── test_envvars.py            # Three-layer environment variable management
│   ├── test_analytics_export.py   # Analytics aggregation and CSV export formatting
│   ├── test_lead_lifecycle.py     # Lead state transitions, notes, follow-up scheduling
│   ├── test_lead_quality.py       # Lead scoring formula, priority levels, Hot/Warm/Cold thresholds
│   ├── test_post_normalization.py # Multi-platform post and comment normalization
│   ├── test_http_endpoints.py     # Complete HTTP/API endpoint status and validation (76 tests)
│   ├── test_log_parser.py         # Structured log parser and secret redaction (117 tests)
│   └── test_notifications.py      # In-app and email notification event dispatch
├── scratch/                       # Diagnostic scripts and integration verifications (gitignored)
├── logs/                          # Rotating application logs (app.log, 10MB × 5, gitignored)
├── .env.example                   # Production environment template with documentation
├── .gitignore                     # Git exclusions (.env, logs/, pycache, node_modules)
├── docker-compose.yml             # Local containerized setup (FastAPI app + MongoDB 7.0)
├── Dockerfile                     # Production container image (python:3.11-slim)
├── requirements.txt               # Locked production dependencies
└── README.md                      # Comprehensive system documentation
```

---

## 6. End-to-End Workflow & Customer Journey

LeadAI seamlessly integrates four distinct user surfaces into one automated, end-to-end social lead pipeline.

### System Architecture & Journey Flowchart

```text
                     PUBLIC WEBSITE (/website)
                               │
            [Stage 1-2] Visitor Requests Demo
                               │
                               ▼
                   POST /api/auth/signup
                               │
                    (Persisted in DB: demo_requests)
                               │
                               ▼
                    SUPER ADMIN PORTAL (/superadmin)
                               │
        [Stage 3-6] Super Admin Reviews & Approves Demo
                               │
         ┌─────────────────────┴──────────────────────┐
         ▼                                            ▼
 Organization Created (status: "demo")      User Account Created
 500 Demo Tokens Granted (token_balances)   Welcome Email Dispatched
         │                                            │
         └─────────────────────┬──────────────────────┘
                               │
                               ▼
                     USER PORTAL (/dashboard)
                               │
      [Stage 7-10] User Signs In & Configures Search:
        • Selects Domain from Catalog (22 industries)
        • Generates/Selects Keyword Presets
        • Inputs Target Social URL (FB, IG, YT, LI)
                               │
                               ▼
                     POST /api/url/search
                               │
    [Stage 11-14] URL Detector & Platform Canonicalizer:
        • Normalizes URL, strips tracking tokens
        • Detects platform (Facebook / Instagram / YouTube / LinkedIn)
        • Spawns async background task in search_history
                               │
                               ▼
                   APIFY SCRAPER ENGINE
                               │
    [Stage 15-19] Platform-Specific Actor Execution:
        • Page Details Actor (name, about, followers, verified)
        • Posts Scraper (fetches latest posts; flags is_relevant & is_qualifying)
        • Comments Scraper (collects comments for qualifying posts ≥ MIN_COMMENTS)
                               │
                               ▼
                   LEADAI QUALIFICATION ENGINE
                               │
    [Stage 20-24] Multi-Stage Analysis & Scoring:
        • Noise / Emoji / Gratitude Filter (drops low-value chatter)
        • Contact Info Extractor (regex for phone, WhatsApp, email, city)
        • Dual-Stage Classifier (Gemini AI with automatic offline rule fallback)
        • Deterministic Scoring Formula (0–100 score; Hot/Warm/Cold; Priority)
        • Persists Leads in ai_comments (scoped by organization_id)
                               │
         ┌─────────────────────┴──────────────────────┐
         │                                            │
         ▼                                            ▼
   USER PORTAL LEAD VIEW                   ORG ADMIN PORTAL (/org-admin)
   [Stage 25] Live Progress Updates        [Stage 26] Org-wide Pipeline Overview
   Interactive Lead Dossier Modal          Team Lead Assignment
   Notes, Follow-ups, Status Changes       Keyword Library & Business Profile
   Scoped CSV Data Exports                 Scraped Data View (/data/{kind})
         │                                            │
         └─────────────────────┬──────────────────────┘
                               │
      [Stage 27-28] Customer Requests Subscription Upgrade:
        • Plan Checkout via Stripe / Mock Provider
        • Verified Webhook (HMAC-SHA256) moves sub to pending_confirmation
        • Super Admin confirms payment in Super Admin Portal
        • Organization activated (status: "active"); Team invitations unlocked
```

### The 28-Stage Customer Journey

1. **Visitor Lands on Public Marketing Website (`/website`)**: The visitor browses platform capabilities, reviews dynamic pricing plans fetched from the database, inspects interactive FAQ items, and explores supported industries.
2. **Demo Request Submission**: The visitor clicks "Request Demo" and completes the signup modal with name, business email, company name, phone, and target industry.
3. **Backend Intake (`POST /api/auth/signup`)**: The backend validates fields, rate-limits submissions per IP, creates a record in `demo_requests` (`status: "pending"`), and logs an audit event.
4. **Super Admin Notification**: An in-app high-priority notification (`demo_requested`) is dispatched to all Super Admins.
5. **Super Admin Queue Inspection**: The Super Admin logs into `/superadmin` (using `SUPERADMIN_EMAIL` / `SUPERADMIN_PASSWORD`), navigates to the Demo Queue (`#demo`), and inspects applicant credentials.
6. **Demo Approval (`POST /api/super-admin/demo-requests/{id}/approve`)**: The Super Admin approves the request with configured tokens (default 500), duration (7 days), and feature allowances.
7. **Automated Tenant Provisioning**: The backend atomically creates a new `organizations` document (`status: "demo"`), creates the primary `users` document, provisions the `organization_members` ownership record, credits 500 tokens in `token_balances`, and transitions the demo request status to `approved`.
8. **User Logs Into User Portal (`/dashboard`)**: The customer accesses `/login`, enters their credentials, receives a signed session cookie, and enters the User Portal workspace.
9. **Guided Domain Taxonomy Selection**: The user opens the guided search catalog (`GET /api/business-context/catalog`) and selects their industry from 22 built-in sectors (e.g., Real Estate, Healthcare, SaaS, Education, Automobiles).
10. **Keyword Configuration & Search Presets**: The user generates recommended keywords or types custom terms. They save their configuration as a tenant-scoped preset via `POST /api/search-presets` for one-click re-use.
11. **URL Submission**: The user pastes a social media link (e.g., `https://www.facebook.com/premierproperties`, Instagram profile, YouTube channel, or LinkedIn company page), specifies max posts (default 20) and comments per post (default 20), and clicks "Analyze Profile".
12. **URL Validation & Canonicalization**: `detect_social_url()` (`app/social/url_detector.py`) validates the URL structure, strips tracking parameters (`fbclid`, `igshid`, `utm_*`), and normalizes trailing slashes. Unsupported platforms or malformed URLs raise a user-friendly `UrlError`.
13. **Platform Auto-Detection**: The backend maps the domain to its normalized platform (`facebook`, `instagram`, `youtube`, or `linkedin`). This platform tag propagates through storage, APIs, and exports.
14. **Background Task Initialization**: `POST /api/url/search` generates a unique `run_id` (prefixed `URL`), inserts a `search_history` document (`status: "running"`, `phase: "queued"`), registers an asynchronous background task, and returns immediately so the UI remains completely responsive.
15. **Apify Actor Selection**: `app/social/scrapers.py` instantiates the designated scraper (`FacebookScraper`, `InstagramScraper`, `YouTubeScraper`, `LinkedInScraper`), verifying the `APIFY_API_TOKEN` and reading active actor IDs from admin settings.
16. **Page Details Extraction**: The actor scrapes profile metadata (name, bio, follower count, verified status, contact details). If the profile details actor is temporarily blocked, the pipeline engages a graceful fallback (`_url_derived_page`) using the canonical URL handle so post/comment scraping continues unimpeded.
17. **Post Ingestion & Qualification**: The actor collects the latest posts into `facebook_posts`. Each post is evaluated: it is flagged `is_relevant` if its text relates to the business context, and `is_qualifying` if its platform-reported total comments satisfy `MIN_COMMENTS` (default 10).
18. **Comment Collection**: Qualifying posts trigger the comment scraper actor. The number of comments collected is bounded by the user's "Comments / Post" setting and platform rate limits. Low-engagement posts are skipped to preserve Apify credits.
19. **Comment Ingestion & Deduplication**: Raw comments are ingested into `facebook_comments`, deduplicated by comment ID and URL, and stamped with tenant ownership (`organization_id`, `user_id`).
20. **Comment Filtering & Pre-Screening**: Comments pass through `evaluate_rule()` (`app/pipeline/comment_filter.py`). Short greetings, emojis, spam, and non-actionable chatter are dropped.
21. **Contact & Signal Extraction**: The regex extraction engine identifies 10-digit Indian phone numbers, international phone formats, WhatsApp numbers, email addresses, price/budget figures, location names, and urgency indicators.
22. **AI & Rule Classification**: Cleaned comments are sent to the classification engine (`app/pipeline/comment_ai.py`). When the Google Gemini API key is active, Gemini classifies the buyer/seller intent, budget, urgency, and requirement. If the Gemini API is rate-limited (HTTP 429) or unavailable, the built-in circuit breaker immediately falls back to Stage 1 regex & rule classification without failing the search run.
23. **Deterministic Lead Scoring**: Every lead receives a transparent, deterministic score (0–100) based on confidence, contact info availability, buying intent, budget, and urgency. Leads are assigned quality tiers: **Hot (≥80)**, **Warm (50–79)**, or **Cold (<50)**.
24. **Persistence in `ai_comments`**: Qualified leads are saved to `ai_comments` with complete context (comment text, post caption, page link, contact details, AI rationale, score, and timestamps).
25. **Live User Portal Experience**: The user's dashboard progress bar updates in real time via polling (`GET /api/search/{run_id}`). When complete, the user drills from Page → Posts → Comments, filters by "Contact Ready" or "Hot Leads", opens the full Lead Dossier modal, adds notes, schedules follow-ups, and downloads scoped CSV reports.
26. **Org Admin Oversight & Pipeline Assignment**: The organization administrator logs into `/org-admin`, views organization-wide search metrics, opens the Lead Pipeline (`#leads`), and reassigns leads across team members.
27. **Subscription Checkout & Payment**: When trial tokens deplete, the customer clicks "Billing", selects a plan (e.g., Professional), and completes checkout via Stripe (or Mock Provider in development). The verified webhook verifies HMAC signatures and transitions the subscription to `pending_confirmation`.
28. **Super Admin Confirmation & Workspace Scaling**: The Super Admin reviews the payment in `/superadmin`, confirms the transaction, automatically activates the organization (`status: "active"`), credits monthly plan tokens, and unlocks the Org Admin's ability to invite team members via hashed email invitations.

---

## 7. UI Pages & Panels

### Portal Access & URL Disambiguation Matrix

| Portal | Canonical URL Route | Auth Scope | Access Role Required | Primary Capabilities | API Route Prefix |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **User Portal** | `/` or `/dashboard` | `scope="site"` | Organization members, admins, owners | Social URL search, lead cards & dossier, CSV exports, team & billing modals | `/api/*`, `/api/me/*` |
| **Organization Admin** | `/org-admin` | `scope="site"` | `owner` or `admin` of confirmed org | Tenant overview, team management, business profile, keyword library, lead rules, org analytics, exports, audit | `/api/org-admin/*` |
| **Platform Console** | `/admin` | `scope="admin"` | Platform staff (`admin`, `super_admin`, `viewer`, `support`) | Platform operations, Apify jobs, actors, global lead inventory, platform analytics, system settings | `/api/admin/*` |
| **Super Admin Portal** | `/superadmin` | `scope="admin"` | `super_admin` only | Global governance: organizations, subscriptions, demo queue, token allocations, industries, impersonation | `/api/super-admin/*` |
| **Public Website** | `/website` | Public (No auth) | Anyone / Prospective customers | Marketing landing, dynamic CMS pricing & FAQ, features, contact form, demo request | `/api/public/*` |

> **Graceful Redirect Rule**: If an authenticated organization owner or admin enters `/admin`, the server automatically redirects them via HTTP 303 to `/org-admin`. Non-admin members are redirected to `/dashboard`. Platform staff entering `/admin` are granted immediate access.

---

### 7A. Login Page (`/login` — `login.html`)

**Two-scope login** (site vs admin):

| Element | Description |
| --- | --- |
| **Logo badge** | Amber gradient square with ✦ icon, brand name "LeadAI", tagline "AI Lead Intelligence" |
| **Heading** | "Welcome Back" (site) / "Admin Portal" (admin) — driven by `AppConfig` |
| **Email field** | Input with email icon, live validation (green checkmark on valid format), placeholder "admin@gmail.com" (site) / "admin123@gmail.com" (admin) |
| **Password field** | Input with lock icon, **eye button** to toggle visibility (show/hide password) |
| **Remember Me** | Checkbox (amber gradient when checked), persists email in localStorage |
| **Forgot Password** | Link → navigates to `/forgot-password` (served by `reset-password.html`) with self-service token reset |
| **Error banner** | Red banner with shake animation, shows validation/login errors |
| **Sign In button** | Amber gradient, arrow icon, loading spinner state, disabled during submission |
| **Footer** | "Protected by secure authentication · LeadAI © 2026" |
| **Ambient background** | 3 floating orbs (amber, violet, teal) + grid overlay, same as main site |

**Behavior**: POSTs to `/api/auth/login` with `{email, password, scope: "site"|"admin"}`. On success, redirects to `/` (site) or `/admin` (admin). 401 on failure shows error. Brute-force throttle with lockout indicator.

### 7B. Main Dashboard (`/` — `index.html` + `app.js`)

Single-page application with **4 view screens** navigated via breadcrumb, plus **3 SaaS modals**:

#### View 1: Search Screen

| Element | Description |
| --- | --- |
| **URL Search Input** | Text input for social media URL, hint text "Waiting for URL…" → "Ready to analyze — platform auto-detected" |
| **Industry & Domain Context** | Auto-inherited from the organization's business profile (`app/pipeline/business_context.py`), dynamically driving keyword suggestions and AI qualification rules |
| **Search Presets Dropdown** | Quick-select tenant-scoped search presets (`GET /api/search-presets`). Includes "Save Current as Preset" modal and preset deletion |
| **Max Posts input** | Number input (default 20, range 1–100) |
| **Comments Per Post input** | Number input (default 30, range 1–500), synced with comments view |
| **Filter Mode** | Radio buttons: All (no filter), Preset (select from categories/rules), Custom (manual keywords) |
| **Preset selector** | Dropdown with category presets, industry keywords, and active organization rules |
| **Custom keywords** | Include keywords text input, Exclude keywords text input, Category checkboxes, Match mode dropdown (any/all) |
| **Analyze Profile button** | Amber gradient, triggers URL search, shows loading state "Analyzing Profile…" |
| **Cancel button** | Appears during search, cancels in-flight Apify run cleanly at next checkpoint |
| **Token Balance Badge** | Header counter displaying remaining allocated demo/subscription tokens with real-time decrements |
| **Platform chip** | Shows detected platform icon + name (Facebook / Instagram / YouTube / LinkedIn), canonical URL, running status |
| **Progress bar** | Determinate progress (5% → 20% → 55% → 75% → 100%) with phase label |
| **Analysis steps checklist** | 5 steps: URL validated, Platform detected, Page details, Posts, Comments — each gets done/active/error state |
| **Status badge** | Top-right badge: Idle / URL search / Viewing pages / Collecting posts / etc. |
| **Recent Searches section** | List of past searches with: icon, URL, platform label, page count, status badge (✓ Completed / ◌ Processing / ! Failed / – Cancelled), relative time, delete button (✕), "Open →" link |
| **Session stats strip** | Shows total searches, completed count, total pages |
| **Filter chips** | All / Facebook / Instagram / YouTube / LinkedIn |
| **View All button** | Toggles between showing 8 or 100 recent searches |

#### View 2: Pages Screen

| Element | Description |
| --- | --- |
| **Summary line** | "Found X page(s)/channel(s) · Y with qualifying high-intent posts" |
| **Category filter** | Dropdown populated from page categories |
| **Contact only checkbox** | Filters to pages with phone/email |
| **Export Pages CSV button** | Downloads pages as CSV |
| **Page cards** | Each card shows: |
| — Avatar | Profile picture or fallback initial |
| — Page name | With platform badge (Facebook/Instagram/YouTube/LinkedIn) and verified badge |
| — Platform link | "Open on Facebook ↗" (external link) |
| — About snippet | First 160 chars of about text |
| — Stats row | Followers/Subs, Likes, Posts Found, Qualifying Posts, Total Comments |
| — Contact chips | 📞 Phone (tel: link), ✉️ Email (mailto: link), 💬 WhatsApp (wa.me link), 🌐 Website (external link), 📍 Address, 🏷️ Category |
| — Action button | "🔍 Analyze Posts" / "📝 View X Posts (Y qualifying)" / "↻ Re-analyze Posts" / spinner "Analyzing posts…" |
| — Error row | If post collection failed |

#### View 3: Posts Screen

| Element | Description |
| --- | --- |
| **Page name header** | Shows which page's posts are displayed |
| **Summary line** | Total Posts, Relevant, Qualifying, Total Comments on Qualifying, Latest Post Date, qualifying threshold note |
| **Export Posts CSV button** | Downloads posts as CSV |
| **Post tiles** | Each tile shows: |
| — Thumbnail | Post image or video icon or text fallback |
| — Caption | Full post text (never truncated) |
| — Date | Published date |
| — Badges | ✓ Relevant / ✗ Low relevance, ★ Qualifying (≥ N comments) / <N comments |
| — External link | "View original post ↗" |
| — Stats row | Likes, Total Comments, Scraped & Analyzed, Shares |
| — Action button | "💬 Collect Comments (N available)" / "💬 View N Comments & Leads" / spinner "Collecting…" |
| — Error row | If comment collection failed |

#### View 4: Comments / Leads Screen

| Element | Description |
| --- | --- |
| **Post name header** | Shows which post's comments are displayed |
| **Filter pills** | All, Leads, Contact, Hot, Pricing, Inquiry — each with count badge |
| **Search input** | Debounced text search with clear button |
| **Quality filter** | Dropdown (hot/warm/cold) |
| **Sort by** | Dropdown (score/date/etc.) |
| **Comments Per Post** | Number input synced with search view |
| **Progress bar** | Determinate/indeterminate while comments are being scraped |
| **Summary line** | Active filter description, search match note, count of displayed/total |
| **Lead cards** | Each card shows: |
| — Avatar | First letter of commenter name |
| — Commenter name | With platform badge and "📞 Contact Ready" badge (if has contact) |
| — Date + platform link | "View on Facebook ↗" |
| — Score pill | 0–100 score with tooltip |
| — Priority badge | HIGH (red) / MEDIUM (yellow) / LOW (gray) |
| — Comment text | Full comment text |
| — AI Intelligence | 🤖 AI rationale/reason |
| — Contact chips | 📞 Phone, ✉️ Email, 💬 WhatsApp, 💰 Budget, 📋 Requirement, 📍 Location, 🎯 Intent, 💭 Sentiment |
| — "🔍 View Full Dossier" button | Opens lead detail modal |
| **Export Leads CSV button** | Downloads analyzed comments as CSV |

#### Lead Detail Modal

Full dossier popup with:

| Section | Content |
| --- | --- |
| **Title** | "Lead Intelligence Dossier — [Commenter Name]" |
| **Original Comment** | Full comment text with "Open original comment on [platform] ↗" link |
| **AI Rationale** | 🤖 AI Intelligence text + analyzed_by (rules/gemini) |
| **Contact Info** | Phone (tel: link), WhatsApp (wa.me link), Email (mailto: link), Website (external link) |
| **Buyer Signals** | Budget, Requirement/Inquiry, Location/City, Buying Intent badge, Urgency |
| **Platform & Quality** | Platform icon + name, Priority Level (🔴 High / 🟡 Medium / ⚪ Low), Lead Quality (🔥 Hot / ⚡ Warm / ❄️ Cold), Confidence Score (0–100%), Lead Score (0–100, large amber display) |
| **Lead Status** | Current status badge (colored), dropdown for valid transitions, "Update" button |
| **Notes** | List of existing notes (author, time, text), input + "Add" button |
| **Follow-ups** | List of follow-ups (title, status badge, due date, notes), input fields (title, datetime) + "Add" button |
| **Status History** | Timeline of status changes (from → to, time, changed_by, reason) |
| **Source Context** | Page name + link, Post caption + link, Commenter profile link |

#### SaaS Modals (Header Buttons)

| Modal | Trigger | Content |
| --- | --- | --- |
| **Workspace Settings** | 🏢 Workspace pill button | Workspace name, company website, timezone, currency, custom branding (primary/accent colors, brand display name) |
| **Team Management** | 👥 Team button | Invite form (email + role), active members table, pending invitations, role change/remove actions |
| **Billing & Subscriptions** | 💳 Billing button | Active subscription banner, monthly usage quota meters (searches/AI/team), available plans grid, invoices table |
| **Quota Exceeded** | Auto-triggered on limit | Warning modal with upgrade CTA |

### 7C. URL Report Page (`/static/url_report.html`)

Standalone report page opened automatically after URL search completion. Shows the full results of a search run with page details, post summary, and comment/lead data. Accessible via `/static/url_report.html?run_id=<URL-run-id>`.

### 7D. Maintenance Page (`/static/maintenance.html`)

Shown when maintenance mode is active. Displays configurable notice message from `maintenance.message` setting.

---

## 8. Admin Control Center (Platform Console)

Full admin SPA at `/admin` (`admin.html` + `admin.js`, 4725+ lines) with **sidebar navigation** organized into sections:

### Main Section

| View | Description |
| --- | ---|
| **Dashboard** | Hero KPI (leads, searches, running, success rate), date range selector (7d/30d/90d/custom), KPI grid with sparklines, activity chart (dual area: leads gold + searches muted), leads by platform bars, alerts, system status, keyword filter summary, recent jobs table. Configurable widget visibility/ordering via Global Settings. |
| **Jobs** | Paginated table of all search runs. Filter by status (Running/Completed/Failed/Cancelled/Queued), platform, query, date range. Actions: Cancel (running), Retry (completed/failed), Delete (super_admin). Click opens job drawer with full report. |
| **Failed Jobs** | Hero with total failed count, today/this week, most common error. Table with retry button per row and "Retry All" bulk action. |
| **Leads** | Hero with total leads, contact count, avg score, per-platform counts. Paginated table with filters (platform, quality, status, text search). Bulk actions: set status, delete. Click opens lead detail modal. |
| **Analytics** | Date range selector (Today/7d/14d/30d/90d/custom). Charts: jobs per day (vbar), leads per day (area), job statuses, leads by platform, quality distribution, score distribution. Platform performance table. Top pages by leads. Lead pipeline status. |
| **Pages** | Paginated table of all collected pages. Filters: platform, name/category/city, contact info. Click opens page detail modal. |
| **Posts** | Paginated table of all collected posts. Filters: platform, caption/page. Click opens post detail modal. |

### Platforms & Data Sources Section

| View | Description |
| --- | --- |
| **Platforms** | Cards per platform (Facebook/Instagram/YouTube/LinkedIn) with enable/disable toggle, stats, actor management (view/edit/test/save actor IDs). |
| **Platform Detail** | Detailed view of one platform: stats, actor list, recent runs. |
| **Apify** | Connection status, token management (masked), live test button, usage this month with cost-by-actor breakdown. |
| **Actors** | Grid of all Apify actor cards with key, actor ID, platform badge, override indicator, test button, link to Apify. |
| **Usage & Cost** | 30-day usage aggregation: total cost, runs with usage, cost-by-actor bars and table. |
| **Environment** | Three-layer env var management: lock/unlock with password, grouped variables with source/override/secret badges, inline edit, reset to .env, guard password change. |

### AI & Lead Engine Section

| View | Description |
| --- | --- |
| **AI / Gemini** | Status (model, API key, analyzed count), settings (enable/disable AI, rule fallback, max calls/job, temperature), live test panel with sample comment analysis. |
| **Lead Scoring** | Three groups: Weights (confidence, priority, quality, phone/email, spam penalty), Signals (phone, email, budget, urgency, location, buying intent), Thresholds (hot_min, warm_min). Derive quality toggle. All auto-saved on input. |
| **Comment Intelligence** | Pipeline stats (total, analyzed, leads, contacts, high value, avg confidence), intent distribution bars. Detection settings toggles (phone, email, budget, location, urgency, buying/selling intent, emoji-only, spam, low value). Feed of recent analyzed comments with filters. |
| **Keyword Rules** | Tabbed view: Rules (table with activate/deactivate/edit/delete/reapply), Filtered Comments (status pills: All/Matched/Not Matched/No Filter, search), Top Keywords (bar chart). Rule editor modal: name, description, match mode (any/all/category/advanced), platform, category checkboxes, include/exclude keywords, keyword groups, detect contacts toggle. |

### Operations Section

| View | Description |
| --- | --- |
| **Limits** | Scrape limits (min_comments, max_posts_default/cap, comments_per_post_default/cap, global_max_comments) with auto-save. Cost protection toggles (stop_on_limit, warn_before_expensive). |
| **Database** | MongoDB connection stats (dbStats), collection table with document counts, sizes, indexes. |
| **Logs** | Structured log viewer with summary cards (level counts), search, level/source/module filters, severity badges, source badges, row-click details modal (all fields + stack trace + raw), copy JSON/raw/message, auto-refresh (10/30/60s), autoscroll with "Jump to latest", pagination, CSV export. |
| **Health** | Live health checks of every subsystem (database, Apify, Gemini, maintenance). Status dots with latency and error details. |
| **Exports** | CSV download buttons for: Jobs, Pages, Posts, Leads, Follow-ups, Application log. |

### Administration Section

| View | Description |
| --- | --- |
| **Users** | Table of admin accounts with role badges. Create/Edit/Delete users (super_admin only). Roles: viewer, manager, super_admin. Password change. |
| **Security** | Session timeout (hours), login protection toggle, audit logging toggle. Revoke all sessions (super_admin). Change own password. |
| **Features** | Feature switches: URL search enabled, exports enabled. Platform toggles (Facebook/Instagram/LinkedIn/YouTube). |
| **Maintenance** | Toggle maintenance mode, edit maintenance notice message. |
| **Audit Log** | Chronological feed of admin actions with user, category, action, timestamp, details. Immutable. |
| **Global Settings** | Tabbed settings editor with search. Tabs: General, Appearance, Branding (with live preview), Security, Localization, Features, AI, Defaults, System & History. Each setting has type-appropriate input (toggle, text, number, select, color picker, JSON textarea, file upload). Dirty tracking, save bar, version badge. System tab: version history with view/restore, export/import JSON, danger zone reset all. |

### SaaS & Billing Section

| View | Description |
| --- | --- |
| **Plans Catalog** | Manage subscription plans (Free/Starter/Pro/Business/Enterprise): name, price, features, limits, enable/disable. |
| **Subscriptions** | Tenant subscriptions: status, trial countdown, plan details, cancellation scheduling. |
| **Invoices** | Billing history: invoice number, date, amount, status. |

### CMS Section

| View | Description |
| --- | --- |
| **Pages** | CMS page management: list, create, edit, draft/publish, version history, restore. |
| **FAQ** | FAQ management: questions, answers, ordering, categories. |
| **Testimonials** | Customer testimonials: content, author, status. |
| **Navigation** | Header/footer nav items management. |
| **Media** | File upload and management. |
| **Contact Submissions** | Contact form submissions: list, mark read. |

### Shell Features

- **Collapsible sidebar** (persisted in localStorage, Ctrl+B shortcut)
- **Profile dropdown** (user name, avatar initial, logout)
- **Global search** (searches across jobs/leads/pages/posts/comments/platforms)
- **Bell notifications** (live alerts from `/api/admin/alerts`)
- **Health pill** (polls `/api/admin/health`)
- **Breadcrumb navigation**
- **Hash-based routing** (each view has a URL hash)

---

## 9. Organization Admin Portal

**URL**: `/org-admin` (or `/admin` when signed in as an organization administrator) — HTML shell `app/static/org-admin.html` + SPA `app/static/org-admin.js` (2,630 lines) + `app/static/org-admin.css` + API `app/api/routes/org_admin.py` (2,206 lines).

A **per-organization** admin portal for owners and admins of an organization whose subscription or demo has been confirmed (`admin_portal_enabled` or `status in {"active", "demo"}`). Unlike the platform console, every single query here is scoped strictly to **the caller's own organization** (`ctx.organization_id`) — IDs belonging to other organizations answer 404 and are logged as out-of-scope access attempts.

### Access Rules

| Rule | Detail |
| --- | --- |
| **Who** | Org `owner` / `admin` only (members, managers, viewers → 403) |
| **Gate** | Organization must have active portal access; suspended/cancelled orgs → `403 {code: "admin_portal_disabled"}` |
| **Scoping** | Every query ANDs `organization_id = ctx.organization_id` via `scope_query()` — never trusts an ID from the request |
| **Auditing** | Every mutation and export writes an immutable `audit_logs` entry with caller, IP, and timestamp |
| **Reuses** | Profile/team (`/api/organizations/current*`), lead detail (`/api/leads/*`), search (`/api/url/search`), billing (`/api/billing/*`), notifications (`/api/notifications`), auth (`/api/auth/*`) |

### Portal Sections (sidebar navigation)

| Section | Description |
| --- | --- |
| **Dashboard / Overview** | Real-time KPIs (users, searches, leads, usage, tokens, subscription), alerts (quota, token, demo, subscription warnings), Business Summary Card, recent searches, failed searches, recent leads, recent members, activity feed |
| **Business Profile** | Industry selector (22 built-in sectors), custom business type, business description, primary offerings, target customer types, custom requirement terms (`GET/PUT /api/org-admin/business-profile`) |
| **Keyword Library** | Interactive keyword management: active lead keywords, excluded terms, AI keyword recommendations (`POST /api/org-admin/recommend-keywords`), and live rule testing against text snippets (`GET/PUT /api/org-admin/keyword-library`) |
| **Team / Users** | Paginated member list with search, filter (role/status), sort (name/email/role/status/last_login/searches/leads/joined), per-member usage stats. Detail view: own searches, leads, activity, token consumption, permissions. Actions: reset access (one-time email link), bulk suspend/restore/deactivate |
| **Invitations** | List invitations by status (pending/accepted/cancelled/expired/all), email search, revoke invitation, seat cap enforcement |
| **Roles & Permissions** | Configurable org roles with delegable permission catalog (Searches, Leads, Exports, Team, Organization groups), defaults vs overrides per role, shared-workspace flag |
| **Searches** | All org search runs with filters (query, user, platform, status, date range), sort (newest/oldest/results/status), lead counts per run. Detail: page/post/comment/lead counts, filter config, cancel state |
| **Leads / Pipeline** | All org leads with filters (status, priority, platform, quality, assignee, owner, score range, date, run_id), sort. Pipeline view: lifecycle counts + owners. Bulk actions: assign / status / priority (one audit entry) |
| **Data (Pages/Posts/Comments)** | Org-wide scraped data views (`/data/pages`, `/data/posts`, `/data/comments`) with platform filtering, user filtering, search, and CSV export links |
| **Apify Activity** | View-only Apify summary, jobs, runs, and actor usage (`/apify/summary`, `/apify/jobs`, `/apify/runs`) |
| **Analytics** | Comprehensive charting data for custom date ranges (`/analytics`): leads over time, platform performance, conversion ratios |
| **Billing History** | Subscriptions, payment invoices, and quota usage records (`/billing/history`) |
| **Exports** | Export history (`/exports`), CSV downloads: users, activity, usage, leads, searches, posts, comments (`/exports/{kind}.csv`), client-side CSV logging (`/exports/client-log`) |
| **Audit Logs** | Org-scoped audit trail with facets, actor filters, and CSV export (`/audit-logs`, `/audit-logs/facets`, `/audit-logs.csv`) |
| **Support Tickets** | Create/list tickets, per-ticket messages, status transitions (open → in_progress → waiting → resolved → closed). Categories: question, bug, billing, feature, account, other. Priorities: low, normal, high, urgent |
| **Profile & Security** | Own name + notification preferences (GET/PATCH `/profile`), password change |

### Permission Model

Permissions are resolved from the role (owner/admin/manager/member/viewer) plus optional per-role overrides and per-member `permissions_override`, checked server-side via `require_portal(perm)`. Delegable permissions include:

| Group | Permissions |
| --- | --- |
| **Searches** | `search.create`, `search.view`, `search.cancel`, `search.export` |
| **Leads** | `leads.view`, `leads.manage`, `leads.assign`, `leads.export` |
| **Exports** | `exports.view`, `exports.create` |
| **Team** | `members.view`, `members.invite`, `members.update`, `members.suspend`, `members.delete`, `members.manage` |
| **Organization** | `workspace.view`, `settings.view`, `settings.manage`, `org_billing.view`, `org_audit.view`, `data.view_all` |

### API Endpoints (`/api/org-admin/`)

| Method | Endpoint | Purpose | Permission |
| --- | --- | --- | --- |
| GET | `/context` | Org, caller, caps, permission catalog | portal |
| GET | `/overview` | Dashboard metrics, alerts, recent activity | portal |
| GET | `/business-summary` | Overview card summary of active business profile & keyword library | `settings.view` |
| GET/PUT | `/business-profile` | Industry + offerings + customer types + requirement terms | `settings.manage` |
| GET/PUT | `/keyword-library` | Active keywords and excluded keywords management | `settings.manage` |
| POST | `/recommend-keywords` | AI-generated keyword suggestions for industry & business type | `settings.manage` |
| POST | `/lead-rules/test` | Test active lead rules and keyword matching against sample text | `settings.manage` |
| GET | `/data/{kind}` | Scraped social data browser: `pages`, `posts`, `comments` | `search.view` |
| GET | `/users` | Members + usage, search/filter/sort/paginate | `members.view` |
| GET | `/users/{user_id}` | Member detail: usage, searches, leads, activity | `members.view` |
| POST | `/users/{user_id}/reset-access` | One-time password reset link (email/display) | `members.update` |
| POST | `/members/bulk` | Suspend / restore / deactivate many (one audit) | `members.update` |
| GET | `/invitations` | Invitations by status | `members.invite` |
| GET | `/roles` | Configurable roles + delegable permission catalog | `members.view` |
| GET | `/searches` | All org searches (filters, sort, paginate) | `search.view` |
| GET | `/searches/{run_id}` | One run with result counts | `search.view` |
| GET | `/leads` | All / assigned / unassigned leads (filters) | `leads.view` |
| GET | `/leads/pipeline` | Lifecycle counts + owners | `leads.view` |
| POST | `/leads/bulk` | Assign / status / priority for many leads | `leads.manage` |
| GET | `/apify/summary`, `/apify/jobs`, `/apify/runs` | Apify activity (view only) | portal |
| GET | `/analytics` | Charts data for a date range | portal |
| GET | `/billing/history` | Subscriptions + payments | `org_billing.view` |
| GET | `/exports` | Export history | `exports.view` |
| POST | `/exports/client-log` | Record in-browser CSV export (audited) | portal |
| GET | `/exports/{kind}.csv` | CSV: users\|activity\|usage\|leads\|searches\|posts\|comments | `exports.create` |
| GET | `/audit-logs`, `/audit-logs/facets`, `/audit-logs.csv` | Org audit trail | `org_audit.view` |
| GET/POST | `/support/tickets` | List / create tickets | portal |
| GET | `/support/tickets/{id}` | Ticket detail | portal |
| POST | `/support/tickets/{id}/messages` | Add a message | portal |
| POST | `/support/tickets/{id}/status` | Transition status | portal |
| GET/PATCH | `/profile` | Own name + notification preferences | portal |

### Shell Features

- Org branding (logo, name, brand mark) dynamically rendered in sidebar
- Global search (users, leads, searches) with `/` keyboard shortcut
- Bell notifications (in-app, mark all read, audience-scoped)
- Light/dark theme toggle with CSS custom properties
- Profile dropdown (profile & security, user portal link, help, sign out)
- Hash-based SPA routing (`#dashboard`, `#team`, `#leads`, `#business-profile`, `#keyword-library`, …)
- "← Open User Portal" navigation link to `/dashboard`
- Support session (impersonation) banner when impersonated by a Super Admin

---

## 10. Super Admin Portal

**URL**: `/superadmin` — HTML shell `app/static/super-admin.html` + SPA `app/static/super-admin.js` (29 routes across 7 functional groups) + API split across three routers:

| Router | Prefix | Scope |
| --- | --- | --- |
| `app/api/routes/super_admin.py` | `/api/super-admin` | Global dashboard, organizations lifecycle, platform users, subscriptions, plan catalog CRUD, audit logs, impersonation |
| `app/api/routes/super_admin_platform.py` | `/api/super-admin/platform` | System health, integrations & secrets, platform analytics, security center, feature flags, global reports, support tooling |
| `app/api/routes/super_admin_lifecycle.py` | `/api/super-admin/lifecycle` | Demo queue & approvals, subscription queue, payment confirmation, token allocations, notification dispatch, role matrix, industry taxonomy |

The **global** platform super admin — the only identity with cross-organization authority. Authenticated exclusively via the environment credentials (`SUPERADMIN_EMAIL` / `SUPERADMIN_PASSWORD` in `.env`); no database account can hold or tamper with this role. Every single action is permanently recorded in the global immutable audit log.

### Access Rules

| Rule | Detail |
| --- | --- |
| **Who** | `SUPERADMIN_*` environment account only (`scope=admin` + `effective_role == super_admin`) |
| **Login** | `/login?superadmin=1` |
| **Scope** | All organizations, all users, all databases — every action applies globally (marked with the amber "GLOBAL" pill in the UI) |
| **Auditing** | Every mutation and status transition is recorded in `audit_logs` with actor email, client IP, and user agent |

### Portal Sections (sidebar navigation)

| Section | Description |
| --- | --- |
| **Dashboard** | Platform-wide metrics: total organizations by status, admin counts, user counts, pending demo requests, active subscriptions, MRR, global token balances, total searches, Apify runs, qualified leads, system error rate, and live subsystem health |
| **Organizations** | Comprehensive organization manager: list, search, drill-down detail, status lifecycle (`pending` → `demo` → `active`, `suspend`, `disable`, `archive`, `cancel`), soft-deletion. View members, searches, leads, token usage, active plan, and business profile |
| **Demo Queue** | Review pending demo signups (`/superadmin#/demo`). Approve demo requests with custom token grants (default 500), duration (default 7 days), and feature allowances (`POST /api/super-admin/demo-requests/{id}/approve`). Rejections record reasons and notify applicants |
| **Subscriptions** | Subscription queue: pending payments, active subscriptions, upgrades, plan overrides, and scheduled cancellations |
| **Payment Confirmation** | Review pending payments from Stripe or Mock Provider. Manually confirm received payments (`POST /api/super-admin/subscriptions/{id}/confirm`) to transition organization status to `active` and re-grant plan tokens |
| **Plans Catalog** | Create, edit, and toggle subscription plan tiers (Free, Starter, Pro, Business, Enterprise) with per-plan limits (searches/mo, comments/post, posts/search, team seats, exports enabled, AI enabled) |
| **Tokens Management** | Global token ledger: adjust token balances, issue promotional top-ups, inspect token consumption history, and set expiration dates |
| **Industries & Taxonomy** | Industry taxonomy catalog manager (`/superadmin#/industries`): view 22 built-in industries, create custom industry profiles, edit keywords, and designate the platform-wide default industry |
| **Platform Users** | Complete directory of tenant users and administrators: filter by status, reset passwords via one-time cryptographically signed links, suspend/reactivate users |
| **Website & CMS Editor** | Visual content management for public website: edit pages (`/website`, `/features`, `/pricing`, `/faq`, `/terms`, `/privacy`), manage FAQ questions/answers, testimonials, navigation links, and SEO metadata |
| **System Health** | Real-time health diagnostic center: tests MongoDB latency, Apify API connectivity, Google Gemini API status, and background maintenance task status |
| **Integrations** | Secrets & external provider configuration (Apify API Token, Gemini API Key & Model, SMTP Host/Port/User, Stripe Secret & Webhook Keys) |
| **Platform Analytics** | Global SaaS charts: revenue growth, search run volume across platforms, lead conversion ratios, and token burn rate |
| **Security Center** | Security event log (cross-tenant probes, brute-force attempts, session revocations), session termination controls |
| **Feature Flags** | Global feature toggles: URL search, exports, demo registration, AI analysis, and platform-specific scrapers |
| **Support Queue** | Cross-tenant support ticket helpdesk: view tickets, reply to customer inquiries, update ticket statuses (open → in_progress → resolved) |
| **Notifications** | Platform-wide notification broadcast and automated event alerts |
| **Audit Logs** | Global immutable audit trail across all organizations with filterable facets and CSV export |
| **Impersonation** | Support session impersonation (`POST /api/super-admin/impersonate` with mandatory reason; exit via `POST /api/super-admin/impersonate/exit`). Displays high-visibility amber banner while active |

### Key API Endpoints (`/api/super-admin/`)

| Method | Endpoint | Purpose | Router |
| --- | --- | --- | --- |
| GET | `/dashboard` | Platform-wide control-center KPIs and counts | `super_admin.py` |
| GET | `/organizations` | Paginated organization list with status & search filters | `super_admin.py` |
| GET | `/organizations/{id}` | Organization detail drill-down (members, usage, plan) | `super_admin.py` |
| PATCH | `/organizations/{id}` | Organization status lifecycle transition | `super_admin.py` |
| GET | `/demo-requests` | List demo requests by status (pending/approved/rejected) | `super_admin_lifecycle.py` |
| POST | `/demo-requests/{id}/approve` | Approve demo: creates org + user + grants 500 tokens | `super_admin_lifecycle.py` |
| POST | `/demo-requests/{id}/reject` | Reject demo request with reason | `super_admin_lifecycle.py` |
| POST | `/subscriptions/{id}/confirm` | Confirm payment and activate organization | `super_admin_lifecycle.py` |
| GET/POST/PUT | `/plans` | Subscription plan tiers CRUD | `super_admin.py` |
| GET | `/platform/health` | Live diagnostic health checks of DB, Apify, AI, Sweeper | `super_admin_platform.py` |
| GET | `/platform/integrations` | Integration credentials and connection statuses | `super_admin_platform.py` |
| GET | `/audit-logs` | Global immutable audit log feed | `super_admin.py` |
| POST | `/impersonate` | Begin support impersonation session | `super_admin.py` |
| POST | `/impersonate/exit` | Terminate support impersonation session | `super_admin.py` |

---

## 11. Public Website

### 11A. Marketing Website (`/website` — `website.html`)

Full public marketing website with sections served from a single HTML file:

| Section | Description |
| --- | --- |
| **Navigation** | Fixed top nav: logo, Features, How It Works, Pricing, FAQ, Contact links, Sign In + Start Free buttons, mobile hamburger menu |
| **Hero** | Gradient headline "Turn Social Conversations Into High-Intent Leads With AI", description, CTA buttons (Start Free, See How It Works), example lead card visual (score 94/100, intent, quality, contacts, urgency) |
| **Supported Platforms Strip** | Facebook, Instagram, YouTube, LinkedIn icons and labels |
| **Features Grid** | 9 feature cards: AI Lead Discovery, Intent Detection, Lead Scoring, Contact Extraction, Lead Lifecycle, Comment Filtering, Analytics, CSV Export, Team Collaboration |
| **How It Works** | 10-step flow: Enter URL → Detect Platform → Collect Data → Qualify Posts → Filter Comments → AI Analysis → Score Leads → Extract Contacts → Lead Intelligence → Sales Action |
| **Pricing** | Dynamic pricing cards fetched from `/api/public/pricing` (plans from DB), skeleton loading state |
| **FAQ** | Dynamic accordion fetched from `/api/public/faq` (FAQ items from DB), skeleton loading state |
| **CTA Banner** | "Start Discovering High-Intent Leads Today" with Start Free + Book a Demo buttons |
| **Footer** | Brand, Product links, Company links, Legal links, copyright (dynamic from settings), Sign In + Get Started buttons |

### 11B. Signup Page (`/signup` — `signup.html`)

Self-service account creation:

| Element | Description |
| --- | --- |
| **Form fields** | Name, Work Email, Company, Industry (optional, from the catalog), Phone (optional), Password (with strength meter), Message |
| **Password strength** | 4-level meter (Too short → Weak → Fair → Good → Strong) with color coding |
| **Terms consent** | Checkbox linking to /terms and /privacy |
| **Error/success banners** | Inline validation errors, success redirect to dashboard |
| **Alternate link** | "Already have an account? Sign in" → /login |

### 11C. Contact Page (`/contact` — `contact.html`)

Contact form with validation and rate limiting:

| Element | Description |
| --- | --- |
| **Info panel** | "Let's Talk" heading, email, response time, global service note |
| **Form fields** | Full Name, Email, Company, Message (with character counter 0/2000) |
| **Validation** | Required fields, email format, message min 10 chars |
| **Rate limiting** | 1 submission per IP per 60 seconds |
| **Success state** | Form replaced with confirmation message + back link |

### 11D. Design System

Shared across public pages:

| File | Purpose |
| --- | --- |
| `design/tokens.css` | CSS custom properties: colors, spacing, typography, borders, shadows, transitions |
| `design/components.css` | Reusable components: buttons, cards, forms, badges, tables, alerts, modals |
| `design/theme.js` | Dynamic theme application from `/api/public/theme` (brand colors, name, logo, favicon) |

---

## 12. User Self-Service API (Me Endpoints)

**Location**: `app/api/routes/me.py` (995 lines) — prefix `/api/me`.

The private, per-user side of the User Portal. Every endpoint ANDs `scope_query(..., force_own=True)` onto every query: **even an organization owner/admin only sees their own records** here — the org-wide view lives in the Org Admin portal (`/api/org-admin/*`).

### Endpoints

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/me/summary` | Dashboard in one call: own counts, recent searches/leads, alerts, org usage + search caps + token costs + blockers |
| GET | `/api/me/profile` | Own profile (email read-only) + notification preferences |
| PATCH | `/api/me/profile` | Update name / phone / notification preferences ONLY (mass-assignment protected) |
| GET | `/api/me/usage` | Own searches / leads / exports / tokens consumed + org meters |
| GET | `/api/me/usage/ledger` | Own token ledger entries (paginated) |
| GET | `/api/me/searches` | Own search history (search, filter by status/platform, sort, paginate) |
| GET | `/api/me/leads` | Own leads + leads assigned to me (`view=all\|mine\|assigned`, filters, paginate) |
| GET | `/api/me/leads.csv` | CSV of the same leads (same filters), quota-metered |
| GET | `/api/me/exports` | Own export history (scope filter, sort, paginate) |
| POST | `/api/me/exports/client-log` | Audit an in-browser table CSV download (rate-limited: 30/min) |

### Key Behaviors

- **Profile protection**: `PATCH /api/me/profile` validates against an allow-list model (`extra='forbid'`). Any attempt to set protected fields (email, role, organization, status, password…) → `400 FIELD_NOT_EDITABLE` + security event. Impersonation sessions are read-only.
- **Search blockers** (`/api/me/summary`, `/api/me/usage`): client-side pre-flight list of reasons a new search would be refused — `ORGANIZATION_INACTIVE`, `DEMO_EXPIRED`, `TOKENS_EXPIRED`, `TOKENS_EXHAUSTED`, `QUOTA_EXCEEDED`. The server enforces them regardless.
- **Search caps**: effective per-search caps = min(plan/demo cap, admin hard cap), returned alongside plan caps and token costs.
- **Lead CSV export**: metered exactly like every other export — `csv_export` feature flag + `monthly_exports` quota + `export` token cost + `exports` record + `export.csv` audit entry. Cells are formula-injection safe. Truncated responses carry `X-Export-Truncated` / `X-Export-Total` headers.
- **Notification preferences**: single source of truth shared with the Org Admin portal (`users.notification_preferences` = flat `{key: bool}`). Keys: `email_notifications` (master), `search_completed`, `lead_assigned`, `usage_warnings`. Security alerts are always delivered.

---

## 13. Notifications API

**Location**: `app/api/routes/notifications.py` (38 lines) — prefix `/api/notifications`.

In-app notifications for tenant users (User Portal and Org Admin Portal), backed by `app/events/notifications.py`.

### Endpoints

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/notifications` | Own notifications + (for org owners/admins) org notifications. Query: `unread`, `page`, `limit` |
| POST | `/api/notifications/read` | Mark one (`{id}`) or all as read |

### Notification Types & Audience

Notifications are delivered by `app/events/notifications.py` through several audience helpers:

| Audience | Who receives |
| --- | --- |
| `notify_user` | A single user (respecting their `users.notification_preferences`) |
| `notify_org_admins` | All owners/admins of an organization |
| `notify_super_admins` | All Super Admins (e.g. system errors, legacy secret warnings, demo decisions) |

Common notification types include: `search_completed`, `search_failed`, `job_completed`, `job_failed`, `leads_found`, `new_leads`, `lead_assigned`, `high_token_usage`, `tokens_low`, `demo_expiring`, `demo_expired`, `quota_warning`, `usage_threshold`, `security_event`, `system_error`. Severity levels: `info`, `warning`, `danger`.

Delivery channels: **in-app** (bell dropdown in User Portal / Org Admin Portal / Super Admin Portal) and **email** (when SMTP configured; otherwise invite/reset links are shown once to the admin who created them).

---

## 14. SaaS & Multi-Tenancy

### Multi-Tenant Architecture

| Component | Description | Location |
| --- | --- | --- |
| **Organizations** | Tenant entities with name, slug, status, plan, timezone, currency, settings | `app/db/saas_models.py` |
| **Memberships** | User-to-org relationships with roles (owner/admin/manager/member/viewer) | `app/db/saas_models.py` |
| **Invitations** | SHA-256 token-hashed, 7-day expiry, accept/revoke flow | `app/billing/invitations.py` |
| **Migration** | Idempotent backfill: Default Org + owner membership + `organization_id` on existing docs | `app/db/migration.py` |
| **Tenant Context** | Resolved from session, all customer data is org-scoped | `app/auth/tenant.py` |

### Subscription Plans

| Plan | Price | Searches | AI Analyses | Team Members | Key Features |
| --- | --- | --- | --- | --- | --- |
| **Free** | $0/mo | 50 | 500 | 1 | Basic features, all platforms |
| **Starter** | $49/mo | 200 | 2,000 | 5 | All platforms, CSV export |
| **Professional** | $149/mo | 500 | 5,000 | 10 | Priority support, advanced analytics |
| **Business** | $399/mo | 2,000 | 20,000 | 25 | Custom branding, API access |
| **Enterprise** | Custom | Unlimited | Unlimited | Unlimited | SLA, dedicated support, custom integrations |

### Usage & Quota System

| Metric | Counter Field | Enforcement |
| --- | --- | --- |
| `monthly_searches` | `searches_used` | Blocks search at limit |
| `monthly_posts` | `posts_used` | Informational |
| `monthly_comments` | `comments_used` | Informational |
| `monthly_ai_analyses` | `ai_used` | Blocks AI analysis at limit |
| `monthly_exports` | `exports_used` | Blocks CSV export at limit |

### API Routes

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/billing/plans` | Public plans catalog |
| GET | `/api/billing/subscription` | Tenant active subscription & trial |
| GET | `/api/billing/usage` | Real-time quota usage |
| POST | `/api/billing/checkout` | Plan upgrade checkout |
| POST | `/api/billing/cancel` | Schedule cancellation |
| GET | `/api/billing/invoices` | Invoice history |
| GET | `/api/organizations/current` | Workspace settings |
| PATCH | `/api/organizations/current` | Update workspace |
| GET | `/api/organizations/current/team` | Team members |
| POST | `/api/organizations/current/invitations` | Invite member |
| DELETE | `/api/organizations/current/invitations/{id}` | Revoke invite |
| PATCH | `/api/organizations/current/members/{id}` | Change role |
| DELETE | `/api/organizations/current/members/{id}` | Remove member |

---

## 15. CMS (Content Management)

### Managed Content Types

| Collection | CRUD | Draft/Publish | Version History | Description |
| --- | --- | --- | --- | --- |
| `cms_pages` | ✅ | ✅ | ✅ | Dynamic website pages with sections |
| `cms_faq` | ✅ | — | — | FAQ items with ordering |
| `cms_testimonials` | ✅ | ✅ | — | Customer testimonials |
| `cms_navigation` | ✅ | — | — | Header/footer nav items |
| `cms_settings` | ✅ | — | — | Branding, SEO, social links, footer |
| `cms_media` | ✅ | — | — | File uploads with metadata |
| `cms_contact` | ✅ | — | — | Contact form submissions |

### Admin CMS API Routes

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/admin/cms/pages` | List pages |
| POST | `/api/admin/cms/pages` | Create page |
| PUT | `/api/admin/cms/pages/{id}` | Update page |
| DELETE | `/api/admin/cms/pages/{id}` | Delete page |
| POST | `/api/admin/cms/pages/{id}/publish` | Publish draft |
| POST | `/api/admin/cms/pages/{id}/restore/{v}` | Restore version |
| GET | `/api/admin/cms/pages/{id}/history` | Version history |
| GET/POST/PUT/DELETE | `/api/admin/cms/faq` | FAQ CRUD |
| GET/POST/PUT/DELETE | `/api/admin/cms/testimonials` | Testimonial CRUD |
| GET/PUT | `/api/admin/cms/navigation` | Navigation management |
| GET/PUT | `/api/admin/cms/settings` | Website settings |
| GET/DELETE | `/api/admin/cms/media` | Media management |
| POST | `/api/admin/cms/media/upload` | Upload file |
| GET/PUT | `/api/admin/cms/contact` | Contact submissions |

### Public CMS API Routes

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/public/theme` | Public branding tokens |
| GET | `/api/public/config` | App config (name, colors, maintenance, features) |
| GET | `/api/public/pricing` | Plans catalog for pricing page |
| GET | `/api/public/faq` | Published FAQ items |
| GET | `/api/public/pages/{slug}` | Published page content |
| POST | `/api/public/contact` | Submit contact form (rate-limited) |

---

## 16. Lead Data Model & Database Inventory

Twenty-plus MongoDB collections, all documents stored from **real actor output only** (absent values are `None`/omitted, never fabricated). Pydantic models live in `app/db/models.py` (core) and `app/db/saas_models.py` (SaaS).

### `search_history` — one doc per agent run

| Field | Type | Description |
| --- | --- | --- |
| `run_id` | str | Unique run ID (URL runs prefixed `URL`) |
| `query`, `intent` | str/dict | Raw URL + parsed `{keyword, type: url, platform, canonical_url, limit}` |
| `platform` | str | Detected platform |
| `status` | str | `running` \| `completed` \| `partial` \| `error` \| `cancelled` |
| `phase`, `message` | str | Live progress (queued/page/posts/comments/completed) |
| `error`, `error_meta` | str/dict | Structured failure (ScrapeError payload) |
| `pages_found`, `pages_stored` | int | Counts |
| `scrape_info` | dict | Last Apify call metadata |
| `created_at`, `completed_at`, `updated_at` | datetime | Timestamps |

### `facebook_pages` — one doc per real page

| Field | Type | Description |
| --- | --- | --- |
| `page_id` | str | Platform page ID |
| `page_name` | str | Page name |
| `facebook_url` | str | Page URL (unique per run) |
| `platform` | str | `facebook` \| `instagram` \| `youtube` \| `linkedin` |
| `category`, `about` | str | Category and intro text |
| `followers`, `likes` | int | Follower/like counts |
| `verified` | bool | Verified badge |
| `phone`, `email`, `whatsapp`, `website` | str | Contact info |
| `address`, `city`, `state`, `country` | str | Location |
| `profile_picture`, `cover_image` | str | Photo URLs |
| `posts_status`, `posts_count`, `posts_error` | mixed | Post-collection progress |
| `total_posts_found`, `relevant_posts_count`, `qualifying_posts_count` | int | Computed post analytics |
| `total_comments_on_qualifying_posts`, `latest_post_date` | mixed | Computed |
| `has_qualifying_posts`, `activity_status`, `lead_score` | mixed | Computed |
| `created_at`, `updated_at` | datetime | Timestamps |

### `facebook_posts` — one doc per post

| Field | Type | Description |
| --- | --- | --- |
| `post_id`, `post_url` | str | Post identity |
| `page_id`, `page_name`, `page_ref` | str | Parent page |
| `platform` | str | Platform of parent page |
| `caption` | str | Full post text |
| `images`, `videos`, `external_links` | list[str] | Media and links |
| `published_date` | str | Post date |
| `likes_count`, `shares_count` | int | Engagement |
| `total_comment_count` | int | Platform-reported total (never overwritten) |
| `scraped_comment_count` | int | Comments actually collected |
| `is_relevant`, `is_qualifying` | bool | Qualification flags |
| `comments_status`, `comments_error` | str/None | Comment-collection progress |
| `search_run_id`, `provider` | str | Provenance |

### `facebook_comments` — one doc per comment

| Field | Type | Description |
| --- | --- | --- |
| `comment_id`, `comment_url` | str | Comment identity |
| `author_name`, `author_profile_url` | str | Commenter info |
| `text` | str | Comment text |
| `published_date`, `reactions_count` | str/int | Metadata |
| `has_contact` | bool | Phone or email present |
| `post_id`, `post_url`, `page_id`, `platform` | str | Parent context |
| `keyword_filter_status` | str | `MATCHED` \| `NOT_MATCHED` \| `NO_FILTER` |
| `matched_keywords` | list[str] | Keywords that matched |
| `lead_score`, `lead_quality`, `priority`, `intent` | str/int | AI analysis fields |
| `phone`, `email`, `whatsapp`, `budget`, `requirement`, `location` | str | Extracted contacts |
| `lead_status` | str | Lifecycle status (new/contacted/qualified/etc.) |
| `notes` | list[dict] | Append-only notes |
| `follow_ups` | list[dict] | Follow-up items |
| `status_history` | list[dict] | Status change history |

### `ai_comments` — AI analysis of one comment

| Field | Type | Description |
| --- | --- | --- |
| `comment_ref`, `comment_id`, `comment_text`, `commenter_name` | str | Comment identity |
| `platform` | str | Platform of parent post |
| `post_ref`, `post_id`, `page_ref`, `page_name` | str | Parent context |
| `phone`, `email`, `whatsapp`, `website` | str | Extracted contacts |
| `budget`, `requirement`, `location` | str | Buyer signals |
| `intent` | str | `buying` \| `selling` \| `rent` \| `investment` \| `other` |
| `urgency` | str | Timeline signal |
| `priority` | str | `high` \| `medium` \| `low` |
| `lead_quality` | str | `hot` \| `warm` \| `cold` |
| `confidence` | float | 0–1 confidence |
| `lead_score` | int | 0–100 deterministic rank |
| `is_lead` | bool | Displayed as a lead candidate |
| `reason` | str | Why the comment was kept/dropped |
| `details` | dict | Full nested extraction |
| `analyzed_by` | str | `rules` \| `gemini` |
| `industry` | str | Industry key of the organization's business context used for the analysis |
| `analyzed_at` | datetime | When analyzed |

### `admin_users` — admin accounts

| Field | Type | Description |
| --- | --- | --- |
| `email` | str | Unique email |
| `name` | str | Display name |
| `password` | str | bcrypt hash (cost 12) |
| `role` | str | `viewer` \| `manager` (a stored `super_admin` is treated as `manager` — the only Super Admin is the `SUPERADMIN_*` environment account) |
| `enabled` | bool | Account active |

### `system_settings` — key-value settings store

| Field | Type | Description |
| --- | --- | --- |
| `key` | str | Unique setting key (e.g. `ai.enabled`) |
| `value` | any | Setting value (typed) |
| `updated_at` | datetime | Last update (TTL index) |

### `env_overrides` — environment variable overrides

| Field | Type | Description |
| --- | --- | --- |
| `name` | str | Unique env var name |
| `value` | str | Override value |
| `updated_at` | datetime | Last update |

### `audit_logs` — admin action audit trail

| Field | Type | Description |
| --- | --- | --- |
| `action` | str | Action performed |
| `category` | str | Action category |
| `user` | str | User who performed the action |
| `details` | dict | Action details (secrets redacted) |
| `success` | bool | Whether the action succeeded |
| `at` | datetime | Timestamp |

### `keyword_filter_rules` — comment filter rules

| Field | Type | Description |
| --- | --- | --- |
| `name`, `description` | str | Rule identity |
| `match_mode` | str | `any` \| `all` \| `category` \| `advanced` |
| `include_keywords`, `exclude_keywords` | list[str] | Keywords |
| `categories` | list[str] | Category keys |
| `keyword_groups` | list[list[str]] | Advanced mode groups |
| `platforms` | list[str] | Platform filter |
| `detect_contacts` | bool | Contact detection |
| `active` | bool | Whether this is the active rule |

### `keyword_filter_results` — per-comment filter results

| Field | Type | Description |
| --- | --- | --- |
| `comment_id` | str | Comment reference |
| `rule_id` | str | Rule reference |
| `status` | str | `MATCHED` \| `NOT_MATCHED` \| `NO_FILTER` |
| `matched_keywords` | list[str] | Keywords that matched |

### `settings_history` — settings version history

| Field | Type | Description |
| --- | --- | --- |
| `version` | int | Version number |
| `changed` | dict | Changed keys and values |
| `changed_by` | str | User who made the change |
| `created_at` | datetime | Timestamp |

### `organizations` — tenant workspaces

| Field | Type | Description |
| --- | --- | --- |
| `name` | str | Organization name |
| `slug` | str | URL-friendly identifier (unique) |
| `status` | str | `pending` (demo request) \| `demo` (approved demo) \| `rejected` \| `active` (paid, confirmed by Super Admin) \| `trial` (legacy) \| `suspended` \| `disabled` \| `cancelled` \| `archived` |
| `industry` | str | Industry key from the catalog (older free-text values are resolved by name or kept as a custom label) |
| `settings.business_profile` | dict | `custom_industry`, `description`, `offerings`, `target_customers`, `lead_criteria`, `requirement_terms` — the business context sent to lead analysis |
| `settings.filter_by_industry` | bool | Pre-filter comments by the industry (default off) |
| `plan_id` | str | Current subscription plan |
| `timezone` | str | Default timezone |
| `currency` | str | Billing currency |
| `settings` | dict | Workspace-specific settings |
| `metadata` | dict | System metadata |

### `organization_members` — user-to-org membership

| Field | Type | Description |
| --- | --- | --- |
| `organization_id` | ObjectId | Parent organization |
| `user_id` | ObjectId | Member user |
| `role` | str | `owner` \| `admin` \| `manager` \| `member` \| `viewer` |
| `status` | str | `active` \| `invited` \| `suspended` |
| `invited_by` | str | Who invited this member |
| `joined_at` | datetime | Join timestamp |

### `organization_invitations` — pending team invites

| Field | Type | Description |
| --- | --- | --- |
| `organization_id` | ObjectId | Target organization |
| `email` | str | Invitee email |
| `role` | str | Assigned role |
| `token_hash` | str | SHA-256 hash of invite token |
| `invited_by` | str | Inviter |
| `expires_at` | datetime | Token expiry (default 7 days) |
| `status` | str | `pending` \| `accepted` \| `expired` \| `revoked` |

### `subscriptions` — tenant subscriptions

| Field | Type | Description |
| --- | --- | --- |
| `organization_id` | ObjectId | Tenant |
| `plan_id` | str | Subscribed plan |
| `status` | str | `trialing` \| `active` \| `past_due` \| `cancelled` \| `expired` |
| `trial_start` / `trial_end` | datetime | Trial period |
| `current_period_start` / `current_period_end` | datetime | Billing period |
| `cancel_at` | datetime | Scheduled cancellation |

### `organization_usage` — aggregated quota counters

| Field | Type | Description |
| --- | --- | --- |
| `organization_id` | ObjectId | Tenant |
| `period` | str | `YYYY-MM` calendar month |
| `searches_used` / `posts_used` / `comments_used` / `ai_used` / `exports_used` | int | Atomic counters |

### `invoices` — billing history

| Field | Type | Description |
| --- | --- | --- |
| `organization_id` | ObjectId | Tenant |
| `invoice_number` | str | Unique invoice ID |
| `amount` | float | Amount in currency |
| `currency` | str | Currency code |
| `status` | str | `pending` \| `paid` \| `failed` \| `refunded` |
| `created_at` | datetime | Invoice date |

### `users` — customer users

| Field | Type | Description |
| --- | --- | --- |
| `email` | str | Unique email |
| `name` | str | Display name |
| `password_hash` | str | bcrypt hash (never stored or shown in plaintext) |
| `default_organization_id` | str | Primary organization (role lives in `organization_members`) |
| `status` | str | `pending_approval` (demo request) \| `active` \| `suspended` \| `disabled` (the demo decision itself lives on the organization: `pending` → `demo` / `rejected`) |

### `industries` — industry catalog (Super Admin)

| Field | Type | Description |
| --- | --- | --- |
| `key` | str | Unique key (built-in keys hold overrides; other keys are custom industries) |
| `name`, `icon`, `description` | str | Display |
| `ai_guidance` | str | What a qualified lead is for this industry (sent to the AI) |
| `category_keys` | list[str] | Linked comment-filter categories |
| `default_keywords` | list[str] | Suggested lead keywords |
| `requirement_terms` | list[str] | Rule-stage vocabulary |
| `enabled` | bool | Selectable by organizations (`general` can never be disabled) |

### `cms_pages` — CMS managed pages

| Field | Type | Description |
| --- | --- | --- |
| `title` | str | Page title |
| `slug` | str | URL slug |
| `content` | str | HTML content |
| `status` | str | `draft` \| `published` \| `archived` |
| `sections` | list | Page sections with ordering |
| `version` | int | Version number |
| `version_history` | list | Previous versions |

### `cms_faq` — FAQ items

| Field | Type | Description |
| --- | --- | --- |
| `question` | str | FAQ question |
| `answer` | str | HTML answer |
| `order` | int | Display ordering |
| `category` | str | Optional category |

### `cms_settings` — website settings

| Field | Type | Description |
| --- | --- | --- |
| `brand_name` | str | Brand display name |
| `tagline` | str | Brand tagline |
| `logo_url` / `favicon_url` | str | Brand assets |
| `primary_color` / `accent_color` | str | Brand colors |
| `seo_title` / `seo_description` | str | SEO metadata |
| `social_links` | dict | Social media URLs |
| `footer_copyright` | str | Footer text |

### `search_presets` — user & organization search configurations

| Field | Type | Description |
| --- | --- | --- |
| `name` | str | Preset name (e.g., "Luxury Villa Buyers") |
| `organization_id` | str | Tenant scope |
| `user_id` | str | Creator user ID |
| `industry_key` | str | Industry taxonomy key |
| `include_keywords` | list[str] | Target keywords |
| `exclude_keywords` | list[str] | Excluded keywords |
| `platforms` | list[str] | Enabled platforms (`facebook`, `instagram`, `youtube`, `linkedin`) |
| `max_posts` | int | Default posts to inspect |
| `comments_per_post` | int | Comments to scrape per post |
| `created_at`, `updated_at` | datetime | Timestamps |

### `token_balances` — organization token balances & quotas

| Field | Type | Description |
| --- | --- | --- |
| `organization_id` | str | Tenant identifier (unique) |
| `allocated_tokens` | int | Total tokens granted (demo or subscription tier) |
| `consumed_tokens` | int | Tokens spent on Apify runs & AI analyses |
| `remaining_tokens` | int | Net available balance (`allocated - consumed`) |
| `demo_tokens` | int | Separate ledger for initial demo allowance |
| `expires_at` | datetime | Expiry date of active tokens |
| `updated_at` | datetime | Timestamp of last decrement/grant |

### `demo_requests` — public demo signup applications

| Field | Type | Description |
| --- | --- | --- |
| `name`, `email`, `phone` | str | Requester contact details |
| `company`, `industry` | str | Business identity & sector |
| `requested_plan` | str | Requested plan tier |
| `status` | str | `pending` \| `approved` \| `rejected` \| `cancelled` |
| `granted_config` | dict | Granted tokens, duration, and feature flags on approval |
| `history` | list[dict] | Status history with approving Super Admin and notes |
| `created_at`, `approved_at` | datetime | Lifecycle timestamps |

### `user_sessions` — active authenticated sessions

| Field | Type | Description |
| --- | --- | --- |
| `session_token_hash` | str | SHA-256 hash of random session token |
| `user_id` | str | Authenticated user |
| `email` | str | User email address |
| `role` | str | Resolved user or platform role |
| `organization_id` | str | Tenant scope (empty for Super Admin) |
| `impersonated_by` | str/None | Super Admin email if active support session |
| `expires_at` | datetime | Absolute session expiration (default 7 days) |
| `created_at` | datetime | Sign-in timestamp |

### Database Inventory & Collection Classification

Audited across the active MongoDB Atlas `LeadAI` cluster:

| Classification | Count | Description & Key Collections |
| :--- | :---: | :--- |
| **Core Active Collections** | **38** | In active production read/write code: `organizations`, `organization_members`, `users`, `user_sessions`, `search_history`, `facebook_pages`, `facebook_posts`, `facebook_comments`, `ai_comments`, `search_presets`, `token_balances`, `demo_requests`, `subscriptions`, `plans`, `organization_usage`, `usage_records`, `invoices`, `notifications`, `audit_logs`, `system_settings`, `env_overrides`, `industries`, `cms_pages`, `cms_faq`, `cms_settings`, `contact_messages`, etc. |
| **Legacy / Migrated Collections** | **31** | Collections holding documents from earlier versions, gracefully superseded: `leads` (migrated to `ai_comments`), `buyer_leads` & `seller_leads` (migrated to `ai_comments`), `memberships` (migrated to `organization_members`), `sessions` (migrated to `user_sessions`), `fb_pages` / `fb_posts` / `fb_comments` (migrated to `facebook_*`), `export_history` (superseded by scoped export endpoints) |
| **Empty / Unused Collections** | **16** | Unpopulated schemas or planned extensions: `audit_events`, `billing_invoices`, `demo_tokens`, etc. |

---

## 17. API Architecture

Interactive docs at `/docs` (Swagger UI) when `enable_api_docs=true`. All product routes are under `/api`, admin routes under `/api/admin`, public routes under `/api/public`.

### Auth Routes (`/api/auth/`)

| Method | Endpoint | Purpose | Auth |
| --- | --- | --- | --- |
| POST | `/api/auth/login` | Login with email/password. Body: `{email, password, scope}`. Sets session cookie. | None |
| POST | `/api/auth/signup` | Submit a demo request (password stored as a bcrypt hash; organization `pending` until the Super Admin approves). Body: `{name, email, password, company, phone?, message?, industry?}`. | None |
| POST | `/api/auth/logout` | Clear session cookie. | Session |
| GET | `/api/auth/me` | Return current user from session. | Session |

### Product Routes (`/api/`)

| Method | Endpoint | Purpose | Request | Response |
| --- | --- | --- | --- | --- |
| POST | `/api/url/search` | Start URL-based search | `url`, `max_posts`, `max_comments_per_post`, `filter_mode`, `preset`, `include_keywords`, `exclude_keywords`, `categories`, `match_mode` | `{run_id, status, platform, canonical_url}` |
| GET | `/api/url/search/{run_id}/report` | Full report bundle | — | `{page, posts, comments, status, platform, search_url}` |
| GET | `/api/search/history` | Recent runs | `limit` | `{searches, count}` |
| GET | `/api/search/{run_id}` | Run status + pages | — | `{success, items, count, scrape_info, search, pages}` |
| POST | `/api/search/{run_id}/cancel` | Cancel running search | — | `{run_id, status}` |
| DELETE | `/api/search/{run_id}` | Delete search + all data | — | `{run_id, deleted: {pages, posts, comments, leads}}` |
| GET | `/api/pages` | List/filter pages | `run_id`, `q`, `category`, `city`, `contact`, `offset`, `limit` | `{pages, total, offset, limit}` |
| GET | `/api/pages/{id}` | One page | — | page doc |
| POST | `/api/pages/{id}/posts` | Collect posts (background) | `max_posts` | `{status: running}` |
| GET | `/api/pages/{id}/posts` | Cached posts + status + stats | `offset`, `limit` | `{page, posts, total, qualifyingPosts, activityStatus, leadScore, ...}` |
| GET | `/api/posts/{id}` | One post | — | post doc |
| POST | `/api/posts/{id}/comments` | Collect comments + AI (background) | `max_comments` | `{status: running}` or `skipped` |
| GET | `/api/posts/{id}/comments` | Analyzed comments | `filter_type`, `q`, `quality`, `sort_by`, `only_leads`, `contact_only`, `limit` | `{post, comments, total, counts, ...}` |
| GET | `/api/comments/{id}` | Lead detail | — | merged doc (comment + AI + page + post) |
| PATCH | `/api/leads/{id}` | Update lead status | `{lead_status}` | `{success}` |
| POST | `/api/leads/{id}/notes` | Add note | `{text}` | `{success}` |
| POST | `/api/leads/{id}/follow-ups` | Add follow-up | `{title, due_at?, notes?}` | `{success}` |
| GET | `/api/comment-filters/catalog` | Filter catalog | — | `{categories, presets, custom_categories}` |
| GET | `/api/export/pages.csv` | Export pages CSV | `run_id` | CSV file |
| GET | `/api/export/posts.csv` | Export posts CSV | `page_id` | CSV file |
| GET | `/api/export/comments.csv` | Export leads CSV | `post_id`, `only_leads` | CSV file |

### Admin Routes (`/api/admin/`) — 50+ endpoints

| Method | Endpoint | Purpose | Role |
| --- | --- | --- | --- |
| GET | `/api/admin/dashboard` | Dashboard KPIs, charts, alerts | viewer+ |
| GET | `/api/admin/jobs` | Paginated jobs list | viewer+ |
| GET | `/api/admin/jobs/{run_id}` | Full job report | viewer+ |
| POST | `/api/admin/jobs/{run_id}/retry` | Retry failed job | manager+ |
| POST | `/api/admin/jobs/{run_id}/cancel` | Cancel running job | manager+ |
| DELETE | `/api/admin/jobs/{run_id}` | Delete job + data | super_admin |
| GET | `/api/admin/failed-jobs` | Failed jobs summary | viewer+ |
| GET | `/api/admin/leads` | Paginated leads | viewer+ |
| PATCH | `/api/admin/leads/{id}` | Update lead status | manager+ |
| POST | `/api/admin/leads/bulk` | Bulk lead actions | manager+ |
| GET | `/api/admin/analytics` | Aggregated analytics | viewer+ |
| GET | `/api/admin/platforms` | Platform list + stats | viewer+ |
| POST | `/api/admin/platforms/{p}/toggle` | Enable/disable platform | manager+ |
| POST | `/api/admin/platforms/{p}/actor` | Update actor ID | manager+ |
| GET | `/api/admin/apify` | Apify connection status | viewer+ |
| POST | `/api/admin/apify/test` | Live Apify test | manager+ |
| POST | `/api/admin/apify/token` | Save Apify token | manager+ |
| GET | `/api/admin/actors` | List all actors | viewer+ |
| POST | `/api/admin/actors/test` | Test an actor | manager+ |
| GET | `/api/admin/usage` | Usage aggregation | viewer+ |
| GET | `/api/admin/env` | List env vars | manager+ |
| POST | `/api/admin/env/unlock` | Unlock env editing | manager+ |
| PUT | `/api/admin/env/{key}` | Set env var override | manager+ |
| DELETE | `/api/admin/env/{key}` | Reset env var | manager+ |
| GET | `/api/admin/ai` | AI settings + stats | viewer+ |
| PUT | `/api/admin/ai` | Update AI settings | manager+ |
| POST | `/api/admin/ai/test` | Live AI test | manager+ |
| GET | `/api/admin/scoring` | Scoring settings | viewer+ |
| PUT | `/api/admin/scoring` | Update scoring | manager+ |
| GET | `/api/admin/comment-intelligence` | CI settings + stats | viewer+ |
| PUT | `/api/admin/comment-intelligence` | Update CI settings | manager+ |
| GET | `/api/admin/comments` | Comments feed | viewer+ |
| GET | `/api/admin/limits` | Limits settings | viewer+ |
| PUT | `/api/admin/limits` | Update limits | manager+ |
| GET | `/api/admin/database` | MongoDB stats | viewer+ |
| GET | `/api/admin/logs` | Structured log entries with filters (q, level, source, module), summary stats, pagination | viewer+ |
| GET | `/api/admin/logs/stats` | Log level summary statistics | viewer+ |
| GET | `/api/admin/logs/download` | Raw log file download | manager+ |
| GET | `/api/admin/health` | Health checks | viewer+ |
| GET | `/api/admin/alerts` | Active alerts | viewer+ |
| GET | `/api/admin/users` | List users | viewer+ |
| POST | `/api/admin/users` | Create user | super_admin |
| PATCH | `/api/admin/users/{id}` | Update user | super_admin |
| DELETE | `/api/admin/users/{id}` | Delete user | super_admin |
| GET | `/api/admin/security` | Security settings | viewer+ |
| PUT | `/api/admin/security` | Update security | manager+ |
| POST | `/api/admin/security/revoke-sessions` | Revoke all sessions | super_admin |
| GET | `/api/admin/features` | Feature flags | viewer+ |
| PUT | `/api/admin/features` | Update features | manager+ |
| GET | `/api/admin/maintenance` | Maintenance status | viewer+ |
| POST | `/api/admin/maintenance` | Toggle maintenance | manager+ |
| GET | `/api/admin/audit-logs` | Audit log entries | viewer+ |
| GET | `/api/admin/settings` | All global settings | viewer+ |
| PUT | `/api/admin/settings` | Save settings | manager+ |
| POST | `/api/admin/settings/reset` | Reset settings | manager+ |
| POST | `/api/admin/settings/upload` | Upload file (logo/favicon) | manager+ |
| GET | `/api/admin/settings/export` | Export settings JSON | viewer+ |
| POST | `/api/admin/settings/import` | Import settings JSON | manager+ |
| GET | `/api/admin/settings/history` | Version history | viewer+ |
| POST | `/api/admin/settings/history/{v}/restore` | Restore version | manager+ |
| GET | `/api/admin/search` | Global search | viewer+ |

### Organization Routes (`/api/organizations/`)

| Method | Endpoint | Purpose | Role |
| --- | --- | --- | --- |
| GET | `/api/organizations/current` | Get workspace settings | member+ |
| PATCH | `/api/organizations/current` | Update workspace settings | admin+ |
| GET | `/api/organizations/current/team` | List team members | member+ |
| POST | `/api/organizations/current/invitations` | Invite team member | admin+ |
| DELETE | `/api/organizations/current/invitations/{id}` | Revoke invitation | admin+ |
| PATCH | `/api/organizations/current/members/{id}` | Change member role | admin+ |
| DELETE | `/api/organizations/current/members/{id}` | Remove member | admin+ |
| GET | `/api/invitations/{token}` | Validate invitation token | None |
| POST | `/api/invitations/{token}/accept` | Accept invitation | None |

### Billing Routes (`/api/billing/`)

| Method | Endpoint | Purpose | Auth |
| --- | --- | --- | --- |
| GET | `/api/billing/plans` | Public plans catalog | None |
| GET | `/api/billing/subscription` | Active subscription & trial | Session |
| GET | `/api/billing/usage` | Real-time quota usage | Session |
| POST | `/api/billing/checkout` | Plan upgrade checkout | Session |
| POST | `/api/billing/cancel` | Schedule cancellation | Session |
| POST | `/api/billing/reactivate` | Reactivate subscription | Session |
| GET | `/api/billing/invoices` | Invoice history | Session |
| POST | `/api/billing/webhook` | Provider webhook handler | None |

### Admin AI Routes (`/api/admin/ai/`)

| Method | Endpoint | Purpose | Role |
| --- | --- | --- | --- |
| GET | `/api/admin/ai/prompts` | List AI prompts | viewer+ |
| GET | `/api/admin/ai/prompts/{id}` | Get prompt detail | viewer+ |
| POST | `/api/admin/ai/prompts` | Create prompt | manager+ |
| PUT | `/api/admin/ai/prompts/{id}` | Update prompt | manager+ |
| DELETE | `/api/admin/ai/prompts/{id}` | Delete prompt | super_admin |
| POST | `/api/admin/ai/prompts/{id}/rollback` | Rollback to previous version | manager+ |
| GET | `/api/admin/ai/models` | List AI models | viewer+ |
| PUT | `/api/admin/ai/models/{name}` | Update model config | manager+ |
| POST | `/api/admin/ai/test` | Live AI test | manager+ |

### Admin Apify Routes (`/api/admin/apify/`)

| Method | Endpoint | Purpose | Role |
| --- | --- | --- | --- |
| GET | `/api/admin/apify/jobs` | Paginated job list with filters | viewer+ |
| GET | `/api/admin/apify/jobs/{id}` | Job detail + report | viewer+ |
| POST | `/api/admin/apify/jobs/{id}/retry` | Retry failed job | manager+ |
| POST | `/api/admin/apify/jobs/{id}/cancel` | Cancel running job | manager+ |
| DELETE | `/api/admin/apify/jobs/{id}` | Delete job + data | super_admin |
| POST | `/api/admin/apify/jobs/bulk-retry` | Bulk retry failed jobs | manager+ |
| GET | `/api/admin/apify/actors` | List all actors with config | viewer+ |
| POST | `/api/admin/apify/actors/{key}/test` | Test specific actor | manager+ |

### Admin Leads Routes (`/api/admin/leads/`)

| Method | Endpoint | Purpose | Role |
| --- | --- | --- | --- |
| GET | `/api/admin/leads` | Paginated leads with filters | viewer+ |
| GET | `/api/admin/leads/{id}` | Lead full dossier | viewer+ |
| PATCH | `/api/admin/leads/{id}` | Update lead status | manager+ |
| POST | `/api/admin/leads/{id}/notes` | Add note to lead | manager+ |
| POST | `/api/admin/leads/{id}/follow-ups` | Add follow-up | manager+ |
| POST | `/api/admin/leads/bulk` | Bulk lead actions | manager+ |

### Admin Analytics Routes (`/api/admin/analytics/`)

| Method | Endpoint | Purpose | Role |
| --- | --- | --- | --- |
| GET | `/api/admin/analytics/overview` | SaaS overview metrics | viewer+ |
| GET | `/api/admin/analytics/lead-funnel` | Lead pipeline funnel | viewer+ |
| GET | `/api/admin/analytics/platforms` | Platform comparison | viewer+ |
| GET | `/api/admin/analytics/categories` | Category breakdown | viewer+ |
| GET | `/api/admin/analytics/plans` | Plan analytics | viewer+ |
| GET | `/api/admin/analytics/activity` | Daily activity charts | viewer+ |

### Admin CMS Routes (`/api/admin/cms/`)

| Method | Endpoint | Purpose | Role |
| --- | --- | --- | --- |
| GET/POST | `/api/admin/cms/pages` | List/Create pages | viewer+/manager+ |
| PUT/DELETE | `/api/admin/cms/pages/{id}` | Update/Delete page | manager+/super_admin |
| POST | `/api/admin/cms/pages/{id}/publish` | Publish draft | manager+ |
| POST | `/api/admin/cms/pages/{id}/restore/{v}` | Restore version | manager+ |
| GET | `/api/admin/cms/pages/{id}/history` | Version history | viewer+ |
| GET/POST/PUT/DELETE | `/api/admin/cms/faq` | FAQ CRUD | viewer+/manager+ |
| GET/POST/PUT/DELETE | `/api/admin/cms/testimonials` | Testimonial CRUD | viewer+/manager+ |
| GET/PUT | `/api/admin/cms/navigation` | Navigation management | manager+ |
| GET/PUT | `/api/admin/cms/settings` | Website settings | viewer+/manager+ |
| GET/POST/DELETE | `/api/admin/cms/media` | Media management | viewer+/manager+ |
| POST | `/api/admin/cms/media/upload` | Upload file | manager+ |
| GET/PUT | `/api/admin/cms/contact` | Contact submissions | viewer+/manager+ |

### Public Routes (`/api/public/`)

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/public/config` | Public-safe settings (branding, maintenance, features) |
| GET | `/api/public/theme` | Branding tokens (colors, name, logo, favicon) |
| GET | `/api/public/pricing` | Plans catalog for pricing page |
| GET | `/api/public/faq` | Published FAQ items |
| GET | `/api/public/pages/{slug}` | Published page content |
| POST | `/api/public/contact` | Submit contact form (rate-limited) |
| GET | `/api/public/industries` | Enabled industries (key, name, icon, description) for signup |
| GET | `/health` | Health check (MongoDB ping, latency) |

### Industry & Business Context Routes

| Method | Endpoint | Purpose | Role |
| --- | --- | --- | --- |
| GET | `/api/business-context/catalog` | 22-industry Domain Intelligence catalog with keywords and guidance | Member+ |
| GET | `/api/search-presets` | List tenant-scoped saved search presets | Member+ |
| POST | `/api/search-presets` | Create a tenant-scoped search preset | Member+ |
| DELETE | `/api/search-presets/{id}` | Delete a saved search preset | Member+ |
| GET | `/api/org-admin/business-summary` | Overview card summary of business profile & keyword library | Org `settings.view` |
| GET | `/api/org-admin/business-profile` | Industry, business profile, AI context preview, catalog | Org `settings.view` |
| PUT | `/api/org-admin/business-profile` | Set industry + business profile + industry filter (audited) | Org `settings.manage` |
| GET/PUT | `/api/org-admin/keyword-library` | Organization active & excluded keywords library | Org `settings.manage` |
| POST | `/api/org-admin/recommend-keywords` | Generate industry keyword recommendations | Org `settings.manage` |
| POST | `/api/org-admin/lead-rules/test` | Test active lead rules and keyword matching against sample text | Org `settings.manage` |
| GET | `/api/super-admin/industries` | Catalog incl. disabled, organizations per industry, default | Super Admin |
| POST | `/api/super-admin/industries` | Add a custom industry | Super Admin |
| PATCH | `/api/super-admin/industries/{key}` | Edit / enable / disable (built-ins are overridden) | Super Admin |
| DELETE | `/api/super-admin/industries/{key}` | Delete a custom industry not in use (built-in → 409) | Super Admin |
| PUT | `/api/super-admin/industries/default` | Platform default industry | Super Admin |
| PATCH | `/api/super-admin/organizations/{org_id}` | Also accepts `industry` | Super Admin |

---

## 18. Apify Architecture

```mermaid
flowchart TD
    R[Social URL input] --> B[Backend pipeline]
    B --> S["Actor selection (per platform)"]
    S --> I["Actor input (startUrls / resultsLimit / platform flags)"]
    I --> C["client.actor(id).call(run_input, run_timeout=8min)<br/>or .start() + poll + abort (cancellable)"]
    C --> D[Apify run + dataset]
    D --> E["Backend reads dataset (iterate_items)"]
    E --> F["Normalization (map_*_item)"]
    F --> G[AI analysis + qualification]
    G --> H[Lead results]
    E -->|"classified errors<br/>BLOCKED/ACTOR_FAILED/NO_RESULTS/..."| ERR[ScrapeError → API/UI]
```

### Facebook Actors (Hardcoded)

| Actor | Purpose | Input | Output (used fields) |
| --- | --- | --- | --- |
| `apify/facebook-pages-scraper` | Page details by URL | `{startUrls: [{url}]}` | verified, email, phone, whatsapp, website, about, photos, followers, category |
| `apify/facebook-posts-scraper` | Posts of a page | `{startUrls, resultsLimit, captionText: true}` | post url/id/text/media/dates/likes/comment count/shares |
| `apify/facebook-comments-scraper` | Comments of a post | `{startUrls, resultsLimit, includeNestedComments: true, viewOption: RANKED_UNFILTERED}` | comment id/url/text/author/date/reactions |

### Configurable URL-Search Actors

| Platform | Default Actor ID | Comments |
| --- | --- | --- |
| Instagram | `apify/instagram-scraper` | ✅ Supported |
| YouTube | `streamers/youtube-scraper` | ❌ Not collected |
| LinkedIn | `harvestapi/linkedin-company` + `harvestapi/linkedin-company-posts` | ✅ Supported |

### Run Lifecycle & Error Handling

- Every call wrapped with **8-minute timeout** (`_RUN_TIMEOUT_MIN`)
- **Cancellable runs**: `actor.start()` then poll every 5s; cancellation calls `run.abort()`
- Datasets read via `iterate_items()`; failures classified as `DATASET_ERROR`
- Errors classified: `BLOCKED`, `ACTOR_FAILED`, `ACTOR_TIMED_OUT`, `INVALID_INPUT`, `API_ERROR`, `ACCESS_DENIED`, `NETWORK_ERROR`, `NO_RESULTS`
- Billing hints produce user-facing `ApifyError` with billing link
- **Cost control**: `MIN_COMMENTS` gates expensive comment actor; posts below threshold never scraped
- **Graceful shutdown**: in-flight Apify runs aborted on server exit

---

## 19. AI Architecture

```mermaid
flowchart TD
    R["Raw lead data (comment text + post caption + author)"] --> CF["Comment Filter Pipeline<br/>app/pipeline/comment_filter.py<br/>keyword/category matching"]
    CF -->|"NOT_MATCHED"| SKIP["Stored but skipped"]
    CF -->|"MATCHED / NO_FILTER"| P["Stage 1 — Rule filter<br/>spam / emoji-only / link-only<br/>+ regex extraction (offline, free)"]
    P -->|"dropped"| OUT["No lead"]
    P -->|passes| PB["Prompt builder — COMMENT_SYSTEM_PROMPT<br/>Strict JSON schema, 'never invent values'"]
    PB --> M["Google Gemini — gemini-2.5-flash<br/>generateContent, 429 backoff + 10-min circuit breaker"]
    M -->|fail/rate-limited| FB["Fallback to rule result<br/>analyzed_by: rules"]
    M -->|structured JSON| V["Validation + normalization"]
    V --> S["comment_lead_score (0-100)"]
    S --> D["extract_display_signals → is_lead"]
    D --> DB[(ai_comments — upsert)]
```

- **Provider**: Google Gemini via direct REST, no SDK. Model default `gemini-2.5-flash` (`GEMINI_MODEL`).
- **Input**: JSON payload with `author`, `post_caption`, `comment_text`, `business_category` — one comment per call.
- **Business context**: the organization's industry and business profile are appended to the system prompt in a delimited, data-only block; intent and lead quality are judged relative to that business (see [Industries & business context](#industries--business-context)).
- **Output**: Strict JSON with `is_useful`, `lead_type`, `confidence_score`, `priority`, `lead_quality`, `sentiment`, `contact{}`, `person{}`, `buyer{}`.
- **Error handling**: 429 opens 10-minute circuit breaker; any exception falls back to Stage-1 rule result.
- **Rule path**: `rule_based_classify` + `_rule_extraction` (regex contact/budget/location/urgency/intent extraction).
- **Without GEMINI_API_KEY**: Everything still works on rules — only richer fields are missing.

---

## 20. Comment Filter Pipeline

**Location**: `app/pipeline/comment_filter.py` (860 lines)

The keyword filter is the **first layer** of the lead pipeline, running before AI analysis.

### Which rule applies to a search

1. A rule or preset explicitly chosen for the run
2. Keywords/categories entered in the search form
3. The organization's own lead keywords (Org Admin → Lead rules)
4. The organization's **industry filter**, when *filter by industry* is on (industry categories + keywords + intent presets)
5. The globally active rule (platform admin)
6. No filter — every comment is analysed (default)

### Modes

| Mode | Behavior |
| --- | --- |
| **NO_FILTER** | No keywords, no categories: every comment processed (status `NO_FILTER`). Default. |
| **KEYWORD** | Match `include_keywords` (any/all mode); `exclude_keywords` always veto. |
| **CATEGORY** | Match predefined category keywords (real_estate, automotive, etc.). |
| **ADVANCED** | Keyword groups — must match one keyword in EVERY group. |

### Predefined Business Categories

| Key | Name | Icon |
| --- | --- | --- |
| `real_estate` | Real Estate | 🏠 |
| `automotive` | Automotive | 🚗 |
| `interior_design` | Interior Design | 🛋️ |
| `wedding` | Wedding Services | 💒 |
| `education` | Education | 📚 |
| `healthcare` | Healthcare | 🏥 |
| `technology` | Technology | 💻 |
| `finance` | Finance | 💰 |
| `travel` | Travel | ✈️ |
| `fashion` | Fashion | 👗 |

### Matching Semantics

- Case-insensitive (both sides normalized to lowercase)
- Unicode normalization (NFKC) + whitespace/punctuation normalization
- Phrase matching (multi-word keywords = normalized substring)
- Word-boundary matching for short keywords (<4 chars) so "car" never matches "career"
- Prefix matching for longer keywords so "price" matches "prices"
- Devanagari (Hindi) keywords match natively after normalization

---

## 21. Lead Lifecycle

**Location**: `app/pipeline/lead_lifecycle.py` (237 lines)

### State Machine

```
new → contacted → qualified → follow_up → converted
new → disqualified
new → lost
contacted → lost
qualified → lost
follow_up → lost
any → archived (admin only)
```

### Statuses

| Status | Label | Description |
| --- | --- | --- |
| `new` | New | Just identified as a lead |
| `contacted` | Contacted | Outreach initiated |
| `qualified` | Qualified | Meets quality criteria |
| `follow_up` | Follow-up | Scheduled for follow-up |
| `converted` | Converted | Successfully converted |
| `lost` | Lost | Lost opportunity |
| `disqualified` | Disqualified | Does not meet criteria |
| `archived` | Archived | Historical record |

### Features

- **Valid transitions** enforced per state (e.g., `archived` has no transitions out)
- **Status history** recorded on every change (from_status, to_status, changed_by, reason, timestamp)
- **Notes** append-only with author, text, and timestamp
- **Follow-ups** with title, due date, status (pending/completed/cancelled/overdue), and notes
- **Terminal states**: converted, lost, disqualified — only transition to archived

---

## 22. Data Flow

```mermaid
flowchart LR
    USER[USER] --> FE[FRONTEND app.js]
    USER --> ADM[ADMIN admin.js]
    FE -->|POST + poll| API[BACKEND API routes]
    ADM -->|GET/POST| API
    API -->|background task| AG[URL SEARCH PIPELINE url_search.py]
    AG --> PROV[PROVIDER — Apify actors]
    PROV --> RAW[RAW ACTOR DATA]
    RAW --> NORM[NORMALIZERS map_*_item]
    NORM --> DB[(MONGODB — 38 collections)]
    NORM --> CF[COMMENT FILTER comment_filter.py]
    CF --> AI[AI ANALYSIS comment_ai]
    AI --> QUAL[LEAD QUALIFICATION score / priority / is_lead]
    QUAL --> LC[LEAD LIFECYCLE lead_lifecycle.py]
    LC --> DB
    DB --> RESP[API RESPONSE — serialized docs]
    RESP --> FE
    RESP --> ADM
    FE --> USER
    ADM --> USER
```

---

## 23. Error Handling

The project treats failures as **data**, not just exceptions.

| Scenario | What Happens |
| --- | --- |
| Invalid URL / unsupported platform | `UrlError` → **422** `{errorType: invalid\|unsupported}` |
| Invalid ObjectId | **400** `Invalid id: ...` |
| Missing document | **404** `<collection> document not found` |
| Mongo unreachable | **503** `Database unavailable` |
| Missing `APIFY_API_TOKEN` | Startup warning; searches fail fast with clear message |
| Apify actor run failed | `ACTOR_FAILED` with `statusMessage`; run marked `error` |
| Apify run timed out (> 8 min) | `ACTOR_TIMED_OUT` — actor aborted |
| Apify API-level errors | 401 → invalid token; 403 → `ACCESS_DENIED`; 429 → rate-limited; 5xx → server error |
| Facebook blocking evidence | Only when run stats contain block/captcha tokens → `BLOCKED` |
| Empty results | `NO_RESULTS` — never treated as a block |
| Billing/credit exhaustion | User-facing `ApifyError` with billing link |
| Posts scrape returns nothing | Page `posts_status: empty` |
| Comments below threshold | Post `comments_status: skipped` — no API call made |
| Gemini failure / 429 | Circuit breaker (10 min), rule-based fallback |
| Page details actor fails | Graceful fallback: URL-derived page doc |
| Cancellation mid-run | `CANCELLED`; Apify run aborted |
| Stale "running" status (30 min) | UI offers Retry |
| Duplicate key on insert | Handled via unique indexes + upsert |
| Brute-force login attempt | Rate limit: 5 failures → lockout |
| Session expired | 401 → redirect to `/login` |
| Idle session timeout | `revoked_by="idle_timeout"` in `user_sessions` → 401 redirect to `/login` |
| Maintenance mode | 503 with custom message (admin panel still accessible) |

---

## 24. Environment Variables

LeadAI manages configuration through a hybrid hierarchy:
1. **Host Environment / `.env` File**: Defined via `pydantic-settings` in `app/config.py`.
2. **Environment Variable Registry**: Defined in `app/admin/envvars.py` (`ENVVAR_REGISTRY`), specifying data types, masking rules, restart requirements, and lock levels.
3. **Database Overrides**: Non-locked settings can be modified by the Super Admin in the Platform Console (`/admin#envvars`) and persisted in the `system_settings` collection.
4. **Locked / Non-Shadowable Variables**: High-security parameters (`SUPERADMIN_*`, `SESSION_SECRET`, Stripe secret keys, SMTP credentials) are marked `locked: true` and can ONLY be read from the physical environment or `.env` file — database records are strictly forbidden from modifying or shadowing them.

Template file: `.env.example`.

### Complete Configuration Matrix

| Variable | Group | Required | Default | Restart | Masked | Purpose |
| --- | --- | --- | --- | --- | --- | --- |
| `SUPERADMIN_EMAIL` | Security | **Yes** | — | Yes | No (Locked) | Permanent Super Admin email. Read only from environment; non-shadowable |
| `SUPERADMIN_PASSWORD` | Security | **Yes** | — | Yes | Yes (Locked) | Permanent Super Admin password (plain text 12+ chars or `$2b$` bcrypt hash). Never stored in DB |
| `SESSION_SECRET` | Security | **Yes** | — | No | Yes (Locked) | High-entropy random secret for session cookie signing. If empty, a volatile per-process secret is used (invalidating logins on reboot) |
| `SESSION_TTL_DAYS` | Security | No | `7` | No | No | Session lifetime in days applied to newly authenticated users |
| `SESSION_COOKIE_SECURE`| Security | No | `false` | No | No | Enforces `Secure` flag on cookies. Must be `true` behind HTTPS/TLS reverse proxies |
| `SESSION_IDLE_TIMEOUT_MINUTES` | Security | No | `30` | No | No | Idle session timeout in minutes (0 disables). Inactive sessions are revoked server-side |
| `ALLOWED_ORIGINS` | Security | No | `""` | Yes | No | Comma-separated CORS allowed origins (e.g., `https://app.leadai.com`). Empty string restricts to same-origin |
| `ENABLE_API_DOCS` | Security | No | `false` | Yes | No | Enables `/docs` and `/redoc` OpenAPI interactive documentation (disable in production) |
| `TRUST_PROXY_HEADERS` | Security | No | `false` | No | No | Trust `X-Forwarded-For` and `X-Real-IP` headers for rate limiting and IP logging behind proxies |
| `MONGO_URI` | Database | **Yes** | `mongodb://localhost:27017` | Yes | No | MongoDB connection URI (supports standard and `mongodb+srv://` Atlas URIs) |
| `MONGO_DB_NAME` | Database | **Yes** | `LeadAI` | Yes | No | Primary database name in MongoDB cluster |
| `DNS_SERVERS` | Database | No | `8.8.8.8,1.1.1.1` | Yes | No | Comma-separated DNS resolvers for reliable SRV lookups during Atlas cluster connects |
| `AUDIT_RETENTION_DAYS`| Database | No | `365` | Yes | No | TTL expiration days for `audit_logs` collection entries (Mongo TTL index) |
| `APIFY_API_TOKEN` | Apify | **Yes**¹ | — | No | Yes | Apify API authentication token. Required for executing social scraper actors |
| `MIN_COMMENTS` | Scraping | No | `10` | Yes | No | Minimum comment count for a social post to qualify for comment scraping (0 disables) |
| `MAX_COMMENTS_TO_COLLECT`| Scraping | No | `100` | Yes | No | Maximum comment budget harvested per URL-search execution |
| `INSTAGRAM_ACTOR_ID` | Actors | No | `apify/instagram-scraper` | Yes | No | Apify actor slug used for Instagram profiles |
| `YOUTUBE_ACTOR_ID` | Actors | No | `streamers/youtube-scraper` | Yes | No | Apify actor slug used for YouTube channels |
| `LINKEDIN_ACTOR_ID` | Actors | No | `harvestapi/linkedin-company` | Yes | No | Apify actor slug used for LinkedIn company pages |
| `LINKEDIN_POSTS_ACTOR_ID`| Actors | No | `harvestapi/linkedin-company-posts` | Yes | No | Apify actor slug used for LinkedIn company posts |
| `GEMINI_API_KEY` | AI | No² | — | No | Yes | Google Gemini API key for contextual comment analysis and lead qualification |
| `GEMINI_MODEL` | AI | No | `gemini-2.5-flash` | Yes | No | Google Gemini model identifier (`gemini-2.5-flash`, `gemini-2.5-pro`) |
| `STRIPE_SECRET_KEY` | Billing | No³ | — | No | Yes (Locked) | Stripe API Secret Key (`sk_live_...` or `sk_test_...`) |
| `STRIPE_PUBLISHABLE_KEY`| Billing | No | — | No | No | Stripe Publishable Key for frontend Stripe Elements checkout integration |
| `STRIPE_WEBHOOK_SECRET`| Billing | No³ | — | No | Yes (Locked) | Stripe Webhook Signing Secret (`whsec_...`) for subscription lifecycle events |
| `BILLING_WEBHOOK_SECRET`| Billing | No | — | No | Yes | HMAC secret for generic/mock billing webhooks (`X-LeadAI-Signature`) |
| `MOCK_PAYMENTS_ENABLED`| Billing | No | `true` | No | No | Enables dev mock payment gateway when Stripe keys are unconfigured |
| `SMTP_HOST` | Email | No⁴ | — | No | No (Locked) | SMTP relay hostname (e.g., `smtp.sendgrid.net`). If empty, emails route to DB outbox |
| `SMTP_PORT` | Email | No | `587` | No | No | SMTP relay port (typically 587 for STARTTLS or 465 for TLS) |
| `SMTP_USER` | Email | No | — | No | No (Locked) | SMTP authentication username |
| `SMTP_PASSWORD` | Email | No | — | No | Yes (Locked) | SMTP authentication password or app API key |
| `SMTP_FROM` | Email | No | — | No | No | Default `From:` sender address for transactional invitations and reset links |
| `API_PORT` | Server | No | `8000` | Yes | No | TCP port FastAPI listens on during startup (read by launcher) |
| `PUBLIC_BASE_URL` | Server | No | `""` | No | No | Public domain URL (e.g., `https://leadai.domain.com`) used to format invite/reset emails |
| `BUSINESS_DOMAIN` | Server | No | `""` | No | No | Platform business domain metadata |
| `LOG_LEVEL` | Logging | No | `INFO` | No | No | Global application log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `LOG_FILE_LEVEL` | Logging | No | `INFO` | No | No | Minimum severity level written to rotating log files (`logs/app.log`) |
| `LOG_CONSOLE_LEVEL` | Logging | No | `INFO` | No | No | Minimum severity level output to terminal stdout |

¹ Required for executing social searches.
² Optional: if unset, system runs in rule-based fallback mode.
³ Optional: if unset, platform utilizes the built-in mock payment provider with manual Super Admin activation.
⁴ Optional: if unset, outbound emails (invitations, password resets) are stored in the `email_outbox` MongoDB collection and visible to administrators in the Outbox view.

---

## 25. System Settings

Stored in MongoDB `system_settings` collection, configurable via Admin Control Center.

### Limits

| Key | Default | Description |
| --- | --- | --- |
| `limits.min_comments` | 10 | Minimum comments to qualify a post |
| `limits.max_posts_default` | 20 | Default post limit |
| `limits.max_posts_cap` | 100 | Hard post limit |
| `limits.max_comments_per_post_default` | 30 | Default comments per post |
| `limits.max_comments_per_post_cap` | 500 | Hard comments per post limit |
| `limits.global_max_comments` | 5000 | Total comment budget per query |
| `cost.stop_on_limit` | false | Abort when budget reached |
| `cost.warn_before_expensive` | true | Show cost warning |

### AI

| Key | Default | Description |
| --- | --- | --- |
| `business.default_industry` | `general` | Industry for organizations that chose none (Super Admin → Industries) |
| `ai.enabled` | true | Enable Gemini analysis |
| `ai.rule_fallback` | true | Fallback to rules if AI fails |
| `ai.max_calls_per_job` | 1000 | Gemini calls budget per run |
| `ai.temperature` | 0.3 | Model temperature |

### Lead Scoring

| Key | Default | Description |
| --- | --- | --- |
| `scoring.confidence_weight` | 30 | Confidence weight |
| `scoring.priority_weight` | 25 | Priority weight |
| `scoring.quality_weight` | 20 | Quality weight |
| `scoring.contact_phone` | 15 | Phone contact points |
| `scoring.contact_email` | 10 | Email contact points |
| `scoring.spam_penalty` | 20 | Spam penalty points |
| `scoring.hot_min` | 80 | Hot lead threshold (min score) |
| `scoring.warm_min` | 50 | Warm lead threshold (min score) |

### Comment Intelligence

| Key | Default | Description |
| --- | --- | --- |
| `ci.detect_phone` | true | Extract phone numbers |
| `ci.detect_email` | true | Extract emails |
| `ci.detect_budget` | true | Detect budget mentions |
| `ci.detect_location` | true | Detect locations |
| `ci.detect_urgency` | true | Detect urgency |
| `ci.detect_buying_intent` | true | Detect buying intent |
| `ci.detect_selling_intent` | false | Detect selling intent |
| `ci.ignore_emoji_only` | true | Skip emoji-only comments |
| `ci.ignore_spam` | true | Skip spam comments |
| `ci.ignore_low_value` | true | Skip low-value comments |

### Security

| Key | Default | Description |
| --- | --- | --- |
| `security.session_timeout_hours` | 168 | Session timeout (7 days / 168 hours, matches SESSION_TTL_DAYS) |
| `security.login_protection` | true | Rate-limit logins |
| `security.audit_logging` | true | Record admin actions |

### Features & Platforms

| Key | Default | Description |
| --- | --- | --- |
| `features.url_search.enabled` | true | Allow URL search |
| `features.exports.enabled` | true | Allow CSV exports |
| `platform.facebook` | true | Facebook enabled |
| `platform.instagram` | true | Instagram enabled |
| `platform.youtube` | true | YouTube enabled |
| `platform.linkedin` | true | LinkedIn enabled |
| `maintenance.enabled` | false | Maintenance mode |
| `maintenance.message` | "" | Maintenance notice |

---

## 26. Installation

### Prerequisites

- **Python 3.11+**
- **MongoDB** — local install or Docker (`mongo:7`)
- **Apify account + token** (free tier: https://apify.com)
- **Google AI Studio key** (optional — https://aistudio.google.com)
- Docker + docker-compose (optional)

### Backend Installation

```bash
git clone https://github.com/saurabh95710/LEADAI.git lead_apify
cd lead_apify

python -m venv .venv
# Windows: .venv\Scripts\activate    |    macOS/Linux: source .venv/bin/activate

pip install -r requirements.txt
```

### Environment Setup

```bash
cp .env.example .env
```

Edit `.env` with your real values. At minimum: `MONGO_URI`, `SUPERADMIN_EMAIL`, `SUPERADMIN_PASSWORD`, `SESSION_SECRET` (and `APIFY_API_TOKEN` to run searches). Admins and users are never configured here — they are database accounts created through demo signup and invitations.

```bash
# Generate bcrypt password hash
python -c "import bcrypt;print(bcrypt.hashpw(b'YourPassword', bcrypt.gensalt(12)).decode())"
```

### Database Setup

No manual schema creation needed — `ensure_indexes()` creates all indexes at startup using `_create_index_safe()` which handles IndexKeySpecsConflict by dropping and recreating conflicting indexes. The database is created automatically on first write.

### Frontend

Nothing to build — `app/static/` is served directly by the backend.

### Docker

```bash
docker compose up --build
```

Starts `leadai_api` (FastAPI on :8000) and `leadai_mongo` (MongoDB 7 on :27017 with named volume). `.env` is passed through `env_file`.

---

## 27. Running the Project

```bash
# Backend
uvicorn app.main:app --reload --port 8000
```

or with Docker:

```bash
docker compose up --build
```

URLs:

- Dashboard: [http://localhost:8000/](http://localhost:8000/)
- Admin: [http://localhost:8000/admin](http://localhost:8000/admin)
- Login: [http://localhost:8000/login](http://localhost:8000/login)
- Admin Login: [http://localhost:8000/login?admin=1](http://localhost:8000/login?admin=1)
- Signup: [http://localhost:8000/signup](http://localhost:8000/signup)
- Contact: [http://localhost:8000/contact](http://localhost:8000/contact)
- Website: [http://localhost:8000/website](http://localhost:8000/website)
- API docs: [http://localhost:8000/docs](http://localhost:8000/docs) (if enabled)
- Health: [http://localhost:8000/health](http://localhost:8000/health)
- URL-search report: `http://localhost:8000/static/url_report.html?run_id=<URL-run-id>`
- Super Admin login: [http://localhost:8000/login?superadmin=1](http://localhost:8000/login?superadmin=1)

### Deploying on Render

Render builds the `Dockerfile`. The `.env` file is never uploaded, so set the
variables in **Render → your service → Environment**:

| Variable | Required | Notes |
|---|---|---|
| `MONGO_URI`, `MONGO_DB_NAME` | Yes | Your MongoDB Atlas connection |
| `SUPERADMIN_EMAIL` | Yes | The one permanent Super Admin |
| `SUPERADMIN_PASSWORD` | Yes | 12+ characters, or a bcrypt hash of it |
| `SESSION_SECRET` | Yes | Long random string; without it everyone is signed out on each restart |
| `SESSION_COOKIE_SECURE` | Yes | `true` (Render serves HTTPS) |
| `TRUST_PROXY_HEADERS` | Yes | `true` (real client IP behind Render's proxy for login limits) |
| `APIFY_API_TOKEN` | For searches | Apify token |
| `GEMINI_API_KEY` | Optional | AI lead analysis (rule-based without it) |
| `PUBLIC_BASE_URL` | Recommended | e.g. `https://your-app.onrender.com`, used in emailed links |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | Optional | Without SMTP, invite and reset links are shown once to the admin who created them |
| `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` | Optional | Real payments; without them the mock checkout is used (a Super Admin must still confirm every subscription) |

Never add admin or user emails/passwords here: those accounts are created in
the app (demo signup → Super Admin approval → invitations).

After a deploy, `https://<your-app>/health` must show
`"superadmin_configured": true`; then sign in at `/login?superadmin=1`.

### Customer journey (who does what)

1. **Visitor** requests a demo on the website with name, email, company and a
   password (stored bcrypt-hashed; status `pending`, no access yet).
2. **Super Admin** approves or rejects it in `/superadmin#/demo`. Approval
   activates the account and grants the configured demo tokens, duration,
   limits and features (Super Admin → Demo configuration).
3. **Customer** signs in with the credentials from step 1 and uses LeadAI in
   the User Portal within the demo limits.
4. **Customer** picks a plan and checks out. Payment only moves the
   subscription to *awaiting confirmation* — nothing is activated yet.
   An Admin can set the organization's **industry and business profile** at
   any time (Admin Portal → Organization → Business profile); every search's
   lead analysis uses it.
5. **Super Admin** confirms the subscription; only then is the organization
   active and its Admin Portal (`/org-admin`) enabled for the customer, who is
   the organization's owner/Admin with the same credentials.
6. **Admin** invites users (one-time links; each user sets their own
   password). The plan's user limit is enforced on the server.
7. **Super Admin** manages everything: organizations, plans and limits, demo
   settings, account status, sign-in email, member roles and password resets
   (one-time links — passwords are never shown or stored in plain text). All
   changes are audited.

---

## 28. Authentication & Authorization

### Three-Tier Auth

| Scope | Login URL | Credential Source | Access |
| --- | --- | --- | --- |
| **Site** (org admins, managers, users, viewers) | `/login` | Database accounts (`users` + `organization_members`), created by signup / invitation | User portal (`/`, `/api/*`, `/api/me/*`) and Org Admin portal (`/org-admin`, `/api/org-admin/*`) |
| **Super Admin** | `/login?superadmin=1` | `SUPERADMIN_EMAIL` + `SUPERADMIN_PASSWORD` environment variables (permanent) | Super Admin portal (`/superadmin`) and platform console (`/admin`) |
| **Platform staff** | `/login?admin=1` | `admin_users` collection / `users.platform_role` (created by the Super Admin) | Platform console (`/admin`) by role |

### Portal Map

| Portal | URL | Who | HTML | API Prefix |
| --- | --- | --- | --- | --- |
| **User Portal** | `/` or `/dashboard` | Any signed-in site user | `index.html` + `app.js` | `/api/*`, `/api/me/*` |
| **Org Admin Portal** | `/org-admin` | Org owner/admin (portal enabled) | `org-admin.html` + `org-admin.js` | `/api/org-admin/*` |
| **Platform Console** | `/admin` | Platform staff (viewer/manager) + Super Admin | `admin.html` + `admin.js` | `/api/admin/*` |
| **Super Admin Portal** | `/superadmin` | `SUPERADMIN_*` environment account | `super-admin.html` + `super-admin.js` | `/api/super-admin/*` |
| **Public Website** | `/website`, `/signup`, `/contact` | Anyone (no auth) | `website.html`, `signup.html`, `contact.html` | `/api/public/*` |

### Public Pages (No Auth Required)

| Page | URL | Description |
| --- | --- | --- |
| Website | `/website` | Marketing landing page |
| Signup | `/signup` | Demo request (account stays `pending` until the Super Admin approves it) |
| Contact | `/contact` | Contact form |
| Features | `/features` | Alias for website |
| Pricing | `/pricing` | Alias for website |
| About | `/about` | Alias for website |
| FAQ | `/faq` | Alias for website |
| Privacy | `/privacy` | Alias for website |
| Terms | `/terms` | Alias for website |
| Health | `/health` | Health check endpoint |

### Roles

| Role | Permissions |
| --- | --- |
| **viewer** | Read-only access to all admin views |
| **manager** | Full access except user management, job/lead deletion, session revocation |
| **super_admin** | Full access including user CRUD, deletion, session revocation — only the `SUPERADMIN_*` environment account; no database account can hold it |

### Security Features

- bcrypt password hashing (cost 12) with automatic SHA-256 migration
- Session cookies (httpOnly, SameSite, configurable secure flag)
- Per-IP brute-force throttle (5 failed attempts → lockout)
- Session epoch for revocation (increment to invalidate all cookies)
- Environment variable lock/unlock with password; `SUPERADMIN_*`, `SESSION_SECRET`, `SMTP_HOST/USER/PASSWORD` and `STRIPE_SECRET_KEY/WEBHOOK_SECRET` are **environment-only** (no in-app override can replace them)
- An org Admin can reset only the passwords of people who belong to that organization alone (never platform staff or members of other organizations); platform staff change their own password only with the current one
- One-time reset / invitation links are never stored: the `email_outbox` log keeps a redacted copy
- Only an org owner/admin can turn on the shared workspace (data visibility)
- Audit logging of all admin actions
- Security headers: CSP, HSTS, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, Permissions-Policy

### Lifecycle automation (`app/lifecycle/maintenance.py`)

A background sweep (every 15 minutes, started with the app) keeps time-driven state in sync across all portals:

| Event | What happens |
| --- | --- |
| Subscription with *cancel at period end* reaches its period end | Subscription `cancelled`, organization `cancelled`; org admins (email) and Super Admin notified |
| Period ended 3+ days ago without renewal | Subscription `past_due`; org admins (email) and Super Admin notified once; the Super Admin can *Extend* (which also extends the plan tokens and makes it active again) |
| Stripe `invoice.paid` renewal (`billing_reason = subscription_cycle`) | Next period starts, the plan's monthly tokens are re-granted, invoice recorded, org admins notified |
| Demo ends within 2 days / has ended | `demo_expiring` / `demo_expired` sent once (in-app + email) |

Other synchronization rules: *Activate* on a suspended organization restores its previous state (a demo stays a demo; only an organization with an active subscription becomes `active`); plan changes grant the new plan's tokens; token top-ups re-arm usage warnings (and revive an expired balance); a request refused for lack of tokens raises the "limit reached" alert; cancelling a pending demo also closes the account; extending a demo never lifts a suspension; every Super Admin subscription decision (reject after payment → refund alert, suspend, resume, expire, cancel) is sent to the organization's admins.

---

## 29. Cost and Resource Usage

```text
Page Details    → Apify actor usage
    ↓
Post Scraping   → Apify actor usage
    ↓
Comment Scraping→ Apify actor usage
                 — gated by MIN_COMMENTS (only qualifying posts)
    ↓
Keyword Filter  → Free (offline, CPU only)
    ↓
AI Analysis     → Gemini API usage (per comment, only MATCHED/NO_FILTER)
```

- **Apify**: Actors consume credits/usage. Cost minimized: only qualifying posts are comment-scraped.
- **Gemini**: Billed per request/token — each analyzed comment is one call. Keyword filter saves cost by pre-filtering.
- **MongoDB**: No cost for local/docker usage.

---

## 30. Security

### Implemented

- Admin authentication (bcrypt, session cookies, brute-force throttle)
- Role-based access control (viewer / manager / super_admin)
- Security headers middleware (CSP, HSTS, X-Frame-Options, etc.)
- CSRF protection (Origin/Referer validation on state-changing requests)
- Input validation (Pydantic models, URL canonicalization, ObjectId validation)
- HTML escaping in frontend (`esc()` in `app.js` and `admin.js`)
- CORS (configurable origins, default same-origin only)
- Error messages never leak tokens or stack traces
- Audit logging of admin actions with secret redaction
- Environment variable lock/unlock with password protection
- **Log secret redaction** — API keys, tokens, passwords, MongoDB URIs, and bearer tokens are automatically redacted in all log output (`app/log_parser.py`)
- **Multi-tenant data isolation** — All customer data is organization-scoped
- **Invitation token hashing** — Cryptographically secure SHA-256 hashed tokens with expiration
- **Server-side tracked sessions & idle timeout** — sliding-window session expiration (`SESSION_IDLE_TIMEOUT_MINUTES`, default 30m) with server-side revocation in `user_sessions`
- **Sensitive field stripping** — `strip_sensitive()` auditable chokepoint automatically purges credentials, password hashes, and provider tokens from all user-facing API payloads
- **In-memory rate limiting** — Per-user API throttling (30 requests/minute), per-IP contact form throttling (1 submission/60 seconds), and brute-force login throttle (5 consecutive failures → lockout)
- **Automatic audit log retention** — Configurable MongoDB TTL index (`AUDIT_RETENTION_DAYS`, default 365 days) automatically purges aged audit trails

### Production Hardening Recommendations

- **Distributed Rate Limiting** — Replace the single-process in-memory rate limiter with a Redis-backed token bucket when scaling beyond a single worker node
- **Secret Management** — Integrate cloud secret managers (AWS Secrets Manager, GCP Secret Manager, or HashiCorp Vault) for zero-trust runtime secret injection
- **TLS Termination** — Terminate TLS/HTTPS via a hardened reverse proxy (Caddy, Nginx, Cloudflare, or Render managed TLS) with HSTS preload
- **Field-Level Encryption (FLE)** — Implement MongoDB Client-Side Field Level Encryption (CSFLE) for sensitive contact PII (phone/email)

---

## 31. Performance

- **Async API layer** — Motor asynchronous database drivers; compute-heavy operations dispatched via `asyncio.to_thread` and managed daemon tasks
- **Background jobs with live status** — In-process tasks keyed by run/page/post eliminate redundant concurrent executions
- **Dedicated DB indexes** — Unique, compound, and TTL indexes across all active collections (38 active collections); idempotent `_create_index_safe()` wrapper handles IndexKeySpecsConflict on restart
- **Provider-side timeouts** — 8-minute Apify execution timeout safeguards; httpx connect/read timeouts for Google Gemini
- **Scrape limits** — Hard `resultsLimit` on every actor; posts capped at `max_posts`, comments capped per post
- **Cost-aware gating** — Only qualifying posts (relevant + comment count >= `min_comments`) are comment-scraped
- **Result caching & deduplication** — Social pages, posts, and comments are stored once and served from MongoDB with per-run deduplication
- **Frontend polling** — Responsive polling (1.5s during scraping runs, 2–4s for background jobs); stops immediately when completed
- **API rate limiting** — 30 requests per minute per user (in-memory sliding window)
- **Configurable log levels** — Suppress noisy third-party loggers; control file/console verbosity via env vars

---

## 32. Logging and Monitoring

### Configuration

Three environment variables control log verbosity (default: `INFO` for all):

| Variable | Default | Effect |
|----------|---------|--------|
| `LOG_LEVEL` | `INFO` | Root logger level |
| `LOG_FILE_LEVEL` | `INFO` | File handler level |
| `LOG_CONSOLE_LEVEL` | `INFO` | Console handler level |

Third-party loggers (pymongo, motor, urllib3, httpcore) are **always forced to WARNING** regardless of configuration — this prevents DEBUG flood from database drivers.

### Handlers

| Handler | Format | Rotation |
|---------|--------|----------|
| **Console** | `HH:MM:SS LEVEL  name: message` | N/A |
| **File** (`logs/app.log`) | `HH:MM:SS LEVEL  name: message` | 10 MB × 5 files (RotatingFileHandler) |

### Structured Log Parser (`app/log_parser.py`)

Raw log lines are parsed into structured dicts with:

| Field | Description |
|-------|-------------|
| `timestamp` | `HH:MM:SS` |
| `level` | `DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL` |
| `module` | Logger name (e.g. `app.pipeline.comment_ai`) |
| `source` | Category: Database, Apify, AI, Search, Authentication, System, API, Application |
| `event` | Concise event summary (first sentence, max 120 chars) |
| `message` | Full message with secrets redacted |
| `error_type` | Exception class if present |
| `run_id`, `request_id`, `job_id` | Correlation IDs |
| `endpoint`, `http_method`, `http_status` | HTTP context |
| `duration`, `collection`, `platform` | Additional structured fields |
| `stack_trace` | Grouped multiline tracebacks |
| `raw` | Original line with secrets redacted |

### Secret Redaction

Sensitive values are **automatically redacted** in all log output:

- API keys (`APIFY_API_TOKEN`, `GEMINI_API_KEY`)
- Passwords and tokens (`password=`, `token=`, `SESSION_SECRET`)
- MongoDB URIs (`mongodb://admin:***@host`)
- Bearer tokens (`Authorization: Bearer ***`)
- Dict keys matching `password`, `token`, `secret`, etc.

### Admin Log Viewer

The admin panel (`/admin` → Logs) provides a production-grade log console:

- **Structured table** with TIME, LEVEL, MODULE, MESSAGE, SOURCE columns
- **Severity badges**: CRITICAL (red), ERROR (red), WARNING (amber), INFO (blue), DEBUG (gray)
- **Source badges**: Database (blue), Apify (teal), AI (violet), Search (green), Authentication (amber)
- **Summary cards** with real-time level counts
- **Search** across all log fields
- **Filters**: level, source, module dropdowns
- **Details modal** on row click with all structured fields, full message, stack trace, raw log
- **Copy**: JSON, raw log, or message to clipboard
- **Auto-refresh**: Off / 10s / 30s / 60s intervals
- **Autoscroll** with "Jump to latest" bar
- **Pagination** with "Load more"
- **CSV export** of filtered logs

### Docker Log Rotation

```yaml
logging:
  driver: json-file
  options:
    max-size: "10m"
    max-file: "3"
```

---

## 33. Testing

```bash
# All tests (pytest.ini limits collection to tests/)
python -m pytest -v

# Log parser tests only
python -m pytest tests/test_log_parser.py -v

# HTTP/API endpoint tests only
python -m pytest tests/test_http_endpoints.py -v

# Quick summary
python -m pytest -q
```

**1,400 tests collected across 39 test files** (`pytest --collect-only`):

| Test File | Tests | Coverage Focus |
| :--- | :---: | :--- |
| `tests/test_ai_intelligence.py` | 135 | Dual-stage AI analysis, prompt templating, Hinglish/currency parsing, rule fallbacks |
| `tests/test_log_parser.py` | 117 | Structured log parser, multiline stack traces, secret redaction, log streaming |
| `tests/test_step3_verification.py` | 86 | End-to-end multi-tenant flows, invitations, role boundaries, tenant scoping |
| `tests/test_analytics_export.py` | 84 | Time-series aggregations, CSV generation, CSV sanitization, tenant isolation |
| `tests/test_http_endpoints.py` | 80 | Full OpenAPI endpoints response codes, headers, and parameter validation |
| `tests/test_lead_lifecycle.py` | 76 | Lead state machine (new → contacted → qualified → converted), history, notes |
| `tests/test_admin_panel_hardening.py` | 73 | Admin input validation, injection protection, actor override constraints |
| `tests/test_post_normalization.py` | 63 | Social post normalization across FB/IG/YT/LI, caption parsing, engagement |
| `tests/test_scalability_production.py` | 54 | Concurrency, connection pool boundaries, graceful task shutdowns, rate limits |
| `tests/test_url_search.py` | 53 | URL detector canonicalization, platform error classification, fallback pages |
| `tests/test_step1_security.py` | 49 | Authentication gates, password policy, CSRF, security headers, XSS protections |
| `tests/test_improvements_user.py` | 44 | User portal search presets, URL search runs, lead dossier, self-service profile |
| `tests/test_comment_filter.py` | 33 | Comment filter modes (all/preset/custom), keyword matching, regex rules |
| `tests/test_improvements_org.py` | 31 | Org admin overview metrics, team member permissions, keyword library |
| `tests/test_security.py` | 29 | Cookie HMAC signatures, brute force protection, lockout escalation |
| `tests/test_user_portal.py` | 27 | User portal workflows, quota enforcement, search isolation |
| `tests/test_org_admin.py` | 26 | Org Admin Portal: access control, tenant data isolation, CSV exports, tickets |
| `tests/test_super_admin_portal2.py` | 26 | Super Admin extended lifecycle: demo approvals, token quotas, org status |
| `tests/test_improvements_platform.py` | 25 | Platform admin operations, Apify actor overrides, system settings |
| `tests/test_superadmin_portal.py` | 25 | Super Admin Portal: global dashboard, org lifecycle, impersonation exits |
| `tests/test_lead_quality.py` | 23 | Deterministic scoring formula, priority levels, Hot/Warm/Cold thresholds |
| `tests/test_envvars.py` | 22 | Three-layer environment variable management, DB overrides, env panel |
| `tests/test_audit_fixes.py` | 21 | Regression audit verification, edge case sanitization, error resilience |
| `tests/test_auth.py` | 20 | Login flows, password hashing with bcrypt, session token generation |
| `tests/test_saas_billing_entitlements.py` | 19 | SaaS billing plans, quota meters, entitlement rejection on limit |
| `tests/test_public_website2.py` | 17 | Public CMS website endpoints, dynamic pricing/FAQ fetch, contact rate limits |
| `tests/test_settings.py` | 17 | System settings registry CRUD, in-memory TTL caching, change history |
| `tests/test_admin_panel.py` | 16 | Platform Console views, job cancellation, platform toggles |
| `tests/test_qualification.py` | 16 | Lead qualification rules, comment relevance thresholds, contact extraction |
| `tests/test_saas_multitenancy.py` | 15 | Multi-tenant organization scoping, cross-tenant access rejection |
| `tests/test_step2_integration.py` | 15 | Integrated customer workflow: URL search → comment scrape → qualification |
| `tests/test_search_isolation.py` | 14 | Cross-tenant search history isolation and query validation |
| `tests/test_industry_context.py` | 13 | 22-industry catalog, business context prompt injection, custom categories |
| `tests/test_superadmin_env.py` | 9 | Super Admin environment variables & secret management |
| `tests/test_final_flow.py` | 9 | End-to-end acceptance verification across all four user portals |
| `tests/test_phase1_perf_hardening.py` | 7 | Compression middleware, immutable asset caching, HTML ETag, batch config |
| `tests/test_e2e_lifecycle.py` | 4 | Complete lifecycle: demo request → approval → search → lead transition |
| `tests/test_improvements_integration.py` | 4 | Real-time event notifications, email outbox queuing |
| `tests/test_admin_data.py` | 3 | Scraped pages, posts, and comments admin data endpoints |

---

## 34. Troubleshooting

| Issue | Solution |
| --- | --- |
| URL search returns no results | Check run's `error_meta` / logs; `BLOCKED` → wait and retry; `ACTOR_FAILED` → check Apify console |
| Page details fail but run works | Graceful fallback creates URL-derived page doc; `details_error` explains why |
| AI analysis fails | Without `GEMINI_API_KEY` runs on rules; 429 → circuit breaker pauses 10 min |
| Database connection fails | Check Mongo is running; verify `MONGO_URI` |
| Frontend cannot connect | Must be served from same origin as API (FastAPI static mount does this) |
| Runs stuck in "running" | Stale after 30 min; UI shows Retry button |
| Maintenance mode active | Admin panel still accessible; user app shows maintenance page |
| Logs flooded with DEBUG noise | Third-party loggers forced to WARNING; set `LOG_LEVEL=INFO` |
| Admin panel stuck on loading | Check browser console for JS errors; verify `/api/admin/logs` returns structured data |
| Index creation crash on restart | `_create_index_safe()` handles IndexKeySpecsConflict automatically |

---

## 35. Developer Guide

### Adding a New Platform

1. `app/social/url_detector.py` — add host aliases, canonicalizer, platform in `SUPPORTED_PLATFORMS`
2. `app/social/scrapers.py` — subclass `SocialMediaScraper`, register in `get_scraper()`
3. `app/config.py` — add `<PLATFORM>_ACTOR_ID` setting

### Adding a New Lead Field

1. Add to model in `app/db/models.py`
2. Extract in `app/pipeline/comment_ai.py`
3. Expose in `app/api/routes/search.py` (GET endpoint + CSV columns)
4. Render in `app/static/app.js` (card + modal)

### Adding a New API Endpoint

1. Add route in `app/api/routes/search.py` or `admin.py`
2. Use `_start(key, fn)` for background work
3. Use `get_async_db()` / `get_sync_db()` for DB access
4. Use `_serialize()` for responses

### Adding a New Admin View

1. Add view definition in `app/settings/registry.py` (`KNOWN_VIEWS`)
2. Add API endpoint in `app/api/routes/admin.py`
3. Add render function in `app/static/admin.js`
4. Add nav item in sidebar

### Adding a New CMS Content Type

1. Add collection definition in `app/cms/models.py`
2. Add CRUD service methods in `app/cms/service.py`
3. Add admin API routes in `app/api/routes/admin_cms.py`
4. Add public API route in `app/api/routes/public_website.py`
5. Add admin view in `app/static/admin.js`

### Adding a New SaaS Feature

1. Add data model in `app/db/saas_models.py`
2. Add service logic in `app/billing/` or new module
3. Add API routes in `app/api/routes/billing.py` or new router
4. Add admin management view in `app/static/admin.js`
5. Add entitlement check in `app/billing/entitlements.py`

---

## 36. Git Workflow

```bash
git checkout main && git pull origin main
git checkout -b feature/new-feature
git add . && git commit -m "Add new feature"
git push origin feature/new-feature
# Open pull request against main
```

Keep `.env` and `logs/` out of commits (gitignored).

---

## 37. Architecture Summary

```text
USER
  ↓
PORTALS
  ├── USER PORTAL    (/dashboard — app/static/index.html + app.js)
  │     └── /api/me/* (self-service: profile, usage, searches, leads, exports)
  ├── ORG ADMIN      (/org-admin — app/static/org-admin.html + org-admin.js)
  │     └── /api/org-admin/* (org-scoped dashboard, team, leads, exports, audit, support)
  ├── PLATFORM CONSOLE (/admin — app/static/admin.html + admin.js)
  │     └── /api/admin/* (30+ views: dashboard, jobs, leads, analytics, platforms, AI, settings, users, CMS)
  ├── SUPER ADMIN    (/superadmin — app/static/super-admin.html + super-admin.js)
  │     └── /api/super-admin/* (global dashboard, orgs, users, plans, demo queue, tokens, industries, impersonation)
  └── PUBLIC WEBSITE (/website — website.html + signup.html + contact.html)
        └── /api/public/* (config, theme, pricing, FAQ, contact, industries)
  ↓
DESIGN SYSTEM (tokens.css + components.css + theme.js)
  ↓
AUTH (app/auth/ — bcrypt, sessions, roles, permissions, tenant context, superadmin env validation)
  ↓
BACKEND (FastAPI — app/api/routes/)
  ├── Product Routes (search, pages, posts, comments, leads, exports)
  ├── Me Routes (self-service: profile, usage, searches, leads, exports)
  ├── Admin Routes (dashboard, jobs, leads, analytics, platforms, AI, settings, users)
  ├── Org Admin Routes (org-scoped: overview, team, searches, leads, rules, exports, audit, support)
  ├── Super Admin Routes (global: dashboard, orgs, users, plans, lifecycle, platform)
  ├── CMS Routes (pages, FAQ, testimonials, navigation, media, contact)
  ├── Organization Routes (workspace, team, invitations)
  ├── Billing Routes (plans, subscription, usage, invoices, webhooks)
  ├── Notifications Routes (in-app notification list/read)
  └── Public Routes (config, theme, pricing, FAQ, contact, industries)
  ↓
URL SEARCH PIPELINE (app/social/url_search.py — background thread)
  ↓
URL DETECTOR (platform detection + canonicalization)
  ↓
PLATFORM SCRAPER (app/social/scrapers.py)
  ↓
APIFY ACTORS (pages / posts / comments + configurable IG/YT/LI)
  ↓
LEAD ENGINE (app/agent/search.py — normalizers + qualification + scoring)
  ↓
KEYWORD FILTER (app/pipeline/comment_filter.py — 4 modes, 10+ categories)
  ↓
AI PIPELINE (app/pipeline/comment_ai.py: rules → Gemini)
  ├── AI Models Service (model registry, pricing, token limits)
  ├── AI Prompt Service (version-controlled prompts with rollback)
  └── AI Usage Service (cost tracking per request)
  ↓
LEAD LIFECYCLE (app/pipeline/lead_lifecycle.py — state machine, notes, follow-ups)
  ↓
SaaS LAYER
  ├── Organizations (multi-tenant workspaces)
  ├── Subscriptions (plans, trials, upgrades)
  ├── Usage Tracking (atomic quota counters)
  ├── Entitlements (feature flags, quota enforcement)
  ├── Invitations (team invites with token hashing)
  ├── Invoices (billing history)
  └── Lifecycle Sweeper (app/lifecycle/maintenance.py — 15-min: periods, cancellations, demo expiry)
  ↓
EVENTS (app/events/ — email delivery, notification dispatch, security events)
  ↓
CMS SERVICE (app/cms/service.py — pages, FAQ, testimonials, navigation, media)
  ↓
MONGODB (38 collections)
  ↓
API RESPONSE (serialized docs, live status)
  ↓
USER PORTAL  (search → pages → posts → comments/leads → lead modal → CSV · workspace/team/billing modals · /api/me/* dashboard)
ORG ADMIN    (overview → team → searches → leads → lead rules → business profile → data → analytics → billing → exports → audit → support)
PLATFORM CONSOLE (dashboard → jobs → leads → analytics → platforms → AI → settings → users → CMS → plans → subscriptions)
SUPER ADMIN  (dashboard → orgs → users → subscriptions → plans → demo queue → payments → tokens → industries → health → integrations → impersonation)
PUBLIC       (hero → features → how-it-works → pricing → FAQ → CTA → footer)
  ↓
USER
```

LeadAI is a complete, working SaaS lead-intelligence platform: paste one social URL, and real data flows from the platform's Apify actors through keyword filtering, deterministic and AI-based lead qualification, lifecycle management, persistent storage with per-run deduplication, and a live dashboard that takes you from a single page to a scored, exportable list of buyers and sellers — all backed by four distinct portals (User, Org Admin, Platform Console, Super Admin), a CMS-managed public website, multi-tenant SaaS infrastructure with subscription plans, team management, usage-based billing, and in-app notifications.
