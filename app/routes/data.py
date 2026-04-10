"""
Data endpoints router - reads from SharePoint + SuryaLogix
"""
from fastapi import APIRouter, Query, HTTPException
from typing import Optional, Dict, Any
import pandas as pd
from datetime import datetime
import logging
from app.core.config import get_settings
from app.core.exceptions import IntegrationError
from app.services import data_service
from app.services.ingestion_bridge import run_ingestion_once
from app.schemas.energy import EnergyDataResponse

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/data", tags=["data"])


def _to_float(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


@router.get("/live/unified", response_model=EnergyDataResponse)
async def get_live_unified_data(
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
):
    """Get unified energy data (grid + solar + diesel) from SharePoint and SuryaLogix."""
    return data_service.load_unified_data(start_date, end_date)


@router.get("/live/grid", response_model=EnergyDataResponse)
async def get_live_grid_data(
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
):
    """Get grid energy data from SharePoint Ops_Manual_Entry list."""
    return data_service.load_grid_data(start_date, end_date)


@router.get("/live/solar", response_model=EnergyDataResponse)
async def get_live_solar_data(
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    limit: Optional[int] = Query(None, description="Max records to return"),
):
    """Get solar energy data from SuryaLogix API."""
    result = data_service.load_solar_data(start_date, end_date)
    if limit and limit > 0 and result.get("data"):
        result["data"] = result["data"][:limit]
        result["total_records"] = len(result["data"])
    return result


@router.get("/live/diesel", response_model=EnergyDataResponse)
async def get_live_diesel_data(
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
):
    """Get diesel generator data from SharePoint Ops_Manual_Entry list."""
    return data_service.load_diesel_data(start_date, end_date)


@router.get("/live/daily-summary", response_model=EnergyDataResponse)
async def get_live_daily_summary(
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
):
    """Get daily aggregated summary."""
    return data_service.load_daily_summary(start_date, end_date)


@router.get("/live/last-7-days", response_model=EnergyDataResponse)
async def get_live_last_7_days(
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
):
    """Get last 7 days of solar data from SuryaLogix."""
    from datetime import timedelta
    if not start_date:
        start_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    if not end_date:
        end_date = datetime.now().strftime("%Y-%m-%d")
    return data_service.load_solar_data(start_date, end_date)


@router.get("/live/smb-status", response_model=EnergyDataResponse)
async def get_live_smb_status():
    """Get SMB inverter status - returns empty until SuryaLogix live data is configured."""
    return {"data": [], "date_range": {"min_date": None, "max_date": None}, "total_records": 0}


@router.get("/live/inverter-status")
async def get_live_inverter_status():
    """Get inverter status from SuryaLogix."""
    try:
        from app.services.surya_logix_api_service import SuryaLogixAPIService
        service = SuryaLogixAPIService()
        live_df = service.get_live_data()
        if live_df.empty:
            return {"data": [], "last_update": None, "total_records": 0}
        rows = live_df.to_dict("records")
        return {"data": rows, "last_update": datetime.now().isoformat(), "total_records": len(rows)}
    except Exception as e:
        logger.warning(f"Could not get inverter status: {e}")
        return {"data": [], "last_update": None, "total_records": 0}


@router.post("/refresh")
async def refresh_data():
    """Trigger ingestion pipeline run."""
    try:
        success = run_ingestion_once()
        return {
            "status": "success" if success else "failed",
            "message": "Ingestion pipeline executed" if success else "Pipeline reported failures",
            "pipeline_success": success,
        }
    except Exception as e:
        logger.error(f"Error running ingestion: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to run ingestion: {e}")


@router.get("/debug/status")
async def get_integration_status():
    """Debug endpoint showing SharePoint and SuryaLogix connection status."""
    from app.services.sharepoint_list_data_service import SharePointListDataService
    from app.services.surya_logix_api_service import SuryaLogixAPIService

    sp_status = {"authenticated": False, "last_error": None}
    try:
        sp_service = SharePointListDataService()
        sp_status["authenticated"] = sp_service.is_authenticated()
        sp_status["last_error"] = sp_service.get_last_error()
        if sp_service.is_authenticated():
            sp_status["site_accessible"] = bool(sp_service._get_site_id())
    except Exception as e:
        sp_status["last_error"] = str(e)

    sl_status = {"configured": False}
    try:
        import os
        sl_status["configured"] = bool(os.getenv("SURYALOGIX_USERNAME") and os.getenv("SURYALOGIX_PLANT_ID"))
        sl_status["username"] = os.getenv("SURYALOGIX_USERNAME", "not set")
        sl_status["plant_id"] = os.getenv("SURYALOGIX_PLANT_ID", "not set")
    except Exception as e:
        sl_status["error"] = str(e)

    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "sharepoint": sp_status,
        "suryalogix": sl_status,
    }