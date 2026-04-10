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
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

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
    # Build search list: standard parents + sibling "energy-dashboard" folders.
    # Structure: app/agents/ingestion/scrape_to_sharepoint.py
    #            app/energy-dashboard/.env  ← sibling of agents/, not a parent
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

# ── Credential sanity check ───────────────────────────────────────────────────
# If these are empty the login will silently fail and 0 APIs will be captured.
# Fix: ensure your .env file exists in the script folder with correct values,
# OR set SURYALOG_LOGIN_ID / SURYALOG_PASSWORD as system environment variables.
if not SURYALOG_LOGIN_ID or not SURYALOG_PASSWORD:
    logging.warning(
        "⚠️  SURYALOG_LOGIN_ID or SURYALOG_PASSWORD is empty! "
        "Login will fail. Check your .env file."
    )
SHAREPOINT_TENANT_ID     = os.getenv("SHAREPOINT_TENANT_ID", "").strip()
SHAREPOINT_CLIENT_ID     = os.getenv("SHAREPOINT_CLIENT_ID", "").strip()
SHAREPOINT_CLIENT_SECRET = os.getenv("SHAREPOINT_CLIENT_SECRET", "").strip()

# Set SURYALOG_HEADLESS=false in .env to watch the browser
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

# ──────────────────────────────────────────────────────────────────────────────
# Scraper  — identical to working scrape.py (plain new_context, same timings)
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
            # ✅ Plain launch + plain new_context — exactly like working scrape.py
            browser = p.chromium.launch(headless=HEADLESS)
            context = browser.new_context()
            page    = context.new_page()

            # Attach BEFORE navigation
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

            # ✅ Exact same wait sequence as scrape.py
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
    """
    Confirmed from real data: suryalog_status is a large int bitmask.
    Values seen: 0 (SMBs=ON), 7922852, 1079007261 (inverters=ACTIVE).
    Logic: 0 or 17 → ON, any other positive int → ACTIVE, negative → OFF.
    """
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
    """Return the first change_plant response."""
    for item in api_data:
        if "change_plant" in item["url"]:
            return item["data"]
    raise ValueError("No change_plant response found in captured data.")


def _find_gen_info(api_data: List[Dict]) -> Dict:
    """Return the first gen_info response."""
    for item in api_data:
        if "gen_info" in item["url"]:
            return item["data"]
    raise ValueError("No gen_info response found in captured data.")


def _select_primary_meter(meters: Dict) -> Optional[Dict]:
    """
    Select the best meter for solar readings.

    Priority (confirmed from real data — 7 meters, PRIMARY_METER_ID not present):
      1. First online meter with VLL > 0  (Meter1 in real data: VLL=425, WT=236340)
      2. Any online meter
      3. Any meter (last resort)
    """
    # Pass 1: online + VLL > 0
    for meter_data in meters.values():
        if isinstance(meter_data, dict):
            if meter_data.get("meter_online", 0) == 1 and _safe_float(meter_data.get("VLL")) > 0:
                return meter_data

    # Pass 2: any online meter
    for meter_data in meters.values():
        if isinstance(meter_data, dict) and meter_data.get("meter_online", 0) == 1:
            return meter_data

    # Pass 3: any meter at all
    for meter_data in meters.values():
        if isinstance(meter_data, dict):
            return meter_data

    return None

# ──────────────────────────────────────────────────────────────────────────────
# Data extraction  — verified against real captured_api_data.json
# ──────────────────────────────────────────────────────────────────────────────

def _extract_row(api_data: List[Dict]) -> Dict[str, Any]:
    """
    Build the Excel row from captured API data.

    All field names and units verified against real JSON:
      WHDay   → already kWh  (confirmed: sum=1357.4 kWh for 598kWp plant)
      DC_W    → Watts
      WT      → Watts
      VLL     → Volts
      WT(m)   → Watts  (meter active power)
      VAT     → VA     (apparent power)
      WHImp   → kWh cumulative
      WHExp   → kWh cumulative
      WTOT    → Watts  (SMB)
    """
    if not api_data:
        raise ValueError("No API data captured. Aborting.")

    # Find the right responses by URL rather than by index
    # (order can vary: real data shows [cp, gi, gi, cp, gi])
    try:
        plant_data = _find_change_plant(api_data)
        live_data  = _find_gen_info(api_data)
    except ValueError as exc:
        raise ValueError(f"{exc}. Got URLs: {[d['url'] for d in api_data]}")

    # ── Timestamp ─────────────────────────────────────────────────────────────
    now = datetime.now()
    rounded_minute = 0 if now.minute < 30 else 30
    slot_time = now.replace(minute=rounded_minute, second=0, microsecond=0).strftime("%H:%M")
    date_str  = now.strftime("%Y-%m-%d")

    row: Dict[str, Any] = {
        "Date":           date_str,
        "Date Formatted": date_str,
        "Time":           slot_time,
    }

    # ── 1. Plant capacities ───────────────────────────────────────────────────
    plant_info = plant_data.get("plantInfo", {})
    row["DC Capacity (kWp)"] = _safe_float(plant_info.get("dc_size"), 598.6)
    row["AC Capacity (kW)"]  = _safe_float(plant_info.get("ac_size"), 500.0)

    last_log = live_data.get("lastLogData", {})

    # ── 2. Inverters (5 inverters confirmed) ──────────────────────────────────
    if "inverter" in last_log and isinstance(last_log["inverter"], dict):
        total_dc_w    = 0.0
        total_ac_w    = 0.0
        total_day_kwh = 0.0

        for i, (inv_id, inv) in enumerate(list(last_log["inverter"].items())[:5], start=1):
            if not isinstance(inv, dict):
                continue

            dc_w    = _safe_float(inv.get("DC_W"))
            ac_w    = _safe_float(inv.get("WT"))
            # WHDay confirmed to be kWh already (values: 111.5, 366.9, 418.2, 329.6, 131.2)
            wh_day  = _safe_float(inv.get("WHDay"))

            total_dc_w    += dc_w
            total_ac_w    += ac_w
            total_day_kwh += wh_day

            row[f"Inverter{i}_status"] = _get_device_status(inv.get("suryalog_status"))
            row[f"Inverter{i}"]        = round(ac_w / 1000.0, 3)   # W → kW

        row["DC Power (kW)"]        = round(total_dc_w / 1000.0, 2)
        row["AC Power (kW)"]        = round(total_ac_w / 1000.0, 2)
        row["Active Power (kW)"]    = row["AC Power (kW)"]
        row["Day Generation (kWh)"] = round(total_day_kwh, 2)   # already kWh

    # ── 3. Meter (7 meters; select best online one with VLL > 0) ─────────────
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

            # WT and VAT are in Watts/VA → convert to kW/kVA
            row["Apparent Power (kVA)"] = round(_safe_float(primary.get("VAT")) / 1000.0, 2)
            row["Power Factor"]         = round(_safe_float(primary.get("PFT")), 3)
            row["Frequency (Hz)"]       = round(_safe_float(primary.get("FREQ")), 2)

            # WHImp / WHExp are cumulative kWh totals
            row["Total Import (kWh)"] = round(_safe_float(primary.get("WHImp")), 2)
            row["Total Export (kWh)"] = round(_safe_float(primary.get("WHExp")), 2)

    # ── 4. SMBs (5 SMBs confirmed) ────────────────────────────────────────────
    if "smb" in last_log and isinstance(last_log["smb"], dict):
        for i, (smb_id, smb) in enumerate(list(last_log["smb"].items())[:5], start=1):
            if not isinstance(smb, dict):
                continue
            row[f"SMB{i}_status"] = _get_device_status(smb.get("suryalog_status"))
            row[f"SMB{i}"]        = round(_safe_float(smb.get("WTOT")) / 1000.0, 3)  # W → kW

    return row

# ──────────────────────────────────────────────────────────────────────────────
# Excel update
# ──────────────────────────────────────────────────────────────────────────────

def update_excel_in_memory(file_bytes: bytes, new_row: Dict) -> bytes:
    logger.info("Loading Excel file into Pandas…")
    df = pd.read_excel(io.BytesIO(file_bytes), sheet_name="Sheet1")

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
    logger.info(f"Headless  : {HEADLESS}  (set SURYALOG_HEADLESS=false in .env to watch the browser)")
    logger.info(f"Login ID  : {SURYALOG_LOGIN_ID!r}  ← empty means .env not found or key missing")
    logger.info(f"Password  : {'SET ✓' if SURYALOG_PASSWORD else 'EMPTY ← login will fail!'}")

    # 1. Scrape
    raw_data = run_scraper()

    # 2. Extract row
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

    # 3. Read → Modify → Write
    try:
        logger.info("Downloading UnifiedSolarData.xlsx from SharePoint…")
        original = _download_excel()
        updated  = update_excel_in_memory(original, row)
        logger.info("Uploading updated file back to SharePoint…")
        _upload_excel(updated)
        logger.info("✅ SharePoint upload SUCCESS!")
    except Exception as exc:
        logger.error(f"❌ SharePoint pipeline FAILED: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()