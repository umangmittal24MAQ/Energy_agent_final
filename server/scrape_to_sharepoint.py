"""
scrape_to_sharepoint.py
=======================
SuryaLogix → SharePoint Online (Excel Document Library).
Schedule: Every 30 minutes.
"""

from __future__ import annotations

import io
import json
import logging
import os
import sys

# THE FIX: Force Playwright to look in Azure's persistent storage BEFORE it imports!
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "/home/site/pw-browsers"


from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import time
import pandas as pd
import requests
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

# ──────────────────────────────────────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

# ──────────────────────────────────────────────────────────────────────────────
# Environment & Paths
# ──────────────────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
CACHE_FILE = SCRIPT_DIR / "solar_offline_cache.json"

def _load_env() -> None:
    candidates = [SCRIPT_DIR]
    for parent in SCRIPT_DIR.parents:
        candidates.append(parent)
        candidates.append(parent / "energy-dashboard")
    candidates.append(Path.cwd())
    candidates.append(Path.cwd() / "energy-dashboard")

    for path in candidates:
        env_file = path / ".env"
        if env_file.exists():
            load_dotenv(dotenv_path=env_file, override=False)
            logger.info(f"Loaded .env from: {env_file}")
            return
    load_dotenv(override=False)

_load_env()

SURYALOG_LOGIN_ID        = os.getenv("SURYALOG_LOGIN_ID", "MAQ_Software").strip()
SURYALOG_PASSWORD        = os.getenv("SURYALOG_PASSWORD", "MAQ@1234").strip()

if not SURYALOG_LOGIN_ID or not SURYALOG_PASSWORD:
    logging.warning(
        "⚠️  SURYALOG_LOGIN_ID or SURYALOG_PASSWORD is empty! "
        "Login will fail. Check your .env file."
    )
SHAREPOINT_TENANT_ID     = os.getenv("SHAREPOINT_TENANT_ID", "").strip()
SHAREPOINT_CLIENT_ID     = os.getenv("SHAREPOINT_CLIENT_ID", "").strip()
SHAREPOINT_CLIENT_SECRET = os.getenv("SHAREPOINT_CLIENT_SECRET", "").strip()

HEADLESS = os.getenv("SURYALOG_HEADLESS", "true").strip().lower() != "false"

HOSTNAME           = "testmaq.sharepoint.com"
SITE_PATH          = "/Admin"
DRIVE_NAME         = "Private"
FILE_PATH_IN_DRIVE = "22. Facilities Report/MIPL/Noida/2. Electrical data/UnifiedSolarData.xlsx"
GRAPH_BASE         = "https://graph.microsoft.com/v1.0"

_access_token: Optional[str] = None
_site_id:      Optional[str] = None
_drive_id:     Optional[str] = None

# ──────────────────────────────────────────────────────────────────────────────
# Offline Cache Handlers
# ──────────────────────────────────────────────────────────────────────────────
def _save_to_cache(row: Dict) -> None:
    """Save a failed row to a local JSON file."""
    cache = []
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r") as f:
                cache = json.load(f)
        except json.JSONDecodeError:
            pass
    cache.append(row)
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=4)
    logger.info(f"Row saved to offline cache. Total pending rows: {len(cache)}")

def _load_and_clear_cache() -> List[Dict]:
    """Load pending rows and clear the cache file."""
    if not CACHE_FILE.exists():
        return []
    try:
        with open(CACHE_FILE, "r") as f:
            cache = json.load(f)
        CACHE_FILE.unlink() # Delete the file after reading
        logger.info(f"Loaded {len(cache)} rows from offline cache.")
        return cache
    except Exception as e:
        logger.error(f"Failed to read cache: {e}")
        return []

# ──────────────────────────────────────────────────────────────────────────────
# SharePoint Graph API
# ──────────────────────────────────────────────────────────────────────────────
def _get_token() -> str:
    global _access_token
    if _access_token:
        return _access_token
    resp = requests.post(
        f"https://login.microsoftonline.com/{SHAREPOINT_TENANT_ID}/oauth2/v2.0/token",
        data={
            "grant_type":    "client_credentials",
            "client_id":     SHAREPOINT_CLIENT_ID,
            "client_secret": SHAREPOINT_CLIENT_SECRET,
            "scope":         "https://graph.microsoft.com/.default",
        },
        timeout=30,
    )
    resp.raise_for_status()
    _access_token = resp.json()["access_token"]
    return _access_token

def _get_site_and_drive_ids() -> tuple[str, str]:
    global _site_id, _drive_id
    if _site_id and _drive_id:
        return _site_id, _drive_id
    headers = {"Authorization": f"Bearer {_get_token()}"}
    _site_id = requests.get(
        f"{GRAPH_BASE}/sites/{HOSTNAME}:{SITE_PATH}", headers=headers, timeout=30
    ).json()["id"]
    for drive in requests.get(
        f"{GRAPH_BASE}/sites/{_site_id}/drives", headers=headers, timeout=30
    ).json().get("value", []):
        if drive["name"] == DRIVE_NAME:
            _drive_id = drive["id"]
            return _site_id, _drive_id
    raise Exception(f"Drive '{DRIVE_NAME}' not found on site '{SITE_PATH}'")

def _download_excel() -> bytes:
    site_id, drive_id = _get_site_and_drive_ids()
    safe = requests.utils.quote(FILE_PATH_IN_DRIVE, safe="/")
    resp = requests.get(
        f"{GRAPH_BASE}/sites/{site_id}/drives/{drive_id}/root:/{safe}:/content",
        headers={"Authorization": f"Bearer {_get_token()}"},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.content

def _upload_excel(file_bytes: bytes) -> None:
    site_id, drive_id = _get_site_and_drive_ids()
    safe = requests.utils.quote(FILE_PATH_IN_DRIVE, safe="/")
    resp = requests.put(
        f"{GRAPH_BASE}/sites/{site_id}/drives/{drive_id}/root:/{safe}:/content",
        headers={
            "Authorization": f"Bearer {_get_token()}",
            "Content-Type":  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        },
        data=file_bytes,
        timeout=60,
    )
    resp.raise_for_status()

def _upload_excel_with_retry(file_bytes: bytes, retries: int = 3, delay: int = 30) -> None:
    """Retries 5 times, waiting 1 minute between attempts (total 5 mins limit)."""
    for attempt in range(1, retries + 1):
        try:
            _upload_excel(file_bytes)
            return
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 423 and attempt < retries:
                logger.warning(f"⚠️ File locked (attempt {attempt}/{retries}). Retrying in {delay}s...")
                time.sleep(delay)
            else:
                raise

# ──────────────────────────────────────────────────────────────────────────────
# Scraper  — EXACTLY your original working logic
# ──────────────────────────────────────────────────────────────────────────────
captured_data: List[Dict] = []

def _on_response(response) -> None:
    try:
        if response.request.resource_type in ("xhr", "fetch"):
            try:
                data = response.json()
                captured_data.append({"url": response.url, "data": data})
                logger.info(f"📡 API URL: {response.url}")
            except Exception:
                pass
    except Exception:
        pass

def run_scraper() -> List[Dict]:
    global captured_data
    captured_data = []

    try:
        with sync_playwright() as p:
            # ✅ Plain launch + plain new_context
            browser = p.chromium.launch(headless=HEADLESS)
            context = browser.new_context()
            page    = context.new_page()

            page.on("response", _on_response)

            logger.info("Opening site...")
            page.goto("https://cloud.suryalog.com")

            logger.info("Attempting automatic login...")
            page.wait_for_selector("#loginId", timeout=10000)
            page.fill("#loginId", SURYALOG_LOGIN_ID)
            page.wait_for_timeout(500)
            page.fill("#password", SURYALOG_PASSWORD)
            page.wait_for_timeout(500)

            page.click("#btnlogin")
            logger.info("Login button clicked, waiting for page to load...")

            # ✅ EXACT sequence requested by user
            page.wait_for_timeout(8000)
            logger.info("Waiting for APIs...")
            page.wait_for_timeout(10000)
            logger.info("Triggering interaction...")
            page.mouse.click(100, 100)
            page.wait_for_timeout(5000)
            logger.info("Reloading...")
            page.reload()
            page.wait_for_timeout(10000)

            if not captured_data:
                screenshot_path = SCRIPT_DIR / "login_failure.png"
                page.screenshot(path=str(screenshot_path))
                logger.warning(
                    f"0 responses captured. Screenshot → {screenshot_path}\n"
                    "  Set SURYALOG_HEADLESS=false in .env and re-run to debug."
                )

            browser.close()

    except Exception as exc:
        logger.warning(f"Browser error (continuing): {exc}")

    logger.info(f"Total captured API responses: {len(captured_data)}")
    for item in captured_data:
        logger.info(f"  → {item['url']}")

    return captured_data

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────
def _safe_float(val, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default

def _get_device_status(status_code) -> str:
    if status_code is None:
        return "FAULT"
    if isinstance(status_code, str):
        if not status_code.isdigit():
            s = status_code.upper()
            return s if s in ("ON", "ACTIVE") else "FAULT"
    try:
        code = int(status_code)
        if code == 0 or code == 17:
            return "ON"
        elif code > 0:
            return "ACTIVE"
        else:
            return "OFF"
    except (ValueError, TypeError):
        return "FAULT"

def _find_change_plant(api_data: List[Dict]) -> Dict:
    for item in api_data:
        if "change_plant" in item["url"]:
            return item["data"]
    raise ValueError("No change_plant response found in captured data.")

def _find_gen_info(api_data: List[Dict]) -> Dict:
    for item in api_data:
        if "gen_info" in item["url"]:
            return item["data"]
    raise ValueError("No gen_info response found in captured data.")

def _select_primary_meter(meters: Dict) -> Optional[Dict]:
    for meter_data in meters.values():
        if isinstance(meter_data, dict):
            if meter_data.get("meter_online", 0) == 1 and _safe_float(meter_data.get("VLL")) > 0:
                return meter_data
    for meter_data in meters.values():
        if isinstance(meter_data, dict) and meter_data.get("meter_online", 0) == 1:
            return meter_data
    for meter_data in meters.values():
        if isinstance(meter_data, dict):
            return meter_data
    return None

def _find_numeric_by_keys(payload: Any, key_candidates: set[str]) -> Optional[float]:
    """Helper to deeply scan JSON for fallback yesterday keys"""
    if isinstance(payload, dict):
        for k, v in payload.items():
            if str(k).strip().lower() in key_candidates:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    pass
            found = _find_numeric_by_keys(v, key_candidates)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _find_numeric_by_keys(item, key_candidates)
            if found is not None:
                return found
    return None

def _extract_yesterday_generation_kwh(last_log: Dict, plant_data: Dict, live_data: Dict) -> float:
    """Extract yesterday generation (kWh) specifically targeting duration block 46800."""
    def find_daily_duration_value(payload: Any) -> Optional[float]:
        if isinstance(payload, dict):
            if payload.get("duration") == 46800 and "value" in payload:
                val = _safe_float(payload.get("value"))
                if val > 0:
                    return val
            for v in payload.values():
                res = find_daily_duration_value(v)
                if res is not None:
                    return res
        elif isinstance(payload, list):
            for item in reversed(payload):
                res = find_daily_duration_value(item)
                if res is not None:
                    return res
        return None

    for payload in [last_log, live_data, plant_data]:
        found_val = find_daily_duration_value(payload)
        if found_val is not None:
            return round(found_val, 2)

    key_candidates = {
        "whyday", "whyd", "whyesterday", "whyesterdayday", "whyesterdaygen",
        "yesterdaygen", "yesterdaygeneration", "previousdaygeneration",
        "prevdaygeneration", "ydaygeneration",
    }
    direct = _find_numeric_by_keys(last_log, key_candidates)
    if direct is not None:
        return max(0.0, direct)

    total = 0.0
    found_any = False
    inverters = last_log.get("inverter")
    if isinstance(inverters, dict):
        for inv in list(inverters.values())[:5]:
            if not isinstance(inv, dict):
                continue
            inv_val = _find_numeric_by_keys(inv, key_candidates)
            if inv_val is not None:
                total += max(0.0, inv_val)
                found_any = True

    return round(total, 2) if found_any else 0.0

# ──────────────────────────────────────────────────────────────────────────────
# Data extraction
# ──────────────────────────────────────────────────────────────────────────────
def _extract_row(api_data: List[Dict]) -> Dict[str, Any]:
    if not api_data:
        raise ValueError("No API data captured. Aborting.")

    try:
        plant_data = _find_change_plant(api_data)
        live_data  = _find_gen_info(api_data)
    except ValueError as exc:
        raise ValueError(f"{exc}. Got URLs: {[d['url'] for d in api_data]}")

    now = datetime.now()
    rounded_minute = 0 if now.minute < 30 else 30
    slot_time = now.replace(minute=rounded_minute, second=0, microsecond=0).strftime("%H:%M")
    date_str  = now.strftime("%Y-%m-%d")

    row: Dict[str, Any] = {
        "Date":           date_str,
        "Date Formatted": date_str,
        "Time":           slot_time,
    }

    plant_info = plant_data.get("plantInfo", {})
    row["DC Capacity (kWp)"] = _safe_float(plant_info.get("dc_size"), 598.6)
    row["AC Capacity (kW)"]  = _safe_float(plant_info.get("ac_size"), 500.0)

    last_log = live_data.get("lastLogData", {})

    if "inverter" in last_log and isinstance(last_log["inverter"], dict):
        total_dc_w    = 0.0
        total_ac_w    = 0.0
        total_day_kwh = 0.0

        for i, (inv_id, inv) in enumerate(list(last_log["inverter"].items())[:5], start=1):
            if not isinstance(inv, dict):
                continue

            dc_w    = _safe_float(inv.get("DC_W"))
            ac_w    = _safe_float(inv.get("WT"))
            wh_day  = _safe_float(inv.get("WHDay"))

            total_dc_w    += dc_w
            total_ac_w    += ac_w
            total_day_kwh += wh_day

            row[f"Inverter{i}_status"] = _get_device_status(inv.get("suryalog_status"))
            row[f"Inverter{i}"]        = round(ac_w / 1000.0, 3)   # W → kW

        row["DC Power (kW)"]        = round(total_dc_w / 1000.0, 2)
        row["AC Power (kW)"]        = round(total_ac_w / 1000.0, 2)
        row["Active Power (kW)"]    = row["AC Power (kW)"]
        row["Day Generation (kWh)"] = round(total_day_kwh, 2)

    # ✅ ADDED: Yesterday Generation Extraction
    row["Yesterday Generation (kWh)"] = _extract_yesterday_generation_kwh(last_log, plant_data, live_data)

    if "meter" in last_log and isinstance(last_log["meter"], dict):
        primary = _select_primary_meter(last_log["meter"])

        if primary:
            row["Voltage Phase-to-Phase (V)"]  = round(_safe_float(primary.get("VLL")), 2)
            row["Voltage Phase-to-Neutral (V)"]= round(_safe_float(primary.get("VLN")), 2)
            row["V1 (V)"]                       = round(_safe_float(primary.get("V1")),  2)
            row["V2 (V)"]                       = round(_safe_float(primary.get("V2")),  2)
            row["V3 (V)"]                       = round(_safe_float(primary.get("V3")),  2)

            i1 = _safe_float(primary.get("I1"))
            i2 = _safe_float(primary.get("I2"))
            i3 = _safe_float(primary.get("I3"))
            total_i = i1 + i2 + i3
            row["Current Total (A)"]   = round(total_i, 2)
            row["Current Average (A)"] = round(total_i / 3.0, 2) if total_i > 0 else 0.0

            row["Apparent Power (kVA)"] = round(_safe_float(primary.get("VAT")) / 1000.0, 2)
            row["Power Factor"]         = round(_safe_float(primary.get("PFT")), 3)
            row["Frequency (Hz)"]       = round(_safe_float(primary.get("FREQ")), 2)

            row["Total Import (kWh)"] = round(_safe_float(primary.get("WHImp")), 2)
            row["Total Export (kWh)"] = round(_safe_float(primary.get("WHExp")), 2)

    if "smb" in last_log and isinstance(last_log["smb"], dict):
        for i, (smb_id, smb) in enumerate(list(last_log["smb"].items())[:5], start=1):
            if not isinstance(smb, dict):
                continue
            row[f"SMB{i}_status"] = _get_device_status(smb.get("suryalog_status"))
            row[f"SMB{i}"]        = round(_safe_float(smb.get("WTOT")) / 1000.0, 3)

    return row

# ──────────────────────────────────────────────────────────────────────────────
# Excel update
# ──────────────────────────────────────────────────────────────────────────────
def update_excel_in_memory(file_bytes: bytes, new_row: Dict) -> bytes:
    logger.info("Loading Excel file into Pandas…")
    df = pd.read_excel(io.BytesIO(file_bytes), sheet_name="Sheet1")

    # Safely map yesterday column if it exists under a weird name
    y_col = next((c for c in df.columns if "yesterday" in str(c).lower() and "gen" in str(c).lower()), None)
    if y_col and "Yesterday Generation (kWh)" in new_row and y_col != "Yesterday Generation (kWh)":
        new_row[y_col] = new_row.pop("Yesterday Generation (kWh)")

    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    df.drop_duplicates(subset=["Date", "Time"], keep="last", inplace=True)
    df.sort_values(["Date", "Time"], inplace=True)

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Sheet1", index=False)
    buf.seek(0)
    return buf.read()

# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────
def main() -> None:
    logger.info("=== SuryaLogix → SharePoint Excel Scraper starting ===")
    
    # 1. Scrape
    raw_data = run_scraper()

    # 2. Extract row
    try:
        current_row = _extract_row(raw_data)
        logger.info(f"✓ Slot: {current_row['Date']} {current_row['Time']}")
    except ValueError as exc:
        logger.error(f"❌ {exc}")
        sys.exit(1)

    # 3. Check for failed offline data
    pending_rows = _load_and_clear_cache()
    all_rows_to_upload = pending_rows + [current_row]

    # 4. Read → Modify → Write
    try:
        logger.info("Downloading UnifiedSolarData.xlsx from SharePoint…")
        file_bytes = _download_excel()
        
        # Apply all pending rows
        for r in all_rows_to_upload:
            file_bytes = update_excel_in_memory(file_bytes, r)
            
        logger.info("Uploading updated file back to SharePoint…")
        _upload_excel_with_retry(file_bytes)
        logger.info("✅ SharePoint upload SUCCESS!")
        
    except Exception as exc:
        logger.error(f"❌ SharePoint pipeline FAILED: {exc}")
        
        # ✅ THE FIX: Save to cache if file was locked!
        for r in all_rows_to_upload:
            _save_to_cache(r)
            
        sys.exit(1)


if __name__ == "__main__":
    main()