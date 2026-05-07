"""
KPI endpoints router - calculates dashboard metrics from Master Data.
"""
# FIX S2: Added Depends import and get_current_user dependency.
# Previously the /kpis/dashboard endpoint was completely unauthenticated —
# energy consumption figures, cost totals and diesel usage were publicly readable.
from fastapi import APIRouter, Query, Depends
from typing import Optional, Dict, Any
import pandas as pd
import logging
from datetime import datetime, timedelta
from app.services import data_service
from app.routes.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/kpis",
    tags=["kpis"],
    dependencies=[Depends(get_current_user)],  # FIX S2: all KPI routes now require auth
)


def _sum_col(df: pd.DataFrame, column_name: str) -> float:
    if column_name in df.columns:
        return float(pd.to_numeric(df[column_name], errors="coerce").fillna(0).sum())
    return 0.0


def _sum_col_with_text_numeric(df: pd.DataFrame, column_name: str) -> float:
    """Sums values from columns that may contain units like '3 Liter'."""
    if column_name not in df.columns:
        return 0.0

    series = df[column_name]
    numeric = pd.to_numeric(series, errors="coerce")

    if numeric.isna().any():
        extracted = series.astype(str).str.extract(r"([-+]?\d*\.?\d+)", expand=False)
        extracted_numeric = pd.to_numeric(extracted, errors="coerce")
        numeric = numeric.fillna(extracted_numeric)

    return float(numeric.fillna(0).sum())


@router.get("/dashboard")
async def get_dashboard_kpis(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
):
    """
    Calculates top-level dashboard KPIs from the unified master dataset.
    If data for the requested date is empty, automatically falls back to the previous day.
    """
    # Try to load data for the requested date
    result = data_service.load_unified_data(start_date, end_date)
    df = pd.DataFrame(result["data"])
    
    effective_date = start_date
    
    # If no data found and a specific date was requested, try the previous day
    if df.empty and start_date:
        try:
            req_date = datetime.strptime(start_date, "%Y-%m-%d")
            prev_date = req_date - timedelta(days=1)
            prev_date_str = prev_date.strftime("%Y-%m-%d")
            
            result = data_service.load_unified_data(prev_date_str, prev_date_str)
            df = pd.DataFrame(result["data"])
            effective_date = prev_date_str
            
            logger.info(f"No data found for {start_date}, using {prev_date_str} instead")
        except Exception as e:
            logger.warning(f"Failed to fallback to previous day: {e}")
    
    if df.empty:
        return {
            "total_grid_kwh": 0,
            "total_solar_kwh": 0,
            "solar_savings_inr": 0,
            "data_date": effective_date,
            "is_fallback": effective_date != start_date if start_date else False
        }

    return {
        "total_grid_kwh": _sum_col(df, "Grid Units Consumed (KWh)"),
        "total_solar_kwh": _sum_col(df, "Solar Units Consumed(KWh)"),
        "total_energy_kwh": _sum_col(df, "Total Units Consumed (KWh)"),
        "total_cost_inr": _sum_col(df, "Total Units Consumed in INR"),
        "solar_savings_inr": _sum_col(df, "Energy Saving in INR"),
        "diesel_consumed_liters": _sum_col_with_text_numeric(df, "Diesel consumed"),
        "data_date": effective_date,
        "is_fallback": effective_date != start_date if start_date else False
    }