"""
KPI endpoints router - calculates dashboard metrics from Master Data.
"""
from fastapi import APIRouter, Query
from typing import Optional, Dict, Any
import pandas as pd
import logging
from app.services import data_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/kpis", tags=["kpis"])

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

    # Fallback for text values containing numeric tokens (e.g., '3 Liter').
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
    """Calculates top-level dashboard KPIs from the unified master dataset."""
    result = data_service.load_unified_data(start_date, end_date)
    df = pd.DataFrame(result["data"])
    
    if df.empty:
        return {"total_grid_kwh": 0, "total_solar_kwh": 0, "solar_savings_inr": 0}

    # Map the unified Excel columns to frontend KPI keys
    return {
        "total_grid_kwh": _sum_col(df, "Grid Units Consumed (KWh)"),
        "total_solar_kwh": _sum_col(df, "Solar Units Consumed(KWh)"),
        "total_energy_kwh": _sum_col(df, "Total Units Consumed (KWh)"),
        "total_cost_inr": _sum_col(df, "Total Units Consumed in INR"),
        "solar_savings_inr": _sum_col(df, "Energy Saving in INR"),
        "diesel_consumed_liters": _sum_col_with_text_numeric(df, "Diesel consumed")
    }