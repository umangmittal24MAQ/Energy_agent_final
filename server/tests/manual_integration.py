import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# Add to manual_integration.py temporarily to test
from dotenv import load_dotenv; load_dotenv()
from app.services.scheduler_service import (
    _run_late_data_check,
    _daily_report_tracker,
    _late_engine_ran_today,
    SCHEDULER_LOG_FILE
)
from datetime import datetime
from zoneinfo import ZoneInfo

today_str = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d")

# Simulate: tracker is locked (report already sent), data now arrives late
_daily_report_tracker[today_str] = True

print("=== Late data check (first run) ===")
_run_late_data_check()
print(f"Late engine ran: {_late_engine_ran_today.get(today_str, False)}")

print("\n=== Late data check (second run - should skip) ===")
_run_late_data_check()
print(f"Late engine ran: {_late_engine_ran_today.get(today_str, False)}")