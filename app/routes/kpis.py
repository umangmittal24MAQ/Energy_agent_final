"""
routes/kpis.py  (FIXED)
========================
Added Master_Data_List column names to all _sum_col / _max_col lookup lists
so KPIs resolve correctly from the new SharePoint Lists.

Column name mapping:
  Old Google Sheets name              →  New SharePoint List column name
  ─────────────────────────────────────────────────────────────────────
  "Grid Units Consumed (kWh)"         →  "Grid_Units_Consumed_kWh"
  "Solar Units Consumed (kWh)"        →  "Solar_Units_Consumed_kWh"
  "Total Units Consumed (kWh)"        →  "Total_Units_Consumed_kWh"
  "Total Cost (INR)"                  →  "Total_Cost_INR"
  "Solar Cost Savings (INR)"  /
  "Energy Saving in INR"              →  "Solar_Cost_Savings_INR"
  "Day Generation (kWh)"              →  "Day_Generation_kWh"
  "Diesel Consumed (Litres)"          →  "Diesel_Consumed_Litres"
"""
from fastapi import APIRouter, Query
from typing import Optional
import pandas as pd
import numpy as np
import logging
from app.core.config import get_settings
from app.services import data_service

_settings = get_settings()
GRID_COST_PER_UNIT = _settings.grid_cost_per_unit
DIESEL_COST_PER_UNIT = _settings.diesel_cost_per_unit
SOLAR_TARGET_PERCENTAGE = _settings.solar_target_percentage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/kpis", tags=["kpis"])


def convert_numpy_types(obj):
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, dict):
        return {k: convert_numpy_types(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [convert_numpy_types(i) for i in obj]
    return obj


def _sum_col(df: pd.DataFrame, keys: list) -> float:
    for key in keys:
        if key in df.columns:
            return float(pd.to_numeric(df[key], errors="coerce").fillna(0).sum())
    return 0.0


def _mean_col(df: pd.DataFrame, keys: list) -> float:
    for key in keys:
        if key in df.columns:
            return float(pd.to_numeric(df[key], errors="coerce").fillna(0).mean())
    return 0.0


def _max_col(df: pd.DataFrame, keys: list) -> float:
    for key in keys:
        if key in df.columns:
            return float(pd.to_numeric(df[key], errors="coerce").fillna(0).max())
    return 0.0


def _first_col(df: pd.DataFrame, keys: list) -> Optional[str]:
    for key in keys:
        if key in df.columns:
            return key
    return None


def _load_df(start_date, end_date) -> pd.DataFrame:
    result = data_service.load_unified_data(start_date, end_date)
    data = result.get("data", [])
    return pd.DataFrame(data) if data else pd.DataFrame()


# ── Column name lists: SharePoint new names FIRST, then legacy fallbacks ──────
GRID_KWH_COLS = [
    "Grid_Units_Consumed_kWh",          # Master_Data_List / Grid_Diesel_List
    "Grid Units Consumed (kWh)",         # legacy
    "Grid KWh",
    "Units_Consumed_kWh",
    "Day Import (kWh)",
]
SOLAR_KWH_COLS = [
    "Solar_Units_Consumed_kWh",          # Master_Data_List
    "Day_Generation_kWh",                # Unified_Solar_List
    "Solar Units Consumed (kWh)",        # legacy
    "Day Generation (kWh)",
    "Solar KWh",
    "Energy (kWh)",
]
TOTAL_KWH_COLS = [
    "Total_Units_Consumed_kWh",          # Master_Data_List
    "Total Units Consumed (kWh)",
    "Total KWh",
]
TOTAL_COST_COLS = [
    "Total_Cost_INR",                    # Master_Data_List
    "Total Cost (INR)",
    "Total Units Consumed in INR",
]
SOLAR_SAVINGS_COLS = [
    "Solar_Cost_Savings_INR",            # Master_Data_List
    "Solar Cost Savings (INR)",
    "Energy Saving in INR",
    "Energy Saving (INR)",
]
DIESEL_FUEL_COLS = [
    "Diesel_Consumed_Litres",            # Master_Data_List / Grid_Diesel_List
    "Diesel Consumed (Litres)",
    "Units_Consumed_kWh",
]
DIESEL_KWH_COLS = [
    "DG Units Consumed (KWh)",
    "Diesel KWh",
]
DIESEL_COST_COLS = [
    "Cost_INR",
    "Diesel Cost (INR)",
]


@router.get("/overview")
async def get_overview_kpis(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
):
    df = _load_df(start_date, end_date)
    if df.empty:
        return {
            "total_energy_kwh": 0.0, "total_grid_kwh": 0.0, "total_solar_kwh": 0.0,
            "total_diesel_kwh": 0.0, "total_cost_inr": 0.0, "solar_contribution_pct": 0.0,
            "insights": [], "recommendations": [],
            "solar_target_pct": float(SOLAR_TARGET_PERCENTAGE),
        }

    total_grid   = _sum_col(df, GRID_KWH_COLS)
    total_solar  = _sum_col(df, SOLAR_KWH_COLS)
    total_diesel = _sum_col(df, DIESEL_KWH_COLS)
    total_energy = _sum_col(df, TOTAL_KWH_COLS)
    if total_energy <= 0:
        total_energy = total_grid + total_solar + total_diesel
    total_cost = _sum_col(df, TOTAL_COST_COLS)
    if total_cost <= 0:
        total_cost = (total_grid * float(GRID_COST_PER_UNIT)) + (total_diesel * float(DIESEL_COST_PER_UNIT))
    solar_pct = (total_solar / total_energy * 100.0) if total_energy > 0 else 0.0
    solar_savings = _sum_col(df, SOLAR_SAVINGS_COLS)
    if solar_savings <= 0:
        solar_savings = total_solar * float(GRID_COST_PER_UNIT)

    return convert_numpy_types({
        "total_energy_kwh": total_energy,
        "total_grid_kwh": total_grid,
        "total_solar_kwh": total_solar,
        "total_diesel_kwh": total_diesel,
        "total_cost_inr": total_cost,
        "solar_contribution_pct": solar_pct,
        "solar_savings_inr": solar_savings,
        "solar_target_pct": float(SOLAR_TARGET_PERCENTAGE),
        "insights": [
            f"Solar contributed {solar_pct:.1f}% of total energy",
            f"Solar savings: ₹{solar_savings:,.2f}",
        ],
        "recommendations": ["Monitor grid vs solar split daily"],
    })


@router.get("/grid")
async def get_grid_kpis(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
):
    result = data_service.load_grid_data(start_date, end_date)
    df = pd.DataFrame(result.get("data", []))
    if df.empty:
        return {"total_grid_kwh": 0, "avg_grid_kwh": 0, "peak_grid_kwh": 0, "total_grid_cost": 0}

    total_grid = _sum_col(df, GRID_KWH_COLS)
    avg_grid   = _mean_col(df, GRID_KWH_COLS)
    peak_grid  = _max_col(df, GRID_KWH_COLS)
    total_cost = _sum_col(df, TOTAL_COST_COLS)
    if total_cost <= 0:
        total_cost = total_grid * float(GRID_COST_PER_UNIT)

    return convert_numpy_types({
        "total_grid_kwh": total_grid,
        "avg_grid_kwh": avg_grid,
        "peak_grid_kwh": peak_grid,
        "total_grid_cost": total_cost,
    })


@router.get("/solar")
async def get_solar_kpis(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
):
    result = data_service.load_solar_data(start_date, end_date)
    df = pd.DataFrame(result.get("data", []))
    if df.empty:
        return {
            "total_solar_kwh": 0, "avg_solar_kwh": 0, "peak_solar_kwh": 0,
            "solar_target_pct": float(SOLAR_TARGET_PERCENTAGE), "actual_solar_pct": 0,
            "energy_saved": 0, "inverter_faults": 0, "weekly_trend": [],
        }

    total_solar  = _sum_col(df, SOLAR_KWH_COLS)
    energy_saved = _sum_col(df, SOLAR_SAVINGS_COLS)
    if energy_saved <= 0:
        energy_saved = total_solar * float(GRID_COST_PER_UNIT)

    weekly_trend = []
    value_col = _first_col(df, SOLAR_KWH_COLS)
    if "Date" in df.columns and value_col:
        trend_df = df[["Date", value_col]].copy()
        trend_df["Date"] = pd.to_datetime(trend_df["Date"], errors="coerce")
        trend_df = trend_df.sort_values("Date")
        trend_df["Day"] = trend_df["Date"].dt.day_name()
        trend_df["Date"] = trend_df["Date"].dt.strftime("%Y-%m-%d")
        trend_df["Generation"] = pd.to_numeric(trend_df[value_col], errors="coerce").fillna(0)
        weekly_trend = trend_df[["Date", "Day", "Generation"]].to_dict("records")

    return convert_numpy_types({
        "total_solar_kwh": total_solar,
        "avg_solar_kwh": _mean_col(df, SOLAR_KWH_COLS),
        "peak_solar_kwh": _max_col(df, SOLAR_KWH_COLS),
        "solar_target_pct": float(SOLAR_TARGET_PERCENTAGE),
        "actual_solar_pct": 0.0,
        "energy_saved": energy_saved,
        "inverter_faults": 0,
        "weekly_trend": weekly_trend,
    })


@router.get("/diesel")
async def get_diesel_kpis(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
):
    result = data_service.load_diesel_data(start_date, end_date)
    df = pd.DataFrame(result.get("data", []))
    if df.empty:
        return {"total_diesel_kwh": 0, "total_runtime": 0, "total_fuel": 0, "total_diesel_cost": 0}

    total_fuel = _sum_col(df, DIESEL_FUEL_COLS)
    total_diesel_cost = _sum_col(df, DIESEL_COST_COLS)
    if total_diesel_cost <= 0:
        total_diesel_cost = total_fuel * float(DIESEL_COST_PER_UNIT)

    return convert_numpy_types({
        "total_diesel_kwh": _sum_col(df, DIESEL_KWH_COLS),
        "total_runtime": _sum_col(df, ["DG Runtime (hrs)"]),
        "total_fuel": total_fuel,
        "total_diesel_cost": total_diesel_cost,
    })


@router.get("/master")
async def get_master_kpis(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
):
    """
    NEW endpoint — returns KPIs directly from Master_Data_List.
    Use this for the main dashboard cards; it's faster and more accurate
    than aggregating raw source data on the fly.
    """
    try:
        from app.services.sharepoint_list_data_service import SharePointListDataService
        sp = SharePointListDataService()
        df = sp.get_master_data(start_date, end_date)
        if df is None or df.empty:
            return {"error": "No master data available", "data": []}

        total_grid   = _sum_col(df, ["Grid_Units_Consumed_kWh"])
        total_solar  = _sum_col(df, ["Solar_Units_Consumed_kWh"])
        total_energy = _sum_col(df, ["Total_Units_Consumed_kWh"])
        total_cost   = _sum_col(df, ["Total_Cost_INR"])
        savings      = _sum_col(df, ["Solar_Cost_Savings_INR"])
        solar_pct    = (total_solar / total_energy * 100) if total_energy > 0 else 0.0

        return convert_numpy_types({
            "total_grid_kwh":        total_grid,
            "total_solar_kwh":       total_solar,
            "total_energy_kwh":      total_energy,
            "total_cost_inr":        total_cost,
            "solar_savings_inr":     savings,
            "solar_contribution_pct": solar_pct,
            "total_panels_cleaned":  _sum_col(df, ["Panels_Cleaned"]),
            "total_diesel_litres":   _sum_col(df, ["Diesel_Consumed_Litres"]),
            "records":               len(df),
        })
    except Exception as exc:
        logger.error(f"Error fetching master KPIs: {exc}")
        return {"error": str(exc)}