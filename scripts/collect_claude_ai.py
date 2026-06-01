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
    """Fetch from Analytics API. Handles list params as key[]=val."""
    url = f"{BASE_URL}/{path}"
    if params:
        parts = []
        for k, v in params.items():
            if isinstance(v, list):
                for item in v:
                    parts.append(f"{urllib.request.quote(str(k))}[]={urllib.request.quote(str(item))}")
            else:
                parts.append(f"{urllib.request.quote(str(k))}={urllib.request.quote(str(v))}")
        url = f"{url}?{'&'.join(parts)}"
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

# ── Date range ──────────────────────────────────────────────────────────
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

