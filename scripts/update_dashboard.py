#!/usr/bin/env python3
"""
update_dashboard.py — Patches index.html and data/saras-daily-adoption.json
with the latest collected data.

Reads:
  data/daily_collected.json   — from collect_data.py (Cursor/Windsurf/ClickUp)
  data/claude_ai_stats.json   — manually maintained Claude.ai stats
  data/saras-daily-adoption.json — running history

Writes:
  index.html                      — dashboard HTML (patched in-place)
  data/saras-daily-adoption.json  — updated history
"""
import json
import re
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT    = Path(__file__).resolve().parent.parent
HTML_PATH    = REPO_ROOT / "index.html"
ADOPTION_PATH = REPO_ROOT / "data" / "saras-daily-adoption.json"
COLLECTED_PATH = REPO_ROOT / "data" / "daily_collected.json"
CLAUDE_STATS_PATH = REPO_ROOT / "data" / "claude_ai_stats.json"
API_AGENTS_PATH   = REPO_ROOT / "data" / "api_agents_stats.json"
MONTHLY_SNAPSHOTS_PATH = REPO_ROOT / "data" / "monthly_snapshots.json"


def log(msg):
    print(msg, flush=True)


# ── Load data ─────────────────────────────────────────────────────────────────
with COLLECTED_PATH.open(encoding="utf-8") as f:
    fresh = json.load(f)

with CLAUDE_STATS_PATH.open(encoding="utf-8") as f:
    cc = json.load(f)

# Load API agents stats (may not exist on first run)
api_agents = {}
if API_AGENTS_PATH.exists():
    try:
        with API_AGENTS_PATH.open(encoding="utf-8") as f:
            api_agents = json.load(f)
    except Exception as e:
        log(f"  [WARN] Could not load api_agents_stats.json: {e}")

with ADOPTION_PATH.open(encoding="utf-8") as f:
    adoption = json.load(f)

with HTML_PATH.open(encoding="utf-8") as f:
    html = f.read()

TODAY     = fresh["today"]
dt = datetime.strptime(TODAY, "%Y-%m-%d")
MONTH_LABEL = dt.strftime("%B %Y")
TODAY_LABEL = dt.strftime("%B %d, %Y").replace(" 0", " ")  # Remove leading zero from day

# ── Build CLICKUP_DATA.people ─────────────────────────────────────────────────
# Extract existing dept / name mappings from HTML so we don't lose them
existing_dept = {}
existing_name = {}

clickup_block_match = re.search(r"const CLICKUP_DATA = \{.*?\n\};", html, flags=re.DOTALL)
if clickup_block_match:
    blk = clickup_block_match.group(0)
    # Match both single and double quoted fields for backwards compatibility
    for m in re.finditer(r'name:["\']([^"\']*)["\'],\s*email:["\']([^"\']+)["\'],\s*dept:["\']([^"\']*)["\']', blk):
        name, email, dept = m.group(1), m.group(2), m.group(3)
        existing_dept[email.lower()] = dept
        existing_name[email.lower()] = name

dept_map = {}
dept_block = re.search(r"const DEPT_MAP = \{(.*?)\};", html, flags=re.DOTALL)
if dept_block:
    for m in re.finditer(r'["\']([^"\']+@sarasanalytics\.com)["\']:\s*["\']([^"\']+)["\']', dept_block.group(1)):
        dept_map[m.group(1).lower()] = m.group(2)

# Extract ALL_EMPLOYEES dept (authoritative HR source)
all_emp_dept = {}
all_emp_block = re.search(r"const ALL_EMPLOYEES = \[(.*?)\];", html, flags=re.DOTALL)
if all_emp_block:
    for m in re.finditer(r'email:["\']([^"\']+)["\'].*?dept:["\']([^"\']+)["\']', all_emp_block.group(1), flags=re.DOTALL):
        all_emp_dept[m.group(1).lower()] = m.group(2)
log(f"  ALL_EMPLOYEES depts: {len(all_emp_dept)} entries")


DEPT_DEFAULTS = {
    "sparsh.gupta": "Engineering",
    "bheem": "Engineering",
    "bhawna.kumari": "Consulting",
    "rakesh.varma": "Marketing",
    "saivarun.pathi": "Data Engineering",
    "saiteja.katrapati": "Data Engineering",
    "rajavardhan": "Data Engineering",
    "venkateshwaran.s": "Data Engineering",
    "harsh.sharma": "Data Engineering",
    "moiz.essaji": "Finance",
    "katharin.benson": "HR",
    "anusha": "Data Engineering",
    "rohit.pandey": "Data Engineering",
    "kritik.kumar": "Data Engineering",
    "vignesh.bodiga": "Data Engineering",
    "yashveer.gaur": "Data Engineering",
    "naveena.kolli": "Data Engineering",
    "waheedul.akbar": "Data Engineering",
    "satyam.deshwal": "Data Engineering",
    "sreeram.ravuri": "Data Engineering",
    "abhignya.pinnoju": "Data Engineering",
    "joseph.ubbarapu": "Data Engineering",
    "ritvik.vemula": "Data Engineering",
    "mahadas.srianshu": "Data Engineering",
    "mohammed.muneeb": "Data Engineering",
    "nithin.dornala": "Data Engineering",
    "jigar.trivedi": "Data Engineering",
    "reeshab.nayak": "Data Engineering",
    "ajay": "Data Engineering",
    "sarath.buchi": "Data Engineering",
    "anudeep.kolla": "IT",
    "rudrasen.gouda": "Data Engineering",
    "ram.gupta": "Data Engineering",
    "thanoj.rahul": "Data Engineering",
    "ripujit": "Data Engineering",
    "arijit.bhattacharyya": "Data Engineering",
    "kaivalya": "Data Engineering",
    "kranthi.kumar": "Data Engineering",
    "srinithi.reddy": "Data Engineering",
}


def dept_for(email):
    e = email.lower()
    prefix = e.replace("@sarasanalytics.com", "")
    # Priority: HR roster > static DEPT_MAP > DEPT_DEFAULTS > stale existing value
    return (all_emp_dept.get(e) or dept_map.get(e)
            or DEPT_DEFAULTS.get(prefix) or existing_dept.get(e) or "Data Engineering")


def name_for(email, fallback):
    return existing_name.get(email.lower()) or fallback


people = []
for email, rec in fresh["clickup"]["people"].items():
    if rec.get("open", 0) == 0 and rec.get("done", 0) == 0:
        continue
    nm = name_for(email, rec.get("name") or email.split("@")[0])
    dp = dept_for(email)
    tasks = rec.get("tasks", [])
    safe_tasks = []
    for t in tasks:
        title_raw = (t.get("title") or "").replace("\\", "\\\\").replace('"', '\\"').replace('[', '(').replace(']', ')')
        # Truncate to 80 chars
        # NOTE: We don't check for balanced quotes here because they're already escaped
        # The escaped \" sequence will not cause JavaScript issues
        title = title_raw[:80]
        # Check for unbalanced parentheses (which won't be escaped)
        if title.count('(') != title.count(')'):
            # Remove extra opening or closing parens
            open_count = title.count('(')
            close_count = title.count(')')
            if open_count > close_count:
                # More opens than closes - remove trailing opens
                while title.count('(') > title.count(')') and len(title) > 0:
                    title = title[:-1]
            elif close_count > open_count:
                # More closes than opens - remove trailing closes
                while title.count(')') > title.count('(') and len(title) > 0:
                    title = title[:-1]
        due      = t.get("due")
        due_js   = f'"{due}"' if due else "null"
        url      = (t.get("url") or "").replace("\\", "\\\\").replace('"', '\\"')
        prio     = t.get("priority") or "normal"
        safe_tasks.append(f'{{title:"{title}",priority:"{prio}",due:{due_js},url:"{url}"}}')
    tasks_js    = "[" + ",".join(safe_tasks) + "]"
    next_due    = rec.get("nextDue")
    next_due_js = f'"{next_due}"' if next_due else "null"
    name_safe   = nm.replace("\\", "\\\\").replace('"', '\\"')
    people.append({
        "sort_open": rec.get("open", 0),
        "js": (
            f'    {{name:"{name_safe}", email:"{email}", dept:"{dp}", '
            f"open:{rec.get('open',0)}, done:{rec.get('done',0)}, "
            f"urgent:{rec.get('urgent',0)}, high:{rec.get('high',0)}, "
            f"normal:{rec.get('normal',0)}, low:{rec.get('low',0)}, "
            f"nextDue:{next_due_js}, tasks:{tasks_js}}},"
        ),
    })

people.sort(key=lambda p: -p["sort_open"])
people_js = "\n".join(p["js"] for p in people)

new_clickup_block = (
    "const CLICKUP_DATA = {\n"
    f'  asOf: "{TODAY}",\n'
    "  people: [\n"
    + people_js + "\n"
    "  ]\n"
    "};"
)

if clickup_block_match:
    html = html.replace(clickup_block_match.group(0), new_clickup_block, 1)
    log(f"  CLICKUP_DATA: {len(people)} people")
else:
    log("  [WARN] CLICKUP_DATA block not found in HTML")


# ── Update DATA block ─────────────────────────────────────────────────────────
total_lines    = cc["totalLines"]
accept_rate    = cc["acceptRate"]
active_members = cc["activeMembers"]
total_members  = cc["totalMembers"]
utilization    = round(cc["utilization"])
members_dict   = cc["members"]
cc_as_of       = cc.get("asOf", TODAY)
wau            = cc.get("wau", 0)
mau            = cc.get("mau", 0)
licensed_seats = cc.get("assignedSeats", 150)  # Auto-fetched from Claude Admin daily
cursor_seats   = cc.get("cursorSeats", 63)     # Auto-fetched from environment/API daily
windsurf_seats = cc.get("windsurfSeats", 8)    # Auto-fetched from environment/API daily

all_lines    = sum(members_dict.values())
avg_lines    = round(all_lines / active_members) if active_members else 0

# Per-day trend arrays (from API; may be empty on first run)
chats_daily  = cc.get("chatsDailyData", [])
cowork_daily = cc.get("coworkDailyData", [])
chats_as_of  = chats_daily[-1]["date"]  if chats_daily  else cc_as_of
cowork_as_of = cowork_daily[-1]["date"] if cowork_daily else cc_as_of

# Cowork daily avg from actual per-day data (not wau/7 which is meaningless)
cowork_daily_avg = round(sum(d["users"] for d in cowork_daily) / len(cowork_daily)) if cowork_daily else cc.get("coworkSessionsPerDay", 0)

chats_daily_js  = json.dumps(chats_daily,  separators=(",", ":"))
cowork_daily_js = json.dumps(cowork_daily, separators=(",", ":"))

# Model usage data (if available)
model_usage      = cc.get("modelUsage", {})
model_cost       = cc.get("modelCost", {})
model_cost_daily = cc.get("modelCostDaily", {})
model_usage_json      = json.dumps(model_usage,      separators=(",", ":"))
model_cost_json       = json.dumps(model_cost,       separators=(",", ":"))
model_cost_daily_json = json.dumps(model_cost_daily, separators=(",", ":"))

# User-level data from available sources
chat_users = cc.get("chatUsers", {})
cowork_users = cc.get("coworkUsers", {})

# userUsage: chat counts per user (shown as "Chat Activity by User")
user_usage = dict(chat_users)

# userCost: not estimated — no real per-user API cost data available
user_cost = {}

# userDailyAvg: daily chat average per user (chats / working days MTD)
days_elapsed = max(1, len(cc.get("chatsDailyData", [])))
user_daily_avg = {}
for user, chats in chat_users.items():
    user_daily_avg[user] = round(chats / days_elapsed, 1) if chats > 0 else 0

# conversations: populate from chatUsers for the Conversations by User table
conversations = {
    user: {"count": chats, "messages": 0}
    for user, chats in chat_users.items()
}

user_usage_json      = json.dumps(user_usage,     separators=(",", ":"))
user_cost_json       = json.dumps(user_cost,      separators=(",", ":"))
user_daily_avg_json  = json.dumps(user_daily_avg, separators=(",", ":"))
conversations_json   = json.dumps(conversations,  separators=(",", ":"))

new_data_block = f"""const DATA = {{
  asOf: '{TODAY_LABEL}',
  monthLabel: '{MONTH_LABEL}',
  utilization: {utilization},
  licensedSeats: {licensed_seats},
  totalLines: {total_lines},
  acceptRate: {accept_rate},
  activeMembers: {active_members},
  totalMembers: {total_members},
  orgSize: 209,
  avgLinesActive: {avg_lines},
  coworkDailyUsers: {cowork_daily_avg},
  coworkSessionsPerDay: {cc.get('coworkSessionsPerDay', 0)},
  coworkUserPct: {cc.get('coworkUserPct', 0)},
  chatsPerDay: {cc.get('chatsPerDay', 0)},
  chatUserPct: {cc.get('chatUserPct', 0)},
  wau: {wau},
  mau: {mau},
  projectsCreated: {cc.get('projectsCreated', 0)},
  projectUserPct: {cc.get('projectUserPct', 0)},
  artifactsCreated: {cc.get('artifactsCreated', 0)},
  artifactUserPct: {cc.get('artifactUserPct', 0)},
  chatsDailyData: {chats_daily_js},
  coworkDailyData: {cowork_daily_js},
  dataAsOf: '{cc_as_of}',
  chatsDataAsOf: '{chats_as_of}',
  coworkDataAsOf: '{cowork_as_of}',
  modelUsage: {model_usage_json},
  modelCost: {model_cost_json},
  modelCostDaily: {model_cost_daily_json},
  userUsage: {user_usage_json},
  userCost: {user_cost_json},
  userDailyAvg: {user_daily_avg_json},
  conversations: {conversations_json},
  claudeAITokens: 0,
  claudeCodeTokens: 0,
  apiTokens: 0,
}};"""

if total_lines > 0 or active_members > 0:
    html = re.sub(r"const DATA = \{.*?\};", new_data_block, html, count=1, flags=re.DOTALL)
    log(f"  DATA block updated: {total_lines:,} lines, {active_members}/{total_members} members")
else:
    log(f"  DATA block: API returned all zeros — keeping existing DATA block in HTML")

# -- Update spendData
claude_spend = cc.get("claudeSpend", {})
cursor_spend = cc.get("cursorSpend")
windsurf_spend = cc.get("windsurfSpend")

spend_data = {
    "claude": {
        "mtd": claude_spend.get("mtd", 0),
        "monthly": claude_spend.get("monthly", 0),
        "seats": licensed_seats,
        "perSeat": 20,
        "subscription": licensed_seats * 20,   # Dynamic seat cost
    },
    "cursor": {
        "mtd": cursor_spend.get("mtd", 0) if cursor_spend else 0,
        "monthly": cursor_spend.get("monthly", 0) if cursor_spend else 0,
        "seats": cursor_seats,
        "perSeat": 20,
        "subscription": cursor_seats * 20,    # Dynamic seat cost
    },
    "windsurf": {
        "mtd": windsurf_spend.get("mtd", 0) if windsurf_spend else 0,
        "monthly": windsurf_spend.get("monthly", 0) if windsurf_spend else 0,
        "seats": windsurf_seats,
        "perSeat": 30,
        "subscription": windsurf_seats * 30,     # Dynamic seat cost
    },
}

spend_js = json.dumps(spend_data, separators=(",", ":"))
for key in ["mtd", "monthly", "seats", "perSeat", "subscription", "claude", "cursor", "windsurf"]:
    spend_js = spend_js.replace(f'"{key}"', key)
new_spend_line = f"const spendData = {spend_js};"
if spend_data['claude']['mtd'] > 0 or spend_data['cursor']['mtd'] > 0 or spend_data['windsurf']['mtd'] > 0:
    html = re.sub(r"const spendData = \{.*?\};", new_spend_line, html, count=1, flags=re.DOTALL)
    log(f"  spendData: Claude ${spend_data['claude']['mtd']}, Cursor ${spend_data['cursor']['mtd']}, Windsurf ${spend_data['windsurf']['mtd']}")
else:
    log(f"  spendData: all zeros from API — keeping existing spendData in HTML")


# ── Update members[] ──────────────────────────────────────────────────────────
# Build per-member lines list (sorted desc)


NAME_DEFAULTS = {
    "sparsh.gupta": "Sparsh Gupta",
    "bheem": "Bheem",
    "bhawna.kumari": "Bhawna Kumari",
    "rakesh.varma": "Rakesh Varma",
    "saivarun.pathi": "Sai Varun Pathi",
    "saiteja.katrapati": "Sai Teja Katrapati",
    "rajavardhan": "Rajavardhan",
    "venkateshwaran.s": "Venkateshwaran S",
    "harsh.sharma": "Harsh Sharma",
    "moiz.essaji": "Moiz Essaji",
    "katharin.benson": "Katharin Benson",
    "anusha": "Anusha Danda",
    "rohit.pandey": "Rohit Pandey",
    "kritik.kumar": "Kritik Kumar",
    "vignesh.bodiga": "Vignesh Bodiga",
    "yashveer.gaur": "Yashveer Gaur",
    "naveena.kolli": "Naveena Kolli",
    "waheedul.akbar": "Waheedul Akbar",
    "satyam.deshwal": "Satyam Deshwal",
    "sreeram.ravuri": "Sreeram Ravuri",
    "abhignya.pinnoju": "Abhignya Pinnoju",
    "joseph.ubbarapu": "Joseph Ubbarapu",
    "ritvik.vemula": "Ritvik Vemula",
    "mahadas.srianshu": "Mahadas Srianshu",
    "mohammed.muneeb": "Mohammed Muneeb",
    "nithin.dornala": "Nithin Dornala",
    "jigar.trivedi": "Jigar Trivedi",
    "reeshab.nayak": "Reeshab Nayak",
    "ajay": "Ajay Kottapally",
    "sarath.buchi": "Sarath Buchi",
    "anudeep.kolla": "Anudeep Kolla",
    "rudrasen.gouda": "Rudrasen Gouda",
    "ram.gupta": "Ram Gupta",
    "thanoj.rahul": "Thanoj Rahul",
    "ripujit": "Ripujit",
    "arijit.bhattacharyya": "Arijit Bhattacharyya",
    "kaivalya": "Kaivalya Sathe",
    "kranthi.kumar": "Kranthi Kumar",
    "srinithi.reddy": "Srinithi Reddy",
}


def get_dept_for_member(prefix):
    email = f"{prefix}@sarasanalytics.com"
    # Priority: HR roster (ALL_EMPLOYEES) > static DEPT_MAP > DEPT_DEFAULTS
    return (all_emp_dept.get(email) or dept_map.get(email)
            or DEPT_DEFAULTS.get(prefix, "Data Engineering"))


# Exclude service/shared accounts from the per-member leaderboard
BOT_EMAILS = {'consulting@sarasanalytics.com', 'consulting.claude@sarasanalytics.com'}
members_sorted = sorted(
    [(e, l) for e, l in members_dict.items() if e not in BOT_EMAILS],
    key=lambda x: -x[1]
)
member_lines = []
for email, lines in members_sorted:
    prefix = email.replace("@sarasanalytics.com", "")
    name   = NAME_DEFAULTS.get(prefix) or prefix.replace(".", " ").title()
    dept   = get_dept_for_member(prefix)
    member_lines.append(f"  {{name:'{name}', email:'{email}', lines:{lines}, dept:'{dept}'}},")

new_members_block = "const members = [\n" + "\n".join(member_lines) + "\n];"
if len(member_lines) > 0:
    html = re.sub(r"const members = \[.*?\];", new_members_block, html, count=1, flags=re.DOTALL)
    log(f"  members[]: {len(member_lines)} entries")
else:
    log(f"  members[]: API returned 0 entries — keeping existing members[] in HTML")


# ── Update CLAUDE_AI_USERS ────────────────────────────────────────────────────
chat_users = cc.get("chatUsers", {})
new_cau_obj  = json.dumps(chat_users, separators=(",", ":"))
new_cau_line = f"const CLAUDE_AI_USERS={new_cau_obj};"
if len(chat_users) > 0:
    html = re.sub(r"const CLAUDE_AI_USERS=\{[^}]*\};", new_cau_line, html, count=1)
log(f"  CLAUDE_AI_USERS: {len(chat_users)} entries")

# ── Update COWORK_USERS ───────────────────────────────────────────────────────
cowork_users = cc.get("coworkUsers", {})
new_cwu_obj  = json.dumps(cowork_users, separators=(",", ":"))
new_cwu_line = f"const COWORK_USERS={new_cwu_obj};"
if len(cowork_users) > 0:
    html = re.sub(r"const COWORK_USERS=\{[^}]*\};", new_cwu_line, html, count=1)
log(f"  COWORK_USERS: {len(cowork_users)} entries")


# ── Update WINDSURF_USERS ─────────────────────────────────────────────────────
ws_match = re.search(r"const WINDSURF_USERS=\{[^}]*\};", html)
if ws_match:
    ws_lines = ["const WINDSURF_USERS={"]
    for em, dt in fresh["windsurf_users"].items():
        val = f'"{dt}"' if dt else "null"
        ws_lines.append(f'  "{em}":{val},')
    ws_lines.append("};")
    html = html.replace(ws_match.group(0), "\n".join(ws_lines), 1)
    log(f"  WINDSURF_USERS: {len(fresh['windsurf_users'])} entries")
else:
    log("  [WARN] WINDSURF_USERS block not found")


# ── Update saras-daily-adoption.json ─────────────────────────────────────────
prior_cc_mtd = adoption.get("claudeCodeMTD", {})

# Compute today's daily delta for Claude Code
cc_daily = {}
for em, lines in members_dict.items():
    prior = prior_cc_mtd.get(em, 0)
    delta = max(0, lines - prior)
    if delta > 0:
        cc_daily[em] = delta

today_entry = {
    "date": TODAY,
    "cursor":  dict(fresh["cursor_today"]),
    "windsurf": dict(fresh["windsurf_today"]),
    "clickup": dict(fresh["clickup"]["daily_active"]),
    "claudeCodeDaily": cc_daily,
}

days  = adoption.get("days", [])
found = False
for i, d in enumerate(days):
    if d.get("date") == TODAY:
        days[i] = today_entry
        found = True
        break
if not found:
    days.append(today_entry)

adoption["days"]           = days
adoption["claudeCodeMTD"]  = members_dict
adoption["lastUpdated"]    = datetime.now(timezone.utc).isoformat()

ADOPTION_PATH.write_text(json.dumps(adoption, indent=2), encoding="utf-8")
log(f"  Adoption JSON: {len(days)} days, CC daily delta {len(cc_daily)} users ({sum(cc_daily.values()):,} lines)")


# ── Update ADOPTION_CC_MTD in HTML ────────────────────────────────────────────
new_cc_mtd_obj  = json.dumps(members_dict, separators=(",", ":"))
new_cc_mtd_line = f"const ADOPTION_CC_MTD={new_cc_mtd_obj};"
html = re.sub(r"const ADOPTION_CC_MTD=\{[^}]*\};", new_cc_mtd_line, html, count=1)
log("  ADOPTION_CC_MTD updated")


# ── Update ADOPTION_DAYS in HTML ──────────────────────────────────────────────
def compact_day(d):
    parts = [f'date:"{d["date"]}"']
    c_obj = ",".join(f'"{k}":{v}' for k, v in d.get("cursor", {}).items())
    parts.append(f"c:{{{c_obj}}}")
    w_obj = ",".join(f'"{k}":{v}' for k, v in d.get("windsurf", {}).items())
    parts.append(f"w:{{{w_obj}}}")
    if "clickup" in d:
        cu_obj = ",".join(f'"{k}":{v}' for k, v in d.get("clickup", {}).items())
        parts.append(f"cu:{{{cu_obj}}}")
    if "claudeCodeDaily" in d:
        cc_obj = ",".join(f'"{k}":{v}' for k, v in d.get("claudeCodeDaily", {}).items())
        parts.append(f"cc:{{{cc_obj}}}")
    return "{" + ",".join(parts) + "}"


last30 = days[-30:]
adoption_array_js = "[" + ",".join(compact_day(d) for d in last30) + "]"
new_adoption_line = f"const ADOPTION_DAYS={adoption_array_js};"
html = re.sub(r"const ADOPTION_DAYS=\[.*?\];", new_adoption_line, html, count=1, flags=re.DOTALL)
log(f"  ADOPTION_DAYS: {len(last30)} days")


# ── Update API_AGENTS_DATA in HTML ───────────────────────────────────────────
if api_agents:
    # Use ensure_ascii=True (default) so all Unicode is \uXXXX, then use a lambda
    # in re.sub to prevent those escape sequences from being re-interpreted.
    agents_json = json.dumps(api_agents, separators=(",", ":"), ensure_ascii=True)
    new_agents_line = f"const API_AGENTS_DATA={agents_json};"
    if re.search(r"const API_AGENTS_DATA=\{.*?\};", html, flags=re.DOTALL):
        html = re.sub(
            r"const API_AGENTS_DATA=\{.*?\};",
            lambda _: new_agents_line,   # lambda avoids \uXXXX being treated as regex escapes
            html, count=1, flags=re.DOTALL
        )
        log(f"  API_AGENTS_DATA: {len(api_agents.get('agents', []))} agents, MTD=${api_agents.get('totalApiSpendMtd', 0):.2f}")
    else:
        log("  [WARN] API_AGENTS_DATA placeholder not found in HTML")
else:
    log("  API_AGENTS_DATA: no data file yet — placeholder kept as-is")

# ── Update MONTHLY_SNAPSHOTS (real per-month trend history) ────────────────────
# Record this month's actual metrics once per month so the Trends tab builds a
# real trend over time instead of relying on fabricated history. The current
# month's entry is upserted on every run; prior months are preserved.
try:
    month_key = TODAY[:7]
    cur_days = [d for d in adoption.get("days", []) if d.get("date", "")[:7] == month_key]
    cursor_events = 0
    cursor_daily_counts = []
    for d in cur_days:
        cur = d.get("cursor") or {}
        cursor_events += sum(cur.values())
        if cur:
            cursor_daily_counts.append(len(cur))
    cursor_dau = round(sum(cursor_daily_counts) / len(cursor_daily_counts)) if cursor_daily_counts else 0

    snapshot = {
        "monthKey":        month_key,
        "month":           MONTH_LABEL,
        "monthShort":      datetime.strptime(TODAY, "%Y-%m-%d").strftime("%b %Y"),
        "lines":           total_lines,
        "activeMembers":   active_members,
        "chatsPerDay":     cc.get("chatsPerDay", 0),
        "chatUserPct":     cc.get("chatUserPct", 0),
        "coworkDAU":       cowork_daily_avg,
        "cursorDAU":       cursor_dau,
        "cursorEvents":    cursor_events,
        "projectsCreated": cc.get("projectsCreated", 0),
        "artifactUserPct": cc.get("artifactUserPct", 0),
    }

    snaps = []
    if MONTHLY_SNAPSHOTS_PATH.exists():
        try:
            snaps = json.loads(MONTHLY_SNAPSHOTS_PATH.read_text(encoding="utf-8"))
        except Exception:
            snaps = []
    snaps = [s for s in snaps if s.get("monthKey") != month_key]   # drop stale current-month entry
    snaps.append(snapshot)
    snaps.sort(key=lambda s: s.get("monthKey", ""))

    # Only persist real data — never overwrite history with an all-zero run.
    if total_lines > 0 or active_members > 0:
        MONTHLY_SNAPSHOTS_PATH.write_text(json.dumps(snaps, indent=2) + "\n", encoding="utf-8")
        snaps_js = json.dumps(snaps, separators=(",", ":"))
        html = re.sub(r"const MONTHLY_SNAPSHOTS = \[.*?\];",
                      lambda m: "const MONTHLY_SNAPSHOTS = " + snaps_js + ";",
                      html, count=1, flags=re.DOTALL)
        log(f"  MONTHLY_SNAPSHOTS: {len(snaps)} month(s), latest {month_key}")
    else:
        log("  MONTHLY_SNAPSHOTS: API returned zeros — keeping existing snapshots")
except Exception as e:
    log(f"  [WARN] MONTHLY_SNAPSHOTS update failed: {e}")


# ── Inject OAuth credentials ──────────────────────────────────────────────────
microsoft_client_id = os.getenv('MICROSOFT_CLIENT_ID', '__MICROSOFT_CLIENT_ID__')
backend_url = os.getenv('BACKEND_URL', '__BACKEND_URL__')

html = html.replace('__MICROSOFT_CLIENT_ID__', microsoft_client_id)
html = html.replace('__BACKEND_URL__', backend_url)

if microsoft_client_id != '__MICROSOFT_CLIENT_ID__':
    log(f"  OAuth: Microsoft Client ID injected")
if backend_url != '__BACKEND_URL__':
    log(f"  OAuth: Backend URL injected")


# ── Write HTML ────────────────────────────────────────────────────────────────
HTML_PATH.write_text(html, encoding="utf-8")
log(f"\nDone. index.html updated.")


