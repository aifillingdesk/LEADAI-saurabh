# Prompt: plans where the customer brings their own Apify / Gemini keys, or LeadAI provides them

You are working in the LeadAI repository (FastAPI + MongoDB backend in `app/`, vanilla-JS portals in `app/static/`). Build the feature below **completely, end to end**, reusing the existing systems. Do not create a second billing, plan, settings or audit system.

## 1. What the business wants

LeadAI uses two paid external APIs:
- **Apify**, to scrape posts and comments.
- **Google Gemini**, to classify comments into leads.

Today LeadAI pays for both and includes them in every plan. Customers should be able to choose, **per API**, who provides it:

| Option | Apify | Gemini | Price |
|---|---|---|---|
| All included | LeadAI | LeadAI | highest |
| Own Apify | customer's key | LeadAI | lower |
| Own Gemini | LeadAI | customer's key | lower |
| Bring both | customer's key | customer's key | lowest |

- Who chooses: the organization **owner/admin**, and a **new customer** while signing up or choosing a plan.
- The price changes with the choice. Every API that LeadAI provides adds its own amount to the plan price; every API the customer brings removes it.
- LeadAI can provide **one API or both**, whatever the customer picks. The customer can change the choice later.

## 2. Existing work to build on (and fix)

A first draft exists and is not committed. Read it before you start; keep what is right and replace what is wrong:

- **`app/services/tenant_api_keys.py`:** the per-organization config and the key resolvers `get_tenant_apify_token_sync` and `get_tenant_gemini_key_sync`. They are already called from `app/connectors/apify_connector.py`, `app/social/url_search.py` and `app/pipeline/comment_ai.py`.
- **`app/api/routes/org_admin.py`:** `GET/PUT /api/org-admin/integrations/api-keys`, plus `.../test-apify` and `.../test-gemini`.
- **`app/billing/plans.py`:** `price_monthly_byok`, `price_yearly_byok` and `allows_byok` on each plan.
- **Other files the draft changed:** `billing.py`, `organizations.py`, `subscriptions.py`, `saas_models.py`, `org-admin.js`, `app.js`, `website.js`, `index.html`, and `tests/test_byok_api_plans.py`.

Problems in the draft you **must** fix:
1. **Security bug:** `GET /api/org-admin/integrations/api-keys` returns `config`, which contains the customer's **raw** `apify_token` and `gemini_api_key`. API responses must only ever contain masked hints (`mask_key`), never a raw key.
2. **Plaintext storage:** keys are stored unencrypted in `organizations.custom_api_keys`. Encrypt them at rest (section 4).
3. **All or nothing:** `api_mode` is either `platform` or `byok`, and BYOK needs both keys. Replace it with a **separate choice per API** (section 3).
4. **Only two prices per plan.** Replace them with a base price plus a price for each API (section 3).
5. **Wrong plan source:** the GET route reads the plan from `organizations.plan_id`. Use the organization's **effective plan** (`EntitlementService.get_effective_plan`, which accounts for subscription, demo and trial).

## 3. Data model and pricing

**Organization.** Store one choice per API:

```
organizations.api_coverage = {"apify": "leadai" | "own", "gemini": "leadai" | "own"}   # default both "leadai"
organizations.custom_api_keys = {
  "apify":  {"ciphertext": ..., "hint": "apify_…a1b2", "verified": bool, "verified_at": dt, "last_error": str|None},
  "gemini": {"ciphertext": ..., "hint": "AIza…9xyz",  "verified": bool, "verified_at": dt, "last_error": str|None}
}
```

Migrate draft data once, idempotently. `api_mode: "byok"` becomes `own` for each API that has a key.

**Plan.** Each plan has a base price, which is what the customer pays when they bring both keys, plus an add-on for each API that LeadAI provides:

```
plans.price_monthly_base, plans.price_yearly_base            # customer brings both keys
plans.addons = {"apify":  {"monthly": x, "yearly": y},       # added when LeadAI provides Apify
                "gemini": {"monthly": x, "yearly": y}}       # added when LeadAI provides Gemini
plans.allowed_coverage = ["leadai", "own"] per API           # Super Admin may disable "own" on a plan (e.g. Free)
```

`price = base + sum of the add-ons for the APIs LeadAI provides`. Keep `price_monthly` / `price_yearly`, the existing fields everything reads today, equal to the **all-included** price, so nothing that reads them breaks.

Defaults: keep the current all-included prices and split the current gap like this. The Super Admin can change any of them.

| Plan | All included | Base (bring both) | + Apify | + Gemini | Own Apify | Own Gemini |
|---|---|---|---|---|---|---|
| Starter | 49 | 29 | 12 | 8 | 37 | 41 |
| Pro | 149 | 89 | 36 | 24 | 113 | 125 |
| Business | 399 | 239 | 96 | 64 | 303 | 335 |
| Enterprise | 999 | 599 | 240 | 160 | 759 | 839 |

Yearly prices follow the same split. The Free plan has no "own key" options.

Write one pricing function, for example `app/billing/plans.py: price_for(plan, coverage, cycle) -> amount`. Use it everywhere a price is shown or charged: the pricing page, checkout, the subscription record, invoices, renewal, partner commission, partner pricing and coupons. Never calculate the price separately in the browser.

**Subscriptions.** When a subscription is created or renewed, save a snapshot of the coverage it was priced with (`subscriptions.api_coverage`). An invoice must always show the price that was actually charged.

## 4. Key security (required)

- **Encryption:** encrypt keys at rest with Fernet (`cryptography`) using a key from a new env var `API_KEY_ENCRYPTION_KEY`. If that var is unset, fall back to a key derived from `SECRET_KEY`. Add the var to `.env.example`, the env-var registry and `docs/DEPLOYMENT.md`. Decrypt only inside the resolvers.
- **Never show or log a key:** after a key is saved, never return, log, email, audit, export or back it up in readable form. The audit entry records which key changed (`apify` or `gemini`) and who changed it, never the value.
- **Verify before use:** when a key is saved, test it with the existing test functions and store `verified` and `last_error`. A key that fails the test is saved but marked unverified, and the UI says so.
- **Who can change it:** only the organization owner/admin (`require_portal(P.SETTINGS_MANAGE)`) and the Super Admin. A Super Admin "sign in as" session may change it, and the change is audited as made by the Super Admin.
- **Removal:** removing a key is allowed. If that API is set to `own`, searches must fail clearly until a key is added or the choice is switched to `leadai`.

## 5. How the app uses the choice

- **One resolver per API:** `resolve_api_key(org_id, "apify" | "gemini") -> (key, source)`. Background workers, scheduled scans, bulk search and the public API must all go through it.
- **Never fall back silently:** if the organization chose `own` and its key is missing, invalid, out of credit or rejected, **do not use LeadAI's key**. That would make LeadAI pay for a customer who chose not to. Stop the run with a clear error, for example `OWN_APIFY_KEY_MISSING`, `OWN_APIFY_KEY_REJECTED` or `OWN_GEMINI_QUOTA_EXCEEDED`. Mark the search failed with that reason, show it to the user, and notify the organization owners/admins once per incident (existing `notify_org_admins`).
- **Usage and tokens:** keep plan limits as they are (searches, leads, members). For each action, record whether LeadAI or the customer paid for each API (`usage_logs` / `token_ledger` gain a `source` field). Use the existing token rules for LeadAI-provided usage, and do **not** spend platform tokens for usage on the customer's own keys. Update the Super Admin cost and unit-economics views (`app/services/unit_economics.py`) so they count only LeadAI-paid usage.

## 6. Changing the choice later

- **Switching an API to the customer's own key:** allowed at any time once a verified key is saved. The lower price starts at the **next renewal**, and the customer is told so. Never refund automatically.
- **Switching an API to LeadAI-provided:** needs payment. Reuse the existing plan-change / checkout flow to charge the prorated difference for the rest of the period. If proration doesn't exist, charge the new price from the next renewal and keep the customer's own key required until then. LeadAI must never provide an API the customer hasn't paid for.
- **Demo or trial without a paid subscription:** the change applies immediately.
- **Records:** every change is audited (`organization.api_coverage_changed`, with before and after) and shown in the organization's billing history.

## 7. Screens (follow the existing cream + mint design tokens; no new colours)

- **Public pricing page (`/pricing`, `website.js`):** add a toggle per API: "Apify: LeadAI provides / I'll use my own key", and the same for Gemini. Each plan card shows its price for the chosen combination, and a short note explains what bringing your own key means (you pay Apify / Google directly). The choice carries through to sign-up and checkout.
- **Sign-up / demo request / checkout:** a new customer picks the coverage together with the plan. Checkout shows the price breakdown: base, plus each add-on.
- **Org Admin → Integrations, "API keys & plan" page:**
  - Two cards, Apify and Gemini. Each shows who provides it, the masked key, Verified / Not verified / Error, and the actions Add key, Replace key, Test, Remove key, and Switch to LeadAI-provided or Switch to my own key.
  - Show how the next invoice changes before the admin confirms.
  - Links: where to get an Apify token, and where to get a Gemini key.
- **User dashboard (`app.js`):** if a search failed because of an own-key problem, explain it, and give owners/admins a link to the Integrations page.
- **Super Admin:**
  - Plan editor: base price, Apify and Gemini add-ons (monthly and yearly), and which options are allowed per plan.
  - Organization detail: an "API coverage" card with the choice per API, key status (masked only) and the last error. Actions (with a reason, audited): switch coverage, and remove a compromised key.
  - Subscriptions list: a filter by coverage.
  - Dashboard: how many organizations use each option.
- **Partner portal:** the sales kit (`app/partners/sales.py`) shows every combination's customer price and the commission on it.

## 8. Tests (pytest, in-memory MongoDB like the existing tests)

- **Pricing:** `price_for` for all four combinations × monthly/yearly × each plan. The Free plan refuses `own`.
- **Checkout and records:** checkout charges the right amount, and the subscription and invoice save the coverage snapshot. Renewal uses the coverage in force at renewal.
- **Keys:** keys are encrypted in MongoDB (the raw value never appears in the document). No API response anywhere contains a raw key: scan every org-admin and super-admin GET response, like `test_super_admin_get_responses_have_no_secrets`.
- **Resolver:** `own` with a valid key → the customer's key is used. `own` with a missing or rejected key → a clear error and **no** call with LeadAI's key (mock the Apify and Gemini clients and assert which key was used). `leadai` → LeadAI's key.
- **Usage:** usage on the customer's own keys spends no platform tokens; LeadAI-provided usage does.
- **Changing the choice:** switching rules from section 6, permissions (members and managers are refused; owner/admin and the Super Admin are allowed), audit entries without key values, and the draft-data migration.
- **Partner commission:** commission is calculated on the price actually charged.

## 9. Definition of done

- Every item above works in the browser: verify the pricing page, checkout, Org Admin Integrations and Super Admin screens in a real browser (Playwright) in both light and dark themes.
- The CI checks in `.github/workflows/ci.yml` pass locally:
  - `ruff check app tests scripts`
  - `mypy app --ignore-missing-imports --no-strict-optional`
  - `pytest` (the plain command, as CI runs it)
  - `node --check` on every changed JS file
- Docs are updated: `README.md` (plans section), `CHANGELOG.md`, `docs/DEPLOYMENT.md` (the new env var) and a short `docs/API_COVERAGE.md` explaining the options, pricing and key handling.
- Do not commit or push unless asked. When finished, report what changed, the default prices, and any decision you had to make that this prompt did not cover.
