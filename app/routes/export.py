"""
Export endpoints router
"""
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from datetime import datetime
from app.services import export_service
from app.schemas.scheduler import ExportRequest

router = APIRouter(prefix="/export", tags=["export"])


@router.post("/unified")
async def export_unified(request: ExportRequest):
    """Export unified data to Excel"""
    output = export_service.export_unified_excel(request.start_date, request.end_date)

    filename = f"Unified_Energy_Report_{datetime.now().strftime('%Y%m%d')}.xlsx"

    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.post("/grid")
async def export_grid(request: ExportRequest):
    """Export grid data to Excel"""
    output = export_service.export_grid_excel(request.start_date, request.end_date)

    filename = f"Grid_Energy_Report_{datetime.now().strftime('%Y%m%d')}.xlsx"

    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.post("/solar")
async def export_solar(request: ExportRequest):
    """Export solar data to Excel"""
    output = export_service.export_solar_excel(request.start_date, request.end_date)

    filename = f"Solar_Energy_Report_{datetime.now().strftime('%Y%m%d')}.xlsx"

    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.post("/diesel")
async def export_diesel(request: ExportRequest):
    """Export diesel data to Excel"""
    output = export_service.export_diesel_excel(request.start_date, request.end_date)

    filename = f"Diesel_Energy_Report_{datetime.now().strftime('%Y%m%d')}.xlsx"

    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.post("/ecs")
async def export_ecs():
    """Export ECS format report"""
    output = export_service.export_ecs_excel()

    filename = f"Energy_Report_ECS_{datetime.now().strftime('%Y%m%d')}.xlsx"

    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )
