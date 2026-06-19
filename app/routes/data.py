"""
Data endpoints router - reads from the Unified Master Excel file.
"""
from fastapi import APIRouter, Query, HTTPException, Depends
from typing import Optional
import logging
from app.services import data_service
from app.schemas.energy import EnergyDataResponse
from app.routes.auth import get_current_user

logger = logging.getLogger(__name__)

# FIX R8: The router-level dependency is sufficient to protect all routes.
# The previous code also injected get_current_user as a parameter on individual
# endpoints, meaning the auth check ran TWICE per request — once at the router
# level and once in the function signature. The router-level dependency is the
# correct single point of enforcement.
router = APIRouter(
    prefix="/data",
    tags=["data"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/live/unified", response_model=EnergyDataResponse)
async def get_live_unified_data(
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
):
    """Fetches unified energy data (grid + solar + diesel) from the Master Excel file."""
    return data_service.load_unified_data(start_date, end_date)


@router.get("/live/inverter-uptime")
async def get_live_inverter_uptime(
    date: Optional[str] = Query(None, description="Date (YYYY-MM-DD). Defaults to today. Past dates read from tracker."),
):
    """
    Inverter uptime/downtime for a given date.
    - Today (default): reads live from UnifiedSolarData sheet.
    - Past dates: reads from inverter_tracker.json (up to 30 days back).
    """
    from app.services.inverter_monitor import get_uptime_from_tracker_for_date
    result = get_uptime_from_tracker_for_date(date_str=date)
    if "error" in result:
        raise HTTPException(status_code=503, detail=result["error"])
    return result


@router.get("/inverter-uptime/trend")
async def get_inverter_uptime_trend(
    days: int = Query(30, ge=1, le=30, description="Number of days of history (1-30)."),
):
    """
    Daily uptime % per inverter for the last N days (max 30).
    Reads entirely from inverter_tracker.json — no SharePoint call.
    Used by the frontend trend chart.
    """
    from app.services.inverter_monitor import get_inverter_trend
    return get_inverter_trend(days=days)


@router.get("/debug/status")
async def get_integration_status():
    """Simplified health check for the Excel-based architecture."""
    from app.services.sharepoint_data_service import get_service

    sp_service = get_service()
    last_err = sp_service.get_last_error()
    
    # FIX: Log the raw error server-side to prevent leaking internal 
    # Graph API details or tenant IDs to the client.
    if last_err:
        logger.error(f"SharePoint Integration Error: {last_err}")

    return {
        "sharepoint": {
            "authenticated": sp_service.authenticated,
            "last_error": "error occurred — check server logs" if last_err else None,
        },
        "architecture": "Unified-Excel-Graph-API",
    }