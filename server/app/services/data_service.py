"""
Data service that reads dashboard data directly from the Master Data Excel file.
"""
import logging
from typing import Optional, Dict, Any
import pandas as pd

from app.services.sharepoint_data_service import get_service as get_excel_service

logger = logging.getLogger(__name__)


def _normalize_key(value: str) -> str:
    return str(value).lower().replace(" ", "").replace("_", "").replace("\n", "")


def _resolve_column(columns: list[str], candidates: list[str]) -> Optional[str]:
    lookup: Dict[str, str] = {}
    for col in columns:
        normalized = _normalize_key(col)
        lookup.setdefault(normalized, col)

    for candidate in candidates:
        match = lookup.get(_normalize_key(candidate))
        if match:
            return match
    return None


def _extract_sort_minutes(series: pd.Series) -> pd.Series:
    """Extract HH:MM from mixed time strings and convert to sortable minute index."""
    text = series.astype(str).str.strip()
    parts = text.str.extract(r"(?P<hour>\d{1,2}):(?P<minute>\d{2})")
    hours = pd.to_numeric(parts["hour"], errors="coerce")
    minutes = pd.to_numeric(parts["minute"], errors="coerce")
    return (hours * 60) + minutes


def _build_live_solar_snapshot(df_solar: pd.DataFrame) -> pd.DataFrame:
    """Return one latest solar snapshot row per date with canonical SMB/Inverter fields."""
    if df_solar is None or df_solar.empty or "Date" not in df_solar.columns:
        return pd.DataFrame()

    work = df_solar.copy()
    work["Date"] = pd.to_datetime(work["Date"], errors="coerce").dt.strftime("%Y-%m-%d")
    work = work[work["Date"].notna()].copy()
    if work.empty:
        return pd.DataFrame()

    cols = [str(col) for col in work.columns]

    status_map: Dict[str, list[str]] = {}
    value_map: Dict[str, list[str]] = {}
    for i in range(1, 6):
        status_map[f"SMB{i}_status"] = [
            f"SMB{i}_status",
            f"SMB{i} status",
            f"SMB{i} Status",
            f"SMB {i}_status",
            f"SMB {i} status",
            f"SMB {i} Status",
        ]
        status_map[f"Inverter{i}_status"] = [
            f"Inverter{i}_status",
            f"Inverter{i} status",
            f"Inverter{i} Status",
            f"Inverter {i}_status",
            f"Inverter {i} status",
            f"Inverter {i} Status",
        ]

        value_map[f"SMB{i}"] = [f"SMB{i}", f"SMB {i}", f"SMB_{i}"]
        value_map[f"Inverter{i}"] = [
            f"Inverter{i}",
            f"Inverter {i}",
            f"Inverter_{i}",
            f"INV_{i}",
        ]

    canonical_fields: Dict[str, Optional[str]] = {
        "Day Generation (kWh)": _resolve_column(
            cols,
            ["Day Generation (kWh)", "DayGeneration", "Day Generation"],
        ),
    }

    for target_col, candidates in {**value_map, **status_map}.items():
        canonical_fields[target_col] = _resolve_column(cols, candidates)

    out = pd.DataFrame({"Date": work["Date"]})
    for target_col, source_col in canonical_fields.items():
        out[target_col] = work[source_col] if source_col else None

    time_col = _resolve_column(cols, ["Time"])
    out["_sort_time"] = (
        _extract_sort_minutes(work[time_col]) if time_col else pd.Series([pd.NA] * len(out))
    )
    out["_row_index"] = range(len(out))

    out = out.sort_values(["Date", "_sort_time", "_row_index"])
    out = out.groupby("Date", as_index=False).tail(1)
    out = out.drop(columns=["_sort_time", "_row_index"])
    return out


def _enrich_unified_with_live_solar(df_master: pd.DataFrame) -> pd.DataFrame:
    """Merge latest per-day live SMB/Inverter fields from UnifiedSolarData into master rows."""
    if df_master is None or df_master.empty or "Date" not in df_master.columns:
        return df_master

    sp_service = get_excel_service()
    df_solar = sp_service.fetch_sheet_data("unified_solar")
    if df_solar is None or df_solar.empty:
        return df_master

    snapshot_df = _build_live_solar_snapshot(df_solar)
    if snapshot_df.empty:
        return df_master

    left = df_master.copy()
    right = snapshot_df.copy()

    left["_date_key"] = pd.to_datetime(left["Date"], errors="coerce")
    right["_date_key"] = pd.to_datetime(right["Date"], errors="coerce")

    left = left.sort_values("_date_key")
    right = right.sort_values("_date_key")

    merged = pd.merge_asof(
        left,
        right.drop(columns=["Date"]),
        on="_date_key",
        direction="backward",
    )
    merged = merged.drop(columns=["_date_key"])
    return merged

def _get_master_data(start_date: Optional[str] = None, end_date: Optional[str] = None) -> pd.DataFrame:
    """Core function to fetch and filter unified data from Electrical Optimization."""
    sp_service = get_excel_service()
    df = sp_service.fetch_sheet_data("master_data")
    
    if df is None or df.empty:
        return pd.DataFrame()
    
    if "Date" in df.columns:
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        if start_date:
            df = df[df["Date"] >= pd.to_datetime(start_date)]
        if end_date:
            df = df[df["Date"] <= pd.to_datetime(end_date)]

        df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")

    df = _enrich_unified_with_live_solar(df)
        
    return df

def load_unified_data(start_date: Optional[str] = None, end_date: Optional[str] = None) -> Dict[str, Any]:
    """Returns the unified dataset for the frontend dashboards."""
    try:
        df = _get_master_data(start_date, end_date)
        if df.empty:
            return {"data": [], "date_range": {"min_date": None, "max_date": None}, "total_records": 0}
        
        dates = pd.to_datetime(df['Date'], errors='coerce').dropna()
        return {
            "data": df.to_dict("records"),
            "date_range": {
                "min_date": dates.min().strftime('%Y-%m-%d') if not dates.empty else None,
                "max_date": dates.max().strftime('%Y-%m-%d') if not dates.empty else None,
            },
            "total_records": len(df)
        }
    except Exception as e:
        logger.error(f"Error loading unified data: {e}")
        return {"data": [], "date_range": {"min_date": None, "max_date": None}, "total_records": 0}

# Since Master Data contains Grid, Solar, and Diesel in one row, we route all queries to the unified loader
def load_solar_data(start_date: Optional[str] = None, end_date: Optional[str] = None) -> Dict[str, Any]:
    return load_unified_data(start_date, end_date)

def load_grid_data(start_date: Optional[str] = None, end_date: Optional[str] = None) -> Dict[str, Any]:
    return load_unified_data(start_date, end_date)
    
def load_daily_summary(start_date: Optional[str] = None, end_date: Optional[str] = None) -> Dict[str, Any]:
    return load_unified_data(start_date, end_date)