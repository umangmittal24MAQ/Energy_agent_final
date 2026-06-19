"""
Temperature Optimization Service — ASHRAE 55 Adaptive Comfort Model
=====================================================================
Implements the Advanced Climate Optimization Agent algorithm:
  1. Core baseline  : 17.8 + (0.31 × T_out)  [ASHRAE 55 adaptive]
  2. Cloud modulation: ±0.5°C based on solar heat gain
  3. Humidity modulation: −0.6°C (humid) / +0.4°C (arid)
  4. Wind-convection modulation: −0.4°C (Loo) / +0.5°C (rain relief)
  5. Institutional guardrails: Hard clamp [21.5°C, 25.5°C]
  6. Energy savings: ~6.2% per °C above the 22°C static baseline

Reuses the existing weather_service cache — no extra API calls.
"""

import logging
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from app.services.weather_service import fetch_weather
from app.services.cache_service import get_cache

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

# ── Guardrails (institutional health & safety bounds) ────────────────────────
SETPOINT_MIN = 21.5   # °C  — lower bound (below this, occupants get cold)
SETPOINT_MAX = 25.5   # °C  — upper bound (above this, discomfort in Indian summer)

# ── Static baseline for savings calculation ───────────────────────────────────
STATIC_BASELINE_C = 22.0          # traditional "set to 22°C" thermostat
SAVINGS_PER_DEGREE = 6.2          # % cooling energy saved per +1°C shift

# ── Score weights (for comfort and efficiency indices) ────────────────────────
IDEAL_COMFORT_TEMP = 23.5         # centre of the [21.5, 25.5] comfort band

CACHE_KEY = "temperature:recommendation"
CACHE_TTL_SECONDS = 600           # match weather cache (10 min)


# ─────────────────────────────────────────────────────────────────────────────
# Core Algorithm
# ─────────────────────────────────────────────────────────────────────────────

def _calculate_setpoint(w: dict) -> dict:
    """
    Apply the multi-variable ASHRAE 55 adaptive model to live weather data.

    Args:
        w: weather dict from weather_service.fetch_weather()

    Returns:
        Full analysis dict with setpoint, modulations, scores, insights.
    """
    temp        = w["temp_c"]
    humidity    = w["humidity_pct"]
    cloud_cover = w["cloud_cover_pct"]
    wind_speed  = w["wind_speed_ms"]
    condition   = w.get("weather_main", "")

    # ── Step 1: ASHRAE 55 Adaptive Comfort Core Baseline ─────────────────────
    base_adaptive = 17.8 + (0.31 * temp)
    modulations: dict[str, float] = {}

    # ── Step 2: Cloud Cover Correction (Solar Heat Gain Coefficient) ──────────
    if cloud_cover >= 70:
        # Overcast → structural radiant loading drops → eco-save (raise setpoint)
        cloud_mod = +0.5
        modulations["Solar Loading — Overcast Sky (ECO-Save)"] = cloud_mod
    elif cloud_cover <= 20:
        # Clear sky → intense radiant baking → need more cooling (lower setpoint)
        cloud_mod = -0.5
        modulations["Solar Loading — Clear Sky (Radiant Bake)"] = cloud_mod
    else:
        cloud_mod = 0.0

    # ── Step 3: Humidity Correction (Latent Moisture Load) ───────────────────
    if humidity >= 65:
        # High humidity reduces body heat rejection → need more dehumidification
        humidity_mod = -0.6
        modulations["Latent Moisture — High Humidity (Chill-Override)"] = humidity_mod
    elif humidity <= 25:
        # Arid conditions → body self-cools efficiently → relax setpoint
        humidity_mod = +0.4
        modulations["Latent Moisture — Arid Sky (ECO-Relaxation)"] = humidity_mod
    else:
        humidity_mod = 0.0

    # ── Step 4: Wind Speed Infiltration Correction ────────────────────────────
    # Detect rain via precipitation volume from the forecast endpoint
    precip_vol = w.get("precip_vol", 0.0) or 0.0

    if wind_speed >= 6.0 and temp >= 38.0:
        # Scorching + windy → Noida Loo heat-draft through walls
        wind_mod = -0.4
        modulations["Wind Convection — Noida Summer Loo (Heat-Draft)"] = wind_mod
    elif wind_speed >= 6.0 and (precip_vol > 0 or temp < 26.0):
        # Cool or rainy + windy → thermal relief through natural ventilation
        wind_mod = +0.5
        modulations["Wind Convection — Cool/Storm Breeze (Thermal Relief)"] = wind_mod
    else:
        wind_mod = 0.0

    # ── Step 5: Raw Setpoint (unbounded) ─────────────────────────────────────
    raw_setpoint = base_adaptive + cloud_mod + humidity_mod + wind_mod

    # ── Step 6: Institutional Guardrails ─────────────────────────────────────
    final_setpoint = round(raw_setpoint, 1)
    was_bounded = False
    bound_reason = ""

    if final_setpoint > SETPOINT_MAX:
        final_setpoint = SETPOINT_MAX
        was_bounded = True
        bound_reason = f"Capped at {SETPOINT_MAX}°C (comfort upper limit)"
    elif final_setpoint < SETPOINT_MIN:
        final_setpoint = SETPOINT_MIN
        was_bounded = True
        bound_reason = f"Floored at {SETPOINT_MIN}°C (health lower limit)"

    # ── Step 7: Energy Savings vs Static 22°C Thermostat ─────────────────────
    savings_pct = 0.0
    if final_setpoint > STATIC_BASELINE_C:
        savings_pct = round((final_setpoint - STATIC_BASELINE_C) * SAVINGS_PER_DEGREE, 1)

    # ── Step 8: HVAC Operational Mode ─────────────────────────────────────────
    if humidity >= 65:
        hvac_mode = "DEHUMIDIFICATION_PRIORITY"
    elif savings_pct > 10:
        hvac_mode = "MAX_ECO_EFFICIENCY"
    else:
        hvac_mode = "STANDARD_AUTOMATION"

    # ── Step 9: Comfort Score (0–100) ─────────────────────────────────────────
    # Gaussian distance from the ideal comfort midpoint
    import math
    band_half = (SETPOINT_MAX - SETPOINT_MIN) / 2
    dist = abs(final_setpoint - IDEAL_COMFORT_TEMP)
    comfort_score = round(100 * math.exp(-0.5 * (dist / band_half) ** 2))

    # ── Step 10: Energy Efficiency Score (0–100) ──────────────────────────────
    # 100 at max setpoint (25.5°C), 0 at min setpoint (21.5°C)
    efficiency_score = round(
        ((final_setpoint - SETPOINT_MIN) / (SETPOINT_MAX - SETPOINT_MIN)) * 100
    )

    # ── Step 11: Cost Estimation ──────────────────────────────────────────────
    # Typical 1.5-ton split AC ~1500W; one degree saves ~6.2% energy
    ac_watt = 1500
    operating_hrs = 8
    kwh_at_22 = (ac_watt * operating_hrs) / 1000             # kWh at static 22°C
    kwh_optimized = kwh_at_22 * (1 - savings_pct / 100)
    kwh_saved = round(kwh_at_22 - kwh_optimized, 2)
    # India avg industrial tariff ₹7/kWh
    cost_saved_inr_per_ac = round(kwh_saved * 7, 1)
    # Campus-level estimate (rough: 50 ACs in a 330 kW facility)
    ac_count_estimate = 50
    campus_saving_inr = round(cost_saved_inr_per_ac * ac_count_estimate)

    cost_estimate = {
        "kwh_saved_per_ac": kwh_saved,
        "cost_saved_inr_per_ac": cost_saved_inr_per_ac,
        "campus_saving_inr_per_day": campus_saving_inr,
        "basis": f"1.5-ton AC @ {operating_hrs}h/day vs static 22°C; ₹7/kWh; ~{ac_count_estimate} units"
    }

    # ── Step 12: Human-readable Insights ─────────────────────────────────────
    insights = _generate_insights(
        temp=temp,
        humidity=humidity,
        cloud_cover=cloud_cover,
        wind_speed=wind_speed,
        final_setpoint=final_setpoint,
        savings_pct=savings_pct,
        hvac_mode=hvac_mode,
        condition=condition,
    )

    return {
        "target_setpoint": final_setpoint,
        "setpoint_range": f"{final_setpoint - 0.5:.1f}–{final_setpoint + 0.5:.1f}°C",
        "base_adaptive": round(base_adaptive, 2),
        "modulations": modulations,
        "bounded_by_guardrails": was_bounded,
        "bound_reason": bound_reason,
        "hvac_mode": hvac_mode,
        "estimated_savings_pct": savings_pct,
        "comfort_score": comfort_score,
        "energy_efficiency_score": efficiency_score,
        "cost_estimate": cost_estimate,
        "insights": insights,
        "recommendations": _build_recommendations(
            final_setpoint, hvac_mode, humidity, wind_speed, savings_pct
        ),
    }


def _generate_insights(
    *,
    temp: float,
    humidity: float,
    cloud_cover: float,
    wind_speed: float,
    final_setpoint: float,
    savings_pct: float,
    hvac_mode: str,
    condition: str,
) -> list[str]:
    """Generate contextual, dynamic energy-saving insight strings."""
    insights = []

    # Temperature-based primary insight
    if temp >= 40:
        insights.append(
            f"Outdoor temperature is {temp}°C. Setting AC to {final_setpoint}°C instead of 21°C "
            f"can reduce cooling energy by ~{savings_pct}%."
        )
    elif temp >= 30:
        insights.append(
            f"With {temp}°C outdoors, maintaining {final_setpoint}°C indoors strikes the right "
            f"comfort–efficiency balance."
        )
    elif temp <= 20:
        insights.append(
            f"Cool outdoor temperature ({temp}°C). Consider natural ventilation before running HVAC.")
    else:
        insights.append(
            f"Moderate outdoor conditions ({temp}°C). Adaptive setpoint {final_setpoint}°C "
            f"minimises unnecessary cooling load."
        )

    # Humidity insight
    if humidity >= 65:
        insights.append(
            f"Humidity is high at {humidity}%. Maintaining {final_setpoint}°C with "
            f"dehumidification provides better perceived comfort than lower temperatures."
        )
    elif humidity <= 25:
        insights.append(
            f"Arid conditions ({humidity}% humidity). The body self-cools efficiently "
            f"— a slightly higher setpoint of {final_setpoint}°C is still comfortable."
        )

    # Cloud / solar insight
    if cloud_cover >= 70:
        insights.append(
            f"Heavy overcast ({cloud_cover}% cloud) reduces radiant heat gain through glass — "
            f"an excellent opportunity to raise the setpoint and save energy."
        )
    elif cloud_cover <= 20 and temp >= 35:
        insights.append(
            f"Clear sky with intense sun ({cloud_cover}% cloud, {temp}°C). Ensure window blinds "
            f"are closed to reduce radiant cooling load on AC units."
        )

    # Wind insight
    if wind_speed >= 6.0 and temp >= 38.0:
        insights.append(
            f"Hot {wind_speed} m/s Loo winds detected. Keep air intake vents closed to prevent "
            f"hot external air infiltrating conditioned spaces."
        )
    elif wind_speed >= 6.0 and temp < 28.0:
        insights.append(
            f"Cool breeze at {wind_speed} m/s. Utilise cross-ventilation before activating HVAC "
            f"to further reduce energy consumption."
        )

    # HVAC mode insight
    if hvac_mode == "MAX_ECO_EFFICIENCY":
        insights.append(
            f"Today's conditions enable MAX ECO mode — estimated {savings_pct}% reduction vs a "
            f"static 22°C setting across all campus AC units."
        )

    return insights[:4]   # cap at 4 to keep the widget clean


def _build_recommendations(
    setpoint: float,
    hvac_mode: str,
    humidity: float,
    wind_speed: float,
    savings_pct: float,
) -> list[str]:
    """Build actionable short-form recommendations for the email and widget."""
    recs = [f"Set AC thermostat to {setpoint}°C"]

    if humidity >= 65:
        recs.append("Enable dehumidification mode if available")
    if wind_speed < 3.0:
        recs.append("Use ceiling fans at medium speed for enhanced air circulation")
    elif wind_speed >= 6.0:
        recs.append("Natural ventilation available — delay AC activation until outdoor temp rises")

    if savings_pct > 0:
        recs.append(f"Expected {savings_pct}% energy saving vs static 22°C baseline")
    if hvac_mode == "DEHUMIDIFICATION_PRIORITY":
        recs.append("Prioritise dehumidification over cooling for improved occupant comfort")

    recs.append("Schedule AC maintenance if unit struggles to reach setpoint in < 20 min")
    return recs[:5]


# ─────────────────────────────────────────────────────────────────────────────
# Public Entry Point
# ─────────────────────────────────────────────────────────────────────────────

def get_temperature_recommendation() -> Optional[dict]:
    """
    Fetch live weather and compute the optimal HVAC setpoint.
    Cached 10 minutes (same TTL as weather cache).
    Returns None if weather data is unavailable.
    """
    cache = get_cache()
    cached = cache.get(CACHE_KEY)
    if cached:
        logger.debug("Temperature: returning cached recommendation")
        return cached

    weather = fetch_weather()
    if not weather:
        logger.warning("Temperature recommendation unavailable — weather fetch failed")
        return None

    try:
        analysis = _calculate_setpoint(weather)

        now_ist = datetime.now(IST)
        result = {
            # ── Weather snapshot ────────────────────────────────────────────
            "outdoor_temperature":    weather["temp_c"],
            "feels_like":             weather["feels_like_c"],
            "humidity":               weather["humidity_pct"],
            "cloud_cover":            weather["cloud_cover_pct"],
            "wind_speed":             weather["wind_speed_ms"],
            "weather_condition":      weather["weather_main"],
            "weather_description":    weather.get("weather_desc", ""),
            # ── Setpoint ────────────────────────────────────────────────────
            "target_setpoint":        analysis["target_setpoint"],
            "setpoint_range":         analysis["setpoint_range"],
            "recommended_indoor_temperature": analysis["setpoint_range"],
            # ── Scores ──────────────────────────────────────────────────────
            "comfort_score":          analysis["comfort_score"],
            "energy_efficiency_score": analysis["energy_efficiency_score"],
            # ── Algorithm transparency ──────────────────────────────────────
            "base_adaptive":          analysis["base_adaptive"],
            "modulations":            analysis["modulations"],
            "bounded_by_guardrails":  analysis["bounded_by_guardrails"],
            "bound_reason":           analysis["bound_reason"],
            "hvac_mode":              analysis["hvac_mode"],
            "estimated_savings_pct":  analysis["estimated_savings_pct"],
            # ── Cost estimate ────────────────────────────────────────────────
            "cost_estimate":          analysis["cost_estimate"],
            # ── Actionable output ────────────────────────────────────────────
            "insights":               analysis["insights"],
            "recommendations":        analysis["recommendations"],
            # ── Meta ─────────────────────────────────────────────────────────
            "calculated_at":          now_ist.strftime("%H:%M"),
            "location":               "Noida, Sector 145",
        }

        cache.set(CACHE_KEY, result, ttl_seconds=CACHE_TTL_SECONDS)
        logger.info(
            f"Temperature recommendation: {result['target_setpoint']}°C | "
            f"Comfort={result['comfort_score']} Efficiency={result['energy_efficiency_score']} "
            f"Savings={result['estimated_savings_pct']}%"
        )
        return result

    except Exception as exc:
        logger.error(f"Temperature calculation error: {type(exc).__name__}: {exc}", exc_info=True)
        return None
