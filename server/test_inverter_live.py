"""
test_inverter_live.py
Full local test of the inverter status pipeline using real SharePoint credentials.
Run from repo root: python test_inverter_live.py
"""
import json
import logging
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()  # Load .env before any app imports

# Set up logging so you see all [INVERTER] log lines
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

from app.services.inverter_monitor import (
    _fetch_latest_inverter_statuses,
    run_inverter_monitor,
    get_inverter_status_summary,
    get_inverter_uptime_for_date,
    load_tracker,
)
from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
today_str = datetime.now(IST).strftime("%Y-%m-%d")

SEP = "\n" + "=" * 60 + "\n"

# ── TEST 1: SharePoint auth + raw data fetch ──────────────────────────────
print(SEP + "TEST 1 — Fetch latest inverter statuses from SharePoint")
try:
    statuses = _fetch_latest_inverter_statuses()
    if statuses:
        print("✅ SharePoint fetch succeeded:")
        for inv, status in statuses.items():
            print(f"   {inv}: {status}")
    else:
        print("⚠️  No data returned (nighttime / empty sheet / auth issue)")
except Exception as e:
    print(f"❌ Fetch failed: {e}")
    sys.exit(1)

# ── TEST 2: Full monitor run (writes tracker, sends alert if FAULT found) ─
print(SEP + "TEST 2 — run_inverter_monitor() — full 30-min tick")
run_inverter_monitor()
print("✅ Monitor run complete. Check energy-dashboard/output/inverter_tracker.json")

# ── TEST 3: Read back the tracker ─────────────────────────────────────────
print(SEP + "TEST 3 — Tracker contents after monitor run")
tracker = load_tracker()
day_data = tracker.get(today_str, {})
if day_data:
    print(f"✅ Tracker has data for {today_str}:")
    print(json.dumps(day_data, indent=2))
else:
    print(f"⚠️  No tracker data for {today_str} (all statuses may have been NaN)")

# ── TEST 4: get_inverter_status_summary() — used by API / dashboard ───────
print(SEP + "TEST 4 — get_inverter_status_summary()")
summary = get_inverter_status_summary()
print(json.dumps(summary, indent=2))
fault_count = summary["fault_count"]
tracker_found = summary["tracker_found"]
print(f"\n  tracker_found : {tracker_found}")
print(f"  fault_count   : {fault_count}")
if fault_count > 0:
    faulted = [k for k, v in summary["inverters"].items() if v["downtime_mins"] > 0]
    print(f"  ⚠️  FAULTs     : {faulted}")
else:
    print("  ✅ All inverters ACTIVE (no downtime recorded today)")

# ── TEST 5: get_inverter_uptime_for_date() — used by master_data_engine ──
print(SEP + "TEST 5 — get_inverter_uptime_for_date(today)")
uptime = get_inverter_uptime_for_date(today_str)
if uptime:
    print(f"✅ Uptime for {today_str}:")
    print(json.dumps(uptime, indent=2))
else:
    print(f"⚠️  No uptime data for {today_str}")

print(SEP + "All tests done.")


# ── FAULT SIMULATION TEST ─────────────────────────────────────────────────
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from app.services.inverter_monitor import load_tracker, save_tracker, _send_fault_alert

IST = ZoneInfo("Asia/Kolkata")
today_str = datetime.now(IST).strftime("%Y-%m-%d")

# Step 1: Manually inject a FAULT into the tracker
tracker = load_tracker()
tracker[today_str]["Inverter2"]["downtime_mins"] = 30
tracker[today_str]["Inverter2"]["uptime_mins"] = 30   # reduce uptime to match
save_tracker(tracker)
print("✅ Tracker patched — Inverter2 now has 30 mins downtime")

# Step 2: Fire the alert directly
today_data = tracker[today_str]
faulted = ["Inverter2"]

print("📧 Sending fault alert email...")
_send_fault_alert(faulted=faulted, today_data=today_data, date_str=today_str)
print("✅ Alert sent — check your inbox")