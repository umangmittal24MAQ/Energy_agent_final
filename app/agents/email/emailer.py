"""
Email service for sending energy reports and notifications.
"""
import json
import os
import re
import html as html_lib
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime, date
import pandas as pd
from dotenv import load_dotenv
from zoneinfo import ZoneInfo

# Assuming export function exists in your ingestion package
try:
    from app.agents.ingestion.exporter import export_ecs_style_xlsx
except ImportError:
    export_ecs_style_xlsx = None

logger = logging.getLogger(__name__)

def _load_email_env() -> None:
    """Load email environment variables from supported files/locations."""
    energy_dashboard_path = Path(__file__).parent.parent.parent.parent / "energy-dashboard"
    candidates = [
        energy_dashboard_path / ".env",
        energy_dashboard_path / "env",
        energy_dashboard_path.parent / ".env",
    ]
    for env_path in candidates:
        if env_path.exists():
            load_dotenv(env_path, override=True)

_load_email_env()

def _get_env_value(*keys: str, default: str = "") -> str:
    for key in keys:
        value = os.getenv(key)
        if value is None:
            continue
        text = str(value).strip().strip('"').strip("'")
        if text:
            return text
    return default

def send_email(
    to_list: List[str], 
    cc_list: List[str], 
    subject: str, 
    html_body: str, 
    attachment_bytes: Optional[bytes] = None, 
    attachment_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Core function to send an email via SMTP.
    """
    smtp_server = _get_env_value("SMTP_SERVER")
    smtp_port_str = _get_env_value("SMTP_PORT", default="587")
    smtp_user = _get_env_value("SMTP_USER", "EMAIL_USER")
    smtp_pass = _get_env_value("SMTP_PASS", "EMAIL_PASS")
    sender_email = _get_env_value("SENDER_EMAIL", "EMAIL_SENDER", default=smtp_user)

    try:
        smtp_port = int(smtp_port_str)
    except ValueError:
        smtp_port = 587

    if not all([smtp_server, smtp_user, smtp_pass, sender_email]):
        error_msg = "Missing required SMTP credentials in environment."
        logger.error(error_msg)
        return {"status": "Failed", "notes": error_msg}

    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = ", ".join(to_list)
    if cc_list:
        msg['Cc'] = ", ".join(cc_list)
    msg['Subject'] = subject

    msg.attach(MIMEText(html_body, 'html'))

    if attachment_bytes and attachment_name:
        part = MIMEBase('application', 'octet-stream')
        part.set_payload(attachment_bytes)
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', f'attachment; filename="{attachment_name}"')
        msg.attach(part)

    all_recipients = to_list + cc_list

    try:
        server = smtplib.SMTP(smtp_server, smtp_port, timeout=30)
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(sender_email, all_recipients, msg.as_string())
        server.quit()
        logger.info(f"Email successfully sent to: {to_list}")
        return {"status": "Success", "notes": f"Sent to {len(to_list)} recipients"}
    except Exception as e:
        error_msg = f"SMTP Error: {type(e).__name__}: {str(e)[:200]}"
        logger.error(error_msg)
        return {"status": "Failed", "notes": error_msg}

def send_energy_report(
    to_list: List[str], 
    cc_list: List[str], 
    day_data: dict, 
    sched_config: dict, 
    empty_fallback: bool = False
) -> Dict[str, Any]:
    """
    Builds and sends the final daily report email.
    If empty_fallback is True (e.g. 10:30 AM cutoff reached), sends an alert that data was not submitted.
    """
    current_date = date.today().strftime("%Y-%m-%d")

    # =========================================================
    # FALLBACK / EMPTY REPORT PATH (Triggered at 10:30 AM)
    # =========================================================
    if empty_fallback or not day_data:
        logger.warning(f"Sending EMPTY fallback report for {current_date}")
        subject = f"⚠️ ACTION REQUIRED: Missing Daily Energy Report for {current_date}"
        html_body = f"""
        <html>
            <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
                <h2 style="color: #d9534f; border-bottom: 2px solid #d9534f; padding-bottom: 5px;">Daily Energy Report: Data Missing</h2>
                <p>Hello Team,</p>
                <p>The automated system was unable to generate today's Energy Report.</p>
                <p><strong>Reason:</strong> The facility operator did not log the daily Grid and Diesel consumption data into the system by the strict <b>10:30 AM</b> deadline.</p>
                <p style="background-color: #f9f2f4; padding: 10px; border-left: 4px solid #d9534f;">
                    Please ensure the operator logs this data into the <b>Grid & Diesel Log</b> in SharePoint immediately to ensure accurate weekly and monthly reporting.
                </p>
                <p><br><i>Automated Notification by EnergyDashboard Agent</i></p>
            </body>
        </html>
        """
        # Send immediately without attachments
        return send_email(to_list, cc_list, subject, html_body, None, None)

    # =========================================================
    # NORMAL REPORT PATH (Data Found)
    # =========================================================
    subject = f"Daily Energy Report - {current_date}"
    
    # Generate HTML summary body (Adjust this block to match your exact HTML template/format)
    total_grid = day_data.get("Grid Units Consumed (KWh)", 0)
    total_solar = day_data.get("Solar Units Consumed(KWh)", 0)
    total_cost = day_data.get("Total Units Consumed in INR", 0)
    savings = day_data.get("Energy Saving in INR", 0)

    html_body = f"""
    <html>
        <body style="font-family: Arial, sans-serif; color: #333;">
            <h2>Energy Consumption Summary - {current_date}</h2>
            <table style="border-collapse: collapse; width: 50%;">
                <tr style="background-color: #f2f2f2;">
                    <th style="border: 1px solid #ddd; padding: 8px; text-align: left;">Metric</th>
                    <th style="border: 1px solid #ddd; padding: 8px; text-align: right;">Value</th>
                </tr>
                <tr>
                    <td style="border: 1px solid #ddd; padding: 8px;">Grid Consumed</td>
                    <td style="border: 1px solid #ddd; padding: 8px; text-align: right;">{total_grid} kWh</td>
                </tr>
                <tr>
                    <td style="border: 1px solid #ddd; padding: 8px;">Solar Consumed</td>
                    <td style="border: 1px solid #ddd; padding: 8px; text-align: right;">{total_solar} kWh</td>
                </tr>
                <tr>
                    <td style="border: 1px solid #ddd; padding: 8px;">Total Cost</td>
                    <td style="border: 1px solid #ddd; padding: 8px; text-align: right;">₹ {total_cost}</td>
                </tr>
                <tr>
                    <td style="border: 1px solid #ddd; padding: 8px; font-weight: bold; color: green;">Energy Savings</td>
                    <td style="border: 1px solid #ddd; padding: 8px; text-align: right; font-weight: bold; color: green;">₹ {savings}</td>
                </tr>
            </table>
            <p>Please find the detailed ECS-format daily breakdown attached.</p>
            <p><i>Automated by EnergyDashboard Agent</i></p>
        </body>
    </html>
    """

    # Attachment Handling: Custom template or ECS-format xlsx
    uploaded_path = sched_config.get("uploaded_template_path")
    if uploaded_path and os.path.exists(uploaded_path):
        with open(uploaded_path, "rb") as f:
            attachment_bytes = f.read()
        attachment_name = os.path.basename(uploaded_path)
    else:
        try:
            if export_ecs_style_xlsx:
                attachment_bytes = export_ecs_style_xlsx(day_data)
                attachment_name = f"Energy_Report_ECS_{datetime.today().strftime('%Y%m%d')}.xlsx"
                logger.info(f"XLSX attachment created: {attachment_name}")
            else:
                logger.warning("Export function missing. Sending without attachment.")
                attachment_bytes = None
                attachment_name = None
        except Exception as e:
            logger.error(f"Unexpected error generating XLSX: {type(e).__name__}: {e}", exc_info=True)
            attachment_bytes = None
            attachment_name = None

    return send_email(to_list, cc_list, subject, html_body, attachment_bytes, attachment_name)

