"""
Weather endpoint — returns live weather + solar impact context for Noida.
Cached 10 min server-side; no heavy query params needed.
"""
from fastapi import APIRouter, Depends, HTTPException
from app.routes.auth import get_current_user
from app.services.weather_service import fetch_weather

router = APIRouter(
    prefix="/weather",
    tags=["weather"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/current")
async def get_current_weather():
    """
    Returns live weather for Noida + derived solar impact level and
    expected generation estimates.
    Cached server-side for 10 minutes.
    """
    data = fetch_weather()
    if data is None:
        raise HTTPException(
            status_code=503,
            detail="Weather data unavailable. Check OPENWEATHERMAP_API_KEY in .env.",
        )
    return data