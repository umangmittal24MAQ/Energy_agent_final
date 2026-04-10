"""
scrape_to_sharepoint.py
=======================
Refactored web scraper for SuryaLogix → SharePoint Online (Excel Document Library).

Schedule: Every 30 minutes (via APScheduler / Windows Task Scheduler / cron).

Changes:
  - Replaced SharePoint List API with SharePoint Drive API.
  - Downloads UnifiedSolarData.xlsx into Pandas memory.
  - Maps scraped data to the exact legacy CSV headers.
  - Appends data, drops duplicates based on Date/Time, and overwrites the file.
"""

from __future__ import annotations

import io
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
import pandas as pd
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
# Environment & Configuration
# ──────────────────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent

def _load_env() -> None:
    search_paths = [
        SCRIPT_DIR, SCRIPT_DIR.parents[0], SCRIPT_DIR.parents[1],
        SCRIPT_DIR.parents[2], SCRIPT_DIR.parents[3], Path.cwd(),
    ]
    for path in search_paths:
        env_file = path / ".env"
        if env_file.exists():
            load_dotenv(dotenv_path=env_file, override=False)
            logger.info(f"Loaded .env from: {env_file}")
            return
    load_dotenv(override=False)

_load_env()

SURYALOG_LOGIN_ID = os.getenv("SURYALOG_LOGIN_ID", "").strip()
SURYALOG_PASSWORD = os.getenv("SURYALOG_PASSWORD", "").strip()
SHAREPOINT_TENANT_ID = os.getenv("SHAREPOINT_TENANT_ID", "").strip()
SHAREPOINT_CLIENT_ID = os.getenv("SHAREPOINT_CLIENT_ID", "").strip()
SHAREPOINT_CLIENT_SECRET = os.getenv("SHAREPOINT_CLIENT_SECRET", "").strip()

# SharePoint Drive Configuration
HOSTNAME = "testmaq.sharepoint.com"
SITE_PATH = "/Admin" 
DRIVE_NAME = "Private" 
FILE_PATH_IN_DRIVE = "22. Facilities Report/MIPL/Noida/2. Electrical data/UnifiedSolarData.xlsx"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"

_access_token: Optional[str] = None
_site_id: Optional[str] = None
_drive_id: Optional[str] = None

# ──────────────────────────────────────────────────────────────────────────────
# SharePoint Drive API Helpers
# ──────────────────────────────────────────────────────────────────────────────
def _get_token() -> str:
    global _access_token
    if _access_token: return _access_token
    url = f"https://login.microsoftonline.com/{SHAREPOINT_TENANT_ID}/oauth2/v2.0/token"
    resp = requests.post(url, data={
        "grant_type": "client_credentials",
        "client_id": SHAREPOINT_CLIENT_ID,
        "client_secret": SHAREPOINT_CLIENT_SECRET,
        "scope": "https://graph.microsoft.com/.default",
    }, timeout=30)
    resp.raise_for_status()
    _access_token = resp.json()["access_token"]
    return _access_token

def _get_site_and_drive_ids() -> tuple[str, str]:
    global _site_id, _drive_id
    if _site_id and _drive_id: return _site_id, _drive_id
    
    headers = {"Authorization": f"Bearer {_get_token()}"}
    site_url = f"{GRAPH_BASE}/sites/{HOSTNAME}:{SITE_PATH}"
    _site_id = requests.get(site_url, headers=headers).json()["id"]

    drives_url = f"{GRAPH_BASE}/sites/{_site_id}/drives"
    for drive in requests.get(drives_url, headers=headers).json().get("value", []):
        if drive["name"] == DRIVE_NAME:
            _drive_id = drive["id"]
            return _site_id, _drive_id
    raise Exception("Drive not found")

def _download_excel() -> bytes:
    site_id, drive_id = _get_site_and_drive_ids()
    safe_path = requests.utils.quote(FILE_PATH_IN_DRIVE, safe="/")
    url = f"{GRAPH_BASE}/sites/{site_id}/drives/{drive_id}/root:/{safe_path}:/content"
    resp = requests.get(url, headers={"Authorization": f"Bearer {_get_token()}"})
    resp.raise_for_status()
    return resp.content

def _upload_excel(file_bytes: bytes) -> None:
    site_id, drive_id = _get_site_and_drive_ids()
    safe_path = requests.utils.quote(FILE_PATH_IN_DRIVE, safe="/")
    url = f"{GRAPH_BASE}/sites/{site_id}/drives/{drive_id}/root:/{safe_path}:/content"
    headers = {
        "Authorization": f"Bearer {_get_token()}",
        "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    }
    resp = requests.put(url, headers=headers, data=file_bytes)
    resp.raise_for_status()

# ──────────────────────────────────────────────────────────────────────────────
# SuryaLogix Scraper
# ──────────────────────────────────────────────────────────────────────────────
captured_data: List[Dict] = []

def _capture_api(response) -> None:
    try:
        if response.request.resource_type in ("xhr", "fetch"):
            try:
                data = response.json()
                captured_data.append({"url": response.url, "data": data})
            except Exception: pass
    except Exception: pass

def run_scraper() -> List[Dict]:
    global captured_data
    captured_data = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            page.on("response", _capture_api)

            logger.info("Opening https://cloud.suryalog.com …")
            page.goto("https://cloud.suryalog.com")
            page.wait_for_selector("#loginId", timeout=15000)
            page.fill("#loginId", SURYALOG_LOGIN_ID)
            page.wait_for_timeout(500)
            page.fill("#password", SURYALOG_PASSWORD)
            page.wait_for_timeout(500)
            page.click("#btnlogin")

            logger.info("Waiting for post-login APIs …")
            page.wait_for_timeout(12000)
            page.mouse.click(100, 100)
            page.wait_for_timeout(5000)
            page.reload()
            page.wait_for_timeout(10000)
            browser.close()
    except Exception as exc:
        logger.warning(f"Browser error (continuing with partial data): {exc}")

    logger.info(f"Captured {len(captured_data)} API responses")
    return captured_data

# ──────────────────────────────────────────────────────────────────────────────
# Data Extraction & Pandas Processing
# ──────────────────────────────────────────────────────────────────────────────
# ──────────────────────────────────────────────────────────────────────────────
# Data Extraction & Pandas Processing (With Fuzzy Matching)
# ──────────────────────────────────────────────────────────────────────────────
# ──────────────────────────────────────────────────────────────────────────────
# Data Extraction & Pandas Processing (With Legacy Status & Fixes)
# ──────────────────────────────────────────────────────────────────────────────
def _safe_float(val, default=None) -> Optional[float]:
    try: return float(val)
    except (TypeError, ValueError): return default

def fuzzy_find(data_dict: dict, possible_keys: list, default=None):
    if not isinstance(data_dict, dict): return default
    for pk in possible_keys:
        if pk in data_dict and data_dict[pk] is not None: return data_dict[pk]
    for key, value in data_dict.items():
        if value is None: continue
        key_lower = str(key).lower()
        for pk in possible_keys:
            if pk.lower() in key_lower or key_lower in pk.lower(): return value
    return default

def get_device_status_text(status_code):
    """Exact replica of legacy get_device_status_text logic"""
    if status_code is None: return "FAULT"
    
    # Handle string statuses if API returns text
    if isinstance(status_code, str) and not status_code.isdigit():
        s = status_code.upper()
        if s in ("ON", "ACTIVE"): return s
        return "FAULT"
        
    # Handle numeric codes based on legacy rules
    try:
        code = int(status_code)
        if code == 0 or code == 17: return "ON"
        elif code > 0: return "ACTIVE"
        else: return "OFF"
    except (ValueError, TypeError):
        return "FAULT"

def _extract_solar_fields(raw_data: List[Dict]) -> Dict[str, Any]:
    now = datetime.now()
    rounded_minute = 0 if now.minute < 30 else 30
    slot_time = now.replace(minute=rounded_minute, second=0, microsecond=0).strftime("%H:%M")
    date_str = now.strftime("%Y-%m-%d")

    fields: Dict[str, Any] = {
        "Date": date_str,
        "Date Formatted": date_str,
        "Time": slot_time,
    }

    if not raw_data or len(raw_data) < 2:
        logger.warning("No API data captured — writing timestamp-only row")
        return fields

    try:
        # 1. Extract Plant Info
        plant_info = raw_data[0]['data'].get('plantInfo', {})
        fields["DC Capacity (kWp)"] = _safe_float(fuzzy_find(plant_info, ['dc_size', 'dcCapacity']))
        fields["AC Capacity (kW)"] = _safe_float(fuzzy_find(plant_info, ['ac_size', 'acCapacity']))

        # 2. Extract Live Telemetry
        live_data = raw_data[1]['data']
        last_log = live_data.get('lastLogData', {})

        # --- A. Inverter Data ---
        inverters = last_log.get('inverter', {})
        if inverters and isinstance(inverters, dict):
            total_dc_w = 0.0
            total_ac_w = 0.0
            total_day_gen = 0.0
            
            for i, (inv_id, inv_data) in enumerate(list(inverters.items())[:5], start=1):
                if not isinstance(inv_data, dict): continue
                
                total_dc_w += _safe_float(fuzzy_find(inv_data, ['DC_W', 'dcPower']), 0)
                total_ac_w += _safe_float(fuzzy_find(inv_data, ['WT', 'acPower', 'activePower']), 0)
                
                # FIXED: No longer dividing WHDay by 1000 so 1519 stays 1519.00
                total_day_gen += _safe_float(fuzzy_find(inv_data, ['WHDay', 'dayEnergy']), 0)
                
                # FIXED: Using legacy status mapping
                status_code = fuzzy_find(inv_data, ['suryalog_status', 'status', 'deviceStatus'])
                fields[f"Inverter{i}_status"] = get_device_status_text(status_code)
                
                inv_power = fuzzy_find(inv_data, ['WT', 'acPower'])
                fields[f"Inverter{i}"] = _safe_float(inv_power) / 1000.0 if inv_power else 0.0

            fields["DC Power (kW)"] = round(total_dc_w / 1000.0, 2)
            fields["AC Power (kW)"] = round(total_ac_w / 1000.0, 2)
            fields["Active Power (kW)"] = fields["AC Power (kW)"]
            
            # Record the full Day Generation value
            fields["Day Generation (kWh)"] = round(total_day_gen, 2)

        # --- B. Meter Data ---
        meters = last_log.get('meter', {})
        primary_meter = None
        
        if meters and isinstance(meters, dict):
            for m_id, m_data in meters.items():
                if isinstance(m_data, dict) and m_data.get('meter_online') == 1:
                    primary_meter = m_data
                    break
            if not primary_meter:
                primary_meter = list(meters.values())[0] if meters.values() else {}

            if primary_meter:
                fields["Voltage Phase-to-Phase (V)"] = _safe_float(fuzzy_find(primary_meter, ['VLL', 'voltageLL']))
                fields["Voltage Phase-to-Neutral (V)"] = _safe_float(fuzzy_find(primary_meter, ['VLN', 'voltageLN']))
                fields["V1 (V)"] = _safe_float(fuzzy_find(primary_meter, ['V1']))
                fields["V2 (V)"] = _safe_float(fuzzy_find(primary_meter, ['V2']))
                fields["V3 (V)"] = _safe_float(fuzzy_find(primary_meter, ['V3']))
                
                i1 = _safe_float(fuzzy_find(primary_meter, ['I1']), 0)
                i2 = _safe_float(fuzzy_find(primary_meter, ['I2']), 0)
                i3 = _safe_float(fuzzy_find(primary_meter, ['I3']), 0)
                total_current = i1 + i2 + i3
                fields["Current Total (A)"] = round(total_current, 2)
                fields["Current Average (A)"] = round(total_current / 3, 2) if total_current > 0 else 0
                
                fields["Apparent Power (kVA)"] = round(_safe_float(fuzzy_find(primary_meter, ['VAT', 'apparentPower']), 0) / 1000.0, 2)
                fields["Power Factor"] = _safe_float(fuzzy_find(primary_meter, ['PFT', 'powerFactor']))
                fields["Frequency (Hz)"] = _safe_float(fuzzy_find(primary_meter, ['FREQ', 'frequency']))
                
                fields["Total Import (kWh)"] = _safe_float(fuzzy_find(primary_meter, ['WHImp', 'totalImport']))
                fields["Total Export (kWh)"] = _safe_float(fuzzy_find(primary_meter, ['WHExp', 'totalExport']))

        # --- C. SMB Data ---
        smb_dict = fuzzy_find(last_log, ['smb', 'smbData', 'smbboxes']) or {}
        if smb_dict and isinstance(smb_dict, dict):
            for i, (smb_id, smb_data) in enumerate(list(smb_dict.items())[:5], start=1):
                if not isinstance(smb_data, dict): continue
                
                # FIXED: Using legacy status mapping
                status_code = fuzzy_find(smb_data, ['suryalog_status', 'status', 'deviceStatus'])
                fields[f"SMB{i}_status"] = get_device_status_text(status_code)
                    
                smb_power = fuzzy_find(smb_data, ['WTOT', 'power_w', 'power'])
                fields[f"SMB{i}"] = _safe_float(smb_power) / 1000.0 if smb_power else 0.0

    except Exception as e:
        logger.error(f"Error parsing JSON fields: {e}")

    return fields

def update_excel_in_memory(file_bytes: bytes, new_row: dict) -> bytes:
    logger.info("Loading Excel file into Pandas...")
    df = pd.read_excel(io.BytesIO(file_bytes), sheet_name='Sheet1')
    
    new_df = pd.DataFrame([new_row])
    df = pd.concat([df, new_df], ignore_index=True)
    
    # Deduplicate: If multiple runs happen in the same 30-min slot, keep the latest
    df.drop_duplicates(subset=["Date", "Time"], keep="last", inplace=True)
    
    output_buffer = io.BytesIO()
    with pd.ExcelWriter(output_buffer, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Sheet1', index=False)
    
    output_buffer.seek(0)
    return output_buffer.read()

# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────
def main() -> None:
    logger.info("=== SuryaLogix → SharePoint Excel Scraper starting ===")

    # 1. Scrape
    raw_data = run_scraper()

    # 2. Extract Fields (Exact Excel Headers)
    fields = _extract_solar_fields(raw_data)
    logger.info(f"Captured data for slot: {fields['Date']} {fields['Time']}")

    # 3. Read -> Modify -> Write via Graph API
    try:
        logger.info("Downloading UnifiedSolarData.xlsx from SharePoint...")
        original_excel = _download_excel()
        
        updated_excel = update_excel_in_memory(original_excel, fields)
        
        logger.info("Uploading updated UnifiedSolarData.xlsx back to SharePoint...")
        _upload_excel(updated_excel)
        logger.info("✅ SharePoint upload SUCCESS!")
        
    except Exception as exc:
        logger.error(f"❌ SharePoint pipeline FAILED: {exc}")
        sys.exit(1)

if __name__ == "__main__":
    main()