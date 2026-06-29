#!/usr/bin/env python3
"""
collect_monthly_spend.py — Build a per-month spend history (from Feb 2026) for
every app + the Anthropic API keys, so the Monthly Trends tab can show how
spend has moved month over month.

Two real, metered sources are queried once per calendar month:

  • Claude.ai / Claude Code model usage
        GET /v1/organizations/analytics/cost_report   (ANTHROPIC_ANALYTICS_KEY)
  • Anthropic platform API keys (pay-as-you-go)
        GET /v1/organizations/cost_report              (ANTHROPIC_ADMIN_KEY)

Both return one row per bucket with `amount` in (fractional) cents; we sum and
divide by 100 → USD, paginating via has_more/next_page exactly like the other
collectors.

Flat seat subscriptions (Cursor, Windsurf, Claude Enterprise seats) have no
usage API, so they are recorded at their current recurring monthly rate for
every month (clearly labelled as flat). The month "total" = subscriptions +
metered API-key spend (the real cash outflow). Claude.ai/Code model usage is
recorded as an informational column (it is consumption value already covered by
the Enterprise seat fee, so it is NOT double-counted into the total).

Anthropic's cost API retains a limited window, so months with no data are
marked available=false and the dashboard simply skips them.

Writes data/monthly_spend.json
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, date, timedelta
from pathlib import Path

REPO_ROOT   = Path(__file__).resolve().parent.parent
OUTPUT_PATH = REPO_ROOT / "data" / "monthly_spend.json"

ANALYTICS_KEY = os.environ.get("ANTHROPIC_ANALYTICS_KEY", "").strip()
ADMIN_KEY     = os.environ.get("ANTHROPIC_ADMIN_KEY", "").strip()

ANALYTICS_COST_URL = "https://api.anthropic.com/v1/organizations/analytics/cost_report"
ORG_COST_URL       = "https://api.anthropic.com/v1/organizations/cost_report"

# First month to report. Anthropic's cost API only retains a rolling window, so
# earlier months will simply come back empty and be marked unavailable.
START_YEAR, START_MONTH = 2026, 2

# Per-seat monthly rate by app. Seat cost shown = seat_count × rate.
PER_SEAT = {"claude": 20.0, "cursor": 20.0, "windsurf": 30.0}

# Current recurring monthly seat subscriptions (flat) — kept for the result
# metadata. Equals default seat count × PER_SEAT.
SUBSCRIPTIONS = {
    "claudeSeats": 3820.0,   # 191 × $20
    "cursor":      1260.0,   # 63 × $20
    "windsurf":     240.0,   # 8 × $30
}
# Current licensed seat counts (fallback for months without a Finance figure).
SEATS_DEFAULT_COUNT = {"claude": 191, "cursor": 63, "windsurf": 8}

# Per-month seat COUNTS from Finance, per app. The monthly seat cost shown is
# simply count × PER_SEAT[app]. Months/apps not listed use the current flat
# count above (Cursor has been a flat 63 throughout; its varying invoice
# amounts were usage, tracked separately in CURSOR_USAGE_HISTORY).
SEAT_COUNT_HISTORY = {
    "claude": {
        "2026-02":  59,
        "2026-03":  93,
        "2026-04": 117,
        "2026-05": 152,
    },
}

# Per-month Cursor metered API usage (USD), from Finance — the actual usage
# billed that month, shown in full (NOT divided) and rolled into the month
# total, consistent with how Claude.ai/Code usage is shown.
CURSOR_USAGE_HISTORY = {
    "2026-02":  4206.08,
    "2026-03": 11755.06,
    "2026-04":  5007.73,
    # 2026-05: no Cursor API usage
}


def resolve_seat(app, month_key):
    """Return (monthly_usd, seat_count) for an app in a given month. Seat cost is
    simply seat_count × PER_SEAT[app]; the count comes from the Finance history
    when present, else the current flat count."""
    count = SEAT_COUNT_HISTORY.get(app, {}).get(month_key, SEATS_DEFAULT_COUNT[app])
    return round(count * PER_SEAT[app], 2), count


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def get_json(url, headers, timeout=60):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _qs(params):
    parts = []
    for k, v in params.items():
        if isinstance(v, list):
            for item in v:
                parts.append(f"{urllib.request.quote(str(k))}[]={urllib.request.quote(str(item))}")
        else:
            parts.append(f"{urllib.request.quote(str(k))}={urllib.request.quote(str(v))}")
    return "&".join(parts)


def fetch_cost(base_url, headers, start_iso, end_iso, group_by="description"):
    """Sum cost_report amount (cents → USD) over [start, end). Returns (usd, ok).
    ok=False means the call failed outright (vs a genuine $0 month).

    group_by differs per endpoint: the org cost_report accepts "description",
    but the analytics/cost_report endpoint only accepts "model" (passing
    "description" there 400s and silently yields $0)."""
    params = {
        "starting_at":  start_iso,
        "ending_at":    end_iso,
        "bucket_width": "1d",
        "group_by":     [group_by],
    }
    total_cents = 0.0
    try:
        url = f"{base_url}?{_qs(params)}"
        r = get_json(url, headers)
        while True:
            for bucket in r.get("data", []):
                for item in bucket.get("results", []):
                    total_cents += float(item.get("amount", 0) or 0)
            if not r.get("has_more"):
                break
            params["page"] = r.get("next_page")
            r = get_json(f"{base_url}?{_qs(params)}", headers)
            time.sleep(0.2)
        return round(total_cents / 100.0, 2), True
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        log(f"    HTTP {e.code} {e.reason}: {body}")
        return 0.0, False
    except Exception as e:
        log(f"    [WARN] cost fetch failed: {e}")
        return 0.0, False


def month_iter(start_y, start_m, end_y, end_m):
    y, m = start_y, start_m
    while (y, m) <= (end_y, end_m):
        yield y, m
        m += 1
        if m > 12:
            m, y = 1, y + 1


def main():
    today = datetime.now(timezone.utc).date()
    analytics_headers = {"x-api-key": ANALYTICS_KEY, "anthropic-version": "2023-06-01"}
    org_headers       = {"x-api-key": ADMIN_KEY,     "anthropic-version": "2023-06-01"}

    log(f"Collecting monthly spend history {START_YEAR}-{START_MONTH:02d} → {today.year}-{today.month:02d}")

    months = []
    for y, m in month_iter(START_YEAR, START_MONTH, today.year, today.month):
        month_start = date(y, m, 1)
        nxt = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
        # For the current month, cap the window at tomorrow (today + 1, exclusive)
        # so we get MTD only. A future ending_at (the 1st of next month) makes the
        # cost_report return empty for the in-progress month → a spurious $0.
        is_current = (y == today.year and m == today.month)
        end_d = (today + timedelta(days=1)) if is_current else nxt
        start_iso = month_start.strftime("%Y-%m-%dT00:00:00Z")
        end_iso   = end_d.strftime("%Y-%m-%dT00:00:00Z")
        label = month_start.strftime("%b %Y")

        claude_usage, ca_ok = (0.0, False)
        if ANALYTICS_KEY:
            claude_usage, ca_ok = fetch_cost(ANALYTICS_COST_URL, analytics_headers, start_iso, end_iso, group_by="model")
        api_keys, ak_ok = (0.0, False)
        if ADMIN_KEY:
            api_keys, ak_ok = fetch_cost(ORG_COST_URL, org_headers, start_iso, end_iso, group_by="description")

        month_key = f"{y}-{m:02d}"

        month_key_in_history = any(month_key in SEAT_HISTORY[a] for a in SEAT_HISTORY)

        # A month counts as "available" if at least one metered source returned
        # data (>$0) OR both calls succeeded (a genuine $0 month inside the
        # retention window). Months entirely outside the window fail both calls.
        # We also force-include any month with a known Finance seat figure.
        has_data = (claude_usage > 0) or (api_keys > 0)
        available = has_data or (ca_ok and ak_ok) or month_key_in_history

        # Per-month seat cost (monthly run-rate = annual ÷ 12) + seat count, per app.
        claude_seats_monthly, claude_seat_count   = resolve_seat("claude",  month_key)
        cursor_monthly,       cursor_seat_count   = resolve_seat("cursor",  month_key)
        windsurf_monthly,     windsurf_seat_count = resolve_seat("windsurf", month_key)

        # Cursor metered usage — actual amount billed that month, shown in full
        # (consistent with Claude.ai/Code usage, which is also a full monthly figure).
        cursor_usage = round(CURSOR_USAGE_HISTORY.get(month_key, 0.0), 2)

        # Real all-in monthly cash outflow: seat subscriptions + every metered
        # usage line (Cursor usage, Anthropic API keys, Claude.ai/Code usage).
        total = round(claude_seats_monthly + cursor_monthly + windsurf_monthly
                      + cursor_usage + api_keys + claude_usage, 2)

        months.append({
            "monthKey":          month_key,
            "label":             label,
            "isCurrent":         is_current,
            "available":         available,
            "claudeUsage":       claude_usage,          # metered Claude.ai/Code usage (live, monthly)
            "apiKeys":           api_keys,              # real metered pay-as-you-go API keys
            "claudeSeats":       claude_seats_monthly,  # monthly run-rate (annual ÷ 12)
            "claudeSeatCount":   claude_seat_count,
            "cursor":            cursor_monthly,
            "cursorSeatCount":   cursor_seat_count,
            "cursorUsage":       cursor_usage,          # Cursor metered usage (annual ÷ 12)
            "windsurf":          windsurf_monthly,
            "windsurfSeatCount": windsurf_seat_count,
            "total":             total,
        })
        log(f"  {label}: available={available} "
            f"claude={claude_seat_count}seats/${claude_seats_monthly:.0f} "
            f"cursor={cursor_seat_count}seats/${cursor_monthly:.0f} "
            f"cursorUsage=${cursor_usage:.2f} claudeUsage=${claude_usage:.2f} "
            f"apiKeys=${api_keys:.2f} total=${total:.2f}")
        time.sleep(0.2)

    result = {
        "asOf":          today.strftime("%Y-%m-%d"),
        "subscriptions": SUBSCRIPTIONS,
        "months":        months,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(result, indent=2), encoding="utf-8")
    avail = sum(1 for x in months if x["available"])
    log(f"\nWrote {OUTPUT_PATH}  ({avail}/{len(months)} months with data)")


if __name__ == "__main__":
    main()
