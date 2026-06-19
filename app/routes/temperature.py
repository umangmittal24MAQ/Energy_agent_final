"""
Temperature Recommendation API
Returns the ASHRAE 55-based optimal HVAC setpoint for Noida Campus.
Cached 10 minutes server-side; no additional OpenWeatherMap calls needed.
"""
from fastapi import APIRouter, Depends, HTTPException

from app.routes.auth import get_current_user
from app.services.temperature_service import get_temperature_recommendation

router = APIRouter(
    prefix="/temperature",
    tags=["temperature"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/recommendation")
async def temperature_recommendation():
    """
    GET /api/temperature/recommendation

    Returns optimal indoor temperature setpoint computed from live weather
    using the ASHRAE 55 Adaptive Comfort Model with cloud, humidity, and
    wind-convection modulations, plus institutional guardrails [21.5–25.5°C].

    Response includes:
      - outdoor weather snapshot
      - target setpoint + comfort band
      - comfort score (0–100)
      - energy efficiency score (0–100)
      - cost estimate (INR savings vs static 22°C)
      - actionable recommendations
      - dynamic energy-saving insights
    """
    data = get_temperature_recommendation()
    if data is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Temperature recommendation unavailable. "
                "Ensure OPENWEATHERMAP_API_KEY is set and the weather service is reachable."
            ),
        )
    return data
