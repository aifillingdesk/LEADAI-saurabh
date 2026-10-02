# LeadAI REST API Reference

LeadAI provides two tiers of RESTful APIs:
1. **Public REST API v1 (`/api/v1/*`)**: For external automation, Zapier, Make, and CRM integrations using scoped API keys.
2. **Internal SaaS Application API (`/api/*`, `/api/org-admin/*`, `/api/super-admin/*`)**: For the LeadAI web application, authenticated via HTTP-only secure session cookies.

---

## 1. Public REST API v1

### Authentication
Include your API key in the `X-API-Key` request header or `Authorization: Bearer <API_KEY>`.
API keys have prefixes:
- `lai_live_...` for production data.
- `lai_test_...` for test sandbox environments.

All API keys are SHA-256 hashed at rest and evaluated for per-org rate limits (default 60 requests per minute).

### Scopes
- `leads:read`: Query leads and export prospect details.
- `leads:write`: Ingest external leads or update lead statuses.
- `search:create`: Trigger URL searches and background scrapes.
- `search:read`: Check status and retrieve search run reports.
- `*`: Full programmatic access.

### Endpoints

#### `GET /api/v1/leads`
Retrieve leads for the organization.
- **Scope required**: `leads:read`
- **Query Parameters**:
  - `limit`: int (default 50, max 100)
  - `platform`: string (optional: `facebook`, `instagram`, `youtube`)
  - `priority`: string (optional: `high`, `medium`, `low`)
- **Response**:
```json
{
  "success": true,
  "total": 42,
  "leads": [
    {
      "id": "60c72b2f9b1d8b2bad7c9099",
      "platform": "youtube",
      "author_name": "Jane Doe",
      "author_url": "https://youtube.com/@janedoe",
      "text": "How much for the 3-bedroom unit in Bandra?",
      "phone": "+919876543210",
      "email": "jane@example.com",
      "lead_score": 85,
      "lead_quality": "hot",
      "intent": "pricing_inquiry",
      "created_at": "2026-10-02T10:00:00Z"
    }
  ]
}
```

#### `POST /api/v1/leads`
Ingest or sync an external lead into the organization's CRM pipeline.
- **Scope required**: `leads:write`
- **Request Body**:
```json
{
  "author_name": "Alex Smith",
  "text": "Looking for commercial office space in Bangalore",
  "platform": "youtube",
  "phone": "+919876500000",
  "email": "alex@example.com",
  "budget": "50L",
  "intent": "purchase_inquiry",
  "priority": "high"
}
```

#### `POST /api/v1/search`
Start a URL scraping and lead discovery job.
- **Scope required**: `search:create`
- **Request Body**:
```json
{
  "url": "https://youtube.com/@examplechannel",
  "max_posts": 20,
  "max_comments_per_post": 30
}
```

#### `GET /api/v1/search/{run_id}`
Check status and metrics of a search run.
- **Scope required**: `search:read`

---

## 2. Workspace Product API (`/api/*`)

Requires an authenticated tenant session cookie.

### Search & Scraping
| Method | Endpoint | Description | Permission |
|---|---|---|---|
| `POST` | `/api/url/search` | Launch a single URL-based scraping run | `search.create` |
| `POST` | `/api/search/bulk` | Launch concurrent searches for a batch of URLs | `search.create` |
| `POST` | `/api/search/scheduled` | Create a recurring scheduled scan | `search.create` |
| `GET` | `/api/search/scheduled` | List active scheduled scans for the organization | `search.view` |
| `DELETE` | `/api/search/scheduled/{id}` | Cancel and delete a scheduled scan | `search.create` |
| `GET` | `/api/search/history` | List recent search runs for the workspace | `search.view` |
| `GET` | `/api/search/{run_id}` | Poll status, progress, and discovered pages | `search.view` |
| `POST` | `/api/search/{run_id}/cancel`| Request immediate cancellation of a running search | `search.cancel` |
| `DELETE` | `/api/search/{run_id}` | Delete a search run and all associated data | `search.cancel` |

### Leads & Comments
| Method | Endpoint | Description | Permission |
|---|---|---|---|
| `GET` | `/api/posts/{id}/comments` | Get analyzed comments and leads for a post | `search.view` |
| `GET` | `/api/comments/{id}` | Get lead detail view with AI context | `leads.view` |
| `PATCH` | `/api/leads/{id}` | Update status (`new`, `contacted`, `qualified`, etc.) | `leads.manage` |
| `POST` | `/api/leads/{id}/notes` | Add timestamped team note to lead | `leads.manage` |
| `POST` | `/api/leads/{id}/follow-ups` | Schedule a follow-up reminder | `leads.manage` |
| `POST` | `/api/leads/assign` | Assign lead to an organization member | `leads.assign` |

### Exports
| Method | Endpoint | Description | Permission |
|---|---|---|---|
| `GET` | `/api/export/{scope}.csv` | Export pages, posts, or comments as CSV | `exports.create` |
| `GET` | `/api/leads/export/xlsx` | Export leads as sanitized Excel spreadsheet XML | `exports.create` |

---

## 3. Organization Admin API (`/api/org-admin/*`)

Requires tenant `owner` or `admin` role with active subscription.

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/org-admin/overview` | Organization dashboard stats and activity |
| `GET` | `/api/org-admin/users` | List members with roles, statuses, and usage |
| `POST` | `/api/org-admin/users/{id}/reset-access` | Generate one-time password reset link |
| `GET` | `/api/org-admin/roles` | Configurable role matrix and delegable permissions |
| `POST` | `/api/org-admin/assignment-rules` | Create automated lead assignment rule with SLA |
| `GET` | `/api/org-admin/assignment-rules` | List lead assignment rules |
| `DELETE` | `/api/org-admin/assignment-rules/{id}` | Delete assignment rule |
| `POST` | `/api/org-admin/webhooks` | Register outbound webhook endpoint |
| `GET` | `/api/org-admin/webhooks` | List registered webhooks |
| `DELETE` | `/api/org-admin/webhooks/{id}` | Remove webhook |
| `POST` | `/api/org-admin/api-keys` | Generate new public API key |
| `GET` | `/api/org-admin/api-keys` | List active API keys |
| `DELETE` | `/api/org-admin/api-keys/{id}` | Revoke API key |
| `GET` | `/api/org-admin/audit-logs` | Filterable organization audit trail |

---

## 4. Super Admin Platform API (`/api/super-admin/*`)

Requires internal platform role (`super_admin`, `operations_admin`, etc.).

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/super-admin/overview` | Platform-wide metrics, active orgs, revenue |
| `GET` | `/api/super-admin/organizations` | List all customer organizations |
| `PATCH` | `/api/super-admin/organizations/{id}/status` | Activate, suspend, or archive organization |
| `GET` | `/api/super-admin/plans` | List and edit subscription plan catalog |
| `GET` | `/api/super-admin/unit-economics` | Real-time token and compute margin economics report |
| `GET` | `/api/super-admin/audit-logs` | Platform-wide master audit logs |
