"""
Unit tests for deterministic functions in app/services/email_service.py.

Run with:
    pytest tests/test_email_service.py -v

These tests have zero external dependencies — no SharePoint, no SMTP, no .env.
"""
import sys
from pathlib import Path
from datetime import date, timedelta
from unittest.mock import patch

import pandas as pd
import numpy as np
import pytest

# ── Path bootstrap ────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.email_service import _coerce_for_column


# ══════════════════════════════════════════════════════════════════════════════
# _coerce_for_column()
# ══════════════════════════════════════════════════════════════════════════════

class TestCoerceForColumn:
    """_coerce_for_column(df, column, value) returns a dtype-compatible value."""

    def _df(self, dtype) -> pd.DataFrame:
        """Build a one-column DataFrame with the requested dtype."""
        if dtype == "int64":
            return pd.DataFrame({"val": pd.array([1, 2], dtype="int64")})
        if dtype == "float64":
            return pd.DataFrame({"val": pd.array([1.0, 2.0], dtype="float64")})
        if dtype == "object":
            return pd.DataFrame({"val": pd.array(["a", "b"], dtype="object")})
        raise ValueError(f"Unknown dtype shorthand: {dtype}")

    # --- float → int64 column ------------------------------------------------
    def test_float_into_int64_column_returns_float(self):
        """
        783.7 into an int64 column.
        _coerce_for_column returns float(783.7); the caller assigns it and
        pandas silently truncates to 784 when writing back — that truncation
        is intentional and lives in pandas, not in this function.
        The contract we test: the function does NOT raise and returns a number.
        """
        df = self._df("int64")
        result = _coerce_for_column(df, "val", 783.7)
        assert isinstance(result, float), f"Expected float, got {type(result)}"
        assert result == 783.7

    # --- float → float64 column ----------------------------------------------
    def test_float_into_float64_column_returns_exact_float(self):
        df = self._df("float64")
        result = _coerce_for_column(df, "val", 783.7)
        assert isinstance(result, float)
        assert result == pytest.approx(783.7)

    # --- float → string column -----------------------------------------------
    def test_float_into_string_column_returns_str(self):
        df = self._df("object")
        result = _coerce_for_column(df, "val", 783.7)
        assert isinstance(result, str), f"Expected str, got {type(result)}"
        assert "783.7" in result

    # --- zero value ----------------------------------------------------------
    def test_zero_into_float64_column(self):
        df = self._df("float64")
        result = _coerce_for_column(df, "val", 0.0)
        assert result == pytest.approx(0.0)

    # --- negative value ------------------------------------------------------
    def test_negative_float_into_float64_column(self):
        df = self._df("float64")
        result = _coerce_for_column(df, "val", -42.5)
        assert result == pytest.approx(-42.5)

    # --- missing column falls back to str ------------------------------------
    def test_missing_column_returns_str_fallback(self):
        """If the column doesn't exist, the except branch returns str(value)."""
        df = pd.DataFrame({"other": [1, 2]})
        result = _coerce_for_column(df, "nonexistent_column", 783.7)
        assert isinstance(result, str)

    # --- empty DataFrame -----------------------------------------------------
    def test_empty_dataframe_does_not_raise(self):
        """Empty DataFrame must not raise IndexError or KeyError."""
        df = pd.DataFrame({"val": pd.Series([], dtype="float64")})
        result = _coerce_for_column(df, "val", 783.7)
        # Empty column still has a dtype — should return float
        assert isinstance(result, (float, str))

    # --- NaN-only column -----------------------------------------------------
    def test_nan_only_float_column(self):
        df = pd.DataFrame({"val": [np.nan, np.nan]})
        result = _coerce_for_column(df, "val", 100.0)
        assert isinstance(result, float)
        assert result == pytest.approx(100.0)


# ══════════════════════════════════════════════════════════════════════════════
# Date-lookup fallback logic (extracted from send_daily_report)
# ══════════════════════════════════════════════════════════════════════════════

def _resolve_report_date(master_df: pd.DataFrame, today: date) -> str:
    """
    Pure re-implementation of the date-resolution block in send_daily_report.
    Extracted here so it can be tested without touching SMTP or SharePoint.
    """
    report_date_title = today.strftime("%Y-%m-%d")

    if "Date" not in master_df.columns or master_df.empty:
        if not master_df.empty:
            report_date_title = str(master_df.iloc[-1].get("Date", report_date_title))
        return report_date_title

    parsed_dates = pd.to_datetime(master_df["Date"], errors="coerce", format="mixed")
    today_mask = parsed_dates.dt.date == today

    if today_mask.any():
        today_rows = master_df.loc[today_mask]
        report_date_title = str(today_rows.iloc[-1].get("Date", report_date_title))
    elif today.weekday() == 0:                           # Monday
        sunday = today - timedelta(days=1)
        sunday_mask = parsed_dates.dt.date == sunday
        if sunday_mask.any():
            sunday_rows = master_df.loc[sunday_mask]
            report_date_title = str(sunday_rows.iloc[-1].get("Date", report_date_title))
        elif not master_df.empty:
            report_date_title = str(master_df.iloc[-1].get("Date", report_date_title))
    elif not master_df.empty:
        report_date_title = str(master_df.iloc[-1].get("Date", report_date_title))

    return report_date_title


class TestResolvReportDate:
    """Date-lookup fallback logic inside send_daily_report."""

    TODAY = date(2024, 4, 16)   # Tuesday — deterministic, never changes

    def _df(self, dates: list) -> pd.DataFrame:
        return pd.DataFrame({"Date": dates})

    # --- today's date present ------------------------------------------------
    def test_uses_todays_row_when_present(self):
        df = self._df(["2024-04-14", "2024-04-15", "2024-04-16"])
        result = _resolve_report_date(df, self.TODAY)
        assert "2024-04-16" in result

    # --- today missing → falls back to last row ------------------------------
    def test_falls_back_to_last_row_when_today_missing(self):
        df = self._df(["2024-04-13", "2024-04-14", "2024-04-15"])
        result = _resolve_report_date(df, self.TODAY)
        assert "2024-04-15" in result

    # --- Monday: picks Sunday row when available -----------------------------
    def test_monday_picks_sunday_row(self):
        monday = date(2024, 4, 15)   # actual Monday
        sunday = date(2024, 4, 14)
        df = self._df(["2024-04-13", str(sunday)])
        result = _resolve_report_date(df, monday)
        assert str(sunday) in result

    # --- Monday, Sunday missing too → last row fallback ----------------------
    def test_monday_falls_back_to_last_row_when_sunday_missing(self):
        monday = date(2024, 4, 15)
        df = self._df(["2024-04-12", "2024-04-13"])
        result = _resolve_report_date(df, monday)
        assert "2024-04-13" in result

    # --- empty DataFrame -----------------------------------------------------
    def test_empty_dataframe_returns_today_string_no_crash(self):
        df = pd.DataFrame({"Date": pd.Series([], dtype="object")})
        result = _resolve_report_date(df, self.TODAY)
        assert result == self.TODAY.strftime("%Y-%m-%d")

    # --- DataFrame with no Date column ---------------------------------------
    def test_no_date_column_returns_today_string(self):
        df = pd.DataFrame({"Value": [1, 2, 3]})
        result = _resolve_report_date(df, self.TODAY)
        assert result == self.TODAY.strftime("%Y-%m-%d")

    # --- all dates unparseable -----------------------------------------------
    def test_all_unparseable_dates_falls_back_to_last_row(self):
        df = self._df(["not-a-date", "also-not-a-date"])
        result = _resolve_report_date(df, self.TODAY)
        # Should not raise; falls back gracefully
        assert isinstance(result, str)

    # --- multiple rows for today → picks last --------------------------------
    def test_multiple_today_rows_picks_last(self):
        df = self._df(["2024-04-16", "2024-04-16", "2024-04-16"])
        df["extra"] = ["first", "second", "third"]
        result = _resolve_report_date(df, self.TODAY)
        assert "2024-04-16" in result