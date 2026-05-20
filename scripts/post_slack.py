#!/usr/bin/env python3
"""
post_slack.py — Posts the daily AI usage report to Slack.

Reads:
  data/daily_collected.json   — Cursor/Windsurf/ClickUp stats
  data/claude_ai_stats.json   — Claude.ai stats (manually maintained)

Required environment variables:
  SLACK_BOT_TOKEN    xoxb-... token with chat:write scope
  SLACK_CHANNEL      Channel ID (default: C0B3AA5ERKL)
"""
import json
import os
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime

REPO_ROOT      = Path(__file__).resolve().parent.parent
COLLECTED_PATH = REPO_ROOT / "data" / "daily_collected.json"
CLAUDE_PATH    = REPO_ROOT / "data" / "claude_ai_stats.json"

SLACK_TOKEN   = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL = os.environ.get("SLACK_CHANNEL", "C0B3AA5ERKL")
SLACK_WEBHOOK = os.environ.get("SLACK_WEBHOOK_URL", "")

with COLLECTED_PATH.open(encoding="utf-8") as f:
    fresh = json.load(f)

with CLAUDE_PATH.open(encoding="utf-8") as f:
    cc = json.load(f)

TODAY = fresh["today"]
date_dt = datetime.strptime(TODAY, "%Y-%m-%d")
DATE_LABEL = date_dt.strftime("%B %-d, %Y")

# ── Claude Code stats ─────────────────────────────────────────────────────────
members_dict   = cc["members"]
total_lines    = cc["totalLines"]
accept_rate    = cc["acceptRate"]
active_members = cc["activeMembers"]
total_members  = cc["totalMembers"]
utilization    = cc["utilization"]
wau            = cc.get("wau", 0)
pending_invites = cc.get("pendingInvites", 0)
cowork_sessions_per_day = cc.get("coworkSessionsPerDay", 0)
cowork_user_pct = cc.get("coworkUserPct", 0)
chats_per_day   = cc.get("chatsPerDay", 0)
chat_user_pct   = cc.get("chatUserPct", 0)
projects        = cc.get("projectsCreated", 0)
project_user_pct = cc.get("projectUserPct", 0)
artifacts       = cc.get("artifactsCreated", 0)
artifact_user_pct = cc.get("artifactUserPct", 0)
cc_as_of        = cc.get("asOf", TODAY)

# ── Leaderboard ───────────────────────────────────────────────────────────────
members_sorted = sorted(members_dict.items(), key=lambda x: -x[1])

NAME_MAP = {
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
}

medals  = [":first_place_medal:", ":second_place_medal:", ":third_place_medal:"]
top10   = members_sorted[:10]
leaderboard = []
for i, (email, lines) in enumerate(top10):
    prefix = email.replace("@sarasanalytics.com", "")
    name   = NAME_MAP.get(prefix) or prefix.replace(".", " ").title()
    icon   = medals[i] if i < 3 else f"{i+1}."
    leaderboard.append(f"{icon} {name} — {lines:,}")

zero_members = [(em, v) for em, v in members_sorted if v == 0
                and not em.startswith("anudeep.kolla")]
low_members  = [(em, v) for em, v in members_sorted if 0 < v < 100]
last_active_lines = members_sorted[active_members - 1][1] if active_members else 0
member_11_lines   = members_sorted[10][1] if len(members_sorted) > 10 else 0

zero_count = len(zero_members)
low_str    = ", ".join(f"{NAME_MAP.get(em.replace('@sarasanalytics.com','')) or em.split('@')[0].split('.')[0].title()} ({v})"
                        for em, v in low_members)
low_count  = len(low_members)
zero_names = [NAME_MAP.get(em.replace("@sarasanalytics.com","")) or em.split("@")[0].split(".")[0].title()
              for em, _ in zero_members]
zero_list  = ", ".join(zero_names[:5])
zero_extra = f" + {zero_count - 5} more" if zero_count > 5 else ""

# ── ClickUp stats ─────────────────────────────────────────────────────────────
clickup_done  = fresh["clickup"]["done_count"]
done_top      = fresh["clickup"]["done_top"][:5]
list_freq_top = fresh["clickup"]["list_freq"][:5]


def short_name(email):
    parts = email.split("@")[0].split(".")
    if len(parts) >= 2:
        return f"{parts[0].title()} {parts[1][0].upper()}"
    return parts[0].title()


contrib_str = ", ".join(f"{short_name(em)} ({n})" for em, n in done_top)
lists_str   = ", ".join(ln for ln, _ in list_freq_top)

# ── Build stale-data note ─────────────────────────────────────────────────────
stale_note = ""
if cc_as_of != TODAY:
    stale_note = f"\n_⚠️ Claude.ai figures from {cc_as_of} — update data/claude_ai_stats.json for fresh numbers_"

msg = (
    f":bar_chart: *Saras Analytics — Full Tool Usage Report · {DATE_LABEL}*\n"
    "_Claude · Claude Code · Cowork · ClickUp · Data in UTC · Updated daily_\n"
    "_⚠️ Analytics data is 2 days delayed — figures shown reflect usage through prior day (UTC)_"
    + stale_note + "\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━\n"
    ":1234: *OVERALL ACTIVITY*\n"
    f"WAU: {wau} | Seat Utilization: {utilization}% | Pending Invites: {pending_invites}\n\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━\n"
    ":computer: *CLAUDE CODE — Month MTD*\n"
    f"Lines accepted: {total_lines:,} | Accept rate: {accept_rate}% | Active members: {active_members} of {total_members}\n\n\n"
    "Top 10 Contributors (Lines This Month):\n"
    + "\n".join(leaderboard) + "\n"
    f"_(Members 11–{active_members} range from {member_11_lines:,} → {last_active_lines:,} lines · {zero_count} members at 0 lines)_\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━\n"
    ":handshake: *COWORK*\n"
    f"Sessions/day: {cowork_sessions_per_day} | Users with 1+ session: {cowork_user_pct}%\n\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━\n"
    ":speech_balloon: *CLAUDE.AI*\n"
    f"Chats/day: {chats_per_day} | Users with 1+ chat: {chat_user_pct}%\n"
    f"Projects created: {projects} | Users with 1+ project: {project_user_pct}%\n"
    f"Artifacts created: {artifacts} | Users with 1+ artifact: {artifact_user_pct}%\n\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━\n"
    ":white_check_mark: *CLICKUP*\n"
    f"57 active spaces | {clickup_done}+ tasks closed this month\n"
    f"Top contributors: {contrib_str}\n"
    f"Most active lists: {lists_str}\n\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━\n"
    ":warning: *NEEDS ATTENTION*\n"
    f"{zero_count} members with 0 Claude Code lines: {zero_list}{zero_extra}\n"
)

if low_count:
    msg += f"{low_count} members critically low (<100 lines): {low_str}\n"

msg += (
    f"{100 - chat_user_pct}% of org has not had a single Claude.ai chat this month\n"
    f"{100 - project_user_pct}% of org hasn't used Projects — huge untapped productivity lever\n"
    f"{100 - cowork_user_pct}% of org hasn't tried Cowork yet\n\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━\n"
    ":mag: *CODE REVIEW* — No data available\n\n\n"
    ":bar_chart: _Full interactive dashboard →_ "
    "<https://anudeepkolla16.github.io/saras-usage-dashboard/|Open Dashboard>"
)

# ── Post to Slack ─────────────────────────────────────────────────────────────
if SLACK_WEBHOOK:
    # Incoming webhook — simpler, no need for bot to be in channel
    payload = json.dumps({"text": msg}).encode()
    req = urllib.request.Request(
        SLACK_WEBHOOK,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode()
        if body.strip() == "ok":
            print("Slack message posted via webhook.")
        else:
            print(f"[WARN] Webhook response: {body}")
    except urllib.error.HTTPError as e:
        print(f"[ERROR] Webhook HTTP {e.code}: {e.read().decode()}", flush=True)
        raise SystemExit(1)
elif SLACK_TOKEN:
    # Bot token with chat:write scope
    payload = json.dumps({"channel": SLACK_CHANNEL, "text": msg}).encode()
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {SLACK_TOKEN}",
            "Content-Type":  "application/json; charset=utf-8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read())
        if resp.get("ok"):
            print(f"Slack message posted to {SLACK_CHANNEL}. ts={resp.get('ts')}")
        else:
            print(f"[ERROR] Slack API error: {resp.get('error')}", flush=True)
            raise SystemExit(1)
    except urllib.error.HTTPError as e:
        print(f"[ERROR] HTTP {e.code}: {e.read().decode()}", flush=True)
        raise SystemExit(1)
else:
    print("[ERROR] Set SLACK_WEBHOOK_URL or SLACK_BOT_TOKEN environment variable.", flush=True)
    raise SystemExit(1)
