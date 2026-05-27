#!/usr/bin/env python3
"""
collect_api_agents.py — Fetch per-API-key (agent) cost and usage from platform.claude.com.

Uses ANTHROPIC_ADMIN_KEY (org admin key from platform.claude.com/settings/admin-keys).

Correct endpoints (WITHOUT /analytics/ prefix — those are claude.ai-only):
  GET /v1/organizations/api_keys                     — list keys + names
  GET /v1/organizations/workspaces                   — list workspaces
  GET /v1/organizations/cost_report                  — model cost rates (MTD, by description)
  GET /v1/organizations/usage_report/messages        — token usage per api_key_id per model

Cost computation (two-step, same as claude-usage-alerts):
  1. cost_report gives total_cost[model][token_type]
  2. usage_report/messages gives tokens[key_id][model][token_type]
  3. rate[model][token_type] = cost[model][token_type] / total_tokens[model][token_type]
  4. key_cost = sum( tokens[key][model][type] * rate[model][type] )

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
data_until  = today_utc - timedelta(days=1)   # yesterday (usage_report has 1-day delay)
month_start = today_utc.replace(day=1)

days_in_month = calendar.monthrange(today_utc.year, today_utc.month)[1]
days_elapsed  = max(1, (data_until - month_start).days + 1)

START = month_start.strftime("%Y-%m-%dT00:00:00Z")
END   = today_utc.strftime("%Y-%m-%dT00:00:00Z")   # exclusive upper bound

log(f"Collecting API agent stats  {month_start} → {data_until}  "
    f"({days_elapsed}/{days_in_month} days elapsed)")
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
# model → token_type → total USD cost
model_type_cost   = defaultdict(lambda: defaultdict(float))
# model → token_type → total tokens (filled from usage_report below)
model_type_tokens = defaultdict(lambda: defaultdict(float))

try:
    params = {
        "starting_at":  START,
        "ending_at":    END,
        "bucket_width": "1month",
        "group_by":     ["model", "token_type"],
    }
    resp = org_get("cost_report", params)

    while True:
        for bucket in resp.get("data", []):
            for item in bucket.get("results", []):
                mdl   = item.get("model") or "unknown"
                ttype = item.get("token_type") or item.get("description") or "unknown"
                amt   = float(item.get("amount", 0) or 0) / 100.0   # cents → USD
                model_type_cost[mdl][ttype] += amt

        if not resp.get("has_more"):
            break
        params["page"] = resp.get("next_page")
        resp = org_get("cost_report", params)
        time.sleep(0.2)

    total_cost = sum(c for tc in model_type_cost.values() for c in tc.values())
    log(f"  ✓ Cost report: {len(model_type_cost)} models, total=${total_cost:.4f}")
    for mdl, tc in sorted(model_type_cost.items(), key=lambda x: -sum(x[1].values()))[:5]:
        log(f"    {mdl}: ${sum(tc.values()):.4f}")

except Exception as e:
    log(f"  [WARN] cost_report failed: {e}")
    # Fallback: try with group_by description
    try:
        params = {
            "starting_at":  START,
            "ending_at":    END,
            "bucket_width": "1month",
            "group_by":     ["description"],
        }
        resp = org_get("cost_report", params)
        for bucket in resp.get("data", []):
            for item in bucket.get("results", []):
                mdl   = item.get("model") or "unknown"
                ttype = item.get("token_type") or "unknown"
                amt   = float(item.get("amount", 0) or 0) / 100.0
                model_type_cost[mdl][ttype] += amt
        total_cost = sum(c for tc in model_type_cost.values() for c in tc.values())
        log(f"  ✓ Cost report (description fallback): total=${total_cost:.4f}")
    except Exception as e2:
        log(f"  [WARN] cost_report fallback also failed: {e2}")


# ── 4. Usage report: tokens per api_key_id per model (daily, MTD) ─────────────
log("\n── 4. Usage report per api_key_id × model (daily) ──────────────────────")
# key_id → date → model → token_type → count
key_day_model_tokens: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(float))))
# key_id → model → token_type → total count (MTD)
key_model_tokens: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
# model → token_type → total across all keys (for rate computation)
all_model_tokens: dict = defaultdict(lambda: defaultdict(float))

usage_ok = False

try:
    params = {
        "starting_at":  START,
        "ending_at":    END,
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

                # Extract all token type counts
                cc = item.get("cache_creation") or {}
                tokens = {
                    "input":       float(item.get("uncached_input_tokens", 0) or 0),
                    "cache_read":  float(item.get("cache_read_input_tokens", 0) or 0),
                    "cache_write": float((cc.get("ephemeral_5m_input_tokens") or 0) +
                                         (cc.get("ephemeral_1h_input_tokens") or 0)),
                    "output":      float(item.get("output_tokens", 0) or 0),
                }
                # Also capture original names the cost_report might use
                tok_map = {
                    "input":       "uncached_input_tokens",
                    "cache_read":  "cache_read_input_tokens",
                    "cache_write": "cache_creation_input_tokens",
                    "output":      "output_tokens",
                }
                for short, orig in tok_map.items():
                    cnt = tokens[short]
                    if cnt:
                        key_day_model_tokens[kid][date_str][mdl][orig]  += cnt
                        key_model_tokens[kid][mdl][orig]                 += cnt
                        all_model_tokens[mdl][orig]                      += cnt

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

# Build per-token-type rate: rate[model][token_type] = cost/token
rates: dict = defaultdict(lambda: defaultdict(float))

for mdl, type_costs in model_type_cost.items():
    for ttype, cost in type_costs.items():
        total_toks = all_model_tokens[mdl].get(ttype, 0)
        if total_toks > 0:
            rates[mdl][ttype] = cost / total_toks
            log(f"    rate {mdl} {ttype}: ${rates[mdl][ttype]:.8f}/tok")

# Compute per-key MTD cost
key_cost_mtd: dict = defaultdict(float)
key_model_cost: dict = defaultdict(lambda: defaultdict(float))

for kid, mdl_map in key_model_tokens.items():
    for mdl, type_map in mdl_map.items():
        for ttype, cnt in type_map.items():
            rate = rates[mdl].get(ttype, 0)
            cost = cnt * rate
            key_cost_mtd[kid]       += cost
            key_model_cost[kid][mdl] += cost

# Compute per-key daily cost
agent_daily: dict = defaultdict(lambda: defaultdict(float))
for kid, day_map in key_day_model_tokens.items():
    for date_str, mdl_map in day_map.items():
        for mdl, type_map in mdl_map.items():
            for ttype, cnt in type_map.items():
                rate = rates[mdl].get(ttype, 0)
                agent_daily[kid][date_str] += cnt * rate

# Global model cost (cross-key sum)
global_model_cost: dict = defaultdict(float)
for kid, mc in key_model_cost.items():
    for mdl, cost in mc.items():
        global_model_cost[mdl] += cost

total_api_spend_mtd = sum(key_cost_mtd.values())
daily_avg           = total_api_spend_mtd / days_elapsed if days_elapsed > 0 else 0
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


# ── 6. Build agents list ──────────────────────────────────────────────────────
def _entity_name(eid):
    if eid in key_names:
        return key_names[eid]
    if eid in workspace_names:
        return workspace_names[eid]
    return eid[:24] + ("…" if len(eid) > 24 else "")

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
    "asOf":             data_until.strftime("%Y-%m-%d"),
    "monthStart":       month_start.strftime("%Y-%m-%d"),
    "totalApiSpendMtd": round(total_api_spend_mtd, 4),
    "dailyAvg":         round(daily_avg, 6),
    "projectedMonthly": round(projected_monthly, 2),
    "projectedYearly":  round(projected_yearly, 2),
    "daysElapsed":      days_elapsed,
    "daysInMonth":      days_in_month,
    "analyticsWorking": analytics_working,
    "adminKeyValid":    len(key_names) > 0,
    "globalModelCost":  {m: round(c, 6) for m, c in
                         sorted(global_model_cost.items(), key=lambda x: -x[1])},
    "agents":           agents,
}

OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
OUTPUT_PATH.write_text(json.dumps(result, indent=2), encoding="utf-8")
log(f"\nWrote {OUTPUT_PATH}  ({len(agents)} agents, analyticsWorking={analytics_working})")
