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
import tempfile
import shutil
from concurrent.futures import ProcessPoolExecutor, TimeoutError as FuturesTimeoutError
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import pandas as pd

# FIX R4: Use a threading.Lock() to guard in-memory tracker dicts.
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
# /home/site/wwwroot/  → deployment target, gets overwritten on every deploy
# /home/LogFiles/      → Azure persistent storage, survives deploys & restarts
# ──────────────────────────────────────────────────────────────────────────────
if "WEBSITE_SITE_NAME" in os.environ:
    BASE_DIR    = Path("/home/site/wwwroot/energy-dashboard")
    PERSIST_DIR = Path("/home/LogFiles/energy-dashboard")
else:
    BASE_DIR    = Path(__file__).parent.parent.parent / "energy-dashboard"
    PERSIST_DIR = BASE_DIR

BASE_DIR.mkdir(parents=True, exist_ok=True)
PERSIST_DIR.mkdir(parents=True, exist_ok=True)
(PERSIST_DIR / "output").mkdir(parents=True, exist_ok=True)

SCHEDULER_CONFIG_FILE  = BASE_DIR    / "scheduler_config.json"
SCHEDULER_LOG_FILE     = PERSIST_DIR / "output" / "scheduler_log.json"
SCHEDULER_TRACKER_FILE = PERSIST_DIR / "output" / "scheduler_tracker.json"
SCHEDULER_LOCK_DIR     = PERSIST_DIR / ".scheduler_locks"
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
    """Load today's tracker state from persistent scheduler_tracker.json."""
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
    """Persist today's report sent flag to scheduler_tracker.json."""
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
    
    # FIX: Use atomic write (tempfile + shutil.move) to prevent race conditions 
    # across multiple Azure worker processes.
    try:
        SCHEDULER_TRACKER_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=SCHEDULER_TRACKER_FILE.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(log_data, f, indent=4)
            shutil.move(tmp_path, SCHEDULER_TRACKER_FILE)
            logger.info(f"Persisted report flag to disk for {today_str} (source: {trigger_source})")
        except Exception:
            # Clean up the temp file if dumping fails to avoid littering the disk
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except OSError as e:
        logger.error(f"Error saving scheduler tracker file: {e}")
        


def tracker_is_locked_for_today() -> bool:
    """Check if the daily report has already been dispatched for today."""
    IST = ZoneInfo("Asia/Kolkata")
    today_str = datetime.now(IST).strftime("%Y-%m-%d")

    # FIX: Keep both in-memory and disk checks inside a single lock 
    # to prevent race conditions across threads.
    with _tracker_lock:
        if _daily_report_tracker.get(today_str, False):
            return True
            
        persistent_tracker = _load_tracker_from_log()
        if persistent_tracker.get(today_str, False):
            _daily_report_tracker[today_str] = True
            return True

    return False


def _tracker_was_sent_as_fallback() -> bool:
    """
    Returns True only if today's report was sent WITHOUT real operator data.

    Late check should only re-run the master engine when the report went out
    as a fallback — meaning operator data arrived after the deadline and the
    master-data sheet still needs to be updated.

    If the report was sent with real data (early_submission, scheduler, api_manual),
    the master-data sheet is already correct and the late check must skip,
    otherwise it hammers SharePoint every 30 min for the rest of the day for
    no reason — and risks a 423 Locked error if the file is open.

    Fallback trigger sources:
      "empty_fallback"        → report sent at deadline with NO operator data
      "engine_failed_fallback"→ operator data existed but master engine crashed
    """
    if not SCHEDULER_TRACKER_FILE.exists():
        return False
    try:
        with open(SCHEDULER_TRACKER_FILE, 'r', encoding='utf-8') as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        return False

    IST = ZoneInfo("Asia/Kolkata")
    today_str = datetime.now(IST).strftime("%Y-%m-%d")
    entry = raw.get(today_str, {})
    fallback_sources = {"empty_fallback", "engine_failed_fallback"}
    return entry.get("trigger_source") in fallback_sources


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
    lock_file = _get_lock_file_path(job_name)

    try:
        with open(lock_file, 'x') as f:
            f.write(f"Locked at {datetime.now(ZoneInfo('Asia/Kolkata')).isoformat()}\n")
        logger.info(f" Acquired distributed lock for job: {job_name}")
        return True

    except FileExistsError:
        try:
            if lock_file.exists():
                # FIX R3: Use timezone-aware UTC datetimes on both sides of the subtraction.
                mtime_utc = datetime.fromtimestamp(lock_file.stat().st_mtime, tz=timezone.utc)
                now_utc = datetime.now(timezone.utc)
                lock_age = (now_utc - mtime_utc).total_seconds()
                
                if lock_age > ttl_seconds:
                    logger.warning(f"Stale lock detected for {job_name} (age: {lock_age:.1f}s). Removing.")
                    lock_file.unlink(missing_ok=True)
                    
                    # FIX: Inline retry instead of recursive call to prevent RecursionError
                    try:
                        with open(lock_file, 'x') as f:
                            f.write(f"Locked at {datetime.now(ZoneInfo('Asia/Kolkata')).isoformat()}\n")
                        return True
                    except FileExistsError:
                        logger.info(f"Lock re-acquired by another instance for {job_name}")
                        return False
                else:
                    logger.info(f"Skipping job {job_name} (lock held by another instance, age: {lock_age:.1f}s)")
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
        logger.info(f" Released distributed lock for job: {job_name}")
    except Exception as e:
        logger.error(f"Error releasing lock for {job_name}: {e}")


def _ensure_scheduler_started() -> None:
    if _scheduler and not _scheduler.running:
        _scheduler.start()


def _split_emails(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        raw_items = value
    else:
        raw_items = str(value).replace(";", ",").split(",")
    return [str(item).strip() for item in raw_items if str(item).strip()]


def _dedupe_emails(emails: list[str]) -> list[str]:
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
    # FIX R7: Wrap json.load in a try/except for JSONDecodeError.
    # If scheduler_config.json is partially written (disk full, process killed
    # mid-write), json.load raises JSONDecodeError which previously propagated
    # all the way to crash the server on startup.
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


def save_scheduler_config(config: Dict[str, Any]) -> Dict[str, Any]:
    normalized_config = _normalize_scheduler_recipients(config)
    SCHEDULER_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=SCHEDULER_CONFIG_FILE.parent, suffix=".tmp")
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
                "meter_ocr_start_job",
                "meter_ocr_stop_job",
            ] or job.id.startswith("operator_reminder_"):
                _scheduler.remove_job(job.id)

    # Cleanly terminate the OCR engine subprocess on app shutdown
    _stop_meter_ocr_engine()

    if disable_auto_start:
        cfg = load_scheduler_config()
        cfg["auto_start"] = False
        save_scheduler_config(cfg)
    return {"status": "stopped"}


# ──────────────────────────────────────────────────────────────────────────────
# Data Integrity (Ojas-Proof Excel Checks)
# ──────────────────────────────────────────────────────────────────────────────
def _status_is_done(value: Any) -> bool:
    if value is None:
        return False
    if pd.isna(value):
        return False
    return str(value).strip().lower() == "done"


def check_grid_diesel_entry_exists() -> bool:
    """Check if data exists for TODAY in the grid_and_diesel Excel file AND Status='Done'."""
    try:
        from .sharepoint_data_service import get_service as get_excel_service

        sp_excel_service = get_excel_service()
        df = sp_excel_service.fetch_sheet_data("grid_and_diesel")

        if df is None or df.empty:
            logger.error("[SCHEDULER DEBUG] Excel file is empty or could not be loaded!")
            return False

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
            return str(value).strip().lower() not in {"", "nan", "none", "null"}

        grid_units_col = next(
            (c for c in df.columns if "grid" in _normalized(c) and "unit" in _normalized(c)),
            None,
        )
        if not grid_units_col:
            logger.error("[SCHEDULER] Could not locate Grid Units column in Grid and Diesel data.")
            return False

        if today_rows[grid_units_col].apply(_has_value).any():
            status_col = next((c for c in df.columns if "status" in str(c).lower()), None)
            if status_col:
                if today_rows[status_col].apply(_status_is_done).any():
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


# ──────────────────────────────────────────────────────────────────────────────
# Column Validation Rules
# Each entry: (matcher_keywords, rule_type, rule_details)
# matcher_keywords: list of lowercase substrings — ALL must appear in column name
# rule_type: "date" | "day" | "time" | "numeric" | "text"
# ──────────────────────────────────────────────────────────────────────────────
_COLUMN_RULES = [
    # keywords              rule      extra
    (["date"],              "date",   {}),
    (["day"],               "day",    {}),
    (["time"],              "time",   {}),
    (["ambient", "temp"],   "temp_range", {"min": -10, "max": 60}),
    (["grid", "unit"],      "numeric", {"min": 0,    "max": 99999, "allow_comma": True}),
    (["solar", "unit"],     "numeric", {"min": 0,    "max": 99999, "allow_comma": True}),
    (["total", "unit"],     "numeric", {"min": 0,    "max": 199999,"allow_comma": True}),
    (["total", "inr"],      "numeric", {"min": 0,    "max": 9999999,"allow_comma": True}),
    (["saving", "inr"],     "numeric", {"min": 0,    "max": 9999999,"allow_comma": True}),
    (["panel"],             "numeric", {"min": 0,    "max": 10000, "allow_comma": False}),
]

VALID_DAYS = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}


def _match_col(col_name: str, keywords: list) -> bool:
    """Returns True if all keywords appear in the normalized column name."""
    norm = str(col_name).lower().replace(" ", "").replace("_", "").replace("\n", "")
    return all(kw.replace(" ", "") in norm for kw in keywords)


def _parse_numeric(raw: str) -> Optional[float]:
    """Parse a numeric string, accepting comma-formatted values like 4,452."""
    cleaned = str(raw).strip().replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _validate_cell(col_name: str, raw_value: Any, rule: str, extra: dict) -> Optional[str]:
    """
    Validate a single cell.
    Returns an error string if invalid, None if valid.
    """
    raw = str(raw_value).strip() if raw_value is not None else ""

    # Skip truly empty cells — missing-data logic handles those separately
    if raw in ("", "nan", "none", "null", "NaT"):
        return None

    if rule == "date":
        parsed = pd.to_datetime(raw, errors="coerce", dayfirst=True)
        if pd.isna(parsed):
            return f"'{raw}' is not a valid date (expected e.g. 05-May-2026)"

    elif rule == "day":
        if raw.strip().lower() not in VALID_DAYS:
            return f"'{raw}' is not a valid day name (expected Monday–Sunday)"

    elif rule == "time":
        # Accept HH:MM or HH:MM:SS only — explicitly reject date strings and
        # dash-separated values like "21-37". The generic pd.to_datetime fallback
        # was removed because it accepts dates (e.g. "05-May-2026") as valid times.
        import re as _re
        _TIME_PATTERNS = [
            r"^\d{1,2}:\d{2}$",        # HH:MM
            r"^\d{1,2}:\d{2}:\d{2}$",  # HH:MM:SS
        ]
        if not any(_re.match(p, raw) for p in _TIME_PATTERNS):
            return f"'{raw}' is not a valid time (expected HH:MM format, e.g. 10:30)"

    elif rule == "temp_range":
        # Accepts a single number (e.g. "31") OR a MIN-MAX range with a hyphen
        # (e.g. "21-37" meaning min 21°C, max 37°C).
        import re as _re
        lo = extra.get("min")
        hi = extra.get("max")
        range_match = _re.match(r"^(-?\d+(?:\.\d+)?)-(-?\d+(?:\.\d+)?)$", raw.strip())
        if range_match:
            t_min = float(range_match.group(1))
            t_max = float(range_match.group(2))
            if t_min > t_max:
                return f"'{raw}' is invalid: first value ({t_min}) must be ≤ second value ({t_max})"
            if lo is not None and t_min < lo:
                return f"'{raw}' range low ({t_min}) is below minimum allowed value ({lo})"
            if hi is not None and t_max > hi:
                return f"'{raw}' range high ({t_max}) exceeds maximum allowed value ({hi})"
        else:
            # Fall back to single numeric value
            num = _parse_numeric(raw)
            if num is None:
                return (
                    f"'{raw}' is not a valid temperature "
                    f"(expected a number e.g. '31', or a MIN-MAX range e.g. '21-37')"
                )
            if lo is not None and num < lo:
                return f"'{raw}' is below minimum allowed value ({lo})"
            if hi is not None and num > hi:
                return f"'{raw}' exceeds maximum allowed value ({hi})"

    elif rule == "numeric":
        num = _parse_numeric(raw)
        if num is None:
            return f"'{raw}' is not a valid number"
        lo = extra.get("min")
        hi = extra.get("max")
        if lo is not None and num < lo:
            return f"'{raw}' is below minimum allowed value ({lo})"
        if hi is not None and num > hi:
            return f"'{raw}' exceeds maximum allowed value ({hi})"

    elif rule == "text":
        # Text columns: just check it's not a number accidentally entered as a date/time
        parsed_as_time = pd.to_datetime(raw, format="%H:%M", errors="coerce")
        if pd.notna(parsed_as_time) and raw.replace(":", "").isdigit():
            return f"'{raw}' looks like a time value but is in the Issues column"

    return None  # valid


def validate_data_for_today() -> Dict[str, Any]:
    """
    Validates ALL columns in today's grid_and_diesel row against expected rules.

    Returns:
        {"valid": True}
        {"valid": False, "errors": [{"column": ..., "value": ..., "error": ...}, ...]}
    """
    try:
        from .sharepoint_data_service import get_service as get_excel_service

        df = get_excel_service().fetch_sheet_data("grid_and_diesel")

        if df is None or df.empty:
            return {"valid": True}  # let missing-data logic handle

        IST = ZoneInfo("Asia/Kolkata")
        today = pd.Timestamp.now(tz=IST).date()

        date_col = next((c for c in df.columns if "date" in str(c).lower()), None)
        if not date_col:
            return {"valid": True}

        parsed_dates = pd.to_datetime(df[date_col], errors="coerce", dayfirst=True)

        # Rows whose date can't be parsed are themselves invalid — validate them too.
        invalid_date_rows = df[parsed_dates.isna()]
        today_rows = df[parsed_dates.dt.date == today]

        # Merge: today's rows + any rows with an unparseable date (they're also "today's" bad data)
        candidate_rows = pd.concat([today_rows, invalid_date_rows]).drop_duplicates()

        if candidate_rows.empty:
            return {"valid": True}  # no rows yet

        errors = []

        for _, row in candidate_rows.iterrows():
            for (keywords, rule, extra) in _COLUMN_RULES:
                # Find the matching column in the dataframe
                matched_col = next(
                    (c for c in df.columns if _match_col(c, keywords)),
                    None,
                )
                if matched_col is None:
                    continue  # column not present — skip

                raw_val = row.get(matched_col)
                err = _validate_cell(matched_col, raw_val, rule, extra)
                if err:
                    errors.append({
                        "column": matched_col,
                        "value":  str(raw_val),
                        "error":  err,
                    })

        if errors:
            logger.warning(f"[DATA VALIDATION] {len(errors)} error(s) found for {today}: {errors}")
            return {"valid": False, "errors": errors}

        logger.info(f"[DATA VALIDATION] All columns valid for {today}.")
        return {"valid": True}

    except Exception as e:
        logger.error(f"[DATA VALIDATION] Crashed: {e}", exc_info=True)
        return {"valid": True}  # fail-open — don't block report on validator crash

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

        # FIX R6: html.escape() all values from SharePoint before injecting into HTML.
        # Excel cells could contain '<', '>', '"' or even script tags from user input.
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
    """Run one isolated master-data merge cycle in a completely fresh subprocess."""
    logger.info(
        "Master data engine run: operator_date=%s, solar_date=%s, fallback_operator_date=%s",
        operator_date, solar_date, fallback_operator_date,
    )
    
    try:
        # Resolve the absolute path to the master_data_engine script
        backend_root = Path(__file__).parent.parent.parent
        script_path = backend_root / "app" / "agents" / "master_data_engine.py"
        
        # Build the command dynamically based on presence of fallback date
        cmd = [sys.executable, str(script_path), operator_date, solar_date]
        if fallback_operator_date:
            cmd.append(fallback_operator_date)

        # FIX: Use subprocess.run instead of ProcessPoolExecutor to prevent 
        # inherited mutex deadlocks and resource leaks on Azure App Service.
        subprocess.run(
            cmd,
            check=True,
            timeout=300
        )
        return {"status": "Success"}
        
    except subprocess.TimeoutExpired:
        logger.error("Master data engine subprocess timed out after 90s")
        return {"status": "Error", "message": "Engine timeout"}
    except subprocess.CalledProcessError as exc:
        logger.error(f"Master data engine subprocess failed (Exit Code {exc.returncode}).")
        return {"status": "Failed", "error": f"Subprocess exit code {exc.returncode}"}
    except Exception as exc:
        logger.error(f"Master data engine failed to trigger: {exc}", exc_info=True)
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
            "solar_date": solar_yesterday,
            "fallback_operator_date": None,
        }
    ]

    if now.weekday() == 0:  # Monday
        sunday   = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        saturday = (now - timedelta(days=2)).strftime("%Y-%m-%d")
        run_specs = [
            {
                "operator_date": sunday,
                "solar_date": saturday,
                "fallback_operator_date": operator_today,
            },
            {
                "operator_date": operator_today,
                "solar_date": sunday,
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
            failures.append({
                "operator_date": spec["operator_date"],
                "solar_date": spec["solar_date"],
                "error": result.get("error", "Unknown failure"),
            })

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
            timeout=300,   # FIX C5: Added timeout=300 (5 minutes).
                           # Without a timeout, a hung scraper (network issue, deadlock)
                           # would block the APScheduler thread permanently, eventually
                           # starving all other jobs.
        )
        logger.info("Scraper subprocess finished successfully.")
    except subprocess.TimeoutExpired:
        logger.error("Scraper subprocess timed out after 300s and was killed.")
    except subprocess.CalledProcessError as exc:
        logger.error(f"Scraper subprocess failed (Exit Code {exc.returncode}).")
    except Exception as exc:
        logger.error(f"Scraper completely failed to trigger: {exc}")

    # Run inverter monitor after every scraper tick (success or fail).
    # Even if the scraper failed, the monitor may still find the previous row
    # on SharePoint and update the tracker / send a fault alert.
    try:
        from app.services.inverter_monitor import run_inverter_monitor
        run_inverter_monitor()
    except Exception as exc:
        logger.error(f"Inverter monitor failed: {exc}")


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
    Re-runs the master engine ONLY when the report was sent as a fallback
    (operator data was missing at deadline) and operator has since submitted.

    GATE LOGIC:
      Gate 1 — tracker locked?          Report sent today at all?
      Gate 2 — sent as fallback?        Was it sent WITHOUT real operator data?
                                        If report was sent with real data → SKIP.
                                        No need to re-run, master-data is already correct.
      Gate 3 — operator data now Done?  Has the operator submitted since the fallback?

    WHY Gate 2 matters:
      Without it, every 30-min tick after a successful early/on-time report would
      hammer SharePoint with unnecessary engine runs, wasting resources and risking
      423 Locked errors if the file happens to be open.

    _late_engine_ran_today flag intentionally REMOVED — it was blocking corrections
    after the first successful late run. Gate 2 + Gate 3 together are sufficient.

    Monday: processes both Sunday and Monday rows with correct solar dates,
    following the universal rule (solar_date = operator_date - 1).
    """
    IST = ZoneInfo("Asia/Kolkata")
    now = datetime.now(IST)
    today_str     = now.strftime("%Y-%m-%d")
    yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    # Gate 1: report must have been sent today
    if not tracker_is_locked_for_today():
        logger.info("[LATE CHECK] Skipping — report not yet sent today (or tracker lost on restart)")
        return

    # Gate 2: only proceed if the report went out WITHOUT real operator data
    if not _tracker_was_sent_as_fallback():
        logger.info("[LATE CHECK] Skipping — report was already sent with real data today, master-data is correct")
        return

    # Gate 3: operator data must now be present and marked Done
    if not check_grid_diesel_entry_exists():
        return

    logger.info("[LATE CHECK] Operator data found after fallback report. Running master engine silently...")

    if now.weekday() == 0:  # Monday
        saturday_str = (now - timedelta(days=2)).strftime("%Y-%m-%d")
        specs = [
            {
                "operator_date": yesterday_str,   # Sunday row → solar from Saturday
                "solar_date":    saturday_str,
                "fallback_operator_date": today_str,
            },
            {
                "operator_date": today_str,        # Monday row → solar from Sunday
                "solar_date":    yesterday_str,
                "fallback_operator_date": None,
            },
        ]
    else:
        specs = [
            {
                "operator_date": today_str,
                "solar_date":    yesterday_str,
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
            logger.info(f"[LATE CHECK] ✅ Master engine complete for {spec['operator_date']}.")
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
            validation = validate_data_for_today()
            if not validation["valid"]:
                errs = validation.get("errors", [])
                logger.warning(f"[VALIDATION] Blocking report — {len(errs)} error(s) found.")

                from app.services.email_service import send_data_correction_alert
                send_data_correction_alert(errs)

                if trigger_source == "scheduler":
                    # Deadline hit with bad data → send fallback
                    logger.warning("[VALIDATION] Deadline reached with invalid data. Sending fallback report.")
                    result = send_daily_report(trigger_source="invalid_data_fallback", is_missing_data=True)
                    _lock_tracker_for_today(today_str)
                    _save_tracker_to_log(today_str, "invalid_data_fallback")
                    return result
                else:
                    return {
                        "status": "Blocked",
                        "notes": f"{len(errs)} validation error(s) found. Correction email sent to operator.",
                        "errors": errs,
                    }

       
            logger.info("Data valid. Running Master Engine before sending report...")
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
            validation = validate_data_for_today()
            if not validation["valid"]:
                errs = validation.get("errors", [])
                logger.warning(f"[VALIDATION] Early cycle: {len(errs)} error(s). Sending correction alert.")
                from app.services.email_service import send_data_correction_alert
                send_data_correction_alert(errs)
                return 
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

def _run_powerapp_grid_sync() -> None:
    """Runs the PowerApp -> Operator Sheet sync in an isolated subprocess."""
    try:
        logger.info("⏳ Starting PowerApp Grid Sync job...")
        backend_root = Path(__file__).parent.parent.parent
        script_path = backend_root / "app" / "agents" / "powerapp_sync_engine.py"
        
        # Runs isolated to prevent pandas memory leaks in the main FastAPI thread
        subprocess.run(
            [sys.executable, str(script_path)],
            check=True,
            timeout=120
        )
        logger.info("PowerApp Grid Sync subprocess finished successfully.")
    except subprocess.TimeoutExpired:
        logger.error("PowerApp Sync timed out after 120s.")
    except subprocess.CalledProcessError as exc:
        logger.error(f"PowerApp Sync subprocess failed (Exit Code {exc.returncode}).")
    except Exception as exc:
        logger.error(f"PowerApp Sync completely failed to trigger: {exc}")


# ──────────────────────────────────────────────────────────────────────────────
# Meter OCR Engine (meter_process.py)
#
# meter_process.py is a long-running polling loop — it connects to a separate
# SharePoint list, picks up pending meter-image jobs, runs Azure OpenAI OCR,
# and writes results back. It is completely independent of the energy-report
# pipeline above.
#
# Integration strategy:
#   - We do NOT import meter_process.py. Like the solar scraper and the master
#     data engine, we launch it as an isolated subprocess. This prevents any
#     OpenCV / Azure OpenAI state from leaking into the FastAPI worker process.
#   - A module-level handle (_meter_process) lets us check whether a previous
#     instance is still running before spawning a new one, so we never have
#     two OCR loops polling simultaneously.
#   - Operating window: 07:00 – 20:00 IST (matches the meter_process.py guard).
#     APScheduler starts it at 07:00 and the stop job at 20:00 sends SIGTERM.
# ──────────────────────────────────────────────────────────────────────────────

_meter_process: Optional[subprocess.Popen] = None
_meter_process_lock = threading.Lock()


def _start_meter_ocr_engine() -> None:
    """
    Launched by APScheduler at 07:00 IST every day.
    Spawns meter_process.py as a background subprocess if not already running.
    """
    global _meter_process

    with _meter_process_lock:
        # Guard: don't double-spawn if a previous instance is still alive
        if _meter_process is not None and _meter_process.poll() is None:
            logger.info("[METER OCR] Engine already running (PID %s). Skipping spawn.", _meter_process.pid)
            return

        try:
            backend_root = Path(__file__).parent.parent.parent
            script_path  = backend_root / "meter_process.py"

            if not script_path.exists():
                logger.error("[METER OCR] meter_process.py not found at %s. Cannot start.", script_path)
                return

            logger.info("[METER OCR] Starting meter OCR engine subprocess...")
            _meter_process = subprocess.Popen(
                [sys.executable, str(script_path)],
                # Inherit the parent environment so all Azure / SharePoint env-vars
                # (loaded by EnergyAgent's startup) are available to the child.
                env=os.environ.copy(),
            )
            logger.info("[METER OCR] Engine started successfully (PID %s).", _meter_process.pid)

        except Exception as exc:
            logger.error("[METER OCR] Failed to start meter_process.py: %s", exc, exc_info=True)


def _stop_meter_ocr_engine() -> None:
    """
    Launched by APScheduler at 20:00 IST every day.
    Sends SIGTERM to the OCR subprocess; escalates to SIGKILL after 10 s.
    """
    global _meter_process

    with _meter_process_lock:
        if _meter_process is None:
            logger.info("[METER OCR] No running engine process found. Nothing to stop.")
            return

        if _meter_process.poll() is not None:
            logger.info("[METER OCR] Engine already exited (return code %s).", _meter_process.returncode)
            _meter_process = None
            return

        pid = _meter_process.pid
        try:
            logger.info("[METER OCR] Sending SIGTERM to engine (PID %s)...", pid)
            _meter_process.terminate()
            try:
                _meter_process.wait(timeout=10)
                logger.info("[METER OCR] Engine (PID %s) terminated cleanly.", pid)
            except subprocess.TimeoutExpired:
                logger.warning("[METER OCR] Engine (PID %s) did not exit in 10 s — sending SIGKILL.", pid)
                _meter_process.kill()
                _meter_process.wait()
                logger.info("[METER OCR] Engine (PID %s) killed.", pid)
        except Exception as exc:
            logger.error("[METER OCR] Error stopping engine (PID %s): %s", pid, exc)
        finally:
            _meter_process = None


def get_meter_ocr_status() -> dict:
    """Returns the current status of the meter OCR subprocess (for API/health checks)."""
    with _meter_process_lock:
        if _meter_process is None:
            return {"status": "stopped", "pid": None}
        rc = _meter_process.poll()
        if rc is None:
            return {"status": "running", "pid": _meter_process.pid}
        return {"status": "exited", "pid": _meter_process.pid, "return_code": rc}


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

    persistent_tracker = _load_tracker_from_log()
    with _tracker_lock:
        _daily_report_tracker.update(persistent_tracker)
    logger.info(f"Startup tracker state: {persistent_tracker or 'empty — no report sent yet today'}")

    _run_data_refresh()

    _scheduler.add_job(
        _run_solar_scraper,
        trigger=CronTrigger(hour='5-19', minute='0,30', timezone=ZoneInfo("Asia/Kolkata")),
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
        trigger=CronTrigger(hour='5-19', minute='0,30', timezone=ZoneInfo("Asia/Kolkata")),
        id="data_refresh_interval",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    # Tries to sync the Power App grid data at 8:15 AM, 8:30 AM, and 8:45 AM.
    # This ensures the Grid row is pre-filled before the 9:00 AM Operator Reminder triggers.
    _scheduler.add_job(
        _run_powerapp_grid_sync,
        trigger=CronTrigger(hour='8', minute='15,30,45', timezone=ZoneInfo("Asia/Kolkata")),
        id="powerapp_grid_sync_job",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # ── Meter OCR Engine — start at 07:00, stop at 20:00 IST every day ──────
    # meter_process.py has its own internal time-window guard, but we drive it
    # from here so that it participates in the same scheduler lifecycle as every
    # other background job (visible in /api/scheduler/status, stopped cleanly
    # on app shutdown via stop_scheduler()).
    _scheduler.add_job(
        _start_meter_ocr_engine,
        trigger=CronTrigger(hour=7, minute=0, timezone=ZoneInfo("Asia/Kolkata")),
        id="meter_ocr_start_job",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        _stop_meter_ocr_engine,
        trigger=CronTrigger(hour=20, minute=0, timezone=ZoneInfo("Asia/Kolkata")),
        id="meter_ocr_stop_job",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info("Meter OCR engine scheduled: start 07:00 IST / stop 20:00 IST daily.")

    # 🚀 THE FIX: Start it immediately if the server boots up during the day!
    IST = ZoneInfo("Asia/Kolkata")
    now = datetime.now(IST)
    if 7 <= now.hour < 20:
        logger.info("Server booted during operating hours. Starting Meter OCR engine immediately...")
        _start_meter_ocr_engine()

    cfg = load_scheduler_config()
    if cfg.get("auto_start", False):
        _schedule_daily_job(
            cfg.get("start_time", cfg.get("send_time", DAILY_REPORT_CRON_TIME))
        )