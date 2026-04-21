"""
Data service that reads dashboard data directly from the Master Data Excel file.
"""
import logging
from typing import Optional, Dict, Any
import pandas as pd

from app.services.sharepoint_data_service import get_service as get_excel_service

logger = logging.getLogger(__name__)

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