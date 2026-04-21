"""
API Router for manual email triggers and connection tests.
"""
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel, EmailStr
from typing import Optional

from app.services import email_service
from app.services.scheduler_service import run_daily_report_automation
from app.core.logger import logger

router = APIRouter(prefix="/mail", tags=["Mail Service"])

class TestEmailRequest(BaseModel):
    recipient: EmailStr
    subject: Optional[str] = "Mailing Service Test"
    message: Optional[str] = "This is a test email from your Energy Dashboard."

@router.post("/send-daily-report")
async def trigger_manual_report(background_tasks: BackgroundTasks):
    """
    Manually triggers the daily energy report email. 
    Runs in the background to prevent the API from hanging.
    """
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