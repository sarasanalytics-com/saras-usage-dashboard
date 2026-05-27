#!/usr/bin/env python3
"""
collect_api_agents.py — Fetch per-API-key (agent) cost and usage from platform.claude.com.

Uses ANTHROPIC_ADMIN_KEY to:
1. List API key names via Admin API  (/v1/organizations/api_keys)
2. Fetch cost_report grouped by api_key_id (MTD, daily buckets)
3. Fetch usage_report grouped by api_key_id (MTD)
4. Fetch cost_report grouped by [api_key_id, model] for per-agent model breakdown

Writes  data/api_agents_stats.json
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

REPO_ROOT   = Path(__file__).resolve().parent.parent
OUTPUT_PATH = REPO_ROOT / "data" / "api_agents_stats.json"

ADMIN_KEY     = os.environ["ANTHROPIC_ADMIN_KEY"].strip()
# Fall back to admin key for analytics if no separate analytics key
ANALYTICS_KEY = os.environ.get("ANTHROPIC_ANALYTICS_KEY", ADMIN_KEY).strip()

ADMIN_BASE     = "https://api.anthropic.com/v1"
ANALYTICS_BASE = "https://api.anthropic.com/v1/organizations/analytics"

ADMIN_HEADERS = {
    "x-api-key":          ADMIN_KEY,
    "anthropic-version":  "2023-06-01",
    "anthropic-beta":     "api-key-management-2025-02-19",
}
ANALYTICS_HEADERS = {
    "x-api-key":          ADMIN_KEY,   # admin key can access analytics too
    "anthropic-version":  "2023-06-01",
}


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def get_json(url, headers, timeout=60):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def analytics_get(path, params):
    """Build analytics URL with list params (key[]=val) and fetch."""
    parts = []
    for k, v in params.items():
        if isinstance(v, list):
            for item in v:
                parts.append(
                    f"{urllib.request.quote(str(k))}[]={urllib.request.quote(str(item))}"
                )
        else:
            parts.append(
                f"{urllib.request.quote(str(k))}={urllib.request.quote(str(v))}"
            )
    url = f"{ANALYTICS_BASE}/{path}?{'&'.join(parts)}"
    return get_json(url, ANALYTICS_HEADERS)


# ── Date range ────────────────────────────────────────────────────────────────
today_utc   = datetime.now(timezone.utc).date()
data_until  = today_utc - timedelta(days=3)   # 3-day API data delay
month_start = today_utc.replace(day=1)

# Days in this month
import calendar
days_in_month = calendar.monthrange(today_utc.year, today_utc.month)[1]
days_elapsed  = max(1, (data_until - month_start).days + 1)

log(f"Collecting API agent stats  {month_start} → {data_until}  "
    f"({days_elapsed}/{days_in_month} days elapsed)")


# ── 1. List API key names ─────────────────────────────────────────────────────
log("\n── 1. Listing API keys ──────────────────────────────────────────────────")
key_names  = {}   # id → name
key_status = {}   # id → status (active/inactive)
try:
    params_str = "?limit=100"
    url = f"{ADMIN_BASE}/organizations/api_keys{params_str}"
    data = get_json(url, ADMIN_HEADERS)
    for key in data.get("data", []):
        kid    = key.get("id", "")
        name   = key.get("name") or kid
        status = key.get("status", "active")
        if kid:
            key_names[kid]  = name
            key_status[kid] = status
    log(f"  Found {len(key_names)} API keys")
    for kid, name in key_names.items():
        log(f"    [{key_status.get(kid,'?')}] {name}  ({kid[:20]}...)")
except Exception as e:
    log(f"  [WARN] Admin API key list failed: {e}")


# ── 2. Cost by api_key_id  (MTD, daily buckets) ───────────────────────────────
log("\n── 2. Cost report by API key (daily, MTD) ───────────────────────────────")
key_cost_mtd    = defaultdict(float)          # key_id → total USD MTD
agent_daily     = defaultdict(lambda: defaultdict(float))  # key_id → date → USD

try:
    params = {
        "starting_at":  month_start.strftime("%Y-%m-%dT00:00:00Z"),
        "ending_at":    (data_until + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z"),
        "bucket_width": "1d",
        "group_by":     ["api_key_id"],
    }
    resp = analytics_get("cost_report", params)

    while True:
        for bucket in resp.get("data", []):
            date_str = bucket.get("starting_at", "")[:10]
            for item in bucket.get("results", []):
                kid    = item.get("api_key_id") or "unknown"
                amount = float(item.get("amount", 0) or 0) / 100.0
                key_cost_mtd[kid] += amount
                if date_str:
                    agent_daily[kid][date_str] += amount

        if not resp.get("has_more"):
            break
        params["page"] = resp.get("next_page")
        resp = analytics_get("cost_report", params)
        time.sleep(0.2)

    total_mtd = sum(key_cost_mtd.values())
    log(f"  {len(key_cost_mtd)} keys with spend  |  Total MTD: ${total_mtd:.2f}")
    for kid, cost in sorted(key_cost_mtd.items(), key=lambda x: -x[1]):
        log(f"    {key_names.get(kid, kid)}: ${cost:.4f}")

except Exception as e:
    log(f"  [WARN] cost_report by api_key failed: {e}")


# ── 3. Cost by [api_key_id, model] (MTD summary) ─────────────────────────────
log("\n── 3. Cost report by API key × model (MTD) ─────────────────────────────")
key_model_cost = defaultdict(lambda: defaultdict(float))  # key_id → model → USD

try:
    params = {
        "starting_at":  month_start.strftime("%Y-%m-%dT00:00:00Z"),
        "ending_at":    (data_until + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z"),
        "bucket_width": "1month",
        "group_by":     ["api_key_id", "model"],
    }
    resp = analytics_get("cost_report", params)

    while True:
        for bucket in resp.get("data", []):
            for item in bucket.get("results", []):
                kid   = item.get("api_key_id") or "unknown"
                mdl   = item.get("model") or "unknown"
                cost  = float(item.get("amount", 0) or 0) / 100.0
                key_model_cost[kid][mdl] += cost

        if not resp.get("has_more"):
            break
        params["page"] = resp.get("next_page")
        resp = analytics_get("cost_report", params)
        time.sleep(0.2)

    log(f"  Model breakdown fetched for {len(key_model_cost)} keys")

except Exception as e:
    log(f"  [WARN] cost_report by api_key+model failed: {e}")


# ── 4. Token usage by api_key_id (MTD) ───────────────────────────────────────
log("\n── 4. Usage (tokens) by API key (MTD) ───────────────────────────────────")
key_usage = defaultdict(lambda: {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0})

try:
    params = {
        "starting_at":  month_start.strftime("%Y-%m-%dT00:00:00Z"),
        "ending_at":    (data_until + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z"),
        "bucket_width": "1month",
        "group_by":     ["api_key_id"],
    }
    resp = analytics_get("usage_report", params)

    while True:
        for bucket in resp.get("data", []):
            for item in bucket.get("results", []):
                kid = item.get("api_key_id") or "unknown"
                key_usage[kid]["input_tokens"]      += item.get("uncached_input_tokens", item.get("input_tokens", 0))
                key_usage[kid]["output_tokens"]      += item.get("output_tokens", 0)
                key_usage[kid]["cache_read_tokens"]  += item.get("cache_read_input_tokens", 0)

        if not resp.get("has_more"):
            break
        params["page"] = resp.get("next_page")
        resp = analytics_get("usage_report", params)
        time.sleep(0.2)

    log(f"  Token usage fetched for {len(key_usage)} keys")

except Exception as e:
    log(f"  [WARN] usage_report by api_key failed: {e}")


# ── 5. Build projections ──────────────────────────────────────────────────────
total_api_spend_mtd = sum(key_cost_mtd.values())
daily_avg           = total_api_spend_mtd / days_elapsed if days_elapsed > 0 else 0
projected_monthly   = daily_avg * days_in_month
projected_yearly    = projected_monthly * 12

# Aggregate model costs across all agents
global_model_cost = defaultdict(float)
for mdl_map in key_model_cost.values():
    for mdl, cost in mdl_map.items():
        global_model_cost[mdl] += cost

# Build agents list — merge all known key IDs
all_key_ids = (
    set(key_cost_mtd.keys())
    | set(key_usage.keys())
    | set(key_model_cost.keys())
    | set(key_names.keys())
)

agents = []
for kid in sorted(all_key_ids, key=lambda k: -key_cost_mtd.get(k, 0)):
    name    = key_names.get(kid, kid[:20] + "…" if len(kid) > 20 else kid)
    status  = key_status.get(kid, "active")
    cost    = key_cost_mtd.get(kid, 0)
    usage   = dict(key_usage.get(kid, {}))
    m_costs = {m: round(c, 6) for m, c in
               sorted(key_model_cost.get(kid, {}).items(), key=lambda x: -x[1])}
    d_costs = {d: round(c, 6) for d, c in
               sorted(agent_daily.get(kid, {}).items())}
    agents.append({
        "id":         kid,
        "name":       name,
        "status":     status,
        "costMtd":    round(cost, 4),
        "usage":      usage,
        "modelCosts": m_costs,
        "dailyCosts": d_costs,
    })

result = {
    "asOf":               data_until.strftime("%Y-%m-%d"),
    "monthStart":         month_start.strftime("%Y-%m-%d"),
    "totalApiSpendMtd":   round(total_api_spend_mtd, 2),
    "dailyAvg":           round(daily_avg, 4),
    "projectedMonthly":   round(projected_monthly, 2),
    "projectedYearly":    round(projected_yearly, 2),
    "daysElapsed":        days_elapsed,
    "daysInMonth":        days_in_month,
    "globalModelCost":    {m: round(c, 4) for m, c in
                           sorted(global_model_cost.items(), key=lambda x: -x[1])},
    "agents":             agents,
}

OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
OUTPUT_PATH.write_text(json.dumps(result, indent=2), encoding="utf-8")

log(f"\nWrote {OUTPUT_PATH}")
log(f"  Total API spend MTD:   ${total_api_spend_mtd:.2f}")
log(f"  Daily avg:             ${daily_avg:.2f}")
log(f"  Projected monthly:     ${projected_monthly:.2f}")
log(f"  Projected yearly:      ${projected_yearly:.2f}")
log(f"  Agents collected:      {len(agents)}")
