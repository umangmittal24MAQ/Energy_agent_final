"""
scrape_to_sharepoint.py
=======================
SuryaLogix → SharePoint Online (Excel Document Library).
Schedule: Every 30 minutes.

Verified against captured_api_data.json (5 responses):
  [0] https://cloud.suryalog.com/common/change_plant   → plantInfo
  [1] https://cloud.suryalog.com/livebar/gen_info       → lastLogData (live)
  [2] https://cloud.suryalog.com/livebar/gen_info       (duplicate, unused)
  [3] https://cloud.suryalog.com/common/change_plant    (duplicate, unused)
  [4] https://cloud.suryalog.com/livebar/gen_info       (duplicate, unused)

Key facts confirmed from real data:
  - 5 inverters, WHDay is already in kWh (e.g. 111.5, 366.9 kWh)
  - 7 meters; Meter1 (first online with VLL > 0) is the solar meter
    (the old hardcoded PRIMARY_METER_ID does not exist in this plant)
  - suryalog_status is a large int bitmask — any value > 0 means ACTIVE
  - Scraper must use plain browser.new_context() (no custom user-agent)
    to match the exact behaviour of the working scrape.py
"""

from __future__ import annotations

import io
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
# Environment
# ──────────────────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent


def _load_env() -> None:
    logger.info("[DEBUG] _load_env: Searching for .env file in parent directories...")
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
            
    logger.info("[DEBUG] _load_env: No .env file found. Falling back to system environment variables.")
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

# SharePoint Drive config
HOSTNAME           = "testmaq.sharepoint.com"
SITE_PATH          = "/Admin"
DRIVE_NAME         = "Private"
FILE_PATH_IN_DRIVE = "22. Facilities Report/MIPL/Noida/2. Electrical data/UnifiedSolarData.xlsx"
GRAPH_BASE         = "https://graph.microsoft.com/v1.0"

_access_token: Optional[str] = None
_site_id:      Optional[str] = None
_drive_id:     Optional[str] = None

# ──────────────────────────────────────────────────────────────────────────────
# SharePoint Graph API
# ──────────────────────────────────────────────────────────────────────────────

def _get_token() -> str:
    global _access_token
    if _access_token:
        logger.info("[DEBUG] _get_token: Reusing cached OAuth2 token.")
        return _access_token
    logger.info("[DEBUG] _get_token: Requesting new OAuth2 token from Microsoft Graph...")
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
    logger.info("[DEBUG] _get_token: Token successfully retrieved.")
    return _access_token


def _get_site_and_drive_ids() -> tuple[str, str]:
    global _site_id, _drive_id
    if _site_id and _drive_id:
        logger.info("[DEBUG] _get_site_and_drive_ids: Reusing cached Site and Drive IDs.")
        return _site_id, _drive_id
        
    logger.info(f"[DEBUG] _get_site_and_drive_ids: Resolving Site ID for '{HOSTNAME}:{SITE_PATH}'...")
    headers = {"Authorization": f"Bearer {_get_token()}"}
    _site_id = requests.get(
        f"{GRAPH_BASE}/sites/{HOSTNAME}:{SITE_PATH}", headers=headers, timeout=30
    ).json()["id"]
    
    logger.info(f"[DEBUG] _get_site_and_drive_ids: Resolving Drive ID for '{DRIVE_NAME}'...")
    for drive in requests.get(
        f"{GRAPH_BASE}/sites/{_site_id}/drives", headers=headers, timeout=30
    ).json().get("value", []):
        if drive["name"] == DRIVE_NAME:
            _drive_id = drive["id"]
            logger.info("[DEBUG] _get_site_and_drive_ids: Site and Drive IDs successfully resolved.")
            return _site_id, _drive_id
    raise Exception(f"Drive '{DRIVE_NAME}' not found on site '{SITE_PATH}'")


def _download_excel() -> bytes:
    logger.info("[DEBUG] _download_excel: Initiating file download from SharePoint...")
    site_id, drive_id = _get_site_and_drive_ids()
    safe = requests.utils.quote(FILE_PATH_IN_DRIVE, safe="/")
    resp = requests.get(
        f"{GRAPH_BASE}/sites/{site_id}/drives/{drive_id}/root:/{safe}:/content",
        headers={"Authorization": f"Bearer {_get_token()}"},
        timeout=60,
    )
    resp.raise_for_status()
    logger.info(f"[DEBUG] _download_excel: Download completed. Bytes received: {len(resp.content)}")
    return resp.content


def _upload_excel(file_bytes: bytes) -> None:
    logger.info(f"[DEBUG] _upload_excel: Initiating file upload to SharePoint. Payload size: {len(file_bytes)} bytes...")
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
    logger.info("[DEBUG] _upload_excel: Upload completed successfully.")


def _upload_excel_with_retry(file_bytes: bytes, retries: int = 5, delay: int = 60) -> None:
    logger.info(f"[DEBUG] _upload_excel_with_retry: Starting upload sequence (max retries: {retries}).")
    for attempt in range(1, retries + 1):
        try:
            logger.info(f"[DEBUG] _upload_excel_with_retry: Upload attempt {attempt}/{retries}...")
            _upload_excel(file_bytes)
            return
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 423 and attempt < retries:
                logger.warning(f"⚠️ File locked (attempt {attempt}/{retries}). Retrying in {delay}s...")
                time.sleep(delay)
            else:
                logger.error(f"[DEBUG] _upload_excel_with_retry: HTTP Error encountered: {e}")
                raise

# ──────────────────────────────────────────────────────────────────────────────
# Scraper
# ──────────────────────────────────────────────────────────────────────────────
captured_data: List[Dict] = []


def _on_response(response) -> None:
    try:
        if response.request.resource_type in ("xhr", "fetch"):
            try:
                data = response.json()
                captured_data.append({"url": response.url, "data": data})
                logger.info(f"📡 API URL: {response.url}")
                
                if "change_plant" in response.url or "gen_info" in response.url:
                    logger.info(f"   [DEBUG] TARGET API CAPTURED: {response.url}")
                else:
                    logger.info(f"   [DEBUG] NON-TARGET API CAPTURED (Saved to memory): {response.url}")
                
            except Exception as parse_exc:
                logger.warning(f"   [DEBUG] FAILED to parse JSON for {response.url}: {parse_exc}")
                pass
    except Exception as exc:
        logger.error(f"[DEBUG] _on_response encountered an error: {exc}")
        pass

def _log_api_capture_summary(api_data: List[Dict]) -> None:
    captured_urls = [str(item.get("url", "")) for item in api_data]
    logger.info("=== API Capture Summary ===")
    for keyword in ("change_plant", "gen_info"):
        matches = [url for url in captured_urls if keyword in url]
        if matches:
            logger.info(f" ✓ CAPTURED [{keyword}] (Count: {len(matches)})")
            for url in matches:
                logger.info(f"    -> {url}")
        else:
            logger.warning(f" ❌ NOT CAPTURED [{keyword}]")
    logger.info("===========================")

def run_scraper() -> List[Dict]:
    global captured_data
    captured_data = []

    try:
        logger.info("[DEBUG] run_scraper: Starting Playwright execution...")
        with sync_playwright() as p:
            logger.info(f"[DEBUG] run_scraper: Launching browser (headless={HEADLESS})...")
            browser = p.chromium.launch(headless=HEADLESS)
            context = browser.new_context()
            page    = context.new_page()

            logger.info("[DEBUG] run_scraper: Attaching API response listener...")
            page.on("response", _on_response)

            logger.info("Opening site...")
            page.goto("https://cloud.suryalog.com")

            logger.info("Attempting automatic login...")
            page.wait_for_selector("#loginId", timeout=10000)
            logger.info("[DEBUG] run_scraper: Filling login credentials...")
            page.fill("#loginId", SURYALOG_LOGIN_ID)
            page.wait_for_timeout(500)
            page.fill("#password", SURYALOG_PASSWORD)
            page.wait_for_timeout(500)

            page.click("#btnlogin")
            logger.info("Login button clicked, waiting for page to load...")

            logger.info("[DEBUG] run_scraper: Initial wait (8s)...")
            page.wait_for_timeout(8000)
            
            logger.info("Waiting for APIs...")
            logger.info("[DEBUG] run_scraper: Secondary wait (10s)...")
            page.wait_for_timeout(10000)
            
            logger.info("Triggering interaction...")
            page.mouse.click(100, 100)
            page.wait_for_timeout(5000)
            
            logger.info("Reloading...")
            page.reload()
            logger.info("[DEBUG] run_scraper: Final wait after reload (10s)...")
            page.wait_for_timeout(10000)

            if not captured_data:
                screenshot_path = SCRIPT_DIR / "login_failure.png"
                page.screenshot(path=str(screenshot_path))
                logger.warning(
                    f"0 responses captured. Screenshot → {screenshot_path}\n"
                    "  Set SURYALOG_HEADLESS=false in .env and re-run to debug."
                )

            logger.info("[DEBUG] run_scraper: Closing browser...")
            browser.close()

    except Exception as exc:
        logger.warning(f"Browser error (continuing): {exc}")

    logger.info(f"Total captured API responses: {len(captured_data)}")
    for item in captured_data:
        logger.info(f"  → {item['url']}")

    _log_api_capture_summary(captured_data)

    return captured_data

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _safe_float(val, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _extract_direct_day_generation_kwh(live_data: Dict, last_log: Dict) -> float:
    candidate_keys = [
        "day_generation", "dayGeneration", "day_gen", "daily_generation",
        "today_generation", "todayGeneration", "today_gen",
        "plant_day_generation", "day_generation_kwh", "dayGenerationKwh",
    ]

    containers: List[Dict] = []
    if isinstance(last_log, dict):
        containers.append(last_log)
        for key, value in last_log.items():
            if key in {"inverter", "meter", "smb"}:
                continue
            if isinstance(value, dict):
                containers.append(value)

    if isinstance(live_data, dict):
        containers.append(live_data)
        for key, value in live_data.items():
            if key == "lastLogData":
                continue
            if isinstance(value, dict):
                containers.append(value)

    for container in containers:
        for candidate in candidate_keys:
            if candidate in container:
                value = _safe_float(container.get(candidate), 0.0)
                if value > 0:
                    return value

    return 0.0

def _find_numeric_by_keys(payload: Any, key_candidates: set[str]) -> Optional[float]:
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
    """Extract yesterday generation (kWh) from known API patterns, specifically targeting duration blocks."""
    
    # --- NEW STRATEGY: Search for the specific report block (duration: 46800) ---
    def find_daily_duration_value(payload: Any) -> Optional[float]:
        if isinstance(payload, dict):
            # If this dictionary IS the target report block
            if payload.get("duration") == 46800 and "value" in payload:
                val = _safe_float(payload.get("value"))
                if val > 0:
                    return val
            # Otherwise, recursively search its children
            for v in payload.values():
                res = find_daily_duration_value(v)
                if res is not None:
                    return res
        elif isinstance(payload, list):
            # Search lists BACKWARDS to get the most recent report first
            for item in reversed(payload):
                res = find_daily_duration_value(item)
                if res is not None:
                    return res
        return None

    # Check all available payloads for the specific 46800 duration block
    for payload in [last_log, live_data, plant_data]:
        found_val = find_daily_duration_value(payload)
        if found_val is not None:
            logger.info(f"[DEBUG] Found yesterday generation via duration=46800 block: {found_val}")
            return round(found_val, 2)

    # --- FALLBACK 1: Legacy key candidates ---
    key_candidates = {
        "whyday", "whyd", "whyesterday", "whyesterdayday", "whyesterdaygen",
        "yesterdaygen", "yesterdaygeneration", "previousdaygeneration",
        "prevdaygeneration", "ydaygeneration",
    }
    direct = _find_numeric_by_keys(last_log, key_candidates)
    if direct is not None:
        return max(0.0, direct)

    # --- FALLBACK 2: Sum inverter-level yesterday fields if present ---
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
    logger.info(f"[DEBUG] _select_primary_meter: Evaluating {len(meters)} meters...")
    for key, meter_data in meters.items():
        if isinstance(meter_data, dict):
            if meter_data.get("meter_online", 0) == 1 and _safe_float(meter_data.get("VLL")) > 0:
                logger.info(f"[DEBUG] _select_primary_meter: Selected '{key}' (Online with VLL > 0)")
                return meter_data

    for key, meter_data in meters.items():
        if isinstance(meter_data, dict) and meter_data.get("meter_online", 0) == 1:
            logger.info(f"[DEBUG] _select_primary_meter: Selected '{key}' (Online fallback)")
            return meter_data

    for key, meter_data in meters.items():
        if isinstance(meter_data, dict):
            logger.info(f"[DEBUG] _select_primary_meter: Selected '{key}' (Absolute fallback)")
            return meter_data

    logger.warning("[DEBUG] _select_primary_meter: No valid meter found in data!")
    return None

# ──────────────────────────────────────────────────────────────────────────────
# Data extraction
# ──────────────────────────────────────────────────────────────────────────────

def _extract_row(api_data: List[Dict]) -> Dict[str, Any]:
    logger.info("[DEBUG] _extract_row: Commencing extraction logic...")
    if not api_data:
        raise ValueError("No API data captured. Aborting.")

    try:
        plant_data = _find_change_plant(api_data)
        live_data  = _find_gen_info(api_data)
        logger.info("[DEBUG] _extract_row: Successfully extracted target API payloads.")
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

    logger.info("[DEBUG] _extract_row: Extracting plant capacities...")
    plant_info = plant_data.get("plantInfo", {})
    row["DC Capacity (kWp)"] = _safe_float(plant_info.get("dc_size"), 598.6)
    row["AC Capacity (kW)"]  = _safe_float(plant_info.get("ac_size"), 500.0)

    last_log = live_data.get("lastLogData", {})

    if "inverter" in last_log and isinstance(last_log["inverter"], dict):
        logger.info("[DEBUG] _extract_row: Extracting inverter data...")
        total_dc_w    = 0.0
        total_ac_w    = 0.0
        total_day_kwh = 0.0

        all_inverters = [item for item in last_log["inverter"].items() if isinstance(item[1], dict)]
        sorted_inverters = sorted(all_inverters, key=lambda item: str(item[0]))

        for inv_id, inv in sorted_inverters:
            total_dc_w    += _safe_float(inv.get("DC_W"))
            total_ac_w    += _safe_float(inv.get("WT"))
            total_day_kwh += _safe_float(inv.get("WHDay"))

        for i, (inv_id, inv) in enumerate(sorted_inverters[:5], start=1):
            if not isinstance(inv, dict):
                continue

            ac_w    = _safe_float(inv.get("WT"))

            row[f"Inverter{i}_status"] = _get_device_status(inv.get("suryalog_status"))
            row[f"Inverter{i}"]        = round(ac_w / 1000.0, 3)

        row["DC Power (kW)"]        = round(total_dc_w / 1000.0, 2)
        row["AC Power (kW)"]        = round(total_ac_w / 1000.0, 2)
        row["Active Power (kW)"]    = row["AC Power (kW)"]

        direct_day_gen_kwh = _extract_direct_day_generation_kwh(live_data, last_log)
        if direct_day_gen_kwh > 0:
            row["Day Generation (kWh)"] = round(direct_day_gen_kwh, 2)
        else:
            row["Day Generation (kWh)"] = round(total_day_kwh, 2)

    # UPDATED LOGIC HERE: Passing all payloads
    yesterday_gen_kwh = _extract_yesterday_generation_kwh(last_log, plant_data, live_data)
    row["Yesterday Generation (kWh)"] = round(yesterday_gen_kwh, 2)
    logger.info(f"[DEBUG] _extract_row: Yesterday Generation (kWh)={row['Yesterday Generation (kWh)']}")

    if "meter" in last_log and isinstance(last_log["meter"], dict):
        logger.info("[DEBUG] _extract_row: Extracting meter data...")
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
        logger.info("[DEBUG] _extract_row: Extracting SMB data...")
        for i, (smb_id, smb) in enumerate(list(last_log["smb"].items())[:5], start=1):
            if not isinstance(smb, dict):
                continue
            row[f"SMB{i}_status"] = _get_device_status(smb.get("suryalog_status"))
            row[f"SMB{i}"]        = round(_safe_float(smb.get("WTOT")) / 1000.0, 3)

    logger.info("[DEBUG] _extract_row: Row extraction complete.")
    return row

# ──────────────────────────────────────────────────────────────────────────────
# Excel update
# ──────────────────────────────────────────────────────────────────────────────

def update_excel_in_memory(file_bytes: bytes, new_row: Dict) -> bytes:
    logger.info("Loading Excel file into Pandas…")
    logger.info(f"[DEBUG] update_excel_in_memory: Received base file size of {len(file_bytes)} bytes.")
    df = pd.read_excel(io.BytesIO(file_bytes), sheet_name="Sheet1")
    logger.info(f"[DEBUG] update_excel_in_memory: Existing DataFrame has {len(df)} rows.")

    y_col = next(
        (
            c for c in df.columns
            if "yesterday" in str(c).lower() and "gen" in str(c).lower()
        ),
        None,
    )
    if y_col and "Yesterday Generation (kWh)" in new_row and y_col != "Yesterday Generation (kWh)":
        new_row[y_col] = new_row.pop("Yesterday Generation (kWh)")

    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    df.drop_duplicates(subset=["Date", "Time"], keep="last", inplace=True)
    df.sort_values(["Date", "Time"], inplace=True)
    logger.info(f"[DEBUG] update_excel_in_memory: Appended row and applied deduplication. New total is {len(df)} rows.")

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Sheet1", index=False)
    buf.seek(0)
    
    out_bytes = buf.read()
    logger.info(f"[DEBUG] update_excel_in_memory: Excel serialization complete. Output size is {len(out_bytes)} bytes.")
    return out_bytes

# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    logger.info("=== SuryaLogix → SharePoint Excel Scraper starting ===")
    logger.info(f"Headless  : {HEADLESS}  (set SURYALOG_HEADLESS=false in .env to watch the browser)")
    logger.info(f"Login ID  : {SURYALOG_LOGIN_ID!r}  ← empty means .env not found or key missing")
    logger.info(f"Password  : {'SET ✓' if SURYALOG_PASSWORD else 'EMPTY ← login will fail!'}")

    logger.info("[DEBUG] Main Step 1: Commencing run_scraper()...")
    raw_data = run_scraper()

    logger.info("[DEBUG] Main Step 2: Commencing _extract_row()...")
    try:
        row = _extract_row(raw_data)
        logger.info(f"✓ Slot: {row['Date']} {row['Time']}")
        logger.info(
            f"  DC={row.get('DC Power (kW)', 'N/A')} kW  "
            f"AC={row.get('AC Power (kW)', 'N/A')} kW  "
            f"DayGen={row.get('Day Generation (kWh)', 'N/A')} kWh  "
            f"Meter VLL={row.get('Voltage Phase-to-Phase (V)', 'N/A')} V"
        )
    except ValueError as exc:
        logger.error(f"❌ {exc}")
        logger.info("Skipping SharePoint upload for this cycle.")
        sys.exit(1)

    logger.info("[DEBUG] Main Step 3: Read -> Modify -> Write sequence to SharePoint...")
    try:
        logger.info("Downloading UnifiedSolarData.xlsx from SharePoint…")
        original = _download_excel()
        updated  = update_excel_in_memory(original, row)
        logger.info("Uploading updated file back to SharePoint…")
        _upload_excel_with_retry(updated)
        logger.info("✅ SharePoint upload SUCCESS!")
    except Exception as exc:
        logger.error(f"❌ SharePoint pipeline FAILED: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()