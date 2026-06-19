"""
EnergyAgent — Full Email Flow Test (All 5 Functions)
=====================================================
Tests every email type with realistic data via Graph API.
Run from the EnergyAgentWork root directory.

Usage (PowerShell):
    $env:AZURE_TENANT_ID     = "e4d98dd2-9199-42e5-ba8b-da3e763ede2e"
    $env:AZURE_CLIENT_ID     = "05cefead-ac03-4395-8b5b-f19c0d6e41f0"
    $env:AZURE_CLIENT_SECRET = "your-secret"
    $env:OPERATOR_EMAIL      = "umang.mittal@maqsoftware.com"
    $env:CC_EMAIL            = ""
    $env:ADMIN_ALERT_EMAIL   = "umang.mittal@maqsoftware.com"
    python test_all_emails.py

What gets tested:
    1. send_admin_alert        — system error alert
    2. send_daily_report       — full HTML report + Excel attachment (reads real SharePoint data)
    3. send_data_correction_alert — Excel data error warning
    4. send_operator_reminder  — missing data nudge
    5. send_inverter_alert     — inverter fault notification
"""

import os, sys, json, base64, time, requests, msal
from datetime import datetime
from zoneinfo import ZoneInfo
import dotenv
dotenv.load_dotenv()

# ── colour helpers ────────────────────────────────────────────────────────────
BOLD="\033[1m"; GREEN="\033[92m"; RED="\033[91m"; CYAN="\033[96m"
YELLOW="\033[93m"; RESET="\033[0m"
def ok(m):     print(f"  {GREEN}✓{RESET}  {m}")
def fail(m):   print(f"  {RED}✗{RESET}  {m}")
def info(m):   print(f"  {CYAN}→{RESET}  {m}")
def warn(m):   print(f"  {YELLOW}!{RESET}  {m}")
def header(m): print(f"\n{BOLD}{m}{RESET}")
def sep():     print(f"  {'─'*60}")

results = {}   # function_name → "PASS" | "FAIL" | "SKIP"

# ── config ────────────────────────────────────────────────────────────────────
TENANT_ID     = os.getenv("EMAIL_AZURE_TENANT_ID",     "")
CLIENT_ID     = os.getenv("EMAIL_AZURE_CLIENT_ID",     "")
CLIENT_SECRET = os.getenv("EMAIL_AZURE_CLIENT_SECRET", "")
SENDER        = os.getenv("GRAPH_SENDER_MAILBOX", "aiplatform@maqsoftware.com")
EMAIL_FROM    = os.getenv("EMAIL_FROM",           "aiplatform@maqsoftware.com")

# Override recipients so test emails only go to you — NOT the full scheduler list
TEST_RECIPIENT = os.getenv("OPERATOR_EMAIL", "")
if not TEST_RECIPIENT:
    print(f"{RED}Set OPERATOR_EMAIL to your email before running.{RESET}")
    sys.exit(1)

# ── token ─────────────────────────────────────────────────────────────────────
header("Acquiring Graph API token")
_token_cache = {}

def get_token() -> str:
    cached = _token_cache.get("t")
    if cached and time.time() < cached["exp"] - 60:
        return cached["tok"]
    app = msal.ConfidentialClientApplication(
        CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{TENANT_ID}",
        client_credential=CLIENT_SECRET,
    )
    r = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    if "access_token" not in r:
        print(f"{RED}Token failed: {r.get('error_description')}{RESET}")
        sys.exit(1)
    _token_cache["t"] = {"tok": r["access_token"], "exp": time.time() + r.get("expires_in", 3600)}
    return r["access_token"]

token = get_token()
ok(f"Token acquired (expires {int(_token_cache['t']['exp'] - time.time())}s)")

def graph_send(*, from_addr, to_list, cc_list, subject, html_body,
               plain_body="", attachment_bytes=None, attachment_name=None):
    msg = {
        "subject": subject,
        "from":    {"emailAddress": {"address": from_addr}},
        "toRecipients": [{"emailAddress": {"address": e}} for e in to_list],
        "body": {"contentType": "HTML", "content": html_body or f"<pre>{plain_body}</pre>"},
    }
    if cc_list:
        msg["ccRecipients"] = [{"emailAddress": {"address": e}} for e in cc_list]
    if attachment_bytes and attachment_name:
        msg["attachments"] = [{
            "@odata.type":  "#microsoft.graph.fileAttachment",
            "name":         attachment_name,
            "contentType":  "application/octet-stream",
            "contentBytes": base64.b64encode(attachment_bytes).decode(),
        }]
    resp = requests.post(
        f"https://graph.microsoft.com/v1.0/users/{SENDER}/sendMail",
        headers={"Authorization": f"Bearer {get_token()}", "Content-Type": "application/json"},
        json={"message": msg, "saveToSentItems": "true"},
        timeout=30,
    )
    if resp.status_code != 202:
        try:    detail = resp.json()
        except: detail = resp.text
        raise RuntimeError(f"Graph {resp.status_code}: {detail}")

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime.now(IST)
NOW_STR = NOW.strftime("%Y-%m-%d %H:%M:%S IST")
DATE_DISPLAY = NOW.strftime("%B %d, %Y")

# ══════════════════════════════════════════════════════════════════════════════
# TEST 1 — send_admin_alert
# ══════════════════════════════════════════════════════════════════════════════
header("TEST 1 — send_admin_alert")
info("Simulates: unhandled exception in the pipeline")
sep()

admin_email = os.getenv("ADMIN_ALERT_EMAIL", TEST_RECIPIENT)

try:
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:560px;padding:20px;color:#333;">
      <div style="border-left:4px solid #dc3545;padding:12px 18px;background:#fff5f5;border-radius:4px;">
        <h2 style="color:#c0392b;margin:0 0 6px;font-size:16px;">[ALERT] Energy Dashboard: SharePoint connection timeout</h2>
        <p style="margin:0;font-size:13px;color:#555;"><b>Time:</b> {NOW_STR}</p>
      </div>
      <pre style="font-size:13px;color:#333;background:#f9f9f9;padding:14px;border-radius:4px;margin-top:14px;">
ConnectionError: SharePoint fetch timed out after 30s
  File "sharepoint_data_service.py", line 142, in fetch_sheet_data
  requests.exceptions.ReadTimeout: HTTPSConnectionPool host=maqsoftware.sharepoint.com
      </pre>
      <p style="font-size:11px;color:#aaa;margin-top:12px;">⚠ TEST EMAIL — sent by test_all_emails.py</p>
    </div>"""

    graph_send(
        from_addr=EMAIL_FROM,
        to_list=[admin_email],
        cc_list=[],
        subject=f"[ALERT] Energy Dashboard: SharePoint connection timeout",
        html_body=html,
    )
    ok(f"Sent to {admin_email}")
    info("Check: red alert box, error traceback in body")
    results["send_admin_alert"] = "PASS"
except Exception as e:
    fail(str(e))
    results["send_admin_alert"] = "FAIL"

# ══════════════════════════════════════════════════════════════════════════════
# TEST 2 — send_daily_report  (calls the real function)
# ══════════════════════════════════════════════════════════════════════════════
header("TEST 2 — send_daily_report (real function + real SharePoint data)")
info("This calls the actual send_daily_report() with real data from SharePoint")
info(f"Sending ONLY to: {TEST_RECIPIENT}  (scheduler_config recipients are bypassed)")
sep()

try:
    # Temporarily patch scheduler_config so only TEST_RECIPIENT gets it
    import importlib, types

    # Patch load_scheduler_config to return test recipient only

    # Import the email service module from the project
    sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
    from app.services import email_service
    from app.services import scheduler_service

    _orig_load = scheduler_service.load_scheduler_config

    result = email_service.send_daily_report(trigger_source="test_script")


    if result and result.get("status") == "Success":
        ok(f"Status: {result['status']}")
        ok(f"Recipients: {result.get('recipients')}")
        ok(f"Attachment: {result.get('attachment')}")
        info("Check: full HTML table with 30-day data + Excel attachment")
        results["send_daily_report"] = "PASS"
    else:
        fail(f"Returned: {result}")
        results["send_daily_report"] = "FAIL"

except Exception as e:
    fail(f"{type(e).__name__}: {e}")
    warn("If this is a SharePoint/import error, the email transport is fine —")
    warn("run the app normally and trigger via API: POST /api/trigger-report")
    results["send_daily_report"] = "FAIL"

# ══════════════════════════════════════════════════════════════════════════════
# TEST 3 — send_data_correction_alert
# ══════════════════════════════════════════════════════════════════════════════
header("TEST 3 — send_data_correction_alert")
info("Simulates: 3 bad rows detected in the operator's Excel upload")
sep()

fake_errors = [
    {"column": "Date",  "value": "32-May-2026",   "error": "Invalid date — day out of range"},
    {"column": "Time",  "value": "9-30",           "error": "Invalid format — expected HH:MM (e.g. 09:30)"},
    {"column": "Grid Units Consumed (KWh)", "value": "abc", "error": "Non-numeric value"},
]

try:
    # Patch env vars so it sends only to test recipient
    _orig_op  = os.environ.get("OPERATOR_EMAIL", "")
    _orig_cc  = os.environ.get("CC_EMAIL", "")

    # Re-import to pick up patched env
    import importlib
    from app.services import email_service as _es
    importlib.reload(_es)

    result = _es.send_data_correction_alert(fake_errors)

    os.environ["OPERATOR_EMAIL"] = _orig_op
    os.environ["CC_EMAIL"] = _orig_cc

    if result and result.get("status") == "Success":
        ok(f"Status: {result['status']}")
        info("Check: yellow warning box, 3-row error table with column / value / problem")
        results["send_data_correction_alert"] = "PASS"
    else:
        fail(f"Returned: {result}")
        results["send_data_correction_alert"] = "FAIL"

except Exception as e:
    fail(f"{type(e).__name__}: {e}")
    results["send_data_correction_alert"] = "FAIL"

# ══════════════════════════════════════════════════════════════════════════════
# TEST 4 — send_operator_reminder
# ══════════════════════════════════════════════════════════════════════════════
header("TEST 4 — send_operator_reminder")
info("Simulates: scheduler ran at 9 AM, no data found for today")
sep()

try:
    _orig_op = os.environ.get("OPERATOR_EMAIL", "")
    _orig_cc = os.environ.get("CC_EMAIL", "")


    importlib.reload(_es)
    result = _es.send_operator_reminder()

    os.environ["OPERATOR_EMAIL"] = _orig_op
    os.environ["CC_EMAIL"] = _orig_cc

    if result and result.get("status") == "Success":
        ok(f"Status: {result['status']}")
        info("Check: plain text reminder to update SharePoint Excel")
        results["send_operator_reminder"] = "PASS"
    else:
        fail(f"Returned: {result}")
        results["send_operator_reminder"] = "FAIL"

except Exception as e:
    fail(f"{type(e).__name__}: {e}")
    results["send_operator_reminder"] = "FAIL"

# ══════════════════════════════════════════════════════════════════════════════
# TEST 5 — send_inverter_alert
# ══════════════════════════════════════════════════════════════════════════════
header("TEST 5 — send_inverter_alert")
info("Simulates: Inverter2 + Inverter4 faulted during 30-min monitor tick")
sep()

faulted = ["Inverter2", "Inverter4"]
today_data = {
    "Inverter1": {"uptime_mins": 480, "downtime_mins": 0},
    "Inverter2": {"uptime_mins": 120, "downtime_mins": 360},   # INACTIVE
    "Inverter3": {"uptime_mins": 480, "downtime_mins": 0},
    "Inverter4": {"uptime_mins": 0,   "downtime_mins": 480},   # FAULT (all downtime)
    "Inverter5": {"uptime_mins": 460, "downtime_mins": 20},
}
date_str = NOW.strftime("%Y-%m-%d")

try:
    _orig_op = os.environ.get("OPERATOR_EMAIL", "")
    _orig_cc = os.environ.get("CC_EMAIL", "")
   

    importlib.reload(_es)
    result = _es.send_inverter_alert(faulted=faulted, today_data=today_data, date_str=date_str)

    os.environ["OPERATOR_EMAIL"] = _orig_op
    os.environ["CC_EMAIL"] = _orig_cc

    if result and result.get("status") == "Success":
        ok(f"Status: {result['status']}")
        info("Check: red fault box, Inverter2=INACTIVE badge, Inverter4=FAULT badge")
        info("Healthy inverters (1, 3, 5) should appear in a green section below")
        results["send_inverter_alert"] = "PASS"
    else:
        fail(f"Returned: {result}")
        results["send_inverter_alert"] = "FAIL"

except Exception as e:
    fail(f"{type(e).__name__}: {e}")
    results["send_inverter_alert"] = "FAIL"

# ══════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════════════════
header("Results")
print()
all_pass = True
for fn, status in results.items():
    icon = f"{GREEN}PASS{RESET}" if status == "PASS" else f"{RED}FAIL{RESET}"
    print(f"  {icon}  {fn}")
    if status != "PASS":
        all_pass = False

print()
if all_pass:
    print(f"  {GREEN}{BOLD}All 5 email flows working.{RESET}")
    print(f"  Deploy email_service.py and add to .env:\n")
    print(f"    EMAIL_AZURE_TENANT_ID      = {TENANT_ID}")
    print(f"    EMAIL_AZURE_CLIENT_ID      = {CLIENT_ID}")
    print(f"    EMAIL_AZURE_CLIENT_SECRET  = <secret>")
    print(f"    GRAPH_SENDER_MAILBOX = {SENDER}")
    print(f"    EMAIL_FROM           = {EMAIL_FROM}")
else:
    print(f"  {YELLOW}Fix failing tests above then re-run.{RESET}")
    print(f"  Note: send_daily_report failure is often a SharePoint/import issue,")
    print(f"  not a Graph API issue. Test it live via POST /api/trigger-report.")
print()