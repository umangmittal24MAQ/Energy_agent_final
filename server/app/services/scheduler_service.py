"""
Scheduler service - Modernized Architecture
Acts strictly as the Clock and Dispatcher for the Energy Dashboard.
"""
import os
import sys
import json
import logging
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pandas as pd

_daily_report_tracker: Dict[str, bool] = {}

# 🚀 FIX 1: Added missing tracker check for the Data Refresh Service
def tracker_is_locked_for_today() -> bool:
    """
    Allows external services (like data_refresh_service) to check 
    if the daily report has already been dispatched for today.
    """
    IST = ZoneInfo("Asia/Kolkata")
    today_str = datetime.now(IST).strftime("%Y-%m-%d")
    return _daily_report_tracker.get(today_str, False)


# Optional dependency - scheduler is not critical for data endpoints
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger
    HAS_SCHEDULER = True
except ImportError:
    HAS_SCHEDULER = False
    BackgroundScheduler = None
    CronTrigger = None
    IntervalTrigger = None

logger = logging.getLogger("app.services.scheduler_service")

# ──────────────────────────────────────────────────────────────────────────────
# Configuration & Paths
# ──────────────────────────────────────────────────────────────────────────────
if "WEBSITE_SITE_NAME" in os.environ:
    # Azure Path: Looks at the persistent storage
    BASE_DIR = Path("/home/site/wwwroot/energy-dashboard")
else:
    # Local Path: Looks at the folder outside the 'app' directory
    BASE_DIR = Path(__file__).parent.parent.parent / "energy-dashboard"

BASE_DIR.mkdir(parents=True, exist_ok=True)
SCHEDULER_CONFIG_FILE = BASE_DIR / "scheduler_config.json"
SCHEDULER_LOG_FILE = BASE_DIR / "output" / "scheduler_log.json"

SCHEDULER_JOB_ID = "daily_energy_report"
DAILY_REPORT_CRON_TIME = "10:30"

if HAS_SCHEDULER:
    _scheduler = BackgroundScheduler(timezone=ZoneInfo("Asia/Kolkata"))
else:
    _scheduler = None

def _ensure_scheduler_started() -> None:
    if _scheduler and not _scheduler.running:
        _scheduler.start()


def _split_emails(value: Any) -> list[str]:
    """Split comma/semicolon-separated email strings into clean tokens."""
    if value is None:
        return []
    if isinstance(value, list):
        raw_items = value
    else:
        raw_items = str(value).replace(";", ",").split(",")
    return [str(item).strip() for item in raw_items if str(item).strip()]


def _dedupe_emails(emails: list[str]) -> list[str]:
    """Deduplicate emails while preserving original order and casing."""
    unique: list[str] = []
    seen: set[str] = set()
    for email in emails:
        key = email.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(email)
    return unique


def _normalize_scheduler_recipients(config: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize configured recipients while preserving scheduler-config values only."""
    normalized = dict(config or {})

    merged_to = _dedupe_emails(_split_emails(normalized.get("to", "")))
    merged_cc = _dedupe_emails(_split_emails(normalized.get("cc", "")))

    to_keys = {email.lower() for email in merged_to}
    merged_cc = [email for email in merged_cc if email.lower() not in to_keys]

    normalized["to"] = ",".join(merged_to)
    normalized["cc"] = ",".join(merged_cc)
    return normalized

# ──────────────────────────────────────────────────────────────────────────────
# Frontend UI Configuration Management
# ──────────────────────────────────────────────────────────────────────────────
def load_scheduler_config() -> Dict[str, Any]:
    """Load scheduler configuration for the UI and Email Service."""
    if SCHEDULER_CONFIG_FILE.exists():
        with open(SCHEDULER_CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
    else:
        config = {
            "to": "umang.mittal@maqsoftware.com",
            "cc": "",
            "subject": "Review Noida Daily Energy Optimization Dashboard",
            "auto_start": True,
        }

    config.setdefault("subject", "Review Noida Daily Energy Optimization Dashboard")
    config.setdefault("auto_start", True)
    return _normalize_scheduler_recipients(config)

def save_scheduler_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Save configuration updates triggered from the frontend."""
    normalized_config = _normalize_scheduler_recipients(config)
    SCHEDULER_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SCHEDULER_CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(normalized_config, f, indent=4)
    return normalized_config

def get_scheduler_status() -> Dict[str, Any]:
    """Returns the live status of the clock to the frontend dashboard."""
    if not _scheduler:
        return {"status": "stopped", "next_run": None}
    job = _scheduler.get_job(SCHEDULER_JOB_ID)
    return {
        "status": "running" if job else "stopped",
        "next_run": job.next_run_time.isoformat() if job and job.next_run_time else None
    }

def start_scheduler(send_time: str = DAILY_REPORT_CRON_TIME) -> Dict[str, Any]:
    _schedule_daily_job(send_time)
    cfg = load_scheduler_config()
    cfg["auto_start"] = True
    save_scheduler_config(cfg)
    return {"status": "running"}

def stop_scheduler(disable_auto_start: bool = True) -> Dict[str, Any]:
    if _scheduler:
        for job in list(_scheduler.get_jobs()):
            if job.id in [
                SCHEDULER_JOB_ID,
                "suryalogix_scraper_job",
                "data_refresh_interval",
            ] or job.id.startswith("operator_reminder_"):
                _scheduler.remove_job(job.id)

    # Do not mutate persistent auto_start during process lifecycle shutdowns.
    if disable_auto_start:
        cfg = load_scheduler_config()
        cfg["auto_start"] = False
        save_scheduler_config(cfg)
    return {"status": "stopped"}

# ──────────────────────────────────────────────────────────────────────────────
# Data Integrity (Ojas-Proof Excel Checks)
# ──────────────────────────────────────────────────────────────────────────────
def _status_is_done(value: any) -> bool:
    """Fuzzy match Status column value against 'Done' (case-insensitive)."""
    if value is None:
        return False
    if pd.isna(value):
        return False
    text = str(value).strip().lower()
    return text == "done"


def check_grid_diesel_entry_exists() -> bool:
    """Check if data exists for TODAY in the grid_and_diesel Excel file AND Status='Done'."""
    from app.core.logger import logger
    try:
        from .sharepoint_data_service import get_service as get_excel_service
        import pandas as pd
        from zoneinfo import ZoneInfo
        
        sp_excel_service = get_excel_service()
        df = sp_excel_service.fetch_sheet_data("grid_and_diesel")
        
        if df is None or df.empty:
            logger.error("[SCHEDULER DEBUG] Excel file is empty or could not be loaded!")
            return False

        # --- THE HEADER HUNTER ---
        if any("Unnamed" in str(c) for c in df.columns):
            logger.warning("[SCHEDULER DEBUG] ⚠️ Detected 'Unnamed' columns. Hunting for the real headers...")
            for i, row in df.head(10).iterrows():
                if any("date" in str(val).lower() for val in row.values):
                    df.columns = row.values
                    df = df.iloc[i+1:].reset_index(drop=True)
                    logger.info(f"[SCHEDULER DEBUG] Found real headers on row {i+2} and fixed the table!")
                    break
        # -------------------------
            
        IST = ZoneInfo("Asia/Kolkata")
        today = pd.Timestamp.now(tz=IST).date()
        
        # 1. Dynamically find the date column
        date_col = next((c for c in df.columns if "date" in str(c).lower()), None)
        
        if not date_col:
            logger.error(f"[SCHEDULER DEBUG] CRITICAL: No date column found! I only see: {list(df.columns)}")
            return False
            
        logger.info(f"[SCHEDULER DEBUG] Found date column named: '{date_col}'")
            
        # 2. Parse dates SAFELY
        parsed_dates = pd.to_datetime(df[date_col], errors="coerce")
        
        if parsed_dates.isna().any():
            fallback_str = df[date_col].astype(str).str.strip()
            ojas_dates = pd.to_datetime(fallback_str, format="%d-%b-%y", errors="coerce")
            parsed_dates = parsed_dates.fillna(ojas_dates)
            general_dates = pd.to_datetime(fallback_str, errors="coerce", dayfirst=True)
            parsed_dates = parsed_dates.fillna(general_dates)
            
        df["_parsed_date"] = parsed_dates.dt.date
        today_rows = df[df["_parsed_date"] == today]
        if today_rows.empty:
            top_3 = df["_parsed_date"].head(3).tolist()
            logger.error(f"[SCHEDULER DEBUG] Could not find {today} in Excel. Top 3 parsed dates are: {top_3}")
            return False

        def _normalized(name: Any) -> str:
            return str(name).lower().replace(" ", "").replace("_", "").replace("\n", "")

        def _has_value(value: Any) -> bool:
            if value is None:
                return False
            if pd.isna(value):
                return False
            text = str(value).strip().lower()
            return text not in {"", "nan", "none", "null"}

        grid_units_col = next(
            (
                c for c in df.columns
                if "grid" in _normalized(c) and "unit" in _normalized(c)
            ),
            None,
        )

        if not grid_units_col:
            logger.error("[SCHEDULER] Could not locate Grid Units column in Grid and Diesel data.")
            return False

        if today_rows[grid_units_col].apply(_has_value).any():
            # NEW: Also check the Status column for "Done" (case-insensitive)
            status_col = next(
                (
                    c for c in df.columns
                    if "status" in str(c).lower()
                ),
                None,
            )
            
            if status_col:
                status_values = today_rows[status_col]
                if status_values.apply(_status_is_done).any():
                    logger.info(f"[SCHEDULER DEBUG] SUCCESS! Found operator data with Status='Done' for: {today}")
                    return True
                else:
                    logger.info(f"[SCHEDULER] Today's row exists with Grid Units, but Status is not 'Done'; treating as incomplete.")
                    return False
            else:
                logger.warning(f"[SCHEDULER] Status column not found; skipping Status check. Found Grid Units for {today}.")
                return True

        logger.info("[SCHEDULER] Today's row exists but Grid Units is blank; treating as missing data.")
        return False
        
    except Exception as e:
        from app.core.logger import logger
        logger.error(f"[SCHEDULER DEBUG] Crashed: {e}")
        return False
    
def build_energy_report_html(df: pd.DataFrame) -> str:
    """Builds the HTML table rows (<tr>) specifically for email_service.py."""
    rows_html = ""
    # Sort by date descending, grab up to 30 days
    if "_parsed_date" not in df.columns and "Date" in df.columns:
        df["_parsed_date"] = pd.to_datetime(df["Date"], errors="coerce").dt.date
        
    if "_parsed_date" in df.columns:
        df_sorted = df.sort_values("_parsed_date", ascending=False).head(30)
    else:
        df_sorted = df.tail(30)
    
    for _, row in df_sorted.iterrows():
        # Force Clean HH:MM
        raw_time = row.get("Time", "")
        try:
            clean_time = pd.to_datetime(raw_time).strftime("%H:%M") if raw_time else ""
        except Exception:
            clean_time = str(raw_time).strip()[:5]

        rows_html += f"""
        <tr>
            <td style="padding: 8px; border-bottom: 1px solid #ddd;">{row.get('Date', '')}</td>
            <td style="padding: 8px; border-bottom: 1px solid #ddd;">{row.get('Day', '')}</td>
            <td style="padding: 8px; border-bottom: 1px solid #ddd;">{clean_time}</td>
            <td style="padding: 8px; border-bottom: 1px solid #ddd; text-align: right;">{row.get('Grid Units Consumed (KWh)', 0)}</td>
            <td style="padding: 8px; border-bottom: 1px solid #ddd; text-align: right;">{row.get('Solar Units Consumed(KWh)', 0)}</td>
            <td style="padding: 8px; border-bottom: 1px solid #ddd; text-align: right;">{row.get('Total Units Consumed in INR', 0)}</td>
        </tr>
        """
    return rows_html

# ──────────────────────────────────────────────────────────────────────────────
# Core Automation Dispatches
# ──────────────────────────────────────────────────────────────────────────────
def _run_master_data_engine_once(
    operator_date: str,
    solar_date: str,
    fallback_operator_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Run one isolated master-data merge cycle."""
    backend_root = Path(__file__).parent.parent.parent
    command = [
        sys.executable,
        "-m",
        "app.agents.master_data_engine",
        operator_date,
        solar_date,
    ]
    if fallback_operator_date:
        command.append(fallback_operator_date)

    try:
        logger.info(
            "Master data engine run: operator_date=%s, solar_date=%s, fallback_operator_date=%s",
            operator_date,
            solar_date,
            fallback_operator_date,
        )
        result = subprocess.run(
            command,
            cwd=str(backend_root),
            capture_output=True,
            text=True,
            check=True,
        )
        if result.stdout:
            logger.info(result.stdout.strip())
        return {"status": "Success"}
    except subprocess.CalledProcessError as exc:
        logger.error(f"Master data engine subprocess failed: {exc.stderr}")
        return {"status": "Failed", "error": str(exc.stderr)}


def _run_master_data_engine() -> Dict[str, Any]:
    """Runs the Master Data merge in isolated subprocesses to prevent memory leaks."""
    IST = ZoneInfo("Asia/Kolkata")
    now = datetime.now(IST)
    operator_today = now.strftime("%Y-%m-%d")
    solar_yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    run_specs = [
        {
            "operator_date": operator_today,
            "solar_date": solar_yesterday,
            "fallback_operator_date": None,
        }
    ]

    if now.weekday() == 0:
        sunday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        # Monday catch-up: write one row for Sunday plus the regular Monday row.
        run_specs = [
            {
                "operator_date": sunday,
                "solar_date": sunday,
                "fallback_operator_date": operator_today,
            },
            {
                "operator_date": operator_today,
                "solar_date": solar_yesterday,
                "fallback_operator_date": None,
            },
        ]
        logger.info("Monday detected. Running two master-data writes (Sunday catch-up + Monday run).")

    failures = []
    for spec in run_specs:
        result = _run_master_data_engine_once(
            spec["operator_date"],
            spec["solar_date"],
            spec.get("fallback_operator_date"),
        )
        if result.get("status") != "Success":
            failures.append(
                {
                    "operator_date": spec["operator_date"],
                    "solar_date": spec["solar_date"],
                    "error": result.get("error", "Unknown failure"),
                }
            )

    if failures:
        return {"status": "Failed", "error": "Master Engine subprocess failure", "details": failures}

    return {"status": "Success", "runs": len(run_specs)}

def _run_solar_scraper() -> None:
    """Runs the SuryaLogix scraper every 30 minutes as a completely isolated subprocess."""
    try:
        import subprocess
        import sys
        from pathlib import Path
        from app.core.logger import logger
        
        logger.info("⏳ Starting 30-minute SuryaLogix Scraper job...")
        
        # Find the exact path to scrape_to_sharepoint.py
        backend_root = Path(__file__).parent.parent.parent
        script_path = backend_root / "scrape_to_sharepoint.py"
        
        # 🚀 FIX 2: Removed capture_output so logs stream in real-time
        subprocess.run(
            [sys.executable, str(script_path)],
            check=True
        )
        
        logger.info("✅ Scraper subprocess finished successfully.")

    except subprocess.CalledProcessError as exc:
        from app.core.logger import logger
        # Removed exc.stderr reference because it's no longer captured, it just prints to the main log
        logger.error(f"❌ Scraper subprocess failed (Exit Code {exc.returncode}). Check main terminal logs for details.")
    except Exception as exc:
        from app.core.logger import logger
        logger.error(f"Scraper completely failed to trigger: {exc}")


def _run_data_refresh() -> None:
    """Tells caching service to pull fresh stats for the UI Dashboard."""
    try:
        from app.services.data_refresh_service import DataRefreshService
        DataRefreshService.refresh_all_data()
    except Exception as e:
        logger.error(f"Error in data refresh task: {e}")

# ──────────────────────────────────────────────────────────────────────────────
# Time-Based Jobs
# ──────────────────────────────────────────────────────────────────────────────
def run_daily_report_automation(trigger_source: str = "scheduler") -> Dict[str, Any]:
    """The 10:30 AM Main Entry Point (The Deadline)"""
    from app.core.logger import logger
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from app.services.email_service import send_daily_report
    
    IST = ZoneInfo("Asia/Kolkata")
    today_str = datetime.now(IST).strftime("%Y-%m-%d")
    should_lock_tracker = trigger_source != "api_manual"

    # 1. Did Ojas upload early? Check the tracker.
    if should_lock_tracker and _daily_report_tracker.get(today_str, False):
        logger.info("10:30 AM Deadline reached, but the report was already sent early today. Skipping!")
        return {"status": "Skipped", "notes": "Report already sent today"}

    logger.info(f"Triggering daily report automation via {trigger_source}")
    
    if check_grid_diesel_entry_exists():
        logger.info("Operator data found at 10:30 AM. Running Master Engine before sending report...")
        engine_result = _run_master_data_engine()
        
        if engine_result["status"] == "Success":
            result = send_daily_report(trigger_source=trigger_source, is_missing_data=False)
            if should_lock_tracker:
                _daily_report_tracker[today_str] = True  # Lock the tracker
            return result
        else:
            logger.error("Master Engine failed. Sending fallback report from existing master data.")
            result = send_daily_report(trigger_source="engine_failed_fallback", is_missing_data=False)
            if should_lock_tracker:
                _daily_report_tracker[today_str] = True  # Lock the tracker
            return result
    else:
        # Operator forgot to submit data by 10:30 AM. 
        logger.warning("Data missing at 10:30 AM! Sending fallback report with yesterday's data.")
        result = send_daily_report(trigger_source="empty_fallback", is_missing_data=True)
        if should_lock_tracker:
            _daily_report_tracker[today_str] = True  # Lock the tracker
        return result

def _run_operator_reminder_cycle():
    """Triggered at 9:00, 9:30, 10:00 to verify data or send early report."""
    from app.core.logger import logger
    from datetime import datetime
    from zoneinfo import ZoneInfo
    
    IST = ZoneInfo("Asia/Kolkata")
    today_str = datetime.now(IST).strftime("%Y-%m-%d")

    # 1. If the report was already sent earlier this morning, stay quiet
    if _daily_report_tracker.get(today_str, False):
        logger.info("Report already sent today. Skipping reminder cycle.")
        return
        
    # 2. Check if the data is there
    if not check_grid_diesel_entry_exists():
        logger.info("Grid data missing! Attempting to send reminder...")
        from app.services.email_service import send_operator_reminder
        
        result = send_operator_reminder()
        if result.get("status") == "Success":
            logger.info(f"Reminder Email sent successfully: {result.get('notes')}")
        else:
            logger.error(f"Reminder Email FAILED: {result.get('error') or result.get('notes')}")
            
    else:
        # 3. THE UPGRADE: Data is here early! Bypass 10:30 AM and send NOW.
        logger.info("Grid data is PRESENT early! Bypassing 10:30 AM deadline and sending Report NOW.")
        
        engine_result = _run_master_data_engine()
        if engine_result["status"] == "Success":
            from app.services.email_service import send_daily_report
            send_result = send_daily_report(trigger_source="early_submission", is_missing_data=False)
            
            if send_result.get("status") == "Success":
                # Lock the tracker so subsequent reminders and 10:30 AM are skipped
                _daily_report_tracker[today_str] = True
                logger.info("✅ Early report sent successfully. Tracker locked for the day.")
            else:
                logger.error(f"Failed to send early report: {send_result.get('error')}")
        else:
            logger.error("Master Engine Failed during early submission.")

# ──────────────────────────────────────────────────────────────────────────────
# Scheduler Initialization
# ──────────────────────────────────────────────────────────────────────────────
def _schedule_daily_job(send_time: str) -> None:
    _ensure_scheduler_started()
    
    from datetime import datetime, timedelta
    
    # 1. Parse the starting time safely
    try:
        base_time = datetime.strptime(send_time, "%H:%M")
    except ValueError:
        # Fallback if frontend sends weird data
        base_time = datetime.strptime("09:00", "%H:%M")

    # 2. Calculate the dynamic +30 minute intervals
    cycle_1 = base_time                                # +0 mins
    cycle_2 = base_time + timedelta(minutes=30)        # +30 mins
    cycle_3 = base_time + timedelta(minutes=60)        # +60 mins
    final_cycle = base_time + timedelta(minutes=90)    # +90 mins (Final Report)

    # 3. Main Daily Report (Runs Monday through Saturday on the 4th cycle)
    _scheduler.add_job(
        run_daily_report_automation,
        trigger=CronTrigger(
            day_of_week='mon-sat', 
            hour=final_cycle.hour, 
            minute=final_cycle.minute, 
            timezone=ZoneInfo("Asia/Kolkata")
        ),
        id=SCHEDULER_JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True
    )

    # 4. Early Warning 1 (Runs on Cycle 1)
    _scheduler.add_job(
        _run_operator_reminder_cycle,
        trigger=CronTrigger(
            day_of_week='mon-sat', 
            hour=cycle_1.hour, 
            minute=cycle_1.minute, 
            timezone=ZoneInfo("Asia/Kolkata")
        ),
        id="operator_reminder_cycle_1",
        replace_existing=True,
        max_instances=1,
        coalesce=True
    )
    
    # 5. Early Warning 2 (Runs on Cycle 2)
    _scheduler.add_job(
        _run_operator_reminder_cycle,
        trigger=CronTrigger(
            day_of_week='mon-sat', 
            hour=cycle_2.hour, 
            minute=cycle_2.minute, 
            timezone=ZoneInfo("Asia/Kolkata")
        ),
        id="operator_reminder_cycle_2",
        replace_existing=True,
        max_instances=1,
        coalesce=True
    )

    # 6. Final Warning for Operator (Runs on Cycle 3)
    _scheduler.add_job(
        _run_operator_reminder_cycle,
        trigger=CronTrigger(
            day_of_week='mon-sat', 
            hour=cycle_3.hour, 
            minute=cycle_3.minute, 
            timezone=ZoneInfo("Asia/Kolkata")
        ),
        id="operator_reminder_cycle_3",
        replace_existing=True,
        max_instances=1,
        coalesce=True
    )

def initialize_scheduler_from_config() -> None:
    """Boot sequence triggered by main.py."""
    if not HAS_SCHEDULER:
        return

    _ensure_scheduler_started()
        
    _run_data_refresh()
    
    # 1. Start Scraper Clock (Runs every 30 mins between 06:00 and 19:30)
    _scheduler.add_job(
        _run_solar_scraper,
        trigger=CronTrigger(hour='6-19', minute='0,30', timezone=ZoneInfo("Asia/Kolkata")),
        id="suryalogix_scraper_job",
        replace_existing=True,
        max_instances=1,
        coalesce=True    
    )
    
    # 2. Start Data Refresh Clock (Updates API Cache every 30 mins between 06:00 and 19:30)
    _scheduler.add_job(
        _run_data_refresh,
        trigger=CronTrigger(hour='6-19', minute='0,30', timezone=ZoneInfo("Asia/Kolkata")),
        id="data_refresh_interval",
        replace_existing=True,
        max_instances=1,
        coalesce=True
    )

    # 3. Start Daily Email Clocks
    cfg = load_scheduler_config()
    if cfg.get("auto_start", False):
        _schedule_daily_job(
            cfg.get("start_time", cfg.get("send_time", DAILY_REPORT_CRON_TIME))
        )
