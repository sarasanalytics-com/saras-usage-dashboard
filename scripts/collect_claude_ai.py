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

API_KEY  = os.environ["ANTHROPIC_ANALYTICS_KEY"]
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
user_accepted   = defaultdict(int)   # email → total lines accepted
user_rejected   = defaultdict(int)   # email → total lines rejected
user_cc_active  = set()              # emails with any CC session MTD

daily_chat_convos   = []             # total conversations per day
daily_cowork_sess   = []             # total cowork sessions per day
chat_users_mtd      = set()          # users with ≥1 chat MTD
project_created_mtd = defaultdict(int)  # email → projects created
artifact_created_mtd = defaultdict(int) # email → artifacts created
project_users_mtd   = set()
artifact_users_mtd  = set()

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

            # Claude Code tool actions
            ta = user.get("claude_code_metrics", {}).get("tool_actions", {})
            for tool in ("edit_tool", "multi_edit_tool", "write_tool", "notebook_edit_tool"):
                t = ta.get(tool, {})
                user_accepted[email] += t.get("accepted_count", 0)
                user_rejected[email] += t.get("rejected_count", 0)

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

            proj_created = cm.get("distinct_projects_created_count", 0)
            if proj_created > 0:
                project_created_mtd[email] += proj_created
                project_users_mtd.add(email)

            art_created = cm.get("distinct_artifacts_created_count", 0)
            if art_created > 0:
                artifact_created_mtd[email] += art_created
                artifact_users_mtd.add(email)

            # Cowork sessions
            day_cowork += (user.get("cowork_metrics", {})
                           .get("distinct_session_count", 0))

        if not data.get("has_more"):
            break
        page = data.get("next_page")
        time.sleep(0.3)  # rate-limit buffer

    daily_chat_convos.append(day_convos)
    daily_cowork_sess.append(day_cowork)
    log(f"  {date_str}: convos={day_convos} cowork_sess={day_cowork} (pages={pages_this_day})")
    time.sleep(0.2)


# ── 3. Derived metrics ────────────────────────────────────────────────────────
total_accepted     = sum(user_accepted.values())
total_rejected     = sum(user_rejected.values())
total_actions      = total_accepted + total_rejected
accept_rate        = round(100 * total_accepted / total_actions, 1) if total_actions else 0

# Active members = users with accepted lines > 0 OR CC sessions > 0
active_emails      = {e for e, v in user_accepted.items() if v > 0} | user_cc_active
active_members     = len(active_emails)
total_members      = len(user_accepted)   # everyone who appears in API at all

avg_chat_per_day   = round(sum(daily_chat_convos) / len(daily_chat_convos), 0) if daily_chat_convos else 0
avg_cowork_per_day = round(sum(daily_cowork_sess)  / len(daily_cowork_sess),  0) if daily_cowork_sess else 0

chat_user_pct      = round(100 * len(chat_users_mtd)    / assigned_seats) if assigned_seats else 0
cowork_user_pct    = round(100 * cowork_mau              / assigned_seats) if assigned_seats else 0
project_user_pct   = round(100 * len(project_users_mtd) / assigned_seats) if assigned_seats else 0
artifact_user_pct  = round(100 * len(artifact_users_mtd) / assigned_seats) if assigned_seats else 0

projects_created_mtd  = sum(project_created_mtd.values())
artifacts_created_mtd = sum(artifact_created_mtd.values())

# Sort members by lines accepted (desc)
members_sorted = dict(sorted(user_accepted.items(), key=lambda x: -x[1]))

log(f"\n=== Summary ===")
log(f"  Lines accepted MTD: {total_accepted:,}  |  Accept rate: {accept_rate}%")
log(f"  Active CC members: {active_members} / {total_members}")
log(f"  WAU: {wau}  |  Cowork MAU: {cowork_mau}")
log(f"  Chat users: {len(chat_users_mtd)}  |  Avg chats/day: {avg_chat_per_day}")


# ── 4. Write output ───────────────────────────────────────────────────────────
result = {
    "asOf":                 data_until.strftime("%Y-%m-%d"),
    "_dataDelay":           "3-day API delay — most recent available date",
    "totalLines":           total_accepted,
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
    "members":              members_sorted,
}

OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
OUTPUT_PATH.write_text(json.dumps(result, indent=2), encoding="utf-8")
log(f"\nWrote {OUTPUT_PATH}")
