import os
import json
from pathlib import Path
from fastapi import APIRouter, HTTPException, Body
from pydantic import BaseModel
from typing import Optional, Any, Dict

from app.core.logger import logger

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
    subject: str
    auto_start: Optional[bool] = True

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
                "auto_start": True
            }
            
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
            
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
        # Ensure the directory exists
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        
        new_config = settings.model_dump()
        
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(new_config, f, indent=4)
            
        logger.info(f"Frontend updated email settings: To={settings.to}, CC={settings.cc}")
        
        return {
            "status": "success", 
            "message": "Email configuration updated successfully!",
            "data": new_config
        }
        
    except Exception as e:
        logger.error(f"Failed to save scheduler config: {e}")
        raise HTTPException(status_code=500, detail="Could not save configuration file.")