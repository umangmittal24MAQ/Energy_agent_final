import os
import json
from pathlib import Path
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Body
from pydantic import BaseModel
from typing import Optional, Any, Dict

from app.core.logger import logger
from app.services.scheduler_service import (
    SCHEDULER_LOG_FILE,
    get_scheduler_status,
    initialize_scheduler_from_config,
    load_scheduler_config,
    save_scheduler_config,
    start_scheduler,
    stop_scheduler,
)

router = APIRouter(tags=["Scheduler Configuration"])

# ──────────────────────────────────────────────────────────────────────────────
# Configuration Path Setup
# ──────────────────────────────────────────────────────────────────────────────
if "WEBSITE_SITE_NAME" in os.environ:
    # Azure Path
    CONFIG_PATH = Path("/home/data/energy-dashboard/scheduler_config.json")
else:
    # Local Path
    CONFIG_PATH = Path(__file__).parent.parent.parent / "energy-dashboard" / "scheduler_config.json"

# ──────────────────────────────────────────────────────────────────────────────
# Pydantic Model for Frontend Validation
# ──────────────────────────────────────────────────────────────────────────────
class EmailSettings(BaseModel):
    to: str
    cc: Optional[str] = ""
    start_time: str = "09:00"
    subject: str
    auto_start: Optional[bool] = True
    include_sections: Optional[Dict[str, bool]] = None
    uploaded_template_path: Optional[str] = None


class SchedulerStartRequest(BaseModel):
    start_time: Optional[str] = None

# ──────────────────────────────────────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────────────────────────────────────
@router.get("/scheduler/config")
async def get_scheduler_config() -> Dict[str, Any]:
    """
    Fetches the current email and scheduler settings for the frontend UI.
    """
    try:
        config = load_scheduler_config()
        if "start_time" not in config:
            config["start_time"] = config.get("send_time", "09:00")
        config.setdefault("start_time", "09:00")
        for deprecated_key in [
            "send_time",
            "reminder_to",
            "reminder_start_time",
            "reminder_interval_minutes",
            "reminder_deadline_time",
            "custom_message",
        ]:
            config.pop(deprecated_key, None)
        config.setdefault(
            "include_sections",
            {
                "summary_kpis": True,
                "unified_table": True,
                "grid_summary": True,
                "solar_summary": True,
                "diesel_summary": True,
                "inverter_status": True,
                "raw_data": False,
            },
        )
        config.setdefault("uploaded_template_path", None)
        return config
            
    except Exception as e:
        logger.error(f"Failed to read scheduler config: {e}")
        raise HTTPException(status_code=500, detail="Could not read configuration file.")

@router.post("/scheduler/config")
async def update_scheduler_config(settings: EmailSettings):
    """
    Saves new email settings from the frontend into the JSON config file.
    These changes will be applied immediately on the next automated run.
    """
    try:
        existing = load_scheduler_config()
        new_config = {**existing, **settings.model_dump(exclude_none=True)}
        # Keep scheduler always-on for config-only updates.
        new_config["auto_start"] = True

        for deprecated_key in [
            "send_time",
            "reminder_to",
            "reminder_start_time",
            "reminder_interval_minutes",
            "reminder_deadline_time",
            "custom_message",
        ]:
            new_config.pop(deprecated_key, None)

        save_scheduler_config(new_config)
        initialize_scheduler_from_config()
            
        logger.info(f"Frontend updated email settings: To={settings.to}, CC={settings.cc}")

        start_scheduler(new_config.get("start_time", "09:00"))
        
        return {
            "status": "success", 
            "message": "Email configuration updated successfully!",
            "data": new_config
        }
        
    except Exception as e:
        logger.error(f"Failed to save scheduler config: {e}")
        raise HTTPException(status_code=500, detail="Could not save configuration file.")


@router.get("/scheduler/status")
async def scheduler_status() -> Dict[str, Any]:
    """Returns active scheduler status and next run time."""
    return get_scheduler_status()


@router.get("/scheduler/history")
async def scheduler_history() -> Dict[str, Any]:
    """Returns scheduler send history entries from scheduler_log.json (read-only)."""
    try:
        if not SCHEDULER_LOG_FILE.exists():
            return {"entries": []}

        with open(SCHEDULER_LOG_FILE, "r", encoding="utf-8") as f:
            payload = json.load(f)

        if not isinstance(payload, list):
            return {"entries": []}

        def _timestamp_for_sort(entry: Dict[str, Any]) -> float:
            raw = str(entry.get("timestamp", "")).strip()
            if not raw:
                return 0.0
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed.timestamp()
            except ValueError:
                return 0.0

        entries = sorted(payload, key=_timestamp_for_sort, reverse=True)
        return {"entries": entries}
    except Exception as e:
        logger.error(f"Failed to read scheduler history log: {e}")
        raise HTTPException(status_code=500, detail="Could not read scheduler history.")


@router.post("/scheduler/start")
async def scheduler_start(payload: SchedulerStartRequest = Body(default=SchedulerStartRequest())) -> Dict[str, Any]:
    """Starts or updates the recurring daily scheduler with the given time."""
    cfg = load_scheduler_config()
    start_time = payload.start_time or cfg.get("start_time", cfg.get("send_time", "09:00"))
    cfg["start_time"] = start_time
    cfg["auto_start"] = True
    for deprecated_key in [
        "send_time",
        "reminder_to",
        "reminder_start_time",
        "reminder_interval_minutes",
        "reminder_deadline_time",
        "custom_message",
    ]:
        cfg.pop(deprecated_key, None)
    save_scheduler_config(cfg)
    start_scheduler(start_time)
    return {"status": "running", "start_time": start_time, **get_scheduler_status()}


@router.post("/scheduler/stop")
async def scheduler_stop() -> Dict[str, Any]:
    """Stops all scheduler jobs related to daily report automation."""
    result = stop_scheduler()
    return {**result, **get_scheduler_status()}