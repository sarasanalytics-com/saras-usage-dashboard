#!/usr/bin/env python3
"""
collect_api_agents.py — Fetch per-API-key (agent) cost and usage from platform.claude.com.

Uses ANTHROPIC_ADMIN_KEY (org admin key from platform.claude.com/settings/admin-keys).

Correct endpoints (WITHOUT /analytics/ prefix — those are claude.ai-only):
  GET /v1/organizations/api_keys                     — list keys + names
  GET /v1/organizations/workspaces                   — list workspaces
  GET /v1/organizations/cost_report                  — model cost rates (MTD, by description)
  GET /v1/organizations/usage_report/messages        — token usage per api_key_id per model

Cost computation (same pattern as claude-usage-alerts):
  1. cost_report gives total_cost[model][token_type_short]  (TOKEN_TYPE_MAP normalises names)
  2. usage_report/messages gives tokens[key_id][model][token_type_short]
  3. rate[model][token_type] = cost[model][token_type] / total_tokens[model][token_type]
  4. key_cost = sum( tokens[key][model][type] * rate[model][type] )

TOKEN_TYPE_MAP (critical — cost_report returns dot-notation names for cache_creation):
  "uncached_input_tokens"                    → "input"
  "cache_read_input_tokens"                  → "cache_read"
  "cache_creation.ephemeral_5m_input_tokens" → "cache_write"
  "cache_creation.ephemeral_1h_input_tokens" → "cache_write"
  "output_tokens"                            → "output"

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

# NOTE: cost_report "description" field is a model/billing label like
# "Claude Sonnet 4.6 Usage - Input Tokens, Cache Write" — NOT the API key name.
# Per-key costs must be computed via rates × tokens (see TOKEN_TYPE_MAP below).

REPO_ROOT   = Path(__file__).resolve().parent.parent
OUTPUT_PATH = REPO_ROOT / "data" / "api_agents_stats.json"

ADMIN_KEY = os.environ["ANTHROPIC_ADMIN_KEY"].strip()
ORG_BASE  = "https://api.anthropic.com/v1/organizations"

HEADERS = {
    "x-api-key":         ADMIN_KEY,
    "anthropic-version": "2023-06-01",
}
HEADERS_BETA = {
    "x-api-key":         ADMIN_KEY,
    "anthropic-version": "2023-06-01",
    "anthropic-beta":    "api-key-management-2025-02-19",
}

# Normalise the raw token_type strings returned by cost_report to the same
# short keys used when storing usage_report token counts.
# cost_report uses dot-notation for cache_creation sub-types.
TOKEN_TYPE_MAP = {
    "uncached_input_tokens":                    "input",
    "cache_read_input_tokens":                  "cache_read",
    "cache_creation.ephemeral_5m_input_tokens": "cache_write",
    "cache_creation.ephemeral_1h_input_tokens": "cache_write",
    "output_tokens":                            "output",
}
TOKEN_TYPES = ("input", "cache_read", "cache_write", "output")


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def get_json(url, headers=None, timeout=60):
    req = urllib.request.Request(url, headers=headers or HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        log(f"    HTTP {e.code} {e.reason}")
        log(f"    Body: {body[:600]}")
        raise


def org_get(path, params, headers=None):
    """Build /v1/organizations/{path}?params URL and fetch."""
    parts = []
    for k, v in params.items():
        if isinstance(v, list):
            for item in v:
                parts.append(f"{urllib.request.quote(k)}[]={urllib.request.quote(str(item))}")
        else:
            parts.append(f"{urllib.request.quote(k)}={urllib.request.quote(str(v))}")
    url = f"{ORG_BASE}/{path}?{'&'.join(parts)}"
    return get_json(url, headers or HEADERS)


# ── Date range ────────────────────────────────────────────────────────────────
today_utc   = datetime.now(timezone.utc).date()
# cost_report is real-time; include today by using tomorrow as exclusive END
# usage_report/messages has a 1-day delay so its END stays at today
data_until  = today_utc                              # portal shows today's costs
month_start = today_utc.replace(day=1)

# If it's early in the month (first 3 days), include last 7 days of previous month
# to show more meaningful data while current month is still accumulating
days_elapsed = (data_until - month_start).days + 1
if days_elapsed <= 3:
    lookback_start = month_start - timedelta(days=7)
    note = " (including last 7 days of previous month for context)"
else:
    lookback_start = month_start
    note = ""

days_in_month = calendar.monthrange(today_utc.year, today_utc.month)[1]

START      = lookback_start.strftime("%Y-%m-%dT00:00:00Z")
COST_END   = (today_utc + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")  # include today
USAGE_END  = (today_utc + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")  # include today (API requires strict inequality)

log(f"Collecting API agent stats  {lookback_start} → {data_until}  "
    f"({days_elapsed}/{days_in_month} days elapsed MTD){note}")
log(f"Admin key prefix: {ADMIN_KEY[:20]}...")


# ── 1. List API keys ──────────────────────────────────────────────────────────
log("\n── 1. Listing API keys ──────────────────────────────────────────────────")
key_names     = {}   # id → name
key_status    = {}   # id → 'active'|'inactive'
key_workspace = {}   # id → workspace_id (may be None)

for h_label, h in [("beta", HEADERS_BETA), ("plain", HEADERS)]:
    try:
        page = None
        while True:
            url = f"{ORG_BASE}/api_keys?limit=100"
            if page:
                url += f"&page={urllib.request.quote(str(page))}"
            data = get_json(url, h)
            for key in data.get("data", []):
                kid = key.get("id", "")
                if kid:
                    key_names[kid]     = key.get("name") or kid
                    key_status[kid]    = key.get("status", "active")
                    key_workspace[kid] = key.get("workspace_id")  # may be None
            if not data.get("has_more"):
                break
            page = data.get("next_page")
        log(f"  ✓ {len(key_names)} API keys ({h_label} header)")
        break
    except Exception as e:
        log(f"  [WARN] Key list failed ({h_label}): {e}")

# ── 2. List Workspaces ────────────────────────────────────────────────────────
log("\n── 2. Listing Workspaces ─────────────────────────────────────────────────")
workspace_names = {}

for h_label, h in [("beta", HEADERS_BETA), ("plain", HEADERS)]:
    try:
        data = get_json(f"{ORG_BASE}/workspaces?limit=100", h)
        for ws in data.get("data", []):
            wid = ws.get("id", "")
            if wid:
                workspace_names[wid] = ws.get("name") or wid
        log(f"  ✓ {len(workspace_names)} workspaces ({h_label} header)")
        for wid, wname in workspace_names.items():
            log(f"    {wname}  ({wid[:30]}...)")
        break
    except Exception as e:
        log(f"  [WARN] Workspace list failed ({h_label}): {e}")


# ── 3. Cost report: total cost by model × token_type (compute per-token rates) ──
log("\n── 3. Cost report (model × token_type rates) ───────────────────────────")
# model → short_token_type → total USD cost  (short types: input/cache_read/cache_write/output)
model_type_cost: dict = defaultdict(lambda: defaultdict(float))

def _fetch_cost_report(group_by_val):
    p = {
        "starting_at":  START,
        "ending_at":    COST_END,   # includes today
        "bucket_width": "1d",
        "group_by":     [group_by_val],
    }
    r = org_get("cost_report", p)
    rows_found = 0
    while True:
        for bucket in r.get("data", []):
            for item in bucket.get("results", []):
                mdl   = item.get("model") or "unknown"
                raw   = item.get("token_type") or ""
                ttype = TOKEN_TYPE_MAP.get(raw, "other")   # normalise to short name
                amt   = float(item.get("amount", 0) or 0) / 100.0   # cents → USD
                model_type_cost[mdl][ttype] += amt
                rows_found += 1
        if not r.get("has_more"):
            break
        p["page"] = r.get("next_page")
        r = org_get("cost_report", p)
        time.sleep(0.2)
    return rows_found

for group_by_val in ["description"]:  # API only supports "description" and "workspace_id"
    try:
        rows = _fetch_cost_report(group_by_val)
        total_cost = sum(c for tc in model_type_cost.values() for c in tc.values())
        log(f"  ✓ Cost report (group_by={group_by_val}): {rows} rows, total=${total_cost:.4f}")
        for mdl, tc in sorted(model_type_cost.items(), key=lambda x: -sum(x[1].values()))[:5]:
            log(f"    {mdl}: ${sum(tc.values()):.4f}  "
                f"(in={tc.get('input',0):.2f} cr={tc.get('cache_read',0):.2f} "
                f"cw={tc.get('cache_write',0):.2f} out={tc.get('output',0):.2f})")
        break
    except Exception as e:
        log(f"  [WARN] cost_report group_by={group_by_val} failed: {e}")
        model_type_cost.clear()
        continue


# ── 4. Usage report: tokens per api_key_id per model (daily, MTD) ─────────────
log("\n── 4. Usage report per api_key_id × model (daily) ──────────────────────")
# key_id → date → model → short_token_type → count
key_day_model_tokens: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(float))))
# key_id → model → short_token_type → total count (MTD)
key_model_tokens: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
# model → short_token_type → total across all keys (for rate computation)
all_model_tokens: dict = defaultdict(lambda: defaultdict(float))

usage_ok = False

try:
    params = {
        "starting_at":  START,
        "ending_at":    USAGE_END,  # through yesterday (1-day delay)
        "bucket_width": "1d",
        "group_by":     ["api_key_id", "model"],
    }
    resp = org_get("usage_report/messages", params)

    while True:
        for bucket in resp.get("data", []):
            date_str = (bucket.get("starting_at") or "")[:10]
            for item in bucket.get("results", []):
                kid = item.get("api_key_id") or "unknown"
                mdl = item.get("model") or "unknown"

                # Extract token counts using the same short names as TOKEN_TYPE_MAP
                cc = item.get("cache_creation") or {}
                tokens = {
                    "input":       float(item.get("uncached_input_tokens", 0) or 0),
                    "cache_read":  float(item.get("cache_read_input_tokens", 0) or 0),
                    "cache_write": float((cc.get("ephemeral_5m_input_tokens") or 0) +
                                         (cc.get("ephemeral_1h_input_tokens") or 0)),
                    "output":      float(item.get("output_tokens", 0) or 0),
                }
                # Store with short names — must match TOKEN_TYPE_MAP output above
                for ttype_short, cnt in tokens.items():
                    if cnt:
                        key_day_model_tokens[kid][date_str][mdl][ttype_short] += cnt
                        key_model_tokens[kid][mdl][ttype_short]               += cnt
                        all_model_tokens[mdl][ttype_short]                    += cnt

        if not resp.get("has_more"):
            break
        params["page"] = resp.get("next_page")
        resp = org_get("usage_report/messages", params)
        time.sleep(0.2)

    total_keys = len(key_model_tokens)
    log(f"  ✓ Usage report: {total_keys} keys with usage")
    usage_ok = True

    for kid in list(key_model_tokens.keys())[:5]:
        total_t = sum(
            t for mt in key_model_tokens[kid].values() for t in mt.values()
        )
        log(f"    {key_names.get(kid, kid[:20])}: {total_t:,.0f} tokens total")

except Exception as e:
    log(f"  [WARN] usage_report/messages failed: {e}")


# ── 5. Compute per-key costs ──────────────────────────────────────────────────
log("\n── 5. Computing per-key costs from token counts × rates ─────────────────")

# Build per-token-type rate: rate[model][short_type] = cost/token
# Both model_type_cost and all_model_tokens now use the same short keys
rates: dict = defaultdict(lambda: defaultdict(float))

for mdl, type_costs in model_type_cost.items():
    for ttype in TOKEN_TYPES:
        cost      = type_costs.get(ttype, 0)
        total_tok = all_model_tokens[mdl].get(ttype, 0)
        if total_tok > 0:
            rates[mdl][ttype] = cost / total_tok
            log(f"    rate {mdl} {ttype}: ${rates[mdl][ttype]:.8f}/tok")

# Compute per-key MTD cost via rates × tokens
key_cost_mtd: dict   = defaultdict(float)
key_model_cost: dict = defaultdict(lambda: defaultdict(float))

for kid, mdl_map in key_model_tokens.items():
    for mdl, type_map in mdl_map.items():
        for ttype, cnt in type_map.items():
            rate = rates[mdl].get(ttype, 0)
            cost = cnt * rate
            key_cost_mtd[kid]       += cost
            key_model_cost[kid][mdl] += cost

# Compute per-key daily cost (rates × tokens; used for trend charts)
agent_daily: dict = defaultdict(lambda: defaultdict(float))
for kid, day_map in key_day_model_tokens.items():
    for date_str, mdl_map in day_map.items():
        for mdl, type_map in mdl_map.items():
            for ttype, cnt in type_map.items():
                rate = rates[mdl].get(ttype, 0)
                agent_daily[kid][date_str] += cnt * rate

# Global model cost (cross-key sum, for header chart)
global_model_cost: dict = defaultdict(float)
for kid, mc in key_model_cost.items():
    for mdl, cost in mc.items():
        global_model_cost[mdl] += cost

# Global model tokens (cross-key sum, for model table token columns)
global_model_tokens: dict = defaultdict(lambda: defaultdict(float))
for mdl, type_map in all_model_tokens.items():
    for ttype, cnt in type_map.items():
        global_model_tokens[mdl][ttype] += cnt

total_api_spend_mtd = sum(key_cost_mtd.values())

# Calculate daily average based on actual date range (not just MTD days)
# When looking back into previous month, span includes those days too
actual_days_span = (data_until - lookback_start).days + 1
daily_avg = total_api_spend_mtd / actual_days_span if actual_days_span > 0 else 0

# For June 1st with lookback: actual spend is May 25-June 1 (7 days)
# So daily_avg should be spend/7, not spend/1
# But monthly projection should still be based on full month

projected_monthly   = daily_avg * days_in_month
projected_yearly    = projected_monthly * 12

analytics_working = len(key_cost_mtd) > 0

log(f"\n── Summary ──────────────────────────────────────────────────────────────")
log(f"  analyticsWorking: {analytics_working}")
log(f"  Total API spend MTD:  ${total_api_spend_mtd:.4f}")
log(f"  Daily avg:            ${daily_avg:.4f}")
log(f"  Projected monthly:    ${projected_monthly:.2f}")
log(f"  Projected yearly:     ${projected_yearly:.2f}")
log(f"  Keys with spend: {len([k for k, v in key_cost_mtd.items() if v > 0])}")
for kid, cost in sorted(key_cost_mtd.items(), key=lambda x: -x[1])[:10]:
    log(f"    {key_names.get(kid, kid[:20])}: ${cost:.4f}")


# ── 5b. Fetch previous month's total cost for comparison ─────────────────────
log(f"\n── Month-on-Month Comparison ───────────────────────────────────────────")
previous_month_total = 0
previous_month_start = (month_start - timedelta(days=1)).replace(day=1)
previous_month_end   = (month_start - timedelta(days=1))
previous_start_str   = previous_month_start.strftime("%Y-%m-%dT00:00:00Z")
previous_end_str     = (previous_month_end + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")

try:
    # Fetch cost_report for full previous month
    prev_cost_resp = org_get("cost_report", {
        "group_by": ["description"],
        "starting_at": previous_start_str,
        "ending_at": previous_end_str,
    })
    for row in prev_cost_resp.get("data", []):
        previous_month_total += row.get("cost", 0)
    log(f"  Previous month ({previous_month_start.strftime('%B %Y')}): ${previous_month_total:.4f}")
except Exception as e:
    log(f"  [WARN] Could not fetch previous month cost: {e}")
    previous_month_total = 0

# Calculate trend
trend_amount = total_api_spend_mtd - previous_month_total
trend_pct = (trend_amount / previous_month_total * 100) if previous_month_total > 0 else 0
trend_dir = "up" if trend_amount > 0 else ("down" if trend_amount < 0 else "flat")


# ── 6. Build agents list ──────────────────────────────────────────────────────
# Merge all known key IDs
all_key_ids = set(key_names.keys()) | set(key_cost_mtd.keys())

agents = []
for kid in sorted(all_key_ids, key=lambda k: -key_cost_mtd.get(k, 0)):
    wid  = key_workspace.get(kid) or ""
    name = key_names.get(kid, kid[:24])
    cost = key_cost_mtd.get(kid, 0)
    agents.append({
        "id":          kid,
        "name":        name,
        "status":      key_status.get(kid, "active"),
        "workspace":   workspace_names.get(wid, wid) if wid else "",
        "costMtd":     round(cost, 4),
        "usage":       {},
        "modelCosts":  {m: round(c, 6) for m, c in
                        sorted(key_model_cost.get(kid, {}).items(), key=lambda x: -x[1])},
        "dailyCosts":  {d: round(c, 6) for d, c in
                        sorted(agent_daily.get(kid, {}).items())},
    })

log(f"  Agents in output: {len(agents)}")


result = {
    "asOf":              data_until.strftime("%Y-%m-%d"),
    "monthStart":        month_start.strftime("%Y-%m-%d"),
    "dataRange":         f"{lookback_start.strftime('%Y-%m-%d')} to {data_until.strftime('%Y-%m-%d')}",
    "dataRangeSpan":     f"{actual_days_span} days",
    "totalApiSpendMtd":  round(total_api_spend_mtd, 4),
    "dailyAvg":          round(daily_avg, 6),
    "projectedMonthly":  round(projected_monthly, 2),
    "projectedYearly":   round(projected_yearly, 2),
    "daysElapsed":       days_elapsed,  # Actual MTD days in current month
    "daysInMonth":       days_in_month,
    "analyticsWorking":  analytics_working,
    "adminKeyValid":     len(key_names) > 0,
    "globalModelCost":   {m: round(c, 6) for m, c in
                          sorted(global_model_cost.items(), key=lambda x: -x[1])},
    "globalModelTokens": {m: {t: int(c) for t, c in tv.items()}
                          for m, tv in global_model_tokens.items()},
    "spendComparison": {
        "previousMonthTotal": round(previous_month_total, 4),
        "previousMonthLabel": previous_month_start.strftime("%B %Y"),
        "currentMonthMtd": round(total_api_spend_mtd, 4),
        "currentMonthLabel": month_start.strftime("%B %Y"),
        "trendAmount": round(trend_amount, 4),
        "trendPercentage": round(trend_pct, 1),
        "trendDirection": trend_dir,
    },
    "agents":            agents,
}

OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
OUTPUT_PATH.write_text(json.dumps(result, indent=2), encoding="utf-8")
log(f"\nWrote {OUTPUT_PATH}  ({len(agents)} agents, analyticsWorking={analytics_working})")
log(f"  Month-on-Month: {previous_month_start.strftime('%b %Y')} ${previous_month_total:.2f} → {month_start.strftime('%b %Y')} ${total_api_spend_mtd:.2f} ({trend_dir} {abs(trend_pct):.1f}%)")
