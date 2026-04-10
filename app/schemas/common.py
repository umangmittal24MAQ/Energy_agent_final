"""
Common Pydantic schemas used across the API
"""
from pydantic import BaseModel
from typing import Optional
from datetime import date


class DateRangeFilter(BaseModel):
    """Date range filter for querying data"""
    start_date: Optional[str] = None
    end_date: Optional[str] = None


class SuccessResponse(BaseModel):
    """Generic success response"""
    message: str
    data: Optional[dict] = None

