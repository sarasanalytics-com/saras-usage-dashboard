#!/usr/bin/env python3
"""
update_clickup.py  —  Auto-updates CLICKUP_DATA in index.html
Fetches fresh task counts from ClickUp API and patches the HTML in-place.

Strategy:
  1. Fetch all workspace members → build user_id → email map
  2. Fetch all space IDs (including private) from the workspace
  3. Fetch tasks from every space with explicit space_ids[] params
  4. Aggregate per email, patch HTML

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

HEADERS = {}  # filled after API_KEY check


# ── Workspace members (user_id → email backup map) ───────────────────────────
def fetch_members():
    """Returns {user_id: email} for every workspace member."""
    url = f'https://api.clickup.com/api/v2/group'
    # Use the team members endpoint
    r = requests.get(
        f'https://api.clickup.com/api/v2/team/{TEAM_ID}/member',
        headers=HEADERS, timeout=30
    )
    members = {}
    try:
        r.raise_for_status()
        for m in r.json().get('members', []):
            uid   = m.get('id') or m.get('user', {}).get('id')
            email = (m.get('email') or m.get('user', {}).get('email') or '').lower().strip()
            if uid and email:
                members[int(uid)] = email
    except Exception as e:
        print(f"  [WARN] Members fetch failed: {e}")
    return members


# ── All space IDs (including private) ────────────────────────────────────────
def fetch_all_space_ids():
    """Returns list of all space IDs in the workspace."""
    space_ids = []
    page = 0
    while True:
        try:
            r = requests.get(
                f'https://api.clickup.com/api/v2/team/{TEAM_ID}/space',
                headers=HEADERS, timeout=30,
                params={'archived': 'false'}
            )
            r.raise_for_status()
            spaces = r.json().get('spaces', [])
            for s in spaces:
                space_ids.append(str(s['id']))
            break  # space endpoint returns all at once
        except Exception as e:
            print(f"  [WARN] Space fetch failed: {e}")
            break
    print(f"  Found {len(space_ids)} spaces")
    return space_ids


# ── Fetch all tasks ───────────────────────────────────────────────────────────
def fetch_tasks(space_ids):
    """Fetch all tasks from all spaces, paginating until empty."""
    url        = f'https://api.clickup.com/api/v2/team/{TEAM_ID}/task'
    now        = datetime.now(timezone.utc)
    month_start = int(datetime(now.year, now.month, 1, tzinfo=timezone.utc).timestamp() * 1000)

    all_tasks = []
    for page in range(100):  # max 10 000 tasks (100 pages × 100)
        # Build params: pass all space_ids[] to include private spaces
        params = [('page', page), ('include_closed', 'true'), ('subtasks', 'false')]
        for sid in space_ids:
            params.append(('space_ids[]', sid))

        try:
            r = requests.get(url, headers=HEADERS, timeout=60, params=params)
            r.raise_for_status()
            batch = r.json().get('tasks', [])
        except Exception as e:
            print(f"  [WARN] Page {page} failed: {e}")
            break
        if not batch:
            break
        all_tasks.extend(batch)
        print(f"  Page {page}: {len(batch)} tasks  (running total: {len(all_tasks)})")
        if len(batch) < 100:
            break  # last page

    return all_tasks, month_start


# ── Aggregation ───────────────────────────────────────────────────────────────
def aggregate(tasks, month_start_ms, id_to_email):
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
                continue  # only tasks closed this calendar month

        prio_raw = str(((task.get('priority') or {}).get('priority') or '')).lower()
        prio_key = PRIO_MAP.get(prio_raw, '')
        due_ms   = task.get('due_date')

        for assignee in (task.get('assignees') or []):
            # Try email directly from task assignee, fall back to id→email map
            email = (assignee.get('email') or '').lower().strip()
            if not email:
                uid = assignee.get('id')
                if uid:
                    email = id_to_email.get(int(uid), '')
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

    # 1. Update asOf date
    html = re.sub(
        r"(const CLICKUP_DATA\s*=\s*\{[^}]*?asOf:\s*')[^']+(')",
        lambda m: m.group(1) + today + m.group(2),
        html, count=1, flags=re.DOTALL
    )

    # 2. Patch each person entry by email
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

    html = re.sub(r'\{name:[^\}]+nextDue:[^\}]+\}', replace_entry, html)
    return html


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    global HEADERS
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    print(f"\n=== ClickUp Dashboard Update  [{today}] ===")

    if not API_KEY:
        sys.exit("ERROR: CLICKUP_API_KEY environment variable not set.")
    HEADERS = {'Authorization': API_KEY}

    print("Fetching workspace members (user_id → email map)...")
    id_to_email = fetch_members()
    print(f"  {len(id_to_email)} members loaded")

    print("Fetching all space IDs (including private)...")
    space_ids = fetch_all_space_ids()

    print("Fetching all tasks from all spaces...")
    tasks, month_start_ms = fetch_tasks(space_ids)
    print(f"Total tasks fetched: {len(tasks)}")

    print("Aggregating per person...")
    api_data = aggregate(tasks, month_start_ms, id_to_email)
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

    # Summary
    print("=== Top 10 by open tasks ===")
    top = sorted(active.items(), key=lambda x: x[1]['open'], reverse=True)[:10]
    for email, d in top:
        print(f"  {email}: open={d['open']} done={d['done']}")


if __name__ == '__main__':
    main()
