"""Tests for NEM12 CSV parsing."""

from __future__ import annotations

from custom_components.pricehawk.csv_analyzer import parse_nem12_text


def _nem12_minimal(suffix: str = "E1", date: str = "20260501") -> str:
    """Build a tiny NEM12 file with one 30-min interval data row."""
    intervals = ",".join(f"{0.5 + i * 0.01:.4f}" for i in range(48))
    return "\n".join(
        [
            "100,NEM12,202605012359,RETAILER,RETAILER",
            f"200,1234567890,E1Q1,1,{suffix},NA,SERIAL1,KWH,30,",
            f"300,{date},{intervals},A,,,2026/05/01 23:59:00,2026/05/02 00:00:00",
            "900",
        ]
    )


class TestSingleChannel:
    def test_parses_48_import_intervals(self):
        rows = parse_nem12_text(_nem12_minimal("E1"))
        assert len(rows) == 48
        assert all(r["channel"] == "general" for r in rows)
        assert all(r["day"] == "2026-05-01" for r in rows)

    def test_first_interval_starts_at_midnight(self):
        rows = parse_nem12_text(_nem12_minimal("E1"))
        assert rows[0]["start_time"] == "2026-05-01 00:00:00"

    def test_last_interval_starts_at_2330(self):
        rows = parse_nem12_text(_nem12_minimal("E1"))
        assert rows[-1]["start_time"] == "2026-05-01 23:30:00"

    def test_kwh_values_extracted_in_order(self):
        rows = parse_nem12_text(_nem12_minimal("E1"))
        assert rows[0]["usage"] == 0.5
        assert rows[1]["usage"] == 0.51

    def test_no_price_or_cost_in_nem12(self):
        rows = parse_nem12_text(_nem12_minimal("E1"))
        assert all(r["price"] == 0.0 for r in rows)
        assert all(r["cost"] == 0.0 for r in rows)


class TestExportSuffixes:
    def test_b1_maps_to_feedin(self):
        rows = parse_nem12_text(_nem12_minimal("B1"))
        assert all(r["channel"] == "feedIn" for r in rows)

    def test_q1_maps_to_feedin(self):
        rows = parse_nem12_text(_nem12_minimal("Q1"))
        assert all(r["channel"] == "feedIn" for r in rows)

    def test_unknown_suffix_skipped(self):
        rows = parse_nem12_text(_nem12_minimal("X9"))
        assert rows == []


class TestMultiChannel:
    def test_e1_then_b1_yields_both_channels(self):
        intervals_e1 = ",".join("0.5" for _ in range(48))
        intervals_b1 = ",".join("0.3" for _ in range(48))
        text = "\n".join(
            [
                "200,12345,E1Q1,1,E1,NA,SERIAL,KWH,30,",
                f"300,20260501,{intervals_e1},A",
                "200,12345,E1Q1,1,B1,NA,SERIAL,KWH,30,",
                f"300,20260501,{intervals_b1},A",
            ]
        )
        rows = parse_nem12_text(text)
        general = [r for r in rows if r["channel"] == "general"]
        feed_in = [r for r in rows if r["channel"] == "feedIn"]
        assert len(general) == 48
        assert len(feed_in) == 48
        assert general[0]["usage"] == 0.5
        assert feed_in[0]["usage"] == 0.3


class TestEdgeCases:
    def test_empty_text(self):
        assert parse_nem12_text("") == []

    def test_whitespace_only(self):
        assert parse_nem12_text("\n\n  \n") == []

    def test_300_without_preceding_200_skipped(self):
        # Without a 200 record we don't know which channel — skip.
        intervals = ",".join("0.5" for _ in range(48))
        text = f"300,20260501,{intervals},A"
        assert parse_nem12_text(text) == []

    def test_negative_values_clamped_to_zero(self):
        # Some retailer exports use negative values for export-side rows;
        # PriceHawk treats kWh as a magnitude.
        intervals = "-1.5," + ",".join("0.5" for _ in range(47))
        text = "\n".join(
            [
                "200,12345,E1Q1,1,E1,NA,SERIAL,KWH,30,",
                f"300,20260501,{intervals},A",
            ]
        )
        rows = parse_nem12_text(text)
        assert rows[0]["usage"] == 0.0  # clamped

    def test_15min_interval_produces_96_rows(self):
        intervals = ",".join("0.25" for _ in range(96))
        text = "\n".join(
            [
                "200,12345,E1Q1,1,E1,NA,SERIAL,KWH,15,",
                f"300,20260501,{intervals},A",
            ]
        )
        rows = parse_nem12_text(text)
        assert len(rows) == 96
        # Second interval starts at 00:15
        assert rows[1]["start_time"] == "2026-05-01 00:15:00"

    def test_partial_row_stops_at_quality_flag(self):
        # Row with only 24 numeric values then a quality flag — parser
        # should stop at the flag without crashing.
        text = "\n".join(
            [
                "200,12345,E1Q1,1,E1,NA,SERIAL,KWH,30,",
                "300,20260501," + ",".join("0.5" for _ in range(24)) + ",A,,,",
            ]
        )
        rows = parse_nem12_text(text)
        assert len(rows) == 24

    def test_invalid_date_skipped(self):
        intervals = ",".join("0.5" for _ in range(48))
        text = "\n".join(
            [
                "200,12345,E1Q1,1,E1,NA,SERIAL,KWH,30,",
                f"300,202605,{intervals},A",  # date too short
            ]
        )
        assert parse_nem12_text(text) == []


class TestMultiDay:
    def test_two_days_of_data(self):
        intervals = ",".join("0.5" for _ in range(48))
        text = "\n".join(
            [
                "200,12345,E1Q1,1,E1,NA,SERIAL,KWH,30,",
                f"300,20260501,{intervals},A",
                f"300,20260502,{intervals},A",
            ]
        )
        rows = parse_nem12_text(text)
        assert len(rows) == 96
        days = sorted({r["day"] for r in rows})
        assert days == ["2026-05-01", "2026-05-02"]
