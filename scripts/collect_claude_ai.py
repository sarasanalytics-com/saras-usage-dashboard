#!/usr/bin/env python3
"""
collect_claude_ai.py — Fetch all claude.ai analytics via Anthropic Analytics API.

Writes data/claude_ai_stats.json automatically — no browser needed.

Required environment variable:
  ANTHROPIC_ANALYTICS_KEY   sk-ant-api01-... key from claude.ai/analytics/api-keys

Note: API has a 3-day data delay. Today's data is always from 3 days ago.
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

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = REPO_ROOT / "data" / "claude_ai_stats.json"

API_KEY  = os.environ["ANTHROPIC_ANALYTICS_KEY"].strip()  # strip trailing newline if any
BASE_URL = "https://api.anthropic.com/v1/organizations/analytics"
HEADERS  = {
    "x-api-key":          API_KEY,
    "anthropic-version":  "2023-06-01",
}


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def get_json(path, params=None, timeout=60):
    url = f"{BASE_URL}/{path}"
    if params:
        qs  = "&".join(f"{k}={urllib.request.quote(str(v))}" for k, v in params.items())
        url = f"{url}?{qs}"
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


# ── Admin API (cost_report / usage_report) ────────────────────────────────────
# Requires a separate Admin API key (sk-ant-admin...) from console.anthropic.com
# This is different from the Analytics API key used above.
ADMIN_KEY      = os.environ.get("ANTHROPIC_ADMIN_KEY", "").strip()
ADMIN_BASE_URL = "https://api.anthropic.com/v1/organizations"
ADMIN_HEADERS  = {
    "x-api-key":         ADMIN_KEY,
    "anthropic-version": "2023-06-01",
}


def get_admin_json(path, params=None, timeout=60):
    """Fetch from the Anthropic Admin API. Handles list params as key[]=val."""
    url = f"{ADMIN_BASE_URL}/{path}"
    if params:
        parts = []
        for k, v in params.items():
            if isinstance(v, list):
                for item in v:
                    parts.append(f"{urllib.request.quote(str(k))}[]={urllib.request.quote(str(item))}")
            else:
                parts.append(f"{urllib.request.quote(str(k))}={urllib.request.quote(str(v))}")
        url = f"{url}?{'&'.join(parts)}"
    req = urllib.request.Request(url, headers=ADMIN_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


# ── Date range ────────────────────────────────────────────────────────────────
today_utc           = datetime.now(timezone.utc).date()
data_until          = today_utc - timedelta(days=3)   # 3-day API delay
month_start         = today_utc.replace(day=1)

all_dates = []
d = month_start
while d <= data_until:
    all_dates.append(d.strftime("%Y-%m-%d"))
    d += timedelta(days=1)

log(f"Collecting claude.ai analytics for {month_start} → {data_until} ({len(all_dates)} days)")


# ── 1. Activity summaries ─────────────────────────────────────────────────────
log("Fetching activity summaries...")
try:
    summ_data = get_json("summaries", {
        "starting_date": month_start.strftime("%Y-%m-%d"),
        "ending_date":   (data_until + timedelta(days=1)).strftime("%Y-%m-%d"),
    })
    summaries = summ_data.get("summaries", [])
except Exception as e:
    log(f"  [WARN] Summaries failed: {e}")
    summaries = []

latest = summaries[-1] if summaries else {}
wau             = latest.get("weekly_active_user_count", 0)
mau             = latest.get("monthly_active_user_count", 0)
assigned_seats  = latest.get("assigned_seat_count", 150)
pending_invites = latest.get("pending_invite_count", 0)
weekly_adoption = latest.get("weekly_adoption_rate", 0)
cowork_wau      = latest.get("cowork_weekly_active_user_count", 0)
cowork_mau      = latest.get("cowork_monthly_active_user_count", 0)

log(f"  WAU={wau} MAU={mau} Seats={assigned_seats} Cowork-MAU={cowork_mau}")


# ── 2. Per-user daily activity ────────────────────────────────────────────────
log(f"Fetching per-user activity ({len(all_dates)} days × all users)...")

# MTD accumulators
user_accepted      = defaultdict(int)   # email → tool-invocation accepts (fallback count)
user_tool_lines    = defaultdict(int)   # email → lines from tool_actions.*.accepted_line_count
user_loc_added     = defaultdict(int)   # email → lines from core_metrics.lines_of_code.added_count
user_rejected      = defaultdict(int)   # email → total tool rejects
user_cc_active     = set()              # emails with any CC session MTD
user_chats         = defaultdict(int)   # email → total chat conversations MTD
user_cowork        = defaultdict(int)   # email → total cowork sessions MTD

daily_chat_convos   = []             # total conversations per day
daily_cowork_sess   = []             # total cowork sessions per day
chat_users_mtd      = set()          # users with ≥1 chat MTD
project_created_mtd = defaultdict(int)  # email → projects created
artifact_created_mtd = defaultdict(int) # email → artifacts created
project_users_mtd   = set()
artifact_users_mtd  = set()

_debug_logged = False   # log full structure of first user once

for date_str in all_dates:
    day_convos        = 0
    day_cowork        = 0
    page              = None
    pages_this_day    = 0

    while True:
        params = {"date": date_str, "limit": 1000}
        if page:
            params["page"] = page

        try:
            data = get_json("users", params)
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            log(f"  [{date_str}] HTTP {e.code}: {body[:120]}")
            break
        except Exception as e:
            log(f"  [{date_str}] Error: {e}")
            break

        pages_this_day += 1
        for user in data.get("data", []):
            email = user["user"]["email_address"].lower().strip()

            ccm = user.get("claude_code_metrics", {})

            # ── Debug: log full structure of first active user once ────────────
            if not _debug_logged and ccm:
                log(f"\n[DEBUG] Sample claude_code_metrics keys: {list(ccm.keys())}")
                core = ccm.get("core_metrics", {})
                log(f"[DEBUG] core_metrics keys: {list(core.keys())}")
                loc = core.get("lines_of_code", {})
                log(f"[DEBUG] lines_of_code: {loc}")
                ta_debug = ccm.get("tool_actions", {})
                log(f"[DEBUG] tool_actions keys: {list(ta_debug.keys())}")
                if ta_debug:
                    first_tool = next(iter(ta_debug))
                    log(f"[DEBUG] tool_actions.{first_tool}: {ta_debug[first_tool]}")
                _debug_logged = True

            # ── Claude Code tool actions ──────────────────────────────────────
            ta = ccm.get("tool_actions", {})
            for tool in ("edit_tool", "multi_edit_tool", "write_tool", "notebook_edit_tool"):
                t = ta.get(tool, {})
                user_accepted[email]   += t.get("accepted_count", 0)
                user_rejected[email]   += t.get("rejected_count", 0)
                # line-level counts within each tool (may or may not exist)
                user_tool_lines[email] += t.get("accepted_line_count", 0)
                user_tool_lines[email] += t.get("lines_accepted", 0)

            # ── lines_of_code from core_metrics (git-commit lines) ────────────
            loc = ccm.get("core_metrics", {}).get("lines_of_code", {})
            user_loc_added[email] += loc.get("added_count", 0)

            # Claude Code session presence
            cc_sessions = (user.get("claude_code_metrics", {})
                           .get("core_metrics", {})
                           .get("distinct_session_count", 0))
            if cc_sessions > 0:
                user_cc_active.add(email)

            # Chat metrics
            cm = user.get("chat_metrics", {})
            convos = cm.get("distinct_conversation_count", 0)
            day_convos += convos
            if convos > 0:
                chat_users_mtd.add(email)
                user_chats[email] += convos

            proj_created = cm.get("distinct_projects_created_count", 0)
            if proj_created > 0:
                project_created_mtd[email] += proj_created
                project_users_mtd.add(email)

            art_created = cm.get("distinct_artifacts_created_count", 0)
            if art_created > 0:
                artifact_created_mtd[email] += art_created
                artifact_users_mtd.add(email)

            # Cowork sessions
            cw_sessions = (user.get("cowork_metrics", {})
                           .get("distinct_session_count", 0))
            day_cowork += cw_sessions
            if cw_sessions > 0:
                user_cowork[email] += cw_sessions

        if not data.get("has_more"):
            break
        page = data.get("next_page")
        time.sleep(0.3)  # rate-limit buffer

    daily_chat_convos.append(day_convos)
    daily_cowork_sess.append(day_cowork)
    log(f"  {date_str}: convos={day_convos} cowork_sess={day_cowork} (pages={pages_this_day})")
    time.sleep(0.2)


# ── 3. Derived metrics ────────────────────────────────────────────────────────
total_accepted     = sum(user_accepted.values())   # tool invocation accepts
total_rejected     = sum(user_rejected.values())
total_actions      = total_accepted + total_rejected
accept_rate        = round(100 * total_accepted / total_actions, 1) if total_actions else 0

# Prefer line-level metrics over invocation counts (priority: tool_lines > loc_added > invocations)
total_tool_lines   = sum(user_tool_lines.values())
total_loc_added    = sum(user_loc_added.values())

if total_tool_lines > 0:
    user_lines_final = user_tool_lines
    total_lines_out  = total_tool_lines
    lines_source     = "tool_actions.accepted_line_count"
elif total_loc_added > 0:
    user_lines_final = user_loc_added
    total_lines_out  = total_loc_added
    lines_source     = "core_metrics.lines_of_code.added_count"
else:
    user_lines_final = user_accepted
    total_lines_out  = total_accepted
    lines_source     = "tool_actions.accepted_count (invocations — fallback)"

log(f"  Tool accepts (invocations): {total_accepted:,}")
log(f"  Tool line counts:           {total_tool_lines:,}")
log(f"  LoC added (git):            {total_loc_added:,}")
log(f"  → Using [{lines_source}] = {total_lines_out:,}")

# Exclude service/shared accounts — not real individual contributors
BOT_EMAILS = {'consulting@sarasanalytics.com', 'consulting.claude@sarasanalytics.com'}

# Subtract bot lines from total so KPI shows real human-only lines
bot_lines      = sum(user_lines_final.get(e, 0) for e in BOT_EMAILS)
total_lines_out -= bot_lines
log(f"  Bot lines excluded: {bot_lines:,}  → Human total: {total_lines_out:,}")

# Active members = users with accepted lines > 0 OR CC sessions > 0 (bots excluded)
active_emails      = ({e for e, v in user_lines_final.items() if v > 0} | user_cc_active) - BOT_EMAILS
active_members     = len(active_emails)
total_members      = sum(1 for e in user_accepted if e not in BOT_EMAILS)

avg_chat_per_day   = round(sum(daily_chat_convos) / len(daily_chat_convos), 0) if daily_chat_convos else 0
avg_cowork_per_day = round(sum(daily_cowork_sess)  / len(daily_cowork_sess),  0) if daily_cowork_sess else 0

chat_user_pct      = round(100 * len(chat_users_mtd)    / assigned_seats) if assigned_seats else 0
cowork_user_pct    = round(100 * cowork_mau              / assigned_seats) if assigned_seats else 0
project_user_pct   = round(100 * len(project_users_mtd) / assigned_seats) if assigned_seats else 0
artifact_user_pct  = round(100 * len(artifact_users_mtd) / assigned_seats) if assigned_seats else 0

projects_created_mtd  = sum(project_created_mtd.values())
artifacts_created_mtd = sum(artifact_created_mtd.values())

log(f"\n=== Summary ===")
log(f"  totalLines = {total_lines_out:,} ({lines_source})")
log(f"  Accept rate: {accept_rate}%  |  Total invocations: {total_accepted:,}")
log(f"  Active CC members: {active_members} / {total_members}")
log(f"  WAU: {wau}  |  Cowork MAU: {cowork_mau}")
log(f"  Chat users: {len(chat_users_mtd)}  |  Avg chats/day: {avg_chat_per_day}")



# ── 4. Model-level costs + usage via Admin API ────────────────────────────────
# Requires ANTHROPIC_ADMIN_KEY (Admin API key, different from Analytics key).
# Get one at: console.anthropic.com → Settings → API Keys → Create Admin Key
log("\nFetching model-level costs via Admin API cost_report...")
model_usage = {}
model_cost  = {}

if not ADMIN_KEY:
    log("  [WARN] ANTHROPIC_ADMIN_KEY not set — model cost data unavailable")
    log("  [INFO] Add an Admin API key (sk-ant-admin...) as GitHub secret ANTHROPIC_ADMIN_KEY")
else:
    # ── 4a. Cost breakdown per model ─────────────────────────────────────────
    try:
        cost_params = {
            "starting_at":  month_start.strftime("%Y-%m-%dT00:00:00Z"),
            "ending_at":    (data_until + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z"),
            "bucket_width": "1d",
            "group_by":     ["description"],
        }
        cost_resp = get_admin_json("cost_report", cost_params)

        # Paginate through all results
        all_cost_items = []
        while True:
            for bucket in cost_resp.get("data", []):
                all_cost_items.extend(bucket.get("results", []))
            if not cost_resp.get("has_more"):
                break
            cost_params["page"] = cost_resp.get("next_page")
            cost_resp = get_admin_json("cost_report", cost_params)
            time.sleep(0.2)

        # Sum costs by model (only token costs, skip web_search / code_execution)
        for item in all_cost_items:
            mdl        = item.get("model")
            cost_type  = item.get("cost_type", "")
            amount_str = item.get("amount", "0") or "0"
            if not mdl or cost_type != "tokens":
                continue
            try:
                # amount is in cents (lowest USD units) as a decimal string
                # e.g. "123.45" = 123.45 cents = $1.23
                cost_usd = float(amount_str) / 100.0
            except (ValueError, TypeError):
                cost_usd = 0.0
            model_cost[mdl] = model_cost.get(mdl, 0) + cost_usd

        if model_cost:
            log(f"  Cost breakdown by model:")
            for mdl, cost in sorted(model_cost.items(), key=lambda x: -x[1]):
                log(f"    {mdl}: ${cost:.2f}")
            log(f"  Total Claude spend MTD: ${sum(model_cost.values()):.2f}")
        else:
            log(f"  [INFO] cost_report returned no token costs for {month_start} → {data_until}")

    except Exception as e:
        log(f"  [WARN] cost_report fetch failed: {e}")
        model_cost = {}

    # ── 4b. Token usage per model ────────────────────────────────────────────
    try:
        usage_params = {
            "starting_at":  month_start.strftime("%Y-%m-%dT00:00:00Z"),
            "ending_at":    (data_until + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z"),
            "bucket_width": "1d",
            "group_by":     ["model"],
        }
        usage_resp = get_admin_json("usage_report/messages", usage_params)

        for bucket in usage_resp.get("data", []):
            for item in bucket.get("results", []):
                mdl = item.get("model")
                if not mdl:
                    continue
                if mdl not in model_usage:
                    model_usage[mdl] = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0}
                model_usage[mdl]["input_tokens"]     += item.get("uncached_input_tokens", 0)
                model_usage[mdl]["output_tokens"]     += item.get("output_tokens", 0)
                model_usage[mdl]["cache_read_tokens"] += item.get("cache_read_input_tokens", 0)

        if model_usage:
            log(f"  Token usage by model:")
            for mdl, u in sorted(model_usage.items()):
                log(f"    {mdl}: in={u['input_tokens']:,}  out={u['output_tokens']:,}  cached={u['cache_read_tokens']:,}")

    except Exception as e:
        log(f"  [WARN] usage_report/messages fetch failed: {e}")
        model_usage = {}

# ── 4b. Extract spend by product ──────────────────────────────────────────────
# Calculate total Claude spend from model costs
claude_spend_mtd = sum(model_cost.values()) if model_cost else 0
claude_spend = {
    "mtd": round(claude_spend_mtd, 2),
    "monthly": round(claude_spend_mtd, 2),  # MTD is our best estimate for monthly
    "byModel": {model: round(cost, 2) for model, cost in model_cost.items()}
}

# Read Cursor and Windsurf spend from daily_collected.json (populated by collect_data.py)
from pathlib import Path as PathlibPath
daily_collected_path = REPO_ROOT / "data" / "daily_collected.json"
cursor_spend = None
windsurf_spend = None

if daily_collected_path.exists():
    try:
        with open(daily_collected_path, 'r', encoding='utf-8') as f:
            daily_data = json.load(f)
            cursor_spend = daily_data.get("cursor_spend")
            windsurf_spend = daily_data.get("windsurf_spend")
    except Exception as e:
        log(f"  [WARN] Could not read daily_collected.json: {e}")

log(f"  Claude spend MTD: ${claude_spend_mtd:.2f}")
if cursor_spend:
    log(f"  Cursor spend MTD: ${cursor_spend.get('mtd', 0):.2f}")
if windsurf_spend:
    log(f"  Windsurf: {windsurf_spend.get('creditsUsed', 0)} credits used")

# ── 4c. Write output ──────────────────────────────────────────────────────────
# Sort members by lines (desc), excluding service/shared accounts
members_sorted = dict(sorted(
    {k: v for k, v in user_lines_final.items() if k not in BOT_EMAILS}.items(),
    key=lambda x: -x[1]
))

# Build per-day arrays for trend charts (weekdays only)
chats_daily_data  = []
cowork_daily_data = []
for date_str, chats, cowork in zip(all_dates, daily_chat_convos, daily_cowork_sess):
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    if dt.weekday() < 5:   # Mon–Fri only
        chats_daily_data.append({"date": date_str, "chats": chats})
        cowork_daily_data.append({"date": date_str, "users": cowork})

result = {
    "asOf":                 data_until.strftime("%Y-%m-%d"),
    "_dataDelay":           "3-day API delay — most recent available date",
    "totalLines":              total_lines_out,
    "_linesSource":            lines_source,
    "_totalLinesInvocations":  total_accepted,
    "_totalToolLines":         total_tool_lines,
    "_totalLocAdded":          total_loc_added,
    "acceptRate":           accept_rate,
    "activeMembers":        active_members,
    "totalMembers":         total_members,
    "assignedSeats":        assigned_seats,
    "wau":                  wau,
    "mau":                  mau,
    "utilization":          round(weekly_adoption, 1),
    "pendingInvites":       pending_invites,
    "coworkSessionsPerDay": int(avg_cowork_per_day),
    "coworkUserPct":        cowork_user_pct,
    "chatsPerDay":          int(avg_chat_per_day),
    "chatUserPct":          chat_user_pct,
    "projectsCreated":      projects_created_mtd,
    "projectUserPct":       project_user_pct,
    "artifactsCreated":     artifacts_created_mtd,
    "artifactUserPct":      artifact_user_pct,
    "chatsDailyData":       chats_daily_data,
    "coworkDailyData":      cowork_daily_data,
    "chatUsers":            dict(sorted(user_chats.items(), key=lambda x: -x[1])),
    "coworkUsers":          dict(sorted(user_cowork.items(), key=lambda x: -x[1])),
    "members":              members_sorted,
    "modelUsage":           model_usage,
    "modelCost":            model_cost,
    "claudeSpend":          claude_spend,
    "cursorSpend":          cursor_spend,
    "windsurfSpend":        windsurf_spend,
}

OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
OUTPUT_PATH.write_text(json.dumps(result, indent=2), encoding="utf-8")
log(f"\nWrote {OUTPUT_PATH}")
