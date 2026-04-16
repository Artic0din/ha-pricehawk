"""Tests for config flow parsing and validation functions.

Tests the pure-Python helper functions used by the config flow,
not the HA config flow machinery itself (which requires a full HA test harness).
"""

from __future__ import annotations

from custom_components.pricehawk.config_flow import (
    _build_export_tariff,
    _build_import_tariff,
    _str_to_windows,
    _time_to_minutes,
    _validate_full_coverage,
    _validate_no_overlap,
    _windows_overlap,
    _windows_to_str,
)


# ---------------------------------------------------------------------------
# Window parsing: _str_to_windows
# ---------------------------------------------------------------------------

class TestStrToWindows:
    def test_single_window(self):
        result = _str_to_windows("16:00-23:00")
        assert result == [["16:00", "23:00"]]

    def test_multiple_windows(self):
        result = _str_to_windows("16:00-23:00, 14:00-16:00")
        assert result == [["16:00", "23:00"], ["14:00", "16:00"]]

    def test_empty_string(self):
        assert _str_to_windows("") == []

    def test_whitespace_handling(self):
        result = _str_to_windows("  16:00 - 23:00 ,  14:00 - 16:00  ")
        assert result == [["16:00", "23:00"], ["14:00", "16:00"]]

    def test_midnight_crossing(self):
        result = _str_to_windows("23:00-01:00")
        assert result == [["23:00", "01:00"]]

    def test_no_dash_ignored(self):
        """Entries without dashes are silently skipped."""
        result = _str_to_windows("invalid, 16:00-23:00")
        assert result == [["16:00", "23:00"]]


# ---------------------------------------------------------------------------
# Window formatting: _windows_to_str
# ---------------------------------------------------------------------------

class TestWindowsToStr:
    def test_single_window(self):
        assert _windows_to_str([["16:00", "23:00"]]) == "16:00-23:00"

    def test_multiple_windows(self):
        result = _windows_to_str([["16:00", "23:00"], ["14:00", "16:00"]])
        assert result == "16:00-23:00, 14:00-16:00"

    def test_empty_list(self):
        assert _windows_to_str([]) == ""

    def test_round_trip(self):
        """str -> windows -> str produces the same string."""
        original = "16:00-23:00, 14:00-16:00"
        assert _windows_to_str(_str_to_windows(original)) == original


# ---------------------------------------------------------------------------
# Time conversion
# ---------------------------------------------------------------------------

class TestTimeToMinutes:
    def test_midnight(self):
        assert _time_to_minutes("00:00") == 0

    def test_noon(self):
        assert _time_to_minutes("12:00") == 720

    def test_end_of_day(self):
        assert _time_to_minutes("23:59") == 1439

    def test_with_whitespace(self):
        assert _time_to_minutes("  16:00  ") == 960


# ---------------------------------------------------------------------------
# Overlap detection
# ---------------------------------------------------------------------------

class TestWindowsOverlap:
    def test_no_overlap(self):
        assert _windows_overlap(
            [["16:00", "23:00"]],
            [["11:00", "14:00"]],
        ) is False

    def test_overlap(self):
        assert _windows_overlap(
            [["15:00", "23:00"]],
            [["14:00", "16:00"]],
        ) is True

    def test_adjacent_no_overlap(self):
        """Adjacent windows (16:00-23:00 and 14:00-16:00) don't overlap."""
        assert _windows_overlap(
            [["16:00", "23:00"]],
            [["14:00", "16:00"]],
        ) is False

    def test_midnight_crossing_overlap(self):
        """23:00-01:00 and 00:00-06:00 overlap at midnight."""
        assert _windows_overlap(
            [["23:00", "01:00"]],
            [["00:00", "06:00"]],
        ) is True

    def test_empty_windows(self):
        assert _windows_overlap([], [["16:00", "23:00"]]) is False
        assert _windows_overlap([["16:00", "23:00"]], []) is False
        assert _windows_overlap([], []) is False


# ---------------------------------------------------------------------------
# Overlap validation (3-period)
# ---------------------------------------------------------------------------

class TestValidateNoOverlap:
    def test_clean_zerohero(self):
        """ZEROHERO windows don't overlap."""
        result = _validate_no_overlap(
            "16:00-23:00",                              # peak
            "23:00-00:00, 00:00-11:00, 14:00-16:00",   # shoulder
            "11:00-14:00",                              # offpeak
        )
        assert result is None

    def test_peak_shoulder_overlap(self):
        result = _validate_no_overlap(
            "15:00-23:00",         # peak overlaps with shoulder
            "14:00-16:00",         # shoulder
            "11:00-14:00",         # offpeak
        )
        assert result == "peak_shoulder_overlap"

    def test_peak_offpeak_overlap(self):
        result = _validate_no_overlap(
            "10:00-23:00",         # peak overlaps with offpeak
            "23:00-00:00",         # shoulder
            "11:00-14:00",         # offpeak
        )
        assert result == "peak_offpeak_overlap"

    def test_shoulder_offpeak_overlap(self):
        result = _validate_no_overlap(
            "16:00-23:00",         # peak
            "10:00-16:00",         # shoulder overlaps with offpeak
            "11:00-14:00",         # offpeak
        )
        assert result == "shoulder_offpeak_overlap"

    def test_empty_windows_no_overlap(self):
        """Empty windows produce no overlap."""
        assert _validate_no_overlap("", "", "") is None


# ---------------------------------------------------------------------------
# Tariff building: _build_import_tariff
# ---------------------------------------------------------------------------

class TestBuildImportTariff:
    def test_tou_tariff(self):
        user_input = {
            "peak_rate": 38.50,
            "peak_windows": "16:00-23:00",
            "shoulder_rate": 26.95,
            "shoulder_windows": "23:00-00:00, 00:00-11:00, 14:00-16:00",
            "offpeak_rate": 0.0,
            "offpeak_windows": "11:00-14:00",
        }
        result = _build_import_tariff("tou", user_input, "zerohero")
        assert result["type"] == "tou"
        assert "peak" in result["periods"]
        assert result["periods"]["peak"]["rate"] == 38.50
        assert result["periods"]["peak"]["windows"] == [["16:00", "23:00"]]

    def test_flat_stepped_tariff(self):
        user_input = {
            "step1_threshold_kwh": 25.0,
            "step1_rate": 21.67,
            "step2_rate": 25.30,
        }
        result = _build_import_tariff("flat_stepped", user_input, "boost")
        assert result["type"] == "flat_stepped"
        assert result["step1_threshold_kwh"] == 25.0
        assert result["step1_rate"] == 21.67
        assert result["step2_rate"] == 25.30


class TestBuildExportTariff:
    def test_export_tariff(self):
        user_input = {
            "export_peak_rate": 3.00,
            "export_peak_windows": "16:00-21:00",
            "export_shoulder_rate": 0.10,
            "export_shoulder_windows": "21:00-00:00, 00:00-10:00, 14:00-16:00",
            "export_offpeak_rate": 0.00,
            "export_offpeak_windows": "10:00-14:00",
        }
        result = _build_export_tariff(user_input, "zerohero")
        assert result["type"] == "tou"
        assert result["periods"]["peak"]["rate"] == 3.00
        assert result["periods"]["shoulder"]["rate"] == 0.10


# ---------------------------------------------------------------------------
# Full TOU coverage validation
# ---------------------------------------------------------------------------

class TestValidateFullCoverage:
    def test_validate_full_coverage_complete(self):
        """ZEROHERO windows cover all 48 half-hour slots."""
        assert _validate_full_coverage(
            "16:00-23:00",                              # peak
            "23:00-00:00, 00:00-11:00, 14:00-16:00",   # shoulder
            "11:00-14:00",                              # offpeak
        ) is True

    def test_validate_full_coverage_gap(self):
        """Missing 14:00-16:00 and 23:00-00:00 leaves gaps."""
        assert _validate_full_coverage(
            "16:00-23:00",   # peak
            "00:00-11:00",   # shoulder (missing 23:00-00:00 and 14:00-16:00)
            "11:00-14:00",   # offpeak
        ) is False

    def test_validate_full_coverage_empty(self):
        """All empty strings means zero coverage."""
        assert _validate_full_coverage("", "", "") is False
