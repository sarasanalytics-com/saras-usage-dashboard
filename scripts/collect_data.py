#!/usr/bin/env python3
"""
collect_data.py — Daily data collector for GitHub Actions.

Pulls:
  - ClickUp: open tasks, MTD closed, daily active
  - Cursor:  today's usage events per user
  - Windsurf: last-active per user

Writes result to data/daily_collected.json (relative to repo root).

Required environment variables:
  CLICKUP_API_KEY        ClickUp personal token  (pk_...)
  CURSOR_BEARER_TOKEN    Cursor API bearer token  (crsr_...)
  WINDSURF_SERVICE_KEY   Windsurf service key

Optional:
  CLICKUP_TEAM_ID        defaults to 9011000533
  REPORT_DATE            YYYY-MM-DD, defaults to today UTC
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
CLICKUP_TOKEN        = os.environ["CLICKUP_API_KEY"]
CURSOR_BEARER_TOKEN  = os.environ["CURSOR_BEARER_TOKEN"]
WINDSURF_SERVICE_KEY = os.environ["WINDSURF_SERVICE_KEY"]
WORKSPACE_ID         = os.environ.get("CLICKUP_TEAM_ID", "9011000533")

TODAY_STR = os.environ.get("REPORT_DATE") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
TODAY     = datetime.strptime(TODAY_STR, "%Y-%m-%d").replace(tzinfo=timezone.utc)

MONTH_START = TODAY.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
START_MS    = int(TODAY.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
END_MS      = int(TODAY.replace(hour=23, minute=59, second=59, microsecond=999000).timestamp() * 1000)

REPO_ROOT   = Path(__file__).resolve().parent.parent
OUTPUT_PATH = REPO_ROOT / "data" / "daily_collected.json"


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def get_json(url, headers=None, timeout=60):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def post_json(url, body, headers=None, timeout=60):
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST", headers=headers or {})
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


# ── ClickUp: member ID → email map ───────────────────────────────────────────
log("Fetching ClickUp members...")
cu_headers = {"Authorization": CLICKUP_TOKEN}
user_id_to_email = {}
user_id_to_name  = {}

try:
    teams_resp = get_json("https://api.clickup.com/api/v2/team", cu_headers)
    teams = teams_resp.get("teams", [])
    team_ids = [t.get("id") for t in teams]
    log(f"  Team IDs found: {team_ids}")
    # Auto-select workspace ID if not matching config
    if teams and WORKSPACE_ID not in [str(tid) for tid in team_ids]:
        WORKSPACE_ID = str(teams[0].get("id", WORKSPACE_ID))
        log(f"  [INFO] Using team ID: {WORKSPACE_ID}")
    for team in teams:
        for m in team.get("members", []):
            u    = m.get("user", {})
            uid  = u.get("id")
            em   = (u.get("email") or "").lower().strip()
            name = u.get("username") or u.get("email", "")
            if uid and em:
                user_id_to_email[uid] = em
                user_id_to_name[uid]  = name
except Exception as e:
    log(f"  [WARN] Members fetch failed: {e}")

log(f"  Members: {len(user_id_to_email)}")


# ── ClickUp: paginated task fetch ─────────────────────────────────────────────
def fetch_clickup_tasks(extra_params, max_pages=100):
    tasks = []
    for page in range(max_pages):
        params = {**extra_params, "page": str(page)}
        qs  = "&".join(f"{k}={urllib.request.quote(str(v))}" for k, v in params.items())
        url = f"https://api.clickup.com/api/v2/team/{WORKSPACE_ID}/task?{qs}"
        try:
            d = get_json(url, cu_headers, timeout=90)
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode()[:200]
            except Exception:
                pass
            log(f"  ClickUp page {page} HTTP {e.code}: {body}")
            break
        except Exception as e:
            log(f"  ClickUp page {page} error: {e}")
            break
        batch = d.get("tasks", [])
        if not batch:
            break
        tasks.extend(batch)
        log(f"    page {page}: {len(batch)} tasks (total so far: {len(tasks)})")
        if len(batch) < 100:
            break
        time.sleep(0.3)
    return tasks


def task_priority(t):
    p = t.get("priority")
    if isinstance(p, dict):
        return (p.get("priority") or "normal").lower()
    if isinstance(p, str):
        return p.lower()
    return "normal"


def emails_for_task(t):
    out = set()
    for a in (t.get("assignees") or []):
        uid = a.get("id")
        if uid in user_id_to_email:
            out.add(user_id_to_email[uid])
        else:
            em = (a.get("email") or "").lower().strip()
            if em:
                out.add(em)
    return out


log("Fetching ClickUp open tasks...")
open_tasks = fetch_clickup_tasks({"include_closed": "false"})
log(f"  Open: {len(open_tasks)}")

log("Fetching ClickUp MTD closed tasks...")
month_start_ms = int(MONTH_START.timestamp() * 1000)
mtd_closed = []
seen_ids   = set()
for st in ("complete", "closed", "done"):
    batch = fetch_clickup_tasks({
        "include_closed": "true",
        "date_done_gt": str(month_start_ms - 1),
        "date_done_lt": str(END_MS + 1),
        "statuses[]": st,
    })
    for t in batch:
        if t["id"] not in seen_ids:
            seen_ids.add(t["id"])
            mtd_closed.append(t)
log(f"  MTD closed: {len(mtd_closed)}")

log("Fetching ClickUp tasks updated today...")
today_updated = fetch_clickup_tasks({
    "include_closed": "true",
    "date_updated_gt": str(START_MS - 1),
    "date_updated_lt": str(END_MS + 1),
})
log(f"  Updated today: {len(today_updated)}")


# ── ClickUp: aggregate per person ─────────────────────────────────────────────
PRIO_ORDER = {"urgent": 1, "high": 2, "normal": 3, "low": 4}

people_open        = defaultdict(lambda: {
    "open": 0, "urgent": 0, "high": 0, "normal": 0, "low": 0,
    "nextDue": None, "tasks": [], "name": None
})
people_done        = defaultdict(int)
clickup_daily_active = set()
list_freq          = defaultdict(int)

for t in mtd_closed:
    for em in emails_for_task(t):
        people_done[em] += 1
    ln = (t.get("list") or {}).get("name", "")
    if ln:
        list_freq[ln] += 1

for t in today_updated:
    for em in emails_for_task(t):
        clickup_daily_active.add(em)

for t in open_tasks:
    emails = emails_for_task(t)
    prio   = task_priority(t)
    due_ms = t.get("due_date")
    due_str = None
    if due_ms:
        try:
            due_str = datetime.fromtimestamp(int(due_ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        except Exception:
            pass
    title = (t.get("name") or "").strip()[:80]
    url   = t.get("url") or ""
    task_obj = {"title": title, "priority": prio, "due": due_str, "url": url}
    for em in emails:
        rec = people_open[em]
        rec["open"] += 1
        if prio in rec:
            rec[prio] += 1
        rec["tasks"].append(task_obj)
        if due_str and (not rec["nextDue"] or due_str < rec["nextDue"]):
            rec["nextDue"] = due_str
        for a in (t.get("assignees") or []):
            uid = a.get("id")
            if user_id_to_email.get(uid) == em and not rec["name"]:
                rec["name"] = user_id_to_name.get(uid) or a.get("username") or em

for rec in people_open.values():
    rec["tasks"].sort(key=lambda x: (
        PRIO_ORDER.get(x["priority"], 3),
        x["due"] is None,
        x["due"] or "9999-12-31",
    ))

all_emails  = set(people_open.keys()) | set(people_done.keys())
people_data = {}
for em in all_emails:
    rec = dict(people_open.get(em) or {
        "open": 0, "urgent": 0, "high": 0, "normal": 0, "low": 0,
        "nextDue": None, "tasks": [], "name": None
    })
    rec["done"] = people_done.get(em, 0)
    if not rec.get("name"):
        for uid, em2 in user_id_to_email.items():
            if em2 == em:
                rec["name"] = user_id_to_name.get(uid) or em
                break
        if not rec.get("name"):
            rec["name"] = em.split("@")[0].replace(".", " ").title()
    people_data[em] = rec

done_counts       = sorted(people_done.items(), key=lambda x: -x[1])
list_freq_sorted  = sorted(list_freq.items(), key=lambda x: -x[1])


# ── Cursor API: today's events ────────────────────────────────────────────────
log("Fetching Cursor events...")
cursor_headers = {
    "Authorization": f"Bearer {CURSOR_BEARER_TOKEN}",
    "Content-Type": "application/json",
}
cursor_user_counts = defaultdict(int)
page = 1
while page < 50:
    try:
        d = post_json(
            "https://api.cursor.com/teams/filtered-usage-events",
            {"startDate": START_MS, "endDate": END_MS, "page": page},
            cursor_headers,
        )
    except urllib.error.HTTPError as e:
        log(f"  Cursor page {page} error: {e}")
        break
    events = d.get("usageEvents") or d.get("events") or []
    for ev in events:
        em = (ev.get("userEmail") or "").lower().strip()
        if em:
            cursor_user_counts[em] += 1
    if not d.get("hasNextPage"):
        break
    page += 1
    time.sleep(0.5)

log(f"  Cursor: {sum(cursor_user_counts.values())} events from {len(cursor_user_counts)} users")


# ── Cursor spend API ──────────────────────────────────────────────────────────
log("Fetching Cursor team spend...")
cursor_spend_mtd = 0
cursor_spend_data = None
try:
    spend_resp = post_json(
        "https://api.cursor.com/teams/spend",
        {"page": 1, "pageSize": 1000},
        cursor_headers,
    )
    # Sum up spend from all team members (spendCents converted to dollars)
    for member in spend_resp.get("data", []):
        spend_cents = member.get("spendCents", 0)
        cursor_spend_mtd += spend_cents
    cursor_spend_mtd = round(cursor_spend_mtd / 100, 2)  # Convert cents to dollars
    cursor_spend_data = {
        "mtd": cursor_spend_mtd,
        "monthly": cursor_spend_mtd,
    }
    log(f"  Cursor MTD spend: ${cursor_spend_mtd}")
except Exception as e:
    log(f"  [WARN] Cursor spend fetch failed: {e}")
    cursor_spend_data = None


# ── Windsurf API ──────────────────────────────────────────────────────────────
log("Fetching Windsurf...")
windsurf_users = {}
windsurf_today = {}
try:
    ws = post_json(
        "https://server.codeium.com/api/v1/UserPageAnalytics",
        {"service_key": WINDSURF_SERVICE_KEY},
        timeout=30,
    )
    for u in (ws.get("userTableStats") or []):
        em = (u.get("email") or "").lower().strip()
        if not em or "sarasanalytics" not in em:
            continue
        latest = None
        for fld in ("lastAutocompleteUsageTime", "lastChatUsageTime", "lastCommandUsageTime"):
            v = u.get(fld)
            if not v:
                continue
            try:
                if isinstance(v, str):
                    dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
                else:
                    dt = datetime.fromtimestamp(float(v), tz=timezone.utc)
                if not latest or dt > latest:
                    latest = dt
            except Exception:
                pass
        if latest:
            windsurf_users[em] = latest.strftime("%Y-%m-%d")
            if windsurf_users[em] == TODAY_STR:
                windsurf_today[em] = 1
        else:
            windsurf_users[em] = None
except Exception as e:
    log(f"  [WARN] Windsurf error: {e}")

log(f"  Windsurf: {len(windsurf_users)} users, {len(windsurf_today)} active today")


# ── Write output ──────────────────────────────────────────────────────────────
result = {
    "today": TODAY_STR,
    "clickup": {
        "open_count": len(open_tasks),
        "done_count": len(mtd_closed),
        "daily_active": {em: 1 for em in clickup_daily_active},
        "done_top": done_counts[:10],
        "list_freq": list_freq_sorted[:10],
        "people": people_data,
    },
    "cursor_today": dict(cursor_user_counts),
    "cursor_spend": cursor_spend_data,
    "windsurf_today": windsurf_today,
    "windsurf_users": windsurf_users,
}

OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
OUTPUT_PATH.write_text(json.dumps(result, indent=2), encoding="utf-8")
log(f"\nWrote {OUTPUT_PATH}")
log(f"  ClickUp open: {result['clickup']['open_count']}, closed MTD: {result['clickup']['done_count']}")
log(f"  Cursor users today: {len(result['cursor_today'])}")
log(f"  Windsurf users: {len(result['windsurf_users'])}")
