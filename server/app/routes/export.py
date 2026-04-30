"""
Export endpoints router - generates downloadable Excel files.
"""
# FIX S2: Added Depends import and get_current_user dependency.
# Previously these endpoints were completely unauthenticated — anyone could
# download full Excel exports of energy/cost data without logging in.
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from datetime import datetime
from app.services import export_service
from app.schemas.scheduler import ExportRequest
from app.routes.auth import get_current_user

router = APIRouter(
    prefix="/export",
    tags=["export"],
    dependencies=[Depends(get_current_user)],  # FIX S2: all export routes now require auth
)

@router.post("/unified")
@router.post("/grid")
@router.post("/solar")
@router.post("/diesel")
async def export_energy_data(request: ExportRequest):
    """Generates a filtered Excel export of the Master Data."""
    output = export_service.export_unified_excel(request.start_date, request.end_date)
    filename = f"Energy_Report_{datetime.now().strftime('%Y%m%d')}.xlsx"

    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )