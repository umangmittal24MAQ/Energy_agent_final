"""
Data service that reads dashboard data from SharePoint and SuryaLogix sources.
"""
import logging
from typing import Optional, Dict, List, Any
import pandas as pd
from datetime import datetime

logger = logging.getLogger(__name__)

from app.core.config import get_settings
from app.agents.ingestion.loader import (
    load_solar_data,
    load_grid_data,
    load_diesel_data,
    load_solar_last7_data,
)

settings = get_settings()


def _apply_date_filter(
    df: pd.DataFrame,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> pd.DataFrame:
    if df.empty or (not start_date and not end_date):
        return df
    df = df.copy()
    date_col = "Timestamp" if "Timestamp" in df.columns else "Date"
    if date_col in df.columns:
        if df[date_col].dtype == "object":
            df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        if start_date:
            df = df[df[date_col] >= pd.to_datetime(start_date)]
        if end_date:
            end = pd.to_datetime(end_date) + pd.Timedelta(days=1)
            df = df[df[date_col] < end]
    return df


def _date_range(df: pd.DataFrame) -> Dict[str, Any]:
    date_col = "Timestamp" if "Timestamp" in df.columns else "Date"
    if date_col in df.columns and not df.empty:
        dates = pd.to_datetime(df[date_col], errors="coerce").dropna()
        if len(dates) > 0:
            return {
                "min_date": dates.min().strftime("%Y-%m-%d"),
                "max_date": dates.max().strftime("%Y-%m-%d"),
            }
    return {"min_date": None, "max_date": None}


def _to_response(df: pd.DataFrame) -> Dict[str, Any]:
    if "Date" in df.columns and df["Date"].dtype == "datetime64[ns]":
        df = df.copy()
        df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")
    data = df.to_dict("records")
    return {
        "data": data,
        "date_range": _date_range(df),
        "total_records": len(data),
    }


def load_unified_data(
    start_date: Optional[str] = None, end_date: Optional[str] = None
) -> Dict[str, Any]:
    """Load unified energy data from SharePoint + SuryaLogix sources."""
    try:
        grid_df = load_grid_data(start_date=start_date, end_date=end_date)
        solar_df = load_solar_data(start_date=start_date, end_date=end_date)
        diesel_df = load_diesel_data(start_date=start_date, end_date=end_date)

        frames = []
        if not grid_df.empty:
            frames.append(grid_df)
        if not solar_df.empty:
            frames.append(solar_df)
        if not diesel_df.empty:
            frames.append(diesel_df)

        if not frames:
            return {"data": [], "date_range": {"min_date": None, "max_date": None}, "total_records": 0}

        unified_df = pd.concat(frames, ignore_index=True, sort=False)
        unified_df = _apply_date_filter(unified_df, start_date, end_date)
        unified_df = unified_df.drop(
            columns=["Irradiance (W/m²)", "DG Runtime (hrs)", "Source", "source"],
            errors="ignore",
        )
        return _to_response(unified_df)
    except Exception as e:
        logger.error(f"Error loading unified data: {e}")
        return {"data": [], "date_range": {"min_date": None, "max_date": None}, "total_records": 0}


def load_grid_data(
    start_date: Optional[str] = None, end_date: Optional[str] = None
) -> Dict[str, Any]:
    """Load grid data from SharePoint."""
    try:
        from app.agents.ingestion.loader import load_grid_data as _load
        df = _load(start_date=start_date, end_date=end_date)
        df = _apply_date_filter(df, start_date, end_date)
        return _to_response(df)
    except Exception as e:
        logger.error(f"Error loading grid data: {e}")
        return {"data": [], "date_range": {"min_date": None, "max_date": None}, "total_records": 0}


def load_solar_data(
    start_date: Optional[str] = None, end_date: Optional[str] = None
) -> Dict[str, Any]:
    """Load solar data from SuryaLogix."""
    try:
        from app.agents.ingestion.loader import load_solar_data as _load
        df = _load(start_date=start_date, end_date=end_date)
        df = _apply_date_filter(df, start_date, end_date)
        return _to_response(df)
    except Exception as e:
        logger.error(f"Error loading solar data: {e}")
        return {"data": [], "date_range": {"min_date": None, "max_date": None}, "total_records": 0}


def load_diesel_data(
    start_date: Optional[str] = None, end_date: Optional[str] = None
) -> Dict[str, Any]:
    """Load diesel data from SharePoint."""
    try:
        from app.agents.ingestion.loader import load_diesel_data as _load
        df = _load(start_date=start_date, end_date=end_date)
        df = _apply_date_filter(df, start_date, end_date)
        return _to_response(df)
    except Exception as e:
        logger.error(f"Error loading diesel data: {e}")
        return {"data": [], "date_range": {"min_date": None, "max_date": None}, "total_records": 0}


def load_daily_summary(
    start_date: Optional[str] = None, end_date: Optional[str] = None
) -> Dict[str, Any]:
    """Load daily aggregated summary."""
    try:
        result = load_unified_data(start_date, end_date)
        df = pd.DataFrame(result["data"])
        if df.empty:
            return {"data": [], "date_range": {"min_date": None, "max_date": None}, "total_records": 0}

        df["Date"] = pd.to_datetime(df.get("Date", pd.Series()), errors="coerce").dt.strftime("%Y-%m-%d")
        df = df.dropna(subset=["Date"])

        def _col(name):
            return pd.to_numeric(df.get(name, 0), errors="coerce").fillna(0)

        grouped = df.groupby("Date", as_index=False).agg(
            {col: "sum" for col in ["Grid Units Consumed (kWh)", "Solar Units Consumed (kWh)",
                                     "Total Units Consumed (kWh)", "Total Cost (INR)",
                                     "Solar Cost Savings (INR)"] if col in df.columns}
        )
        data = grouped.to_dict("records")
        return {
            "data": data,
            "date_range": _date_range(grouped),
            "total_records": len(data),
        }
    except Exception as e:
        logger.error(f"Error loading daily summary: {e}")
        return {"data": [], "date_range": {"min_date": None, "max_date": None}, "total_records": 0}