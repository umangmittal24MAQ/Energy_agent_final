"""
inverter_monitor.py
===================
Runs every 30 minutes (hooked into the solar scraper cron).

Responsibilities:
  1. Read the latest inverter statuses from UnifiedSolarData on SharePoint.
  2. Update inverter_tracker.json with cumulative uptime/downtime per inverter.
  3. If any inverter is in FAULT → send alert mail to scheduler recipients.
  4. NaN status (nighttime / no data) is ignored — not counted as up or down.

Tracker file location (mirrors scheduler_service.py PERSIST_DIR logic):
  Azure : /home/LogFiles/energy-dashboard/output/inverter_tracker.json
  Local : <repo-root>/energy-dashboard/output/inverter_tracker.json

Tracker JSON structure:
  {
    "2026-05-08": {
      "Inverter1": {"uptime_mins": 390, "downtime_mins": 30},
      ...
    },
    "2026-05-07": { ... }   ← kept for 7 days so master engine can always find it
  }
"""

import json
import logging
import os
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("app.services.inverter_monitor")

IST = ZoneInfo("Asia/Kolkata")

# ── Inverter definitions ──────────────────────────────────────────────────────
INVERTERS = ["Inverter1", "Inverter2", "Inverter3", "Inverter4", "Inverter5"]
INTERVAL_MINS = 30          # scraper cadence
TRACKER_KEEP_DAYS = 30      # how many days of history to retain in tracker

# ── Tracker path (mirrors scheduler_service.py PERSIST_DIR logic) ─────────────
def _get_tracker_path() -> Path:
    if "WEBSITE_SITE_NAME" in os.environ:
        persist_dir = Path("/home/LogFiles/energy-dashboard")
    else:
        # Walk up from this file to find energy-dashboard sibling of app/
        base = Path(__file__).resolve().parent.parent.parent
        persist_dir = base / "energy-dashboard"
    tracker_path = persist_dir / "output" / "inverter_tracker.json"
    tracker_path.parent.mkdir(parents=True, exist_ok=True)
    return tracker_path


# ── Tracker I/O ───────────────────────────────────────────────────────────────
def load_tracker() -> Dict[str, Any]:
    path = _get_tracker_path()
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"[INVERTER] Failed to read tracker: {e}")
        return {}


def save_tracker(data: Dict[str, Any]) -> None:
    path = _get_tracker_path()
    # Prune old dates — keep last TRACKER_KEEP_DAYS only
    today = datetime.now(IST).date()
    cutoff = (today - timedelta(days=TRACKER_KEEP_DAYS)).strftime("%Y-%m-%d")
    pruned = {k: v for k, v in data.items() if k >= cutoff}

    try:
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(pruned, f, indent=4)
        shutil.move(tmp, path)
    except OSError as e:
        logger.error(f"[INVERTER] Failed to save tracker: {e}")
        try:
            os.unlink(tmp)
        except Exception:
            pass


# ── Status helpers ────────────────────────────────────────────────────────────
def _is_active(status: Any) -> bool:
    return str(status).strip().upper() in ("ACTIVE", "ON")


def _is_fault(status: Any) -> bool:
    """
    Returns True for any non-operational status that counts as downtime.
    Covers the old FAULT status and the new INACTIVE status written by the
    scraper when SMB has power but the inverter's AC output is 0.
    """
    return str(status).strip().upper() in ("FAULT", "INACTIVE")


def _is_nan(status: Any) -> bool:
    if status is None:
        return True
    import pandas as pd
    try:
        return pd.isna(status)
    except Exception:
        return str(status).strip().lower() in ("nan", "none", "", "nat")


# ── Main monitor function ─────────────────────────────────────────────────────
def run_inverter_monitor() -> None:
    """
    Called every 30 minutes from scheduler_service._run_solar_scraper wrapper.
    Reads latest inverter statuses, updates tracker, sends alert if any FAULT.
    """
    logger.info("[INVERTER] Starting 30-min inverter monitor tick...")

    try:
        statuses = _fetch_latest_inverter_statuses()
    except Exception as e:
        logger.error(f"[INVERTER] Could not fetch statuses from SharePoint: {e}")
        return

    if not statuses:
        logger.info("[INVERTER] No inverter status data available this tick (likely nighttime).")
        return

    today_str = datetime.now(IST).strftime("%Y-%m-%d")
    tracker = load_tracker()
    today_data = tracker.get(today_str, {})

    faulted: List[str] = []

    for inv in INVERTERS:
        status = statuses.get(inv)

        if _is_nan(status):
            # Nighttime / no data — skip, don't count
            logger.debug(f"[INVERTER] {inv}: NaN — skipping")
            continue

        entry = today_data.get(inv, {"uptime_mins": 0, "downtime_mins": 0})

        if _is_active(status):
            entry["uptime_mins"] = entry.get("uptime_mins", 0) + INTERVAL_MINS
            logger.info(f"[INVERTER] {inv}: {status} — uptime +30min "
                        f"(total: {entry['uptime_mins']}min)")
        elif _is_fault(status):
            entry["downtime_mins"] = entry.get("downtime_mins", 0) + INTERVAL_MINS
            faulted.append(inv)
            # INACTIVE = SMB has power but inverter AC output was 0 (new formula)
            # FAULT    = suryalog status code indicated a device fault
            logger.warning(f"[INVERTER] {inv}: {status} — downtime +30min "
                           f"(total: {entry['downtime_mins']}min)")

        today_data[inv] = entry

    tracker[today_str] = today_data
    save_tracker(tracker)
    logger.info(f"[INVERTER] Tracker updated for {today_str}.")

    if faulted:
        logger.warning(f"[INVERTER] FAULTs detected: {faulted}. Sending alert...")
        try:
            _send_fault_alert(faulted, today_data, today_str)
        except Exception as e:
            logger.error(f"[INVERTER] Failed to send fault alert: {e}")
    else:
        logger.info("[INVERTER] All inverters ACTIVE this tick. No alert needed.")


# ── SharePoint data fetch ─────────────────────────────────────────────────────
def _fetch_latest_inverter_statuses() -> Optional[Dict[str, Any]]:
    """
    Reads the latest row from UnifiedSolarData.xlsx on SharePoint.
    Returns dict like {"Inverter1": "ACTIVE", "Inverter2": "FAULT", ...}
    or None if no data available.
    """
    try:
        from app.services.sharepoint_data_service import get_service
        sp = get_service()
        df = sp.fetch_sheet_data("unified_solar")  # same key used elsewhere
    except Exception:
        # Fallback: try direct download via master_data_engine helpers
        df = _download_unified_solar_direct()

    if df is None or df.empty:
        return None

    import pandas as pd

    # Parse dates and times to find the most recent row for today or yesterday
    if "Date" not in df.columns:
        logger.warning("[INVERTER] 'Date' column not found in UnifiedSolarData.")
        return None

    df = df.copy()
    df["_date"] = pd.to_datetime(df["Date"], errors="coerce").dt.date

    today = datetime.now(IST).date()
    yesterday = (datetime.now(IST) - timedelta(days=1)).date()

    # Prefer today's rows, fallback to yesterday
    today_rows = df[df["_date"] == today]
    candidate_rows = today_rows if not today_rows.empty else df[df["_date"] == yesterday]

    if candidate_rows.empty:
        logger.info("[INVERTER] No rows found for today or yesterday in UnifiedSolarData.")
        return None

    # Sort by time to get the most recent reading
    time_col = next((c for c in candidate_rows.columns if str(c).strip().lower() == "time"), None)
    if time_col:
        candidate_rows = candidate_rows.copy()
        candidate_rows["_time"] = pd.to_datetime(
            candidate_rows[time_col], format="mixed", errors="coerce"
        )
        candidate_rows = candidate_rows.sort_values("_time")

    latest_row = candidate_rows.iloc[-1]

    result: Dict[str, Any] = {}
    for inv in INVERTERS:
        status_col = f"{inv}_status"
        if status_col in latest_row.index:
            result[inv] = latest_row[status_col]
        else:
            result[inv] = None

    logger.info(f"[INVERTER] Latest statuses from SharePoint: {result}")
    return result


def _download_unified_solar_direct() -> Optional[Any]:
    """Fallback: download UnifiedSolarData directly using Graph API credentials."""
    try:
        import io
        import os
        import requests
        from dotenv import load_dotenv
        load_dotenv()

        tenant_id = os.getenv("SHAREPOINT_TENANT_ID", "")
        client_id = os.getenv("SHAREPOINT_CLIENT_ID", "")
        client_secret = os.getenv("SHAREPOINT_CLIENT_SECRET", "")
        hostname = os.getenv("SHAREPOINT_HOSTNAME", "")
        site_path = os.getenv("SHAREPOINT_SITE_PATH", "/Admin")
        drive_name = os.getenv("SHAREPOINT_DRIVE_NAME", "")
        base_folder = os.getenv("SHAREPOINT_BASE_FOLDER", "")

        token_resp = requests.post(
            f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": "https://graph.microsoft.com/.default",
            },
            timeout=30,
        )
        token_resp.raise_for_status()
        token = token_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        graph = "https://graph.microsoft.com/v1.0"
        site_resp = requests.get(f"{graph}/sites/{hostname}:{site_path}", headers=headers, timeout=30)
        site_resp.raise_for_status()
        site_id = site_resp.json()["id"]

        drives = requests.get(f"{graph}/sites/{site_id}/drives", headers=headers, timeout=30)
        drives.raise_for_status()
        drive_id = next(
            (d["id"] for d in drives.json().get("value", []) if d["name"] == drive_name),
            None,
        )
        if not drive_id:
            return None

        import pandas as pd
        file_path = f"{base_folder}UnifiedSolarData.xlsx"
        safe_path = requests.utils.quote(file_path, safe="/")
        url = f"{graph}/sites/{site_id}/drives/{drive_id}/root:/{safe_path}:/content"
        file_resp = requests.get(url, headers=headers, timeout=60)
        file_resp.raise_for_status()
        return pd.read_excel(io.BytesIO(file_resp.content), sheet_name="Sheet1")

    except Exception as e:
        logger.error(f"[INVERTER] Direct SharePoint download failed: {e}")
        return None


# ── Alert mail ────────────────────────────────────────────────────────────────
def _send_fault_alert(
    faulted: List[str],
    today_data: Dict[str, Any],
    date_str: str,
) -> None:
    """Build and send the inverter fault alert email."""
    from app.services.email_service import send_inverter_alert
    send_inverter_alert(faulted=faulted, today_data=today_data, date_str=date_str)


# ── Live status summary (used by API routes / dashboard) ─────────────────────
def get_inverter_status_summary() -> Dict[str, Any]:
    """
    Returns a live snapshot of all inverters for the current day.
    Intended for API routes and dashboard polling.

    Return shape:
    {
        "date": "2026-05-08",
        "as_of": "2026-05-08T14:30:00+05:30",   ← IST timestamp of this call
        "inverters": {
            "Inverter1": {
                "uptime_mins":   390,
                "downtime_mins": 30,
                "uptime_hrs":    6.5,
                "downtime_hrs":  0.5,
                "uptime_pct":    92.9,            ← % of tracked time that was ACTIVE
            },
            ...
        },
        "fault_count":  1,                        ← inverters with downtime_mins > 0
        "tracker_found": True,                    ← False when tracker missing/empty for today
    }

    Fallback: if the tracker is missing or today has no data, all inverters show
    zeros and tracker_found is False — caller can decide how to surface that.
    """
    IST = ZoneInfo("Asia/Kolkata")
    now_ist = datetime.now(IST)
    today_str = now_ist.strftime("%Y-%m-%d")

    tracker = load_tracker()
    day_data = tracker.get(today_str, {})
    tracker_found = bool(day_data)

    inverters_out: Dict[str, Any] = {}
    fault_count = 0

    for inv in INVERTERS:
        entry = day_data.get(inv, {"uptime_mins": 0, "downtime_mins": 0})
        up_mins = int(entry.get("uptime_mins", 0))
        dn_mins = int(entry.get("downtime_mins", 0))
        total_mins = up_mins + dn_mins
        uptime_pct = round((up_mins / total_mins * 100), 1) if total_mins > 0 else 0.0

        if dn_mins > 0:
            fault_count += 1

        inverters_out[inv] = {
            "uptime_mins":   up_mins,
            "downtime_mins": dn_mins,
            "uptime_hrs":    round(up_mins / 60, 2),
            "downtime_hrs":  round(dn_mins / 60, 2),
            "uptime_pct":    uptime_pct,
        }

    return {
        "date":          today_str,
        "as_of":         now_ist.isoformat(),
        "inverters":     inverters_out,
        "fault_count":   fault_count,
        "tracker_found": tracker_found,
    }

def get_inverter_uptime_for_date(date_str: str) -> Optional[Dict[str, Dict[str, float]]]:
    """
    Called by master_data_engine.py to get uptime/downtime for a specific date.

    Returns:
        {
          "Inverter1": {"uptime_hrs": 6.5, "downtime_hrs": 0.5},
          ...
        }
        or None if no data found for that date.
    """
    tracker = load_tracker()
    day_data = tracker.get(date_str)

    if not day_data:
        logger.warning(f"[INVERTER] No tracker data found for {date_str}.")
        return None

    result: Dict[str, Dict[str, float]] = {}
    for inv in INVERTERS:
        entry = day_data.get(inv, {"uptime_mins": 0, "downtime_mins": 0})
        result[inv] = {
            "uptime_hrs":   round(entry.get("uptime_mins", 0) / 60, 2),
            "downtime_hrs": round(entry.get("downtime_mins", 0) / 60, 2),
        }

    logger.info(f"[INVERTER] Uptime summary for {date_str}: {result}")
    return result


def get_today_uptime_from_sheet() -> Dict[str, Any]:
    """
    On-demand: reads UnifiedSolarData directly and calculates
    uptime/downtime for today from midnight to now.
    Each 30-min row where status == ACTIVE/ON counts as +30 mins uptime,
    FAULT or INACTIVE counts as +30 mins downtime, NaN is skipped.

    Return shape:
    {
        "date": "2026-05-08",
        "as_of": "2026-05-08T14:30:00+05:30",
        "rows_processed": 14,
        "inverters": {
            "Inverter1": {
                "uptime_mins": 390, "downtime_mins": 0,
                "uptime_hrs": 6.5,  "downtime_hrs": 0.0,
                "uptime_pct": 100.0
            }, ...
        },
        "fault_count": 0
    }
    """
    import pandas as pd
    from app.services.sharepoint_data_service import get_service

    IST = ZoneInfo("Asia/Kolkata")
    now_ist = datetime.now(IST)
    today_str = now_ist.strftime("%Y-%m-%d")

    try:
        sp = get_service()
        df = sp.fetch_sheet_data("unified_solar")
    except Exception as e:
        logger.error(f"[INVERTER] on-demand fetch failed: {e}")
        return {"error": str(e)}

    if df is None or df.empty or "Date" not in df.columns:
        return {"error": "No data available from UnifiedSolarData"}

    df = df.copy()
    df["_date"] = pd.to_datetime(df["Date"], errors="coerce").dt.date
    today_rows = df[df["_date"] == now_ist.date()].copy()

    if today_rows.empty:
        return {
            "date": today_str,
            "as_of": now_ist.isoformat(),
            "rows_processed": 0,
            "day_generation_kwh": 0.0,
            "inverters": {
                inv: {"uptime_mins": 0, "downtime_mins": 0,
                      "uptime_hrs": 0.0, "downtime_hrs": 0.0, "uptime_pct": 0.0}
                for inv in INVERTERS
            },
            "fault_count": 0,
        }

    inverters_out: Dict[str, Any] = {}
    fault_count = 0

    for inv in INVERTERS:
        status_col = f"{inv}_status"
        up = 0
        dn = 0
        if status_col in today_rows.columns:
            for val in today_rows[status_col]:
                if _is_nan(val):
                    continue
                elif _is_active(val):
                    up += INTERVAL_MINS
                elif _is_fault(val):
                    dn += INTERVAL_MINS

        total = up + dn
        uptime_pct = round(up / total * 100, 1) if total > 0 else 0.0
        if dn > 0:
            fault_count += 1

        inverters_out[inv] = {
            "uptime_mins":   up,
            "downtime_mins": dn,
            "uptime_hrs":    round(up / 60, 2),
            "downtime_hrs":  round(dn / 60, 2),
            "uptime_pct":    uptime_pct,
        }

    # ── Extract today's actual generation (kWh) from the latest row ──────────
    # The frontend uses this as "Actual so far" in the Expected vs Actual widget.
    def _norm(c: str) -> str:
        return "".join(ch for ch in str(c).lower() if ch.isalnum())

    time_col = next((c for c in today_rows.columns if _norm(c) == "time"), None)
    if time_col:
        today_rows = today_rows.copy()
        today_rows["_time"] = pd.to_datetime(today_rows[time_col], format="mixed", errors="coerce")
        today_rows = today_rows.sort_values("_time")

    # Read "Day Generation (kWh)" from the latest today row.
    # This is a running cumulative counter — the last row of the day
    # holds the highest (most current) value.
    # We do NOT use YesterdayGen — that column is written by tomorrow's scraper
    # run and always contains the PREVIOUS day's total, not today's live value.
    daygen_col = next(
        (c for c in today_rows.columns if _norm(c) in {
            "daygenerationkwh",    # "Day Generation (kWh)"  <- exact match
            "daygeneration",       # "DayGeneration"
            "daygeneration(kwh)",  # "DayGeneration(kWh)"
        }),
        None,
    )

    def _safe(val) -> float:
        try:
            return float(str(val).replace(",", "").strip())
        except (TypeError, ValueError):
            return 0.0

    latest = today_rows.iloc[-1]
    day_generation_kwh = 0.0
    if daygen_col:
        day_generation_kwh = _safe(latest.get(daygen_col, 0))
    else:
        logger.warning(
            "[INVERTER] 'Day Generation (kWh)' column not found. "
            "Columns present: %s", list(today_rows.columns)
        )

    logger.info(f"[INVERTER] on-demand live calc for {today_str}: {inverters_out}, day_generation_kwh={day_generation_kwh}")

    return {
        "date":                today_str,
        "as_of":               now_ist.isoformat(),
        "rows_processed":      len(today_rows),
        "day_generation_kwh":  round(day_generation_kwh, 1),
        "inverters":           inverters_out,
        "fault_count":         fault_count,
    }

# ── Date-selective uptime (from tracker) ─────────────────────────────────────
def get_uptime_from_tracker_for_date(date_str: Optional[str] = None) -> Dict[str, Any]:
    """
    Returns inverter uptime/downtime for a specific date from inverter_tracker.json.

    For today: falls back to get_today_uptime_from_sheet() so the live 30-min
    granularity is always used for the current day (tracker only updates every
    30 min and may be slightly behind).

    For past dates: reads directly from tracker (up to 30 days back).

    Args:
        date_str: "YYYY-MM-DD". Defaults to today (IST) if None.

    Returns same shape as get_today_uptime_from_sheet().
    """
    IST = ZoneInfo("Asia/Kolkata")
    now_ist = datetime.now(IST)
    today_str = now_ist.strftime("%Y-%m-%d")

    if date_str is None or date_str == today_str:
        # Always use live sheet data for today
        return get_today_uptime_from_sheet()

    # Validate date format
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return {"error": f"Invalid date format '{date_str}'. Expected YYYY-MM-DD."}

    tracker = load_tracker()
    day_data = tracker.get(date_str, {})

    if not day_data:
        return {
            "date": date_str,
            "as_of": now_ist.isoformat(),
            "rows_processed": 0,
            "inverters": {
                inv: {
                    "uptime_mins": 0, "downtime_mins": 0,
                    "uptime_hrs": 0.0, "downtime_hrs": 0.0, "uptime_pct": 0.0,
                }
                for inv in INVERTERS
            },
            "fault_count": 0,
            "source": "tracker",
            "tracker_found": False,
        }

    inverters_out: Dict[str, Any] = {}
    fault_count = 0

    for inv in INVERTERS:
        entry = day_data.get(inv, {"uptime_mins": 0, "downtime_mins": 0})
        up_mins = int(entry.get("uptime_mins", 0))
        dn_mins = int(entry.get("downtime_mins", 0))
        total_mins = up_mins + dn_mins
        uptime_pct = round(up_mins / total_mins * 100, 1) if total_mins > 0 else 0.0
        if dn_mins > 0:
            fault_count += 1

        inverters_out[inv] = {
            "uptime_mins":   up_mins,
            "downtime_mins": dn_mins,
            "uptime_hrs":    round(up_mins / 60, 2),
            "downtime_hrs":  round(dn_mins / 60, 2),
            "uptime_pct":    uptime_pct,
        }

    return {
        "date":          date_str,
        "as_of":         now_ist.isoformat(),
        "rows_processed": sum(
            int(day_data.get(inv, {}).get("uptime_mins", 0) +
                day_data.get(inv, {}).get("downtime_mins", 0)) // INTERVAL_MINS
            for inv in INVERTERS
        ) // len(INVERTERS),
        "inverters":     inverters_out,
        "fault_count":   fault_count,
        "source":        "tracker",
        "tracker_found": True,
    }


# ── 30-day trend from tracker ─────────────────────────────────────────────────
def get_inverter_trend(days: int = 30) -> Dict[str, Any]:
    """
    Returns daily uptime_pct per inverter for the last `days` days.
    Reads entirely from inverter_tracker.json — no SharePoint call needed.

    Today's entry uses the tracker snapshot (updated every 30 min by the monitor).
    Missing dates return 0 uptime_pct with tracker_found=False so the frontend
    can render gaps correctly.

    Return shape:
    {
        "days": 30,
        "inverters": ["Inverter1", ..., "Inverter5"],
        "trend": [
            {
                "date": "2026-05-18",
                "tracker_found": True,
                "Inverter1": {"uptime_pct": 100.0, "uptime_hrs": 12.5, "downtime_hrs": 0.0},
                "Inverter2": { ... },
                ...
            },
            ...   ← sorted chronologically, oldest first
        ]
    }
    """
    IST = ZoneInfo("Asia/Kolkata")
    today = datetime.now(IST).date()
    tracker = load_tracker()

    trend = []
    for offset in range(days - 1, -1, -1):   # oldest → newest
        date = today - timedelta(days=offset)
        date_str = date.strftime("%Y-%m-%d")
        day_data = tracker.get(date_str, {})
        found = bool(day_data)

        entry: Dict[str, Any] = {
            "date": date_str,
            "tracker_found": found,
        }

        for inv in INVERTERS:
            inv_data = day_data.get(inv, {"uptime_mins": 0, "downtime_mins": 0})
            up_mins = int(inv_data.get("uptime_mins", 0))
            dn_mins = int(inv_data.get("downtime_mins", 0))
            total   = up_mins + dn_mins
            entry[inv] = {
                "uptime_pct":    round(up_mins / total * 100, 1) if total > 0 else 0.0,
                "uptime_hrs":    round(up_mins / 60, 2),
                "downtime_hrs":  round(dn_mins / 60, 2),
                "downtime_mins": dn_mins,
            }

        trend.append(entry)

    return {
        "days":      days,
        "inverters": INVERTERS,
        "trend":     trend,
    }