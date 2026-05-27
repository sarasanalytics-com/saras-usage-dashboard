#!/usr/bin/env python3
"""
collect_api_agents.py — Fetch per-API-key (agent) cost and usage from platform.claude.com.

Uses ANTHROPIC_ADMIN_KEY to:
1. List API key names via Admin API  (/v1/organizations/api_keys)
2. Fetch cost_report grouped by model (MTD) — aggregate + per-model
3. Attempt cost_report grouped by workspace_id (if available)
4. Fetch usage_report grouped by model (MTD)

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
import calendar

REPO_ROOT   = Path(__file__).resolve().parent.parent
OUTPUT_PATH = REPO_ROOT / "data" / "api_agents_stats.json"

ADMIN_KEY     = os.environ["ANTHROPIC_ADMIN_KEY"].strip()
# Fall back to admin key for analytics if no separate analytics key
ANALYTICS_KEY = os.environ.get("ANTHROPIC_ANALYTICS_KEY", ADMIN_KEY).strip()

ADMIN_BASE     = "https://api.anthropic.com/v1"
ANALYTICS_BASE = "https://api.anthropic.com/v1/organizations/analytics"

# Headers for key-management Admin API
ADMIN_HEADERS_BETA = {
    "x-api-key":          ADMIN_KEY,
    "anthropic-version":  "2023-06-01",
    "anthropic-beta":     "api-key-management-2025-02-19",
}
ADMIN_HEADERS_NO_BETA = {
    "x-api-key":          ADMIN_KEY,
    "anthropic-version":  "2023-06-01",
}

# Headers for Analytics API
# Try admin key first (to access platform API data); fall back to analytics key.
# NOTE: If ANTHROPIC_ADMIN_KEY is invalid, ANALYTICS_KEY (claude.ai) will be used
# as fallback — the data returned may be claude.ai costs rather than API costs.
ANALYTICS_HEADERS_ADMIN = {
    "x-api-key":          ADMIN_KEY,
    "anthropic-version":  "2023-06-01",
}
ANALYTICS_HEADERS_FALLBACK = {
    "x-api-key":          ANALYTICS_KEY,
    "anthropic-version":  "2023-06-01",
}
ANALYTICS_HEADERS = ANALYTICS_HEADERS_ADMIN  # will be reassigned below if admin key fails


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def get_json(url, headers, timeout=60):
    """Fetch JSON; on error print full response body for debugging."""
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        log(f"    HTTP {e.code} {e.reason}")
        log(f"    Body: {body[:800]}")
        raise
    except Exception as e:
        log(f"    Error: {e}")
        raise


def analytics_get(path, params, headers=None):
    """Build analytics URL with list params (key[]=val) and fetch."""
    if headers is None:
        headers = ANALYTICS_HEADERS
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
    return get_json(url, headers)


# ── Date range ────────────────────────────────────────────────────────────────
today_utc   = datetime.now(timezone.utc).date()
data_until  = today_utc - timedelta(days=3)   # 3-day API data delay
month_start = today_utc.replace(day=1)

days_in_month = calendar.monthrange(today_utc.year, today_utc.month)[1]
days_elapsed  = max(1, (data_until - month_start).days + 1)

log(f"Collecting API agent stats  {month_start} → {data_until}  "
    f"({days_elapsed}/{days_in_month} days elapsed)")
log(f"Admin key prefix: {ADMIN_KEY[:20]}...")
log(f"Analytics key prefix: {ANALYTICS_KEY[:20]}...")


# ── 1. List API key names (try with beta header, then without) ────────────────
log("\n── 1. Listing API keys via Admin API ────────────────────────────────────")
key_names  = {}   # id → name
key_status = {}   # id → status (active/inactive)

for attempt_label, headers in [
    ("with beta header",    ADMIN_HEADERS_BETA),
    ("without beta header", ADMIN_HEADERS_NO_BETA),
]:
    try:
        url = f"{ADMIN_BASE}/organizations/api_keys?limit=100"
        log(f"  Trying {attempt_label}: GET {url}")
        data = get_json(url, headers)
        for key in data.get("data", []):
            kid    = key.get("id", "")
            name   = key.get("name") or kid
            status = key.get("status", "active")
            if kid:
                key_names[kid]  = name
                key_status[kid] = status
        log(f"  ✓ Found {len(key_names)} API keys")
        for kid, name in key_names.items():
            log(f"    [{key_status.get(kid,'?')}] {name}  ({kid[:25]}...)")
        break  # success — stop trying
    except Exception as e:
        log(f"  [WARN] Key list failed ({attempt_label}): {e}")
        continue

# Also try listing workspaces (useful for grouping)
log("\n── 1b. Listing Workspaces ────────────────────────────────────────────────")
workspace_names = {}  # id → name
for attempt_label, headers in [
    ("with beta header",    ADMIN_HEADERS_BETA),
    ("without beta header", ADMIN_HEADERS_NO_BETA),
]:
    try:
        url = f"{ADMIN_BASE}/organizations/workspaces?limit=100"
        log(f"  Trying {attempt_label}: GET {url}")
        data = get_json(url, headers)
        for ws in data.get("data", []):
            wid  = ws.get("id", "")
            name = ws.get("name") or wid
            if wid:
                workspace_names[wid] = name
        log(f"  ✓ Found {len(workspace_names)} workspaces")
        for wid, name in workspace_names.items():
            log(f"    {name}  ({wid[:25]}...)")
        break
    except Exception as e:
        log(f"  [WARN] Workspace list failed ({attempt_label}): {e}")
        continue


# ── 1c. Probe which key works for analytics ────────────────────────────────
log("\n── 1c. Probe analytics endpoint with admin key vs analytics key ─────────")
_probe_params = {
    "starting_at":  data_until.strftime("%Y-%m-%dT00:00:00Z"),
    "ending_at":    (data_until + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z"),
    "bucket_width": "1d",
    "group_by":     ["model"],
}
_analytics_key_source = None

for _label, _hdrs in [
    ("admin key",    ANALYTICS_HEADERS_ADMIN),
    ("analytics key (fallback)", ANALYTICS_HEADERS_FALLBACK),
]:
    try:
        _test = analytics_get("cost_report", _probe_params, headers=_hdrs)
        log(f"  ✓ {_label} → cost_report succeeded (HTTP 200)")
        ANALYTICS_HEADERS = _hdrs
        _analytics_key_source = _label
        break
    except Exception as e:
        log(f"  [WARN] {_label} → cost_report failed: {e}")

if _analytics_key_source:
    log(f"  → Using {_analytics_key_source} for all analytics calls")
    if "fallback" in _analytics_key_source:
        log("  ⚠️  ADMIN KEY IS INVALID — using analytics key as fallback.")
        log("  ⚠️  Data may reflect claude.ai costs, NOT platform API costs.")
        log("  ⚠️  FIX: Update ANTHROPIC_ADMIN_KEY secret with a valid org admin key")
        log("  ⚠️  from https://console.anthropic.com/settings/keys")
else:
    log("  [ERROR] Both keys failed for analytics — no cost data will be collected.")


# ── 2. Cost report: try several group_by strategies ──────────────────────────
log("\n── 2. Cost report — trying different group_by strategies ────────────────")

key_cost_mtd    = defaultdict(float)   # entity_id → total USD MTD
agent_daily     = defaultdict(lambda: defaultdict(float))  # entity_id → date → USD
global_model_cost = defaultdict(float)  # model → total USD
key_model_cost  = defaultdict(lambda: defaultdict(float))  # entity_id → model → USD

cost_group_used = None  # which group_by succeeded

BASE_COST_PARAMS = {
    "starting_at":  month_start.strftime("%Y-%m-%dT00:00:00Z"),
    "ending_at":    (data_until + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z"),
    "bucket_width": "1d",
}

# Strategy A: group by api_key_id + model
for strategy, group_by_list, id_field in [
    ("api_key_id",    ["api_key_id"],           "api_key_id"),
    ("workspace_id",  ["workspace_id"],          "workspace_id"),
    ("api_key+model", ["api_key_id", "model"],   "api_key_id"),
]:
    try:
        params = {**BASE_COST_PARAMS, "group_by": group_by_list}
        log(f"  Trying group_by={group_by_list} ...")
        resp = analytics_get("cost_report", params)

        rows_found = 0
        while True:
            for bucket in resp.get("data", []):
                date_str = bucket.get("starting_at", "")[:10]
                for item in bucket.get("results", []):
                    entity_id = item.get(id_field) or "unknown"
                    mdl       = item.get("model", "")
                    amount    = float(item.get("amount", 0) or 0) / 100.0
                    key_cost_mtd[entity_id] += amount
                    if mdl:
                        key_model_cost[entity_id][mdl] += amount
                        global_model_cost[mdl] += amount
                    if date_str:
                        agent_daily[entity_id][date_str] += amount
                    rows_found += 1

            if not resp.get("has_more"):
                break
            params["page"] = resp.get("next_page")
            resp = analytics_get("cost_report", params)
            time.sleep(0.2)

        total = sum(key_cost_mtd.values())
        log(f"  ✓ group_by={group_by_list}: {len(key_cost_mtd)} entities, "
            f"{rows_found} rows, total=${total:.4f}")
        cost_group_used = strategy
        break  # success
    except Exception as e:
        log(f"  [WARN] group_by={group_by_list} failed: {e}")
        key_cost_mtd.clear()
        agent_daily.clear()
        global_model_cost.clear()
        key_model_cost.clear()
        continue

# Strategy B (always run): model-only for global total + model breakdown
log(f"\n── 2b. Cost report group_by=model (aggregate fallback) ──────────────────")
try:
    params = {**BASE_COST_PARAMS, "group_by": ["model"]}
    resp   = analytics_get("cost_report", params)

    model_cost_daily = {}  # date → {model → cost}
    while True:
        for bucket in resp.get("data", []):
            date_str = bucket.get("starting_at", "")[:10]
            for item in bucket.get("results", []):
                mdl    = item.get("model") or "unknown"
                amount = float(item.get("amount", 0) or 0) / 100.0
                global_model_cost[mdl] += amount
                if date_str:
                    if date_str not in model_cost_daily:
                        model_cost_daily[date_str] = {}
                    model_cost_daily[date_str][mdl] = (
                        model_cost_daily[date_str].get(mdl, 0) + amount
                    )

        if not resp.get("has_more"):
            break
        params["page"] = resp.get("next_page")
        resp = analytics_get("cost_report", params)
        time.sleep(0.2)

    total_global = sum(global_model_cost.values())
    log(f"  ✓ Global model cost: ${total_global:.4f}")
    for mdl, cost in sorted(global_model_cost.items(), key=lambda x: -x[1]):
        log(f"    {mdl}: ${cost:.4f}")

    # If no per-key breakdown was obtained, build a single "All API Keys" aggregate
    if not key_cost_mtd and total_global > 0:
        log("  → No per-key breakdown available — using single aggregate entry")
        for date_str, mdl_map in model_cost_daily.items():
            day_total = sum(mdl_map.values())
            agent_daily["all_api_keys"][date_str] += day_total
            key_cost_mtd["all_api_keys"] += day_total
            for mdl, cost in mdl_map.items():
                key_model_cost["all_api_keys"][mdl] += cost
        cost_group_used = "aggregate_model"

except Exception as e:
    log(f"  [WARN] model cost_report failed: {e}")


# ── 3. Usage (tokens) by model (MTD) ─────────────────────────────────────────
log("\n── 3. Usage (tokens) by model (MTD) ─────────────────────────────────────")
key_usage = defaultdict(lambda: {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0})
model_token_totals = {}  # model → {input, output, cache}

for group_by_list, id_field in [
    (["api_key_id"], "api_key_id"),
    (["workspace_id"], "workspace_id"),
]:
    try:
        params = {
            **BASE_COST_PARAMS,
            "bucket_width": "1month",
            "group_by":     group_by_list,
        }
        params.pop("bucket_width", None)
        params["bucket_width"] = "1month"
        log(f"  Trying usage group_by={group_by_list} ...")
        resp = analytics_get("usage_report", params)

        while True:
            for bucket in resp.get("data", []):
                for item in bucket.get("results", []):
                    eid = item.get(id_field) or "unknown"
                    key_usage[eid]["input_tokens"]     += item.get("uncached_input_tokens", item.get("input_tokens", 0))
                    key_usage[eid]["output_tokens"]     += item.get("output_tokens", 0)
                    key_usage[eid]["cache_read_tokens"] += item.get("cache_read_input_tokens", 0)

            if not resp.get("has_more"):
                break
            params["page"] = resp.get("next_page")
            resp = analytics_get("usage_report", params)
            time.sleep(0.2)

        log(f"  ✓ Token usage fetched for {len(key_usage)} entities")
        break
    except Exception as e:
        log(f"  [WARN] usage group_by={group_by_list} failed: {e}")
        continue

# Fallback: model-level token usage
if not key_usage:
    try:
        params = {
            **BASE_COST_PARAMS,
            "bucket_width": "1month",
            "group_by":     ["model"],
        }
        resp = analytics_get("usage_report", params)
        total_in = total_out = 0
        while True:
            for bucket in resp.get("data", []):
                for item in bucket.get("results", []):
                    mdl = item.get("model") or "unknown"
                    inp = item.get("uncached_input_tokens", item.get("input_tokens", 0))
                    out = item.get("output_tokens", 0)
                    total_in  += inp
                    total_out += out
                    if mdl not in model_token_totals:
                        model_token_totals[mdl] = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0}
                    model_token_totals[mdl]["input_tokens"]     += inp
                    model_token_totals[mdl]["output_tokens"]     += out
                    model_token_totals[mdl]["cache_read_tokens"] += item.get("cache_read_input_tokens", 0)
            if not resp.get("has_more"):
                break
            params["page"] = resp.get("next_page")
            resp = analytics_get("usage_report", params)
            time.sleep(0.2)
        key_usage["all_api_keys"]["input_tokens"]  = total_in
        key_usage["all_api_keys"]["output_tokens"] = total_out
        log(f"  ✓ Model-level token usage: in={total_in:,} out={total_out:,}")
    except Exception as e:
        log(f"  [WARN] usage model fallback failed: {e}")


# ── 4. Build projections ───────────────────────────────────────────────────────
total_api_spend_mtd = sum(key_cost_mtd.values())
daily_avg           = total_api_spend_mtd / days_elapsed if days_elapsed > 0 else 0
projected_monthly   = daily_avg * days_in_month
projected_yearly    = projected_monthly * 12

log(f"\n── Summary ──────────────────────────────────────────────────────────────")
log(f"  cost_group_used = {cost_group_used}")
log(f"  Total API spend MTD:  ${total_api_spend_mtd:.4f}")
log(f"  Daily avg:            ${daily_avg:.4f}")
log(f"  Projected monthly:    ${projected_monthly:.2f}")
log(f"  Projected yearly:     ${projected_yearly:.2f}")

# Build agents list — merge all known entity IDs
# Prefer key_names (admin API keys); fall back to workspace_names; fall back to raw id
all_entity_ids = (
    set(key_cost_mtd.keys())
    | set(key_usage.keys())
    | set(key_model_cost.keys())
    | set(key_names.keys())
)

# Label map: use API key names if available, then workspace names, then raw id
def _entity_name(eid):
    if eid in key_names:
        return key_names[eid]
    if eid in workspace_names:
        return f"[WS] {workspace_names[eid]}"
    if eid == "all_api_keys":
        return "All API Keys (aggregate)"
    return eid[:24] + ("…" if len(eid) > 24 else "")

agents = []
for eid in sorted(all_entity_ids, key=lambda k: -key_cost_mtd.get(k, 0)):
    name    = _entity_name(eid)
    status  = key_status.get(eid, "active")
    cost    = key_cost_mtd.get(eid, 0)
    usage   = dict(key_usage.get(eid, {}))
    m_costs = {m: round(c, 6) for m, c in
               sorted(key_model_cost.get(eid, {}).items(), key=lambda x: -x[1])}
    d_costs = {d: round(c, 6) for d, c in
               sorted(agent_daily.get(eid, {}).items())}
    agents.append({
        "id":         eid,
        "name":       name,
        "status":     status,
        "costMtd":    round(cost, 4),
        "usage":      usage,
        "modelCosts": m_costs,
        "dailyCosts": d_costs,
    })

log(f"  Agents in output: {len(agents)}")
for a in agents[:10]:
    log(f"    {a['name']}: ${a['costMtd']:.4f}")

result = {
    "asOf":               data_until.strftime("%Y-%m-%d"),
    "monthStart":         month_start.strftime("%Y-%m-%d"),
    "totalApiSpendMtd":   round(total_api_spend_mtd, 4),
    "dailyAvg":           round(daily_avg, 6),
    "projectedMonthly":   round(projected_monthly, 2),
    "projectedYearly":    round(projected_yearly, 2),
    "daysElapsed":        days_elapsed,
    "daysInMonth":        days_in_month,
    "costGroupUsed":      cost_group_used or "none",
    "dataSource":         _analytics_key_source or "none",
    "adminKeyValid":      _analytics_key_source is not None and "fallback" not in (_analytics_key_source or ""),
    "globalModelCost":    {m: round(c, 6) for m, c in
                           sorted(global_model_cost.items(), key=lambda x: -x[1])},
    "modelTokenTotals":   model_token_totals,
    "agents":             agents,
}

OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
OUTPUT_PATH.write_text(json.dumps(result, indent=2), encoding="utf-8")
log(f"\nWrote {OUTPUT_PATH}  ({len(agents)} agents)")
