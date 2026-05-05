"""
Scheduler service - Modernized Architecture
Acts strictly as the Clock and Dispatcher for the Energy Dashboard.
"""
import os
import sys
import json
import logging
import subprocess
import threading
from concurrent.futures import ProcessPoolExecutor, TimeoutError as FuturesTimeoutError
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import pandas as pd

# FIX R4: Use a threading.Lock() to guard in-memory tracker dicts.
# APScheduler runs jobs on daemon threads. Plain dict reads/writes are not
# atomic for compound operations — two threads can both pass the "not in dict"
# check and both fire the report. The lock makes those operations safe.
_tracker_lock = threading.Lock()
_daily_report_tracker: Dict[str, bool] = {}
# NOTE: _late_engine_ran_today intentionally removed — see _run_late_data_check.


# ──────────────────────────────────────────────────────────────────────────────
# FIX C4: Split scheduler_log.json into two separate files with clear purposes.
#
# The original code had two services writing to the SAME file in INCOMPATIBLE
# formats:
#   - email_service._append_scheduler_send_history() wrote a JSON *list*
#   - scheduler_service._save_tracker_to_log() wrote a JSON *dict*
#
# Each time one ran after the other it overwrote the other's format. The
# scheduler_history endpoint (expecting a list) and _load_tracker_from_log()
# (expecting a dict) would silently return empty data or throw exceptions.
#
# Fix: Separate the two concerns into two files:
#   SCHEDULER_LOG_FILE    → scheduler_log.json   (list of history entries — owned by email_service)
#   SCHEDULER_TRACKER_FILE → scheduler_tracker.json (dict of "sent today" flags — owned here)
# ──────────────────────────────────────────────────────────────────────────────

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
#
# FIX PATH: Separate code dir (wiped on redeploy) from persistent state dir.
# scheduler_tracker.json and lock files MUST survive restarts — if lost, the
# late-data check gate (tracker_is_locked_for_today) always returns False and
# _run_late_data_check silently skips every tick for the rest of the day.
#
# /home/site/wwwroot/  → deployment target, gets overwritten on every deploy
# /home/LogFiles/      → Azure persistent storage, survives deploys & restarts
# ──────────────────────────────────────────────────────────────────────────────
if "WEBSITE_SITE_NAME" in os.environ:
    BASE_DIR    = Path("/home/site/wwwroot/energy-dashboard")   # code/config — ok to wipe
    PERSIST_DIR = Path("/home/LogFiles/energy-dashboard")        # state — must survive restarts
else:
    BASE_DIR    = Path(__file__).parent.parent.parent / "energy-dashboard"
    PERSIST_DIR = BASE_DIR

BASE_DIR.mkdir(parents=True, exist_ok=True)
PERSIST_DIR.mkdir(parents=True, exist_ok=True)
(PERSIST_DIR / "output").mkdir(parents=True, exist_ok=True)

SCHEDULER_CONFIG_FILE  = BASE_DIR    / "scheduler_config.json"       # config — ok to reset on deploy
SCHEDULER_LOG_FILE     = PERSIST_DIR / "output" / "scheduler_log.json"      # must survive restarts
SCHEDULER_TRACKER_FILE = PERSIST_DIR / "output" / "scheduler_tracker.json"  # must survive restarts
SCHEDULER_LOCK_DIR     = PERSIST_DIR / ".scheduler_locks"                    # must survive restarts
SCHEDULER_LOCK_DIR.mkdir(parents=True, exist_ok=True)

SCHEDULER_JOB_ID = "daily_energy_report"
DAILY_REPORT_CRON_TIME = "10:30"

if HAS_SCHEDULER:
    _scheduler = BackgroundScheduler(timezone=ZoneInfo("Asia/Kolkata"))
else:
    _scheduler = None


# ──────────────────────────────────────────────────────────────────────────────
# Persistent Tracker Functions (Survives App Restarts)
# ──────────────────────────────────────────────────────────────────────────────
def _load_tracker_from_log() -> Dict[str, bool]:
    """
    Load today's tracker state from the persistent scheduler_tracker.json file.
    This survives app restarts, slot swaps, and worker recycling.
    Returns a dict like {"2024-04-29": True} if report was already sent today.

    FIX C4: Now reads from SCHEDULER_TRACKER_FILE (dict format), not SCHEDULER_LOG_FILE
    (which is a list of history entries owned by email_service). Previously both
    services wrote to the same file in incompatible formats, corrupting each other.
    """
    if not SCHEDULER_TRACKER_FILE.exists():
        return {}

    try:
        with open(SCHEDULER_TRACKER_FILE, 'r', encoding='utf-8') as f:
            raw = json.load(f)
            log_data = raw if isinstance(raw, dict) else {}
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"Error reading scheduler tracker file: {e}")
        return {}

    IST = ZoneInfo("Asia/Kolkata")
    today_str = datetime.now(IST).strftime("%Y-%m-%d")

    if today_str in log_data and log_data[today_str].get("status") == "Sent":
        return {today_str: True}

    return {}


def _save_tracker_to_log(today_str: str, trigger_source: str = "scheduler") -> None:
    """
    Persist today's report sent flag to scheduler_tracker.json.
    Survives app restarts, slot swaps, and worker recycling.

    FIX C4: Writes to SCHEDULER_TRACKER_FILE (separate from the history log).
    """
    IST = ZoneInfo("Asia/Kolkata")
    now = datetime.now(IST)

    log_data: Dict[str, Any] = {}
    if SCHEDULER_TRACKER_FILE.exists():
        try:
            with open(SCHEDULER_TRACKER_FILE, 'r', encoding='utf-8') as f:
                raw = json.load(f)
                if isinstance(raw, dict):
                    log_data = raw
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Error reading existing scheduler tracker: {e}")

    log_data[today_str] = {
        "status": "Sent",
        "timestamp": now.isoformat(),
        "trigger_source": trigger_source,
    }
    try:
        SCHEDULER_TRACKER_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(SCHEDULER_TRACKER_FILE, 'w', encoding='utf-8') as f:
            json.dump(log_data, f, indent=4)
        logger.info(f"✅ Persisted report flag to disk for {today_str}")
    except OSError as e:
        logger.error(f"Error saving scheduler tracker file: {e}")


def tracker_is_locked_for_today() -> bool:
    """
    Check if the daily report has already been dispatched for today.
    Checks BOTH in-memory tracker AND persistent scheduler_tracker.json.

    FIX R4: All reads/writes to _daily_report_tracker are now under _tracker_lock
    to prevent race conditions between APScheduler's daemon threads.
    """
    IST = ZoneInfo("Asia/Kolkata")
    today_str = datetime.now(IST).strftime("%Y-%m-%d")

    with _tracker_lock:
        if _daily_report_tracker.get(today_str, False):
            return True

    persistent_tracker = _load_tracker_from_log()
    if persistent_tracker.get(today_str, False):
        with _tracker_lock:
            _daily_report_tracker[today_str] = True
        return True

    return False


def _lock_tracker_for_today(today_str: str) -> None:
    """Thread-safe helper to set the in-memory tracker flag."""
    with _tracker_lock:
        _daily_report_tracker[today_str] = True


# ──────────────────────────────────────────────────────────────────────────────
# Distributed Lock Management (Survives Slot Swaps)
# ──────────────────────────────────────────────────────────────────────────────
def _get_lock_file_path(job_name: str) -> Path:
    return SCHEDULER_LOCK_DIR / f"{job_name}.lock"


def _acquire_distributed_lock(job_name: str, ttl_seconds: int = 300) -> bool:
    """
    Attempt to acquire a distributed lock using atomic file creation.
    Returns True if lock was acquired, False if already held by another instance.
    """
    lock_file = _get_lock_file_path(job_name)

    try:
        with open(lock_file, 'x') as f:
            f.write(f"Locked at {datetime.now(ZoneInfo('Asia/Kolkata')).isoformat()}\n")
        logger.info(f"🔒 Acquired distributed lock for job: {job_name}")
        return True

    except FileExistsError:
        try:
            if lock_file.exists():
                # FIX R3: Use timezone-aware UTC datetimes on both sides of the subtraction.
                mtime_utc = datetime.fromtimestamp(lock_file.stat().st_mtime, tz=timezone.utc)
                now_utc = datetime.now(timezone.utc)
                lock_age = (now_utc - mtime_utc).total_seconds()

                if lock_age > ttl_seconds:
                    logger.warning(f"⚠️ Stale lock detected for {job_name} (age: {lock_age:.1f}s). Removing.")
                    lock_file.unlink(missing_ok=True)
                    return _acquire_distributed_lock(job_name, ttl_seconds)
                else:
                    logger.info(f"⏭️ Skipping job {job_name} (lock held by another instance, age: {lock_age:.1f}s)")
        except Exception as e:
            logger.warning(f"Error checking lock age: {e}")
        return False

    except Exception as e:
        logger.error(f"Error acquiring distributed lock for {job_name}: {e}")
        return False


def _release_distributed_lock(job_name: str) -> None:
    lock_file = _get_lock_file_path(job_name)
    try:
        lock_file.unlink(missing_ok=True)
        logger.info(f"🔓 Released distributed lock for job: {job_name}")
    except Exception as e:
        logger.error(f"Error releasing lock for {job_name}: {e}")


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
        try:
            with open(SCHEDULER_CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
        except json.JSONDecodeError as e:
            logger.error(f"scheduler_config.json is corrupt ({e}). Falling back to defaults.")
            config = {}
        except OSError as e:
            logger.error(f"Could not read scheduler_config.json: {e}. Using defaults.")
            config = {}
    else:
        config = {
            "to": "",
            "cc": "",
            "subject": "Review Noida Daily Energy Optimization Dashboard",
            "auto_start": True,
        }

    config.setdefault("subject", "Review Noida Daily Energy Optimization Dashboard")
    config.setdefault("auto_start", True)
    return _normalize_scheduler_recipients(config)


import tempfile, shutil

def save_scheduler_config(config: Dict[str, Any]) -> Dict[str, Any]:
    normalized_config = _normalize_scheduler_recipients(config)
    SCHEDULER_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=SCHEDULER_CONFIG_FILE.parent, suffix=".tmp"
    )
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(normalized_config, f, indent=4)
        shutil.move(tmp_path, SCHEDULER_CONFIG_FILE)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return normalized_config


def get_scheduler_status() -> Dict[str, Any]:
    """Returns the live status of the clock to the frontend dashboard."""
    if not _scheduler:
        return {"status": "stopped", "next_run": None}
    job = _scheduler.get_job(SCHEDULER_JOB_ID)
    return {
        "status": "running" if job else "stopped",
        "next_run": job.next_run_time.isoformat() if job and job.next_run_time else None,
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

    if disable_auto_start:
        cfg = load_scheduler_config()
        cfg["auto_start"] = False
        save_scheduler_config(cfg)
    return {"status": "stopped"}


# ──────────────────────────────────────────────────────────────────────────────
# Data Integrity (Ojas-Proof Excel Checks)
# ──────────────────────────────────────────────────────────────────────────────
def _status_is_done(value: Any) -> bool:
    """Fuzzy match Status column value against 'Done' (case-insensitive)."""
    if value is None:
        return False
    if pd.isna(value):
        return False
    text = str(value).strip().lower()
    return text == "done"


def check_grid_diesel_entry_exists() -> bool:
    """Check if data exists for TODAY in the grid_and_diesel Excel file AND Status='Done'."""
    try:
        from .sharepoint_data_service import get_service as get_excel_service

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

        IST = ZoneInfo("Asia/Kolkata")
        today = pd.Timestamp.now(tz=IST).date()

        date_col = next((c for c in df.columns if "date" in str(c).lower()), None)
        if not date_col:
            logger.error(f"[SCHEDULER DEBUG] CRITICAL: No date column found! I only see: {list(df.columns)}")
            return False

        logger.info(f"[SCHEDULER DEBUG] Found date column named: '{date_col}'")

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
            (c for c in df.columns if "grid" in _normalized(c) and "unit" in _normalized(c)),
            None,
        )
        if not grid_units_col:
            logger.error("[SCHEDULER] Could not locate Grid Units column in Grid and Diesel data.")
            return False

        if today_rows[grid_units_col].apply(_has_value).any():
            status_col = next(
                (c for c in df.columns if "status" in str(c).lower()),
                None,
            )
            if status_col:
                status_values = today_rows[status_col]
                if status_values.apply(_status_is_done).any():
                    logger.info(f"[SCHEDULER DEBUG] SUCCESS! Found operator data with Status='Done' for: {today}")
                    return True
                else:
                    logger.info("[SCHEDULER] Today's row exists with Grid Units, but Status is not 'Done'; treating as incomplete.")
                    return False
            else:
                logger.warning(f"[SCHEDULER] Status column not found; skipping Status check. Found Grid Units for {today}.")
                return True

        logger.info("[SCHEDULER] Today's row exists but Grid Units is blank; treating as missing data.")
        return False

    except Exception as e:
        logger.error(f"[SCHEDULER DEBUG] Crashed: {e}")
        return False


def build_energy_report_html(df: pd.DataFrame) -> str:
    """Builds the HTML table rows (<tr>) specifically for email_service.py."""
    import html as html_lib

    rows_html = ""
    if "_parsed_date" not in df.columns and "Date" in df.columns:
        df["_parsed_date"] = pd.to_datetime(df["Date"], errors="coerce").dt.date

    if "_parsed_date" in df.columns:
        df_sorted = df.sort_values("_parsed_date", ascending=False).head(30)
    else:
        df_sorted = df.tail(30)

    for _, row in df_sorted.iterrows():
        raw_time = row.get("Time", "")
        try:
            clean_time = pd.to_datetime(raw_time).strftime("%H:%M") if raw_time else ""
        except Exception:
            clean_time = str(raw_time).strip()[:5]

        def _e(v: Any) -> str:
            return html_lib.escape(str(v)) if v is not None else ""

        rows_html += f"""
        <tr>
            <td style="padding: 8px; border-bottom: 1px solid #ddd;">{_e(row.get('Date', ''))}</td>
            <td style="padding: 8px; border-bottom: 1px solid #ddd;">{_e(row.get('Day', ''))}</td>
            <td style="padding: 8px; border-bottom: 1px solid #ddd;">{_e(clean_time)}</td>
            <td style="padding: 8px; border-bottom: 1px solid #ddd; text-align: right;">{_e(row.get('Grid Units Consumed (KWh)', 0))}</td>
            <td style="padding: 8px; border-bottom: 1px solid #ddd; text-align: right;">{_e(row.get('Solar Units Consumed(KWh)', 0))}</td>
            <td style="padding: 8px; border-bottom: 1px solid #ddd; text-align: right;">{_e(row.get('Total Units Consumed in INR', 0))}</td>
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
    """Run one isolated master-data merge cycle in a separate process."""
    from app.agents.master_data_engine import process_master_data

    logger.info(
        "Master data engine run: operator_date=%s, solar_date=%s, fallback_operator_date=%s",
        operator_date,
        solar_date,
        fallback_operator_date,
    )
    try:
        with ProcessPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                process_master_data,
                operator_date,
                solar_date,
                fallback_operator_date,
            )
            future.result(timeout=90)
        return {"status": "Success"}
    except FuturesTimeoutError:
        logger.error("Master data engine timed out after 90s")
        return {"status": "Error", "message": "Engine timeout"}
    except Exception as exc:
        logger.error(f"Master data engine failed: {exc}", exc_info=True)
        return {"status": "Failed", "error": str(exc)}


def _run_master_data_engine() -> Dict[str, Any]:
    """
    Runs the Master Data merge in isolated subprocesses to prevent memory leaks.

    UNIVERSAL SOLAR DATE RULE:
    The operator always records yesterday's consumption labelled as today's date.
    Therefore solar_date = operator_date - 1 day, for every run without exception.

    Monday has two runs because the operator enters two rows:
      "Sunday" row  → operator_date=sunday,  solar_date=saturday  (scraper ran Saturday)
      "Monday" row  → operator_date=monday,  solar_date=sunday    (scraper ran Sunday)
    """
    IST = ZoneInfo("Asia/Kolkata")
    now = datetime.now(IST)
    operator_today = now.strftime("%Y-%m-%d")
    solar_yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    run_specs = [
        {
            "operator_date": operator_today,
            "solar_date": solar_yesterday,       # universal rule: solar = operator_date - 1
            "fallback_operator_date": None,
        }
    ]

    if now.weekday() == 0:  # Monday
        sunday   = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        saturday = (now - timedelta(days=2)).strftime("%Y-%m-%d")
        run_specs = [
            {
                # "Sunday" row: operator recorded Saturday's consumption as Sunday
                "operator_date": sunday,
                "solar_date": saturday,          # scraper ran on Saturday
                "fallback_operator_date": operator_today,
            },
            {
                # "Monday" row: operator recorded Sunday's consumption as Monday
                "operator_date": operator_today,
                "solar_date": sunday,            # scraper ran on Sunday
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
        logger.info("⏳ Starting 30-minute SuryaLogix Scraper job...")

        backend_root = Path(__file__).parent.parent.parent
        script_path = backend_root / "scrape_to_sharepoint.py"

        subprocess.run(
            [sys.executable, str(script_path)],
            check=True,
            timeout=300,
        )
        logger.info("Scraper subprocess finished successfully.")

    except subprocess.TimeoutExpired:
        logger.error("Scraper subprocess timed out after 300s and was killed.")
    except subprocess.CalledProcessError as exc:
        logger.error(f"Scraper subprocess failed (Exit Code {exc.returncode}).")
    except Exception as exc:
        logger.error(f"Scraper completely failed to trigger: {exc}")


def _run_data_refresh() -> None:
    """Tells caching service to pull fresh stats for the UI Dashboard."""
    try:
        from app.services.data_refresh_service import DataRefreshService
        DataRefreshService.refresh_all_data()
    except Exception as e:
        logger.error(f"Error in data refresh task: {e}")


def _run_late_data_check() -> None:
    """
    Runs every 30 min (10:30-19:30) after the report deadline.
    If operator submits or corrects data late, silently re-runs the master
    engine so the data is correct. No email is sent.

    KEY BEHAVIOURS:
    - Runs every tick as long as Status=Done — supports corrections made after
      the first late submission. The Status=Done gate is the only guard needed.
    - _late_engine_ran_today flag intentionally REMOVED — it was blocking
      corrections from being picked up for the rest of the day.
    - Skips silently if the tracker is not locked (report not sent yet today,
      or tracker file was lost on restart — log line makes this visible).
    - Monday: processes both Sunday and Monday rows with correct solar dates,
      matching the universal rule (solar_date = operator_date - 1).
    """
    IST = ZoneInfo("Asia/Kolkata")
    now = datetime.now(IST)
    today_str     = now.strftime("%Y-%m-%d")
    yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    # Gate 1: only run after the report has already been sent today
    if not tracker_is_locked_for_today():
        logger.info("[LATE CHECK] Skipping — tracker not locked (report not yet sent, or tracker file lost on restart)")
        return

    # Gate 2: operator data must exist and be marked Done
    if not check_grid_diesel_entry_exists():
        return

    logger.info("[LATE CHECK] Operator data found after deadline. Running master engine silently...")

    if now.weekday() == 0:  # Monday
        saturday_str = (now - timedelta(days=2)).strftime("%Y-%m-%d")
        specs = [
            {
                # "Sunday" row: Saturday's consumption → solar from Saturday
                "operator_date": yesterday_str,
                "solar_date":    saturday_str,
                "fallback_operator_date": today_str,
            },
            {
                # "Monday" row: Sunday's consumption → solar from Sunday
                "operator_date": today_str,
                "solar_date":    yesterday_str,
                "fallback_operator_date": None,
            },
        ]
    else:
        specs = [
            {
                "operator_date": today_str,
                "solar_date":    yesterday_str,   # universal rule: solar = operator_date - 1
                "fallback_operator_date": None,
            }
        ]

    for spec in specs:
        result = _run_master_data_engine_once(
            spec["operator_date"],
            spec["solar_date"],
            spec.get("fallback_operator_date"),
        )
        if result["status"] == "Success":
            logger.info(f"[LATE CHECK] Master engine complete for {spec['operator_date']}.")
        else:
            logger.error(f"[LATE CHECK] Engine failed for {spec['operator_date']}: {result.get('error')}")


# ──────────────────────────────────────────────────────────────────────────────
# Time-Based Jobs
# ──────────────────────────────────────────────────────────────────────────────
def run_daily_report_automation(trigger_source: str = "scheduler") -> Dict[str, Any]:
    """
    Main Entry Point for sending reports.

    trigger_source="scheduler"  → standard scheduled run; distributed lock applied;
                                   tracker checked; fallback report sent if data missing.
    trigger_source="api_manual" → frontend Send Now button; lock bypassed; tracker bypassed;
                                   if data present  → full report sent + tracker locked;
                                   if data missing  → operator reminder sent, tracker NOT locked.
    """
    from app.services.email_service import send_daily_report

    IST = ZoneInfo("Asia/Kolkata")
    today_str = datetime.now(IST).strftime("%Y-%m-%d")

    # Distributed lock — scheduler only (prevents slot-swap double-fire)
    job_name = "daily_energy_report"
    if trigger_source == "scheduler":
        if not _acquire_distributed_lock(job_name, ttl_seconds=300):
            return {"status": "Skipped", "notes": "Lock held by another instance"}

    try:
        if trigger_source != "api_manual" and tracker_is_locked_for_today():
            logger.info("Deadline reached, but the report was already sent today. Skipping!")
            return {"status": "Skipped", "notes": "Report already sent today"}

        logger.info(f"Triggering daily report automation via {trigger_source}")

        if check_grid_diesel_entry_exists():
            logger.info("Operator data found. Running Master Engine before sending report...")
            engine_result = _run_master_data_engine()

            if engine_result["status"] == "Success":
                result = send_daily_report(trigger_source=trigger_source, is_missing_data=False)
                _lock_tracker_for_today(today_str)
                _save_tracker_to_log(today_str, trigger_source)
                return result
            else:
                logger.error("Master Engine failed. Sending fallback report from existing master data.")
                result = send_daily_report(trigger_source="engine_failed_fallback", is_missing_data=False)
                _lock_tracker_for_today(today_str)
                _save_tracker_to_log(today_str, "engine_failed_fallback")
                return result
        else:
            if trigger_source == "api_manual":
                logger.warning("Data missing during manual Send Now. Sending operator reminder instead of fallback report.")
                from app.services.email_service import send_operator_reminder
                result = send_operator_reminder()
                # Intentionally NOT locking the tracker so the scheduler still fires later
                return result

            logger.warning("Data missing at deadline! Sending fallback report with yesterday's data.")
            result = send_daily_report(trigger_source="empty_fallback", is_missing_data=True)
            _lock_tracker_for_today(today_str)
            _save_tracker_to_log(today_str, "empty_fallback")
            return result

    finally:
        if trigger_source == "scheduler":
            _release_distributed_lock(job_name)


def _run_operator_reminder_cycle() -> None:
    """Triggered at 9:00, 9:30, 10:00 to verify data or send early report."""
    job_name = "operator_reminder_cycle"
    if not _acquire_distributed_lock(job_name, ttl_seconds=300):
        return

    try:
        IST = ZoneInfo("Asia/Kolkata")
        today_str = datetime.now(IST).strftime("%Y-%m-%d")

        if tracker_is_locked_for_today():
            logger.info("Report already sent today. Skipping reminder cycle.")
            return

        if not check_grid_diesel_entry_exists():
            logger.info("Grid data missing! Attempting to send reminder...")
            from app.services.email_service import send_operator_reminder

            result = send_operator_reminder()
            if result.get("status") == "Success":
                logger.info(f"Reminder Email sent successfully: {result.get('notes')}")
            else:
                logger.error(f"Reminder Email FAILED: {result.get('error') or result.get('notes')}")
        else:
            logger.info("Grid data is PRESENT early! Bypassing 10:30 AM deadline and sending Report NOW.")

            engine_result = _run_master_data_engine()
            if engine_result["status"] == "Success":
                from app.services.email_service import send_daily_report
                send_result = send_daily_report(trigger_source="early_submission", is_missing_data=False)

                if send_result.get("status") == "Success":
                    _lock_tracker_for_today(today_str)
                    _save_tracker_to_log(today_str, "early_submission")
                    logger.info("✅ Early report sent successfully. Tracker locked for the day.")
                else:
                    logger.error(f"Failed to send early report: {send_result.get('error')}")
            else:
                logger.error("Master Engine Failed during early submission.")

    finally:
        _release_distributed_lock(job_name)


# ──────────────────────────────────────────────────────────────────────────────
# Scheduler Initialization
# ──────────────────────────────────────────────────────────────────────────────
def _schedule_daily_job(send_time: str) -> None:
    _ensure_scheduler_started()

    try:
        base_time = datetime.strptime(send_time, "%H:%M")
    except ValueError:
        base_time = datetime.strptime("09:00", "%H:%M")

    cycle_1     = base_time
    cycle_2     = base_time + timedelta(minutes=30)
    cycle_3     = base_time + timedelta(minutes=60)
    final_cycle = base_time + timedelta(minutes=90)

    _scheduler.add_job(
        run_daily_report_automation,
        trigger=CronTrigger(
            day_of_week='mon-sat',
            hour=final_cycle.hour,
            minute=final_cycle.minute,
            timezone=ZoneInfo("Asia/Kolkata"),
        ),
        id=SCHEDULER_JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    for cycle, job_id in [
        (cycle_1, "operator_reminder_cycle_1"),
        (cycle_2, "operator_reminder_cycle_2"),
        (cycle_3, "operator_reminder_cycle_3"),
    ]:
        _scheduler.add_job(
            _run_operator_reminder_cycle,
            trigger=CronTrigger(
                day_of_week='mon-sat',
                hour=cycle.hour,
                minute=cycle.minute,
                timezone=ZoneInfo("Asia/Kolkata"),
            ),
            id=job_id,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )


def initialize_scheduler_from_config() -> None:
    """Boot sequence triggered by main.py."""
    if not HAS_SCHEDULER:
        return

    _ensure_scheduler_started()

    # Load persistent tracker on startup to prevent duplicate reports after restart
    persistent_tracker = _load_tracker_from_log()
    with _tracker_lock:
        _daily_report_tracker.update(persistent_tracker)
    # Always log — empty means tracker file was missing or today not yet in it
    logger.info(f"Startup tracker state: {persistent_tracker or 'empty — no report sent yet today'}")

    _run_data_refresh()

    _scheduler.add_job(
        _run_solar_scraper,
        trigger=CronTrigger(hour='6-19', minute='0,30', timezone=ZoneInfo("Asia/Kolkata")),
        id="suryalogix_scraper_job",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    _scheduler.add_job(
        _run_late_data_check,
        trigger=CronTrigger(hour='10-19', minute='30', timezone=ZoneInfo("Asia/Kolkata")),
        id="late_data_check",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    _scheduler.add_job(
        _run_data_refresh,
        trigger=CronTrigger(hour='6-19', minute='0,30', timezone=ZoneInfo("Asia/Kolkata")),
        id="data_refresh_interval",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    cfg = load_scheduler_config()
    if cfg.get("auto_start", False):
        _schedule_daily_job(
            cfg.get("start_time", cfg.get("send_time", DAILY_REPORT_CRON_TIME))
        )