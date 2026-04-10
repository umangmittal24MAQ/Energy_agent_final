"""
Email service for sending energy reports and notifications.
"""
import io
import os
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime
import pandas as pd

from zoneinfo import ZoneInfo
from app.core.logger import logger

# ──────────────────────────────────────────────────────────────────────────────
# Configuration & Paths
# ──────────────────────────────────────────────────────────────────────────────
if "WEBSITE_SITE_NAME" in os.environ:
    BASE_DIR = Path("/home/data/energy-dashboard")
    ROOT_DIR = Path("/home/site/wwwroot")
else:
    ROOT_DIR = Path(__file__).parent.parent.parent
    BASE_DIR = ROOT_DIR / "energy-dashboard"

# Ensure the template exists
TEMPLATE_PATH = ROOT_DIR / "app" / "templates" / "email_body.html"

def _generate_excel_attachment(df: pd.DataFrame) -> bytes:
    """Generates an in-memory Excel file containing the recent Master Data."""
    output_buffer = io.BytesIO()
    
    # Sort descending by date and grab the last 30 days
    if "Date" in df.columns:
        df_sorted = df.sort_values(by="Date", ascending=False).head(30)
    else:
        df_sorted = df.tail(30)
        
    with pd.ExcelWriter(output_buffer, engine='openpyxl') as writer:
        df_sorted.to_excel(writer, sheet_name='Energy_Report', index=False)
        
    output_buffer.seek(0)
    return output_buffer.read()

def send_daily_report(trigger_source: str = "scheduler", manual_date: Optional[str] = None) -> Dict[str, Any]:
    """
    Sends the daily energy report by reading from Master-data.xlsx.
    Dynamically pulls recipients and subject from scheduler_config.json.
    """
    try:
        from app.services.sharepoint_data_service import get_service as get_excel_service
        from app.services.scheduler_service import load_scheduler_config
        
        logger.info(f"Generating Daily Energy Report (Trigger: {trigger_source})...")
        
        # 1. Fetch live config for dynamic frontend control
        sched_config = load_scheduler_config()
        
        # 2. Fetch Master Data
        sp_service = get_excel_service()
        master_df = sp_service.fetch_sheet_data("master_data")
        
        if master_df is None or master_df.empty:
            logger.error("Master data is empty. Cannot send report.")
            return {"status": "Failed", "notes": "Master data is empty"}

        # 3. Get the target row
        if manual_date:
            row_df = master_df[master_df['Date'] == manual_date]
            if row_df.empty:
                logger.error(f"No data found for requested date {manual_date}")
                return {"status": "Failed", "notes": f"No data for {manual_date}"}
            day_data = row_df.iloc[-1].to_dict()
        else:
            day_data = master_df.iloc[-1].to_dict()
            
        report_date = str(day_data.get("Date", datetime.today().strftime("%Y-%m-%d")))

        # 4. Generate HTML Body
        try:
            from app.services.scheduler_service import build_energy_report_html
            table_html = build_energy_report_html(master_df)
            
            if TEMPLATE_PATH.exists():
                with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
                    body_html = f.read()
                # Inject the table into the template
                body_html = body_html.replace("", table_html)
            else:
                # Fallback simple HTML
                body_html = f"<html><body><h3>Energy Report for {report_date}</h3><table border='1'>{table_html}</table></body></html>"
                
        except Exception as e:
            logger.error(f"HTML generation failed: {e}")
            body_html = f"<p>Report generated for {report_date}, but HTML formatting failed.</p>"

        # 5. Generate Excel Attachment directly from Pandas (No Ingestion Dependency!)
        try:
            attachment_bytes = _generate_excel_attachment(master_df)
            attachment_name = f"Energy_Report_{datetime.today().strftime('%Y%m%d')}.xlsx"
        except Exception as e:
            logger.error(f"Attachment generation failed: {e}")
            attachment_bytes = None
            attachment_name = None

        # 6. Configure Email Parameters dynamically from JSON
        smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
        smtp_port = int(os.getenv("SMTP_PORT", 587))
        email_from = os.getenv("EMAIL_FROM", "suryalogix.renew@gmail.com")
        sender_password = os.getenv("EMAIL_PASSWORD", "")

        operator_email = sched_config.get("to", os.getenv("OPERATOR_EMAIL", "umang.mittal@maqsoftware.com"))
        cc_emails_str = sched_config.get("cc", "")
        
        # Build recipient list
        to_list = [e.strip() for e in operator_email.split(',') if e.strip()]
        cc_list = [e.strip() for e in cc_emails_str.split(',') if e.strip()]
        all_recipients = to_list + cc_list
        
        subject = sched_config.get("subject", f"Review Noida Daily Energy Optimization Dashboard ({report_date})")

        # 7. Construct Email
        msg = MIMEMultipart("alternative")
        msg["From"] = email_from
        msg["To"] = ", ".join(to_list)
        if cc_list:
            msg["Cc"] = ", ".join(cc_list)
        msg["Subject"] = subject

        msg.attach(MIMEText(body_html, "html"))

        if attachment_bytes and attachment_name:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(attachment_bytes)
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f'attachment; filename="{attachment_name}"')
            msg.attach(part)

        # 8. Send Email
        logger.debug(f"Connecting to {smtp_server}:{smtp_port}")
        with smtplib.SMTP(smtp_server, smtp_port, timeout=15) as server:
            server.starttls()
            server.login(email_from, sender_password)
            server.sendmail(email_from, all_recipients, msg.as_string())

        logger.info(f"Report emailed successfully to {', '.join(all_recipients)}")
        return {
            "status": "Success", 
            "recipients": ", ".join(all_recipients), 
            "attachment": attachment_name
        }

    except Exception as e:
        logger.error(f"Failed to send daily report: {e}")
        return {"status": "Failed", "error": str(e)}

def send_operator_reminder() -> Dict[str, Any]:
    """Sends a reminder if grid data is missing."""
    try:
        from app.services.scheduler_service import load_scheduler_config
        sched_config = load_scheduler_config()
        
        smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
        smtp_port = int(os.getenv("SMTP_PORT", 587))
        email_from = os.getenv("EMAIL_FROM", "suryalogix.renew@gmail.com")
        sender_password = os.getenv("EMAIL_PASSWORD", "")
        
        operator_email = sched_config.get("to", os.getenv("OPERATOR_EMAIL", "umang.mittal@maqsoftware.com"))
        
        subject = "Action Required: Operator Data Missing"
        body = (
            f"Hello,\n\n"
            f"The Automated Energy Pipeline attempted to run, but no operator data was found for today.\n"
            f"Please update the Grid and Diesel entries in the SharePoint Excel file so the report can be generated.\n\n"
            f"Thank you,\nEnergy Automation Agent"
        )

        msg = MIMEMultipart("alternative")
        msg["From"] = email_from
        msg["To"] = operator_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP(smtp_server, smtp_port, timeout=10) as server:
            server.starttls()
            server.login(email_from, sender_password)
            server.sendmail(email_from, [operator_email], msg.as_string())
            
        return {"status": "Success", "notes": "Operator reminder sent"}
    except Exception as e:
        logger.error(f"Failed to send reminder: {e}")
        return {"status": "Failed", "error": str(e)}