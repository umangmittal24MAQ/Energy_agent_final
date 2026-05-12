"""
Unit tests for:
  1. validate_data_for_today()  — all column validation rules
  2. send_data_correction_alert() — correction email sending

Run locally:
    cd server                          # your backend root
    pip install pytest pandas
    pytest tests/test_data_validation.py -v

No SharePoint, no SMTP, no Azure needed — everything is mocked.
"""

import os
import pytest
import smtplib
from datetime import date, datetime
from unittest.mock import MagicMock, patch, call
from zoneinfo import ZoneInfo

import pandas as pd

# ─────────────────────────────────────────────────────────────────────────────
# Helpers — build a minimal valid DataFrame row for today
# ─────────────────────────────────────────────────────────────────────────────

IST = ZoneInfo("Asia/Kolkata")

def _today_str() -> str:
    return datetime.now(IST).strftime("%d-%b-%Y")   # e.g. "05-May-2026"

def _today_day() -> str:
    return datetime.now(IST).strftime("%A")          # e.g. "Tuesday"

def _make_valid_row() -> dict:
    """A perfectly filled row — every column valid."""
    return {
        "Date":                          _today_str(),
        "Day":                           _today_day(),
        "Time":                          "10:30",
        "Ambient Temperature °C":        "28",
        "Grid Units Consumed (KWh)":     "4,452",
        "Solar Units Consumed(KWh)":     "1,013",
        "Total Units Consumed (KWh)":    "5,465",
        "Total Units Consumed in INR":   "31,653.72",
        "Energy Saving in INR":          "7,202.43",
        "Number of Panels Cleaned":      "156",
        "Diesel consumed":               "0",
        "Water treated through STP":     "26",
        "Water treated through WTP":     "34",
        "Issues":                        "No issues",
    }

def _make_df(row: dict) -> pd.DataFrame:
    """Wrap a single row dict into a DataFrame."""
    return pd.DataFrame([row])


# ─────────────────────────────────────────────────────────────────────────────
# Import the functions under test
# We patch sharepoint BEFORE importing so no real network calls happen.
# ─────────────────────────────────────────────────────────────────────────────

# We'll patch at call-time inside each test using the helpers below.

from app.services.scheduler_service import (
    validate_data_for_today,
    _validate_cell,
    _parse_numeric,
    _match_col,
    _COLUMN_RULES,
    VALID_DAYS,
)
from app.services.email_service import send_data_correction_alert


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 1 — Unit tests for individual validation helpers
# ═════════════════════════════════════════════════════════════════════════════

class TestParseNumeric:
    def test_plain_integer(self):
        assert _parse_numeric("4452") == 4452.0

    def test_comma_formatted(self):
        assert _parse_numeric("4,452") == 4452.0

    def test_decimal(self):
        assert _parse_numeric("31,653.72") == 31653.72

    def test_zero(self):
        assert _parse_numeric("0") == 0.0

    def test_invalid_text(self):
        assert _parse_numeric("abc") is None

    def test_time_like_string(self):
        # "21-37" should not parse as a number
        assert _parse_numeric("21-37") is None


class TestMatchCol:
    def test_matches_grid_units(self):
        assert _match_col("Grid Units Consumed (KWh)", ["grid", "unit"]) is True

    def test_matches_solar_units(self):
        assert _match_col("Solar Units Consumed(KWh)", ["solar", "unit"]) is True

    def test_no_match(self):
        assert _match_col("Issues", ["grid", "unit"]) is False

    def test_partial_match_not_enough(self):
        # Only one keyword matches — should return False
        assert _match_col("Grid Consumption", ["grid", "unit"]) is False


class TestValidateCell:
    # ── Date ──────────────────────────────────────────────────────────────────
    def test_valid_date(self):
        assert _validate_cell("Date", "05-May-2026", "date", {}) is None

    def test_invalid_date(self):
        err = _validate_cell("Date", "21-37", "date", {})
        assert err is not None
        assert "not a valid date" in err

    def test_empty_date_skipped(self):
        # Empty cells are skipped — missing-data logic handles them
        assert _validate_cell("Date", "", "date", {}) is None

    # ── Day ───────────────────────────────────────────────────────────────────
    def test_valid_day(self):
        assert _validate_cell("Day", "Tuesday", "day", {}) is None

    def test_valid_day_lowercase(self):
        assert _validate_cell("Day", "tuesday", "day", {}) is None

    def test_invalid_day(self):
        err = _validate_cell("Day", "Tuesdayy", "day", {})
        assert err is not None
        assert "not a valid day" in err

    def test_numeric_in_day_column(self):
        err = _validate_cell("Day", "21-37", "day", {})
        assert err is not None

    # ── Time ──────────────────────────────────────────────────────────────────
    def test_valid_time_hhmm(self):
        assert _validate_cell("Time", "10:30", "time", {}) is None

    def test_valid_time_with_seconds(self):
        # "10:30:00" should also be accepted
        assert _validate_cell("Time", "10:30:00", "time", {}) is None

    def test_invalid_time_dash(self):
        # The exact bug from today: "21-37" instead of "21:37"
        err = _validate_cell("Time", "21-37", "time", {})
        assert err is not None
        assert "not a valid time" in err

    def test_invalid_time_text(self):
        err = _validate_cell("Time", "morning", "time", {})
        assert err is not None

    def test_invalid_time_date_entered(self):
        err = _validate_cell("Time", "05-May-2026", "time", {})
        assert err is not None

    # ── Numeric ───────────────────────────────────────────────────────────────
    def test_valid_numeric_plain(self):
        assert _validate_cell("Grid Units", "4452", "numeric", {"min": 0, "max": 99999}) is None

    def test_valid_numeric_comma(self):
        assert _validate_cell("Grid Units", "4,452", "numeric", {"min": 0, "max": 99999}) is None

    def test_valid_numeric_decimal(self):
        assert _validate_cell("Cost", "31,653.72", "numeric", {"min": 0, "max": 9999999}) is None

    def test_numeric_below_min(self):
        err = _validate_cell("Grid Units", "-10", "numeric", {"min": 0, "max": 99999})
        assert err is not None
        assert "below minimum" in err

    def test_numeric_above_max(self):
        err = _validate_cell("Grid Units", "999999", "numeric", {"min": 0, "max": 99999})
        assert err is not None
        assert "exceeds maximum" in err

    def test_numeric_text_value(self):
        # Time-like value entered in numeric column
        err = _validate_cell("Grid Units", "21-37", "numeric", {"min": 0, "max": 99999})
        assert err is not None
        assert "not a valid number" in err

    def test_numeric_zero_allowed(self):
        assert _validate_cell("Diesel consumed", "0", "numeric", {"min": 0, "max": 10000}) is None

    # ── Temperature Range ─────────────────────────────────────────────────────
    def test_temp_range_valid_hyphen(self):
        # "21-37" means min 21°C, max 37°C — the real-world format
        assert _validate_cell("Ambient Temperature °C", "21-37", "temp_range", {"min": -10, "max": 60}) is None

    def test_temp_range_valid_single(self):
        # Single value also accepted
        assert _validate_cell("Ambient Temperature °C", "31", "temp_range", {"min": -10, "max": 60}) is None

    def test_temp_range_valid_negative_low(self):
        assert _validate_cell("Ambient Temperature °C", "-5-15", "temp_range", {"min": -10, "max": 60}) is None

    def test_temp_range_low_below_min(self):
        err = _validate_cell("Ambient Temperature °C", "-20-30", "temp_range", {"min": -10, "max": 60})
        assert err is not None
        assert "below minimum" in err

    def test_temp_range_high_above_max(self):
        err = _validate_cell("Ambient Temperature °C", "20-70", "temp_range", {"min": -10, "max": 60})
        assert err is not None
        assert "exceeds maximum" in err

    def test_temp_range_inverted(self):
        # Low > high is invalid
        err = _validate_cell("Ambient Temperature °C", "37-21", "temp_range", {"min": -10, "max": 60})
        assert err is not None
        assert "must be" in err

    def test_temp_range_invalid_text(self):
        err = _validate_cell("Ambient Temperature °C", "hot", "temp_range", {"min": -10, "max": 60})
        assert err is not None
        assert "not a valid temperature" in err

    def test_temp_range_single_below_min(self):
        err = _validate_cell("Ambient Temperature °C", "-999", "temp_range", {"min": -10, "max": 60})
        assert err is not None
        assert "below minimum" in err

    # ── Text ──────────────────────────────────────────────────────────────────
    def test_valid_text_issues(self):
        assert _validate_cell("Issues", "No issues", "text", {}) is None

    def test_valid_text_with_description(self):
        assert _validate_cell("Issues", "Panel 3 offline", "text", {}) is None


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 2 — Integration tests for validate_data_for_today()
# Patches SharePoint so no real network calls happen.
# ═════════════════════════════════════════════════════════════════════════════

def _patch_sharepoint(df: pd.DataFrame):
    """Helper: returns a context manager that makes get_service() return df."""
    mock_service = MagicMock()
    mock_service.fetch_sheet_data.return_value = df
    return patch(
        "app.services.sharepoint_data_service.get_service",  # patch source so local import picks it up
        return_value=mock_service,
    )


class TestValidateDataForToday:

    def _run(self, row: dict) -> dict:
        df = _make_df(row)
        mock_sp = MagicMock()
        mock_sp.fetch_sheet_data.return_value = df
        with patch(
            "app.services.sharepoint_data_service.get_service",
            return_value=mock_sp,
        ):
            return validate_data_for_today()

    # ── Happy path ────────────────────────────────────────────────────────────
    def test_all_valid_returns_valid(self):
        result = self._run(_make_valid_row())
        assert result["valid"] is True

    # ── Time errors ───────────────────────────────────────────────────────────
    def test_time_dash_format_caught(self):
        row = {**_make_valid_row(), "Time": "21-37"}
        result = self._run(row)
        assert result["valid"] is False
        cols = [e["column"] for e in result["errors"]]
        assert any("Time" in c or "time" in c.lower() for c in cols)

    def test_time_text_caught(self):
        row = {**_make_valid_row(), "Time": "morning"}
        result = self._run(row)
        assert result["valid"] is False

    # ── Date errors ───────────────────────────────────────────────────────────
    def test_invalid_date_caught(self):
        row = {**_make_valid_row(), "Date": "not-a-date"}
        result = self._run(row)
        assert result["valid"] is False

    # ── Day errors ────────────────────────────────────────────────────────────
    def test_wrong_day_name_caught(self):
        row = {**_make_valid_row(), "Day": "Tuesdayy"}
        result = self._run(row)
        assert result["valid"] is False

    # ── Numeric errors ────────────────────────────────────────────────────────
    def test_negative_grid_units_caught(self):
        row = {**_make_valid_row(), "Grid Units Consumed (KWh)": "-100"}
        result = self._run(row)
        assert result["valid"] is False

    def test_text_in_numeric_column_caught(self):
        row = {**_make_valid_row(), "Grid Units Consumed (KWh)": "21-37"}
        result = self._run(row)
        assert result["valid"] is False

    def test_comma_numeric_accepted(self):
        row = {**_make_valid_row(), "Grid Units Consumed (KWh)": "4,452"}
        result = self._run(row)
        assert result["valid"] is True

    # ── Multiple errors at once ───────────────────────────────────────────────
    def test_multiple_errors_all_reported(self):
        row = {
            **_make_valid_row(),
            "Time":                       "21-37",       # bad
            "Grid Units Consumed (KWh)":  "bad_value",   # bad
            "Ambient Temperature °C":     "-999",        # below temp_range min
        }
        result = self._run(row)
        assert result["valid"] is False
        assert len(result["errors"]) >= 3

    # ── Edge cases ────────────────────────────────────────────────────────────
    def test_empty_dataframe_returns_valid(self):
        """No rows → let missing-data logic handle it."""
        mock_sp = MagicMock()
        mock_sp.fetch_sheet_data.return_value = pd.DataFrame()
        with patch("app.services.sharepoint_data_service.get_service", return_value=mock_sp):
            result = validate_data_for_today()
        assert result["valid"] is True

    def test_sharepoint_crash_fails_open(self):
        """If SharePoint throws, validator should NOT block the report."""
        mock_sp = MagicMock()
        mock_sp.fetch_sheet_data.side_effect = Exception("SharePoint timeout")
        with patch("app.services.sharepoint_data_service.get_service", return_value=mock_sp):
            result = validate_data_for_today()
        assert result["valid"] is True  # fail-open


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 3 — Tests for send_data_correction_alert()
# SMTP is fully mocked — no real email sent.
# ═════════════════════════════════════════════════════════════════════════════

SAMPLE_ERRORS = [
    {"column": "Time",                     "value": "21-37",    "error": "'21-37' is not a valid time (expected HH:MM format, e.g. 10:30)"},
    {"column": "Grid Units Consumed (KWh)","value": "bad_value","error": "'bad_value' is not a valid number"},
    {"column": "Ambient Temperature °C",   "value": "-999",     "error": "'-999' is below minimum allowed value (-10)"},
]

ENV_VARS = {
    "SMTP_SERVER":    "smtp.gmail.com",
    "SMTP_PORT":      "587",
    "EMAIL_FROM":     "test@example.com",
    "EMAIL_PASSWORD": "testpassword",
    "OPERATOR_EMAIL": "operator@example.com",
    "CC_EMAIL":       "manager@example.com",
}


class TestSendDataCorrectionAlert:

    def _run_alert(self, errors=None, mock_smtp=None):
        """Run send_data_correction_alert with mocked SMTP and env vars."""
        if errors is None:
            errors = SAMPLE_ERRORS
        with patch.dict(os.environ, ENV_VARS):
            with patch("app.services.email_service._append_scheduler_send_history"):
                with patch("smtplib.SMTP") as mock_smtp_cls:
                    mock_smtp_cls.return_value.__enter__ = lambda s: mock_smtp or MagicMock()
                    mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)
                    result = send_data_correction_alert(errors)
        return result, mock_smtp_cls

    def test_returns_success(self):
        result, _ = self._run_alert()
        assert result["status"] == "Success"
        assert result["errors_reported"] == 3

    def test_smtp_called_once(self):
        _, mock_smtp_cls = self._run_alert()
        mock_smtp_cls.assert_called_once_with("smtp.gmail.com", 587, timeout=10)

    def test_email_contains_each_bad_value(self):
        """The email body must mention every bad value so the operator knows what to fix."""
        import base64
        captured_messages = []

        class FakeSMTP:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def starttls(self): pass
            def login(self, *a): pass
            def sendmail(self, frm, to, msg):
                captured_messages.append(msg)

        with patch.dict(os.environ, ENV_VARS):
            with patch("app.services.email_service._append_scheduler_send_history"):
                with patch("smtplib.SMTP", return_value=FakeSMTP()):
                    send_data_correction_alert(SAMPLE_ERRORS)

        assert len(captured_messages) == 1
        raw = captured_messages[0]
        # The HTML part is base64-encoded in a MIMEMultipart message.
        # Decode all base64 payloads and search the combined text.
        decoded_parts = []
        for line in raw.split("\n"):
            line = line.strip()
            if len(line) > 60 and all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=" for c in line):
                try:
                    decoded_parts.append(base64.b64decode(line).decode("utf-8", errors="ignore"))
                except Exception:
                    pass
        body = raw + "\n" + "\n".join(decoded_parts)
        assert "21-37"     in body
        assert "bad_value" in body
        assert "-999"      in body

    def test_email_subject_contains_error_count(self):
        captured = []

        class FakeSMTP:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def starttls(self): pass
            def login(self, *a): pass
            def sendmail(self, frm, to, msg):
                captured.append(msg)

        with patch.dict(os.environ, ENV_VARS):
            with patch("app.services.email_service._append_scheduler_send_history"):
                with patch("smtplib.SMTP", return_value=FakeSMTP()):
                    send_data_correction_alert(SAMPLE_ERRORS)

        assert "3" in captured[0]   # error count in subject or body

    def test_no_recipients_returns_failed(self):
        env = {**ENV_VARS, "OPERATOR_EMAIL": "", "CC_EMAIL": ""}
        with patch.dict(os.environ, env):
            result = send_data_correction_alert(SAMPLE_ERRORS)
        assert result["status"] == "Failed"
        assert "No operator email" in result["error"]

    def test_smtp_failure_returns_failed(self):
        with patch.dict(os.environ, ENV_VARS):
            with patch("app.services.email_service._append_scheduler_send_history"):
                with patch("smtplib.SMTP", side_effect=smtplib.SMTPException("Connection refused")):
                    result = send_data_correction_alert(SAMPLE_ERRORS)
        assert result["status"] == "Failed"
        assert "Connection refused" in result["error"]

    def test_history_logged_on_success(self):
        with patch.dict(os.environ, ENV_VARS):
            with patch("app.services.email_service._append_scheduler_send_history") as mock_log:
                with patch("smtplib.SMTP") as mock_smtp_cls:
                    mock_smtp_cls.return_value.__enter__ = lambda s: MagicMock()
                    mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)
                    send_data_correction_alert(SAMPLE_ERRORS)
            mock_log.assert_called_once()
            logged = mock_log.call_args[0][0]
            assert logged["status"] == "Success"
            assert logged["kind"] == "data_correction_alert"

    def test_single_error_still_works(self):
        single = [SAMPLE_ERRORS[0]]
        result, _ = self._run_alert(errors=single)
        assert result["status"] == "Success"
        assert result["errors_reported"] == 1


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 4 — Full flow test: bad data → validate → alert email
# ═════════════════════════════════════════════════════════════════════════════

class TestFullValidationToEmailFlow:

    def test_bad_time_triggers_correction_email(self):
        """
        End-to-end: bad Time in Excel → validate_data_for_today returns invalid
        → send_data_correction_alert is called with the right error.
        """
        row = {**_make_valid_row(), "Time": "21-37"}
        df  = _make_df(row)

        mock_sp = MagicMock()
        mock_sp.fetch_sheet_data.return_value = df

        captured_errors = []

        def fake_alert(errors):
            captured_errors.extend(errors)
            return {"status": "Success", "errors_reported": len(errors)}

        with patch("app.services.sharepoint_data_service.get_service", return_value=mock_sp):
            validation = validate_data_for_today()

        assert validation["valid"] is False

        with patch.dict(os.environ, ENV_VARS):
            with patch("app.services.email_service._append_scheduler_send_history"):
                with patch("smtplib.SMTP") as mock_smtp:
                    mock_smtp.return_value.__enter__ = lambda s: MagicMock()
                    mock_smtp.return_value.__exit__ = MagicMock(return_value=False)
                    result = send_data_correction_alert(validation["errors"])

        assert result["status"] == "Success"
        assert any("Time" in e["column"] for e in validation["errors"])
        assert any("21-37" in e["value"] for e in validation["errors"])

    def test_multiple_bad_columns_single_email(self):
        """
        Multiple bad columns → one email with all errors listed (not one per column).
        """
        row = {
            **_make_valid_row(),
            "Time":                       "21-37",
            "Grid Units Consumed (KWh)":  "bad_value",
        }
        df = _make_df(row)

        mock_sp = MagicMock()
        mock_sp.fetch_sheet_data.return_value = df

        with patch("app.services.sharepoint_data_service.get_service", return_value=mock_sp):
            validation = validate_data_for_today()

        assert validation["valid"] is False
        assert len(validation["errors"]) >= 2

        with patch.dict(os.environ, ENV_VARS):
            with patch("app.services.email_service._append_scheduler_send_history"):
                with patch("smtplib.SMTP") as mock_smtp:
                    mock_smtp.return_value.__enter__ = lambda s: MagicMock()
                    mock_smtp.return_value.__exit__ = MagicMock(return_value=False)
                    result = send_data_correction_alert(validation["errors"])

        # One email covers all errors
        assert result["errors_reported"] == len(validation["errors"])

    def test_valid_data_no_email_sent(self):
        """
        All columns valid → validate returns valid → alert should NOT be called.
        """
        df = _make_df(_make_valid_row())

        mock_sp = MagicMock()
        mock_sp.fetch_sheet_data.return_value = df

        with patch("app.services.sharepoint_data_service.get_service", return_value=mock_sp):
            validation = validate_data_for_today()

        assert validation["valid"] is True
        # If the caller respects valid=True, send_data_correction_alert is never called
        # (tested here by asserting no errors to report)
        assert "errors" not in validation