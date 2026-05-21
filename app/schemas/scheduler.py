"""
Pydantic schemas for scheduler configuration and operations
"""
from pydantic import BaseModel, EmailStr
from typing import Optional, Dict, List
from datetime import datetime


class SchedulerConfig(BaseModel):
    """Scheduler configuration"""
    to: str  # Comma-separated emails
    cc: Optional[str] = ""
    send_time: str  # HH:MM format
    subject: str
    custom_message: Optional[str] = ""
    auto_start: bool = False
    include_sections: Dict[str, bool] = {
        "summary_kpis": True,
        "unified_table": True,
        "grid_summary": True,
        "solar_summary": True,
        "diesel_summary": True,
        "inverter_status": True,
        "raw_data": False
    }
    uploaded_template_path: Optional[str] = None


class SchedulerStatus(BaseModel):
    """Scheduler status response"""
    status: str  # "running" or "stopped"
    next_run: Optional[str] = None
    last_run: Optional[Dict[str, str]] = None


class SchedulerHistoryEntry(BaseModel):
    """Single scheduler history entry"""
    timestamp: str
    status: str
    recipients: str
    attachment: Optional[str] = None
    notes: str


class SendNowRequest(BaseModel):
    """Request to send email immediately"""
    pass  # No parameters needed, uses stored config


class ExportRequest(BaseModel):
    """Request for exporting data"""
    start_date: Optional[str] = None
    end_date: Optional[str] = None

