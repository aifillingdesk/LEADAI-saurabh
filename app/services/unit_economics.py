"""
LeadAI Unit-Economics & Margin Analytics Service (Phase 6 Product Feature).

Calculates:
- Cost per action: AI token cost (Gemini 2.5 Flash), Apify scraping cost per search
- Cost per qualified lead detected
- Gross profit margin per subscription tier (USD & INR)
"""
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Standard Cost Benchmarks
GEMINI_INPUT_PER_M = 0.075   # $0.075 per 1M tokens
GEMINI_OUTPUT_PER_M = 0.30   # $0.30 per 1M tokens
APIFY_CU_HOUR_COST = 0.25    # $0.25 per compute unit hour
AVG_APIFY_CU_PER_SEARCH = 0.02  # ~0.02 CU ($0.005) per search run
AVG_TOKENS_PER_COMMENT = 150    # ~115 in, ~35 out

PLAN_ECONOMICS: dict[str, dict[str, Any]] = {
    "free": {
        "name": "Free / Demo",
        "monthly_price_usd": 0.0,
        "monthly_price_inr": 0.0,
        "quota_searches": 10,
        "quota_comments": 300,
        "quota_ai_tokens": 45000,
    },
    "starter": {
        "name": "Starter",
        "monthly_price_usd": 29.0,
        "monthly_price_inr": 2499.0,
        "quota_searches": 100,
        "quota_comments": 3000,
        "quota_ai_tokens": 450000,
    },
    "pro": {
        "name": "Professional",
        "monthly_price_usd": 79.0,
        "monthly_price_inr": 6499.0,
        "quota_searches": 500,
        "quota_comments": 25000,
        "quota_ai_tokens": 3750000,
    },
    "business": {
        "name": "Business",
        "monthly_price_usd": 199.0,
        "monthly_price_inr": 16499.0,
        "quota_searches": 2000,
        "quota_comments": 150000,
        "quota_ai_tokens": 22500000,
    },
}


def compute_action_costs() -> dict[str, Any]:
    """Calculate unit cost breakdown for individual actions."""
    # Cost per 1,000 comments analyzed with Gemini 2.5 Flash
    # 1,000 comments * 115 in = 115k tokens ($0.0086); 35k out = $0.0105 => ~$0.0191
    ai_cost_per_comment = (115 * GEMINI_INPUT_PER_M / 1_000_000) + (35 * GEMINI_OUTPUT_PER_M / 1_000_000)
    ai_cost_per_1000 = ai_cost_per_comment * 1000

    apify_cost_per_search = AVG_APIFY_CU_PER_SEARCH * APIFY_CU_HOUR_COST  # $0.005

    # Blended search run: 1 search + 30 comments analyzed
    blended_search_run = apify_cost_per_search + (30 * ai_cost_per_comment)

    return {
        "gemini_cost_per_1000_comments_usd": round(ai_cost_per_1000, 4),
        "ai_cost_per_single_comment_usd": round(ai_cost_per_comment, 6),
        "apify_cost_per_search_run_usd": round(apify_cost_per_search, 4),
        "blended_cost_per_search_run_usd": round(blended_search_run, 4),
        "currency_usd_inr_exchange_rate": 83.5,
    }


def compute_plan_margins() -> dict[str, Any]:
    """Compute gross margins and COGS per plan tier at 100% quota utilization."""
    action_costs = compute_action_costs()
    search_unit_cost = action_costs["apify_cost_per_search_run_usd"]
    comment_unit_cost = action_costs["ai_cost_per_single_comment_usd"]

    report: dict[str, Any] = {}

    for plan_key, plan in PLAN_ECONOMICS.items():
        if plan["monthly_price_usd"] == 0:
            cogs = (plan["quota_searches"] * search_unit_cost) + (plan["quota_comments"] * comment_unit_cost)
            report[plan_key] = {
                "name": plan["name"],
                "price_usd": 0.0,
                "cogs_usd": round(cogs, 3),
                "margin_usd": round(-cogs, 3),
                "margin_percent": 0.0,
                "status": "Freemium / CAC",
            }
            continue

        cogs_max = (plan["quota_searches"] * search_unit_cost) + (plan["quota_comments"] * comment_unit_cost)
        # Average actual utilization benchmark (typically 45-60%)
        cogs_avg = cogs_max * 0.50

        margin_max = plan["monthly_price_usd"] - cogs_max
        margin_percent_max = (margin_max / plan["monthly_price_usd"]) * 100

        margin_avg = plan["monthly_price_usd"] - cogs_avg
        margin_percent_avg = (margin_avg / plan["monthly_price_usd"]) * 100

        report[plan_key] = {
            "name": plan["name"],
            "price_usd": plan["monthly_price_usd"],
            "price_inr": plan["monthly_price_inr"],
            "max_cogs_usd": round(cogs_max, 2),
            "max_margin_percent": round(margin_percent_max, 1),
            "projected_avg_cogs_usd": round(cogs_avg, 2),
            "projected_avg_margin_percent": round(margin_percent_avg, 1),
        }

    return {
        "unit_costs": action_costs,
        "plan_margins": report,
    }


def get_tenant_usage_economics(db, organization_id: str) -> dict[str, Any]:
    """Calculate actual COGS and profitability for a specific tenant organization."""
    action_costs = compute_action_costs()

    search_count = db.search_history.count_documents({"organization_id": str(organization_id)})
    leads_count = db.ai_comments.count_documents({"organization_id": str(organization_id)})

    apify_cogs = search_count * action_costs["apify_cost_per_search_run_usd"]
    ai_cogs = leads_count * 5 * action_costs["ai_cost_per_single_comment_usd"]  # ~5 comments scanned per lead
    total_cogs = apify_cogs + ai_cogs

    sub = db.subscriptions.find_one({"organization_id": str(organization_id)})
    plan_tier = sub.get("plan_id", "starter") if sub else "free"
    monthly_rev = PLAN_ECONOMICS.get(plan_tier, {}).get("monthly_price_usd", 29.0)

    margin = monthly_rev - total_cogs
    margin_pct = (margin / monthly_rev * 100) if monthly_rev > 0 else 0.0

    return {
        "organization_id": organization_id,
        "plan_tier": plan_tier,
        "monthly_revenue_usd": monthly_rev,
        "total_searches_run": search_count,
        "total_leads_generated": leads_count,
        "apify_cost_usd": round(apify_cogs, 3),
        "ai_cost_usd": round(ai_cogs, 3),
        "total_cogs_usd": round(total_cogs, 3),
        "gross_margin_usd": round(margin, 2),
        "gross_margin_pct": round(margin_pct, 1),
    }
