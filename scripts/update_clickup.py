#!/usr/bin/env python3
"""
update_clickup.py  —  Auto-updates CLICKUP_DATA in index.html
Fetches fresh task counts from ClickUp API and patches the HTML in-place.

Usage:
  CLICKUP_API_KEY=<key> python scripts/update_clickup.py

Environment variables:
  CLICKUP_API_KEY   (required)  Your ClickUp personal API token
  CLICKUP_TEAM_ID   (optional)  Workspace/team ID  [default: 9011000533]
  HTML_FILE         (optional)  Path to index.html  [default: index.html]
"""

import os, re, sys, requests
from datetime import datetime, timezone
from collections import defaultdict

# ── Config ────────────────────────────────────────────────────────────────────
API_KEY   = os.environ.get('CLICKUP_API_KEY', '')
TEAM_ID   = os.environ.get('CLICKUP_TEAM_ID', '9011000533')
HTML_FILE = os.environ.get('HTML_FILE', 'index.html')

CLOSED_STATUSES = {'closed', 'complete', 'done', 'completed', 'cancelled'}
PRIO_MAP        = {'urgent':'urgent','high':'high','normal':'normal','low':'low',
                   '1':'urgent','2':'high','3':'normal','4':'low'}

# ── ClickUp fetch ─────────────────────────────────────────────────────────────
def fetch_tasks():
    if not API_KEY:
        sys.exit("ERROR: CLICKUP_API_KEY environment variable not set.")

    headers     = {'Authorization': API_KEY}
    url         = f'https://api.clickup.com/api/v2/team/{TEAM_ID}/task'
    now         = datetime.now(timezone.utc)
    month_start = int(datetime(now.year, now.month, 1, tzinfo=timezone.utc).timestamp() * 1000)

    all_tasks = []
    for page in range(10):
        try:
            r = requests.get(url, headers=headers, timeout=30,
                             params={'page': page, 'include_closed': 'true', 'subtasks': 'false'})
            r.raise_for_status()
            batch = r.json().get('tasks', [])
        except Exception as e:
            print(f"  [WARN] Page {page} failed: {e}")
            break
        if not batch:
            break
        all_tasks.extend(batch)
        print(f"  Page {page}: {len(batch)} tasks  (running total: {len(all_tasks)})")

    return all_tasks, month_start


# ── Aggregation ───────────────────────────────────────────────────────────────
def aggregate(tasks, month_start_ms):
    data = defaultdict(lambda: dict(
        open=0, done=0, urgent=0, high=0, normal=0, low=0,
        next_due=None, name=''
    ))

    for task in tasks:
        status    = ((task.get('status') or {}).get('status') or '').lower().strip()
        is_closed = status in CLOSED_STATUSES

        if is_closed:
            dc = task.get('date_closed')
            if not dc or int(dc) < month_start_ms:
                continue  # Only count tasks closed this calendar month

        prio_raw = str(((task.get('priority') or {}).get('priority') or '')).lower()
        prio_key = PRIO_MAP.get(prio_raw, '')
        due_ms   = task.get('due_date')

        for assignee in (task.get('assignees') or []):
            email = ((assignee.get('email') or '')).lower().strip()
            if not email:
                continue
            d = data[email]
            if not d['name']:
                d['name'] = assignee.get('username') or assignee.get('name') or ''

            if is_closed:
                d['done'] += 1
            else:
                d['open'] += 1
                if prio_key:
                    d[prio_key] += 1
                if due_ms:
                    ms = int(due_ms)
                    if d['next_due'] is None or ms < d['next_due']:
                        d['next_due'] = ms

    return dict(data)


# ── HTML patching ─────────────────────────────────────────────────────────────
def ms_to_date(ms):
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime('%Y-%m-%d')
    except Exception:
        return None


def patch_html(html: str, api_data: dict, today: str) -> str:
    """Patch CLICKUP_DATA inside the HTML string in-place."""

    # 1. Update asOf date (first occurrence inside CLICKUP_DATA block)
    html = re.sub(
        r"(const CLICKUP_DATA\s*=\s*\{[^}]*?asOf:\s*')[^']+(')",
        lambda m: m.group(1) + today + m.group(2),
        html, count=1, flags=re.DOTALL
    )

    # 2. For each person entry, patch numeric fields if we have API data
    def replace_entry(m):
        entry = m.group(0)
        em = re.search(r"email:'([^']+)'", entry)
        if not em:
            return entry
        email = em.group(1).lower()
        if email not in api_data:
            return entry

        d = api_data[email]
        nd_str = f"'{ms_to_date(d['next_due'])}'" if d['next_due'] else 'null'

        entry = re.sub(r'\bopen:\d+',    f"open:{d['open']}",     entry)
        entry = re.sub(r'\bdone:\d+',    f"done:{d['done']}",     entry)
        entry = re.sub(r'\burgent:\d+',  f"urgent:{d['urgent']}", entry)
        entry = re.sub(r'\bhigh:\d+',    f"high:{d['high']}",     entry)
        entry = re.sub(r'\bnormal:\d+',  f"normal:{d['normal']}", entry)
        entry = re.sub(r'\blow:\d+',     f"low:{d['low']}",       entry)
        entry = re.sub(r"nextDue:(?:null|'[^']*')", f"nextDue:{nd_str}", entry)
        return entry

    # Match each {name:'...', email:'...', ... nextDue:...} person block
    html = re.sub(r'\{name:[^\}]+nextDue:[^\}]+\}', replace_entry, html)
    return html


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    print(f"\n=== ClickUp Dashboard Update  [{today}] ===")

    print("Fetching tasks from ClickUp...")
    tasks, month_start_ms = fetch_tasks()
    print(f"Total tasks: {len(tasks)}")

    print("Aggregating per person...")
    api_data = aggregate(tasks, month_start_ms)
    active   = {e: d for e, d in api_data.items() if d['open'] + d['done'] > 0}
    print(f"People with tasks this month: {len(active)}")

    print(f"Reading {HTML_FILE}...")
    with open(HTML_FILE, 'r', encoding='utf-8') as f:
        html = f.read()

    print("Patching HTML...")
    updated = patch_html(html, active, today)

    with open(HTML_FILE, 'w', encoding='utf-8') as f:
        f.write(updated)

    print(f"Done. {HTML_FILE} updated with {today} ClickUp data.\n")


if __name__ == '__main__':
    main()
