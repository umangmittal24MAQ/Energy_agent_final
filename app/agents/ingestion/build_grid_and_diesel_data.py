import json
import os
from datetime import datetime


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_FILE = os.path.join(SCRIPT_DIR, "filtered_dashboard_data.json")
OUTPUT_FILE = os.path.join(SCRIPT_DIR, "grid_and_diesel_data.json")

# Fallback defaults used when no explicit tariff config is available in ingestion-agent.
GRID_RATE_INR_PER_KWH = 7.11
DIESEL_RATE_INR_PER_KWH = 30.0


def _safe_float(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def build_grid_and_diesel_row():
    if not os.path.exists(INPUT_FILE):
        raise FileNotFoundError(f"Missing input file: {INPUT_FILE}")

    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        dashboard = json.load(f)

    last_update = dashboard.get("dashboard_info", {}).get("last_update")
    if last_update:
        dt = datetime.fromisoformat(last_update.replace("Z", "+00:00"))
    else:
        dt = datetime.now()

    energy_data = dashboard.get("energy_data", {})
    grid_units_kwh = _safe_float(energy_data.get("Day_Import_kWh"), 0.0)

    # DG units are not available in the current captured API payload.
    # Keep deterministic 0.0 until source fields become available.
    dg_units_kwh = 0.0

    grid_cost_inr = round(grid_units_kwh * GRID_RATE_INR_PER_KWH, 2)
    diesel_cost_inr = round(dg_units_kwh * DIESEL_RATE_INR_PER_KWH, 2)
    total_cost_inr = round(grid_cost_inr + diesel_cost_inr, 2)

    total_units_kwh = round(grid_units_kwh + dg_units_kwh, 3)

    # Savings vs a diesel-only baseline for the same consumed units.
    diesel_only_cost = total_units_kwh * DIESEL_RATE_INR_PER_KWH
    energy_saving_inr = round(max(diesel_only_cost - total_cost_inr, 0.0), 2)

    row = {
        "Date": dt.strftime("%Y-%m-%d"),
        "Time": dt.strftime("%H:%M:%S"),
        "Grid Units Consumed (KWh)": round(grid_units_kwh, 3),
        "DG Units Consumed (KWh)": round(dg_units_kwh, 3),
        "Total Units Consumed in INR": total_units_kwh,
        "Grid Cost (INR)": grid_cost_inr,
        "Diesel Cost (INR)": diesel_cost_inr,
        "Total Cost (INR)": total_cost_inr,
        "Energy Saving (INR)": energy_saving_inr,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump([row], f, indent=2)

    print(f"[OK] grid_and_diesel data generated: {OUTPUT_FILE}")
    print(f"[INFO] Date={row['Date']} Time={row['Time']} Grid={row['Grid Units Consumed (KWh)']} DG={row['DG Units Consumed (KWh)']}")


if __name__ == "__main__":
    build_grid_and_diesel_row()
