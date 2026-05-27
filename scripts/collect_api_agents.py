#!/usr/bin/env python3
"""
collect_api_agents.py — Fetch per-API-key (agent) cost and usage from platform.claude.com.

Uses ANTHROPIC_ADMIN_KEY (org admin key from platform.claude.com/settings/admin-keys) to:
1. List API key names via Admin API  (/v1/organizations/api_keys)
2. List workspaces                   (/v1/organizations/workspaces)
3. Attempt cost/usage per workspace  (/v1/organizations/workspaces/{id}/usage  — exploratory)
4. Attempt analytics cost_report with admin key (403 expected — admin keys lack analytics perm)

NOTE: Anthropic's analytics API currently does NOT support:
  - Admin key authentication for cost_report (returns 403)
  - group_by: api_key_id or workspace_id (returns 400 / "not yet supported")
  - group_by: rbac_group_id or claude_project_id (returns 400 / "not yet supported")
Until Anthropic exposes per-key analytics, cost data will be $0.
The analytics key (ANTHROPIC_ANALYTICS_KEY) is intentionally NOT used as fallback
because it accesses claude.ai team product costs — different product, would double-count.

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

# Analytics headers — admin key only (403 expected; no claude.ai fallback to avoid double-counting)
ANALYTICS_HEADERS = {
    "x-api-key":          ADMIN_KEY,
    "anthropic-version":  "2023-06-01",
}


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
        log(f"    Body: {body[:600]}")
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


# ── 1. List API key names ─────────────────────────────────────────────────────
log("\n── 1. Listing API keys via Admin API ────────────────────────────────────")
key_names  = {}   # id → name
key_status = {}   # id → status (active/inactive)
key_workspace = {}  # id → workspace_id

for attempt_label, headers in [
    ("with beta header",    ADMIN_HEADERS_BETA),
    ("without beta header", ADMIN_HEADERS_NO_BETA),
]:
    try:
        page = None
        while True:
            url = f"{ADMIN_BASE}/organizations/api_keys?limit=100"
            if page:
                url += f"&page={urllib.request.quote(str(page))}"
            data = get_json(url, headers)
            for key in data.get("data", []):
                kid    = key.get("id", "")
                name   = key.get("name") or kid
                status = key.get("status", "active")
                ws_id  = key.get("workspace_id", "")
                if kid:
                    key_names[kid]     = name
                    key_status[kid]    = status
                    key_workspace[kid] = ws_id
            if not data.get("has_more"):
                break
            page = data.get("next_page")
        log(f"  ✓ Found {len(key_names)} API keys ({attempt_label})")
        for kid, name in list(key_names.items())[:20]:
            ws = key_workspace.get(kid, '')
            log(f"    [{key_status.get(kid,'?')}] {name}  ws={ws[:20] if ws else 'default'}")
        break
    except Exception as e:
        log(f"  [WARN] Key list failed ({attempt_label}): {e}")
        continue


# ── 2. List Workspaces ────────────────────────────────────────────────────────
log("\n── 2. Listing Workspaces ────────────────────────────────────────────────")
workspace_names = {}  # id → name
workspace_keys  = defaultdict(list)  # workspace_id → [key_ids]

for attempt_label, headers in [
    ("with beta header",    ADMIN_HEADERS_BETA),
    ("without beta header", ADMIN_HEADERS_NO_BETA),
]:
    try:
        url  = f"{ADMIN_BASE}/organizations/workspaces?limit=100"
        data = get_json(url, headers)
        for ws in data.get("data", []):
            wid  = ws.get("id", "")
            name = ws.get("name") or wid
            if wid:
                workspace_names[wid] = name
        log(f"  ✓ Found {len(workspace_names)} workspaces ({attempt_label})")
        for wid, name in workspace_names.items():
            log(f"    {name}  ({wid[:30]}...)")
        break
    except Exception as e:
        log(f"  [WARN] Workspace list failed ({attempt_label}): {e}")
        continue

# Build workspace → keys mapping
for kid, wid in key_workspace.items():
    if wid:
        workspace_keys[wid].append(kid)


# ── 3. Try analytics cost_report with admin key (expected 403) ────────────────
log("\n── 3. Analytics cost_report with admin key ──────────────────────────────")
log("  (Admin keys have management perms only; analytics requires separate auth)")

BASE_COST_PARAMS = {
    "starting_at":  month_start.strftime("%Y-%m-%dT00:00:00Z"),
    "ending_at":    (data_until + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z"),
    "bucket_width": "1d",
}

global_model_cost  = defaultdict(float)
cost_group_used    = None
analytics_working  = False
key_cost_mtd       = defaultdict(float)
agent_daily        = defaultdict(lambda: defaultdict(float))
key_model_cost     = defaultdict(lambda: defaultdict(float))

# Try model-level aggregate (most permissive) to test if admin key can access analytics at all
try:
    params = {**BASE_COST_PARAMS, "group_by": ["model"]}
    resp   = analytics_get("cost_report", params)
    model_cost_daily = {}

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

    total = sum(global_model_cost.values())
    log(f"  ✓ Admin key CAN access analytics! Total model cost: ${total:.4f}")
    analytics_working = True
    cost_group_used   = "model"

    # Build aggregate agent entry using daily model data
    for date_str, mdl_map in model_cost_daily.items():
        day_total = sum(mdl_map.values())
        agent_daily["all_api_keys"][date_str] += day_total
        key_cost_mtd["all_api_keys"] += day_total
        for mdl, cost in mdl_map.items():
            key_model_cost["all_api_keys"][mdl] += cost

except Exception as e:
    log(f"  [INFO] Admin key cannot access analytics (expected): {e}")
    log("  → Cost data will show $0 until Anthropic enables per-key analytics")


# ── 4. Try workspace-level usage endpoints (exploratory) ──────────────────────
log("\n── 4. Workspace-level usage endpoints (exploratory) ─────────────────────")
workspace_costs = {}  # workspace_id → total_cost

for wid, wname in list(workspace_names.items())[:3]:  # try first 3 only
    for endpoint in [
        f"{ADMIN_BASE}/organizations/workspaces/{wid}/usage",
        f"{ADMIN_BASE}/organizations/workspaces/{wid}/analytics",
    ]:
        try:
            data = get_json(endpoint, ADMIN_HEADERS_BETA)
            log(f"  ✓ {wname}: {endpoint} → keys: {list(data.keys())[:8]}")
            workspace_costs[wid] = data
            break
        except Exception as e:
            log(f"  [INFO] {wname} {endpoint}: {e}")


# ── 5. Build projections ──────────────────────────────────────────────────────
total_api_spend_mtd = sum(key_cost_mtd.values())
daily_avg           = total_api_spend_mtd / days_elapsed if days_elapsed > 0 else 0
projected_monthly   = daily_avg * days_in_month
projected_yearly    = projected_monthly * 12

log(f"\n── Summary ──────────────────────────────────────────────────────────────")
log(f"  cost_group_used = {cost_group_used}")
log(f"  analytics_working = {analytics_working}")
log(f"  Total API spend MTD:  ${total_api_spend_mtd:.4f}")
log(f"  Daily avg:            ${daily_avg:.4f}")
log(f"  Projected monthly:    ${projected_monthly:.2f}")
log(f"  Projected yearly:     ${projected_yearly:.2f}")

# Build agents list — one entry per API key, plus any aggregate
# Priority: individual keys from key listing first, then aggregate if present
def _entity_name(eid):
    if eid in key_names:
        return key_names[eid]
    if eid in workspace_names:
        return workspace_names[eid]
    if eid == "all_api_keys":
        return "All API Keys (aggregate)"
    return eid[:24] + ("…" if len(eid) > 24 else "")

# Build per-key cost breakdown if analytics worked at aggregate level only
# (No per-key cost available — all keys show $0)
all_entity_ids = set(key_names.keys()) | set(key_cost_mtd.keys())

agents = []
# First add aggregate entry if it has spend
if "all_api_keys" in key_cost_mtd and key_cost_mtd["all_api_keys"] > 0:
    eid = "all_api_keys"
    agents.append({
        "id":          eid,
        "name":        "All API Keys (aggregate)",
        "status":      "active",
        "costMtd":     round(key_cost_mtd[eid], 4),
        "usage":       {},
        "modelCosts":  {m: round(c, 6) for m, c in
                        sorted(key_model_cost[eid].items(), key=lambda x: -x[1])},
        "dailyCosts":  {d: round(c, 6) for d, c in
                        sorted(agent_daily[eid].items())},
        "isAggregate": True,
    })

# Then add individual key entries (sorted by workspace then name)
for kid in sorted(key_names.keys(),
                  key=lambda k: (key_workspace.get(k) or '', key_names.get(k) or '')):
    wid  = key_workspace.get(kid) or ''
    name = key_names[kid]
    agents.append({
        "id":          kid,
        "name":        name,
        "status":      key_status.get(kid, "active"),
        "workspace":   workspace_names.get(wid, wid) if wid else "",
        "costMtd":     round(key_cost_mtd.get(kid, 0), 4),
        "usage":       {},
        "modelCosts":  {m: round(c, 6) for m, c in
                        sorted(key_model_cost.get(kid, {}).items(), key=lambda x: -x[1])},
        "dailyCosts":  {d: round(c, 6) for d, c in
                        sorted(agent_daily.get(kid, {}).items())},
    })

log(f"  Agents in output: {len(agents)}")
for a in agents[:5]:
    log(f"    {a['name']}: ${a['costMtd']:.4f}")

result = {
    "asOf":                data_until.strftime("%Y-%m-%d"),
    "monthStart":          month_start.strftime("%Y-%m-%d"),
    "totalApiSpendMtd":    round(total_api_spend_mtd, 4),
    "dailyAvg":            round(daily_avg, 6),
    "projectedMonthly":    round(projected_monthly, 2),
    "projectedYearly":     round(projected_yearly, 2),
    "daysElapsed":         days_elapsed,
    "daysInMonth":         days_in_month,
    "costGroupUsed":       cost_group_used or "none",
    "analyticsWorking":    analytics_working,
    "adminKeyValid":       len(key_names) > 0,
    "analyticsNote":       "Admin key (api:admin scope) cannot access analytics. Need a key with read:analytics scope — see platform.claude.com/settings/api-keys or service accounts." if not analytics_working else "",
    "globalModelCost":     {m: round(c, 6) for m, c in
                            sorted(global_model_cost.items(), key=lambda x: -x[1])},
    "workspaceNames":      workspace_names,
    "agents":              agents,
}

OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
OUTPUT_PATH.write_text(json.dumps(result, indent=2), encoding="utf-8")
log(f"\nWrote {OUTPUT_PATH}  ({len(agents)} agents, analyticsWorking={analytics_working})")
