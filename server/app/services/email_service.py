"""
Email service for sending energy reports and notifications.
"""
import io
import os
import re
import json
import smtplib
import logging
import html as html_lib
from email.utils import getaddresses
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
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


def _parse_email_list_from_env(env_value: str) -> list[str]:
    """Parse comma-separated email addresses from environment variable."""
    if not env_value:
        return []
    # Split by comma and strip whitespace, filter empty strings
    return [email.strip() for email in env_value.split(',') if email.strip()]


# Load reminder email lists from environment variables
REMINDER_TO_DISPLAY = _parse_email_list_from_env(
    os.getenv("OPERATOR_EMAIL", "umang.mittal@maqsoftware.com")
)

REMINDER_CC_DISPLAY = _parse_email_list_from_env(
    os.getenv("CC_EMAIL", "Prajwal Yuvraj Khadse | MAQ Software <prajwal.khadse@maqsoftware.com>,Krishna Vatsa | MAQ Software <krishnav@maqsoftware.com>,Ishita Singh | MAQ Software <ishitas@maqsoftware.com>,Umang Mittal | MAQ Software <umang.mittal@maqsoftware.com>")
)


def _emails_from_display(items: list[str]) -> list[str]:
    parsed = getaddresses(items)
    return [email.strip() for _, email in parsed if email and email.strip()]


def _append_scheduler_send_history(entry: Dict[str, Any]) -> None:
    """Append one send-history record to scheduler_log.json without affecting mail flow."""
    try:
        from app.services.scheduler_service import SCHEDULER_LOG_FILE

        SCHEDULER_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        history: list[Dict[str, Any]] = []

        if SCHEDULER_LOG_FILE.exists():
            try:
                with open(SCHEDULER_LOG_FILE, "r", encoding="utf-8") as f:
                    payload = json.load(f)
                if isinstance(payload, list):
                    history = payload
            except Exception:
                history = []

        history.append(entry)
        with open(SCHEDULER_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)
    except Exception as exc:
        logger.warning(f"Failed to append scheduler history entry: {exc}")

def _normalize_col_name(name: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def _find_column(df: pd.DataFrame, candidates: list[str]) -> Optional[str]:
    wanted = {_normalize_col_name(c) for c in candidates}
    for col in df.columns:
        if _normalize_col_name(col) in wanted:
            return col
    return None


def _compute_yesterday_generation_for_email(sp_service: Any, for_date: Optional[Any] = None) -> Optional[float]:
    """
    Compare two UnifiedSolarData values and return the higher one:
    1) for_date's "Yesterday Gen"
    2) (for_date - 1)'s final "Day Generation (kWh)"

    When for_date is None, defaults to IST today.
    """
    try:
        unified_df = sp_service.fetch_sheet_data("unified_solar")
        if unified_df is None or unified_df.empty:
            logger.warning("UnifiedSolarData is empty; cannot compute Yesterday Generation override.")
            return None

        df = unified_df.copy()
        date_col = _find_column(df, ["Date"])
        if not date_col:
            logger.warning("Date column not found in UnifiedSolarData; cannot compute Yesterday Generation override.")
            return None

        ygen_col = _find_column(
            df,
            [
                "Yesterday Gen",
                "Yesterday Generation (kWh)",
                "Yesterday Generation",
                "YesterdayGen",
            ],
        )
        daygen_col = _find_column(
            df,
            ["Day Generation (kWh)", "DayGeneration", "Day Generation"],
        )
        time_col = _find_column(df, ["Time"])

        parsed_date = pd.to_datetime(df[date_col], errors="coerce").dt.date
        parsed_time = pd.to_datetime(df[time_col], errors="coerce") if time_col else pd.Series(pd.NaT, index=df.index)

        if for_date is not None:
            ist_today = pd.to_datetime(for_date).date()
        else:
            ist_today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
        ist_yesterday = ist_today - timedelta(days=1)

        val_today_ygen = 0.0
        if ygen_col:
            today_rows = df[parsed_date == ist_today].copy()
            if not today_rows.empty:
                today_rows["_t"] = parsed_time.loc[today_rows.index]
                today_rows = today_rows.sort_values("_t")
                val_today_ygen = _num(today_rows.iloc[-1].get(ygen_col), 0.0)

        val_yday_final_daygen = 0.0
        if daygen_col:
            yday_rows = df[parsed_date == ist_yesterday].copy()
            if not yday_rows.empty:
                yday_rows["_t"] = parsed_time.loc[yday_rows.index]
                yday_rows = yday_rows.sort_values("_t")
                val_yday_final_daygen = _num(yday_rows.iloc[-1].get(daygen_col), 0.0)

        selected = max(val_today_ygen, val_yday_final_daygen)
        logger.info(
            f"Yesterday Generation compare: today[YGen]={val_today_ygen}, "
            f"yesterday[final DayGen]={val_yday_final_daygen}, selected={selected}"
        )
        return round(selected, 2)
    except Exception as e:
        logger.warning(f"Failed computing Yesterday Generation override: {e}")
        return None


def _coerce_for_column(df: pd.DataFrame, column: str, value: float) -> Any:
    """Return a dtype-compatible value for dataframe column assignment."""
    try:
        dtype = df[column].dtype
        if pd.api.types.is_string_dtype(dtype):
            return str(value)
        return float(value)
    except Exception:
        return str(value)

# ──────────────────────────────────────────────────────────────────────────────
# Core HTML Generator
# ──────────────────────────────────────────────────────────────────────────────
def send_daily_report(trigger_source: str = "scheduler", manual_date: Optional[str] = None, is_missing_data: bool = False) -> Dict[str, Any]:
    subject = ""
    attachment_name: Optional[str] = None
    all_recipients: list[str] = []

    try:
        from app.services.sharepoint_data_service import get_service as get_excel_service
        from app.services.scheduler_service import load_scheduler_config
        from app.core.logger import logger
        
        logger.info(f"Generating Daily Energy Report (Trigger: {trigger_source})...")
        
        sched_config = load_scheduler_config()
        sp_service = get_excel_service()
        master_df = sp_service.fetch_sheet_data("master_data")

        operator_email_preview = str(sched_config.get("to", "")).strip()
        cc_preview = str(sched_config.get("cc", "")).strip()
        all_recipients = [e.strip() for e in f"{operator_email_preview},{cc_preview}".split(",") if e.strip()]
        subject = sched_config.get("subject", "Daily Energy Report - Noida Campus - {date}")

        if not all_recipients:
            _append_scheduler_send_history(
                {
                    "timestamp": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),
                    "status": "Failed",
                    "kind": "daily_report",
                    "trigger_source": trigger_source,
                    "subject": subject,
                    "recipients": "",
                    "attachment": None,
                    "notes": "No recipients configured in scheduler settings",
                }
            )
            return {"status": "Failed", "notes": "No recipients configured in scheduler settings"}
        
        if master_df is None or master_df.empty:
            _append_scheduler_send_history(
                {
                    "timestamp": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),
                    "status": "Failed",
                    "kind": "daily_report",
                    "trigger_source": trigger_source,
                    "subject": subject,
                    "recipients": ", ".join(all_recipients),
                    "attachment": None,
                    "notes": "Master data is empty",
                }
            )
            return {"status": "Failed", "notes": "Master data is empty"}

        today = datetime.now(ZoneInfo("Asia/Kolkata"))
        report_date_title = today.strftime("%Y-%m-%d")

        # --- 1. DETERMINE SINGLE REPORT DATE ---
        if manual_date:
            report_date_title = manual_date
        elif "Date" in master_df.columns:
            parsed_dates = pd.to_datetime(master_df["Date"], errors="coerce")
            today_mask = parsed_dates.dt.date == today.date()
            if today_mask.any():
                report_date_title = str(master_df.loc[today_mask].iloc[-1].get("Date", report_date_title))
            elif today.weekday() == 0:
                sunday_date = (today - timedelta(days=1)).date()
                sunday_mask = parsed_dates.dt.date == sunday_date
                if sunday_mask.any():
                    report_date_title = str(master_df.loc[sunday_mask].iloc[-1].get("Date", report_date_title))
                else:
                    report_date_title = str(master_df.iloc[-1].get("Date", report_date_title))
            else:
                report_date_title = str(master_df.iloc[-1].get("Date", report_date_title))
        else:
            report_date_title = str(master_df.iloc[-1].get("Date", report_date_title))

        # --- 1.5 OVERRIDE YESTERDAY GENERATION FOR EMAIL CONTENT ---
        # Determine which dates the report covers.
        report_dates = [today.date()]
        if today.weekday() == 0:
            # Monday: also cover Sunday (no email was sent on Sunday).
            report_dates = [(today - timedelta(days=1)).date(), today.date()]

        if "Yesterday Generation (kWh)" not in master_df.columns:
            master_df["Yesterday Generation (kWh)"] = ""

        solar_units_source_col = _find_column(
            master_df,
            [
                "Solar Units Consumed(KWh)",
                "Solar Units Consumed (KWh)",
                "Solar Units Consumed (kWh)",
            ],
        )
        if not solar_units_source_col:
            solar_units_source_col = "Solar Units Consumed(KWh)"
            master_df[solar_units_source_col] = ""

        parsed_master_dates = pd.to_datetime(master_df["Date"], errors="coerce").dt.date
        updated_any = False
        for r_date in report_dates:
            selected_gen = _compute_yesterday_generation_for_email(sp_service, for_date=r_date)
            if selected_gen is None:
                continue
            mask = parsed_master_dates == r_date
            if mask.any():
                ygen_value = _coerce_for_column(master_df, "Yesterday Generation (kWh)", selected_gen)
                solar_value = _coerce_for_column(master_df, solar_units_source_col, selected_gen)
                master_df.loc[mask, "Yesterday Generation (kWh)"] = ygen_value
                master_df.loc[mask, solar_units_source_col] = solar_value
                updated_any = True

        # Safety fallback: if no report-date row was found, update the latest row.
        if not updated_any and not master_df.empty:
            fallback_gen = _compute_yesterday_generation_for_email(sp_service)
            if fallback_gen is not None:
                ygen_value = _coerce_for_column(master_df, "Yesterday Generation (kWh)", fallback_gen)
                solar_value = _coerce_for_column(master_df, solar_units_source_col, fallback_gen)
                master_df.loc[master_df.index[-1], "Yesterday Generation (kWh)"] = ygen_value
                master_df.loc[master_df.index[-1], solar_units_source_col] = solar_value

        # --- 2. INJECT WARNING IF DATA IS MISSING ---
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

        # --- 3. INJECT HTML FOR THE SELECTED DATE ---
        try:
            body_html = _build_strict_email_html(master_df, report_date_title, custom_message=warning_msg)
        except Exception as e:
            logger.error(f"HTML generation failed: {e}")
            body_html = f"<p>Report generated for {report_date_title}, but HTML formatting failed.</p>"

        # --- 4. ATTACHMENT AND EMAIL SENDING ---
        try:
            attachment_df = master_df
            if "Date" in master_df.columns:
                target_date = pd.to_datetime(report_date_title, errors="coerce")
                parsed_dates = pd.to_datetime(master_df["Date"], errors="coerce")
                if pd.notna(target_date):
                    day_rows = master_df[parsed_dates.dt.date == target_date.date()]
                    if not day_rows.empty:
                        attachment_df = day_rows

            attachment_bytes = _generate_excel_attachment(attachment_df)
            parsed_attachment_date = pd.to_datetime(report_date_title, errors="coerce")
            attachment_suffix = (
                parsed_attachment_date.strftime("%Y%m%d")
                if pd.notna(parsed_attachment_date)
                else datetime.today().strftime("%Y%m%d")
            )
            attachment_name = f"Energy_Report_{attachment_suffix}.xlsx"
        except Exception as e:
            logger.error(f"Attachment generation failed: {e}")
            attachment_bytes = None
            attachment_name = None

        smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
        smtp_port = int(os.getenv("SMTP_PORT", 587))
        email_from = os.getenv("EMAIL_FROM", "suryalogix.renew@gmail.com")
        sender_password = os.getenv("EMAIL_PASSWORD", "")

        operator_email = str(sched_config.get("to", "")).strip()
        cc_emails_str = str(sched_config.get("cc", "")).strip()
        
        to_list = [e.strip() for e in operator_email.split(',') if e.strip()]
        cc_list = [e.strip() for e in cc_emails_str.split(',') if e.strip()]
        all_recipients = to_list + cc_list
        
        # --- 1. Format the Subject Date to current day (or manual override) ---
        try:
            subject_date_source = manual_date or today.strftime("%Y-%m-%d")
            subject_date_str = pd.to_datetime(subject_date_source).strftime("%B %d, %Y").replace(" 0", " ")
        except Exception:
            subject_date_str = today.strftime("%B %d, %Y").replace(" 0", " ")

        # --- 2. Inject Date and Clean Encoding ---
        raw_subject = sched_config.get("subject", "Daily Energy Report - Noida Campus - {date}")
        base_subject = raw_subject.replace("{date}", subject_date_str).replace("â€”", "-").replace("—", "-")
        
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

        _append_scheduler_send_history(
            {
                "timestamp": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),
                "status": "Success",
                "kind": "daily_report",
                "trigger_source": trigger_source,
                "subject": subject,
                "recipients": ", ".join(all_recipients),
                "attachment": attachment_name,
                "notes": "Daily report email sent",
            }
        )

        return {"status": "Success", "recipients": ", ".join(all_recipients), "attachment": attachment_name}

    except Exception as e:
        from app.core.logger import logger
        logger.error(f"Failed to send daily report: {e}")

        _append_scheduler_send_history(
            {
                "timestamp": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),
                "status": "Failed",
                "kind": "daily_report",
                "trigger_source": trigger_source,
                "subject": subject,
                "recipients": ", ".join(all_recipients),
                "attachment": attachment_name,
                "notes": str(e),
            }
        )
        return {"status": "Failed", "error": str(e)}

def _build_strict_email_html(df: pd.DataFrame, report_date: str, custom_message: str = "") -> str:
    """Builds the exact custom HTML table and layout requested by the user."""
    
    # --- Format the Report Date to 'Month DD, YYYY' ---
    try:
        formatted_date = pd.to_datetime(report_date).strftime("%B %d, %Y").replace(" 0", " ")
    except Exception:
        formatted_date = report_date

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
        master_day = str(safe_row.get("Day", "")).strip()
        display_day = master_day if master_day else true_date.strftime("%A")

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
        f'<tr><td colspan="{len(display_df.columns)}" style="padding:8px 10px; font-size:11px; color:#94a3b8; text-align:center; '
        f'border-top:1px solid #e2e8f0; background-color:#f8fafc;">'
        f'Showing {len(display_df)} records &nbsp;|&nbsp; Generated by Energy Optimization Agent &nbsp;|&nbsp; '
        f'Noida Campus &nbsp;|&nbsp; Do not reply</td></tr>'
    )
    table_parts.append('</tbody></table></div>')
    table_html = "\n".join(table_parts)

    custom_message_html = f'<tr><td style="padding:0;">{custom_message}</td></tr>' if custom_message else ''

    # 4. Wrap in the Custom Layout (Using the newly formatted_date)
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
                                    <div style="font-size:20px; margin-top:6px; opacity:0.95;">Report Date: {formatted_date} - Auto-generated by Energy Agent</div>
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
        smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
        smtp_port = int(os.getenv("SMTP_PORT", 587))
        email_from = os.getenv("EMAIL_FROM", "suryalogix.renew@gmail.com")
        sender_password = os.getenv("EMAIL_PASSWORD", "")

        to_list = _emails_from_display(REMINDER_TO_DISPLAY)
        cc_list = _emails_from_display(REMINDER_CC_DISPLAY)

        all_recipients = to_list + cc_list
        if not all_recipients:
            _append_scheduler_send_history(
                {
                    "timestamp": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),
                    "status": "Failed",
                    "kind": "operator_reminder",
                    "trigger_source": "operator_reminder_cycle",
                    "subject": "Action Required: Operator Data Missing",
                    "recipients": "",
                    "attachment": None,
                    "notes": "Reminder recipients are empty",
                }
            )
            return {"status": "Failed", "error": "Reminder recipients are empty"}

        subject = "Action Required: Operator Data Missing"
        body = (
            f"Hello,\n\n"
            f"The Automated Energy Pipeline attempted to run, but no operator data was found for today.\n"
            f"Please update the Grid and Diesel entries in the SharePoint Excel file so the report can be generated.\n\n"
            f"Thank you,\nEnergy Automation Agent"
        )

        msg = MIMEMultipart("alternative")
        msg["From"] = email_from

        msg["To"] = ", ".join(REMINDER_TO_DISPLAY)
        if cc_list:
            msg["Cc"] = ", ".join(REMINDER_CC_DISPLAY)

        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP(smtp_server, smtp_port, timeout=10) as server:
            server.starttls()
            server.login(email_from, sender_password)
            server.sendmail(email_from, all_recipients, msg.as_string())

        _append_scheduler_send_history(
            {
                "timestamp": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),
                "status": "Success",
                "kind": "operator_reminder",
                "trigger_source": "operator_reminder_cycle",
                "subject": subject,
                "recipients": ", ".join(all_recipients),
                "attachment": None,
                "notes": f"Operator reminder sent to {len(all_recipients)} recipients",
            }
        )

        return {"status": "Success", "notes": f"Operator reminder sent to {len(all_recipients)} recipients"}
    except Exception as e:
        _append_scheduler_send_history(
            {
                "timestamp": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),
                "status": "Failed",
                "kind": "operator_reminder",
                "trigger_source": "operator_reminder_cycle",
                "subject": "Action Required: Operator Data Missing",
                "recipients": ", ".join(_emails_from_display(REMINDER_TO_DISPLAY + REMINDER_CC_DISPLAY)),
                "attachment": None,
                "notes": str(e),
            }
        )
        return {"status": "Failed", "error": str(e)}


