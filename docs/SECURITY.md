# LeadAI Security Architecture & Controls

Security is a foundational design principle across all layers of the LeadAI platform. This document outlines the defensive measures and verification controls implemented throughout the codebase.

---

## 1. Authentication & Session Security

- **Signed Session Cookies**: Sessions use HMAC-SHA256 signatures (`itsdangerous`) with `HttpOnly`, `SameSite=Lax`, and `Secure` (in production) flags.
- **Durable Session Tracking**: Sessions are tracked in `user_sessions` with SHA-256 hashed tokens. Revoking a session or suspending a user invalidates all active sessions across devices immediately.
- **Single-Use Tokens**: Email invitations, verification links, and password reset requests generate cryptographically random 256-bit secrets. Only the SHA-256 hash is stored in the database; tokens are strictly single-use and bound to time-limited expiry windows (default 24 hours).
- **Brute Force Protection**: Failed login attempts trigger per-IP and per-account rate limits with progressive delays.

---

## 2. Multi-Tenant Isolation & Tamper Resistance

LeadAI enforces tenant boundaries at the database access layer, preventing unauthorized cross-tenant read or write access:
- **Tenant Context (`TenantContext`)**: Every request resolves the tenant context and verifies active membership in the organization.
- **Query Scoping (`scope_query`)**: Database queries automatically inject `organization_id: ctx.organization_id`.
- **Fail-Closed ID Verification (`find_scoped_or_404`)**: When an ID does not belong to the calling tenant, the system does NOT return 200 or reveal document existence. It returns a generic `404 Not Found` and records an immediate security event (`cross_tenant_access`) with the actor's IP, email, and target resource ID.
- **User-Level Scoping**: Non-admin members (`member`, `viewer`) can only access their own created records or leads specifically assigned to them (`cross_user_access` monitoring).

---

## 3. Public API Key Cryptography

- **Hash-at-Rest**: API keys (`lai_live_...`, `lai_test_...`) are shown only once at creation time. Only their SHA-256 hash is persisted in the `api_keys` collection.
- **Scope Enforcement**: Every API route validates that the key possesses the required scope (`leads:read`, `leads:write`, `search:create`).
- **Timing-Safe Checks**: Comparisons of webhook signatures and authentication tokens use `hmac.compare_digest` to prevent timing attacks.

---

## 4. Prompt Injection & LLM Guardrails

When feeding scraped comments and captions to Google Gemini for buyer intent classification:
- **Untrusted Data Boundary**: Scraped text is wrapped within explicit boundary delimiters (`<untrusted_user_content>`).
- **System Instructions**: The system prompt instructs the model to treat all user comments exclusively as unstructured text data and ignore instructions, role manipulations, or prompts contained within them.
- **Strict Output Validation**: The model response is parsed strictly against a validated Pydantic JSON schema. If the model output violates the schema or includes unexpected attributes, the comment is rejected safely.

---

## 5. Formula Injection Defense (CSV & Excel)

Exporting user-generated content to spreadsheets poses a risk of CSV/formula injection (attacks where cells starting with `=`, `+`, `-`, or `@` execute arbitrary commands in Microsoft Excel or Google Sheets).
- **Sanitization Engine**: `_sanitize_csv_value` in `app/api/routes/search.py` and `ExcelExportService` in `app/services/crm_connectors.py` inspect every cell.
- Any text starting with dangerous spreadsheet operators (`=`, `+`, `-`, `@`, `\t`, `\r`) is automatically prefixed with a single quote (`'`), ensuring spreadsheet software parses the cell strictly as literal text.
