import os
import json
import re
from pathlib import Path
from fastapi import APIRouter, HTTPException, Body
from pydantic import BaseModel, field_validator
from typing import Optional, Any, Dict

from app.core.logger import logger
from app.services.scheduler_service import initialize_scheduler_from_config

router = APIRouter(tags=["Scheduler Configuration"])

# ──────────────────────────────────────────────────────────────────────────────
# Configuration Path Setup
# ──────────────────────────────────────────────────────────────────────────────
if "WEBSITE_SITE_NAME" in os.environ:
    # Azure Path
    CONFIG_PATH = Path("/home/site/wwwroot/energy-dashboard/scheduler_config.json")
else:
    # Local Path
    CONFIG_PATH = Path(__file__).parent.parent.parent / "energy-dashboard" / "scheduler_config.json"

# ──────────────────────────────────────────────────────────────────────────────
# Pydantic Model for Frontend Validation
# ──────────────────────────────────────────────────────────────────────────────
class SchedulerSettings(BaseModel):
    to: str
    cc: Optional[str] = ""
    subject: str
    auto_start: Optional[bool] = True
    send_time: str = "09:00"  # Default start time

    @field_validator('send_time')
    @classmethod
    def validate_time_format(cls, v: str) -> str:
        """Ensures the frontend always sends a valid 24-hour HH:MM string."""
        if not re.match(r"^(?:[01]\d|2[0-3]):[0-5]\d$", v):
            raise ValueError("Time must be in strict HH:MM 24-hour format (e.g., '09:30').")
        return v

# ──────────────────────────────────────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────────────────────────────────────
@router.get("/scheduler/config")
async def get_scheduler_config() -> Dict[str, Any]:
    """
    Fetches the current email and scheduler settings for the frontend UI.
    """
    try:
        if not CONFIG_PATH.exists():
            # Return default fallback if the file doesn't exist yet
            return {
                "to": "umang.mittal@maqsoftware.com",
                "cc": "",
                "subject": "Review Noida Daily Energy Optimization Dashboard",
                "auto_start": True,
                "send_time": "09:00"
            }
            
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
            
    except Exception as e:
        logger.error(f"Failed to read scheduler config: {e}")
        raise HTTPException(status_code=500, detail="Could not read configuration file.")

@router.post("/scheduler/config")
async def update_scheduler_config(settings: SchedulerSettings):
    """
    Saves new settings from the frontend into the JSON config file
    and instantly triggers a hot-reload of the background scheduler engine.
    """
    try:
        # Ensure the directory exists
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        
        new_config = settings.model_dump()
        
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(new_config, f, indent=4)
            
        logger.info(f"Frontend updated settings: Time={settings.send_time}, To={settings.to}")
        
        # --- THE HOT RELOAD ---
        # Instantly apply the new timeline to the live server
        logger.info("Triggering background scheduler hot-reload...")
        initialize_scheduler_from_config()
        
        return {
            "status": "success", 
            "message": f"Configuration updated successfully! The system will now start its checks at {settings.send_time}.",
            "data": new_config
        }
        
    except Exception as e:
        logger.error(f"Failed to save scheduler config: {e}")
        raise HTTPException(status_code=500, detail="Could not save configuration file.")