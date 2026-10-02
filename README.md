# LeadAI - AI-Powered Social Lead Generation SaaS

[![CI](https://github.com/your-org/lead_apify/actions/workflows/ci.yml/badge.svg)](https://github.com/your-org/lead_apify/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com)
[![MongoDB](https://img.shields.io/badge/MongoDB-6.0+-47A248.svg)](https://www.mongodb.com)

**LeadAI** is an enterprise-grade multi-tenant B2B SaaS platform that monitors social media (Facebook, Instagram, YouTube), extracts comments from target posts and channels, and uses **Google Gemini AI** to identify high-intent buyer inquiries, extract contact information, and automate lead delivery to your CRM.

---

## Architecture Overview

```
                      +---------------------------------------+
                      |   Client Web Browser / Public API     |
                      +-------------------+-------------------+
                                          |
                                    HTTPS / WSS
                                          |
                      +-------------------v-------------------+
                      |         FastAPI Backend Cluster       |
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

## Key Features

- **Multi-Platform Social Listening**: Scrapes posts, videos, and comments from Facebook Pages, Instagram, and YouTube channels.
- **AI Intent & Lead Classification**: Powered by Google Gemini to identify buyer intent (`purchase_inquiry`, `pricing_inquiry`), budget, location, and contact details (phone, email, WhatsApp).
- **Strict Multi-Tenancy**: Built-in tenant isolation with fail-closed RBAC, scoped queries, and cross-tenant tamper protection.
- **Recurring & Scheduled Scans**: Automatically scan target channels and social profiles on an hourly or daily basis.
- **Cross-Run Lead Deduplication**: Merges duplicate prospect contacts across multiple runs and platforms.
- **Lead Assignment & SLA Timers**: Automated round-robin or criteria-based lead routing with SLA response countdowns.
- **CRM Integrations & Webhooks**: Real-time HMAC-SHA256 signed webhooks, HubSpot and Zoho CRM connectors, and formula-safe Excel (.xlsx) / CSV exports.
- **Developer API**: Scoped Public REST API v1 (`/api/v1/leads`, `/api/v1/search`) with SHA-256 hashed API keys and rate limiting.
- **Dual Billing Gateways**: Razorpay (UPI, NetBanking, Cards) and Stripe support with auto-activation and token quotas.
- **Unit Economics Engine**: Live token costs, scraping compute metrics, and margin tracking per tier.

---

## Administration & User Portals

| Portal | URL | Who Can Access | API Prefix | Description |
|---|---|---|---|---|
| **Platform Console** | `/superadmin` | Super Admin & Platform Staff (`super_admin`, `operations_admin`, etc.) | `/api/super-admin` | Multi-tenant platform management, global configs, billing plans, audit trail, unit economics. |
| **Organization Admin Portal** | `/org-admin` | Customer Org Owners & Admins (`owner`, `admin`) with confirmed subscription | `/api/org-admin` | Member management, RBAC, assignment rules, webhooks, API keys, org audit logs. |
| **Workspace Dashboard** | `/dashboard` (or `/`) | All verified organization members (`owner`, `admin`, `manager`, `member`, `viewer`) | `/api` | Scrape execution, social lead pipeline, CRM views, note taking, follow-up reminders. |
| **Public Landing & Pricing** | `/`, `/website`, `/pricing` | Public / Anonymous | `/api/public` | Product marketing, pricing tiers, self-service organization signup, demo requests. |

For detailed portal documentation, see [docs/ADMIN_PORTALS.md](docs/ADMIN_PORTALS.md).

---

## Quick Start

### 1. Prerequisites
- **Python**: 3.11+ (recommended 3.12)
- **MongoDB**: 6.0+ (local instance or MongoDB Atlas)
- **Redis**: 7.0+ (optional; in-memory fallback enabled if omitted)
- **Apify API Token**: [https://apify.com](https://apify.com)
- **Google Gemini API Key**: [https://aistudio.google.com](https://aistudio.google.com)

### 2. Installation
```bash
# Clone repository
git clone https://github.com/your-org/lead_apify.git
cd lead_apify

# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Configure Environment
```bash
cp .env.example .env
```
Edit `.env` with your credentials:
```ini
ENV=development
SECRET_KEY=change_this_to_a_secure_random_string_in_production
MONGODB_URI=mongodb://localhost:27017/leadai
GEMINI_API_KEY=your_gemini_api_key
APIFY_API_TOKEN=your_apify_token
```

### 4. Run Locally
```bash
# Start the web server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
Open your browser at [http://localhost:8000](http://localhost:8000).

### 5. Run Tests
```bash
# Run unit and integration test suite
pytest

# Run linter
ruff check .
```

---

## Detailed Documentation

Comprehensive documentation is organized in the [`docs/`](docs/) directory:

- [System Architecture](docs/ARCHITECTURE.md) - High-level topology, queue service, and AI pipeline.
- [REST API Reference](docs/API.md) - Public API v1 and internal endpoints.
- [Portals Guide](docs/ADMIN_PORTALS.md) - Platform Console, Org Admin, and User Dashboard.
- [Production Deployment](docs/DEPLOYMENT.md) - Docker, Nginx, environment variables, and scaling.
- [Security Controls](docs/SECURITY.md) - RBAC, session management, tamper protection, and injection defense.
- [Compliance & Data Privacy](docs/COMPLIANCE.md) - PII retention, data subject deletion, and blocklists.
- [Unit Economics](docs/UNIT_ECONOMICS.md) - Token costs, compute expenses, and pricing margins.
- [Disaster Recovery](docs/DISASTER_RECOVERY.md) - MongoDB backup, restore runbook, and failover steps.
- [Performance Report](docs/PERFORMANCE_REPORT.md) - Load time benchmarks and caching optimizations.
- [Product Roadmap](docs/ROADMAP.md) - Feature implementation statuses and upcoming milestones.
- [Known Limitations](docs/KNOWN_LIMITATIONS.md) - Architecture boundaries, defaults, and platform limits.
- [Upgrade Notes](docs/UPGRADE_NOTES.md) - Migration guides and breaking changes.

---

## Contributing & License

- **Contributing**: Please review [CONTRIBUTING.md](CONTRIBUTING.md) for code standards and pull request workflows.
- **Changelog**: Detailed release notes are tracked in [CHANGELOG.md](CHANGELOG.md).
- **License**: Released under the [MIT License](LICENSE).
