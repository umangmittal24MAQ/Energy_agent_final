"""
API Router for manual email triggers and connection tests.
"""
from fastapi import APIRouter, HTTPException, BackgroundTasks, Body, Depends
from pydantic import BaseModel, field_validator
from typing import Optional

from app.services import email_service
from app.services.scheduler_service import (
    run_daily_report_automation,
    load_scheduler_config,
    save_scheduler_config,
)
from app.core.logger import get_logger

logger = get_logger(__name__)

# FIX S1: Import auth dependencies — these were completely missing from this file.
# Without them, any anonymous caller could trigger report emails to arbitrary
# recipients and overwrite scheduler_config.json via the request body.
from app.routes.auth import get_current_user
from app.routes.scheduler import verify_admin

router = APIRouter(prefix="/mail", tags=["Mail Service"])


class TestEmailRequest(BaseModel):
    recipient: str
    subject: Optional[str] = "Mailing Service Test"
    message: Optional[str] = "This is a test email from your Energy Dashboard."

    @field_validator("recipient")
    @classmethod
    def validate_recipient(cls, value: str) -> str:
        text = (value or "").strip()
        if "@" not in text or "." not in text.split("@")[-1]:
            raise ValueError("Invalid recipient email address")
        return text


class ManualReportRequest(BaseModel):
    to: Optional[str] = None
    cc: Optional[str] = None
    subject: Optional[str] = None
    start_time: Optional[str] = None


# FIX S1: send-daily-report now requires verify_admin.
# This endpoint mutates scheduler_config.json and fires real emails —
# it must be restricted to admins, matching the protection on /scheduler/config.
@router.post("/send-daily-report")
async def trigger_manual_report(
    background_tasks: BackgroundTasks,
    request: ManualReportRequest = Body(default=ManualReportRequest()),
    admin_user: dict = Depends(verify_admin),
):
    """
    Manually triggers the daily energy report email.
    Runs in the background to prevent the API from hanging.
    Restricted to admin users only.
    """
    if any(
        value is not None
        for value in [request.to, request.cc, request.subject, request.start_time]
    ):
        config = load_scheduler_config()
        if request.to is not None:
            config["to"] = request.to
        if request.cc is not None:
            config["cc"] = request.cc
        if request.subject is not None:
            config["subject"] = request.subject
        if request.start_time is not None:
            config["start_time"] = request.start_time
        save_scheduler_config(config)

    logger.info(
        f"Manual daily report triggered by {admin_user.get('email')!r} via API."
    )
    background_tasks.add_task(run_daily_report_automation, trigger_source="api_manual")
    return {"message": "Daily report generation started in background."}


