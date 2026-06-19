"""
Email service for sending energy reports and notifications.
Transport: Microsoft Graph API (application permissions, client credentials flow).
"""
import io
import os
import re
import json
import base64
import logging
import html as html_lib
from email.utils import getaddresses
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
import pandas as pd
from zoneinfo import ZoneInfo
import msal
import requests

from app.core.logger import get_logger

logger = get_logger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Microsoft Graph API — Email Transport
# ──────────────────────────────────────────────────────────────────────────────
_graph_token_cache: Dict[str, Any] = {}

def _get_graph_token() -> str:
    """Obtain an access token via client credentials (app-only). Cached in memory."""
    tenant_id     = os.environ["EMAIL_AZURE_TENANT_ID"]
    client_id     = os.environ["EMAIL_AZURE_CLIENT_ID"]
    client_secret = os.environ["EMAIL_AZURE_CLIENT_SECRET"]

    cache_key = f"{tenant_id}:{client_id}"
    cached = _graph_token_cache.get(cache_key)
    if cached:
        import time
        if time.time() < cached["expires_at"] - 60:
            return cached["token"]

    authority = f"https://login.microsoftonline.com/{tenant_id}"
    app = msal.ConfidentialClientApplication(
        client_id,
        authority=authority,
        client_credential=client_secret,
    )
    result = app.acquire_token_for_client(
        scopes=["https://graph.microsoft.com/.default"]
    )
    if "access_token" not in result:
        raise RuntimeError(
            f"Graph token acquisition failed: {result.get('error_description', result)}"
        )

    import time
    _graph_token_cache[cache_key] = {
        "token":      result["access_token"],
        "expires_at": time.time() + result.get("expires_in", 3600),
    }
    return result["access_token"]


def _graph_send(
    *,
    from_address: str,
    to_list: list[str],
    cc_list: list[str],
    subject: str,
    html_body: str,
    plain_body: str = "",
    attachment_bytes: bytes | None = None,
    attachment_name: str | None = None,
) -> None:
    """
    Send an email via POST /users/{from_address}/sendMail using Graph API.

    from_address must be the shared mailbox primary address OR one of its
    verified aliases (alias sending requires SendFromAliasEnabled=True on tenant).
    The app must have Mail.Send application permission and be scoped via
    Application Access Policy to the aiplatform@maqsoftware.com mailbox.
    """
    token = _get_graph_token()
    sender_mailbox = os.getenv("GRAPH_SENDER_MAILBOX", "aiplatform@maqsoftware.com")

    message: Dict[str, Any] = {
        "subject": subject,
        "from": {
            "emailAddress": {"address": from_address}
        },
        "toRecipients": [
            {"emailAddress": {"address": e}} for e in to_list
        ],
        "body": {
            "contentType": "HTML",
            "content": html_body or plain_body,
        },
    }

    if cc_list:
        message["ccRecipients"] = [
            {"emailAddress": {"address": e}} for e in cc_list
        ]

    if attachment_bytes and attachment_name:
        message["attachments"] = [
            {
                "@odata.type":  "#microsoft.graph.fileAttachment",
                "name":         attachment_name,
                "contentType":  "application/octet-stream",
                "contentBytes": base64.b64encode(attachment_bytes).decode(),
            }
        ]

    payload = {"message": message, "saveToSentItems": "true"}

    url = f"https://graph.microsoft.com/v1.0/users/{sender_mailbox}/sendMail"
    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
        },
        json=payload,
        timeout=30,
    )

    if resp.status_code == 202:
        return  # accepted — success

    # Surface a useful error
    try:
        detail = resp.json()
    except Exception:
        detail = resp.text
    raise RuntimeError(
        f"Graph sendMail failed [{resp.status_code}]: {detail}"
    )


# ──────────────────────────────────────────────────────────────────────────────
# Formatting Helpers
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
    if not env_value:
        return []
    return [email.strip() for email in env_value.split(',') if email.strip()]

def _get_reminder_to() -> list[str]:
    return _parse_email_list_from_env(os.getenv("OPERATOR_EMAIL", ""))

def _get_reminder_cc() -> list[str]:
    return _parse_email_list_from_env(os.getenv("CC_EMAIL", ""))

if not _get_reminder_to():
    logging.getLogger("app.services.email_service").warning(
        "OPERATOR_EMAIL env var is not set. Reminder emails will have no recipients."
    )

if not _get_reminder_cc():
    logging.getLogger("app.services.email_service").warning(
        "CC_EMAIL env var is not set. Reminder emails will have no CC recipients."
    )

def _emails_from_display(items: list[str]) -> list[str]:
    parsed = getaddresses(items)
    return [email.strip() for _, email in parsed if email and email.strip()]


def _append_scheduler_send_history(entry: Dict[str, Any]) -> None:
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
        MAX_HISTORY = 500
        history = history[-MAX_HISTORY:]
        with open(SCHEDULER_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)
    except Exception as exc:
        logger.warning(f"Failed to append scheduler history entry: {exc}")


def send_admin_alert(subject: str, error_message: str) -> None:
    admin_email = os.getenv("ADMIN_ALERT_EMAIL")
    if not admin_email:
        logger.warning(
            "send_admin_alert: ADMIN_ALERT_EMAIL env var is not set. "
            f"Skipping admin alert for: {subject}"
        )
        return

    try:
        email_from = os.getenv("EMAIL_FROM", "aiplatform@maqsoftware.com")

        plain_body = (
            f"Energy Dashboard Alert\n\n"
            f"Subject: {subject}\n"
            f"Time: {datetime.now(ZoneInfo('Asia/Kolkata')).isoformat()}\n\n"
            f"Error:\n{error_message}"
        )
        html_body = (
            f"<div style=\"font-family:Arial,sans-serif;max-width:560px;padding:20px;\">"
            f"<div style=\"border-left:4px solid #dc3545;padding:12px 18px;background:#fff5f5;border-radius:4px;\">"
            f"<h2 style=\"color:#c0392b;margin:0 0 6px;font-size:16px;\">[ALERT] Energy Dashboard: {subject}</h2>"
            f"<p style=\"margin:0;font-size:13px;color:#555;\">"
            f"<b>Time:</b> {datetime.now(ZoneInfo('Asia/Kolkata')).isoformat()}</p>"
            f"</div>"
            f"<pre style=\"font-size:13px;color:#333;background:#f9f9f9;padding:14px;border-radius:4px;margin-top:14px;\">"
            f"{html_lib.escape(error_message)}</pre></div>"
        )

        _graph_send(
            from_address=email_from,
            to_list=[admin_email],
            cc_list=[],
            subject=f"[ALERT] Energy Dashboard: {subject}",
            html_body=html_body,
            plain_body=plain_body,
        )
        logger.info(f"Admin alert sent to {admin_email}: {subject}")
    except Exception as alert_exc:
        logger.error(f"Failed to send admin alert: {alert_exc}")


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
            _append_scheduler_send_history({
                "timestamp": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),
                "status": "Failed",
                "kind": "daily_report",
                "trigger_source": trigger_source,
                "subject": subject,
                "recipients": "",
                "attachment": None,
                "notes": "No recipients configured in scheduler settings",
            })
            return {"status": "Failed", "notes": "No recipients configured in scheduler settings"}

        if master_df is None or master_df.empty:
            _append_scheduler_send_history({
                "timestamp": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),
                "status": "Failed",
                "kind": "daily_report",
                "trigger_source": trigger_source,
                "subject": subject,
                "recipients": ", ".join(all_recipients),
                "attachment": None,
                "notes": "Master data is empty",
            })
            return {"status": "Failed", "notes": "Master data is empty"}

        today = datetime.now(ZoneInfo("Asia/Kolkata"))
        report_date_title = today.strftime("%Y-%m-%d")

        # --- DETERMINE REPORT DATE TITLE ---
        if manual_date:
            report_date_title = manual_date
        elif "Date" in master_df.columns:
            parsed_dates = pd.to_datetime(master_df["Date"], errors="coerce", format="mixed")
            today_mask = parsed_dates.dt.date == today.date()
            if today_mask.any():
                today_rows_master = master_df.loc[today_mask]
                if not today_rows_master.empty:
                    report_date_title = str(today_rows_master.iloc[-1].get("Date", report_date_title))
            elif today.weekday() == 0:
                sunday_date = (today - timedelta(days=1)).date()
                sunday_mask = parsed_dates.dt.date == sunday_date
                if sunday_mask.any():
                    sunday_rows = master_df.loc[sunday_mask]
                    if not sunday_rows.empty:
                        report_date_title = str(sunday_rows.iloc[-1].get("Date", report_date_title))
                elif not master_df.empty:
                    report_date_title = str(master_df.iloc[-1].get("Date", report_date_title))
            elif not master_df.empty:
                report_date_title = str(master_df.iloc[-1].get("Date", report_date_title))
        elif not master_df.empty:
            report_date_title = str(master_df.iloc[-1].get("Date", report_date_title))

        # --- WARNING BANNER IF DATA IS MISSING ---
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

        # --- BUILD HTML BODY ---
        try:
            body_html = _build_strict_email_html(master_df, report_date_title, custom_message=warning_msg)
        except Exception as e:
            logger.error(f"HTML generation failed: {e}")
            body_html = f"<p>Report generated for {report_date_title}, but HTML formatting failed.</p>"

        # --- EXCEL ATTACHMENT ---
        try:
            attachment_df = master_df.copy()
            if "Date" in master_df.columns:
                target_date = pd.to_datetime(report_date_title, errors="coerce", format="mixed")
                parsed_dates = pd.to_datetime(master_df["Date"], errors="coerce", format="mixed")
                if pd.notna(target_date):
                    cutoff = target_date - pd.Timedelta(days=30)
                    attachment_df = master_df[parsed_dates.dt.date >= cutoff.date()]

            ATTACHMENT_COLUMNS = [
                "Date", "Day", "Time", "Ambient Temperature °C",
                "Grid Units Consumed (KWh)", "Solar Units Consumed(KWh)",
                "Total Units Consumed (KWh)", "Total Units Consumed in INR",
                "Energy Saving in INR", "Number of Panels Cleaned",
                "Diesel consumed", "Water treated through STP",
                "Water treated through WTP", "Issues",
            ]
            cols_present = [c for c in ATTACHMENT_COLUMNS if c in attachment_df.columns]
            missing = [c for c in ATTACHMENT_COLUMNS if c not in attachment_df.columns]
            if missing:
                logger.warning(f"[ATTACHMENT] Missing columns: {missing}")
            attachment_df = attachment_df[cols_present]

            attachment_bytes = _generate_excel_attachment(attachment_df)
            parsed_attachment_date = pd.to_datetime(report_date_title, errors="coerce", format="mixed")
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

        # --- SEND EMAIL ---
        email_from = os.getenv("EMAIL_FROM", "energyreports@maqsoftware.com")

        operator_email = str(sched_config.get("to", "")).strip()
        cc_emails_str = str(sched_config.get("cc", "")).strip()
        to_list = [e.strip() for e in operator_email.split(',') if e.strip()]
        cc_list = [e.strip() for e in cc_emails_str.split(',') if e.strip()]
        all_recipients = to_list + cc_list

        try:
            subject_date_str = pd.to_datetime(report_date_title, format="mixed").strftime("%B %d, %Y").replace(" 0", " ")
        except Exception:
            subject_date_str = report_date_title

        raw_subject = sched_config.get("subject", "Daily Energy Report - Noida Campus - {date}")
        base_subject = raw_subject.replace("{date}", subject_date_str).replace("\u2014", "-").replace("\u2013", "-")
        subject = f"{subject_prefix}{base_subject}"

        _graph_send(
            from_address=email_from,
            to_list=to_list,
            cc_list=cc_list,
            subject=subject,
            html_body=body_html,
            attachment_bytes=attachment_bytes,
            attachment_name=attachment_name,
        )

        _append_scheduler_send_history({
            "timestamp": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),
            "status": "Success",
            "kind": "daily_report",
            "trigger_source": trigger_source,
            "subject": subject,
            "recipients": ", ".join(all_recipients),
            "attachment": attachment_name,
            "notes": "Daily report email sent",
        })

        logger.warning(f"Daily report sent OK to {len(all_recipients)} recipients (attachment: {attachment_name})")
        return {"status": "Success", "recipients": ", ".join(all_recipients), "attachment": attachment_name}

    except Exception as e:
        from app.core.logger import logger
        logger.error(f"Failed to send daily report: {e}")
        send_admin_alert("Daily report FAILED", str(e))

        _append_scheduler_send_history({
            "timestamp": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),
            "status": "Failed",
            "kind": "daily_report",
            "trigger_source": trigger_source,
            "subject": subject,
            "recipients": ", ".join(all_recipients),
            "attachment": attachment_name,
            "notes": str(e),
        })
        return {"status": "Failed", "error": str(e)}


def _build_strict_email_html(df: pd.DataFrame, report_date: str, custom_message: str = "") -> str:
    try:
        formatted_date = pd.to_datetime(report_date).strftime("%B %d, %Y").replace(" 0", " ")
    except Exception:
        formatted_date = report_date

    df = df.copy()
    if "Date" in df.columns:
        df["_parsed_date"] = pd.to_datetime(df["Date"], errors="coerce", format="mixed")
        df = df.dropna(subset=["_parsed_date"])
        df = df.sort_values(by="_parsed_date", ascending=False).head(30)
    else:
        df = df.tail(30)

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

    right_aligned_columns = {
        "Ambient Temperature (°C)", "Grid Units Consumed (kWh)", "Solar Units Consumed (kWh)",
        "Total Units Consumed (kWh)", "Total Cost (INR)", "Solar Cost Savings (INR)",
        "Panels Cleaned", "Diesel Consumed (Litres)",
        "Water Treated through STP (kilo Litres)", "Water Treated through WTP (kilo Litres)"
    }
    decimals_by_column = {
        "Grid Units Consumed (kWh)": 0, "Solar Units Consumed (kWh)": 0, "Total Units Consumed (kWh)": 0,
        "Total Cost (INR)": 0, "Solar Cost Savings (INR)": 0, "Panels Cleaned": 0,
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
def send_data_correction_alert(errors: list) -> Dict[str, Any]:
    try:
        email_from = os.getenv("EMAIL_FROM", "infraalerts@maqsoftware.com")

        to_list = _emails_from_display(_get_reminder_to())
        cc_list = _emails_from_display(_get_reminder_cc())
        all_recipients = to_list + cc_list

        if not all_recipients:
            logger.error("[DATA ALERT] No OPERATOR_EMAIL set — cannot send correction alert.")
            return {"status": "Failed", "error": "No operator email configured"}

        IST = ZoneInfo("Asia/Kolkata")
        today_str = datetime.now(IST).strftime("%B %d, %Y")

        rows_html = ""
        for e in errors:
            rows_html += f"""
            <tr>
              <td style="padding:8px 12px;border-bottom:1px solid #f0f0f0;font-weight:500;color:#333;">
                {html_lib.escape(str(e['column']))}
              </td>
              <td style="padding:8px 12px;border-bottom:1px solid #f0f0f0;color:#c0392b;font-family:monospace;">
                {html_lib.escape(str(e['value']))}
              </td>
              <td style="padding:8px 12px;border-bottom:1px solid #f0f0f0;color:#555;">
                {html_lib.escape(str(e['error']))}
              </td>
            </tr>"""

        hints_html = """
        <ul style="color:#555;font-size:13px;line-height:1.8;margin:10px 0 0 0;padding-left:18px;">
          <li><b>Date</b> — e.g. <code>05-May-2026</code></li>
          <li><b>Day</b> — full day name, e.g. <code>Tuesday</code></li>
          <li><b>Time</b> — HH:MM format, e.g. <code>10:30</code> &nbsp;(not <code>10-30</code>)</li>
          <li><b>Numeric columns</b> — plain number or comma-separated, e.g. <code>4,452</code> or <code>4452</code></li>
          <li><b>Issues</b> — plain text, e.g. <code>No issues</code></li>
        </ul>"""

        body_html = f"""
        <div style="font-family:Arial,sans-serif;max-width:680px;margin:auto;padding:20px;color:#333;">
          <div style="border-left:5px solid #ffc107;padding:16px 20px;border-radius:4px;margin-bottom:20px;">
            <h2 style="color:#856404;margin:0 0 6px;font-size:17px;">
              Action Required: Invalid Data in Today's Entry
            </h2>
            <p style="margin:0;font-size:14px;">
              The automated pipeline found <b>{len(errors)} error(s)</b> in the
              Grid &amp; Diesel Excel sheet for <b>{today_str}</b>.
              The daily report will <b>not be sent</b> until these are corrected.
            </p>
          </div>

          <table style="width:100%;border-collapse:collapse;font-size:13px;border:1px solid #e0e0e0;border-radius:4px;overflow:hidden;">
            <thead>
              <tr style="background:#f5f5f5;">
                <th style="padding:10px 12px;text-align:left;border-bottom:2px solid #e0e0e0;">Column</th>
                <th style="padding:10px 12px;text-align:left;border-bottom:2px solid #e0e0e0;">Value Entered</th>
                <th style="padding:10px 12px;text-align:left;border-bottom:2px solid #e0e0e0;">Problem</th>
              </tr>
            </thead>
            <tbody>{rows_html}</tbody>
          </table>

          <div style="margin-top:18px;background:#f9f9f9;border:1px solid #e0e0e0;border-radius:4px;padding:14px 18px;">
            <p style="margin:0 0 6px;font-size:13px;font-weight:600;">Expected formats:</p>
            {hints_html}
          </div>

          <p style="margin-top:18px;font-size:13px;color:#888;">
            Please fix the above in the SharePoint Excel file and re-save.
            The pipeline will pick up the corrected data automatically at the next check.
          </p>
        </div>"""

        subject = f"⚠️ Action Required: Fix Excel Data for {today_str} ({len(errors)} error(s))"

        _graph_send(
            from_address=email_from,
            to_list=to_list,
            cc_list=cc_list,
            subject=subject,
            html_body=body_html,
        )

        _append_scheduler_send_history({
            "timestamp":      datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),
            "status":         "Success",
            "kind":           "data_correction_alert",
            "trigger_source": "validation",
            "subject":        subject,
            "recipients":     ", ".join(all_recipients),
            "attachment":     None,
            "notes":          f"{len(errors)} validation error(s) reported",
        })

        logger.info(f"[DATA ALERT] Correction email sent to {all_recipients}")
        return {"status": "Success", "errors_reported": len(errors)}

    except Exception as e:
        logger.error(f"[DATA ALERT] Failed to send correction email: {e}")
        return {"status": "Failed", "error": str(e)}

def send_operator_reminder(
    reminder_number: int = 1,
    total_reminders: int = 3,
    deadline_str: str = "10:30 AM",
) -> Dict[str, Any]:
    try:
        email_from = os.getenv("EMAIL_FROM", "energyreports@maqsoftware.com")

        to_list = _emails_from_display(_get_reminder_to())
        cc_list = _emails_from_display(_get_reminder_cc())
        all_recipients = to_list + cc_list

        if not all_recipients:
            _append_scheduler_send_history({
                "timestamp": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),
                "status": "Failed",
                "kind": "operator_reminder",
                "trigger_source": "operator_reminder_cycle",
                "subject": f"Action Required: Operator Data Missing (Reminder {reminder_number}/{total_reminders})",
                "recipients": "",
                "attachment": None,
                "notes": "Reminder recipients are empty",
            })
            return {"status": "Failed", "error": "Reminder recipients are empty"}

        reminder_tag = f"Reminder {reminder_number}/{total_reminders}"
        subject = f"Action Required: Operator Data Missing ({reminder_tag})"

        # FIX 1: Cleaned up the redundant "reminder Reminder 1/3" phrasing
        border_color, bg_color, heading_color = "#FFC000", "#fffbeb", "#92400e"

        if reminder_number == total_reminders:
            urgency_note = ""
        elif reminder_number == 2:
            urgency_note = ""
        else:
            urgency_note = ""

        now_str = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%d %b %Y, %I:%M %p IST")

        fallback_note = (
            f"Note: This is the last reminder. At {deadline_str} the report will be sent automatically "
            f"using yesterday's data if today's entries are still missing."
            if reminder_number == total_reminders
            else
            f"Note: If data is not updated before the {deadline_str} deadline, the report will be sent "
            f"automatically using yesterday's data as a fallback."
        )

        # FIX 2: Added structural formatting and clear spacing for the plain text fallback
        plain_body = (
            f"[{reminder_tag}] ACTION REQUIRED: Operator Data Missing\n"
            f"-" * 50 + "\n\n"
            f"The Automated Energy Pipeline checked at {now_str} but no operator data was found for today.\n\n"
            f"Please update the Grid and Diesel entries in the SharePoint Excel file.\n\n"
            f"{urgency_note}\n\n"
            f"{fallback_note}\n\n"
            f"Thank you,\n"
            f"Energy Automation Agent"
        )

        # FIX 3: Created an HTML body utilizing the severity colors you already defined
        html_body = f"""
        <div style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; max-width: 600px; margin: 0 auto; border: 1px solid {border_color}; border-radius: 8px; overflow: hidden; color: #333;">
            <div style="background-color: {bg_color}; padding: 20px; border-bottom: 1px solid {border_color};">
                <h2 style="color: {heading_color}; margin: 0; font-size: 20px;">Action Required: Operator Data Missing</h2>
                <p style="color: {heading_color}; margin: 5px 0 0 0; font-size: 14px; font-weight: bold;">{reminder_tag}</p>
            </div>
            <div style="padding: 20px; background-color: #ffffff; line-height: 1.6;">
                <p style="margin-top: 0;">The Automated Energy Pipeline checked at <strong>{now_str}</strong>, but no operator data was found for today.</p>
                <p style="font-size: 16px;"><strong>Please update the Grid and Diesel entries in the SharePoint Excel file.</strong></p>
                <p style="color: {heading_color}; font-weight: 500;">{urgency_note}</p>
                
                <div style="background-color: #f8f9fa; border-left: 4px solid #adb5bd; padding: 12px; margin: 25px 0 15px 0; font-size: 13px; color: #555;">
                    <em>{fallback_note}</em>
                </div>
                
                <p style="margin-bottom: 0;">Thank you,<br/><span style="font-weight: 600; color: #555;">Energy Automation Agent</span></p>
            </div>
        </div>
        """

        # FIX 4: Passed the new html_body into your mail sender
        _graph_send(
            from_address=email_from,
            to_list=to_list,
            cc_list=cc_list,
            subject=subject,
            html_body=html_body, 
            plain_body=plain_body,
        )

        _append_scheduler_send_history({
            "timestamp": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),
            "status": "Success",
            "kind": "operator_reminder",
            "trigger_source": "operator_reminder_cycle",
            "subject": subject,
            "recipients": ", ".join(all_recipients),
            "attachment": None,
            "notes": f"{reminder_tag} sent to {len(all_recipients)} recipients",
        })

        return {"status": "Success", "notes": f"{reminder_tag} sent to {len(all_recipients)} recipients"}

    except Exception as e:
        _append_scheduler_send_history({
            "timestamp": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),
            "status": "Failed",
            "kind": "operator_reminder",
            "trigger_source": "operator_reminder_cycle",
            "subject": f"Action Required: Operator Data Missing (Reminder {reminder_number}/{total_reminders})",
            "recipients": ", ".join(_emails_from_display(_get_reminder_to() + _get_reminder_cc())),
            "attachment": None,
            "notes": str(e),
        })
        return {"status": "Failed", "error": str(e)}
# ──────────────────────────────────────────────────────────────────────────────
# Inverter Fault Alert
# ──────────────────────────────────────────────────────────────────────────────
def send_inverter_alert(
    faulted: list,
    today_data: dict,
    date_str: str,
) -> Dict[str, Any]:
    """
    Sends an HTML alert email when one or more inverters are in FAULT or INACTIVE
    state during a 30-minute monitor tick.

    INACTIVE = SMB has DC power but the inverter's AC output was 0 kW
               (new formula applied by scrape_to_sharepoint.py).
    FAULT    = suryalog_status code indicated a device fault.

    Recipients: same to/cc list used by the daily report and operator reminder
    (REMINDER_TO / REMINDER_CC env vars, or scheduler_config.json).
    """
    try:
        email_from = os.getenv("EMAIL_FROM", "infraalerts@maqsoftware.com")

        to_list = _emails_from_display(_get_reminder_to())
        cc_list = _emails_from_display(_get_reminder_cc())
        all_recipients = to_list + cc_list

        if not all_recipients:
            logger.error("[INVERTER ALERT] No recipients configured — skipping alert email.")
            return {"status": "Failed", "error": "No recipients configured"}

        IST = ZoneInfo("Asia/Kolkata")
        now_ist   = datetime.now(IST)
        time_str  = now_ist.strftime("%I:%M %p IST")
        date_disp = datetime.strptime(date_str, "%Y-%m-%d").strftime("%B %d, %Y")

        # Build one table row per faulted inverter
        rows_html = ""
        for inv in faulted:
            entry      = today_data.get(inv, {})
            up_mins    = int(entry.get("uptime_mins",   0))
            dn_mins    = int(entry.get("downtime_mins", 0))
            total_mins = up_mins + dn_mins
            uptime_pct = round(up_mins / total_mins * 100, 1) if total_mins > 0 else 0.0

            # Colour-code INACTIVE vs FAULT
            badge_bg    = "#fff3cd"
            badge_fg    = "#856404"
            badge_label = "INACTIVE"
            if dn_mins > 0 and up_mins == 0:
                # Only downtime recorded — likely a hard FAULT from the start of day
                badge_bg    = "#f8d7da"
                badge_fg    = "#721c24"
                badge_label = "FAULT"

            rows_html += f"""
            <tr>
              <td style="padding:10px 14px;border-bottom:1px solid #f0f0f0;font-weight:600;color:#333;">{html_lib.escape(inv)}</td>
              <td style="padding:10px 14px;border-bottom:1px solid #f0f0f0;">
                <span style="background:{badge_bg};color:{badge_fg};padding:3px 10px;border-radius:12px;font-size:12px;font-weight:600;">{badge_label}</span>
              </td>
              <td style="padding:10px 14px;border-bottom:1px solid #f0f0f0;color:#c0392b;font-weight:500;">{dn_mins} min</td>
              <td style="padding:10px 14px;border-bottom:1px solid #f0f0f0;color:#27ae60;">{up_mins} min</td>
              <td style="padding:10px 14px;border-bottom:1px solid #f0f0f0;color:#555;">{uptime_pct}%</td>
            </tr>"""

        # Healthy inverters summary
        healthy_rows = ""
        from app.services.inverter_monitor import INVERTERS as _ALL_INVERTERS
        for inv in _ALL_INVERTERS:
            if inv in faulted:
                continue
            entry      = today_data.get(inv, {})
            up_mins    = int(entry.get("uptime_mins",   0))
            dn_mins    = int(entry.get("downtime_mins", 0))
            total_mins = up_mins + dn_mins
            uptime_pct = round(up_mins / total_mins * 100, 1) if total_mins > 0 else 0.0
            healthy_rows += f"""
            <tr>
              <td style="padding:8px 14px;border-bottom:1px solid #f5f5f5;color:#555;">{html_lib.escape(inv)}</td>
              <td style="padding:8px 14px;border-bottom:1px solid #f5f5f5;">
                <span style="background:#d4edda;color:#155724;padding:3px 10px;border-radius:12px;font-size:12px;font-weight:600;">ACTIVE</span>
              </td>
              <td style="padding:8px 14px;border-bottom:1px solid #f5f5f5;color:#c0392b;">{dn_mins} min</td>
              <td style="padding:8px 14px;border-bottom:1px solid #f5f5f5;color:#27ae60;">{up_mins} min</td>
              <td style="padding:8px 14px;border-bottom:1px solid #f5f5f5;color:#555;">{uptime_pct}%</td>
            </tr>"""

        fault_count  = len(faulted)
        plural       = "inverter" if fault_count == 1 else "inverters"
        faulted_list = ", ".join(faulted)

        healthy_section = ""
        if healthy_rows:
            healthy_section = f"""
          <h3 style="font-size:14px;font-weight:600;color:#27ae60;margin:0 0 8px;">Healthy inverters</h3>
          <table style="width:100%;border-collapse:collapse;font-size:13px;border:1px solid #e0e0e0;border-radius:4px;overflow:hidden;margin-bottom:20px;">
            <thead>
              <tr style="background:#d4edda;">
                <th style="padding:8px 14px;text-align:left;border-bottom:2px solid #c3e6cb;">Inverter</th>
                <th style="padding:8px 14px;text-align:left;border-bottom:2px solid #c3e6cb;">Status</th>
                <th style="padding:8px 14px;text-align:left;border-bottom:2px solid #c3e6cb;">Downtime today</th>
                <th style="padding:8px 14px;text-align:left;border-bottom:2px solid #c3e6cb;">Uptime today</th>
                <th style="padding:8px 14px;text-align:left;border-bottom:2px solid #c3e6cb;">Uptime %</th>
              </tr>
            </thead>
            <tbody>{healthy_rows}</tbody>
          </table>"""

        body_html = f"""
        <div style="font-family:Arial,sans-serif;max-width:700px;margin:auto;padding:20px;color:#333;">

          <div style="border-left:5px solid #dc3545;padding:16px 20px;border-radius:4px;background:#fff5f5;margin-bottom:20px;">
            <h2 style="color:#c0392b;margin:0 0 6px;font-size:18px;">
              &#9888;&#65039; Inverter Fault Detected
            </h2>
            <p style="margin:0;font-size:14px;color:#555;">
              <b>{fault_count} {plural}</b> reported a fault at <b>{time_str}</b> on <b>{date_disp}</b>.<br>
              Affected: <b>{html_lib.escape(faulted_list)}</b>
            </p>
            <p style="margin:8px 0 0;font-size:12px;color:#888;">
              <b>INACTIVE</b> = SMB has DC input but inverter AC output was 0 kW (possible failure or disconnection).<br>
              <b>FAULT</b> = Device status code reported a fault condition from SuryaLogix.
            </p>
          </div>

          <h3 style="font-size:14px;font-weight:600;color:#c0392b;margin:0 0 8px;">Faulted inverters</h3>
          <table style="width:100%;border-collapse:collapse;font-size:13px;border:1px solid #e0e0e0;border-radius:4px;overflow:hidden;margin-bottom:20px;">
            <thead>
              <tr style="background:#f8d7da;">
                <th style="padding:10px 14px;text-align:left;border-bottom:2px solid #f5c6cb;">Inverter</th>
                <th style="padding:10px 14px;text-align:left;border-bottom:2px solid #f5c6cb;">Status</th>
                <th style="padding:10px 14px;text-align:left;border-bottom:2px solid #f5c6cb;">Downtime today</th>
                <th style="padding:10px 14px;text-align:left;border-bottom:2px solid #f5c6cb;">Uptime today</th>
                <th style="padding:10px 14px;text-align:left;border-bottom:2px solid #f5c6cb;">Uptime %</th>
              </tr>
            </thead>
            <tbody>{rows_html}</tbody>
          </table>

          {healthy_section}

          <p style="font-size:12px;color:#999;margin-top:18px;">
            This alert fires automatically every 30 minutes when a fault is detected.<br>
            Please inspect the affected inverter(s) and check the SuryaLogix portal for details.
          </p>
        </div>"""

        subject = (
            f"\u26a0\ufe0f Inverter Fault Alert \u2014 "
            f"{fault_count} {plural} on {date_disp} at {time_str}"
        )

        _graph_send(
            from_address=email_from,
            to_list=to_list,
            cc_list=cc_list,
            subject=subject,
            html_body=body_html,
        )

        _append_scheduler_send_history({
            "timestamp":      now_ist.isoformat(),
            "status":         "Success",
            "kind":           "inverter_fault_alert",
            "trigger_source": "inverter_monitor",
            "subject":        subject,
            "recipients":     ", ".join(all_recipients),
            "attachment":     None,
            "notes":          f"Faulted inverters: {faulted_list}",
        })

        logger.info(f"[INVERTER ALERT] Alert sent to {all_recipients} — {faulted_list}")
        return {"status": "Success", "faulted": faulted, "recipients": all_recipients}

    except Exception as e:
        logger.error(f"[INVERTER ALERT] Failed to send alert email: {e}")
        _append_scheduler_send_history({
            "timestamp":      datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),
            "status":         "Failed",
            "kind":           "inverter_fault_alert",
            "trigger_source": "inverter_monitor",
            "subject":        f"Inverter fault alert — {', '.join(faulted)}",
            "recipients":     ", ".join(_emails_from_display(_get_reminder_to() + _get_reminder_cc())),
            "attachment":     None,
            "notes":          str(e),
        })
        return {"status": "Failed", "error": str(e)}
    

# ──────────────────────────────────────────────────────────────────────────────
# Daily Temperature Optimization Email
# ──────────────────────────────────────────────────────────────────────────────
def send_temperature_optimization_email(trigger_source: str = "scheduler") -> Dict[str, Any]:
    """
    Send the daily optimal indoor temperature recommendation email.
    Uses the ASHRAE 55 model from temperature_service and the existing Graph transport.
    """
    try:
        from app.services.temperature_service import get_temperature_recommendation

        rec = get_temperature_recommendation()
        if rec is None:
            logger.error("[TEMP EMAIL] Weather data unavailable — skipping temperature email.")
            return {"status": "Failed", "error": "Weather data unavailable"}

        email_from = os.getenv("EMAIL_FROM", "energyreports@maqsoftware.com")
        to_list    = _emails_from_display(_get_reminder_to())
        cc_list    = _emails_from_display(_get_reminder_cc())
        all_recipients = to_list + cc_list

        if not all_recipients:
            return {"status": "Failed", "error": "No recipients configured (OPERATOR_EMAIL)"}

        IST   = ZoneInfo("Asia/Kolkata")
        today = datetime.now(IST).strftime("%B %d, %Y")

        html_body = _build_temperature_email_html(rec, today)
        subject   = f"🌡️ Daily HVAC Optimisation Brief — Noida Campus — {today}"

        _graph_send(
            from_address=email_from,
            to_list=to_list,
            cc_list=cc_list,
            subject=subject,
            html_body=html_body,
        )

        _append_scheduler_send_history({
            "timestamp":      datetime.now(IST).isoformat(),
            "status":         "Success",
            "kind":           "temperature_optimization",
            "trigger_source": trigger_source,
            "subject":        subject,
            "recipients":     ", ".join(all_recipients),
            "attachment":     None,
            "notes":          f"Setpoint={rec['target_setpoint']}°C | Savings={rec['estimated_savings_pct']}%",
        })

        logger.info(f"[TEMP EMAIL] Sent to {len(all_recipients)} recipients. Setpoint={rec['target_setpoint']}°C")
        return {"status": "Success", "recipients": ", ".join(all_recipients)}

    except Exception as e:
        logger.error(f"[TEMP EMAIL] Failed: {e}")
        _append_scheduler_send_history({
            "timestamp":      datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),
            "status":         "Failed",
            "kind":           "temperature_optimization",
            "trigger_source": trigger_source,
            "subject":        "Daily HVAC Optimisation Brief",
            "recipients":     "",
            "attachment":     None,
            "notes":          str(e),
        })
        return {"status": "Failed", "error": str(e)}


def _build_temperature_email_html(rec: dict, date_str: str) -> str:
    """Build the professional HTML email for the temperature optimization brief."""

    def _score_bar(score: int, color: str) -> str:
        return (
            f'<div style="background:#e5e7eb;border-radius:999px;height:8px;width:100%;overflow:hidden;">'
            f'<div style="background:{color};width:{score}%;height:100%;border-radius:999px;"></div>'
            f'</div>'
        )

    modulations_html = ""
    for label, delta in rec.get("modulations", {}).items():
        sign  = "▲" if delta > 0 else "▼"
        color = "#16a34a" if delta > 0 else "#dc2626"
        modulations_html += (
            f'<tr>'
            f'<td style="padding:5px 10px;font-size:12px;color:#374151;">{html_lib.escape(label)}</td>'
            f'<td style="padding:5px 10px;font-size:12px;font-weight:700;color:{color};text-align:right;">'
            f'{sign} {abs(delta):.1f}°C</td>'
            f'</tr>'
        )

    insights_html = ""
    for insight in rec.get("insights", []):
        insights_html += (
            f'<li style="margin-bottom:8px;color:#374151;font-size:13px;line-height:1.5;">'
            f'{html_lib.escape(insight)}</li>'
        )

    recs_html = ""
    for r in rec.get("recommendations", []):
        recs_html += (
            f'<li style="margin-bottom:6px;color:#374151;font-size:13px;line-height:1.5;">'
            f'✅ {html_lib.escape(r)}</li>'
        )

    cost = rec.get("cost_estimate", {})
    hvac_mode_label = {
        "DEHUMIDIFICATION_PRIORITY": "🌧️ Dehumidification Priority",
        "MAX_ECO_EFFICIENCY":        "♻️ Max Eco Efficiency",
        "STANDARD_AUTOMATION":       "⚙️ Standard Automation",
    }.get(rec.get("hvac_mode", ""), rec.get("hvac_mode", ""))

    bounded_note = ""
    if rec.get("bounded_by_guardrails"):
        bounded_note = (
            f'<p style="font-size:11px;color:#9ca3af;margin:4px 0 0;">'
            f'⚠️ Guardrail applied: {html_lib.escape(rec.get("bound_reason",""))}</p>'
        )

    return f"""
    <html>
    <body style="margin:0;padding:0;background:#f3f4f6;font-family:'Segoe UI',Helvetica,Arial,sans-serif;">
    <table width="100%" cellpadding="0" cellspacing="0" style="padding:24px 0;background:#f3f4f6;">
      <tr><td align="center">
        <table width="100%" cellpadding="0" cellspacing="0" style="max-width:620px;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.08);">

          <!-- HEADER -->
          <tr>
            <td style="background:linear-gradient(135deg,#1e3a5f 0%,#1d4ed8 100%);padding:28px 32px;">
              <p style="margin:0;font-size:11px;letter-spacing:2px;color:#93c5fd;text-transform:uppercase;">Energy Optimization Agent</p>
              <h1 style="margin:6px 0 0;font-size:22px;font-weight:700;color:#ffffff;">Daily HVAC Optimisation Brief</h1>
              <p style="margin:6px 0 0;font-size:13px;color:#bfdbfe;">Noida Campus — Sector 145 &nbsp;|&nbsp; {html_lib.escape(date_str)}</p>
            </td>
          </tr>

          <!-- SETPOINT HERO -->
          <tr>
            <td style="padding:28px 32px 0;">
              <table width="100%" cellpadding="0" cellspacing="0"
                style="background:linear-gradient(135deg,#eff6ff,#dbeafe);border:1px solid #bfdbfe;border-radius:10px;overflow:hidden;">
                <tr>
                  <td style="padding:20px 24px;">
                    <p style="margin:0;font-size:11px;letter-spacing:1.5px;color:#1d4ed8;text-transform:uppercase;">Recommended HVAC Setpoint</p>
                    <p style="margin:6px 0;font-size:42px;font-weight:800;color:#1e3a5f;letter-spacing:-1px;">{rec['target_setpoint']}°C</p>
                    <p style="margin:0;font-size:13px;color:#3b82f6;">Comfort band: {rec['setpoint_range']}</p>
                    {bounded_note}
                  </td>
                  <td style="padding:20px 24px;text-align:right;">
                    <p style="margin:0;font-size:11px;color:#6b7280;">Operational Mode</p>
                    <p style="margin:4px 0 0;font-size:15px;font-weight:600;color:#1e3a5f;">{html_lib.escape(hvac_mode_label)}</p>
                    <p style="margin:8px 0 0;font-size:11px;color:#6b7280;">Est. Energy Saving</p>
                    <p style="margin:2px 0 0;font-size:22px;font-weight:700;color:#16a34a;">{rec['estimated_savings_pct']}%</p>
                    <p style="margin:0;font-size:11px;color:#9ca3af;">vs static 22°C</p>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- SCORES -->
          <tr>
            <td style="padding:20px 32px 0;">
              <table width="100%" cellpadding="0" cellspacing="0">
                <tr>
                  <td width="48%" style="padding-right:8px;">
                    <div style="border:1px solid #e5e7eb;border-radius:8px;padding:14px;">
                      <p style="margin:0 0 4px;font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:1px;">Comfort Score</p>
                      <p style="margin:0 0 8px;font-size:26px;font-weight:700;color:#1e3a5f;">{rec['comfort_score']}<span style="font-size:14px;color:#9ca3af;">/100</span></p>
                      {_score_bar(rec['comfort_score'], '#3b82f6')}
                    </div>
                  </td>
                  <td width="4%"></td>
                  <td width="48%" style="padding-left:8px;">
                    <div style="border:1px solid #e5e7eb;border-radius:8px;padding:14px;">
                      <p style="margin:0 0 4px;font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:1px;">Energy Efficiency</p>
                      <p style="margin:0 0 8px;font-size:26px;font-weight:700;color:#1e3a5f;">{rec['energy_efficiency_score']}<span style="font-size:14px;color:#9ca3af;">/100</span></p>
                      {_score_bar(rec['energy_efficiency_score'], '#16a34a')}
                    </div>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- WEATHER SNAPSHOT -->
          <tr>
            <td style="padding:20px 32px 0;">
              <p style="margin:0 0 10px;font-size:13px;font-weight:600;color:#1e3a5f;">🌤️ Live Weather Inputs</p>
              <table width="100%" cellpadding="0" cellspacing="0" style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;overflow:hidden;">
                <tr>
                  <td style="padding:10px 14px;font-size:12px;color:#6b7280;border-bottom:1px solid #e5e7eb;">Temperature</td>
                  <td style="padding:10px 14px;font-size:12px;font-weight:600;color:#111827;text-align:right;border-bottom:1px solid #e5e7eb;">{rec['outdoor_temperature']}°C (feels like {rec['feels_like']}°C)</td>
                </tr>
                <tr>
                  <td style="padding:10px 14px;font-size:12px;color:#6b7280;border-bottom:1px solid #e5e7eb;">Humidity</td>
                  <td style="padding:10px 14px;font-size:12px;font-weight:600;color:#111827;text-align:right;border-bottom:1px solid #e5e7eb;">{rec['humidity']}%</td>
                </tr>
                <tr>
                  <td style="padding:10px 14px;font-size:12px;color:#6b7280;border-bottom:1px solid #e5e7eb;">Cloud Cover</td>
                  <td style="padding:10px 14px;font-size:12px;font-weight:600;color:#111827;text-align:right;border-bottom:1px solid #e5e7eb;">{rec['cloud_cover']}%</td>
                </tr>
                <tr>
                  <td style="padding:10px 14px;font-size:12px;color:#6b7280;">Wind Speed</td>
                  <td style="padding:10px 14px;font-size:12px;font-weight:600;color:#111827;text-align:right;">{rec['wind_speed']} m/s — {html_lib.escape(rec['weather_condition'])}</td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- MODULATIONS -->
          {"" if not rec.get("modulations") else f'''
          <tr>
            <td style="padding:20px 32px 0;">
              <p style="margin:0 0 10px;font-size:13px;font-weight:600;color:#1e3a5f;">🔍 Algorithm Influence Log</p>
              <table width="100%" cellpadding="0" cellspacing="0" style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;overflow:hidden;">
                <tr style="background:#f3f4f6;">
                  <th style="padding:8px 10px;font-size:11px;color:#6b7280;text-align:left;font-weight:600;">Factor</th>
                  <th style="padding:8px 10px;font-size:11px;color:#6b7280;text-align:right;font-weight:600;">Setpoint Shift</th>
                </tr>
                {modulations_html}
              </table>
            </td>
          </tr>'''}

          <!-- INSIGHTS -->
          <tr>
            <td style="padding:20px 32px 0;">
              <p style="margin:0 0 10px;font-size:13px;font-weight:600;color:#1e3a5f;">💡 Energy Saving Insights</p>
              <ul style="margin:0;padding-left:18px;">{insights_html}</ul>
            </td>
          </tr>

          <!-- RECOMMENDATIONS -->
          <tr>
            <td style="padding:20px 32px 0;">
              <p style="margin:0 0 10px;font-size:13px;font-weight:600;color:#1e3a5f;">📋 Recommended Actions</p>
              <ul style="margin:0;padding-left:18px;">{recs_html}</ul>
            </td>
          </tr>

          <!-- COST ESTIMATE -->
          <tr>
            <td style="padding:20px 32px 0;">
              <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:16px 20px;">
                <p style="margin:0 0 8px;font-size:13px;font-weight:600;color:#166534;">💰 Cost Saving Estimate</p>
                <table width="100%" cellpadding="0" cellspacing="0">
                  <tr>
                    <td style="font-size:12px;color:#374151;padding:3px 0;">Per AC unit (1.5T, 8h/day)</td>
                    <td style="font-size:12px;font-weight:700;color:#15803d;text-align:right;padding:3px 0;">₹{cost.get('cost_saved_inr_per_ac','—')}/day</td>
                  </tr>
                  <tr>
                    <td style="font-size:12px;color:#374151;padding:3px 0;">Campus estimate (~50 units)</td>
                    <td style="font-size:16px;font-weight:800;color:#15803d;text-align:right;padding:3px 0;">₹{cost.get('campus_saving_inr_per_day','—')}/day</td>
                  </tr>
                </table>
                <p style="margin:8px 0 0;font-size:11px;color:#9ca3af;">{html_lib.escape(str(cost.get('basis','')))}</p>
              </div>
            </td>
          </tr>

          <!-- FOOTER -->
          <tr>
            <td style="padding:24px 32px;border-top:1px solid #e5e7eb;margin-top:20px;">
              <p style="margin:0;font-size:11px;color:#9ca3af;text-align:center;">
                Automated HVAC Optimisation Report &nbsp;|&nbsp; Energy Optimization Agent &nbsp;|&nbsp;
                Noida Campus &nbsp;|&nbsp; Do not reply
              </p>
            </td>
          </tr>

        </table>
      </td></tr>
    </table>
    </body>
    </html>
    """
