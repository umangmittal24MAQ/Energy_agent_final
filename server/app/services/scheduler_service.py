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
    BASE_DIR = Path("/home/data/energy-dashboard")
else:
    # Local Path: Looks at the folder outside the 'app' directory
    BASE_DIR = Path(__file__).parent.parent.parent / "energy-dashboard"

BASE_DIR.mkdir(parents=True, exist_ok=True)
SCHEDULER_CONFIG_FILE = BASE_DIR / "scheduler_config.json"
SCHEDULER_LOG_FILE = BASE_DIR / "output" / "scheduler_log.json"

SCHEDULER_SLOT_JOB_PREFIX = "daily_slot_"
SCHEDULER_LEGACY_JOB_ID = "daily_energy_report"
SLOT_DEFAULT_START_TIME = "09:00"
SLOT_INTERVAL_MINUTES = 30
SLOT_COUNT = 4

DATA_STATUS_AVAILABLE = "data_available"
DATA_STATUS_MISSING = "missing_data"
DATA_STATUS_FETCH_FAILED = "fetch_failed"

_last_team_report_date: Optional[str] = None

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
            config = json.load(f)
    else:
        config = {
        "to": "umang.mittal@maqsoftware.com",
        "cc": "",
        "start_time": SLOT_DEFAULT_START_TIME,
        "subject": "Review Noida Daily Energy Optimization Dashboard",
        "auto_start": True
    }

    # Backward compatibility with older config key.
    if "start_time" not in config:
        config["start_time"] = config.get("send_time", SLOT_DEFAULT_START_TIME)

    config.setdefault("start_time", SLOT_DEFAULT_START_TIME)
    return config

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

    slot_jobs = [
        job
        for job in _scheduler.get_jobs()
        if job.id.startswith(SCHEDULER_SLOT_JOB_PREFIX)
    ]
    next_run = None
    if slot_jobs:
        next_run_dt = min(
            [job.next_run_time for job in slot_jobs if job.next_run_time],
            default=None,
        )
        next_run = next_run_dt.isoformat() if next_run_dt else None

    return {
        "status": "running" if slot_jobs else "stopped",
        "next_run": next_run,
    }

def start_scheduler(start_time: str = SLOT_DEFAULT_START_TIME) -> Dict[str, Any]:
    _schedule_daily_job(start_time)
    cfg = load_scheduler_config()
    cfg["start_time"] = start_time
    cfg["auto_start"] = True
    save_scheduler_config(cfg)
    return {"status": "running"}

def stop_scheduler() -> Dict[str, Any]:
    if _scheduler:
        for job in list(_scheduler.get_jobs()):
            if job.id in [
                SCHEDULER_LEGACY_JOB_ID,
                "suryalogix_scraper_job",
                "data_refresh_interval",
            ] or job.id.startswith(SCHEDULER_SLOT_JOB_PREFIX) or job.id.startswith("operator_reminder_"):
                _scheduler.remove_job(job.id)
    cfg = load_scheduler_config()
    cfg["auto_start"] = False
    save_scheduler_config(cfg)
    return {"status": "stopped"}

# ──────────────────────────────────────────────────────────────────────────────
# Data Integrity (Ojas-Proof Excel Checks)
# ──────────────────────────────────────────────────────────────────────────────
def check_grid_diesel_entry_status() -> str:
    """Check the status of today's Grid and Diesel data."""
    try:
        from app.services.sharepoint_data_service import get_service as get_excel_service
        
        sp_excel_service = get_excel_service()
        df = sp_excel_service.fetch_sheet_data("grid_and_diesel")
        
        if df is None:
            logger.error("[SCHEDULER] Could not fetch Grid and Diesel data from SharePoint.")
            return DATA_STATUS_FETCH_FAILED

        if df.empty:
            logger.error("[SCHEDULER] Excel file is empty or could not be loaded!")
            return DATA_STATUS_MISSING

        # --- THE HEADER HUNTER ---
        if any("Unnamed" in str(c) for c in df.columns):
            for i, row in df.head(10).iterrows():
                if any("date" in str(val).lower() for val in row.values):
                    df.columns = [str(c).strip() for c in row.values]
                    df = df.iloc[i+1:].reset_index(drop=True)
                    break
            
        IST = ZoneInfo("Asia/Kolkata")
        today = pd.Timestamp.now(tz=IST).date()
        
        date_col = next((c for c in df.columns if "date" in str(c).lower()), None)
        if not date_col:
            logger.error("[SCHEDULER] Could not locate a Date column in Grid and Diesel data.")
            return DATA_STATUS_FETCH_FAILED
            
        # Parse dates safely
        parsed_dates = pd.to_datetime(df[date_col], errors="coerce")
        if parsed_dates.isna().any():
            fallback_str = df[date_col].astype(str).str.strip()
            parsed_dates = parsed_dates.fillna(pd.to_datetime(fallback_str, format="%d-%b-%y", errors="coerce"))
            parsed_dates = parsed_dates.fillna(pd.to_datetime(fallback_str, errors="coerce", dayfirst=True))
            
        df["_parsed_date"] = parsed_dates.dt.date
        
        if not df[df["_parsed_date"] == today].empty:
            return DATA_STATUS_AVAILABLE

        return DATA_STATUS_MISSING
        
    except Exception as e:
        logger.error(f"Error checking grid_and_diesel: {e}")
        return DATA_STATUS_FETCH_FAILED

def check_grid_diesel_entry_exists() -> bool:
    """Backward-compatible boolean check for data existence."""
    return check_grid_diesel_entry_status() == DATA_STATUS_AVAILABLE

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
def _run_master_data_engine() -> Dict[str, Any]:
    """Runs the Master Data merge in an isolated subprocess to prevent memory leaks."""
    try:
        target_date_str = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d")
        logger.info(f"Master data engine running for date: {target_date_str}")
        
        backend_root = Path(__file__).parent.parent.parent
        subprocess.run(
            [sys.executable, "-m", "app.agents.master_data_engine", target_date_str],
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
    """Triggers Playwright API scraper every 30 minutes in the background."""
    try:
        logger.info("⏳ Starting 30-minute SuryaLogix Scraper job...")
        backend_root = Path(__file__).parent.parent.parent
        script_path = backend_root / "scrape_to_sharepoint.py"
        
        subprocess.run([sys.executable, str(script_path)], capture_output=True, text=True, check=True)
        logger.info("✅ Scraper completed successfully.")
    except Exception as exc:
        logger.error(f"❌ Scraper failed: {exc}")

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
    logger.info(f"Triggering daily report automation via {trigger_source}")
    from app.services.email_service import (
        send_daily_report,
        send_operator_reminder,
        send_data_fetch_failure_alert,
    )
    
    return _run_slot_check(trigger_source=trigger_source, is_final_slot=True)

def _parse_hhmm(value: str, default_value: str) -> tuple[int, int]:
    try:
        hh, mm = map(int, str(value).split(":"))
        return hh, mm
    except Exception:
        hh, mm = map(int, default_value.split(":"))
        return hh, mm

def _build_check_slots(start_time: str) -> list[tuple[int, int, bool]]:
    start_h, start_m = _parse_hhmm(start_time, SLOT_DEFAULT_START_TIME)

    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    current = datetime(today.year, today.month, today.day, start_h, start_m)

    slots: list[tuple[int, int, bool]] = []
    for i in range(SLOT_COUNT):
        slots.append((current.hour, current.minute, i == SLOT_COUNT - 1))
        current += timedelta(minutes=SLOT_INTERVAL_MINUTES)

    return slots


def _run_slot_check(trigger_source: str, is_final_slot: bool) -> Dict[str, Any]:
    """Runs one slot check. Sends reminder/report/fallback based on data availability."""
    global _last_team_report_date

    from app.services.email_service import (
        send_daily_report,
        send_operator_reminder,
        send_data_fetch_failure_alert,
    )

    now_ist = datetime.now(ZoneInfo("Asia/Kolkata"))
    today_str = now_ist.strftime("%Y-%m-%d")

    if _last_team_report_date == today_str:
        return {
            "status": "Skipped",
            "notes": "Team report already sent for today",
        }

    data_status = check_grid_diesel_entry_status()

    if data_status == DATA_STATUS_AVAILABLE:
        engine_result = _run_master_data_engine()
        if engine_result["status"] != "Success":
            return {"status": "Error", "notes": "Master Engine Failed"}

        result = send_daily_report(trigger_source=trigger_source)
        if result.get("status") == "Success":
            _last_team_report_date = today_str
        return result

    if data_status == DATA_STATUS_FETCH_FAILED:
        return send_data_fetch_failure_alert()

    if is_final_slot:
        prev_date = (now_ist - timedelta(days=1)).strftime("%Y-%m-%d")
        fallback_note = (
            f"Data for today is not updated. Showing daily energy report till {prev_date}."
        )
        result = send_daily_report(
            trigger_source=trigger_source,
            manual_date=prev_date,
            status_note=fallback_note,
        )
        if result.get("status") == "Success":
            _last_team_report_date = today_str
        return result

    return send_operator_reminder()


def _run_slot_job(slot_label: str, is_final_slot: bool) -> Dict[str, Any]:
    """APScheduler job wrapper for each slot."""
    trigger_source = f"scheduler_slot_{slot_label}"
    return _run_slot_check(trigger_source=trigger_source, is_final_slot=is_final_slot)

# ──────────────────────────────────────────────────────────────────────────────
# Scheduler Initialization
# ──────────────────────────────────────────────────────────────────────────────
def _schedule_daily_job(start_time: str) -> None:
    _ensure_scheduler_started()

    slots = _build_check_slots(start_time)

    for job in list(_scheduler.get_jobs()):
        if job.id.startswith(SCHEDULER_SLOT_JOB_PREFIX) or job.id.startswith("operator_reminder_") or job.id == SCHEDULER_LEGACY_JOB_ID:
            _scheduler.remove_job(job.id)

    for slot_hour, slot_minute, is_final_slot in slots:
        slot_label = f"{slot_hour:02d}{slot_minute:02d}"
        _scheduler.add_job(
            _run_slot_job,
            args=[slot_label, is_final_slot],
            trigger=CronTrigger(hour=slot_hour, minute=slot_minute, timezone=ZoneInfo("Asia/Kolkata")),
            id=f"{SCHEDULER_SLOT_JOB_PREFIX}{slot_label}",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
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
        _schedule_daily_job(cfg.get("start_time", cfg.get("send_time", SLOT_DEFAULT_START_TIME)))