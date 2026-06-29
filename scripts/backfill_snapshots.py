#!/usr/bin/env python3
"""
backfill_snapshots.py — One-time-ish backfill of the Monthly Trends snapshots
(MONTHLY_SNAPSHOTS) for past months, so the trend-line charts show history
instead of a single current-month point.

For each past month (from Feb 2026 up to last month) that isn't already recorded
in data/monthly_snapshots.json, this replays the same Anthropic Analytics
`/users` per-day loop as collect_claude_ai.py and computes the same headline
metrics (Claude Code lines, active members, chats/day, chat adoption %, Cowork
daily sessions, projects, artifact adoption %). Cursor events/DAU come from the
local saras-daily-adoption.json. Months the API no longer retains return empty
and are skipped.

It only fills months that are MISSING, so after a successful run subsequent runs
are cheap (the current month keeps being recorded live by update_dashboard.py).

Requires ANTHROPIC_ANALYTICS_KEY. Writes/merges data/monthly_snapshots.json.
"""
import json
import os
import sys
import time
import calendar
import urllib.request
import urllib.error
from collections import defaultdict
from datetime import datetime, timezone, date, timedelta
from pathlib import Path

REPO_ROOT     = Path(__file__).resolve().parent.parent
SNAP_PATH     = REPO_ROOT / "data" / "monthly_snapshots.json"
ADOPTION_PATH = REPO_ROOT / "data" / "saras-daily-adoption.json"

API_KEY  = os.environ.get("ANTHROPIC_ANALYTICS_KEY", "").strip()
BASE_URL = "https://api.anthropic.com/v1/organizations/analytics"
HEADERS  = {"x-api-key": API_KEY, "anthropic-version": "2023-06-01"}

START_YEAR, START_MONTH = 2026, 2
BOT_EMAILS = {"consulting@sarasanalytics.com", "consulting.claude@sarasanalytics.com"}


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def get_json(path, params=None, timeout=60):
    url = f"{BASE_URL}/{path}"
    if params:
        parts = []
        for k, v in params.items():
            parts.append(f"{urllib.request.quote(str(k))}={urllib.request.quote(str(v))}")
        url = f"{url}?{'&'.join(parts)}"
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _ec_usd(ec):  # unused here but kept parallel to live collector
    return 0.0


def month_metrics(year, month, assigned_default=191):
    """Replay /users for every day of the month; return the snapshot dict or
    None if the API returned no usable data (month outside retention)."""
    first = date(year, month, 1)
    last  = date(year, 12, 31) if month == 12 else (date(year, month + 1, 1) - timedelta(days=1))

    # assigned seats for the month (last summary in range), for adoption %.
    assigned_seats = assigned_default
    cowork_mau = 0
    try:
        sd = get_json("summaries", {
            "starting_date": first.strftime("%Y-%m-%d"),
            "ending_date":   (last + timedelta(days=1)).strftime("%Y-%m-%d"),
        })
        summ = sd.get("summaries", [])
        if summ:
            assigned_seats = summ[-1].get("assigned_seat_count", assigned_default) or assigned_default
            cowork_mau     = summ[-1].get("cowork_monthly_active_user_count", 0) or 0
    except Exception as e:
        log(f"    [WARN] summaries {year}-{month:02d}: {e}")

    user_accepted   = defaultdict(int)
    user_tool_lines = defaultdict(int)
    user_loc_added  = defaultdict(int)
    user_cc_active  = set()
    chat_users      = set()
    project_users   = set()
    artifact_users  = set()
    projects_total  = 0
    daily_chats     = []
    daily_cowork    = []
    any_rows        = False

    d = first
    while d <= last:
        date_str = d.strftime("%Y-%m-%d")
        day_chats = 0
        day_cowork = 0
        page = None
        while True:
            params = {"date": date_str, "limit": 1000}
            if page:
                params["page"] = page
            try:
                data = get_json("users", params)
            except urllib.error.HTTPError as e:
                log(f"    [{date_str}] HTTP {e.code}")
                break
            except Exception as e:
                log(f"    [{date_str}] {e}")
                break
            rows = data.get("data", [])
            if rows:
                any_rows = True
            for user in rows:
                email = user["user"]["email_address"].lower().strip()
                ccm = user.get("claude_code_metrics", {})
                ta = ccm.get("tool_actions", {})
                for tool in ("edit_tool", "multi_edit_tool", "write_tool", "notebook_edit_tool"):
                    t = ta.get(tool, {})
                    user_accepted[email]   += t.get("accepted_count", 0)
                    user_tool_lines[email] += t.get("accepted_line_count", 0)
                    user_tool_lines[email] += t.get("lines_accepted", 0)
                user_loc_added[email] += ccm.get("core_metrics", {}).get("lines_of_code", {}).get("added_count", 0)
                if ccm.get("core_metrics", {}).get("distinct_session_count", 0) > 0:
                    user_cc_active.add(email)
                cm = user.get("chat_metrics", {})
                convos = cm.get("distinct_conversation_count", 0)
                day_chats += convos
                if convos > 0:
                    chat_users.add(email)
                pc = cm.get("distinct_projects_created_count", 0)
                if pc > 0:
                    projects_total += pc
                    project_users.add(email)
                if cm.get("distinct_artifacts_created_count", 0) > 0:
                    artifact_users.add(email)
                cw = user.get("cowork_metrics", {}).get("distinct_session_count", 0)
                day_cowork += cw
            if not data.get("has_more"):
                break
            page = data.get("next_page")
            time.sleep(0.15)
        daily_chats.append(day_chats)
        daily_cowork.append(day_cowork)
        d += timedelta(days=1)

    if not any_rows:
        return None

    total_tool_lines = sum(user_tool_lines.values())
    total_loc_added  = sum(user_loc_added.values())
    if total_tool_lines > 0:
        user_lines_final, total_lines = user_tool_lines, total_tool_lines
    elif total_loc_added > 0:
        user_lines_final, total_lines = user_loc_added, total_loc_added
    else:
        user_lines_final, total_lines = user_accepted, sum(user_accepted.values())
    total_lines -= sum(user_lines_final.get(e, 0) for e in BOT_EMAILS)

    active = ({e for e, v in user_lines_final.items() if v > 0} | user_cc_active) - BOT_EMAILS
    chats_per_day  = round(sum(daily_chats) / len(daily_chats)) if daily_chats else 0
    cowork_per_day = round(sum(daily_cowork) / len(daily_cowork)) if daily_cowork else 0
    chat_pct       = round(100 * len(chat_users) / assigned_seats) if assigned_seats else 0
    artifact_pct   = round(100 * len(artifact_users) / assigned_seats) if assigned_seats else 0

    # Nothing meaningful → treat as no data.
    if total_lines == 0 and not active and not chat_users:
        return None

    return {
        "lines":           int(total_lines),
        "activeMembers":   len(active),
        "chatsPerDay":     chats_per_day,
        "chatUserPct":     chat_pct,
        "coworkDAU":       cowork_per_day,
        "projectsCreated": projects_total,
        "artifactUserPct": artifact_pct,
    }


def cursor_for_month(adoption, year, month):
    """Cursor events (sum) and DAU (avg distinct users/active day) from local data."""
    mk = f"{year}-{month:02d}"
    events = 0
    user_days = 0
    active_days = 0
    for day in adoption.get("days", []):
        if (day.get("date") or "")[:7] != mk:
            continue
        cur = day.get("cursor") or {}
        if cur:
            events += sum(cur.values())
            user_days += len(cur)
            active_days += 1
    dau = round(user_days / active_days) if active_days else 0
    return events, dau


def main():
    if not API_KEY:
        log("No ANTHROPIC_ANALYTICS_KEY — skipping snapshot backfill.")
        return

    snaps = []
    if SNAP_PATH.exists():
        try:
            snaps = json.loads(SNAP_PATH.read_text(encoding="utf-8"))
        except Exception:
            snaps = []
    have = {s.get("monthKey") for s in snaps}

    adoption = {}
    if ADOPTION_PATH.exists():
        try:
            adoption = json.loads(ADOPTION_PATH.read_text(encoding="utf-8"))
        except Exception:
            adoption = {}

    today = datetime.now(timezone.utc).date()
    cur_key = today.strftime("%Y-%m")

    added = 0
    y, m = START_YEAR, START_MONTH
    while (y, m) < (today.year, today.month):     # past months only; current is live
        mk = f"{y}-{m:02d}"
        if mk in have:
            log(f"{mk}: already recorded — skip")
        else:
            log(f"{mk}: backfilling…")
            met = month_metrics(y, m)
            if met:
                events, dau = cursor_for_month(adoption, y, m)
                met.update({
                    "monthKey":    mk,
                    "month":       date(y, m, 1).strftime("%B %Y"),
                    "monthShort":  date(y, m, 1).strftime("%b %Y"),
                    "cursorDAU":   dau,
                    "cursorEvents": events,
                })
                snaps.append(met)
                added += 1
                log(f"  ✓ {mk}: lines={met['lines']:,} active={met['activeMembers']} "
                    f"chats/day={met['chatsPerDay']} cowork={met['coworkDAU']}")
            else:
                log(f"  ✗ {mk}: no data retained — skipped")
        m += 1
        if m > 12:
            m, y = 1, y + 1

    if added:
        snaps.sort(key=lambda s: s.get("monthKey", ""))
        SNAP_PATH.write_text(json.dumps(snaps, indent=2) + "\n", encoding="utf-8")
        log(f"\nWrote {SNAP_PATH} — added {added} month(s); total {len(snaps)}")
    else:
        log("\nNo new months backfilled.")


if __name__ == "__main__":
    main()
