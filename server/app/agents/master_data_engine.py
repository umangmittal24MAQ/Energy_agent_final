"""
master_data_engine.py
=====================
Aggregates daily data from Grid_Diesel and Unified_Solar into the Master-data Excel file.
Designed to run once daily (e.g., at 08:00 AM) to process the previous day's data.

Usage: 
  python master_data_engine.py              # Processes yesterday's data
  python master_data_engine.py 2026-04-08   # Processes a specific date
"""

import os
import sys
import io
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests
import pandas as pd
from dotenv import load_dotenv

# ──────────────────────────────────────────────────────────────────────────────
# Logging Setup
# ──────────────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Configuration & Environment
# ──────────────────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent

def _load_env() -> None:
    search_paths = [SCRIPT_DIR, SCRIPT_DIR.parents[0], SCRIPT_DIR.parents[1], Path.cwd()]
    for path in search_paths:
        env_file = path / ".env"
        if env_file.exists():
            load_dotenv(dotenv_path=env_file)
            return
    load_dotenv()

_load_env()

TENANT_ID = os.getenv("SHAREPOINT_TENANT_ID", "").strip()
CLIENT_ID = os.getenv("SHAREPOINT_CLIENT_ID", "").strip()
CLIENT_SECRET = os.getenv("SHAREPOINT_CLIENT_SECRET", "").strip()
GRID_RATE = float(os.getenv("GRID_RATE_INR_PER_KWH", "7.11"))

HOSTNAME = "testmaq.sharepoint.com"
SITE_PATH = "/Admin"
DRIVE_NAME = "Private"
BASE_FOLDER = "22. Facilities Report/MIPL/Noida/2. Electrical data/"

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

_access_token: Optional[str] = None
_site_id: Optional[str] = None
_drive_id: Optional[str] = None

# ──────────────────────────────────────────────────────────────────────────────
# SharePoint Graph API Helpers
# ──────────────────────────────────────────────────────────────────────────────
def _get_token() -> str:
    global _access_token
    if _access_token: return _access_token
    url = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
    resp = requests.post(url, data={
        "grant_type": "client_credentials",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "scope": "https://graph.microsoft.com/.default",
    }, timeout=30)
    resp.raise_for_status()
    _access_token = resp.json()["access_token"]
    return _access_token

def _get_site_and_drive_ids() -> tuple[str, str]:
    global _site_id, _drive_id
    if _site_id and _drive_id: return _site_id, _drive_id
    headers = {"Authorization": f"Bearer {_get_token()}"}
    _site_id = requests.get(f"{GRAPH_BASE}/sites/{HOSTNAME}:{SITE_PATH}", headers=headers).json()["id"]
    for drive in requests.get(f"{GRAPH_BASE}/sites/{_site_id}/drives", headers=headers).json().get("value", []):
        if drive["name"] == DRIVE_NAME:
            _drive_id = drive["id"]
            return _site_id, _drive_id
    raise Exception("Drive not found")

def upload_excel(filename: str, df: pd.DataFrame) -> None:
    site_id, drive_id = _get_site_and_drive_ids()
    file_path = f"{BASE_FOLDER}{filename}"
    safe_path = requests.utils.quote(file_path, safe="/")
    url = f"{GRAPH_BASE}/sites/{site_id}/drives/{drive_id}/root:/{safe_path}:/content"
    
    output_buffer = io.BytesIO()
    with pd.ExcelWriter(output_buffer, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Sheet1', index=False)
    output_buffer.seek(0)
    
    headers = {
        "Authorization": f"Bearer {_get_token()}",
        "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    }
    resp = requests.put(url, headers=headers, data=output_buffer.read())
    resp.raise_for_status()

def download_excel(filename: str) -> pd.DataFrame:
    import io
    site_id, drive_id = _get_site_and_drive_ids()
    file_path = f"{BASE_FOLDER}{filename}"
    safe_path = requests.utils.quote(file_path, safe="/")
    url = f"{GRAPH_BASE}/sites/{site_id}/drives/{drive_id}/root:/{safe_path}:/content"
    
    resp = requests.get(url, headers={"Authorization": f"Bearer {_get_token()}"})
    resp.raise_for_status()
    df = pd.read_excel(io.BytesIO(resp.content), sheet_name="Sheet1")
    
    if any("Unnamed" in str(c) for c in df.columns):
        logger.info(f"Detected 'Unnamed' columns in {filename}. Hunting for real headers...")
        for i, row in df.head(10).iterrows():
            if any("date" in str(val).lower() for val in row.values):
                df.columns = [str(c).strip().replace('\n', ' ') for c in row.values]
                df = df.iloc[i+1:].reset_index(drop=True)
                logger.info(f"✅ Found real headers and fixed the table for {filename}!")
                break
    else:
        df.columns = [str(c).strip().replace('\n', ' ') for c in df.columns]
        
    return df

# ──────────────────────────────────────────────────────────────────────────────
# Core Logic
# ──────────────────────────────────────────────────────────────────────────────
def _safe_float(val) -> float:
    try: return float(str(val).replace(',', '').strip())
    except (TypeError, ValueError, AttributeError): return 0.0

def _robust_parse_date(series: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(series, errors="coerce")
    if parsed.isna().any():
        fallback_str = series.astype(str).str.strip()
        parsed = parsed.fillna(pd.to_datetime(fallback_str, format="%d-%b-%y", errors="coerce"))
        parsed = parsed.fillna(pd.to_datetime(fallback_str, errors="coerce", dayfirst=True))
    return parsed.dt.strftime("%Y-%m-%d")

def _get_fuzzy(row: pd.Series, keyword: str, default: any = ""):
    kw_lower = keyword.lower().replace(" ", "").replace("\n", "")
    for col in row.index:
        col_lower = str(col).lower().replace(" ", "").replace("\n", "")
        if kw_lower in col_lower:
            val = row[col]
            if pd.isna(val) or str(val).strip() == "":
                return default
            if kw_lower == "time":
                try:
                    return pd.to_datetime(val).strftime("%H:%M")
                except:
                    return str(val).strip()[:5]
            return val
    return default


def _normalize_key(value: str) -> str:
    return str(value).lower().replace(" ", "").replace("_", "").replace("\n", "")


def _get_from_candidates(row: pd.Series, candidates: list[str], default: any = ""):
    """Get value from row by exact normalized candidate column names."""
    lookup: dict[str, str] = {}
    for col in row.index:
        key = _normalize_key(col)
        lookup.setdefault(key, str(col))

    for candidate in candidates:
        source_col = lookup.get(_normalize_key(candidate))
        if not source_col:
            continue
        val = row[source_col]
        if pd.isna(val) or str(val).strip() == "":
            continue
        return val
    return default


def _extract_live_solar_snapshot(solar_rows: pd.DataFrame) -> dict[str, any]:
    """Extract latest SMB/Inverter values and statuses from UnifiedSolarData rows."""
    snapshot = {
        "Day Generation (kWh)": "",
    }
    for i in range(1, 6):
        snapshot[f"SMB{i}"] = ""
        snapshot[f"SMB{i}_status"] = ""
        snapshot[f"Inverter{i}"] = ""
        snapshot[f"Inverter{i}_status"] = ""

    if solar_rows is None or solar_rows.empty:
        return snapshot

    latest_rows = solar_rows.copy()
    if "Time" in latest_rows.columns:
        latest_rows["_parsed_time"] = pd.to_datetime(latest_rows["Time"], errors="coerce")
        latest_rows = latest_rows.sort_values("_parsed_time")

    latest = latest_rows.iloc[-1]
    snapshot["Day Generation (kWh)"] = _get_from_candidates(
        latest,
        ["Day Generation (kWh)", "DayGeneration", "Day Generation"],
        "",
    )

    for i in range(1, 6):
        snapshot[f"SMB{i}"] = _get_from_candidates(
            latest,
            [f"SMB{i}", f"SMB {i}", f"SMB_{i}"],
            "",
        )
        snapshot[f"SMB{i}_status"] = _get_from_candidates(
            latest,
            [
                f"SMB{i}_status",
                f"SMB{i} status",
                f"SMB{i} Status",
                f"SMB {i}_status",
                f"SMB {i} status",
                f"SMB {i} Status",
            ],
            "",
        )
        snapshot[f"Inverter{i}"] = _get_from_candidates(
            latest,
            [f"Inverter{i}", f"Inverter {i}", f"Inverter_{i}", f"INV_{i}"],
            "",
        )
        snapshot[f"Inverter{i}_status"] = _get_from_candidates(
            latest,
            [
                f"Inverter{i}_status",
                f"Inverter{i} status",
                f"Inverter{i} Status",
                f"Inverter {i}_status",
                f"Inverter {i} status",
                f"Inverter {i} Status",
            ],
            "",
        )

    return snapshot


def _get_peak_automated_solar_units(solar_rows: pd.DataFrame) -> float:
    """Return the peak cumulative automated solar units for the day."""
    if solar_rows is None or solar_rows.empty:
        return 0.0

    candidate_rows = solar_rows
    if "Time" in solar_rows.columns:
        parsed_times = pd.to_datetime(solar_rows["Time"], errors="coerce").dt.strftime("%H:%M")
        upto_1930 = solar_rows[parsed_times.notna() & (parsed_times <= "19:30")]
        if not upto_1930.empty:
            candidate_rows = upto_1930

    solar_candidates = candidate_rows.apply(
        lambda row: _safe_float(_get_fuzzy(row, "daygeneration", _get_fuzzy(row, "solar", 0))),
        axis=1,
    )

    if solar_candidates.empty:
        return 0.0

    return float(solar_candidates.max())


def _compute_solar_units_from_unified(df_solar: pd.DataFrame, for_date: str) -> Optional[float]:
    """Return max(Today Yesterday Gen, Yesterday final Day Generation) from UnifiedSolarData."""
    if df_solar is None or df_solar.empty:
        return None

    work = df_solar.copy()
    if "Date" not in work.columns:
        return None

    work["_date"] = pd.to_datetime(work["Date"], errors="coerce").dt.date
    time_col = next((c for c in work.columns if _normalize_key(c) == "time"), None)
    if time_col:
        work["_time"] = pd.to_datetime(work[time_col], errors="coerce")
    else:
        work["_time"] = pd.NaT

    target_date = pd.to_datetime(for_date, errors="coerce")
    if pd.isna(target_date):
        return None
    today = target_date.date()
    yesterday = (target_date - timedelta(days=1)).date()

    def _norm(value: str) -> str:
        return "".join(ch for ch in str(value).lower() if ch.isalnum())

    ygen_col = next(
        (
            c
            for c in work.columns
            if _norm(c) in {
                "yesterdaygen",
                "yesterdaygenerationkwh",
                "yesterdaygeneration",
            }
        ),
        None,
    )
    daygen_col = next(
        (
            c
            for c in work.columns
            if _norm(c) in {"daygenerationkwh", "daygeneration"}
        ),
        None,
    )

    today_ygen = 0.0
    if ygen_col:
        today_rows = work[work["_date"] == today].copy()
        if not today_rows.empty:
            today_rows = today_rows.sort_values("_time")
            today_ygen = _safe_float(today_rows.iloc[-1].get(ygen_col, 0))

    yday_last_daygen = 0.0
    if daygen_col:
        yday_rows = work[work["_date"] == yesterday].copy()
        if not yday_rows.empty:
            yday_rows = yday_rows.sort_values("_time")
            yday_last_daygen = _safe_float(yday_rows.iloc[-1].get(daygen_col, 0))

    selected = max(today_ygen, yday_last_daygen)
    logger.info(
        "Solar Units compare from UnifiedSolarData for %s: today[YGen]=%s, yesterday[last DayGen]=%s, selected=%s",
        for_date,
        today_ygen,
        yday_last_daygen,
        selected,
    )
    return float(selected)

# REPLACE WITH THIS
def process_master_data(
    operator_date: str,
    solar_date: str,
    fallback_operator_date: Optional[str] = None,
) -> None:
    logger.info(f"=== Master Data Engine: operator_date={operator_date}, solar_date={solar_date} ===")
        
    # 1. Download BOTH source files
    logger.info("Downloading Electrical Optimization (1).xlsx (Operator Data)...")
    df_grid = download_excel("Electrical Optimization (1).xlsx")
    
    logger.info("Downloading UnifiedSolarData.xlsx (Automated Solar Data)...")
    df_solar = download_excel("UnifiedSolarData.xlsx")
    
    # 2. Parse dates safely
    df_grid['Date'] = _robust_parse_date(df_grid['Date'])
    df_solar['Date'] = _robust_parse_date(df_solar['Date'])
    
    # 3. Filter for the target date
    grid_rows  = df_grid[df_grid['Date'] == operator_date]   # operator wrote today's date
    solar_rows = df_solar[df_solar['Date'] == solar_date]    # scraper ran on yesterday

    grid_source_date = operator_date
    if grid_rows.empty and fallback_operator_date:
        fallback_rows = df_grid[df_grid['Date'] == fallback_operator_date]
        if not fallback_rows.empty:
            grid_rows = fallback_rows
            grid_source_date = fallback_operator_date
            logger.warning(
                "No operator row found for %s. Using fallback operator row from %s.",
                operator_date,
                fallback_operator_date,
            )

    if grid_rows.empty:
        logger.error(f"❌ No Operator Grid/Diesel data found for {operator_date}. Aborting.")
        return
        
    grid_today = grid_rows.iloc[-1]
    
    # 4. Extract Grid Data purely from Operator Sheet
    grid_units = _safe_float(_get_fuzzy(grid_today, "gridunits", 0))
    grid_cost_inr = _safe_float(_get_fuzzy(grid_today, "consumedin", 0))
    
    # 5. Extract Solar Data from UnifiedSolarData using requested max logic.
    automated_solar_units = 0.0
    live_solar_snapshot = _extract_live_solar_snapshot(solar_rows)
    calculated_solar_units = _compute_solar_units_from_unified(df_solar, operator_date)
    if calculated_solar_units is not None:
        automated_solar_units = calculated_solar_units

    if automated_solar_units > 0:
        solar_units = automated_solar_units
        solar_source = "UnifiedSolarData Max Rule"
    else:
        logger.warning(
            "UnifiedSolarData max-rule value is missing/zero for %s. Falling back to day peak for %s.",
            operator_date,
            solar_date,
        )
        fallback_peak = _get_peak_automated_solar_units(solar_rows) if not solar_rows.empty else 0.0
        solar_units = fallback_peak
        solar_source = "Automated Day-Peak Fallback"
        
    # 6. Calculate True Totals
    total_units = grid_units + solar_units
    energy_savings_inr = solar_units * GRID_RATE
    
    logger.info(f"   Grid Units: {grid_units} kWh")
    if grid_source_date != operator_date:
        logger.info(f"   Grid source row date (fallback): {grid_source_date}")
    logger.info(f"   Solar Units: {solar_units} kWh (Source: {solar_source})")
    logger.info(f"   Calculated Total Units: {total_units} kWh")
    logger.info(f"   Grid Cost (INR): ₹{grid_cost_inr:,.2f}")
    logger.info(f"   Calculated Savings (INR): ₹{energy_savings_inr:,.2f}")
    
    consumption_dt = pd.to_datetime(solar_date) 
    
    # 7. Build the Master Row
    master_row = {
        "Date": operator_date,                      
        "Day":  consumption_dt.strftime("%A"),
        "Time": _get_fuzzy(grid_today, "time", "09:00"),
        "Ambient Temperature °C": _get_fuzzy(grid_today, "ambient", ""),
        "Grid Units Consumed (KWh)": grid_units,
        "Solar Units Consumed(KWh)": solar_units,
        "Total Units Consumed (KWh)": total_units,
        "Total Units Consumed in INR": grid_cost_inr,
        "Energy Saving in INR": round(energy_savings_inr, 2),
        "Number of Panels Cleaned": _get_fuzzy(grid_today, "panelscleaned", 0),
        "Diesel consumed": _get_fuzzy(grid_today, "diesel", "0"),
        "Water treated through STP": _get_fuzzy(grid_today, "stp", "0"),
        "Water treated through WTP": _get_fuzzy(grid_today, "wtp", "0"),
        "Issues": _get_fuzzy(grid_today, "issues", "No issues"),
        "Inverter_1": _get_fuzzy(grid_today, "inv_1", ""),
        "Inverter_2": _get_fuzzy(grid_today, "inv_2", ""),
        "Inverter_3": _get_fuzzy(grid_today, "inv_3", ""),
        "Inverter_4": _get_fuzzy(grid_today, "inv_4", ""),
        "Inverter_5": _get_fuzzy(grid_today, "inv_5", ""),
    }
    master_row.update(live_solar_snapshot)
    
    # 8. Push to Master Data
    logger.info("Downloading Master-data.xlsx...")
    df_master = download_excel("Master-data.xlsx")
    
    df_master['Date_Str'] = _robust_parse_date(df_master['Date'])
    
    # FIX: The deduplication check must match the date we are inserting!
    df_master = df_master[df_master['Date_Str'] != operator_date].drop(columns=['Date_Str'])
    
    new_df = pd.DataFrame([master_row])
    df_master = pd.concat([df_master, new_df], ignore_index=True)
    
    logger.info("Uploading updated Master-data.xlsx to SharePoint...")
    upload_excel("Master-data.xlsx", df_master)
    logger.info("✅ Master data synchronization SUCCESS!")
    
# REPLACE WITH THIS
if __name__ == "__main__":
    import sys
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
    now = datetime.now(IST)
    # argv[1] = operator_date (TODAY)      e.g. 2026-04-13
    # argv[2] = solar_date    (YESTERDAY)  e.g. 2026-04-12
    # argv[3] = fallback_operator_date      e.g. 2026-04-13
    operator_date = sys.argv[1] if len(sys.argv) > 1 else now.strftime("%Y-%m-%d")
    solar_date    = sys.argv[2] if len(sys.argv) > 2 else (now - timedelta(days=1)).strftime("%Y-%m-%d")
    fallback_operator_date = sys.argv[3] if len(sys.argv) > 3 else None
    try: process_master_data(operator_date, solar_date, fallback_operator_date)
    except Exception as e:
        logger.error(f"❌ Master Engine Failed: {e}")
        sys.exit(1)