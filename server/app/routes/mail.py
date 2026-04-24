"""
API Router for manual email triggers and connection tests.
"""
from fastapi import APIRouter, HTTPException, BackgroundTasks, Body
from pydantic import BaseModel, field_validator
from typing import Optional

from app.services import email_service
from app.services.scheduler_service import (
    run_daily_report_automation,
    load_scheduler_config,
    save_scheduler_config,
)
from app.core.logger import logger

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

@router.post("/send-daily-report")
async def trigger_manual_report(
    background_tasks: BackgroundTasks,
    request: ManualReportRequest = Body(default=ManualReportRequest()),
):
    """
    Manually triggers the daily energy report email. 
    Runs in the background to prevent the API from hanging.
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

    logger.info("Manual daily report triggered via API.")
    background_tasks.add_task(run_daily_report_automation, trigger_source="api_manual")
    return {"message": "Daily report generation started in background."}

@router.post("/test-connection")
async def send_test_email(request: TestEmailRequest):
    """
    Sends a simple text email to verify SMTP credentials.
    """
    result = email_service.send_test_connection(
        recipient=request.recipient,
        subject=request.subject,
        message=request.message
    )
    
    if result["status"] == "Success":
        return result
    else:
        raise HTTPException(status_code=500, detail=result["error"])