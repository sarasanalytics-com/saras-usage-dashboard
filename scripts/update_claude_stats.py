#!/usr/bin/env python3
"""
update_claude_stats.py — Quick helper to refresh data/claude_ai_stats.json
from today's claude.ai/analytics readings, then commit & push so GitHub
Actions picks up the fresh numbers on its next run.

Usage (run from repo root):
  python scripts/update_claude_stats.py

The script prompts you for each number (press Enter to keep the current value).
After editing, it commits the file and pushes automatically.

Alternatively, edit data/claude_ai_stats.json directly and run:
  git add data/claude_ai_stats.json && git commit -m "Update Claude.ai stats" && git push
"""
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT  = Path(__file__).resolve().parent.parent
STATS_PATH = REPO_ROOT / "data" / "claude_ai_stats.json"

with STATS_PATH.open(encoding="utf-8") as f:
    stats = json.load(f)

today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
print(f"\n=== Claude.ai Stats Update ({today}) ===")
print(f"Current data is from: {stats.get('asOf', '?')}")
print("(Press Enter to keep current value, or type new value)\n")


def prompt_int(label, key):
    cur = stats.get(key, 0)
    val = input(f"  {label} [{cur}]: ").strip()
    if val:
        stats[key] = int(val)


def prompt_float(label, key):
    cur = stats.get(key, 0)
    val = input(f"  {label} [{cur}]: ").strip()
    if val:
        stats[key] = float(val)


# ── Claude Code ───────────────────────────────────────────────────────────────
print("--- Claude Code (from claude.ai/analytics/claude-code) ---")
prompt_int("Total Lines Accepted (MTD)", "totalLines")
prompt_float("Accept Rate %", "acceptRate")
prompt_int("Active Members", "activeMembers")
prompt_int("Total Members", "totalMembers")

# ── Members leaderboard ───────────────────────────────────────────────────────
print("\n--- Member lines (from claude.ai/analytics/claude-code leaderboard) ---")
print("  Leave blank to skip updating members individually.")
print("  Or paste a JSON object like: {\"email@saras.com\": 1234, ...}")
members_raw = input("  Members JSON (or blank): ").strip()
if members_raw:
    try:
        new_members = json.loads(members_raw)
        stats["members"] = new_members
        print(f"  Updated {len(new_members)} member entries.")
    except json.JSONDecodeError as e:
        print(f"  [WARN] Could not parse members JSON: {e} — keeping existing values")

# ── Activity (from claude.ai/analytics/activity) ──────────────────────────────
print("\n--- Activity (from claude.ai/analytics/activity) ---")
prompt_int("WAU (weekly active users)", "wau")
prompt_float("Seat Utilization %", "utilization")
prompt_int("Pending Invites", "pendingInvites")

# ── Cowork (from claude.ai/analytics/cowork) ──────────────────────────────────
print("\n--- Cowork (from claude.ai/analytics/cowork) ---")
prompt_int("Sessions per day", "coworkSessionsPerDay")
prompt_int("Users with 1+ session %", "coworkUserPct")

# ── Claude.ai Usage (from claude.ai/analytics/usage) ──────────────────────────
print("\n--- Claude.ai Usage (from claude.ai/analytics/usage) ---")
prompt_int("Chats per day", "chatsPerDay")
prompt_int("Users with 1+ chat %", "chatUserPct")
prompt_int("Projects created MTD", "projectsCreated")
prompt_int("Users with 1+ project %", "projectUserPct")
prompt_int("Artifacts created MTD", "artifactsCreated")
prompt_int("Users with 1+ artifact %", "artifactUserPct")

# ── Write ─────────────────────────────────────────────────────────────────────
stats["asOf"] = today
STATS_PATH.write_text(json.dumps(stats, indent=2), encoding="utf-8")
print(f"\nSaved {STATS_PATH}")

# ── Commit & push ─────────────────────────────────────────────────────────────
push = input("\nCommit and push to GitHub? [Y/n]: ").strip().lower()
if push not in ("n", "no"):
    subprocess.run(["git", "add", "data/claude_ai_stats.json"], cwd=REPO_ROOT, check=True)
    subprocess.run(
        ["git", "commit", "-m", f"Update Claude.ai stats {today}"],
        cwd=REPO_ROOT, check=True
    )
    subprocess.run(["git", "push"], cwd=REPO_ROOT, check=True)
    print("Pushed! GitHub Actions will use fresh numbers on next run.")
else:
    print("Not pushed. Run `git push` manually when ready.")
