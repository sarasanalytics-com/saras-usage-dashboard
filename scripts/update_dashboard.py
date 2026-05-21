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
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT    = Path(__file__).resolve().parent.parent
HTML_PATH    = REPO_ROOT / "index.html"
ADOPTION_PATH = REPO_ROOT / "data" / "saras-daily-adoption.json"
COLLECTED_PATH = REPO_ROOT / "data" / "daily_collected.json"
CLAUDE_STATS_PATH = REPO_ROOT / "data" / "claude_ai_stats.json"


def log(msg):
    print(msg, flush=True)


# ── Load data ─────────────────────────────────────────────────────────────────
with COLLECTED_PATH.open(encoding="utf-8") as f:
    fresh = json.load(f)

with CLAUDE_STATS_PATH.open(encoding="utf-8") as f:
    cc = json.load(f)

with ADOPTION_PATH.open(encoding="utf-8") as f:
    adoption = json.load(f)

with HTML_PATH.open(encoding="utf-8") as f:
    html = f.read()

TODAY     = fresh["today"]
MONTH_LABEL = datetime.strptime(TODAY, "%Y-%m-%d").strftime("%B %Y")
TODAY_LABEL = datetime.strptime(TODAY, "%Y-%m-%d").strftime("%B %-d, %Y")

# ── Build CLICKUP_DATA.people ─────────────────────────────────────────────────
# Extract existing dept / name mappings from HTML so we don't lose them
existing_dept = {}
existing_name = {}

clickup_block_match = re.search(r"const CLICKUP_DATA = \{.*?\n\};", html, flags=re.DOTALL)
if clickup_block_match:
    blk = clickup_block_match.group(0)
    for m in re.finditer(r"name:'([^']*)',\s*email:'([^']+)',\s*dept:'([^']*)'", blk):
        name, email, dept = m.group(1), m.group(2), m.group(3)
        existing_dept[email.lower()] = dept
        existing_name[email.lower()] = name

dept_map = {}
dept_block = re.search(r"const DEPT_MAP = \{(.*?)\};", html, flags=re.DOTALL)
if dept_block:
    for m in re.finditer(r"'([^']+@sarasanalytics\.com)':\s*'([^']+)'", dept_block.group(1)):
        dept_map[m.group(1).lower()] = m.group(2)

# Extract ALL_EMPLOYEES dept (authoritative HR source)
all_emp_dept = {}
all_emp_block = re.search(r"const ALL_EMPLOYEES = \[(.*?)\];", html, flags=re.DOTALL)
if all_emp_block:
    for m in re.finditer(r"email:'([^']+)'.*?dept:'([^']+)'", all_emp_block.group(1), flags=re.DOTALL):
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
        title    = (t.get("title") or "").replace("'", "\\'").replace('"', '\\"')[:80]
        due      = t.get("due")
        due_js   = f"'{due}'" if due else "null"
        url      = (t.get("url") or "").replace("'", "\\'")
        prio     = t.get("priority") or "normal"
        safe_tasks.append(f"{{title:'{title}',priority:'{prio}',due:{due_js},url:'{url}'}}")
    tasks_js    = "[" + ",".join(safe_tasks) + "]"
    next_due    = rec.get("nextDue")
    next_due_js = f"'{next_due}'" if next_due else "null"
    name_safe   = nm.replace("'", "\\'")
    people.append({
        "sort_open": rec.get("open", 0),
        "js": (
            f"    {{name:'{name_safe}', email:'{email}', dept:'{dp}', "
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
    f"  asOf: '{TODAY}',\n"
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
model_usage = cc.get("modelUsage", {})
model_cost  = cc.get("modelCost", {})
model_usage_json = json.dumps(model_usage, separators=(",", ":"))
model_cost_json  = json.dumps(model_cost,  separators=(",", ":"))

new_data_block = f"""const DATA = {{
  asOf: '{TODAY_LABEL}',
  monthLabel: '{MONTH_LABEL}',
  utilization: {utilization},
  licensedSeats: 150,
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
  chatsDataAsOf: '{chats_as_of}',
  coworkDataAsOf: '{cowork_as_of}',
  modelUsage: {model_usage_json},
  modelCost: {model_cost_json},
}};"""

html = re.sub(r"const DATA = \{.*?\};", new_data_block, html, count=1, flags=re.DOTALL)
log(f"  DATA block updated: {total_lines:,} lines, {active_members}/{total_members} members")


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
html = re.sub(r"const members = \[.*?\];", new_members_block, html, count=1, flags=re.DOTALL)
log(f"  members[]: {len(member_lines)} entries")


# ── Update CLAUDE_AI_USERS ────────────────────────────────────────────────────
chat_users = cc.get("chatUsers", {})
new_cau_obj  = json.dumps(chat_users, separators=(",", ":"))
new_cau_line = f"const CLAUDE_AI_USERS={new_cau_obj};"
html = re.sub(r"const CLAUDE_AI_USERS=\{[^}]*\};", new_cau_line, html, count=1)
log(f"  CLAUDE_AI_USERS: {len(chat_users)} entries")

# ── Update COWORK_USERS ───────────────────────────────────────────────────────
cowork_users = cc.get("coworkUsers", {})
new_cwu_obj  = json.dumps(cowork_users, separators=(",", ":"))
new_cwu_line = f"const COWORK_USERS={new_cwu_obj};"
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


# ── Write HTML ────────────────────────────────────────────────────────────────
HTML_PATH.write_text(html, encoding="utf-8")
log(f"\nDone. index.html updated.")
