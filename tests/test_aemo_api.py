"""Tests for the AEMO NEMWeb dispatch RRP client."""

from __future__ import annotations

import pytest

from custom_components.pricehawk.aemo_api import (
    build_test_dispatch_zip,
    parse_dispatch_zip_for_test,
    pick_latest_dispatch_file_for_test,
)


class TestPickLatestFile:
    def test_picks_latest_by_timestamp(self):
        html = """<html><body>
        <a href="PUBLIC_DISPATCHIS_202605012045_0000000000001_LEGACY.zip">old</a>
        <a href="PUBLIC_DISPATCHIS_202605012050_0000000000002_LEGACY.zip">new</a>
        <a href="PUBLIC_DISPATCHIS_202605012040_0000000000003_LEGACY.zip">older</a>
        </body></html>"""
        assert pick_latest_dispatch_file_for_test(html) == (
            "PUBLIC_DISPATCHIS_202605012050_0000000000002_LEGACY.zip"
        )

    def test_returns_none_when_no_files(self):
        assert pick_latest_dispatch_file_for_test("<html></html>") is None

    def test_ignores_other_zip_types(self):
        html = (
            '<a href="PUBLIC_TRADINGIS_202605012050_0_LEGACY.zip">trade</a>'
            '<a href="PUBLIC_DISPATCHIS_202605012050_1_LEGACY.zip">disp</a>'
        )
        assert pick_latest_dispatch_file_for_test(html) == (
            "PUBLIC_DISPATCHIS_202605012050_1_LEGACY.zip"
        )

    def test_matches_new_no_legacy_suffix_format(self):
        """AEMO retired the `_LEGACY` filename suffix in May 2026.
        Current filenames look like
        ``PUBLIC_DISPATCHIS_YYYYMMDDHHMM_NNNNNNNNNNNNNNNN.zip``.
        Picker must match these or DWT-AEMO + Flow Power AEMO poll dies.
        Regression test for live UAT 2026-05-23.
        """
        html = """<html><body>
        <a href="PUBLIC_DISPATCHIS_202605210140_0000000518621724.zip">old</a>
        <a href="PUBLIC_DISPATCHIS_202605210145_0000000518622229.zip">new</a>
        </body></html>"""
        assert pick_latest_dispatch_file_for_test(html) == (
            "PUBLIC_DISPATCHIS_202605210145_0000000518622229.zip"
        )

    def test_picks_latest_when_legacy_and_new_format_mixed(self):
        """During the AEMO transition window the directory could
        plausibly carry both. The newer timestamp wins regardless of
        whether it carries the `_LEGACY` suffix or not."""
        html = """<html><body>
        <a href="PUBLIC_DISPATCHIS_202605012050_1_LEGACY.zip">legacy older</a>
        <a href="PUBLIC_DISPATCHIS_202605210145_999.zip">new newer</a>
        </body></html>"""
        assert pick_latest_dispatch_file_for_test(html) == (
            "PUBLIC_DISPATCHIS_202605210145_999.zip"
        )


class TestParseDispatchZip:
    def test_extracts_rrp_for_requested_region(self):
        payload = build_test_dispatch_zip(
            [
                {"region": "NSW1", "rrp_dollars_per_mwh": 82.45},
                {"region": "QLD1", "rrp_dollars_per_mwh": 110.20},
                {"region": "VIC1", "rrp_dollars_per_mwh": 65.10},
            ]
        )
        result = parse_dispatch_zip_for_test(payload, "NSW1")
        assert result is not None
        rrp_c_kwh, settlement = result
        # 82.45 $/MWh = 8.245 c/kWh
        assert rrp_c_kwh == pytest.approx(8.245)
        assert settlement == "2026/05/01 12:00:00"

    def test_unit_conversion_dollars_per_mwh_to_cents_per_kwh(self):
        # 100 $/MWh = 10 c/kWh
        payload = build_test_dispatch_zip(
            [{"region": "VIC1", "rrp_dollars_per_mwh": 100.0}]
        )
        result = parse_dispatch_zip_for_test(payload, "VIC1")
        assert result is not None
        assert result[0] == pytest.approx(10.0)

    def test_negative_rrp_passes_through(self):
        payload = build_test_dispatch_zip(
            [{"region": "VIC1", "rrp_dollars_per_mwh": -50.0}]
        )
        result = parse_dispatch_zip_for_test(payload, "VIC1")
        assert result is not None
        assert result[0] == pytest.approx(-5.0)

    def test_missing_region_returns_none(self):
        payload = build_test_dispatch_zip(
            [{"region": "NSW1", "rrp_dollars_per_mwh": 82.45}]
        )
        assert parse_dispatch_zip_for_test(payload, "TAS1") is None

    def test_malformed_zip_returns_none(self):
        assert parse_dispatch_zip_for_test(b"not a zip", "NSW1") is None

    def test_takes_last_matching_row_when_multiple(self):
        # If a CSV has multiple D rows for the same region, the last one
        # is the most recent (interventions come after the base run).
        payload = build_test_dispatch_zip(
            [
                {"region": "NSW1", "rrp_dollars_per_mwh": 50.0},
                {"region": "NSW1", "rrp_dollars_per_mwh": 75.0},
            ]
        )
        result = parse_dispatch_zip_for_test(payload, "NSW1")
        assert result is not None
        assert result[0] == pytest.approx(7.5)


class TestRegionValidation:
    def test_invalid_region_raises(self):
        from custom_components.pricehawk.aemo_api import fetch_current_rrp

        async def run():
            class _FakeSession:
                async def get(self, *a, **kw):
                    raise AssertionError("should not be called")

            await fetch_current_rrp(_FakeSession(), "INVALID")

        import asyncio

        with pytest.raises(ValueError):
            asyncio.run(run())
