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
    BASE_DIR = Path("/home/site/wwwroot/energy-dashboard/energy-dashboard")
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

# ──────────────────────────────────────────────────────────────────────────────
# Frontend UI Configuration Management
# ──────────────────────────────────────────────────────────────────────────────
def load_scheduler_config() -> Dict[str, Any]:
    """Load scheduler configuration for the UI and Email Service."""
    if SCHEDULER_CONFIG_FILE.exists():
        with open(SCHEDULER_CONFIG_FILE, 'r') as f:
            return json.load(f)
    return {
        "to": "umang.mittal@maqsoftware.com",
        "cc": "",
        "subject": "Review Noida Daily Energy Optimization Dashboard",
        "auto_start": True
    }

def save_scheduler_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Save configuration updates triggered from the frontend."""
    SCHEDULER_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SCHEDULER_CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=4)
    return config

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

def stop_scheduler() -> Dict[str, Any]:
    if _scheduler:
        for job_id in [SCHEDULER_JOB_ID, "operator_reminder_9am_930am", "operator_reminder_10am", "suryalogix_scraper_job", "data_refresh_interval"]:
            if _scheduler.get_job(job_id):
                _scheduler.remove_job(job_id)
    cfg = load_scheduler_config()
    cfg["auto_start"] = False
    save_scheduler_config(cfg)
    return {"status": "stopped"}

# ──────────────────────────────────────────────────────────────────────────────
# Data Integrity (Ojas-Proof Excel Checks)
# ──────────────────────────────────────────────────────────────────────────────
def check_grid_diesel_entry_exists() -> bool:
    """Check if data exists for TODAY in the grid_and_diesel Excel file."""
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
        
        # 3. Check for today
        if not df[df["_parsed_date"] == today].empty:
            logger.info(f"[SCHEDULER DEBUG] SUCCESS! Found operator data for: {today}")
            return True
            
        # 4. If it fails, print the parsed dates to the Azure log
        top_3 = df["_parsed_date"].head(3).tolist()
        logger.error(f"[SCHEDULER DEBUG] Could not find {today} in Excel. Top 3 parsed dates are: {top_3}")
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
# REPLACE WITH THIS
def _run_master_data_engine() -> Dict[str, Any]:
    """Runs the Master Data merge in an isolated subprocess to prevent memory leaks."""
    try:
        IST = ZoneInfo("Asia/Kolkata")
        now = datetime.now(IST)
        operator_date = now.strftime("%Y-%m-%d")                        # TODAY   e.g. 2026-04-13
        solar_date    = (now - timedelta(days=1)).strftime("%Y-%m-%d")  # YESTERDAY e.g. 2026-04-12

        logger.info(f"Master data engine: operator_date={operator_date}, solar_date={solar_date}")

        backend_root = Path(__file__).parent.parent.parent
        subprocess.run(
            [sys.executable, "-m", "app.agents.master_data_engine", operator_date, solar_date],
            cwd=str(backend_root),
            capture_output=True,
            text=True,
            check=True
        )
        return {"status": "Success"}
    except subprocess.CalledProcessError as exc:
        logger.error(f"Master data engine subprocess failed: {exc.stderr}")
        return {"status": "Failed", "error": str(exc.stderr)}

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
        
        # Run it as a separate process
        result = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=True,
            text=True,
            check=True
        )
        
        logger.info(f"Scraper completed successfully. Output: {result.stdout[:200]}...")

    except subprocess.CalledProcessError as exc:
        from app.core.logger import logger
        logger.error(f"Scraper subprocess failed (Exit Code {exc.returncode})")
        logger.error(f"--- SCRAPER STDERR ---\n{exc.stderr}")
        logger.error(f"--- SCRAPER STDOUT ---\n{exc.stdout}")
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
    """The 10:30 AM Main Entry Point"""
    from app.core.logger import logger
    logger.info(f"Triggering daily report automation via {trigger_source}")
    from app.services.email_service import send_daily_report
    
    if check_grid_diesel_entry_exists():
        logger.info("Operator data found. Running Master Data Engine before sending report...")
        engine_result = _run_master_data_engine()
        
        if engine_result["status"] == "Success":
            # Send the normal report
            return send_daily_report(trigger_source=trigger_source, is_missing_data=False)
        else:
            return {"status": "Error", "notes": "Master Engine Failed"}
    else:
        # Operator forgot to submit data by 10:30 AM. 
        # Skip Master Engine and send the report with the warning flag enabled.
        logger.warning("Data missing at 10:30 AM! Sending fallback report with yesterday's data.")
        return send_daily_report(trigger_source="empty_fallback", is_missing_data=True)

def _run_operator_reminder_cycle():
    """Triggered at 9:00, 9:30, 10:00 to verify data presence before the deadline."""
    from app.core.logger import logger
    
    if not check_grid_diesel_entry_exists():
        logger.info("Grid data missing! Attempting to send reminder...")
        from app.services.email_service import send_operator_reminder
        
        # Capture the result so we can log it properly for Azure
        result = send_operator_reminder()
        
        if result.get("status") == "Success":
            logger.info(f"Reminder Email sent successfully: {result.get('notes')}")
        else:
            logger.error(f"Reminder Email FAILED: {result.get('error') or result.get('notes')}")
    else:
        logger.info("Grid data is already present. Skipping reminder.")

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
        
    _run_data_refresh()
    
    # 1. Start Scraper Clock (Runs every 30 mins)
    _scheduler.add_job(
        _run_solar_scraper,
        trigger=IntervalTrigger(minutes=30, timezone=ZoneInfo("Asia/Kolkata")),
        id="suryalogix_scraper_job",
        replace_existing=True,
        max_instances=1,
        coalesce=True    
    )
    
    # 2. Start Data Refresh Clock (Updates API Cache every 30 mins)
    _scheduler.add_job(
        _run_data_refresh,
        trigger=IntervalTrigger(minutes=30, timezone=ZoneInfo("Asia/Kolkata")),
        id="data_refresh_interval",
        replace_existing=True,
        max_instances=1,
        coalesce=True
    )

    # 3. Start Daily Email Clocks
    cfg = load_scheduler_config()
    if cfg.get("auto_start", False):
        _schedule_daily_job(cfg.get("send_time", DAILY_REPORT_CRON_TIME))