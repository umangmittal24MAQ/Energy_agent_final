import os
import json
from pathlib import Path
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Body, Depends, status
from pydantic import BaseModel
from typing import Optional, Any, Dict
import os


from app.core.logger import get_logger

logger = get_logger(__name__)
from app.routes.auth import get_current_user

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
# Security: Role-Based Access Control (RBAC)
# ──────────────────────────────────────────────────────────────────────────────
def _get_authorized_admins() -> list[str]:
    """Read admin list from environment at request time, never at import time."""
    raw = os.getenv("AUTHORIZED_ADMINS", "")
    return [e.strip().lower() for e in raw.split(",") if e.strip()]
# ─────────────────────────────────────────────────────────────────────────────
# FIX C3: verify_admin was reading current_user.get("preferred_username") first,
# but get_current_user() returns {"email": ..., "name": ...} — "preferred_username"
# is never in that dict. The "or" fallback to "email" worked accidentally, but
# any future change could silently deny all admins with a 403.
# Now we use the canonical "email" key consistently everywhere.
# ─────────────────────────────────────────────────────────────────────────────
def _extract_email(user: dict) -> str:
    """Single helper to extract email from the session dict — used everywhere."""
    return user.get("email") or ""


def verify_admin(current_user: dict = Depends(get_current_user)) -> dict:
    user_email = _extract_email(current_user)
    if user_email.lower() not in _get_authorized_admins():
        logger.warning(f"Unauthorized attempt by: {user_email!r}")
        raise HTTPException(status_code=403, detail="Admin access required.")
    return current_user

# ──────────────────────────────────────────────────────────────────────────────
# GET /api/scheduler/check-admin-status
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/scheduler/check-admin-status")
async def check_admin_status(current_user: dict = Depends(get_current_user)) -> Dict[str, Any]:
    """
    Check if the current user is an admin.
    Returns { "is_admin": true/false, "email": user_email }
    """
    user_email = _extract_email(current_user)
    is_admin = user_email.lower() in [e.lower() for e in _get_authorized_admins()]

    logger.info(f"Admin check for user: '{user_email}' | is_admin: {is_admin}")

    return {
        "is_admin": is_admin,
        "email": user_email,
    }

# ──────────────────────────────────────────────────────────────────────────────
# Configuration Path Setup
# ──────────────────────────────────────────────────────────────────────────────
if "WEBSITE_SITE_NAME" in os.environ:
    CONFIG_PATH = Path("/home/data/energy-dashboard/scheduler_config.json")
else:
    CONFIG_PATH = Path(__file__).parent.parent.parent / "energy-dashboard" / "scheduler_config.json"

# ──────────────────────────────────────────────────────────────────────────────
# Pydantic Models
# ──────────────────────────────────────────────────────────────────────────────
from pydantic import BaseModel, field_validator
import re

class EmailSettings(BaseModel):
    to: str
    cc: Optional[str] = ""
    start_time: str = "09:00"
    subject: str
    auto_start: Optional[bool] = True
    include_sections: Optional[Dict[str, bool]] = None
    uploaded_template_path: Optional[str] = None

    @field_validator("start_time")
    @classmethod
    def validate_time_format(cls, v: str) -> str:
        if not re.match(r"^\d{2}:\d{2}$", v):
            raise ValueError("start_time must be in HH:MM format (e.g. 09:30)")
        h, m = v.split(":")
        if not (0 <= int(h) <= 23 and 0 <= int(m) <= 59):
            raise ValueError("start_time out of range (00:00–23:59)")
        return v

class SchedulerStartRequest(BaseModel):
    start_time: Optional[str] = None

# ──────────────────────────────────────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────────────────────────────────────

# PUBLIC (Read-Only): Any logged-in user can view the config
@router.get("/scheduler/config")
async def get_scheduler_config(current_user: dict = Depends(get_current_user)) -> Dict[str, Any]:
    """Fetches the current email and scheduler settings for the frontend UI."""
    try:
        config = load_scheduler_config()
        if "start_time" not in config:
            config["start_time"] = config.get("send_time", "09:00")
        config.setdefault("start_time", "09:00")
        for deprecated_key in [
            "send_time", "reminder_to", "reminder_start_time",
            "reminder_interval_minutes", "reminder_deadline_time", "custom_message",
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


# 🔴 RESTRICTED (Admins Only)
@router.post("/scheduler/config")
async def update_scheduler_config(
    settings: EmailSettings,
    admin_user: dict = Depends(verify_admin),
):
    """Saves new email settings from the frontend into the JSON config file."""
    try:
        existing = load_scheduler_config()
        new_config = {**existing, **settings.model_dump(exclude_none=True)}
        new_config["auto_start"] = True

        for deprecated_key in [
            "send_time", "reminder_to", "reminder_start_time",
            "reminder_interval_minutes", "reminder_deadline_time", "custom_message",
        ]:
            new_config.pop(deprecated_key, None)

        save_scheduler_config(new_config)
        initialize_scheduler_from_config()

        logger.info(
            f"Email settings updated by {_extract_email(admin_user)!r}: "
            f"To={settings.to}, CC={settings.cc}"
        )

        start_scheduler(new_config.get("start_time", "09:00"))

        return {
            "status": "success",
            "message": "Email configuration updated successfully!",
            "data": new_config,
        }

    except Exception as e:
        logger.error(f"Failed to save scheduler config: {e}")
        raise HTTPException(status_code=500, detail="Could not save configuration file.")


# 🟢 PUBLIC (Read-Only)
@router.get("/scheduler/status")
async def scheduler_status(current_user: dict = Depends(get_current_user)) -> Dict[str, Any]:
    """Returns active scheduler status and next run time."""
    return get_scheduler_status()


# 🟢 PUBLIC (Read-Only)
@router.get("/scheduler/history")
async def scheduler_history(current_user: dict = Depends(get_current_user)) -> Dict[str, Any]:
    """Returns scheduler send history from scheduler_log.json (read-only)."""
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


# 🔴 RESTRICTED (Admins Only)
@router.post("/scheduler/start")
async def scheduler_start(
    payload: SchedulerStartRequest = Body(default=SchedulerStartRequest()),
    admin_user: dict = Depends(verify_admin),
) -> Dict[str, Any]:
    """Starts or updates the recurring daily scheduler with the given time."""
    cfg = load_scheduler_config()
    start_time = payload.start_time or cfg.get("start_time", cfg.get("send_time", "09:00"))
    cfg["start_time"] = start_time
    cfg["auto_start"] = True
    for deprecated_key in [
        "send_time", "reminder_to", "reminder_start_time",
        "reminder_interval_minutes", "reminder_deadline_time", "custom_message",
    ]:
        cfg.pop(deprecated_key, None)
    save_scheduler_config(cfg)
    start_scheduler(start_time)
    return {"status": "running", "start_time": start_time, **get_scheduler_status()}


# 🔴 RESTRICTED (Admins Only)
@router.post("/scheduler/stop")
async def scheduler_stop(admin_user: dict = Depends(verify_admin)) -> Dict[str, Any]:
    """Stops all scheduler jobs related to daily report automation."""
    result = stop_scheduler()
    return {**result, **get_scheduler_status()}

# TEMP TEST ONLY — remove after testing
@router.post("/scheduler/test-late-check")
async def test_late_check(admin_user: dict = Depends(verify_admin)):
    from app.services.scheduler_service import _run_late_data_check
    _run_late_data_check()
    return {"message": "Late check triggered — see backend logs"}