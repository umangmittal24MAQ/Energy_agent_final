#!/usr/bin/env python3
"""
run_pipeline_now.py
===================
Runs the REAL end-to-end pipeline with your actual SharePoint data and SMTP.

What it does, in order:
  1. Connects to SharePoint and fetches today's grid_and_diesel row
  2. Validates every column (date, time, numeric, etc.)
     - If invalid  → sends correction alert email and STOPS
     - If valid     → continues
  3. Runs master_data_engine:
       pulls grid_and_diesel  (today's row)
       pulls UnifiedSolarData (yesterday's solar)
       merges them → pushes updated Master-data.xlsx to SharePoint
  4. Sends the daily HTML report email from the freshly updated Master-data

USAGE
-----
    cd path\\to\\server          # folder that contains app/
    python run_pipeline_now.py

    # Override the date (useful if you want to re-process yesterday's data):
    python run_pipeline_now.py --date 2026-05-04

    # Dry-run: do everything EXCEPT actually send the email or push to SharePoint
    python run_pipeline_now.py --dry-run

    # Redirect all emails to yourself instead of the real recipients:
    python run_pipeline_now.py --override-to you@example.com

PREREQUISITES
-------------
    pip install python-dotenv pandas openpyxl msal requests apscheduler
    A .env file in the server/ folder with all SharePoint + SMTP credentials.
"""

import os
import sys
import argparse
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# ──────────────────────────────────────────────────────────────────────────────
# Bootstrap: find server/ (the folder containing app/) from anywhere
# ──────────────────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent

def _find_backend_root() -> Path:
    candidates = [SCRIPT_DIR, SCRIPT_DIR.parent]
    cwd = Path.cwd().resolve()
    for p in [cwd] + list(cwd.parents):
        candidates += [p, p / "server"]
    for c in candidates:
        if (c / "app" / "services" / "scheduler_service.py").exists():
            return c
        if (c / "app").is_dir() and (c / "app" / "__init__.py").exists():
            return c
    return SCRIPT_DIR

_BACKEND_ROOT = _find_backend_root()
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

IST = ZoneInfo("Asia/Kolkata")

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────
def _banner(title: str) -> None:
    bar = "═" * 64
    print(f"\n{bar}\n  {title}\n{bar}")

def _ok(msg):   print(f"  ✅  {msg}")
def _fail(msg): print(f"  ❌  {msg}")
def _info(msg): print(f"  ℹ️   {msg}")
def _warn(msg): print(f"  ⚠️   {msg}")
def _step(msg): print(f"  ──  {msg}")


# ──────────────────────────────────────────────────────────────────────────────
# STEP 0 — Load .env
# ──────────────────────────────────────────────────────────────────────────────
def load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        _warn("python-dotenv not installed. Run: pip install python-dotenv")
        return

    for candidate in [_BACKEND_ROOT, SCRIPT_DIR, SCRIPT_DIR.parent, _BACKEND_ROOT.parent]:
        ef = candidate / ".env"
        if ef.exists():
            load_dotenv(dotenv_path=ef, override=False)
            _ok(f".env loaded from {ef}")
            return
    _warn(".env not found — using existing shell environment variables.")


# ──────────────────────────────────────────────────────────────────────────────
# STEP 1 — Pre-flight: SMTP + SharePoint
# ──────────────────────────────────────────────────────────────────────────────
def check_smtp() -> bool:
    import smtplib
    server   = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    port     = int(os.getenv("SMTP_PORT", 587))
    frm      = os.getenv("EMAIL_FROM", "")
    pwd      = os.getenv("EMAIL_PASSWORD", "")
    if not frm or not pwd:
        _fail("EMAIL_FROM or EMAIL_PASSWORD not set in .env")
        return False
    try:
        with smtplib.SMTP(server, port, timeout=10) as s:
            s.starttls()
            s.login(frm, pwd)
        _ok(f"SMTP OK  ({frm} → {server}:{port})")
        return True
    except Exception as e:
        _fail(f"SMTP failed: {e}")
        return False


def check_sharepoint():
    """Returns the authenticated SharePoint service or None."""
    try:
        from app.services.sharepoint_data_service import get_service
        sp = get_service()
        if sp.authenticated:
            _ok("SharePoint authenticated (MSAL token acquired)")
            return sp
        _fail("SharePoint auth failed — check SHAREPOINT_TENANT_ID / CLIENT_ID / CLIENT_SECRET")
        return None
    except Exception as e:
        _fail(f"SharePoint check raised: {e}")
        traceback.print_exc()
        return None


# ──────────────────────────────────────────────────────────────────────────────
# STEP 2 — Fetch & validate today's grid_and_diesel row
# ──────────────────────────────────────────────────────────────────────────────
def fetch_and_validate(sp, target_date: str, dry_run: bool) -> bool:
    """
    Returns True  → data exists, is valid, safe to merge.
    Returns False → data missing or invalid (correction alert already sent).
    """
    from app.services.scheduler_service import (
        check_grid_diesel_entry_exists,
        validate_data_for_today,
    )
    from app.services.email_service import send_data_correction_alert

    _step("Checking if today's grid_and_diesel row exists with Status='Done'...")
    exists = check_grid_diesel_entry_exists()
    if not exists:
        _fail(
            "No row found for today in grid_and_diesel (or Status != 'Done').\n"
            "         Please fill in the SharePoint Excel file and set Status = Done, then re-run."
        )
        return False
    _ok("Row found with Status='Done'")

    _step("Validating all columns (date, time, numeric, ranges)...")
    result = validate_data_for_today()

    if result["valid"]:
        _ok("All columns valid — ready to merge")
        return True

    errors = result.get("errors", [])
    _fail(f"{len(errors)} validation error(s) found:")
    for e in errors:
        print(f"         Column : {e['column']}")
        print(f"         Value  : {e['value']}")
        print(f"         Error  : {e['error']}")
        print()

    if dry_run:
        _warn("[DRY-RUN] Would send correction alert email — skipped.")
    else:
        _step("Sending correction alert email to operator...")
        alert_result = send_data_correction_alert(errors)
        if alert_result.get("status") == "Success":
            _ok("Correction alert email sent. Fix the errors above, then re-run.")
        else:
            _fail(f"Correction alert failed: {alert_result}")

    return False


# ──────────────────────────────────────────────────────────────────────────────
# STEP 3 — Run master_data_engine (pull → merge → push to SharePoint)
# ──────────────────────────────────────────────────────────────────────────────
def run_master_engine(target_date: str, dry_run: bool) -> bool:
    """
    Calls master_data_engine.process_master_data() directly (not via subprocess).
    operator_date = target_date
    solar_date    = target_date - 1 day  (universal rule in your codebase)
    """
    import importlib, sys as _sys

    # master_data_engine lives under app/agents/ and does its own env loading.
    # Import it fresh so it picks up the already-loaded env.
    agent_path = _BACKEND_ROOT / "app" / "agents" / "master_data_engine.py"
    if not agent_path.exists():
        _fail(f"master_data_engine.py not found at {agent_path}")
        return False

    _step(f"Running master_data_engine for operator_date={target_date}...")

    # Compute solar_date = operator_date - 1 day (universal rule)
    from datetime import date as _date
    import pandas as pd
    op_dt     = pd.to_datetime(target_date)
    solar_date = (op_dt - timedelta(days=1)).strftime("%Y-%m-%d")
    _info(f"operator_date = {target_date}   |   solar_date = {solar_date}")

    if dry_run:
        _warn("[DRY-RUN] Would run process_master_data() — skipped.")
        _warn("[DRY-RUN] Would push updated Master-data.xlsx to SharePoint — skipped.")
        return True

    try:
        # Dynamically load the module from its file path so it works regardless
        # of whether app/agents/ is on the import path.
        import importlib.util
        spec = importlib.util.spec_from_file_location("master_data_engine", agent_path)
        engine = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(engine)

        engine.process_master_data(
            operator_date=target_date,
            solar_date=solar_date,
            fallback_operator_date=None,
        )
        _ok("Master-data.xlsx updated on SharePoint successfully")
        return True

    except Exception as e:
        _fail(f"master_data_engine raised: {e}")
        traceback.print_exc()
        return False


# ──────────────────────────────────────────────────────────────────────────────
# STEP 4 — Send the daily HTML report email
# ──────────────────────────────────────────────────────────────────────────────
def send_report(dry_run: bool) -> bool:
    from app.services.email_service import send_daily_report

    if dry_run:
        _warn("[DRY-RUN] Would call send_daily_report() — skipped.")
        return True

    _step("Sending daily HTML report email...")
    try:
        result = send_daily_report(trigger_source="manual_run_pipeline", is_missing_data=False)
        if result.get("status") == "Success":
            recip = result.get("recipients", "")
            attach = result.get("attachment", "")
            _ok(f"Report sent to: {recip}")
            _ok(f"Attachment: {attach}")
            return True
        else:
            _fail(f"send_daily_report returned: {result}")
            return False
    except Exception as e:
        _fail(f"send_daily_report raised: {e}")
        traceback.print_exc()
        return False


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Run the full pipeline: validate → merge → push to SharePoint → send report.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--date", "-d",
        default=None,
        metavar="YYYY-MM-DD",
        help="Target date to process (default: today IST).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and merge locally but skip SharePoint writes and email sends.",
    )
    parser.add_argument(
        "--override-to",
        metavar="EMAIL",
        help="Redirect ALL outgoing emails to this address (safe testing).",
    )
    parser.add_argument(
        "--skip-checks",
        action="store_true",
        help="Skip SMTP + SharePoint pre-flight checks.",
    )
    args = parser.parse_args()

    # ── Apply email override before anything imports email_service ────────────
    if args.override_to:
        os.environ["OPERATOR_EMAIL"]    = args.override_to
        os.environ["CC_EMAIL"]          = ""
        os.environ["ADMIN_ALERT_EMAIL"] = args.override_to
        _warn(f"OVERRIDE: all emails redirected to {args.override_to}")

    load_env()

    target_date = args.date or datetime.now(IST).strftime("%Y-%m-%d")

    _banner("Energy Dashboard — Full Pipeline Run")
    _info(f"Backend root : {_BACKEND_ROOT}")
    _info(f"Target date  : {target_date}")
    _info(f"Dry-run      : {args.dry_run}")
    print()

    # ── Early import check ────────────────────────────────────────────────────
    try:
        import app.services.scheduler_service  # noqa
        import app.services.email_service      # noqa
    except ModuleNotFoundError as e:
        _fail(f"Cannot import app package: {e}")
        _info(f"Make sure you run this script from inside server/")
        _info(f"  cd path\\to\\server")
        _info(f"  python run_pipeline_now.py")
        sys.exit(1)

    # ── Pre-flight ────────────────────────────────────────────────────────────
    if not args.skip_checks:
        _banner("Step 0 — Pre-flight checks")
        smtp_ok = check_smtp()
        sp = check_sharepoint()
        if not smtp_ok or sp is None:
            _fail("Pre-flight failed. Fix the issues above and retry.")
            _info("(Use --skip-checks to bypass pre-flight.)")
            sys.exit(1)
    else:
        _warn("Skipping pre-flight checks (--skip-checks)")
        try:
            from app.services.sharepoint_data_service import get_service
            sp = get_service()
        except Exception as e:
            _fail(f"Could not instantiate SharePoint service: {e}")
            sys.exit(1)

    # ── Step 2: Fetch + Validate ──────────────────────────────────────────────
    _banner("Step 1 — Fetch & validate today's grid_and_diesel row")
    data_ok = fetch_and_validate(sp, target_date, args.dry_run)
    if not data_ok:
        print()
        _fail("Pipeline stopped at validation. See errors above.")
        sys.exit(1)

    # ── Step 3: Master engine (merge + push) ─────────────────────────────────
    _banner("Step 2 — Merge grid + solar → push Master-data.xlsx to SharePoint")
    engine_ok = run_master_engine(target_date, args.dry_run)
    if not engine_ok:
        _fail("Master engine failed. Check logs above.")
        sys.exit(1)

    # ── Step 4: Send report email ─────────────────────────────────────────────
    _banner("Step 3 — Send daily report email")
    mail_ok = send_report(args.dry_run)

    # ── Summary ───────────────────────────────────────────────────────────────
    _banner("Done")
    steps = [
        ("Fetch + validate grid_and_diesel", data_ok),
        ("Master engine (merge + push)",     engine_ok),
        ("Send report email",                mail_ok),
    ]
    for label, ok in steps:
        (_ok if ok else _fail)(label)

    if not all(ok for _, ok in steps):
        sys.exit(1)


if __name__ == "__main__":
    main()