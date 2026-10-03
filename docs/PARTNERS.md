# Partner / Reseller / Affiliate Program

Partners are ordinary `users` accounts signed in with a **`partner` session scope**. The customers they bring are ordinary organizations that go through the normal demo → subscription → payment flow. A partner program does not add a second customer, billing or payment system.

```
Super Admin ─► Partner (affiliate | reseller) ─► Customer organization ─► Users
```

## Flow

1. **Apply**: `/partners` (public) posts to `POST /api/public/partners/apply`. This creates the applicant's user account, or links an existing account after its password is verified. Applications are rate limited per IP.
2. **Review**: Super Admin → *Partners & Resellers → Applications*. The Super Admin can approve (choosing the type, tier and permissions), reject, or request changes. Applicants can sign in at `/login?partner=1` to see their status and resubmit, but nothing else.
3. **Partner Portal**: `/partner` shows the dashboard, tasks from LeadAI, analytics, referrals, customers, campaigns, coupons, marketing center, commissions, wallet, payouts, notifications, API access, profile, settings and help. Each module appears only if the partner holds the matching permission.
4. **Attribution**: `/r/{code}[/{campaign}]` records a click and sets a signed cookie (`leadai_ref`) holding the first-touch and last-touch partner. At signup the cookie wins; otherwise a typed `?ref=` code is used, if allowed. Each organization gets exactly one `partner_referrals` record (enforced by a unique index).
5. **Funnel**: `signed_up` → `demo` (demo approved) → `subscription` (checkout started) → `payment` (payment verified) → `customer` (subscription confirmed). A referral only moves forward. Each step is recorded once in the referral's conversion history, as are churn, refund, chargeback, reassignment and invalidation events. Partners see the history in the portal, with the customer's contact data masked.
6. **Commissions**: these are created only from billing events — `confirm_subscription` (the first payment) and `renew_subscription` (each renewal). Each commission references its organization, subscription and invoice, and is idempotent per `(subscription_id, period_key)`. Lifecycle:

   `pending` (qualification period) → `qualified` → `approved` (automatically, or by a Super Admin when flagged) → `payable` (per the payout schedule) → `processing` (inside a payout) → `paid`. `reversed` is possible from any unpaid state.

   - **Refunds and chargebacks**: recorded on the customer's invoice — Super Admin `POST /api/super-admin/invoices/{id}/refund`, or the provider webhooks `charge.refunded`, `charge.dispute.created`, `refund.processed` and `payment.refunded`. The commission earned on that payment is reduced in proportion. A commission that is already paid gets a negative `clawback` entry, which is deducted from the next payout.
   - **Cancellation**: cancelling a subscription during its qualification period reverses the pending commission (a program setting).
7. **Wallet and payouts**: balances are derived from the commission records, so partners can't edit them. The buckets are pending, available (payable), processing, paid and reversed. Every movement goes into the ledger with its customer, subscription, invoice, commission and payout references. A partner requests the whole available balance. The Super Admin then moves the payout through **requested → under review → approved → processing → paid** (with a transfer reference) or **failed / rejected**, in which case the money becomes payable again.
8. **Reseller onboarding**: resellers can add customers directly, or share an *onboarding link* (a campaign of kind `onboarding`); signups through it become managed customers. A tier can cap how many managed customers a reseller has.

## Rules and settings (Super Admin)

- **Commission rules**: percentage, fixed, or hybrid (a percentage plus a fixed amount); one-time or recurring with a duration in months (0 means lifetime); a cap per customer and a cap per payment; a qualification period that overrides the program default; an optional plan filter. The most specific active rule wins: partner, then tier, then global.
- **Program settings**: attribution model and window, qualification days, auto-approval, reversing on cancellation, payout schedule (on request, weekly or monthly, plus the day), minimum payout, payout methods, partner coupons and their cap, click rate limit, self-referral blocking, holding suspicious commissions for review, automatic tiering and downgrades.
- **Coupons**: a percentage or fixed discount applied in `start_checkout`. Eligibility can be any customer (once per organization), new customers only, or only customers referred by that partner. The commission basis can be the paid amount, the list price, or no commission. A redemption is counted only when the subscription is confirmed.
- **Partner pricing**: a discount for referred customers, defined per partner, per tier, or for all referred customers, optionally per plan and optionally for the first payment only. It never stacks with a coupon: the better of the two applies, and billing charges the discounted amount.
- **Tiers**: requirements (minimum customers, referrals and revenue, optionally within a number of days), a cap on managed customers, coupon permission and a coupon percentage cap, and benefits. Commission rates per tier are set with tier-scoped rules. Automatic tiering upgrades a partner to the highest tier they've earned; it downgrades only if allowed, and never touches a tier a Super Admin set by hand.
- **Relationships and fraud**: a Super Admin can reassign a customer to another partner (only before anything is paid out), clear a fraud flag, or invalidate a referral (reversing all its commissions, with clawback for paid ones). The fraud review screen lists flagged referrals, commissions held for review, click floods, blocked self-referrals and chargebacks.

## Analytics, notifications and marketing

- **Analytics**:
  - Partner portal `/api/partner/v1/analytics` (plus `.csv`); Super Admin `/api/super-admin/partners/analytics` (plus `.csv`) with partner and campaign filters, and a paged `/leaderboard`.
  - Metrics: clicks, unique visitors, signups, demos, customers, active subscriptions, revenue, commission earned, commission amounts by status, and payouts, with time series, funnel, sources and campaign tables.
  - Revenue comes from the referred organizations' paid invoices, net of refunds, so it reconciles with billing.
  - `/api/super-admin/partners/reconciliation` is a read-only cross-check of commissions, payouts, wallets and referral revenue against invoices.
- **Notifications** use the existing in-app notifications and email outbox. Events covered:
  - Applications and their decisions; suspension and reactivation; tier changes.
  - Each funnel stage: referral, demo, checkout and payment.
  - Commissions: earned, lifecycle steps, approval, refund or chargeback reversal.
  - Payouts: request, review, approval, processing, paid, failed, rejected.
  - Coupons: issued, created by a partner, redeemed.
  - Fraud alerts, which go to Super Admins.
- **Marketing center**:
  - Categories: logos, product images, brochures, videos, social creatives, banners, email templates, copy and campaign materials.
  - Files are uploaded through the existing storage service. With the local backend they're stored privately in `data/partner_assets` and served only through a permission-checked endpoint. With S3 or Cloudinary the provider's URL is used, which is public.
  - Visibility can be limited by partner type, tier or named partners.
  - Copy and email templates fill in the partner's referral link and name. Downloads are counted and audited.

## Selling and deal registration

- **Sell LeadAI** (`GET /api/partner/v1/sales`, permission `sales.view`): the live plan catalog with, for that partner, the price the customer pays after partner pricing, the commission on the first payment and on each renewal (from the rule that would really apply), a plan-specific share link (`/r/{code}?plan={slug}`, which opens the demo request with that plan selected), active coupons and the product pitch.
- **Deals** (`/api/partner/v1/deals`, permission `deals.manage`): partners register prospects before they sign up. Competing claims (another partner's open deal on the same email, domain or company, or an existing account) are shown to the Super Admin. The Super Admin approves (protecting the claim for `deal_protection_days`), rejects, or marks the deal won or lost. When the prospect signs up through the partner's link, the deal is linked and follows the referral's stage. A signup through another partner while a deal is protected opens an `attribution_conflict` flag, and attribution and money are never changed automatically. Resellers can turn a deal into a customer through the normal demo flow. Approved deals that never sign up expire.

## Tasks

The Super Admin assigns work; partners do it in the Partner Portal and report back.

- **Assign** (Super Admin → *Partners & Resellers → Tasks*, or **Assign task** on a partner's page; `POST /api/super-admin/partners/tasks`): a title, details, an optional due date, a priority, and an optional Partner Portal page to link to (for example *Sell LeadAI*). A task goes to one partner, or to every active partner, affiliate or reseller (one task per partner). Suspended partners can't be assigned tasks. The partner gets an in-app notification and, by default, an email.
- **Do** (Partner Portal → *Tasks*, `/api/partner/v1/tasks`; every active partner, no extra permission): the menu shows how many tasks are open, and the dashboard shows a banner. The partner opens a task, starts it, and submits it with a note on what they did. The note is required.
- **Review**: submitted tasks appear under *Ready for review* and in the sidebar count. The Super Admin approves the task (done), or sends it back with feedback (a reason is required; the partner is emailed). The Super Admin can edit or cancel an open task (cancelling needs a reason). Overdue = past the due date and not yet submitted.

  `open` → `in_progress` → `submitted` → `done`; a task sent back returns to `in_progress`. `open`, `in_progress` and `submitted` tasks can be `cancelled`.
- Every step is audited (`partner.task.assigned`, `.started`, `.submitted`, `.sent_back`, `.approved`, `.edited`, `.cancelled`) and appears in the partner's activity log. Partner API keys can read tasks but not change them. Opening another partner's task returns 404 and raises a `cross_partner_access` fraud flag.

## Super Admin oversight

- **Activity log** (`/api/super-admin/partners/activity`, plus `.csv`), collection `partner_activity`:
  - Sign-ins: successful, failed and 2FA, plus sign-outs.
  - Every Partner Portal and Partner API request, with method, path, status, session or API key, and duration.
  - Every audited partner action, including the Super Admin's actions on a partner.
  - Filters: partner, type, result, channel, email, text and dates. IP addresses are stored only as hashes, and rows expire after `activity_retention_days`.
- **Sessions** (`/api/super-admin/partners/sessions`): every signed-in Partner Portal session. The Super Admin can end one session or force a partner's sign-out everywhere, which is audited and notified.
- **Partner detail** adds last sign-in and activity, request, API and failed-sign-in counts, deals, and the Activity, Sessions, Deals and Audit log tabs.

## Super Admin full access

The Super Admin holds every permission:

- **Platform**: every platform permission, which no role-matrix edit can reduce. Platform staff roles always get a strict subset.
- **Organizations**: "sign in as" any organization (reason required, time-limited) acts as its **Owner** with every organization permission. The organization's own role restrictions don't apply.
- **Partners**: **View as partner** (`POST /api/super-admin/partners/{id}/impersonate`, reason required, time-limited) opens that partner's portal with their full rights, under a visible banner. Every request and action is recorded with `impersonated_by`. Leave with "Exit to Super Admin".
- **API keys and webhooks**: **API keys & webhooks** (`/api/super-admin/api-keys`, `/api/super-admin/webhooks`) lists every organization and partner key and every outbound webhook. The Super Admin can revoke keys and disable or enable webhooks, effective immediately; the owner is notified and the action audited.
- **Customer API and Partner API**: the Super Admin can **issue** an API key for any organization (choosing the scopes) or any partner (`POST /api/super-admin/api-keys`, reason required). Issuing a partner key can also grant that partner API access. The key is shown once, marked as issued by the Super Admin, audited, and the owner is notified.

## Fraud review

Suspicious signals open a **fraud flag** (`partner_fraud_flags`) for Super Admin review: self-referral, the partner's own network, duplicate customer, conflicting attribution, tampered referral cookie, click flood, coupon abuse, chargeback, cross-partner access, a partner session probing admin APIs, an invalid or revoked API key, and actions denied by permissions.

A flag never changes money. It only *holds* the related referral's unpaid commissions: they still qualify, but nothing is approved or paid until review. The Super Admin then dismisses the flag (which releases the hold), confirms it, or invalidates the referral (an explicit, audited reversal).

## Security

- Every partner request re-reads the user and partner records. Suspending a partner revokes their sessions and API keys immediately.
- Every query is filtered by the caller's `partner_id`. A request for another partner's record returns 404 and logs a `cross_partner_access` security event.
- Partner sessions can't reach the organization APIs (`resolve_tenant_context` rejects the `partner` scope) or the Super Admin APIs.
- Partner API keys (`lap_live_…`, stored in `api_keys` with `owner_type="partner"`) are read-only and rate limited to 60 requests per minute. They never authenticate against `/api/v1`.
- Abuse protections: self-referral is blocked (same account, normalized email, or phone), and a signup from the partner's own IP is flagged as suspicious and held for manual review. Click floods are recorded but never attribute a signup. Attribution cookies are signed and expire. A partner can't use their own coupon, and each organization can redeem a coupon only once.
- Payout and tax details are masked in every partner-facing response. The Super Admin sees them in full, because they process payouts. Changes to these details are audited (values redacted) and trigger a notification to the partner.
- Audit trail: the platform audit log, `category="partner"`, with `details.partner_id`.

## Collections

`partner_applications`, `partners`, `partner_tiers`, `partner_commission_rules`, `partner_settings`, `partner_referral_clicks`, `partner_referrals`, `partner_campaigns`, `partner_commissions`, `partner_wallets` (cached balances recomputed from commissions), `partner_wallet_transactions` (ledger), `partner_payouts`, `partner_coupons`, `partner_coupon_usages`, `partner_marketing_assets`, `partner_pricing`, `partner_deals`, `partner_tasks`.

Reused collections: `invoices` and `payments` (refunds are recorded on them), `users`, `user_sessions`, `organizations`, `subscriptions`, `notifications`, `audit_logs`, `api_keys`, `password_resets`, `rate_limits`.
