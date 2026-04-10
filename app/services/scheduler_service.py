"""
Scheduler service (simplified for MVP)
"""
import json
import sys
import os
import re
import html as html_lib
import threading
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List
from datetime import datetime, date
import pandas as pd
from dotenv import load_dotenv

# scheduler_service.py

FAILURE_NOTIFICATION_SUBJECT = "System Alert: Data Read Failure"
FAILURE_NOTIFICATION_BODY = "Agent is not able to read data. Kindly check the data."

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

from zoneinfo import ZoneInfo

from app.core.config import get_settings
from app.core.logger import logger

if "WEBSITE_SITE_NAME" in os.environ:
    # Running in Azure
    BASE_DIR = Path("/home/site/wwwroot/energy-dashboard")
else:
    # Running locally
    BASE_DIR = Path(__file__).parent.parent.parent / "energy-dashboard"

BASE_DIR.mkdir(parents=True, exist_ok=True)
SCHEDULER_CONFIG_FILE = BASE_DIR / "scheduler_config.json"
SCHEDULER_LOG_FILE = BASE_DIR / "output" / "scheduler_log.json"
SCHEDULER_JOB_ID = "daily_energy_report"
RETRY_JOB_ID = "daily_energy_report_retry"
RETRY_INTERVAL_MINUTES = 30
STAKEHOLDER_NOTIFICATION_EMAIL = "prajwal.khadse@maqsoftware.com,umang.mittal@maqsoftware.com"
DAILY_REPORT_CRON_TIME = "10:30"
FAILURE_NOTIFICATION_SUBJECT = "Failed to automate mail"
FAILURE_NOTIFICATION_BODY = (
    "Good Morning. The Energy Consumption Reporting Agent could not create or send "
    "the daily report.\nPlease send it manually."
)
# Find these two lines and change the defaults:
SHAREPOINT_OPS_MANUAL_LIST_NAME = os.getenv("SHAREPOINT_OPS_MANUAL_LIST_NAME", "Grid_Diesel_List")
SHAREPOINT_UNIFIED_MASTER_LIST_NAME = os.getenv("SHAREPOINT_UNIFIED_MASTER_LIST_NAME", "Master_Data_List")# was "Solar_Master_Unified"
SHAREPOINT_MORNING_JOB_ID = "sharepoint_morning_master_sync"
SHAREPOINT_EOD_JOB_ID = "sharepoint_suryalogix_eod_sync"
SHAREPOINT_MORNING_CRON_TIME = os.getenv("SHAREPOINT_MORNING_SYNC_TIME", "08:00")
SHAREPOINT_EOD_CRON_TIME = os.getenv("SHAREPOINT_EOD_SYNC_TIME", "19:00")
MASTER_DATA_ENGINE_JOB_ID = "master_data_engine_nightly"
# Load email environment variables
energy_dashboard_path = Path(__file__).parent.parent.parent / "energy-dashboard"


def _load_scheduler_env() -> None:
    """Load scheduler env vars from supported files/locations."""
    candidates = [
        energy_dashboard_path / ".env",
        energy_dashboard_path / "env",
        energy_dashboard_path.parent / ".env",
    ]
    for env_path in candidates:
        if env_path.exists():
            load_dotenv(env_path, override=True)


_load_scheduler_env()

# Add energy-dashboard to sys.path at module level so imports work throughout the module
if str(energy_dashboard_path) not in sys.path:
    sys.path.insert(0, str(energy_dashboard_path))

# Debug marker
_debug_log_path = energy_dashboard_path / "output" / "scheduler_module_debug.txt"
_debug_log_path.parent.mkdir(parents=True, exist_ok=True)

# Initialize scheduler only if apscheduler is available
if HAS_SCHEDULER:
    _scheduler = BackgroundScheduler(timezone=ZoneInfo("Asia/Kolkata"))
else:
    _scheduler = None

_automation_lock = threading.Lock()


def _get_env_value(*keys: str, default: str = "") -> str:
    for key in keys:
        value = os.getenv(key)
        if value is None:
            continue
        text = str(value).strip().strip('"').strip("'")
        if text:
            return text
    return default


def _to_bool(value: str, default: bool = True) -> bool:
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _parse_date_input(date_input: Any) -> Optional[pd.Timestamp]:
    if date_input in (None, ""):
        return None

    text = str(date_input).strip()
    if not text:
        return None

    dmy_slash = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", text)
    if dmy_slash:
        parsed = pd.to_datetime(
            f"{dmy_slash.group(3)}-{int(dmy_slash.group(2)):02d}-{int(dmy_slash.group(1)):02d}",
            errors="coerce",
        )
        return None if pd.isna(parsed) else parsed

    parsed = pd.to_datetime(text, errors="coerce")
    if pd.notna(parsed):
        return parsed

    parsed = pd.to_datetime(text, errors="coerce", dayfirst=True)
    if pd.notna(parsed):
        return parsed

    return None


def formatDate(date_input: Any) -> str:
    parsed = _parse_date_input(date_input)
    if parsed is None:
        return str(date_input or "").strip()

    day = parsed.day
    month = parsed.strftime("%b")
    year = parsed.year
    return f"{day}-{month}-{year}"


def _format_en_in(value: float, decimals: int) -> str:
    rounded = f"{abs(value):.{decimals}f}"
    whole, frac = rounded.split(".") if "." in rounded else (rounded, "")

    if len(whole) > 3:
        last_three = whole[-3:]
        lead = whole[:-3]
        groups = []
        while len(lead) > 2:
            groups.insert(0, lead[-2:])
            lead = lead[:-2]
        if lead:
            groups.insert(0, lead)
        whole = ",".join(groups + [last_three])

    sign = "-" if value < 0 else ""
    if decimals > 0:
        return f"{sign}{whole}.{frac}"
    return f"{sign}{whole}"


def normalizeIssueText(value: Any) -> str:
    if value is None:
        return "No issues"

    text = str(value).strip()
    if not text:
        return "No issues"

    lower = text.lower()
    return lower[:1].upper() + lower[1:]


def _validate_send_time(send_time: str) -> Tuple[int, int]:
    """Validate HH:MM time and return (hour, minute)."""
    if not isinstance(send_time, str) or ":" not in send_time:
        raise ValueError("send_time must be in HH:MM format")
    hour_str, minute_str = send_time.split(":", 1)
    if not hour_str.isdigit() or not minute_str.isdigit():
        raise ValueError("send_time must be in HH:MM format")

    hour = int(hour_str)
    minute = int(minute_str)
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError("send_time must be in HH:MM format")
    return hour, minute


def _ensure_scheduler_started() -> None:
    if not _scheduler.running:
        _scheduler.start()


def _append_scheduler_log_entry(entry: Dict[str, Any]) -> None:
    """Persist a single scheduler log entry and cap history length."""
    SCHEDULER_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

    logs = []
    if SCHEDULER_LOG_FILE.exists():
        try:
            with open(SCHEDULER_LOG_FILE, "r") as f:
                logs = json.load(f)
        except (json.JSONDecodeError, ValueError):
            logs = []

    if not isinstance(logs, list):
        logs = []

    logs.insert(0, entry)
    logs = logs[:100]

    with open(SCHEDULER_LOG_FILE, "w") as f:
        json.dump(logs, f, indent=2)


def _normalize_record_date_key(value: Any) -> str:
    if value in (None, ""):
        return ""

    parsed = pd.to_datetime(value, errors="coerce")
    if pd.notna(parsed):
        return parsed.strftime("%Y-%m-%d")

    return str(value).strip()[:10]


def _extract_record_date_key(row: Dict[str, Any]) -> str:
    for key in ["Date", "date", "Timestamp", "timestamp"]:
        if key in row and row.get(key) not in (None, ""):
            date_key = _normalize_record_date_key(row.get(key))
            if date_key:
                return date_key
    return ""


def check_master_data_today_flag() -> Dict[str, Any]:
    """
    Check if yesterday's data row exists in Master_Data_List on SharePoint.
    FOUND=True  → data is present, send the daily report.
    FOUND=False → data is missing, notify operator and retry.
    """
    today_ist = pd.Timestamp.now(tz=ZoneInfo("Asia/Kolkata")).date()
    checked_date_obj = today_ist - pd.Timedelta(days=1)
    checked_date = checked_date_obj.strftime("%Y-%m-%d")
    error_message = ""
    FOUND = False
    total_records = 0
    data_source = "sharepoint-master-data"

    try:
        from .sharepoint_list_data_service import SharePointListDataService
        sp_service = SharePointListDataService()

        if not sp_service.is_authenticated():
            error_message = sp_service.get_last_error() or "SharePoint not authenticated"
            logger.warning(f"check_master_data_today_flag: {error_message}")
        else:
            item = sp_service.get_list_item_by_date(
                SHAREPOINT_UNIFIED_MASTER_LIST_NAME, checked_date_obj
            )
            if item and item.get("id"):
                FOUND = True
                total_records = 1
            else:
                FOUND = False
                total_records = 0

    except Exception as exc:
        error_message = str(exc)[:200]
        logger.error(f"check_master_data_today_flag exception: {exc}")

    source_label = data_source
    notes = f"checked {total_records} rows from {source_label} for {checked_date}"
    if error_message:
        notes = f"{notes}; error={error_message}"

    return {
        "date_checked": checked_date,
        "found": FOUND,
        "rows_checked": total_records,
        "data_source": source_label,
        "error": error_message,
        "notes": notes,
    }
def _schedule_retry_loop() -> None:
    """Create or replace a single retry job that runs every 30 minutes."""
    _ensure_scheduler_started()
    trigger = IntervalTrigger(minutes=RETRY_INTERVAL_MINUTES, timezone=ZoneInfo("Asia/Kolkata"))
    _scheduler.add_job(
        _run_retry_cycle,
        trigger=trigger,
        id=RETRY_JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=1800,
    )


def _stop_retry_loop() -> None:
    retry_job = _scheduler.get_job(RETRY_JOB_ID)
    if retry_job is not None:
        _scheduler.remove_job(RETRY_JOB_ID)


def send_stakeholder_pending_notification() -> Dict[str, Any]:
    """Send pending-log reminder to stakeholder when today's row is missing."""
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    _load_scheduler_env()

    smtp_server = _get_env_value("SMTP_SERVER", "SMTP_HOST", "MAIL_SERVER", default="smtp.gmail.com")
    smtp_port = int(_get_env_value("SMTP_PORT", default="587"))
    sender_email = _get_env_value("SENDER_EMAIL", "SMTP_USERNAME", "EMAIL_FROM", "MAIL_USERNAME", "EMAIL_USER")
    sender_password = _get_env_value("SENDER_PASSWORD", "SMTP_PASSWORD", "MAIL_PASSWORD", "EMAIL_PASSWORD")
    use_tls = _to_bool(_get_env_value("SMTP_USE_TLS", "SMTP_TLS", "SMTP_STARTTLS", default="True"), default=True)
    timeout = int(_get_env_value("SMTP_TIMEOUT", default="10"))
    login_user = _get_env_value("SMTP_USERNAME", "SENDER_EMAIL", "MAIL_USERNAME", "EMAIL_USER", default=sender_email)
    email_from = _get_env_value("EMAIL_FROM", "SENDER_EMAIL", "SMTP_USERNAME", "MAIL_FROM", "MAIL_USERNAME", default=sender_email)

    today_ist = pd.Timestamp.now(tz=ZoneInfo("Asia/Kolkata")).date()
    previous_day = today_ist - pd.Timedelta(days=1)
    previous_day_str = previous_day.strftime("%d-%m-%Y")
    
    subject = "Update Energy Log"
    body = f"Good Morning,\n\nPlease update the energy log for {previous_day_str}\n"
    to_list = [STAKEHOLDER_NOTIFICATION_EMAIL]

    msg = MIMEMultipart("alternative")
    msg["From"] = email_from
    msg["To"] = ", ".join(to_list)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        if not email_from:
            raise ValueError("Missing sender email in env (set SENDER_EMAIL / SMTP_USERNAME / EMAIL_FROM)")
        if not sender_password:
            raise ValueError("Missing sender password in env (set SENDER_PASSWORD / SMTP_PASSWORD)")

        with smtplib.SMTP(smtp_server, smtp_port, timeout=timeout) as server:
            if use_tls:
                server.starttls()
            if login_user and sender_password:
                server.login(login_user, sender_password)
            server.sendmail(email_from, to_list, msg.as_string())

        return {
            "timestamp": datetime.now().isoformat(),
            "status": "Success",
            "recipients": ", ".join(to_list),
            "attachment": None,
            "notes": "Pending-log stakeholder notification sent",
        }
    except Exception as exc:
        return {
            "timestamp": datetime.now().isoformat(),
            "status": "Failed",
            "recipients": ", ".join(to_list),
            "attachment": None,
            "notes": f"Pending-log stakeholder notification failed: {str(exc)[:200]}",
        }


def send_stakeholder_failure_notification(error_context: str = "") -> Dict[str, Any]:
    """Notify stakeholder when daily report automation fails for any reason."""
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    _load_scheduler_env()

    smtp_server = _get_env_value("SMTP_SERVER", "SMTP_HOST", "MAIL_SERVER", default="smtp.gmail.com")
    smtp_port = int(_get_env_value("SMTP_PORT", default="587"))
    sender_email = _get_env_value("SENDER_EMAIL", "SMTP_USERNAME", "EMAIL_FROM", "MAIL_USERNAME", "EMAIL_USER")
    sender_password = _get_env_value("SENDER_PASSWORD", "SMTP_PASSWORD", "MAIL_PASSWORD", "EMAIL_PASSWORD")
    use_tls = _to_bool(_get_env_value("SMTP_USE_TLS", "SMTP_TLS", "SMTP_STARTTLS", default="True"), default=True)
    timeout = int(_get_env_value("SMTP_TIMEOUT", default="10"))
    login_user = _get_env_value("SMTP_USERNAME", "SENDER_EMAIL", "MAIL_USERNAME", "EMAIL_USER", default=sender_email)
    email_from = _get_env_value("EMAIL_FROM", "SENDER_EMAIL", "SMTP_USERNAME", "MAIL_FROM", "MAIL_USERNAME", default=sender_email)

    to_list = [STAKEHOLDER_NOTIFICATION_EMAIL]
    message_body = FAILURE_NOTIFICATION_BODY
    if error_context:
        message_body = f"{message_body}\n\nRoot cause: {error_context[:500]}"

    msg = MIMEMultipart("alternative")
    msg["From"] = email_from
    msg["To"] = ", ".join(to_list)
    msg["Subject"] = FAILURE_NOTIFICATION_SUBJECT
    msg.attach(MIMEText(message_body, "plain"))

    try:
        if not email_from:
            raise ValueError("Missing sender email in env (set SENDER_EMAIL / SMTP_USERNAME / EMAIL_FROM)")
        if not sender_password:
            raise ValueError("Missing sender password in env (set SENDER_PASSWORD / SMTP_PASSWORD)")

        with smtplib.SMTP(smtp_server, smtp_port, timeout=timeout) as server:
            if use_tls:
                server.starttls()
            if login_user and sender_password:
                server.login(login_user, sender_password)
            server.sendmail(email_from, to_list, msg.as_string())

        context_suffix = f"; root_cause={error_context[:200]}" if error_context else ""
        return {
            "timestamp": datetime.now().isoformat(),
            "status": "Success",
            "recipients": ", ".join(to_list),
            "attachment": None,
            "notes": f"Automation-failure stakeholder notification sent{context_suffix}",
        }
    except Exception as exc:
        context_suffix = f"; root_cause={error_context[:200]}" if error_context else ""
        return {
            "timestamp": datetime.now().isoformat(),
            "status": "Failed",
            "recipients": ", ".join(to_list),
            "attachment": None,
            "notes": f"Automation-failure stakeholder notification failed: {str(exc)[:200]}{context_suffix}",
        }

def _send_empty_fallback_email() -> Dict[str, Any]:
    """Send a 'data missing' alert to admin when operator failed to submit data by cutoff."""
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    _load_scheduler_env()

    smtp_server = _get_env_value("SMTP_SERVER", "SMTP_HOST", "MAIL_SERVER", default="smtp.gmail.com")
    smtp_port = int(_get_env_value("SMTP_PORT", default="587"))
    sender_password = _get_env_value("SENDER_PASSWORD", "SMTP_PASSWORD", "MAIL_PASSWORD", "EMAIL_PASSWORD")
    use_tls = _to_bool(_get_env_value("SMTP_USE_TLS", "SMTP_TLS", "SMTP_STARTTLS", default="True"), default=True)
    timeout = int(_get_env_value("SMTP_TIMEOUT", default="10"))
    login_user = _get_env_value("SMTP_USERNAME", "SENDER_EMAIL", "MAIL_USERNAME", "EMAIL_USER")
    email_from = _get_env_value("EMAIL_FROM", "SENDER_EMAIL", "SMTP_USERNAME", "MAIL_FROM", "MAIL_USERNAME")

    config = load_scheduler_config()
    default_to = _get_env_value("DEFAULT_RECIPIENT_EMAIL", "EMAIL_TO", default="")
    to_list = [a.strip() for a in config.get("to", default_to).split(",") if a.strip()]
    cc_list = [a.strip() for a in config.get("cc", "").split(",") if a.strip()]

    today_ist = pd.Timestamp.now(tz=ZoneInfo("Asia/Kolkata")).date()
    previous_day = today_ist - pd.Timedelta(days=1)
    previous_day_str = formatDate(previous_day)

    subject = f"ACTION REQUIRED: Missing Daily Energy Report for {previous_day_str}"
    body_html = f"""
    <html>
    <body style="font-family:Arial,sans-serif;color:#333;line-height:1.6;">
        <h2 style="color:#d9534f;border-bottom:2px solid #d9534f;padding-bottom:5px;">
            Daily Energy Report: Data Missing
        </h2>
        <p>Hello Team,</p>
        <p>The automated system could not generate today's Energy Report.</p>
        <p><strong>Reason:</strong> The facility operator did not log the daily Grid and Diesel
        consumption data for <strong>{previous_day_str}</strong> by the 10:30 AM deadline.</p>
        <p style="background-color:#f9f2f4;padding:10px;border-left:4px solid #d9534f;">
            Please ensure the operator logs this data into the
            <b>Grid &amp; Diesel Log</b> in SharePoint immediately.
        </p>
        <p><i>Automated Notification by EnergyDashboard Agent</i></p>
    </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["From"] = email_from
    msg["To"] = ", ".join(to_list)
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
    msg["Subject"] = subject
    msg.attach(MIMEText(body_html, "html"))

    all_recipients = to_list + cc_list

    try:
        if not email_from:
            raise ValueError("Missing sender email (set SENDER_EMAIL / SMTP_USERNAME / EMAIL_FROM)")
        if not sender_password:
            raise ValueError("Missing sender password (set SENDER_PASSWORD / SMTP_PASSWORD)")

        with smtplib.SMTP(smtp_server, smtp_port, timeout=timeout) as server:
            if use_tls:
                server.starttls()
            if login_user and sender_password:
                server.login(login_user, sender_password)
            server.sendmail(email_from, all_recipients, msg.as_string())

        return {
            "timestamp": datetime.now().isoformat(),
            "status": "Success",
            "recipients": ", ".join(to_list),
            "attachment": None,
            "notes": f"Empty-fallback alert sent for {previous_day_str}",
        }
    except Exception as exc:
        return {
            "timestamp": datetime.now().isoformat(),
            "status": "Failed",
            "recipients": ", ".join(to_list),
            "attachment": None,
            "notes": f"Empty-fallback alert failed: {str(exc)[:200]}",
        }
    
def send_daily_report_email_from_settings(empty_fallback: bool = False) -> Dict[str, Any]:
    """Send the daily report using frontend-configured recipients from scheduler settings.
    
    Args:
        empty_fallback: If True, sends a 'data missing' alert email instead of the full report.
                        Used when the 10:30 AM cutoff is reached and operator never submitted data.
    """
    if empty_fallback:
        return _send_empty_fallback_email()
    return send_email_now()


def _log_daily_check_attempt(date_checked: str, found: bool, action: str, trigger_source: str, notes: str) -> None:
    _append_scheduler_log_entry(
        {
            "timestamp": datetime.now().isoformat(),
            "status": "Check",
            "recipients": "",
            "attachment": None,
            "notes": notes,
            "date_checked": date_checked,
            "found": found,
            "action": action,
            "trigger_source": trigger_source,
        }
    )


def run_daily_report_automation(trigger_source: str = "daily_cron") -> Dict[str, Any]:
    """
    10:30 AM Final Check:
    If data is present -> Run Master Engine to sync -> Send the actual report.
    If data is missing -> Send the empty fallback alert.
    """
    with _automation_lock:
        # Check if the data is there (looks for today's row based on previous fixes)
        FOUND = check_grid_diesel_entry_exists()

        action = ""
        report_result = None

        if FOUND is False:
            # Send the empty fallback email
            report_result = send_daily_report_email_from_settings(empty_fallback=True)
            action = "found_false_empty_fallback_sent"

        if FOUND is True:
            # 1. APPEND HAPPENS HERE: Run master data engine at 10:30 AM
            logger.info("Operator data found. Running Master Data Engine before sending report...")
            _run_master_data_engine()
            
            # 2. EMAIL GENERATES HERE: Send the normal, populated email report
            report_result = send_daily_report_email_from_settings(empty_fallback=False)
            action = "found_true_daily_report_sent"

        _log_daily_check_attempt(
            date_checked=pd.Timestamp.now(tz=ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d"),
            found=FOUND,
            action=action,
            trigger_source=trigger_source,
            notes=report_result.get("notes", "") if report_result else ""
        )

        return {
            "timestamp": datetime.now().isoformat(),
            "trigger_source": trigger_source,
            "found": FOUND,
            "action": action,
            "daily_report": report_result,
        }


def _schedule_data_refresh() -> None:
    """Schedule data refresh at a configurable interval (default 30 minutes)."""
    _ensure_scheduler_started()

    # Use interval trigger for periodic refresh.
    refresh_minutes = int(os.getenv("INGESTION_REFRESH_INTERVAL_MINUTES", "30"))
    if refresh_minutes < 1:
        refresh_minutes = 1

    trigger = IntervalTrigger(minutes=refresh_minutes, timezone=ZoneInfo("Asia/Kolkata"))
    _scheduler.add_job(
        _run_data_refresh,
        trigger=trigger,
        id="data_refresh_interval",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60,
    )
    
    with open(_debug_log_path, 'a', encoding='utf-8') as f:
            f.write(f"Scheduled ingestion data refresh every {refresh_minutes} minute(s) at {datetime.now()}\n")


def _get_sharepoint_target_date(days_ago: int = 1) -> date:
    return pd.Timestamp.now(tz=ZoneInfo("Asia/Kolkata")).date() - pd.Timedelta(days=days_ago)


def _normalize_field_name_for_sharepoint(field_name: Any) -> str:
    text_val = str(field_name or "").replace("_x0020_", " ").replace("_", " ").strip().lower()
    return re.sub(r"\s+", "", text_val)


def _find_field_value(record: Dict[str, Any], candidates: List[str]) -> Any:
    for candidate in candidates:
        if candidate in record and record.get(candidate) not in (None, ""):
            return record.get(candidate)

    normalized_candidates = { _normalize_field_name_for_sharepoint(c): c for c in candidates }
    for key, value in record.items():
        if _normalize_field_name_for_sharepoint(key) in normalized_candidates:
            return value
    return None


def _aggregate_suryalogix_daily_data(target_date: date) -> Dict[str, Any]:
    try:
        from .surya_logix_api_service import SuryaLogixAPIService
        service = SuryaLogixAPIService()
        daily_df = service.get_daily_generation(target_date.isoformat())
    except Exception as exc:
        logger.error(f"SuryaLogix daily aggregation failed for {target_date}: {exc}")
        return {
            "solar_kwh": 0.0,
            "ambient_temperature": None,
            "error": str(exc),
        }

    if daily_df is None or daily_df.empty:
        return {"solar_kwh": 0.0, "ambient_temperature": None, "error": None}

    energy_col = None
    for col in daily_df.columns:
        if col.lower().replace(" ", "") in {"energy(kwh)", "energy", "daygeneration(kwh)"}:
            energy_col = col
            break

    solar_kwh = float(daily_df[energy_col].sum()) if energy_col and energy_col in daily_df.columns else 0.0
    ambient_temperature = None
    for temp_col in ["Ambient Temperature (°C)", "Temperature (°C)", "Temperature_C", "Temperature"]:
        if temp_col in daily_df.columns:
            ambient_temperature = float(pd.to_numeric(daily_df[temp_col], errors="coerce").mean())
            break

    return {
        "solar_kwh": solar_kwh,
        "ambient_temperature": ambient_temperature,
        "error": None,
    }


def _create_or_update_sharepoint_unified_row(target_date: date, field_values: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from .sharepoint_list_data_service import SharePointListDataService
        sp_service = SharePointListDataService()
    except Exception as exc:
        logger.error(f"Unable to initialize SharePoint service: {exc}")
        return {"status": "Failed", "error": str(exc)}

    existing_item = sp_service.get_list_item_by_date(SHAREPOINT_UNIFIED_MASTER_LIST_NAME, target_date)
    if existing_item and existing_item.get("id"):
        updated = sp_service.update_list_item(SHAREPOINT_UNIFIED_MASTER_LIST_NAME, existing_item.get("id"), field_values)
        if updated is not None:
            return {"status": "Updated", "item_id": updated.get("id"), "action": "updated"}

    created = sp_service.create_list_item(SHAREPOINT_UNIFIED_MASTER_LIST_NAME, field_values)
    if created is not None:
        return {"status": "Created", "item_id": created.get("id"), "action": "created"}

    return {"status": "Failed", "error": sp_service.get_last_error()}


def _run_sharepoint_morning_master_sync() -> Dict[str, Any]:
    """
    Morning job to sync manual ops data and solar data into the unified master list.
    Triggers a global fallback notification on any read or write failure.
    """
    try:
        target_date = _get_sharepoint_target_date(1)
        from .sharepoint_list_data_service import SharePointListDataService
        sp_service = SharePointListDataService()
        
        entry = sp_service.get_list_item_by_date(SHAREPOINT_OPS_MANUAL_LIST_NAME, target_date)
        if not entry:
            logger.warning(f"No Ops_Manual_Entry record found for {target_date}")
            return {"status": "Skipped", "reason": "Ops entry missing", "date": target_date.isoformat()}

        grid_consumed = _find_field_value(entry, ["Grid Units Consumed", "GridUnitsConsumed", "Units_Consumed_kWh", "Grid Units Consumed (KWh)"])
        solar_data = _aggregate_suryalogix_daily_data(target_date)

        field_values = {
            "Date": target_date.isoformat(),
            "Solar Units Consumed": solar_data.get("solar_kwh", 0.0),
            "Ambient Temperature °C": solar_data.get("ambient_temperature") or "",
        }
        if grid_consumed is not None:
            field_values["Grid Units Consumed"] = grid_consumed

        result = _create_or_update_sharepoint_unified_row(target_date, field_values)
        logger.info(f"SharePoint morning master sync result for {target_date}: {result}")
        return result

    except Exception as exc:
        logger.error(f"Morning sync failed: {exc}")
        # Trigger the global fallback notification
        send_stakeholder_failure_notification(error_context=f"Morning Master Sync Failure: {str(exc)}")
        return {"status": "Failed", "error": str(exc)}


def _run_sharepoint_eod_solar_aggregation() -> Dict[str, Any]:
    """
    End-of-day job to aggregate solar data.
    Triggers a global fallback notification on any API or SharePoint failure.
    """
    try:
        target_date = _get_sharepoint_target_date(1)
        solar_data = _aggregate_suryalogix_daily_data(target_date)
        
        field_values = {
            "Date": target_date.isoformat(),
            "Solar Units Consumed": solar_data.get("solar_kwh", 0.0),
            "Ambient Temperature °C": solar_data.get("ambient_temperature") or "",
        }
        
        result = _create_or_update_sharepoint_unified_row(target_date, field_values)
        logger.info(f"SharePoint EOD solar aggregation result for {target_date}: {result}")
        return result

    except Exception as exc:
        logger.error(f"EOD solar aggregation failed: {exc}")
        # Trigger the global fallback notification
        send_stakeholder_failure_notification(error_context=f"EOD Solar Aggregation Failure: {str(exc)}")
        return {"status": "Failed", "error": str(exc)}


def _run_master_data_engine() -> Dict[str, Any]:
    """
    Job that aggregates grid_and_diesel.xlsx + UnifiedSolarData.xlsx 
    into Master-data.xlsx on SharePoint.
    """
    try:
        from app.agents.master_data_engine import process_master_data
        
        # FIX: Use 0 to process TODAY'S row (where the operator put the data)
        target_date = _get_sharepoint_target_date(0)   
        target_date_str = target_date.strftime("%Y-%m-%d")
        
        logger.info(f"Master data engine running for date: {target_date_str}")
        process_master_data(target_date_str)
        logger.info(f"Master data engine completed for {target_date_str}")
        
        return {"status": "Success", "date": target_date_str}

    except Exception as exc:
        logger.error(f"Master data engine failed: {exc}")
        send_stakeholder_failure_notification(error_context=f"Master Data Engine Failure: {str(exc)}")
        return {"status": "Failed", "error": str(exc)[:300]}
    
def _schedule_sharepoint_jobs() -> None:
    _ensure_scheduler_started()
    morning_hour, morning_minute = _validate_send_time(SHAREPOINT_MORNING_CRON_TIME)
    eod_hour, eod_minute = _validate_send_time(SHAREPOINT_EOD_CRON_TIME)

    _scheduler.add_job(
        _run_sharepoint_morning_master_sync,
        trigger=CronTrigger(hour=morning_hour, minute=morning_minute, timezone=ZoneInfo("Asia/Kolkata")),
        id=SHAREPOINT_MORNING_JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=1800,
    )

    _scheduler.add_job(
        _run_sharepoint_eod_solar_aggregation,
        trigger=CronTrigger(hour=eod_hour, minute=eod_minute, timezone=ZoneInfo("Asia/Kolkata")),
        id=SHAREPOINT_EOD_JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=1800,
    )

# def _schedule_master_data_engine() -> None:
#     """Schedule the nightly master data aggregation job."""
#     _ensure_scheduler_started()
#     hour, minute = _validate_send_time(MASTER_DATA_ENGINE_CRON_TIME)
#     _scheduler.add_job(
#         _run_master_data_engine,
#         trigger=CronTrigger(hour=hour, minute=minute, timezone=ZoneInfo("Asia/Kolkata")),
#         id=MASTER_DATA_ENGINE_JOB_ID,
#         replace_existing=True,
#         max_instances=1,
#         coalesce=True,
#         misfire_grace_time=1800,
#     )

def _schedule_daily_job(send_time: str) -> None:
    """
    Schedules the main daily report and the proactive operator reminders.
    """
    _ensure_scheduler_started()

    # 1. Schedule the Main Daily Report (10:30 AM)
    # This uses the DAILY_REPORT_CRON_TIME constant defined in your script
    hour, minute = _validate_send_time(DAILY_REPORT_CRON_TIME)
    
    _scheduler.add_job(
        run_daily_report_automation,
        trigger=CronTrigger(hour=hour, minute=minute, timezone=ZoneInfo("Asia/Kolkata")),
        id=SCHEDULER_JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=1800
    )

    # 2. Schedule Operator Reminders (9:00 AM, 9:30 AM, 10:00 AM)
    # These jobs will check if data is missing and email Ojas
    _scheduler.add_job(
        _run_operator_reminder_cycle,
        trigger=CronTrigger(hour=9, minute="0,30", timezone=ZoneInfo("Asia/Kolkata")),
        id="operator_reminder_9am_930am",
        replace_existing=True,
        max_instances=1,
        coalesce=True
    )

    _scheduler.add_job(
        _run_operator_reminder_cycle,
        trigger=CronTrigger(hour=10, minute=0, timezone=ZoneInfo("Asia/Kolkata")),
        id="operator_reminder_10am",
        replace_existing=True,
        max_instances=1,
        coalesce=True
    )
    
    logger.info(f"Daily report and operator reminders scheduled successfully.")


def _run_data_refresh() -> None:
    """Background task to refresh ingestion data and cache layers."""
    try:
        from .data_refresh_service import DataRefreshService
        
        result = DataRefreshService.refresh_all_data()
        
        with open(_debug_log_path, 'a', encoding='utf-8') as f:
            f.write(f"Data refresh at {result['timestamp']}: {len(result['successful'])} successful, {len(result['failed'])} failed\n")
            if result['errors']:
                f.write(f"  Errors: {result['errors']}\n")
    
    except Exception as e:
        with open(_debug_log_path, 'a', encoding='utf-8') as f:
            f.write(f"Error in data refresh task: {e}\n")


def initialize_scheduler_from_config() -> None:
    """Initialize scheduler from persisted config when API starts."""
    _run_data_refresh()
    _schedule_data_refresh()
    
    cfg = load_scheduler_config()
    if cfg.get("auto_start", False):
        try:
            _schedule_daily_job(DAILY_REPORT_CRON_TIME)
            _schedule_sharepoint_jobs()
            # REMOVED: _schedule_master_data_engine() <-- Delete this line!
        except Exception as exc:
            with open(_debug_log_path, 'a', encoding='utf-8') as f:
                f.write(f"Scheduler auto-start failed: {exc}\n")


def load_scheduler_config() -> Dict[str, Any]:
    """Load scheduler configuration"""
    if SCHEDULER_CONFIG_FILE.exists():
        with open(SCHEDULER_CONFIG_FILE, 'r') as f:
            return json.load(f)
    else:
        # Return default config
        return {
            "to": "",
            "cc": "",
            "send_time": "10:30",
            "subject": "Review Noida Daily Energy Optimization — {date}",
            "custom_message": "",
            "auto_start": False,
            "include_sections": {
                "summary_kpis": True,
                "unified_table": True,
                "grid_summary": True,
                "solar_summary": True,
                "diesel_summary": True,
                "inverter_status": True,
                "raw_data": False
            },
            "uploaded_template_path": None
        }


def save_scheduler_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Save scheduler configuration"""
    config["send_time"] = DAILY_REPORT_CRON_TIME
    with open(SCHEDULER_CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)

    # If scheduler is already active, immediately apply updated send_time.
    if _scheduler.get_job(SCHEDULER_JOB_ID) is not None:
        _schedule_daily_job(DAILY_REPORT_CRON_TIME)

    # Respect auto_start preference for future startup and current runtime.
    if config.get("auto_start", False) and _scheduler.get_job(SCHEDULER_JOB_ID) is None:
        _schedule_daily_job(DAILY_REPORT_CRON_TIME)
    if not config.get("auto_start", False) and _scheduler.get_job(SCHEDULER_JOB_ID) is not None:
        _scheduler.remove_job(SCHEDULER_JOB_ID)

    return config


def get_scheduler_status() -> Dict[str, Any]:
    """Get scheduler status"""
    job = _scheduler.get_job(SCHEDULER_JOB_ID)
    retry_job = _scheduler.get_job(RETRY_JOB_ID)
    next_run_time = job.next_run_time if job else None
    retry_next_run = retry_job.next_run_time if retry_job else None
    morning_job = _scheduler.get_job(SHAREPOINT_MORNING_JOB_ID)
    eod_job = _scheduler.get_job(SHAREPOINT_EOD_JOB_ID)
    morning_next_run = morning_job.next_run_time if morning_job else None
    eod_next_run = eod_job.next_run_time if eod_job else None
    history = load_scheduler_history(limit=1)
    return {
        "status": "running" if job else "stopped",
        "next_run": next_run_time.isoformat() if next_run_time else None,
        "retry_loop_active": retry_job is not None,
        "retry_next_run": retry_next_run.isoformat() if retry_next_run else None,
        "sharepoint_morning_next_run": morning_next_run.isoformat() if morning_next_run else None,
        "sharepoint_eod_next_run": eod_next_run.isoformat() if eod_job else None,
        "last_run": history[0] if history else None,
    }


def start_scheduler(send_time: str) -> Dict[str, Any]:
    """Start the scheduler"""
    _ = send_time
    _schedule_daily_job(DAILY_REPORT_CRON_TIME)
    _schedule_sharepoint_jobs()
    # REMOVED: _schedule_master_data_engine() <-- Delete this line!

    # Persist chosen time so UI refresh reflects latest schedule.
    config = load_scheduler_config()
    config["send_time"] = DAILY_REPORT_CRON_TIME
    config["auto_start"] = True
    with open(SCHEDULER_CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)

    job = _scheduler.get_job(SCHEDULER_JOB_ID)
    next_run_time = job.next_run_time if job else None
    return {
        "status": "running",
        "next_run": next_run_time.isoformat() if next_run_time else None,
    }

def stop_scheduler() -> Dict[str, Any]:
    """Stop the scheduler"""
    for job_id in [SCHEDULER_JOB_ID, RETRY_JOB_ID, SHAREPOINT_MORNING_JOB_ID, SHAREPOINT_EOD_JOB_ID, MASTER_DATA_ENGINE_JOB_ID]:
        if _scheduler.get_job(job_id) is not None:
            _scheduler.remove_job(job_id)

    config = load_scheduler_config()
    config["auto_start"] = False
    with open(SCHEDULER_CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)

    return {
        "status": "stopped"
    }

def build_energy_report_html(config: Dict[str, Any]) -> Tuple:
    """Build HTML body and CSV content for Energy Consumption Report format.
    Returns: (html_content, csv_content)
    """
    import json as _json

    def _dbg(msg: str) -> None:
        with open(_debug_log_path, 'a', encoding='utf-8') as _f:
            _f.write(f"[build_energy_report_html] {msg}\n")

    try:
        from .sharepoint_list_data_service import SharePointListDataService as _SPService
        import pandas as pd
        import re
        import html as html_lib
        from zoneinfo import ZoneInfo

        def _num(value: Any, default: float = 0.0) -> float:
            if value is None or value == "": return default
            try: return float(str(value).replace(",", "").strip())
            except Exception:
                match = re.search(r"[-+]?\d*\.?\d+", str(value))
                return float(match.group(0)) if match else default

        def normalizeIssueText(value: Any) -> str:
            if value is None: return "No issues"
            text = str(value).strip()
            if not text: return "No issues"
            return text.lower()[:1].upper() + text.lower()[1:]

        def _format_en_in(value: float, decimals: int) -> str:
            rounded = f"{abs(value):.{decimals}f}"
            whole, frac = rounded.split(".") if "." in rounded else (rounded, "")
            if len(whole) > 3:
                last_three = whole[-3:]
                lead = whole[:-3]
                groups = []
                while len(lead) > 2:
                    groups.insert(0, lead[-2:])
                    lead = lead[:-2]
                if lead: groups.insert(0, lead)
                whole = ",".join(groups + [last_three])
            sign = "-" if value < 0 else ""
            return f"{sign}{whole}.{frac}" if decimals > 0 else f"{sign}{whole}"

        def _parse_to_ist_date(series: pd.Series) -> pd.Series:
            """
            Robustly parse a date/datetime column to IST date.
            Handles: UTC ISO strings, naive ISO strings, plain date strings,
                      Excel serial numbers, and mixed formats.
            """
            IST = ZoneInfo("Asia/Kolkata")

            # Try 1: UTC-aware parse (e.g. "2026-04-08T00:00:00Z")
            attempt = pd.to_datetime(series, errors="coerce", utc=True)
            if attempt.notna().any():
                result = attempt.dt.tz_convert(IST)
                _dbg(f"  _parse_to_ist_date | UTC-aware parse succeeded for {attempt.notna().sum()}/{len(series)} rows")
                return result

            # Try 2: Naive parse then localize to IST (e.g. "2026-04-08T00:00:00")
            attempt2 = pd.to_datetime(series, errors="coerce")
            if attempt2.notna().any():
                result = attempt2.dt.tz_localize(IST)
                _dbg(f"  _parse_to_ist_date | Naive→IST parse succeeded for {attempt2.notna().sum()}/{len(series)} rows")
                return result

            # Try 3: dayfirst for DD/MM/YYYY or DD-MM-YYYY
            attempt3 = pd.to_datetime(series, errors="coerce", dayfirst=True)
            if attempt3.notna().any():
                result = attempt3.dt.tz_localize(IST)
                _dbg(f"  _parse_to_ist_date | dayfirst parse succeeded for {attempt3.notna().sum()}/{len(series)} rows")
                return result

            _dbg(f"  _parse_to_ist_date | WARNING: All parse attempts failed for series sample: {series.head(3).tolist()}")
            return pd.Series([pd.NaT] * len(series))

        # ── STEP 1: Target date ───────────────────────────────────────────────
        IST = ZoneInfo("Asia/Kolkata")
        report_end_ts = pd.Timestamp.now(tz=IST).normalize()
        previous_day_cutoff = report_end_ts.date()
        _dbg(f"STEP 1 | Target date (yesterday IST): {previous_day_cutoff}")

        # ── STEP 2: Fetch from SharePoint Excel Folder ────────────────────────
        _dbg("STEP 2 | Fetching master data from SharePoint Excel...")
        
        try:
            # Import the Excel service instead of the List service
            from .sharepoint_data_service import get_service as get_excel_service
            
            sp_excel_service = get_excel_service()
            _master_df = sp_excel_service.fetch_sheet_data("master_data")
            
        except Exception as e:
            _dbg(f"STEP 2 | ERROR: Failed to fetch Excel from SharePoint. {e}")
            raise RuntimeError(f"Could not load Excel data: {e}")

        if _master_df is None or _master_df.empty:
            _dbg("STEP 2 | ERROR: Excel file is empty or could not be reached.")
            raise RuntimeError("No master data rows returned from Excel.")

        _dbg(f"STEP 2 | Rows: {len(_master_df)} | Columns: {list(_master_df.columns)}")

        raw_df = _master_df.copy()
        # ── STEP 3: Dump raw columns + first row ──────────────────────────────
        _dbg(f"STEP 3 | All column names: {list(raw_df.columns)}")
        if not raw_df.empty:
            _dbg(f"STEP 3 | First row:\n{_json.dumps({k: str(v)[:120] for k, v in raw_df.iloc[0].items()}, ensure_ascii=False, indent=2)}")

        # ── STEP 4: Auto-detect date column ───────────────────────────────────
        DATE_CANDIDATES = ["field_0", "Date", "date", "Timestamp", "timestamp", "Created", "created"]
        date_col = next((c for c in DATE_CANDIDATES if c in raw_df.columns), None)

        # Fallback: find any column whose name contains "date" (case-insensitive)
        if date_col is None:
            date_col = next(
                (c for c in raw_df.columns if "date" in c.lower() or "time" in c.lower()),
                None
            )

        _dbg(f"STEP 4 | Date column selected: {date_col!r} (from candidates {DATE_CANDIDATES})")
        if date_col is None:
            _dbg(f"STEP 4 | FATAL: No date column found. All columns: {list(raw_df.columns)}")
            raise RuntimeError(f"Could not find Date column. Available: {list(raw_df.columns)}")

        # ── STEP 5: Parse dates and filter ────────────────────────────────────
        _dbg(f"STEP 5 | Raw values in '{date_col}' (first 5): {raw_df[date_col].head(5).tolist()}")

        raw_df["_date_sort"] = _parse_to_ist_date(raw_df[date_col])

        valid_df = raw_df[raw_df["_date_sort"].notna()].copy()
        _dbg(f"STEP 5 | Rows with valid parsed date: {len(valid_df)}/{len(raw_df)}")

        # Extract just the date component to easily match days
        valid_df["_just_date"] = valid_df["_date_sort"].dt.date

        # Generate a perfect 30-day calendar ending on the target date (Today)
        calendar_dates = [report_end_ts.date() - pd.Timedelta(days=i) for i in range(30)]
        calendar_df = pd.DataFrame({"_just_date": calendar_dates})

        # Merge SharePoint data into the perfect 30-day calendar
        # This guarantees 30 rows. Missing SharePoint days will be left blank.
        merged_df = pd.merge(calendar_df, valid_df, on="_just_date", how="left")
        
        # If there are duplicate entries for the same day, keep the first one
        merged_df = merged_df.drop_duplicates(subset=["_just_date"], keep="first")

        raw_df = merged_df
        _dbg(f"STEP 5 | Using {len(raw_df)} rows for the guaranteed 30-day window.")

        # ── STEP 6: Map fields → display columns ──────────────────────────────
        chosen_row_raw = raw_df.iloc[0].to_dict()
        _dbg(f"STEP 6 | All fields in chosen row:\n{_json.dumps({k: str(v)[:120] for k, v in chosen_row_raw.items()}, ensure_ascii=False, indent=2)}")

        display_dict = []
        for _, row in raw_df.iterrows():
            safe_row = row.fillna("")

            true_date    = pd.to_datetime(row["_just_date"])
            display_date = true_date.strftime("%d-%b-%Y")
            display_day  = true_date.strftime("%A")

            # --- FIX: Force HH:MM format for EVERY row ---
            raw_time = safe_row.get("Time", "")
            try:
                # If it's a valid time/datetime, convert to HH:MM
                clean_time = pd.to_datetime(raw_time).strftime("%H:%M") if raw_time else ""
            except:
                # Fallback: if it's already a string like "09:00:00", just take the first 5 chars
                clean_time = str(raw_time).strip()[:5]

            new_row = {
                "Date":                                    display_date,
                "Day":                                     display_day,
                "Time":                                    clean_time, # <--- Use the cleaned version
                "Ambient Temperature (°C)":                safe_row.get("Ambient Temperature °C", ""),
                "Grid Units Consumed (kWh)":               safe_row.get("Grid Units Consumed (KWh)", ""),
                "Solar Units Consumed (kWh)":              safe_row.get("Solar Units Consumed(KWh)", ""),
                "Total Units Consumed (kWh)":              safe_row.get("Total Units Consumed (KWh)", ""),
                "Total Cost (INR)":                        safe_row.get("Total Units Consumed in INR", ""),
                "Solar Cost Savings (INR)":                safe_row.get("Energy Saving in INR", ""),
                "Panels Cleaned":                          safe_row.get("Number of Panels Cleaned", ""),
                "Diesel Consumed (Litres)":                safe_row.get("Diesel consumed", ""),
                "Water Treated through STP (kilo Litres)": safe_row.get("Water treated through STP", ""),
                "Water Treated through WTP (kilo Litres)": safe_row.get("Water treated through WTP", ""),
                "Issues":                                  safe_row.get("Issues", ""),
            }

            display_dict.append(new_row)

        display_df = pd.DataFrame(display_dict)

        requested_columns = [
            "Date", "Day", "Time", "Ambient Temperature (°C)", "Grid Units Consumed (kWh)",
            "Solar Units Consumed (kWh)", "Total Units Consumed (kWh)", "Total Cost (INR)",
            "Solar Cost Savings (INR)", "Panels Cleaned", "Diesel Consumed (Litres)",
            "Water Treated through STP (kilo Litres)", "Water Treated through WTP (kilo Litres)", "Issues"
        ]
        display_df = display_df.reindex(columns=requested_columns)

        empty_cols = [c for c in display_df.columns if display_df[c].replace("", pd.NA).isna().all()]
        _dbg(f"STEP 6 | Columns entirely empty after mapping: {empty_cols}")

        # ── STEP 7: CSV ───────────────────────────────────────────────────────
        csv_content = display_df.to_csv(index=False)
        _dbg(f"STEP 7 | CSV generated ({len(csv_content)} chars)")

        # ── STEP 8: HTML table ────────────────────────────────────────────────
        right_aligned_columns = {
            "Ambient Temperature (°C)", "Grid Units Consumed (kWh)", "Solar Units Consumed (kWh)",
            "Total Units Consumed (kWh)", "Total Cost (INR)", "Solar Cost Savings (INR)",
            "Panels Cleaned", "Diesel Consumed (Litres)",
            "Water Treated through STP (kilo Litres)", "Water Treated through WTP (kilo Litres)"
        }
        decimals_by_column = {
            "Grid Units Consumed (kWh)": 0, "Solar Units Consumed (kWh)": 0, "Total Units Consumed (kWh)": 0,
            "Total Cost (INR)": 2, "Solar Cost Savings (INR)": 2, "Panels Cleaned": 0,
            "Diesel Consumed (Litres)": 0, "Water Treated through STP (kilo Litres)": 0,
            "Water Treated through WTP (kilo Litres)": 0,
        }

        table_parts = [
            '<div style="overflow-x:auto; width:100%; max-width:100%;">',
            '<table style="border-collapse:collapse; width:100%; min-width:1000px; font-family:Arial, Helvetica, sans-serif; font-size:12px; color:#1e293b;">',
            '<thead><tr style="background-color:#1E3A5F; color:#ffffff; font-size:12px;">',
        ]
        for col in display_df.columns:
            align = "right" if col in right_aligned_columns else "left"
            table_parts.append(f'<th style="padding:8px 10px; text-align:{align};">{html_lib.escape(str(col))}</th>')
        table_parts.append('</tr></thead><tbody>')

        for idx, (_, row) in enumerate(display_df.iterrows()):
            bg = "#ffffff" if idx % 2 == 0 else "#f8fafc"
            table_parts.append(f'<tr style="background-color:{bg}; font-size:12px;">')
            for col in display_df.columns:
                value = row.get(col, "")
                if pd.isna(value) or value == "":
                    text = "-"
                elif col == "Date":
                    text = str(value)
                elif col == "Ambient Temperature (°C)":
                    raw_ambient = str(value).strip()
                    if raw_ambient in ("", "-"): text = "0"
                    else:
                        try: text = _format_en_in(float(raw_ambient.replace(",", "")), 0)
                        except Exception: text = raw_ambient
                elif col == "Issues":
                    text = normalizeIssueText(value)
                elif col in decimals_by_column:
                    text = _format_en_in(_num(value, 0.0), decimals_by_column[col])
                else:
                    text = str(value)

                align = "right" if col in right_aligned_columns else "left"
                num_style = "font-variant-numeric:tabular-nums;" if col in right_aligned_columns else ""
                table_parts.append(
                    f'<td style="padding:7px 10px; border-bottom:1px solid #e2e8f0; text-align:{align}; {num_style}">'
                    f'{html_lib.escape(text)}</td>'
                )
            table_parts.append('</tr>')

        table_parts.append(
            f'<tr><td colspan="14" style="padding:8px 10px; font-size:11px; color:#94a3b8; text-align:center; '
            f'border-top:1px solid #e2e8f0; background-color:#f8fafc;">'
            f'Showing {len(display_df)} records &nbsp;|&nbsp; Generated by Energy Optimization Agent &nbsp;|&nbsp; '
            f'Noida Campus &nbsp;|&nbsp; Do not reply</td></tr>'
        )
        table_parts.append('</tbody></table></div>')
        table_html = "\n".join(table_parts)

        custom_message = html_lib.escape(config.get('custom_message', '') or '')
        current_date_display = display_df.iloc[0]["Date"]
        _dbg(f"STEP 8 | HTML built. Date shown in header: {current_date_display!r}")

        html = f"""
        <html>
            <body style="margin:0; padding:0; background:#f2f3f5; font-family:Segoe UI, Helvetica Neue, Arial, sans-serif; font-size:13px;">
                <table width="100%" cellpadding="0" cellspacing="0" style="padding:18px 0; background:#f2f3f5;">
                    <tr>
                        <td align="center">
                            <table width="99%" cellpadding="0" cellspacing="0" style="max-width:1460px; border:1px solid #d9d9d9; background:#ffffff;">
                                <tr>
                                    <td style="background:#233f70; color:#ffffff; padding:14px 26px;">
                                        <div style="display:inline-block; vertical-align:middle; font-size:32px; font-weight:700; line-height:1.2;">Energy Consumption Report</div>
                                        <div style="font-size:20px; margin-top:6px; opacity:0.95;">Report Date: {current_date_display} - Auto-generated by Energy Agent</div>
                                    </td>
                                </tr>
                                <tr>
                                    <td style="padding:18px 24px 8px 24px; color:#223b63; font-weight:700; font-size:20px;">30-Day Data Log</td>
                                </tr>
                                <tr>
                                    <td style="padding:0 24px 20px 24px;">
                                        {table_html}
                                    </td>
                                </tr>
                                {f'<tr><td style="padding:0 24px 18px 24px; font-size:13px; color:#555;">{custom_message}</td></tr>' if custom_message else ''}
                                <tr>
                                    <td style="background:#f0f0f0; padding:14px 24px; text-align:center; color:#7a7a7a; font-size:13px; border-top:1px solid #dddddd;">Generated by Energy Optimization Agent | Noida Campus | Do not reply</td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                </table>
            </body>
        </html>
        """

        _dbg("STEP 8 | Done. Returning html + csv.")
        return html, csv_content

    except Exception as e:
        _dbg(f"EXCEPTION | {type(e).__name__}: {e}")
        raise RuntimeError(f"Could not build daily report email body: {str(e)[:200]}") from e
    
    
def send_email_now() -> Dict[str, Any]:
    """Send email immediately with Energy Report and CSV attachment"""
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    from email.mime.base import MIMEBase
    from email import encoders
    from datetime import datetime

    with open(_debug_log_path, 'a', encoding='utf-8') as f:
        f.write("send_email_now called\n")

    config = load_scheduler_config()

    try:
        # Reload env so latest credentials apply without restart.
        _load_scheduler_env()

        # Accept both legacy and .env.example naming styles.
        smtp_server = _get_env_value("SMTP_SERVER", "SMTP_HOST", "MAIL_SERVER", default="smtp.gmail.com")
        smtp_port = int(_get_env_value("SMTP_PORT", default="587"))
        sender_email = _get_env_value("SENDER_EMAIL", "SMTP_USERNAME", "EMAIL_FROM", "MAIL_USERNAME", "EMAIL_USER")
        sender_password = _get_env_value("SENDER_PASSWORD", "SMTP_PASSWORD", "MAIL_PASSWORD", "EMAIL_PASSWORD")
        use_tls = _to_bool(_get_env_value("SMTP_USE_TLS", "SMTP_TLS", "SMTP_STARTTLS", default="True"), default=True)
        timeout = int(_get_env_value("SMTP_TIMEOUT", default="10"))
        login_user = _get_env_value("SMTP_USERNAME", "SENDER_EMAIL", "MAIL_USERNAME", "EMAIL_USER", default=sender_email)
        email_from = _get_env_value("EMAIL_FROM", "SENDER_EMAIL", "SMTP_USERNAME", "MAIL_FROM", "MAIL_USERNAME", default=sender_email)

        if not email_from:
            raise ValueError("Missing sender email in env (set SENDER_EMAIL / SMTP_USERNAME / EMAIL_FROM)")

        if not sender_password:
            raise ValueError("Missing sender password in env (set SENDER_PASSWORD / SMTP_PASSWORD)")

        # Get recipient from config or env
        default_to = _get_env_value("DEFAULT_RECIPIENT_EMAIL", "EMAIL_TO", "DEFAULT_TO", default="")
        to_list = [addr.strip() for addr in config.get("to", default_to).split(",") if addr.strip()]
        
        # FIX: Define the default_cc FIRST, then use it as the fallback in config.get()
        default_cc = _get_env_value("CC_EMAIL", default="")
        cc_list = [addr.strip() for addr in config.get("cc", default_cc).split(",") if addr.strip()]
        
        if not to_list:
            raise ValueError("No recipient email address configured")

        # Build the Energy Report HTML and CSV
        html_body, csv_content = build_energy_report_html(config)

        # Create email message with mixed content (HTML + attachment)
        msg = MIMEMultipart("mixed")
        msg["From"] = email_from
        msg["To"] = ", ".join(to_list)
        if cc_list:
            msg["Cc"] = ", ".join(cc_list)

        report_date = pd.Timestamp.now(tz=ZoneInfo("Asia/Kolkata")).date()
        subject = f"Daily Energy Report - Noida Campus - {formatDate(report_date)}"
        msg["Subject"] = subject

        # Create alternative part for HTML
        msg_alternative = MIMEMultipart("alternative")
        msg.attach(msg_alternative)

        # Attach HTML body
        msg_alternative.attach(MIMEText(html_body, "html"))

        uploaded_template_path = config.get("uploaded_template_path")
        attachment_name = None

        if uploaded_template_path and Path(uploaded_template_path).exists():
            attachment_name = Path(uploaded_template_path).name
            with open(uploaded_template_path, "rb") as f:
                attachment_bytes = f.read()
        else:
            attachment_name = f"Energy_Report_{datetime.now().strftime('%d%m%Y')}.csv"
            attachment_bytes = csv_content.encode('utf-8')

        attachment = MIMEBase("application", "octet-stream")
        attachment.set_payload(attachment_bytes)
        encoders.encode_base64(attachment)
        attachment.add_header("Content-Disposition", f"attachment; filename= {attachment_name}")
        msg.attach(attachment)

        # Connect and send
        all_recipients = to_list + cc_list

        print(f"[DEBUG] Connecting to {smtp_server}:{smtp_port}")
        with smtplib.SMTP(smtp_server, smtp_port, timeout=timeout) as server:
            if use_tls:
                server.starttls()

            if login_user and sender_password:
                server.login(login_user, sender_password)

            server.sendmail(email_from, all_recipients, msg.as_string())

        # Log successful send
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "status": "Success",
            "recipients": ", ".join(to_list),
            "attachment": attachment_name,
            "notes": f"Email sent successfully to {', '.join(to_list)} with HTML report and attachment"
        }
        print(f"[DEBUG] Log Entry Attac: {log_entry['attachment']}", flush=True)

        logs = load_scheduler_history()
        logs.insert(0, log_entry)
        logs = logs[:50]

        with open(SCHEDULER_LOG_FILE, 'w') as f:
            json.dump(logs, f, indent=2)

        return log_entry

    except Exception as e:
        failure_notification = send_stakeholder_failure_notification(error_context=str(e))
        failure_notification_status = failure_notification.get("status", "Unknown")

        # Log failed send
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "status": "Failed",
            "recipients": config.get("to", ""),
            "attachment": None,
            "notes": (
                f"Error: {str(e)[:150]}; "
                f"automation_failure_notification={failure_notification_status}"
            ),
            "failure_notification": failure_notification,
        }

        logs = load_scheduler_history()
        logs.insert(0, log_entry)
        logs = logs[:50]

        with open(SCHEDULER_LOG_FILE, 'w') as f:
            json.dump(logs, f, indent=2)

        return log_entry


def load_scheduler_history(limit: int = 10) -> list:
    """Load scheduler history"""
    if SCHEDULER_LOG_FILE.exists():
        with open(SCHEDULER_LOG_FILE, 'r') as f:
            logs = json.load(f)
            return logs[:limit]
    return []


def upload_template(file_path: str) -> Dict[str, Any]:
    """Handle template upload"""
    config = load_scheduler_config()
    config["uploaded_template_path"] = file_path

    save_scheduler_config(config)

    return {
        "filename": Path(file_path).name,
        "path": file_path,
        "uploaded_at": datetime.now().isoformat()
    }

def check_grid_diesel_entry_exists() -> bool:
    """Check if data exists for TODAY in the grid_and_diesel Excel file."""
    try:
        from .sharepoint_data_service import get_service as get_excel_service
        import pandas as pd
        from zoneinfo import ZoneInfo
        
        sp_excel_service = get_excel_service()
        df = sp_excel_service.fetch_sheet_data("grid_and_diesel")
        
        if df is None or df.empty:
            print("[SCHEDULER DEBUG] ❌ Excel file is empty or could not be loaded!")
            return False

        # --- THE HEADER HUNTER ---
        if any("Unnamed" in str(c) for c in df.columns):
            print("[SCHEDULER DEBUG] ⚠️ Detected 'Unnamed' columns. Hunting for the real headers...")
            for i, row in df.head(10).iterrows():
                if any("date" in str(val).lower() for val in row.values):
                    df.columns = row.values
                    df = df.iloc[i+1:].reset_index(drop=True)
                    print(f"[SCHEDULER DEBUG] ✅ Found real headers on row {i+2} and fixed the table!")
                    break
        # -------------------------
            
        IST = ZoneInfo("Asia/Kolkata")
        today = pd.Timestamp.now(tz=IST).date()
        
        # 1. Dynamically find the date column
        date_col = next((c for c in df.columns if "date" in str(c).lower()), None)
        
        if not date_col:
            print(f"[SCHEDULER DEBUG] ❌ CRITICAL: No date column found! I only see: {list(df.columns)}")
            return False
            
        print(f"[SCHEDULER DEBUG] ✅ Found date column named: '{date_col}'")
            
        # 2. Parse dates SAFELY
        # First, let Pandas convert it directly (This instantly handles the perfect Date objects from Excel)
        parsed_dates = pd.to_datetime(df[date_col], errors="coerce")
        
        # For any rows that failed (because they are just text), clean them and try our specific formats
        if parsed_dates.isna().any():
            fallback_str = df[date_col].astype(str).str.strip()
            
            # Try Ojas's exact format
            ojas_dates = pd.to_datetime(fallback_str, format="%d-%b-%y", errors="coerce")
            parsed_dates = parsed_dates.fillna(ojas_dates)
            
            # Try standard dayfirst fallback
            general_dates = pd.to_datetime(fallback_str, errors="coerce", dayfirst=True)
            parsed_dates = parsed_dates.fillna(general_dates)
            
        df["_parsed_date"] = parsed_dates.dt.date
        
        # 3. Check for today
        if not df[df["_parsed_date"] == today].empty:
            print(f"[SCHEDULER DEBUG] ✅ SUCCESS! Found operator data for: {today}")
            return True
            
        # 4. If it fails, print the parsed dates so we can see what it converted them to
        top_3 = df["_parsed_date"].head(3).tolist()
        print(f"[SCHEDULER DEBUG] ❌ Could not find {today} in Excel. Top 3 parsed dates are: {top_3}")
        return False
        
    except Exception as e:
        from app.core.logger import logger
        print(f"[SCHEDULER DEBUG] ❌ Crashed: {e}")
        logger.error(f"Error checking grid_and_diesel: {e}")
        return False

def send_operator_reminder_email() -> Dict[str, Any]:
    """Send a reminder specifically to the operator (Ojas) to fill the grid sheet."""
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    _load_scheduler_env()

    smtp_server = _get_env_value("SMTP_SERVER", "SMTP_HOST", default="smtp.gmail.com")
    smtp_port = int(_get_env_value("SMTP_PORT", default="587"))
    sender_password = _get_env_value("SENDER_PASSWORD", "SMTP_PASSWORD")
    use_tls = _to_bool(_get_env_value("SMTP_USE_TLS", default="True"), default=True)
    login_user = _get_env_value("SMTP_USERNAME", "SENDER_EMAIL")
    email_from = _get_env_value("EMAIL_FROM", "SENDER_EMAIL")
    operator_email = _get_env_value("OPERATOR_EMAIL", default="ojas@yourcompany.com")

    IST = ZoneInfo("Asia/Kolkata")
    
    # Get both dates to make the email extremely clear
    today_str = pd.Timestamp.now(tz=IST).strftime("%d-%b-%Y")
    yesterday_str = (pd.Timestamp.now(tz=IST) - pd.Timedelta(days=1)).strftime("%d-%b-%Y")
    
    subject = f"REMINDER: Please update Grid & Diesel Log for {today_str}"
    body = (
        f"Hi Ojas,\n\n"
        f"The system noticed that the Grid and Diesel data has not been logged in today's row ({today_str}).\n"
        f"Please log the consumption data for {yesterday_str} into the '{today_str}' row in the 'grid_and_diesel.xlsx' file on SharePoint.\n\n"
        f"This must be completed before 10:30 AM so the Daily Energy Report can be generated.\n\n"
        f"Thank you,\nEnergy Automation Agent"
    )

    msg = MIMEMultipart("alternative")
    msg["From"] = email_from
    msg["To"] = operator_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(smtp_server, smtp_port, timeout=10) as server:
            if use_tls: server.starttls()
            if login_user and sender_password: server.login(login_user, sender_password)
            server.sendmail(email_from, [operator_email], msg.as_string())
        return {"status": "Success", "notes": "Operator reminder sent"}
    except Exception as e:
        return {"status": "Failed", "notes": f"Reminder failed: {e}"}
    
def _run_operator_reminder_cycle():
    """Triggered at 9:00, 9:30, and 10:00 to check and remind."""
    data_exists = check_grid_diesel_entry_exists()
    
    if not data_exists:
        print("[SCHEDULER] Grid data missing! Attempting to send reminder...")
        # Capture the result of the email send
        result = send_operator_reminder_email()
        
        # Print the result so we can see the error
        print(f"[SCHEDULER] Email Status: {result.get('status')}")
        print(f"[SCHEDULER] Notes: {result.get('notes')}")
        if result.get("status") == "Success":
            print(f"[SCHEDULER] Sent to: {result.get('recipients')}")
    else:
        print("[SCHEDULER] Grid data is already present. Skipping reminder.")

