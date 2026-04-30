import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv; load_dotenv()
from app.services.scheduler_service import (
    run_daily_report_automation,
    _run_operator_reminder_cycle,
    tracker_is_locked_for_today,
    _daily_report_tracker,
    SCHEDULER_LOG_FILE
)

# Reset state
_daily_report_tracker.clear()
if SCHEDULER_LOG_FILE.exists(): SCHEDULER_LOG_FILE.unlink()

print("=== Simulating 9:00 AM reminder cycle ===")
_run_operator_reminder_cycle()
print(f"Tracker locked: {tracker_is_locked_for_today()}")

print("\n=== Simulating 10:30 AM scheduler run ===")
result = run_daily_report_automation(trigger_source="scheduler")
print(f"Result: {result}")
print(f"Tracker locked: {tracker_is_locked_for_today()}")

print("\n=== Simulating second 10:30 AM run (should skip) ===")
result2 = run_daily_report_automation(trigger_source="scheduler")
print(f"Result: {result2}")
# Should return {"status": "Skipped", ...}