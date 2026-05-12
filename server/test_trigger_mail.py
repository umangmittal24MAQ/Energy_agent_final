#!/usr/bin/env python3
"""
trigger_test_emails.py
======================
Fires every email type in the Energy Dashboard using REAL SharePoint data
and REAL SMTP credentials — no mocks, no dummy data.

USAGE
-----
    cd server                           # backend root where .env lives
    python trigger_test_emails.py                      # run all 4 cases
    python trigger_test_emails.py --case 2             # one specific case
    python trigger_test_emails.py --case 1 --case 3    # multiple cases
    python trigger_test_emails.py --list               # show case descriptions

    # Redirect ALL emails to yourself during testing (highly recommended)
    OVERRIDE_TO=you@example.com python trigger_test_emails.py

CASES
-----
    1  Daily Report           — fetches master_data from SharePoint, sends the HTML report
    2  Data Correction Alert  — fetches grid_and_diesel, runs real validation, sends alert
                                if errors found; use --force-alert to send even if data is clean
    3  Operator Reminder      — sends "please enter today's data" plain-text nudge
    4  Admin Alert            — sends a plain-text system-error notification to ADMIN_ALERT_EMAIL

PREREQUISITES
-------------
    pip install python-dotenv pandas openpyxl msal requests apscheduler jinja2
"""

import os
import sys
import argparse
import smtplib
import traceback
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch

# ── 1. Bootstrap: load .env and put server/ on sys.path ──────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent

_loaded_env = False
for candidate in [SCRIPT_DIR, SCRIPT_DIR / "server", SCRIPT_DIR.parent / "server"]:
    env_file = candidate / ".env"
    if env_file.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(dotenv_path=env_file, override=True)
            sys.path.insert(0, str(candidate))
            print(f"[ENV] Loaded {env_file}")
            _loaded_env = True
            break
        except ImportError:
            print("[ENV] python-dotenv not installed — run: pip install python-dotenv")
            sys.exit(1)

if not _loaded_env:
    print("[ENV] WARNING: .env not found. Using existing shell environment.")

IST = ZoneInfo("Asia/Kolkata")

# ── 2. OVERRIDE_TO: redirect all mail to one address for safe testing ─────────
OVERRIDE_TO = os.getenv("OVERRIDE_TO")
if OVERRIDE_TO:
    print(f"[ENV] OVERRIDE_TO active — all mail goes to: {OVERRIDE_TO}")
    os.environ["OPERATOR_EMAIL"]    = OVERRIDE_TO
    os.environ["CC_EMAIL"]          = ""
    os.environ["ADMIN_ALERT_EMAIL"] = OVERRIDE_TO

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _section(title: str) -> None:
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)

def _ok(msg: str)   -> None: print(f"  ✅  {msg}")
def _fail(msg: str) -> None: print(f"  ❌  {msg}")
def _info(msg: str) -> None: print(f"  ℹ️   {msg}")

def _result(res) -> None:
    if isinstance(res, dict):
        if res.get("status") == "Success":
            _ok(str(res))
        else:
            _fail(str(res))
    else:
        _info(str(res))


# ─────────────────────────────────────────────────────────────────────────────
# PRE-FLIGHT CHECKS
# ─────────────────────────────────────────────────────────────────────────────

def check_smtp() -> bool:
    smtp_server    = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    smtp_port      = int(os.getenv("SMTP_PORT", 587))
    email_from     = os.getenv("EMAIL_FROM", "")
    email_password = os.getenv("EMAIL_PASSWORD", "")

    if not email_from or not email_password:
        _fail("EMAIL_FROM or EMAIL_PASSWORD not set in .env")
        return False
    try:
        with smtplib.SMTP(smtp_server, smtp_port, timeout=10) as s:
            s.starttls()
            s.login(email_from, email_password)
        _ok(f"SMTP login OK  ({email_from} → {smtp_server}:{smtp_port})")
        return True
    except Exception as e:
        _fail(f"SMTP login failed: {e}")
        return False


def check_sharepoint() -> bool:
    try:
        from app.services.sharepoint_data_service import get_service
        sp = get_service()
        if sp.authenticated:
            _ok("SharePoint authenticated via Azure AD (MSAL token acquired)")
            return True
        else:
            _fail(
                "SharePoint authentication failed.\n"
                "  Check SHAREPOINT_TENANT_ID / SHAREPOINT_CLIENT_ID / SHAREPOINT_CLIENT_SECRET in .env"
            )
            return False
    except Exception as e:
        _fail(f"SharePoint check raised: {e}")
        return False


def get_live_sp():
    """Return the live authenticated SharePoint service, or raise clearly."""
    from app.services.sharepoint_data_service import get_service
    sp = get_service()
    if not sp.authenticated:
        raise RuntimeError(
            "SharePoint is not authenticated. "
            "Check SHAREPOINT_TENANT_ID / CLIENT_ID / CLIENT_SECRET in .env"
        )
    return sp


# ─────────────────────────────────────────────────────────────────────────────
# CASE 1 — Daily Report
# Fetches master_data.xlsx from SharePoint and sends the full HTML report.
# ─────────────────────────────────────────────────────────────────────────────

def case_1_daily_report():
    _section("CASE 1 — Daily Report  (real master_data from SharePoint)")

    sp = get_live_sp()

    _info("Fetching master_data.xlsx from SharePoint...")
    master_df = sp.fetch_sheet_data("master_data")

    if master_df is None or master_df.empty:
        _fail("master_data returned empty or None — nothing to report")
        return

    _ok(f"master_data fetched: {len(master_df)} rows, {len(master_df.columns)} columns")

    if "Date" in master_df.columns:
        _info(f"Date range in file: {master_df['Date'].iloc[0]}  →  {master_df['Date'].iloc[-1]}")

    _info("Sending daily report email (send_daily_report)...")

    # send_daily_report internally calls get_service() again — patch it to
    # return the same already-authenticated instance we just verified above.
    with patch("app.services.sharepoint_data_service.get_service", return_value=sp):
        from app.services.email_service import send_daily_report
        result = send_daily_report(trigger_source="manual_test_script")

    _result(result)


# ─────────────────────────────────────────────────────────────────────────────
# CASE 2 — Data Correction Alert
# Fetches grid_and_diesel.xlsx, validates today's row with the real rules,
# and sends the correction email if errors are found.
# Use --force-alert to send even when data is clean.
# ─────────────────────────────────────────────────────────────────────────────

def case_2_data_correction_alert(force: bool = False):
    _section(
        "CASE 2 — Data Correction Alert  (real grid_and_diesel validation)"
        + ("  [FORCED]" if force else "")
    )

    sp = get_live_sp()

    _info("Fetching grid_and_diesel.xlsx from SharePoint...")
    df = sp.fetch_sheet_data("grid_and_diesel")

    if df is None or df.empty:
        _fail("grid_and_diesel returned empty or None — cannot validate")
        return

    _ok(f"grid_and_diesel fetched: {len(df)} rows, {len(df.columns)} columns")

    if "Date" in df.columns:
        _info(f"Date range in file: {df['Date'].iloc[0]}  →  {df['Date'].iloc[-1]}")

    _info("Running live validation on today's row...")

    # validate_data_for_today() calls get_service() internally — inject the
    # already-fetched df so we don't make a second SharePoint request.
    with patch("app.services.sharepoint_data_service.get_service", return_value=sp):
        from app.services.scheduler_service import validate_data_for_today
        validation = validate_data_for_today()

    if validation["valid"] and not force:
        _ok(
            "Validation passed — today's data is clean. No correction email needed.\n"
            "  Tip: run with --force-alert to send the email anyway to test the template."
        )
        return

    errors = validation.get("errors") or []

    if not errors:
        # Data is clean but --force-alert was set: inject one synthetic marker
        _info("Data is clean. Injecting a synthetic error so the email template can be tested.")
        errors = [{
            "column": "Time",
            "value":  "FORCED-TEST",
            "error":  "This is a forced test — actual data passed all validation rules.",
        }]
    else:
        _fail(f"Validation found {len(errors)} error(s) in today's row:")
        for e in errors:
            print(f"       Column : {e['column']}")
            print(f"       Value  : {e['value']}")
            print(f"       Error  : {e['error']}")
            print()

    _info("Sending data correction alert email...")
    from app.services.email_service import send_data_correction_alert
    result = send_data_correction_alert(errors)
    _result(result)


# ─────────────────────────────────────────────────────────────────────────────
# CASE 3 — Operator Reminder
# ─────────────────────────────────────────────────────────────────────────────

def case_3_operator_reminder():
    _section("CASE 3 — Operator Reminder")
    _info("Sends the 'please enter today\\'s data' plain-text nudge.")
    _info(f"OPERATOR_EMAIL : {os.getenv('OPERATOR_EMAIL', '[not set]')}")
    _info(f"CC_EMAIL       : {os.getenv('CC_EMAIL', '[not set]')}")
    print()

    from app.services.email_service import send_operator_reminder
    result = send_operator_reminder()
    _result(result)


# ─────────────────────────────────────────────────────────────────────────────
# CASE 4 — Admin Alert
# ─────────────────────────────────────────────────────────────────────────────

def case_4_admin_alert():
    _section("CASE 4 — Admin Alert")
    _info("Sends a plain-text system-error notification to ADMIN_ALERT_EMAIL.")
    _info(f"ADMIN_ALERT_EMAIL : {os.getenv('ADMIN_ALERT_EMAIL', '[not set]')}")
    print()

    from app.services.email_service import send_admin_alert
    try:
        send_admin_alert(
            subject="Test: Scheduler Failed to Start",
            error_message=(
                "This is a TEST admin alert fired by trigger_test_emails.py.\n\n"
                "In a real scenario this would contain:\n"
                "  - Full Python traceback\n"
                "  - Failed job name and timestamp\n"
                "  - Environment: APP_ENV, host, worker PID\n\n"
                "No action required — this is just a test."
            ),
        )
        _ok("send_admin_alert completed  (check ADMIN_ALERT_EMAIL inbox)")
    except Exception as e:
        _fail(f"send_admin_alert raised: {e}")
        traceback.print_exc()


# ─────────────────────────────────────────────────────────────────────────────
# REGISTRY & CLI
# ─────────────────────────────────────────────────────────────────────────────

CASES = {
    1: ("Daily Report            — master_data from SharePoint → HTML report email",
        case_1_daily_report),
    2: ("Data Correction Alert   — validate grid_and_diesel → alert email if errors found",
        case_2_data_correction_alert),
    3: ("Operator Reminder       — plain-text 'please enter today\\'s data' nudge",
        case_3_operator_reminder),
    4: ("Admin Alert             — plain-text system-error to ADMIN_ALERT_EMAIL",
        case_4_admin_alert),
}


def main():
    parser = argparse.ArgumentParser(
        description="Fire test emails for every case using real SharePoint data.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--case", "-c",
        type=int, action="append", dest="cases", metavar="N",
        help="Case number(s) to run (1–4). Repeatable. Default: all.",
    )
    parser.add_argument(
        "--list", "-l",
        action="store_true",
        help="List all cases and exit.",
    )
    parser.add_argument(
        "--skip-checks",
        action="store_true",
        help="Skip SMTP + SharePoint pre-flight checks.",
    )
    parser.add_argument(
        "--force-alert",
        action="store_true",
        help=(
            "Case 2 only: send the correction email even if today's data is valid. "
            "Injects a synthetic error entry if none are found."
        ),
    )
    args = parser.parse_args()

    if args.list:
        print("\nAvailable cases:")
        for num, (desc, _) in CASES.items():
            print(f"  {num}  {desc}")
        print()
        return

    selected = sorted(set(args.cases)) if args.cases else list(CASES.keys())
    invalid  = [n for n in selected if n not in CASES]
    if invalid:
        print(f"ERROR: Unknown case number(s): {invalid}. Valid: {list(CASES.keys())}")
        sys.exit(1)

    # ── Header ────────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("  Energy Dashboard — Email Trigger Test  [REAL DATA]")
    print(f"  Cases to run : {selected}")
    if OVERRIDE_TO:
        print(f"  Redirect all : {OVERRIDE_TO}")
    print(f"{'='*60}")

    # ── Pre-flight ────────────────────────────────────────────────────────────
    if not args.skip_checks:
        _section("Pre-flight: SMTP")
        smtp_ok = check_smtp()

        if any(c in selected for c in [1, 2]):
            _section("Pre-flight: SharePoint / Azure AD")
            sp_ok = check_sharepoint()
        else:
            sp_ok = True

        if not smtp_ok or not sp_ok:
            print("\n  Pre-flight failed. Fix the issues above and retry.")
            print("  (Use --skip-checks to bypass.)")
            sys.exit(1)

    # ── Run cases ─────────────────────────────────────────────────────────────
    passed, failed = [], []

    for num in selected:
        desc, fn = CASES[num]
        try:
            if num == 2:
                fn(force=args.force_alert)
            else:
                fn()
            passed.append(num)
        except Exception:
            _fail(f"Case {num} raised an unexpected exception:")
            traceback.print_exc()
            failed.append(num)

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print(f"  Done — {len(passed)} succeeded, {len(failed)} failed")
    if passed: print(f"  ✅  {passed}")
    if failed: print(f"  ❌  {failed}")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()