"""
Pydantic schemas for KPI data
"""
from pydantic import BaseModel
from typing import Optional, List


class OverviewKPIs(BaseModel):
    """Overview KPIs response"""
    total_kwh: float
    solar_kwh: float
    solar_pct: float
    total_cost: float
    energy_saved: float
    avg_temp: float
    delta_kwh: Optional[float] = None
    delta_solar_pct: Optional[float] = None
    delta_cost: Optional[float] = None
    delta_solar_gen: Optional[float] = None


class GridKPIs(BaseModel):
    """Grid KPIs response"""
    total_grid_kwh: float
    avg_grid_kwh: float
    peak_grid_kwh: float
    total_grid_cost: float


class SolarKPIs(BaseModel):
    """Solar KPIs response"""
    total_solar_kwh: float
    avg_solar_kwh: float
    peak_solar_kwh: float
    solar_target_pct: float
    actual_solar_pct: float
    energy_saved: float
    inverter_faults: int


class DieselKPIs(BaseModel):
    """Diesel KPIs response"""
    total_diesel_kwh: float
    total_runtime: float
    total_fuel: float
    total_diesel_cost: float

