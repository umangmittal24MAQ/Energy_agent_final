#!/usr/bin/env python3
"""
test_full_flow.py
=================
End-to-end flow test for the Energy Dashboard automation.

Simulates the EXACT scenario described below, in order:

  PHASE 1  9:00 AM  — No data in SharePoint. Reminder email sent.
  PHASE 2 10:00 AM  — Operator enters row but Time = "morning" (invalid).
                       Correction alert sent. Report BLOCKED.
  PHASE 3 10:30 AM  — Scheduler deadline fires. Data still invalid.
                       Second correction alert + FALLBACK report sent.
                       Tracker locked as "invalid_data_fallback".
  PHASE 4 11:00 AM  — Operator fixes Time = "10:30". Late-check gates pass.
                       Master-data engine runs silently. No extra email.

Every SharePoint read/write and every SMTP send is intercepted — no real
network calls are made. All emails are captured in memory so you can inspect
exactly what would have landed in the mailbox.

USAGE
-----
    cd <backend_root>          # directory that contains the app/ package
    pip install pytest pandas
    python test_full_flow.py          # run all 4 phases
    python test_full_flow.py --phase 2          # single phase
    python test_full_flow.py --verbose          # show captured email bodies

    # With a real .env (SMTP + SharePoint creds loaded but still mocked):
    python test_full_flow.py --load-env
"""

import os
import sys
import json
import argparse
import traceback
import tempfile
import subprocess
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch, call
from zoneinfo import ZoneInfo

import pandas as pd

# ──────────────────────────────────────────────────────────────────────────────
# Bootstrap: find the backend root (the folder that CONTAINS app/) and add it
# to sys.path — regardless of where this script lives or where you ran it from.
#
# Handled layouts:
#   server/test_full_flow.py          → server/ contains app/
#   server/app/test_full_flow.py      → server/ is the parent
#   anywhere on PATH                  → walk CWD upward
# ──────────────────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent

def _find_backend_root() -> Path:
    # 1. Script's own directory or its parent (covers server/ and server/app/)
    candidates = [
        SCRIPT_DIR,
        SCRIPT_DIR.parent,
        SCRIPT_DIR / "server",
        SCRIPT_DIR.parent / "server",
    ]
    # 2. Walk up from CWD (covers running from any sub-directory)
    cwd = Path.cwd().resolve()
    for p in [cwd] + list(cwd.parents):
        candidates.append(p)
        candidates.append(p / "server")

    for c in candidates:
        if (c / "app" / "services" / "scheduler_service.py").exists():
            return c
        if (c / "app").is_dir() and (c / "app" / "__init__.py").exists():
            return c
    return SCRIPT_DIR  # last resort; import will fail with a clear message

_BACKEND_ROOT = _find_backend_root()
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

IST = ZoneInfo("Asia/Kolkata")


# ──────────────────────────────────────────────────────────────────────────────
# Helpers — pretty printing
# ──────────────────────────────────────────────────────────────────────────────
def _section(title: str) -> None:
    bar = "═" * 64
    print(f"\n{bar}")
    print(f"  {title}")
    print(bar)

def _ok(msg: str)   -> None: print(f"  ✅  {msg}")
def _fail(msg: str) -> None: print(f"  ❌  {msg}")
def _info(msg: str) -> None: print(f"  ℹ️   {msg}")
def _warn(msg: str) -> None: print(f"  ⚠️   {msg}")
def _step(msg: str) -> None: print(f"  ──  {msg}")

PASSED: List[str] = []
FAILED: List[str] = []


def _assert(cond: bool, label: str, detail: str = "") -> None:
    if cond:
        _ok(label)
        PASSED.append(label)
    else:
        _fail(f"{label}  {detail}".strip())
        FAILED.append(label)


# ──────────────────────────────────────────────────────────────────────────────
# Captured email store — every send_* call appends here
# ──────────────────────────────────────────────────────────────────────────────
SENT_EMAILS: List[Dict[str, Any]] = []


# ──────────────────────────────────────────────────────────────────────────────
# Fake data factory
# ──────────────────────────────────────────────────────────────────────────────
def _today_str() -> str:
    return datetime.now(IST).strftime("%d-%b-%Y")

def _today_day() -> str:
    return datetime.now(IST).strftime("%A")


def _make_grid_df_no_data() -> pd.DataFrame:
    """SharePoint has NO row for today — empty dataframe with headers only."""
    return pd.DataFrame(columns=[
        "Date", "Day", "Time", "Ambient Temperature °C",
        "Grid Units Consumed (KWh)", "Solar Units Consumed(KWh)",
        "Total Units Consumed (KWh)", "Total Units Consumed in INR",
        "Energy Saving in INR", "Number of Panels Cleaned",
        "Diesel consumed", "Water treated through STP",
        "Water treated through WTP", "Issues", "Status",
    ])


def _make_grid_df_invalid_time() -> pd.DataFrame:
    """Operator entered row but Time column contains text 'morning'."""
    return pd.DataFrame([{
        "Date":                        _today_str(),
        "Day":                         _today_day(),
        "Time":                        "morning",        # ← BAD VALUE
        "Ambient Temperature °C":      "28",
        "Grid Units Consumed (KWh)":   "4,452",
        "Solar Units Consumed(KWh)":   "1,013",
        "Total Units Consumed (KWh)":  "5,465",
        "Total Units Consumed in INR": "31,653.72",
        "Energy Saving in INR":        "7,202.43",
        "Number of Panels Cleaned":    "156",
        "Diesel consumed":             "0",
        "Water treated through STP":   "26",
        "Water treated through WTP":   "34",
        "Issues":                      "No issues",
        "Status":                      "Done",
    }])


def _make_grid_df_valid() -> pd.DataFrame:
    """Operator corrected Time to '10:30' — all columns valid."""
    df = _make_grid_df_invalid_time().copy()
    df["Time"] = "10:30"
    return df


def _make_master_df() -> pd.DataFrame:
    """Minimal master_data sheet with one historical row (yesterday)."""
    yesterday = (datetime.now(IST) - timedelta(days=1)).strftime("%d-%b-%Y")
    return pd.DataFrame([{
        "Date": yesterday, "Day": "Yesterday", "Grid Units": "4100",
        "Solar Units": "900", "Total Units": "5000",
        "Total INR": "30000", "Saving INR": "6500",
    }])


def _make_unified_solar_df() -> pd.DataFrame:
    """Minimal unified solar sheet."""
    today = datetime.now(IST).strftime("%d-%b-%Y")
    return pd.DataFrame([{
        "Date": today, "Time": "17:00",
        "Day Generation (kWh)": "1013",
        "Yesterday Gen": "980",
    }])


# ──────────────────────────────────────────────────────────────────────────────
# Mock SMTP — captures all outgoing emails
# ──────────────────────────────────────────────────────────────────────────────
class FakeSMTP:
    """Drop-in mock for smtplib.SMTP. Captures sent messages."""

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def starttls(self):
        pass

    def login(self, user, password):
        pass

    def sendmail(self, from_addr: str, to_addrs: list, msg_string: str):
        # Parse subject from raw message
        subject = ""
        for line in msg_string.splitlines():
            if line.lower().startswith("subject:"):
                subject = line[8:].strip()
                break
        SENT_EMAILS.append({
            "from":    from_addr,
            "to":      to_addrs,
            "subject": subject,
            "raw":     msg_string,
        })
        _step(f"📧 EMAIL CAPTURED → {to_addrs}  |  {subject[:70]}")


# ──────────────────────────────────────────────────────────────────────────────
# Mock SharePoint service factory
# ──────────────────────────────────────────────────────────────────────────────
def _make_sp_mock(grid_df: pd.DataFrame) -> MagicMock:
    """Return a mock SharePoint service that returns the given grid_df."""
    sp = MagicMock()
    sp.authenticated = True

    master_df = _make_master_df()
    solar_df  = _make_unified_solar_df()

    def _fetch(sheet_name: str) -> pd.DataFrame:
        mapping = {
            "grid_and_diesel": grid_df,
            "master_data":     master_df,
            "unified_solar":   solar_df,
        }
        return mapping.get(sheet_name, pd.DataFrame())

    sp.fetch_sheet_data.side_effect = _fetch
    sp.update_sheet_data = MagicMock(return_value=True)
    return sp


# ──────────────────────────────────────────────────────────────────────────────
# Patch context — all external I/O replaced with mocks
# ──────────────────────────────────────────────────────────────────────────────
def _build_patches(sp_mock: MagicMock, tracker_file: Path):
    """Return a list of (target_string, mock_object) pairs to patch."""
    return [
        # SMTP: capture all outgoing emails
        ("smtplib.SMTP", FakeSMTP),

        # SharePoint: return our fake data
        ("app.services.sharepoint_data_service.get_service", lambda: sp_mock),
        ("app.services.scheduler_service.get_service",       lambda: sp_mock),  # alias used inside module

        # Scheduler tracker/log files: redirect to a temp directory so
        # state survives across phases within this test run
        ("app.services.scheduler_service.SCHEDULER_TRACKER_FILE", tracker_file),
        ("app.services.scheduler_service.SCHEDULER_LOG_FILE",
         tracker_file.parent / "scheduler_log.json"),

        # Master data engine subprocess: replace with a no-op function
        # that just logs what *would* have run
        ("app.services.scheduler_service._run_master_data_engine_once",
         _fake_master_engine),
    ]


# Global flag so we can assert the engine was (or wasn't) called
MASTER_ENGINE_CALLS: List[Dict] = []


def _fake_master_engine(operator_date: str, solar_date: str,
                         fallback_operator_date: Optional[str] = None) -> Dict:
    """Replaces the real subprocess call to master_data_engine.py."""
    MASTER_ENGINE_CALLS.append({
        "operator_date":          operator_date,
        "solar_date":             solar_date,
        "fallback_operator_date": fallback_operator_date,
    })
    _step(f"🔧 MASTER ENGINE (mock) → operator={operator_date}, solar={solar_date}")
    return {"status": "Success"}


# ──────────────────────────────────────────────────────────────────────────────
# Individual phase runners
# ──────────────────────────────────────────────────────────────────────────────

def phase_1_no_data(tracker_file: Path, verbose: bool) -> None:
    """
    9:00 AM — No operator data. Reminder cycle fires.
    Expected: 1 operator-reminder email sent. No report. Tracker NOT locked.
    """
    _section("PHASE 1 — 9:00 AM | No data in SharePoint → reminder email")

    grid_df  = _make_grid_df_no_data()
    sp_mock  = _make_sp_mock(grid_df)
    email_count_before = len(SENT_EMAILS)

    from app.services.scheduler_service import (
        _run_operator_reminder_cycle,
        tracker_is_locked_for_today,
        _daily_report_tracker,
    )
    # Reset in-memory tracker (simulate fresh scheduler state)
    _daily_report_tracker.clear()

    patches = _build_patches(sp_mock, tracker_file)
    with _apply_patches(patches):
        _run_operator_reminder_cycle()

    emails_sent = SENT_EMAILS[email_count_before:]
    _assert(len(emails_sent) == 1,
            "Exactly 1 email sent (operator reminder)",
            f"got {len(emails_sent)}")
    if emails_sent:
        subj = emails_sent[0]["subject"]
        _assert("Action Required" in subj or "Operator Data Missing" in subj,
                "Email subject contains 'Action Required'",
                f"got: {subj!r}")

    with _apply_patches(patches):
        locked = tracker_is_locked_for_today()
    _assert(not locked, "Tracker NOT locked (report not sent yet)")

    if verbose:
        _print_captured_emails(emails_sent, label="Phase 1 emails")


def phase_2_invalid_time(tracker_file: Path, verbose: bool) -> None:
    """
    10:00 AM — Operator enters row with Time='morning'.
    Reminder cycle fires, finds data, runs validation → BLOCKED.
    Expected: 1 correction-alert email. No daily report. Tracker NOT locked.
    """
    _section("PHASE 2 — 10:00 AM | Data entered with Time='morning' → correction alert")

    grid_df = _make_grid_df_invalid_time()
    sp_mock = _make_sp_mock(grid_df)
    email_count_before = len(SENT_EMAILS)
    engine_calls_before = len(MASTER_ENGINE_CALLS)

    from app.services.scheduler_service import (
        _run_operator_reminder_cycle,
        tracker_is_locked_for_today,
        validate_data_for_today,
        _daily_report_tracker,
    )
    _daily_report_tracker.clear()

    patches = _build_patches(sp_mock, tracker_file)
    with _apply_patches(patches):
        # First confirm the validator sees the error
        validation = validate_data_for_today()
        _step(f"Validator result: valid={validation['valid']}  errors={validation.get('errors', [])}")
        _assert(not validation["valid"],
                "Validator correctly rejects Time='morning'")
        if not validation["valid"]:
            errs = validation.get("errors", [])
            time_err = next((e for e in errs if "time" in e["column"].lower()), None)
            _assert(time_err is not None,
                    "Error is specifically about the Time column",
                    f"errors={errs}")

        # Now run the cycle — it should send correction alert only
        _run_operator_reminder_cycle()

    emails_sent = SENT_EMAILS[email_count_before:]
    correction_emails = [e for e in emails_sent if "Fix Excel" in e["subject"] or "Action Required" in e["subject"]]
    daily_report_emails = [e for e in emails_sent
                           if "Energy" in e["subject"] or "Dashboard" in e["subject"]
                           or "Optimization" in e["subject"]]

    _assert(len(correction_emails) >= 1,
            "Correction-alert email was sent",
            f"subjects: {[e['subject'] for e in emails_sent]}")
    _assert(len(daily_report_emails) == 0,
            "Daily report was NOT sent (blocked by validation)")
    _assert(len(MASTER_ENGINE_CALLS) == engine_calls_before,
            "Master data engine was NOT called")

    with _apply_patches(patches):
        locked = tracker_is_locked_for_today()
    _assert(not locked, "Tracker NOT locked (report was blocked)")

    if verbose:
        _print_captured_emails(emails_sent, label="Phase 2 emails")


def phase_3_deadline_fallback(tracker_file: Path, verbose: bool) -> None:
    """
    10:30 AM — Scheduler deadline fires. Data still invalid.
    Expected: correction alert + fallback daily report.
    Tracker locked as 'invalid_data_fallback'.
    """
    _section("PHASE 3 — 10:30 AM | Deadline fires, data still invalid → fallback report")

    grid_df = _make_grid_df_invalid_time()
    sp_mock = _make_sp_mock(grid_df)
    email_count_before = len(SENT_EMAILS)
    engine_calls_before = len(MASTER_ENGINE_CALLS)

    from app.services.scheduler_service import (
        run_daily_report_automation,
        tracker_is_locked_for_today,
        _tracker_was_sent_as_fallback,
        _daily_report_tracker,
        SCHEDULER_TRACKER_FILE,
    )
    _daily_report_tracker.clear()

    patches = _build_patches(sp_mock, tracker_file)
    with _apply_patches(patches):
        result = run_daily_report_automation(trigger_source="scheduler")
        _step(f"run_daily_report_automation result: {result}")

    emails_sent = SENT_EMAILS[email_count_before:]
    all_subjects = [e["subject"] for e in emails_sent]
    _step(f"Subjects sent this phase: {all_subjects}")

    correction_emails = [e for e in emails_sent
                         if "Fix Excel" in e["subject"] or "error" in e["subject"].lower()]
    report_emails = [e for e in emails_sent
                     if "Energy" in e["subject"] or "Dashboard" in e["subject"]
                     or "Optimization" in e["subject"] or "Review" in e["subject"]]

    _assert(len(correction_emails) >= 1,
            "Correction-alert email sent at deadline",
            f"subjects: {all_subjects}")
    _assert(len(report_emails) >= 1,
            "Fallback daily report email sent",
            f"subjects: {all_subjects}")

    _assert(len(MASTER_ENGINE_CALLS) == engine_calls_before,
            "Master engine NOT called (invalid data → fallback path, no merge)")

    # Verify tracker was locked as fallback
    with _apply_patches(patches):
        locked   = tracker_is_locked_for_today()
        fallback = _tracker_was_sent_as_fallback()

    _assert(locked,    "Tracker IS locked after fallback report")
    _assert(fallback,  "Tracker records trigger_source='invalid_data_fallback'")

    # Verify tracker.json on disk
    if tracker_file.exists():
        raw = json.loads(tracker_file.read_text())
        today_key = datetime.now(IST).strftime("%Y-%m-%d")
        entry = raw.get(today_key, {})
        _step(f"scheduler_tracker.json → {json.dumps(entry, indent=2)}")
        _assert(entry.get("trigger_source") == "invalid_data_fallback",
                "tracker.json trigger_source == 'invalid_data_fallback'",
                f"got {entry.get('trigger_source')!r}")
    else:
        _warn("tracker_file not written to disk (expected in real deployment)")

    if verbose:
        _print_captured_emails(emails_sent, label="Phase 3 emails")


def phase_4_late_correction(tracker_file: Path, verbose: bool) -> None:
    """
    ~11:00 AM — Operator fixed Time='10:30'. Late-check detects corrected data.
    Gate 1: report was sent ✓
    Gate 2: it was a fallback ✓
    Gate 3: data now valid and Done ✓
    Expected: master engine runs silently. NO new email sent.
    """
    _section("PHASE 4 — 11:00 AM | Operator fixes Time='10:30' → master engine runs silently")

    grid_df = _make_grid_df_valid()   # corrected data
    sp_mock = _make_sp_mock(grid_df)
    email_count_before  = len(SENT_EMAILS)
    engine_calls_before = len(MASTER_ENGINE_CALLS)

    from app.services.scheduler_service import (
        _run_late_data_check,
        validate_data_for_today,
        _daily_report_tracker,
    )
    # Do NOT clear _daily_report_tracker — Phase 3 locked it; late check needs that state

    patches = _build_patches(sp_mock, tracker_file)
    with _apply_patches(patches):
        # Confirm validator now passes
        validation = validate_data_for_today()
        _step(f"Validator after correction: valid={validation['valid']}")
        _assert(validation["valid"], "Validator now passes after Time='10:30' fix")

        # Run the late check
        _run_late_data_check()

    emails_sent_this_phase = SENT_EMAILS[email_count_before:]
    new_engine_calls = MASTER_ENGINE_CALLS[engine_calls_before:]

    _assert(len(emails_sent_this_phase) == 0,
            "NO new email sent during late correction (silent background update)",
            f"unexpected emails: {[e['subject'] for e in emails_sent_this_phase]}")
    _assert(len(new_engine_calls) >= 1,
            "Master data engine WAS called to merge corrected data",
            f"engine calls: {new_engine_calls}")

    if new_engine_calls:
        today_str = datetime.now(IST).strftime("%Y-%m-%d")
        _assert(new_engine_calls[0]["operator_date"] == today_str,
                f"Engine called with today's date ({today_str})",
                f"got {new_engine_calls[0]['operator_date']!r}")

    if verbose:
        _print_captured_emails(emails_sent_this_phase, label="Phase 4 emails (expected: none)")
        _step(f"Master engine calls this phase: {json.dumps(new_engine_calls, indent=2)}")


# ──────────────────────────────────────────────────────────────────────────────
# Full mailbox summary
# ──────────────────────────────────────────────────────────────────────────────
def print_mailbox_summary() -> None:
    _section("MAILBOX SUMMARY — all emails captured across all phases")
    if not SENT_EMAILS:
        _warn("No emails were captured.")
        return
    print(f"\n  {'#':<4} {'TIME':<8} {'SUBJECT':<60} {'TO'}")
    print(f"  {'─'*4} {'─'*8} {'─'*60} {'─'*30}")
    for i, email in enumerate(SENT_EMAILS, 1):
        to_str = ", ".join(str(t) for t in email.get("to", []))[:30]
        print(f"  {i:<4} {'—':<8} {email['subject'][:60]:<60} {to_str}")

    # Group by kind
    kinds: Dict[str, int] = {}
    for e in SENT_EMAILS:
        subj = e["subject"]
        if "Operator Data Missing" in subj or "data was found" in subj.lower():
            kinds["operator_reminder"] = kinds.get("operator_reminder", 0) + 1
        elif "Fix Excel" in subj or "error" in subj.lower():
            kinds["correction_alert"] = kinds.get("correction_alert", 0) + 1
        elif "Energy" in subj or "Dashboard" in subj or "Review" in subj or "Optimization" in subj:
            kinds["daily_report"] = kinds.get("daily_report", 0) + 1
        else:
            kinds["other"] = kinds.get("other", 0) + 1

    print(f"\n  Breakdown:")
    for kind, count in kinds.items():
        print(f"    {kind:<30} × {count}")


def print_log_summary(tracker_file: Path) -> None:
    _section("LOG FILE SUMMARY")
    log_file = tracker_file.parent / "scheduler_log.json"
    if log_file.exists():
        entries = json.loads(log_file.read_text())
        print(f"\n  scheduler_log.json  ({len(entries)} entries)")
        for e in entries:
            ts  = e.get("timestamp", "")[-8:]   # HH:MM:SS portion
            kind = e.get("kind", "unknown")[:28]
            status = e.get("status", "?")
            src    = e.get("trigger_source", "")
            notes  = (e.get("notes") or "")[:40]
            print(f"    [{ts}] {kind:<30} {status:<8} {src:<25} {notes}")
    else:
        _warn("scheduler_log.json not written (normal when using in-memory only).")

    if tracker_file.exists():
        print(f"\n  scheduler_tracker.json:")
        raw = json.loads(tracker_file.read_text())
        print(f"    {json.dumps(raw, indent=4)}")


# ──────────────────────────────────────────────────────────────────────────────
# Context-manager helper to apply a list of patches
# ──────────────────────────────────────────────────────────────────────────────
from contextlib import contextmanager

@contextmanager
def _apply_patches(patches):
    """Apply a list of (target, mock) pairs as nested context managers."""
    active = []
    try:
        for target, mock_obj in patches:
            p = patch(target, mock_obj)
            try:
                p.start()
                active.append(p)
            except AttributeError:
                # Some targets (like Path objects) can't be patched with patch();
                # they're injected by monkey-patching the module attribute directly.
                pass
        yield
    finally:
        for p in reversed(active):
            try:
                p.stop()
            except RuntimeError:
                pass


def _print_captured_emails(emails: List[Dict], label: str = "") -> None:
    if label:
        print(f"\n  --- {label} ---")
    if not emails:
        print("  (no emails)")
        return
    for i, e in enumerate(emails, 1):
        print(f"\n  Email #{i}")
        print(f"    To      : {e['to']}")
        print(f"    Subject : {e['subject']}")
        # Show first ~400 chars of body
        body_lines = [l for l in e["raw"].splitlines() if l.strip() and not l.startswith("Content-")]
        body_preview = "\n    ".join(body_lines[:12])
        print(f"    Body    : {body_preview[:400]}")


# ──────────────────────────────────────────────────────────────────────────────
# Minimal env setup so modules don't blow up on import
# ──────────────────────────────────────────────────────────────────────────────
def _setup_env() -> None:
    defaults = {
        "SMTP_SERVER":                 "smtp.gmail.com",
        "SMTP_PORT":                   "587",
        "EMAIL_FROM":                  "test@example.com",
        "EMAIL_PASSWORD":              "test_password",
        "OPERATOR_EMAIL":              "operator@example.com",
        "CC_EMAIL":                    "manager@example.com",
        "ADMIN_ALERT_EMAIL":           "admin@example.com",
        "SHAREPOINT_TENANT_ID":        "fake-tenant-id",
        "SHAREPOINT_CLIENT_ID":        "fake-client-id",
        "SHAREPOINT_CLIENT_SECRET":    "fake-secret",
        "SHAREPOINT_HOSTNAME":         "company.sharepoint.com",
        "SHAREPOINT_SITE_PATH":        "/Admin",
        "SHAREPOINT_DRIVE_NAME":       "Documents",
        "SHAREPOINT_BASE_FOLDER":      "/Energy",
        "SHAREPOINT_SITE_URL":         "https://company.sharepoint.com/sites/Admin",
        "SHAREPOINT_GRID_DIESEL_DRIVE_ID":    "fake-drive-id",
        "SHAREPOINT_GRID_DIESEL_FOLDER_PATH": "/Energy",
        "SHAREPOINT_GRID_DIESEL_FILE_NAME":   "grid_and_diesel.xlsx",
        "SHAREPOINT_MASTER_DATA_DRIVE_ID":    "fake-drive-id",
        "SHAREPOINT_MASTER_DATA_FOLDER_PATH": "/Energy",
        "SHAREPOINT_MASTER_DATA_FILE_NAME":   "master_data.xlsx",
        "SHAREPOINT_UNIFIED_SOLAR_DRIVE_ID":  "fake-drive-id",
        "SHAREPOINT_UNIFIED_SOLAR_FOLDER_PATH": "/Energy",
        "SHAREPOINT_UNIFIED_SOLAR_FILE_NAME": "unified_solar.xlsx",
        "GRID_RATE_INR_PER_KWH":       "7.11",
    }
    for key, val in defaults.items():
        os.environ.setdefault(key, val)


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────
PHASE_MAP = {
    1: ("9:00 AM — No data → reminder email",               phase_1_no_data),
    2: ("10:00 AM — Invalid Time='morning' → correction",   phase_2_invalid_time),
    3: ("10:30 AM — Deadline + fallback report",            phase_3_deadline_fallback),
    4: ("11:00 AM — Operator fixes data → engine (silent)", phase_4_late_correction),
}


def main():
    parser = argparse.ArgumentParser(
        description="End-to-end scheduler flow test (all mocked, no real SMTP/SharePoint).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--phase", "-p", type=int, action="append", dest="phases",
                        metavar="N", help="Phase(s) to run (1–4). Default: all.")
    parser.add_argument("--list",    "-l", action="store_true",
                        help="List phases and exit.")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print full email bodies and engine call details.")
    parser.add_argument("--load-env", action="store_true",
                        help="Load .env from project root before running.")
    args = parser.parse_args()

    if args.list:
        print("\nPhases:")
        for n, (desc, _) in PHASE_MAP.items():
            print(f"  {n}  {desc}")
        print()
        return

    if args.load_env:
        try:
            from dotenv import load_dotenv
            for candidate in [_BACKEND_ROOT, SCRIPT_DIR, SCRIPT_DIR.parent, _BACKEND_ROOT.parent]:
                ef = candidate / ".env"
                if ef.exists():
                    load_dotenv(dotenv_path=ef, override=False)
                    _info(f"Loaded {ef}")
                    break
        except ImportError:
            _warn("python-dotenv not installed — skipping .env load.")

    _setup_env()

    selected = sorted(set(args.phases)) if args.phases else list(PHASE_MAP.keys())
    bad = [n for n in selected if n not in PHASE_MAP]
    if bad:
        print(f"ERROR: Unknown phase(s): {bad}. Valid: {list(PHASE_MAP.keys())}")
        sys.exit(1)

    print(f"\n{chr(9552)*64}")
    print("  Energy Dashboard — End-to-End Scheduler Flow Test")
    print(f"  Phases      : {selected}")
    print(f"  Backend root: {_BACKEND_ROOT}")
    print(f"  Date        : {datetime.now(IST).strftime('%A, %d-%b-%Y %H:%M IST')}")
    print(f"{chr(9552)*64}")

    # Early import check
    try:
        import app.services.scheduler_service  # noqa
    except ModuleNotFoundError:
        print("")
        print("  ERROR: Cannot import app.services.scheduler_service")
        print(f"  Detected backend root : {_BACKEND_ROOT}")
        print("")
        print("  FIX: This script must live in your server/ folder")
        print("  (the folder that CONTAINS the app/ package).")
        print("")
        print("  On Windows, run:")
        print("    cd path\\to\\server")
        print("    python test_full_flow.py")
        print("")
        sys.exit(1)


    # Shared temp dir for tracker/log files (state shared across phases)
    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir    = Path(tmpdir) / "output"
        output_dir.mkdir()
        tracker_file  = output_dir / "scheduler_tracker.json"

        # Patch the module-level path constants before importing the module
        try:
            import app.services.scheduler_service as sched_mod
            sched_mod.SCHEDULER_TRACKER_FILE = tracker_file
            sched_mod.SCHEDULER_LOG_FILE     = output_dir / "scheduler_log.json"
            sched_mod.SCHEDULER_LOCK_DIR.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            _warn(f"Could not pre-patch scheduler paths: {e}")

        for phase_num in selected:
            desc, fn = PHASE_MAP[phase_num]
            try:
                fn(tracker_file=tracker_file, verbose=args.verbose)
            except Exception:
                _fail(f"Phase {phase_num} raised an unexpected exception:")
                traceback.print_exc()
                FAILED.append(f"Phase {phase_num} (exception)")

        if len(selected) > 1:
            print_mailbox_summary()
            print_log_summary(tracker_file)

    # Final tally
    _section(f"RESULTS — {len(PASSED)} passed, {len(FAILED)} failed")
    for p in PASSED: print(f"  ✅  {p}")
    for f in FAILED: print(f"  ❌  {f}")

    if FAILED:
        sys.exit(1)
    else:
        _ok("All assertions passed.")


if __name__ == "__main__":
    main()