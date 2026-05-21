

import sys
import logging
import pandas as pd
import requests
import io
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import os

from app.agents.master_data_engine import (
    download_excel, 
    upload_excel, 
    _robust_parse_date, 
    _get_token,
    GRAPH_BASE,
    HOSTNAME,
    SITE_PATH # Default Operator site path
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# --- POWER APP LOCATION CONFIG (Cross-Site) ---
POWERAPP_SITE_PATH   = os.getenv("SHAREPOINT_POWERAPP_SITE_PATH", "/sites/OperationsDataHub").strip()
POWERAPP_DRIVE_NAME  = os.getenv("SHAREPOINT_POWERAPP_DRIVE_NAME", "Documents").strip()
POWERAPP_BASE_FOLDER = os.getenv("SHAREPOINT_POWERAPP_BASE_FOLDER", "").strip()
POWERAPP_FILE_NAME   = os.getenv("SHAREPOINT_POWERAPP_FILE_NAME", "PowerAppMeterReadings.xlsx").strip()

# --- OPERATOR LOCATION CONFIG ---
OPERATOR_FILE_NAME = os.getenv("SHAREPOINT_GRID_DIESEL_FILE_NAME", "Electrical Optimization (1).xlsx").strip()


def download_powerapp_excel(filename: str) -> pd.DataFrame:
    """Custom fetcher to retrieve the Power App sheet from a completely different SharePoint Site."""
    if not POWERAPP_DRIVE_NAME:
        logger.error("Missing PowerApp Drive configuration in environment variables.")
        raise ValueError("SHAREPOINT_POWERAPP_DRIVE_NAME must be set.")

    headers = {"Authorization": f"Bearer {_get_token()}"}
    
    # 1. Fetch the Site ID using the POWER APP specific site path
    site_url = f"{GRAPH_BASE}/sites/{HOSTNAME}:{POWERAPP_SITE_PATH}"
    site_resp = requests.get(site_url, headers=headers, timeout=30)
    
    if site_resp.status_code != 200:
        logger.error(f"Failed to find PowerApp Site at {POWERAPP_SITE_PATH}. Response: {site_resp.text}")
        site_resp.raise_for_status()
        
    site_id = site_resp.json()["id"]

    # 2. Fetch the Drive ID
    drives_resp = requests.get(f"{GRAPH_BASE}/sites/{site_id}/drives", headers=headers, timeout=30)
    drives_resp.raise_for_status()
    drive_id = next((d["id"] for d in drives_resp.json().get("value", []) if d["name"] == POWERAPP_DRIVE_NAME), None)
            
    if not drive_id:
        # Fallback: Sometimes "Shared Documents" is actually the name. Try that if "Documents" fails.
        if POWERAPP_DRIVE_NAME == "Documents":
            drive_id = next((d["id"] for d in drives_resp.json().get("value", []) if d["name"] == "Shared Documents"), None)
            
        if not drive_id:
            available = [d["name"] for d in drives_resp.json().get("value", [])]
            raise RuntimeError(f"Drive '{POWERAPP_DRIVE_NAME}' not found. Available drives: {available}")

    # 3. Construct File Path
    folder_path = POWERAPP_BASE_FOLDER if POWERAPP_BASE_FOLDER.endswith("/") else f"{POWERAPP_BASE_FOLDER}/"
    if folder_path == "/": folder_path = ""
    
    file_path = f"{folder_path}{filename}"
    safe_path = requests.utils.quote(file_path, safe="/")
    url = f"{GRAPH_BASE}/sites/{site_id}/drives/{drive_id}/root:/{safe_path}:/content"
    
    # 4. Download File
    resp = requests.get(url, headers=headers, timeout=60)
    if resp.status_code != 200:
        logger.error(f"Failed to download {filename} from {POWERAPP_SITE_PATH}. Response: {resp.text}")
        resp.raise_for_status()
    
    df = pd.read_excel(io.BytesIO(resp.content), sheet_name="Sheet1")
    
    # Clean headers
    if any("Unnamed" in str(c) for c in df.columns):
        for i, row in df.head(10).iterrows():
            if any("date" in str(val).lower() for val in row.values):
                df.columns = [str(c).strip().replace('\n', ' ') for c in row.values]
                df = df.iloc[i+1:].reset_index(drop=True)
                break
    else:
        df.columns = [str(c).strip().replace('\n', ' ') for c in df.columns]
        
    return df


def _parse_powerapp_date(series: pd.Series) -> pd.Series:
    """Handles both M/D/YYYY and YYYY-MM-DD gracefully."""
    parsed = pd.to_datetime(series, format="%m/%d/%Y", errors="coerce")
    if parsed.isna().any():
        parsed = parsed.fillna(pd.to_datetime(series, errors="coerce"))
    return parsed.dt.strftime("%Y-%m-%d")


def _extract_meter_reading(val) -> float:
    """
    Safely extracts numbers from PowerApp entries, stripping text like '/M'.
    Example: '12356565/M' -> 12356565.0
    """
    if pd.isna(val) or val is None:
        return 0.0
    
    cleaned = str(val).replace(",", "").strip()
    match = re.search(r"(\d+(?:\.\d+)?)", cleaned)
    if match:
        return float(match.group(1))
    return 0.0


def _get_latest_valid_8am(df_powerapp: pd.DataFrame, target_date: str) -> float:
    """
    Scans all rows for a specific date and returns the last valid 8am reading.
    Solves the issue where Power App creates multiple rows per day and leaves 
    8am_entry blank on later rows (e.g. 2pm updates).
    """
    day_rows = df_powerapp[df_powerapp['Date'] == target_date]
    
    if "8am_entry" not in day_rows.columns:
        return 0.0
        
    valid_readings = []
    for val in day_rows["8am_entry"]:
        extracted = _extract_meter_reading(val)
        if extracted > 0:
            valid_readings.append(extracted)
            
    # Return the last valid entry found for this date (handles operator corrections)
    return valid_readings[-1] if valid_readings else 0.0


def sync_grid_consumption(operator_date_str: str):
    logger.info(f"=== PowerApp Sync Engine started for Operator Date: {operator_date_str} ===")
    
    try:
        logger.info(f"Downloading {POWERAPP_FILE_NAME} from Drive: {POWERAPP_DRIVE_NAME}...")
        df_powerapp = download_powerapp_excel(POWERAPP_FILE_NAME)
        
        logger.info(f"Downloading {OPERATOR_FILE_NAME} from Default Operator Drive...")
        df_operator = download_excel(OPERATOR_FILE_NAME)
    except Exception as e:
        logger.error(f"Failed to download required Excel files: {e}")
        return

    df_powerapp['Date'] = _parse_powerapp_date(df_powerapp['Date'])
    df_operator['Date'] = _robust_parse_date(df_operator['Date'])

    target_dt = pd.to_datetime(operator_date_str).date()
    yesterday_str = (target_dt - timedelta(days=1)).strftime("%Y-%m-%d")

    # Use the new multi-row scanner to find the actual 8 AM numbers
    today_8am = _get_latest_valid_8am(df_powerapp, operator_date_str)
    yday_8am = _get_latest_valid_8am(df_powerapp, yesterday_str)

    if today_8am <= 0 or yday_8am <= 0:
        logger.warning(f"Invalid or missing 8am readings detected. Today ({operator_date_str}): {today_8am}, Yesterday ({yesterday_str}): {yday_8am}")
        return

    raw_consumption = round(today_8am - yday_8am, 2)

    if raw_consumption < 0:
        logger.error(f"Negative consumption calculated ({raw_consumption}).")
        return

    # Multiply by 14 to convert meter pulse count to actual KWh units
    grid_consumption = round(raw_consumption * 14, 2)

    logger.info(
        f"Calculated Consumption for {yesterday_str}: "
        f"({today_8am} - {yday_8am}) × 14 = {grid_consumption} KWh"
    )

    grid_units_col = next((c for c in df_operator.columns if "grid" in str(c).lower() and "unit" in str(c).lower()), None)
    
    if not grid_units_col:
        logger.error("Could not find the 'Grid Units Consumed (KWh)' column in the Operator Sheet.")
        return

    operator_row_idx = df_operator[df_operator['Date'] == operator_date_str].index

    if not operator_row_idx.empty:
        df_operator.loc[operator_row_idx, grid_units_col] = grid_consumption
        logger.info(f"Updated existing operator row for {operator_date_str} with {grid_consumption} units.")
    else:
        new_row = {
            "Date": target_dt.strftime("%d-%b-%y"), 
            "Day": target_dt.strftime("%A"),
            grid_units_col: grid_consumption,
            "Status": "Pending" 
        }
        df_operator = pd.concat([df_operator, pd.DataFrame([new_row])], ignore_index=True)
        logger.info(f"Created new operator row for {operator_date_str} with {grid_consumption} units.")

    try:
        logger.info("Uploading updated Operator sheet to SharePoint...")
        upload_excel(OPERATOR_FILE_NAME, df_operator)
        logger.info("✅ PowerApp grid sync SUCCESS!")
    except Exception as e:
        logger.error(f"Failed to upload Operator sheet: {e}")

if __name__ == "__main__":
    IST = ZoneInfo("Asia/Kolkata")
    today_str = datetime.now(IST).strftime("%Y-%m-%d")
    target = sys.argv[1] if len(sys.argv) > 1 else today_str
    sync_grid_consumption(target)