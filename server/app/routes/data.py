"""
Data endpoints router - reads from the Unified Master Excel file.
"""
# 1. 🚀 ADDED 'Depends' to the FastAPI imports
from fastapi import APIRouter, Query, HTTPException, Depends
from typing import Optional
import logging
from app.services import data_service
from app.schemas.energy import EnergyDataResponse

# 2. 🚀 IMPORTED your new bouncer function
from app.routes.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/data", 
    tags=["data"],
    dependencies=[Depends(get_current_user)]
)

@router.get("/live/unified", response_model=EnergyDataResponse)
async def get_live_unified_data(
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    # 3. 🚀 INJECTED the dependency here
    user: dict = Depends(get_current_user) 
):
    """Fetches unified energy data (grid + solar + diesel) from the Master Excel file."""
    
    # Optional: You can now log exactly who is pulling the enterprise data!
    logger.info(f"User '{user['email']}' requested energy data.")
    
    return data_service.load_unified_data(start_date, end_date)

@router.get("/debug/status")
async def get_integration_status(
    # 🚀 Protected the debug route too, so outside attackers can't map your architecture
    user: dict = Depends(get_current_user) 
):
    """Simplified health check for the new Excel-based architecture."""
    from app.services.sharepoint_data_service import get_service
    
    sp_service = get_service()
    return {
        "sharepoint": {
            "authenticated": sp_service.authenticated,
            "last_error": sp_service.get_last_error()
        },
        "architecture": "Unified-Excel-Graph-API",
        "requested_by": user['email'] # Optional: echo the user back
    }