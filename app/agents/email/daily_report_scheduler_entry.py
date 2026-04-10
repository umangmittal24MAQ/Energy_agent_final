"""
Standalone entry point for OS-level schedulers (cron/Task Scheduler).
Run this script daily at 08:00 AM IST to execute the notification flow.
"""

import json
import sys
import time
from datetime import datetime, time as dt_time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services import scheduler_service

def main() -> None:
    attempt = 0
    final_result = {}
    
    # Define the absolute cutoff time (10:30 AM)
    cutoff_time = dt_time(10, 30)

    while True:
        attempt += 1
        now_time = datetime.now().time()

        # Check SharePoint Master Data
        check_result = scheduler_service.check_master_data_today_flag()
        FOUND = bool(check_result.get("found", False))
        date_checked = str(check_result.get("date_checked", ""))
        notes = str(check_result.get("notes", ""))

        if FOUND is True:
            # Data found! Send the final compiled daily report
            report_result = scheduler_service.send_daily_report_email_from_settings()
            action = "found_true_daily_report_sent"
            notes = f"{notes}; report_status={report_result.get('status', 'Unknown')}; attempt={attempt}"

            scheduler_service._log_daily_check_attempt(
                date_checked=date_checked,
                found=FOUND,
                action=action,
                trigger_source="os_scheduler_8am",
                notes=notes,
            )

            final_result = {
                "attempt": attempt,
                "found": FOUND,
                "action": action,
                "daily_report": report_result,
            }
            break

        else:
            # Data NOT found. Have we reached the 10:30 AM cutoff?
            # (or if the script was somehow delayed and ran 6 times = 3 hours)
            if now_time >= cutoff_time or attempt >= 6:
                # CUTOFF REACHED: Send the EMPTY report
                report_result = scheduler_service.send_daily_report_email_from_settings(empty_fallback=True)
                action = "cutoff_reached_empty_report_sent"
                notes = f"{notes}; cutoff_hit=True; report_status={report_result.get('status', 'Unknown')}; attempt={attempt}"

                scheduler_service._log_daily_check_attempt(
                    date_checked=date_checked,
                    found=FOUND,
                    action=action,
                    trigger_source="os_scheduler_8am",
                    notes=notes,
                )

                final_result = {
                    "attempt": attempt,
                    "found": False,
                    "action": action,
                    "daily_report": report_result,
                }
                break

            else:
                # Cutoff not reached. Send warning to operator and wait 30 minutes
                notify_result = scheduler_service.send_stakeholder_pending_notification()
                action = "found_false_notified_operator_waiting_30_minutes"
                notes = f"{notes}; notification_status={notify_result.get('status', 'Unknown')}; attempt={attempt}"

                scheduler_service._log_daily_check_attempt(
                    date_checked=date_checked,
                    found=FOUND,
                    action=action,
                    trigger_source="os_scheduler_8am",
                    notes=notes,
                )

                # Sleep for 30 minutes (1800 seconds)
                time.sleep(scheduler_service.RETRY_INTERVAL_MINUTES * 60)

    print(json.dumps(final_result))

if __name__ == "__main__":
    main()