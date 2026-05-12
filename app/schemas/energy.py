"""
Pydantic schemas for energy data
"""
from pydantic import BaseModel
from typing import List, Optional


class UnifiedEnergyData(BaseModel):
    """Unified energy data response"""
    date: str
    day: str
    time: str
    ambient_temp: str
    grid_kwh: float
    solar_kwh: float
    diesel_kwh: float
    total_kwh: float
    grid_cost_inr: float
    diesel_cost_inr: float
    solar_cost_inr: float
    total_cost_inr: float
    energy_saving_inr: float
    solar_pct: float
    source: str


class GridEnergyData(BaseModel):
    """Grid energy data response"""
    date: str
    day: str
    time: str
    ambient_temp: str
    grid_kwh: float
    total_kwh: float
    total_cost_inr: float
    energy_saving_inr: float


class SolarEnergyData(BaseModel):
    """Solar energy data response"""
    date: str
    day: str
    time: str
    solar_kwh: float
    inverter_status: str
    smb1: float
    smb2: float
    smb3: float
    smb4: float
    smb5: float
    plant_capacity: float
    irradiance: float


class DieselEnergyData(BaseModel):
    """Diesel energy data response"""
    date: str
    day: str
    time: str
    dg_kwh: float
    dg_runtime: float
    fuel_consumed: float
    cost_per_unit: float
    total_cost: float
    dg_id: str


class EnergyDataResponse(BaseModel):
    """Response containing energy data"""
    data: List[dict]
    date_range: dict
    total_records: int

