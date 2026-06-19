"""
Weather Service
Fetches live weather data from OpenWeatherMap for Noida and derives
solar impact context (cloud cover, humidity, temperature effects).
Cached for 10 minutes to avoid hammering the API on every dashboard load.
"""
import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import httpx

from app.services.cache_service import get_cache

logger = logging.getLogger(__name__)

# Noida coordinates
NOIDA_LAT = 28.535
NOIDA_LON = 77.391
IST = ZoneInfo("Asia/Kolkata")

# Solar plant constants (updated from manager specs: 598 kW, 1049 panels, ~3000 kWh/day target)
SYSTEM_CAPACITY_KW = 598.0                     # Installed DC capacity (kW)
TOTAL_PANELS = 1049                            # Number of panels
PANEL_WATTAGE_WP = 570                         # ~570 Wp per panel (598000W / 1049)
TARGET_DAILY_KWH = 3000.0                      # Expected daily generation at STC (kWh)
PANEL_EFFICIENCY_DERATING_PER_DEGREE = 0.004   # ~0.4% loss per °C above 25°C

CACHE_KEY = "weather:noida:current"
CACHE_TTL_SECONDS = 600  # 10 minutes

# Pull real farm capacity from settings (overridable via SOLAR_SYSTEM_CAPACITY_KW env var)
def _get_system_capacity_kw() -> float:
    try:
        from app.core.config import get_settings
        return get_settings().solar_system_capacity_kw
    except Exception:
        return SYSTEM_CAPACITY_KW  # fallback to module-level constant


def _derive_solar_impact(cloud_pct: int, weather_main: str, temp_c: float) -> dict:
    """
    Derive a human-readable solar impact level and reason from weather data.
    Returns: { level: 'clear'|'moderate'|'heavy', reason: str, derating_factor: float }
    """
    weather_lower = weather_main.lower()

    # Rain / thunderstorm / snow → always heavy
    if any(w in weather_lower for w in ("rain", "drizzle", "thunderstorm", "snow")):
        return {
            "level": "heavy",
            "reason": f"{weather_main} detected — panel output significantly reduced. Expect 50–70% lower generation.",
            "derating_factor": 0.60,
        }

    # Fog / haze / mist (common in Noida winters)
    if any(w in weather_lower for w in ("fog", "haze", "mist", "smoke", "dust", "sand")):
        return {
            "level": "moderate",
            "reason": f"{weather_main} reducing direct sunlight — morning generation will recover as visibility improves.",
            "derating_factor": 0.35,
        }

    # Cloud-cover based
    if cloud_pct >= 70:
        return {
            "level": "heavy",
            "reason": f"Heavy cloud cover at {cloud_pct}% is significantly blocking solar irradiance. Expect 40–60% lower generation.",
            "derating_factor": 0.55,
        }
    elif cloud_pct >= 25:
        return {
            "level": "moderate",
            "reason": f"Partial cloud cover at {cloud_pct}% is reducing irradiance. Generation will be moderately impacted.",
            "derating_factor": 0.25,
        }

    # High temperature efficiency loss
    if temp_c >= 40:
        return {
            "level": "moderate",
            "reason": f"Clear sky but high ambient temperature ({temp_c:.0f}°C) may reduce panel efficiency by ~{int((temp_c - 25) * PANEL_EFFICIENCY_DERATING_PER_DEGREE * 100)}%.",
            "derating_factor": 0.08,
        }

    return {
        "level": "clear",
        "reason": "Clear sky conditions — optimal solar generation expected today.",
        "derating_factor": 0.05,
    }


def _calculate_expected_generation(
    derating_factor: float,
    sunrise_ts: int,
    sunset_ts: int,
    now_ts: int,
    temp_c: float,
) -> dict:
    """
    Calculate expected and projected solar generation for today.

    Returns:
        full_day_kwh: Total expected generation for full day (kWh)
        so_far_kwh:   Expected generation from sunrise until now (kWh)
        daylight_hrs: Total daylight hours today
    """
    sunrise_dt = datetime.fromtimestamp(sunrise_ts, tz=IST)
    sunset_dt = datetime.fromtimestamp(sunset_ts, tz=IST)
    now_dt = datetime.fromtimestamp(now_ts, tz=IST)

    daylight_hrs = max(0.0, (sunset_ts - sunrise_ts) / 3600)

    # Temperature derating on top of cloud derating
    temp_derating = max(0.0, (temp_c - 25) * PANEL_EFFICIENCY_DERATING_PER_DEGREE) if temp_c > 25 else 0.0
    total_derating = min(0.90, derating_factor + temp_derating)

    effective_capacity = _get_system_capacity_kw() * (1 - total_derating)

    # Site-specific capacity factor derived from real farm specs:
    # Target 3000 kWh/day ÷ (598 kW × 10.0h avg daylight) = 0.501
    # Validated against Noida PSH range of 4.5–6.0h. ~0.65 was a generic India estimate.
    CAPACITY_FACTOR = 0.501
    full_day_kwh = effective_capacity * daylight_hrs * CAPACITY_FACTOR

    # How much of the day has elapsed since sunrise?
    elapsed_secs = max(0.0, min(now_ts - sunrise_ts, sunset_ts - sunrise_ts))
    elapsed_frac = elapsed_secs / max(1, sunset_ts - sunrise_ts)
    so_far_kwh = full_day_kwh * elapsed_frac

    return {
        "full_day_kwh": round(full_day_kwh, 1),
        "so_far_kwh": round(so_far_kwh, 1),
        "daylight_hrs": round(daylight_hrs, 1),
        "elapsed_pct": round(elapsed_frac * 100, 1),
    }


def fetch_weather() -> Optional[dict]:
    """
    Fetch current weather for Noida from OpenWeatherMap.
    Returns cached result if fresh (< 10 min), otherwise fetches live.
    Returns None if API key is missing or request fails.
    """
    cache = get_cache()
    cached = cache.get(CACHE_KEY)
    if cached:
        logger.debug("Weather: returning cached data")
        return cached

    api_key = os.getenv("OPENWEATHERMAP_API_KEY")
    if not api_key:
        logger.warning("OPENWEATHERMAP_API_KEY not set — weather endpoint will return null")
        return None

    url = (
        f"https://api.openweathermap.org/data/2.5/weather"
        f"?lat={NOIDA_LAT}&lon={NOIDA_LON}"
        f"&appid={api_key}&units=metric"
    )

    try:
        with httpx.Client(timeout=8.0) as client:
            resp = client.get(url)
            resp.raise_for_status()
            raw = resp.json()
    except httpx.TimeoutException:
        logger.error("Weather API request timed out")
        return None
    except httpx.HTTPStatusError as e:
        logger.error(f"Weather API HTTP error {e.response.status_code}: {e.response.text[:200]}")
        return None
    except Exception as e:
        logger.error(f"Weather API unexpected error: {type(e).__name__}: {e}")
        return None

    try:
        now_ts = int(datetime.now(tz=timezone.utc).timestamp())
        temp_c = raw["main"]["temp"]
        feels_like = raw["main"]["feels_like"]
        humidity = raw["main"]["humidity"]
        cloud_pct = raw["clouds"]["all"]
        weather_main = raw["weather"][0]["main"]
        weather_desc = raw["weather"][0]["description"].capitalize()
        wind_ms = raw["wind"]["speed"]
        sunrise_ts = raw["sys"]["sunrise"]
        sunset_ts = raw["sys"]["sunset"]

        # Convert sunrise/sunset to IST strings
        sunrise_str = datetime.fromtimestamp(sunrise_ts, tz=IST).strftime("%H:%M")
        sunset_str = datetime.fromtimestamp(sunset_ts, tz=IST).strftime("%H:%M")

        impact = _derive_solar_impact(cloud_pct, weather_main, temp_c)
        generation = _calculate_expected_generation(
            impact["derating_factor"], sunrise_ts, sunset_ts, now_ts, temp_c
        )

        result = {
            # Core weather
            "temp_c": round(temp_c, 1),
            "feels_like_c": round(feels_like, 1),
            "humidity_pct": humidity,
            "cloud_cover_pct": cloud_pct,
            "weather_main": weather_main,
            "weather_desc": weather_desc,
            "wind_speed_ms": round(wind_ms, 1),
            "sunrise": sunrise_str,
            "sunset": sunset_str,
            # Solar impact
            "solar_impact": impact["level"],          # 'clear' | 'moderate' | 'heavy'
            "impact_reason": impact["reason"],
            "derating_factor": impact["derating_factor"],
            # Expected generation estimates
            "expected_full_day_kwh": generation["full_day_kwh"],
            "expected_so_far_kwh": generation["so_far_kwh"],
            "daylight_hrs": generation["daylight_hrs"],
            "day_elapsed_pct": generation["elapsed_pct"],
            # Meta
            "fetched_at": datetime.now(tz=IST).strftime("%H:%M"),
            "location": "Noida, IN",
        }

        cache.set(CACHE_KEY, result, ttl_seconds=CACHE_TTL_SECONDS)
        logger.info(f"Weather fetched: {temp_c}°C, clouds={cloud_pct}%, impact={impact['level']}")
        return result

    except (KeyError, TypeError, ValueError) as e:
        logger.error(f"Weather response parsing error: {e}. Raw: {str(raw)[:300]}")
        return None