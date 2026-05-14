"""Tests for config flow parsing and validation functions.

Tests the pure-Python helper functions used by the config flow,
not the HA config flow machinery itself (which requires a full HA test harness).
"""

from __future__ import annotations

import pytest

from custom_components.pricehawk.cdr.registry import RetailerEndpoint
from custom_components.pricehawk.config_flow import (
    CDR_ANY_DISTRIBUTOR_SENTINEL,
    CDR_SKIP_SENTINEL,
    STATE_DISTRIBUTORS,
    _build_cdr_plan_options,
    _build_cdr_retailer_options,
    _build_distributor_options,
    _build_export_tariff,
    _build_import_tariff,
    _build_state_options,
    _deep_merge_dict,
    _filter_plans_by_locale,
    _parse_override_json,
    _postcode_to_state,
    _str_to_windows,
    _summarise_cdr_plan,
    _summarise_fit,
    _summarise_import_rate,
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


# ---------------------------------------------------------------------------
# Phase 2.2 — CDR wizard helpers
# ---------------------------------------------------------------------------


class TestBuildCdrRetailerOptions:
    def test_skip_sentinel_first(self):
        endpoints = [
            RetailerEndpoint(brand_id="a", brand_name="AGL", base_uri="https://a"),
            RetailerEndpoint(brand_id="b", brand_name="Origin", base_uri="https://b"),
        ]
        options = _build_cdr_retailer_options(endpoints)
        assert options[0]["value"] == CDR_SKIP_SENTINEL
        assert "manually" in options[0]["label"].lower()

    def test_sorted_alphabetically_case_insensitive(self):
        endpoints = [
            RetailerEndpoint(brand_id="o", brand_name="Origin", base_uri="https://o"),
            RetailerEndpoint(brand_id="a", brand_name="agl", base_uri="https://a"),
            RetailerEndpoint(brand_id="r", brand_name="Red Energy", base_uri="https://r"),
        ]
        options = _build_cdr_retailer_options(endpoints)
        # Skip is index 0; brands at 1..N must be sorted case-insensitively.
        brand_labels = [o["label"] for o in options[1:]]
        assert brand_labels == ["agl", "Origin", "Red Energy"]

    def test_empty_endpoints_returns_just_skip(self):
        options = _build_cdr_retailer_options([])
        assert len(options) == 1
        assert options[0]["value"] == CDR_SKIP_SENTINEL


class TestBuildCdrPlanOptions:
    def test_basic_conversion(self):
        plans = [
            {
                "planId": "AGL123",
                "displayName": "AGL Value Saver Residential",
                "effectiveFrom": "2026-01-01T00:00:00Z",
            }
        ]
        options = _build_cdr_plan_options(plans)
        assert len(options) == 1
        assert options[0]["value"] == "AGL123"
        # Effective-from gets sliced to YYYY-MM-DD for human readability.
        assert "2026-01-01" in options[0]["label"]
        assert "Value Saver" in options[0]["label"]

    def test_filters_entries_missing_required_fields(self):
        plans = [
            {"planId": "OK", "displayName": "Plan A", "effectiveFrom": "2026-01-01"},
            {"planId": "", "displayName": "Plan B"},  # empty planId — dropped
            {"displayName": "Plan C"},  # no planId — dropped
            {"planId": "D"},  # no displayName — dropped
        ]
        options = _build_cdr_plan_options(plans)
        assert [o["value"] for o in options] == ["OK"]

    def test_sorted_by_display_name(self):
        plans = [
            {"planId": "Z", "displayName": "Zappy", "effectiveFrom": "2026-01-01"},
            {"planId": "A", "displayName": "Alpine", "effectiveFrom": "2026-01-01"},
            {"planId": "M", "displayName": "moderate", "effectiveFrom": "2026-01-01"},
        ]
        options = _build_cdr_plan_options(plans)
        # Case-insensitive sort: Alpine, moderate, Zappy
        labels = [o["label"] for o in options]
        assert labels[0].startswith("Alpine")
        assert labels[1].startswith("moderate")
        assert labels[2].startswith("Zappy")

    def test_missing_effective_from_renders_unknown(self):
        plans = [{"planId": "X", "displayName": "Plan X"}]
        options = _build_cdr_plan_options(plans)
        assert "?" in options[0]["label"]

    def test_empty_list_returns_empty(self):
        assert _build_cdr_plan_options([]) == []


# ---------------------------------------------------------------------------
# Phase 2.4 — Branch C audit field (CDR_SKIP_REASON_*) sanity
# ---------------------------------------------------------------------------


class TestCdrSkipReasonConstants:
    def test_skip_reasons_distinct(self):
        from custom_components.pricehawk.const import (
            CDR_SKIP_REASON_AFTER_ERROR,
            CDR_SKIP_REASON_NO_RETAILER,
            CDR_SKIP_REASON_RETRY_EXHAUSTED,
            CDR_SKIP_REASON_USER_AT_PLAN,
            CDR_SKIP_REASON_USER_AT_RETAILER,
        )
        reasons = {
            CDR_SKIP_REASON_USER_AT_RETAILER,
            CDR_SKIP_REASON_USER_AT_PLAN,
            CDR_SKIP_REASON_AFTER_ERROR,
            CDR_SKIP_REASON_RETRY_EXHAUSTED,
            CDR_SKIP_REASON_NO_RETAILER,
        }
        # 5 distinct values — each branch site is identifiable.
        assert len(reasons) == 5
        # All snake_case lowercase ascii — safe for JSON keys/logs.
        for r in reasons:
            assert r == r.lower()
            assert " " not in r

    def test_cdr_skip_reason_conf_key(self):
        from custom_components.pricehawk.const import CONF_CDR_SKIP_REASON
        assert CONF_CDR_SKIP_REASON == "cdr_skip_reason"


# ---------------------------------------------------------------------------
# Phase 2.5 — Override JSON deep-merge + parser
# ---------------------------------------------------------------------------


class TestDeepMergeDict:
    def test_disjoint_keys_merged_flat(self):
        base = {"a": 1, "b": 2}
        overlay = {"c": 3}
        assert _deep_merge_dict(base, overlay) == {"a": 1, "b": 2, "c": 3}

    def test_overlay_scalar_replaces_base_scalar(self):
        base = {"a": 1}
        overlay = {"a": 99}
        assert _deep_merge_dict(base, overlay) == {"a": 99}

    def test_nested_dicts_recurse(self):
        base = {"outer": {"inner": {"x": 1, "y": 2}}}
        overlay = {"outer": {"inner": {"x": 99}}}
        result = _deep_merge_dict(base, overlay)
        assert result == {"outer": {"inner": {"x": 99, "y": 2}}}

    def test_overlay_list_replaces_base_list(self):
        # Schemas like timeOfUse windows would be silently distorted if we
        # concatenated; replacement is the safer default.
        base = {"windows": [["00:00", "10:00"], ["10:00", "14:00"]]}
        overlay = {"windows": [["16:00", "21:00"]]}
        result = _deep_merge_dict(base, overlay)
        assert result == {"windows": [["16:00", "21:00"]]}

    def test_overlay_does_not_mutate_inputs(self):
        base = {"a": {"b": 1}}
        overlay = {"a": {"b": 2}}
        _deep_merge_dict(base, overlay)
        assert base == {"a": {"b": 1}}
        assert overlay == {"a": {"b": 2}}

    def test_base_unmatched_keys_survive(self):
        base = {"a": 1, "z": {"deep": "kept"}}
        overlay = {"a": 2}
        result = _deep_merge_dict(base, overlay)
        assert result["z"] == {"deep": "kept"}

    def test_type_mismatch_overlay_wins(self):
        # dict in base + scalar in overlay → overlay replaces (no merge).
        base = {"x": {"nested": 1}}
        overlay = {"x": "now a string"}
        result = _deep_merge_dict(base, overlay)
        assert result == {"x": "now a string"}


class TestParseOverrideJson:
    def test_empty_returns_none(self):
        assert _parse_override_json("") is None
        assert _parse_override_json("   ") is None
        assert _parse_override_json("\n\t") is None

    def test_valid_json_object_parsed(self):
        result = _parse_override_json('{"a": 1, "b": [2, 3]}')
        assert result == {"a": 1, "b": [2, 3]}

    def test_nested_object_parsed(self):
        result = _parse_override_json(
            '{"electricityContract": {"dailySupplyCharge": "1.20"}}'
        )
        assert result == {"electricityContract": {"dailySupplyCharge": "1.20"}}

    def test_invalid_json_raises_valueerror(self):
        import json
        with pytest.raises(json.JSONDecodeError):
            _parse_override_json("not json")

    def test_json_list_root_raises_valueerror(self):
        with pytest.raises(ValueError, match="object/dict"):
            _parse_override_json("[1, 2, 3]")

    def test_json_scalar_root_raises_valueerror(self):
        with pytest.raises(ValueError, match="object/dict"):
            _parse_override_json("42")


# ---------------------------------------------------------------------------
# Phase 2.8 — Locale + distributor filter
# ---------------------------------------------------------------------------


class TestPostcodeToState:
    def test_nsw_sydney_2000(self):
        assert _postcode_to_state("2000") == "NSW"

    def test_nsw_country_2480(self):
        assert _postcode_to_state("2480") == "NSW"

    def test_act_canberra_2601(self):
        # ACT range is tested BEFORE NSW so 2601 wins.
        assert _postcode_to_state("2601") == "ACT"

    def test_act_canberra_2615(self):
        assert _postcode_to_state("2615") == "ACT"

    def test_vic_melbourne_3000(self):
        assert _postcode_to_state("3000") == "VIC"

    def test_vic_po_box_8000(self):
        assert _postcode_to_state("8000") == "VIC"

    def test_qld_brisbane_4000(self):
        assert _postcode_to_state("4000") == "QLD"

    def test_sa_adelaide_5000(self):
        assert _postcode_to_state("5000") == "SA"

    def test_wa_perth_6000(self):
        assert _postcode_to_state("6000") == "WA"

    def test_tas_hobart_7000(self):
        assert _postcode_to_state("7000") == "TAS"

    def test_invalid_letters(self):
        assert _postcode_to_state("ABCD") is None

    def test_invalid_too_short(self):
        assert _postcode_to_state("20") is None

    def test_invalid_too_long(self):
        assert _postcode_to_state("20000") is None

    def test_whitespace_handled(self):
        assert _postcode_to_state(" 2000 ") == "NSW"

    def test_unmapped_range(self):
        # 0700 is not in any electricity state mapping.
        assert _postcode_to_state("0700") is None


class TestFilterPlansByLocale:
    def _plan(self, name: str) -> dict:
        return {"planId": name[:8], "displayName": name, "customerType": "RESIDENTIAL"}

    def test_no_filter_returns_all(self):
        plans = [self._plan("AGL Plan A NSW"), self._plan("AGL Plan B VIC")]
        result = _filter_plans_by_locale(plans, state=None, distributor=None)
        assert len(result) == 2

    def test_state_only_filter_keeps_matches(self):
        plans = [
            self._plan("AGL Residential Saver Ausgrid"),
            self._plan("AGL Residential Saver Powercor"),
            self._plan("AGL Business Plan Endeavour"),
        ]
        result = _filter_plans_by_locale(plans, state="NSW", distributor=None)
        # Ausgrid + Endeavour both NSW distributors → 2 hits.
        names = [p["displayName"] for p in result]
        assert "AGL Residential Saver Ausgrid" in names
        assert "AGL Business Plan Endeavour" in names
        assert "AGL Residential Saver Powercor" not in names

    def test_state_filter_matches_bare_state_code(self):
        plans = [
            self._plan("BOOST Residential NSW"),
            self._plan("BOOST Residential VIC"),
        ]
        result = _filter_plans_by_locale(plans, state="NSW", distributor=None)
        assert len(result) == 1
        assert result[0]["displayName"] == "BOOST Residential NSW"

    def test_distributor_only_filter(self):
        plans = [
            self._plan("AGL Saver Ausgrid"),
            self._plan("AGL Saver Endeavour"),
        ]
        result = _filter_plans_by_locale(
            plans, state=None, distributor="Ausgrid",
        )
        assert len(result) == 1
        assert "Ausgrid" in result[0]["displayName"]

    def test_state_and_distributor_intersect(self):
        plans = [
            self._plan("AGL Saver Ausgrid"),       # NSW + Ausgrid
            self._plan("AGL Saver Endeavour"),     # NSW + Endeavour
            self._plan("AGL Saver Powercor"),      # VIC + Powercor
        ]
        result = _filter_plans_by_locale(
            plans, state="NSW", distributor="Ausgrid",
        )
        assert len(result) == 1
        assert "Ausgrid" in result[0]["displayName"]

    def test_any_distributor_sentinel_treated_as_no_filter(self):
        plans = [
            self._plan("AGL Saver Ausgrid"),
            self._plan("AGL Saver Endeavour"),
        ]
        result = _filter_plans_by_locale(
            plans, state="NSW", distributor=CDR_ANY_DISTRIBUTOR_SENTINEL,
        )
        # State NSW matches both via distributor keywords.
        assert len(result) == 2

    def test_no_match_returns_empty(self):
        plans = [self._plan("AGL Saver Powercor")]
        result = _filter_plans_by_locale(plans, state="NSW", distributor=None)
        assert result == []


class TestStateDistributorOptions:
    def test_state_options_include_all_8(self):
        opts = _build_state_options()
        labels = [o["label"] for o in opts]
        # Skip + 7 states
        assert len(opts) == 8
        assert "Skip filter — show all plans" in labels
        for state_name in ["New South Wales", "Victoria", "Queensland", "South Australia",
                           "Tasmania", "Australian Capital Territory", "Western Australia"]:
            assert state_name in labels

    def test_distributor_options_for_nsw(self):
        opts = _build_distributor_options("NSW")
        values = [o["value"] for o in opts]
        # "Any" + 3 NSW distributors
        assert CDR_ANY_DISTRIBUTOR_SENTINEL in values
        assert "Ausgrid" in values
        assert "Endeavour" in values
        assert "Essential Energy" in values

    def test_distributor_options_for_unknown_state(self):
        opts = _build_distributor_options("XX")
        # Just the "Any" sentinel.
        assert len(opts) == 1
        assert opts[0]["value"] == CDR_ANY_DISTRIBUTOR_SENTINEL

    def test_distributor_options_none_state(self):
        opts = _build_distributor_options(None)
        assert len(opts) == 1

    def test_state_distributors_dict_completeness(self):
        # All 8 states have at least one known distributor.
        for state in ["NSW", "VIC", "QLD", "SA", "TAS", "ACT", "WA", "NT"]:
            assert state in STATE_DISTRIBUTORS
            assert len(STATE_DISTRIBUTORS[state]) >= 1


# ---------------------------------------------------------------------------
# Phase 2.9 — Plan-confirmation summary helper
# ---------------------------------------------------------------------------


class TestSummariseCdrPlan:
    def test_minimal_envelope(self):
        out = _summarise_cdr_plan({})
        assert out["brand"] == "?"
        assert out["plan_name"] == "?"

    def test_extracts_displayName_and_brand(self):
        detail = {"data": {
            "brandName": "GloBird Energy",
            "displayName": "ZEROHERO Residential",
            "effectiveFrom": "2026-03-31T00:00:00Z",
            "electricityContract": {},
        }}
        out = _summarise_cdr_plan(detail)
        assert out["brand"] == "GloBird Energy"
        assert out["plan_name"] == "ZEROHERO Residential"
        # Effective gets sliced to YYYY-MM-DD for legibility.
        assert out["effective"] == "2026-03-31"

    def test_daily_supply_converted_to_inc_gst_cents(self):
        # 1.05 $/day ex-GST = 1.155 $/day inc-GST = 115.50 c/day inc-GST
        detail = {"data": {"electricityContract": {"dailySupplyCharge": "1.05"}}}
        out = _summarise_cdr_plan(detail)
        assert "115.50" in out["daily_supply"]
        assert "inc-GST" in out["daily_supply"]

    def test_incentives_listed_with_overflow(self):
        detail = {"data": {"electricityContract": {
            "incentives": [
                {"displayName": "A"}, {"displayName": "B"},
                {"displayName": "C"}, {"displayName": "D"},
                {"displayName": "E"},
            ]
        }}}
        out = _summarise_cdr_plan(detail)
        assert "A, B, C" in out["incentives"]
        assert "+2 more" in out["incentives"]

    def test_no_incentives_renders_none(self):
        detail = {"data": {"electricityContract": {"incentives": []}}}
        out = _summarise_cdr_plan(detail)
        assert out["incentives"] == "none"

    def test_handles_non_dict_root(self):
        out = _summarise_cdr_plan("garbage")  # type: ignore[arg-type]
        assert out["brand"] == "?"


class TestSummariseImportRate:
    def test_tou_three_periods(self):
        elec = {"tariffPeriod": [
            {"type": "PEAK", "rates": [{"unitPrice": "0.36"}]},
            {"type": "SHOULDER", "rates": [{"unitPrice": "0.25"}]},
            {"type": "OFF_PEAK", "rates": [{"unitPrice": "0.0000001"}]},
        ]}
        result = _summarise_import_rate(elec)
        # 0.36 ex-GST × 110 = 39.6 c/kWh inc-GST
        assert "39.6" in result
        assert "27.5" in result
        assert "OFF_PEAK" in result

    def test_single_rate_flat(self):
        elec = {"singleRate": {"rates": [{"unitPrice": "0.30"}]}}
        result = _summarise_import_rate(elec)
        assert "Flat" in result
        assert "33.00" in result

    def test_no_rate_returns_q(self):
        assert _summarise_import_rate({}) == "?"


class TestSummariseFit:
    def test_single_tariff(self):
        elec = {"solarFeedInTariff": [
            {"singleTariff": {"rates": [{"unitPrice": "0.05"}]}}
        ]}
        result = _summarise_fit(elec)
        # 0.05 × 110 = 5.50 c/kWh inc-GST
        assert "5.50" in result

    def test_multiple_blocks_summed(self):
        elec = {"solarFeedInTariff": [
            {"singleTariff": {"rates": [{"unitPrice": "0.05"}]}},
            {"singleTariff": {"rates": [{"unitPrice": "0.03"}]}},
        ]}
        result = _summarise_fit(elec)
        assert "5.50" in result
        assert "3.30" in result

    def test_empty_returns_none(self):
        assert _summarise_fit({}) == "none"

    def test_tou_block_falls_back_to_note(self):
        elec = {"solarFeedInTariff": [{"timeVaryingTariffs": [{"rates": []}]}]}
        result = _summarise_fit(elec)
        assert "structured TOU" in result
