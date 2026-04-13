"""
Email service for sending energy reports and notifications.
"""
import io
import os
import re
import smtplib
import logging
import html as html_lib
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
# Formatting Helpers (From your strict specifications)
# ──────────────────────────────────────────────────────────────────────────────
def _num(value: Any, default: float = 0.0) -> float:
    if value is None or value == "": return default
    try: return float(str(value).replace(",", "").strip())
    except Exception:
        match = re.search(r"[-+]?\d*\.?\d+", str(value))
        return float(match.group(0)) if match else default

def _format_en_in(value: float, decimals: int) -> str:
    rounded = f"{abs(value):.{decimals}f}"
    whole, frac = rounded.split(".") if "." in rounded else (rounded, "")
    if len(whole) > 3:
        last_three = whole[-3:]
        lead = whole[:-3]
        groups = []
        while len(lead) > 2:
            groups.insert(0, lead[-2:])
            lead = lead[:-2]
        if lead: groups.insert(0, lead)
        whole = ",".join(groups + [last_three])
    sign = "-" if value < 0 else ""
    return f"{sign}{whole}.{frac}" if decimals > 0 else f"{sign}{whole}"

def normalizeIssueText(value: Any) -> str:
    if value is None: return "No issues"
    text = str(value).strip()
    if not text: return "No issues"
    return text.lower()[:1].upper() + text.lower()[1:]

# ──────────────────────────────────────────────────────────────────────────────
# Core HTML Generator
# ──────────────────────────────────────────────────────────────────────────────
def send_daily_report(trigger_source: str = "scheduler", manual_date: Optional[str] = None, is_missing_data: bool = False) -> Dict[str, Any]:
    try:
        from app.services.sharepoint_data_service import get_service as get_excel_service
        from app.services.scheduler_service import load_scheduler_config
        
        logger.info(f"Generating Daily Energy Report (Trigger: {trigger_source})...")
        
        sched_config = load_scheduler_config()
        sp_service = get_excel_service()
        master_df = sp_service.fetch_sheet_data("master_data")
        
        if master_df is None or master_df.empty:
            return {"status": "Failed", "notes": "Master data is empty"}

        if manual_date:
            row_df = master_df[master_df['Date'] == manual_date]
            day_data = row_df.iloc[-1].to_dict() if not row_df.empty else {}
        else:
            day_data = master_df.iloc[-1].to_dict()
            
        report_date = str(day_data.get("Date", datetime.today().strftime("%Y-%m-%d")))

        # --- Inject Warning if Data is Missing ---
        if is_missing_data:
            warning_msg = (
                "<div style='background-color:#ffebee; padding:15px; border-left:4px solid #f44336; color:#d32f2f; margin:18px 24px 0 24px; font-size:14px;'>"
                "<b>⚠️ ACTION REQUIRED:</b> The operator did not log today's data by the 10:30 AM deadline. "
                "The report below only contains data up to yesterday."
                "</div>"
            )
            subject_prefix = "⚠️ ACTION REQUIRED: Missing Data - "
        else:
            warning_msg = ""
            subject_prefix = ""

        # --- Inject HTML EXACTLY as specified ---
        try:
            body_html = _build_strict_email_html(master_df, report_date, custom_message=warning_msg)
        except Exception as e:
            logger.error(f"HTML generation failed: {e}")
            body_html = f"<p>Report generated for {report_date}, but HTML formatting failed.</p>"

        try:
            attachment_bytes = _generate_excel_attachment(master_df)
            attachment_name = f"Energy_Report_{datetime.today().strftime('%Y%m%d')}.xlsx"
        except Exception as e:
            logger.error(f"Attachment generation failed: {e}")
            attachment_bytes = None
            attachment_name = None

        smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
        smtp_port = int(os.getenv("SMTP_PORT", 587))
        email_from = os.getenv("EMAIL_FROM", "suryalogix.renew@gmail.com")
        sender_password = os.getenv("EMAIL_PASSWORD", "")

        operator_email = sched_config.get("to", os.getenv("OPERATOR_EMAIL", "umang.mittal@maqsoftware.com"))
        cc_emails_str = sched_config.get("cc", "")
        
        to_list = [e.strip() for e in operator_email.split(',') if e.strip()]
        cc_list = [e.strip() for e in cc_emails_str.split(',') if e.strip()]
        all_recipients = to_list + cc_list
        
        # Base subject from config, prepended with warning if applicable
        base_subject = sched_config.get("subject", f"Daily Energy Report - Noida Campus - {report_date}")
        subject = f"{subject_prefix}{base_subject}"

        msg = MIMEMultipart("alternative")
        msg["From"] = email_from
        msg["To"] = ", ".join(to_list)
        if cc_list: msg["Cc"] = ", ".join(cc_list)
        msg["Subject"] = subject
        msg.attach(MIMEText(body_html, "html"))

        if attachment_bytes and attachment_name:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(attachment_bytes)
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f'attachment; filename="{attachment_name}"')
            msg.attach(part)

        with smtplib.SMTP(smtp_server, smtp_port, timeout=15) as server:
            server.starttls()
            server.login(email_from, sender_password)
            server.sendmail(email_from, all_recipients, msg.as_string())

        return {"status": "Success", "recipients": ", ".join(all_recipients), "attachment": attachment_name}

    except Exception as e:
        logger.error(f"Failed to send daily report: {e}")
        return {"status": "Failed", "error": str(e)}


def _build_strict_email_html(df: pd.DataFrame, report_date: str, custom_message: str = "") -> str:
    """Builds the exact custom HTML table and layout requested by the user."""
    
    # 1. Clean and Sort the Data
    df = df.copy()
    if "Date" in df.columns:
        df["_parsed_date"] = pd.to_datetime(df["Date"], errors="coerce")
        df = df.dropna(subset=["_parsed_date"])
        df = df.sort_values(by="_parsed_date", ascending=False).head(30)
    else:
        df = df.tail(30)

    # 2. Map Columns to Exact Specifications
    display_dict = []
    for _, row in df.iterrows():
        safe_row = row.fillna("")
        
        true_date = row["_parsed_date"]
        display_date = true_date.strftime("%d-%b-%Y")
        display_day = true_date.strftime("%A")

        raw_time = safe_row.get("Time", "")
        try:
            clean_time = pd.to_datetime(raw_time).strftime("%H:%M") if raw_time else ""
        except:
            clean_time = str(raw_time).strip()[:5]

        new_row = {
            "Date":                                    display_date,
            "Day":                                     display_day,
            "Time":                                    clean_time,
            "Ambient Temperature (°C)":                safe_row.get("Ambient Temperature °C", ""),
            "Grid Units Consumed (kWh)":               safe_row.get("Grid Units Consumed (KWh)", ""),
            "Solar Units Consumed (kWh)":              safe_row.get("Solar Units Consumed(KWh)", ""),
            "Total Units Consumed (kWh)":              safe_row.get("Total Units Consumed (KWh)", ""),
            "Total Cost (INR)":                        safe_row.get("Total Units Consumed in INR", ""),
            "Solar Cost Savings (INR)":                safe_row.get("Energy Saving in INR", ""),
            "Panels Cleaned":                          safe_row.get("Number of Panels Cleaned", ""),
            "Diesel Consumed (Litres)":                safe_row.get("Diesel consumed", ""),
            "Water Treated through STP (kilo Litres)": safe_row.get("Water treated through STP", ""),
            "Water Treated through WTP (kilo Litres)": safe_row.get("Water treated through WTP", ""),
            "Issues":                                  safe_row.get("Issues", ""),
        }
        display_dict.append(new_row)

    display_df = pd.DataFrame(display_dict)

    # 3. Format the HTML Table
    right_aligned_columns = {
        "Ambient Temperature (°C)", "Grid Units Consumed (kWh)", "Solar Units Consumed (kWh)",
        "Total Units Consumed (kWh)", "Total Cost (INR)", "Solar Cost Savings (INR)",
        "Panels Cleaned", "Diesel Consumed (Litres)",
        "Water Treated through STP (kilo Litres)", "Water Treated through WTP (kilo Litres)"
    }
    decimals_by_column = {
        "Grid Units Consumed (kWh)": 0, "Solar Units Consumed (kWh)": 0, "Total Units Consumed (kWh)": 0,
        "Total Cost (INR)": 2, "Solar Cost Savings (INR)": 2, "Panels Cleaned": 0,
        "Diesel Consumed (Litres)": 0, "Water Treated through STP (kilo Litres)": 0,
        "Water Treated through WTP (kilo Litres)": 0,
    }

    table_parts = [
        '<div style="overflow-x:auto; width:100%; max-width:100%;">',
        '<table style="border-collapse:collapse; width:100%; min-width:1000px; font-family:Arial, Helvetica, sans-serif; font-size:12px; color:#1e293b;">',
        '<thead><tr style="background-color:#1E3A5F; color:#ffffff; font-size:12px;">',
    ]
    for col in display_df.columns:
        align = "right" if col in right_aligned_columns else "left"
        table_parts.append(f'<th style="padding:8px 10px; text-align:{align};">{html_lib.escape(str(col))}</th>')
    table_parts.append('</tr></thead><tbody>')

    for idx, (_, row) in enumerate(display_df.iterrows()):
        bg = "#ffffff" if idx % 2 == 0 else "#f8fafc"
        table_parts.append(f'<tr style="background-color:{bg}; font-size:12px;">')
        for col in display_df.columns:
            value = row.get(col, "")
            if pd.isna(value) or value == "":
                text = "-"
            elif col == "Date":
                text = str(value)
            elif col == "Ambient Temperature (°C)":
                raw_ambient = str(value).strip()
                if raw_ambient in ("", "-"): text = "0"
                else:
                    try: text = _format_en_in(float(raw_ambient.replace(",", "")), 0)
                    except Exception: text = raw_ambient
            elif col == "Issues":
                text = normalizeIssueText(value)
            elif col in decimals_by_column:
                text = _format_en_in(_num(value, 0.0), decimals_by_column[col])
            else:
                text = str(value)

            align = "right" if col in right_aligned_columns else "left"
            num_style = "font-variant-numeric:tabular-nums;" if col in right_aligned_columns else ""
            table_parts.append(
                f'<td style="padding:7px 10px; border-bottom:1px solid #e2e8f0; text-align:{align}; {num_style}">'
                f'{html_lib.escape(text)}</td>'
            )
        table_parts.append('</tr>')

    table_parts.append(
        f'<tr><td colspan="14" style="padding:8px 10px; font-size:11px; color:#94a3b8; text-align:center; '
        f'border-top:1px solid #e2e8f0; background-color:#f8fafc;">'
        f'Showing {len(display_df)} records &nbsp;|&nbsp; Generated by Energy Optimization Agent &nbsp;|&nbsp; '
        f'Noida Campus &nbsp;|&nbsp; Do not reply</td></tr>'
    )
    table_parts.append('</tbody></table></div>')
    table_html = "\n".join(table_parts)

    custom_message_html = f'<tr><td style="padding:0;">{custom_message}</td></tr>' if custom_message else ''

    # 4. Wrap in the Custom Layout
    return f"""
    <html>
        <body style="margin:0; padding:0; background:#f2f3f5; font-family:Segoe UI, Helvetica Neue, Arial, sans-serif; font-size:13px;">
            <table width="100%" cellpadding="0" cellspacing="0" style="padding:18px 0; background:#f2f3f5;">
                <tr>
                    <td align="center">
                        <table width="99%" cellpadding="0" cellspacing="0" style="max-width:1460px; border:1px solid #d9d9d9; background:#ffffff;">
                            <tr>
                                <td style="background:#233f70; color:#ffffff; padding:14px 26px;">
                                    <div style="display:inline-block; vertical-align:middle; font-size:32px; font-weight:700; line-height:1.2;">Energy Consumption Report</div>
                                    <div style="font-size:20px; margin-top:6px; opacity:0.95;">Report Date: {report_date} - Auto-generated by Energy Agent</div>
                                </td>
                            </tr>
                            {custom_message_html}
                            <tr>
                                <td style="padding:18px 24px 8px 24px; color:#223b63; font-weight:700; font-size:20px;">30-Day Data Log</td>
                            </tr>
                            <tr>
                                <td style="padding:0 24px 20px 24px;">
                                    {table_html}
                                </td>
                            </tr>
                            <tr>
                                <td style="background:#f0f0f0; padding:14px 24px; text-align:center; color:#7a7a7a; font-size:13px; border-top:1px solid #dddddd;">Generated by Energy Optimization Agent | Noida Campus | Do not reply</td>
                            </tr>
                        </table>
                    </td>
                </tr>
            </table>
        </body>
    </html>
    """

def _generate_excel_attachment(df: pd.DataFrame) -> bytes:
    """Generates an in-memory Excel file, safely handling mixed date types."""
    output_buffer = io.BytesIO()
    if "Date" in df.columns:
        df = df.copy()
        df["_parsed_date"] = pd.to_datetime(df["Date"], errors="coerce")
        df = df.dropna(subset=["_parsed_date"])
        df_sorted = df.sort_values(by="_parsed_date", ascending=False).drop(columns=["_parsed_date"]).head(30)
    else:
        df_sorted = df.tail(30)
        
    with pd.ExcelWriter(output_buffer, engine='openpyxl') as writer:
        df_sorted.to_excel(writer, sheet_name='Energy_Report', index=False)
        
    output_buffer.seek(0)
    return output_buffer.read()

# ──────────────────────────────────────────────────────────────────────────────
# Mail Dispatchers
# ──────────────────────────────────────────────────────────────────────────────
def send_operator_reminder() -> Dict[str, Any]:
    try:
        from app.services.scheduler_service import load_scheduler_config
        sched_config = load_scheduler_config()
        
        smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
        smtp_port = int(os.getenv("SMTP_PORT", 587))
        email_from = os.getenv("EMAIL_FROM", "suryalogix.renew@gmail.com")
        sender_password = os.getenv("EMAIL_PASSWORD", "")
        
        # 1. Fetch TO and CC strings (Prioritize JSON, fallback to .env)
        # Using 'or' ensures that if the JSON value is an empty string "", it looks at the .env
        operator_email_str = sched_config.get("to") or os.getenv("OPERATOR_EMAIL", "umang.mittal@maqsoftware.com")
        cc_email_str = sched_config.get("cc") or os.getenv("CC_EMAIL", "")
        
        # 2. Convert comma-separated strings into clean lists
        to_list = [e.strip() for e in operator_email_str.split(',') if e.strip()]
        cc_list = [e.strip() for e in cc_email_str.split(',') if e.strip()]
        
        # Combine them for the final SMTP delivery command
        all_recipients = to_list + cc_list
        
        subject = "Action Required: Operator Data Missing"
        body = (
            f"Hello,\n\n"
            f"The Automated Energy Pipeline attempted to run, but no operator data was found for today.\n"
            f"Please update the Grid and Diesel entries in the SharePoint Excel file so the report can be generated.\n\n"
            f"Thank you,\nEnergy Automation Agent"
        )

        msg = MIMEMultipart("alternative")
        msg["From"] = email_from
        
        # 3. Apply the headers so they show up correctly in the email client
        msg["To"] = ", ".join(to_list)
        if cc_list:
            msg["Cc"] = ", ".join(cc_list)
            
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP(smtp_server, smtp_port, timeout=10) as server:
            server.starttls()
            server.login(email_from, sender_password)
            # 4. Send to all recipients (SMTP requires the combined list here)
            server.sendmail(email_from, all_recipients, msg.as_string())
            
        return {"status": "Success", "notes": f"Operator reminder sent to {len(all_recipients)} recipients"}
    except Exception as e:
        return {"status": "Failed", "error": str(e)}