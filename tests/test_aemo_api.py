"""Tests for the AEMO NEMWeb dispatch RRP client."""

from __future__ import annotations

import asyncio
import io
import zipfile
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from custom_components.pricehawk.aemo_api import (
    build_test_dispatch_zip,
    fetch_current_rrp,
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

    def test_matches_real_nemweb_uppercase_href_with_path_prefix(self):
        """Live UAT 2026-05-24 regression: the real NEMWeb directory
        listing serves filenames with the full server path prefix and
        an UPPERCASE ``HREF=`` attribute, e.g.

            <A HREF="/Reports/CURRENT/DispatchIS_Reports/PUBLIC_DISPATCHIS_202605221100_0000000518835834.zip">

        Prior regex was case-insensitive but required ``PUBLIC_DISPATCHIS``
        to sit immediately after the opening quote — so it matched zero
        files for two days even after PR #107 made the ``_LEGACY`` suffix
        optional. Both the AEMO-Direct DWT provider and Flow Power's AEMO
        poll were silently broken in production. The fix accepts an
        arbitrary path prefix via ``[^"]*?`` between the quote and the
        filename while still capturing only the filename in group 1.
        """
        html = """<html><body>
        <A HREF="/Reports/CURRENT/DispatchIS_Reports/PUBLIC_DISPATCHIS_202605221100_0000000518835834.zip">old</A><br>
        <A HREF="/Reports/CURRENT/DispatchIS_Reports/PUBLIC_DISPATCHIS_202605221110_0000000518837602.zip">new</A><br>
        </body></html>"""
        assert pick_latest_dispatch_file_for_test(html) == (
            "PUBLIC_DISPATCHIS_202605221110_0000000518837602.zip"
        )

    def test_matches_real_nemweb_mixed_case_and_path_prefix(self):
        """Mixed case + path prefix in the same listing (defensive)."""
        html = """<html><body>
        <a href="/relative/path/PUBLIC_DISPATCHIS_202605012050_1.zip">lower path</a>
        <A HREF="https://example.com/abs/PUBLIC_DISPATCHIS_202605210145_999.zip">upper abs</A>
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
        payload = build_test_dispatch_zip([{"region": "VIC1", "rrp_dollars_per_mwh": 100.0}])
        result = parse_dispatch_zip_for_test(payload, "VIC1")
        assert result is not None
        assert result[0] == pytest.approx(10.0)

    def test_negative_rrp_passes_through(self):
        payload = build_test_dispatch_zip([{"region": "VIC1", "rrp_dollars_per_mwh": -50.0}])
        result = parse_dispatch_zip_for_test(payload, "VIC1")
        assert result is not None
        assert result[0] == pytest.approx(-5.0)

    def test_missing_region_returns_none(self):
        payload = build_test_dispatch_zip([{"region": "NSW1", "rrp_dollars_per_mwh": 82.45}])
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

    def test_does_not_pick_up_regionsum_totaldemand_as_rrp(self):
        """Live UAT 2026-05-24: the parser previously matched on
        ``D,DISPATCH,REGIONSUM`` rows where index 9 is ``TOTALDEMAND``
        (in MW), not RRP. Real VIC1 dispatch had TOTALDEMAND ~5738 MW;
        ``5738/10 = 573.8`` showed up as "c/kWh" — a ~60x inflation that
        broke ``today_cost``. This test wires a synthetic CSV with BOTH
        record types (RRP=10 $/MWh, TOTALDEMAND=5000 MW) and asserts the
        parser picks the RRP, not the demand.
        """
        # Hand-build a CSV with both REGIONSUM (containing a demand value
        # at index 9 that would look plausible as a c/kWh price if mis-
        # read) and PRICE (containing the real RRP).
        import io as _io
        import zipfile as _zipfile

        csv_text = (
            "C,NEMP.WORLD,DISPATCHIS,AEMO,PUBLIC,2026/05/01,test,test,test,1\n"
            "I,DISPATCH,REGIONSUM,9,SETTLEMENTDATE,RUNNO,REGIONID,"
            "DISPATCHINTERVAL,INTERVENTION,TOTALDEMAND,AVAILABLEGENERATION\n"
            "D,DISPATCH,REGIONSUM,9,"
            '"2026/05/01 12:00:00",1,"VIC1",159000,0,5000.0,8500.0\n'
            "I,DISPATCH,PRICE,5,SETTLEMENTDATE,RUNNO,REGIONID,"
            "DISPATCHINTERVAL,INTERVENTION,RRP,EEP,ROP,APCFLAG,"
            "MARKETSUSPENDEDFLAG,LASTCHANGED\n"
            "D,DISPATCH,PRICE,5,"
            '"2026/05/01 12:00:00",1,"VIC1",159000,0,'
            '100.0,0,100.0,0,0,"2026/05/01 11:55:07"\n'
        ).encode("utf-8")
        buf = _io.BytesIO()
        with _zipfile.ZipFile(buf, "w") as z:
            z.writestr("PUBLIC_DISPATCHIS_TEST.CSV", csv_text)
        payload = buf.getvalue()

        result = parse_dispatch_zip_for_test(payload, "VIC1")
        assert result is not None
        rrp_c_kwh, _ = result
        # 100 $/MWh = 10 c/kWh. Must NOT be 500 (which would be
        # 5000 MW TOTALDEMAND / 10 — the prior bug).
        assert rrp_c_kwh == pytest.approx(10.0), (
            f"Parser must read RRP from PRICE row (10 c/kWh), not "
            f"TOTALDEMAND from REGIONSUM row (would be 500 c/kWh). "
            f"Got {rrp_c_kwh} c/kWh."
        )

    def test_real_nemweb_dispatch_csv_shape(self):
        """Defensive: a tiny but realistic CSV with the actual NEMWeb
        record types and order, matching what the live directory ships
        (REGIONSUM rows precede PRICE rows in real files).
        """
        import io as _io
        import zipfile as _zipfile

        csv_text = (
            "C,NEMP.WORLD,DISPATCHIS,AEMO,PUBLIC,2026/05/24,X,Y,Z,1\n"
            "I,DISPATCH,REGIONSUM,9,SETTLEMENTDATE,RUNNO,REGIONID,"
            "DISPATCHINTERVAL,INTERVENTION,TOTALDEMAND,X\n"
            'D,DISPATCH,REGIONSUM,9,"2026/05/24 15:40:00",1,"VIC1",'
            "20260524140,0,5738.11,9337.32806\n"
            'D,DISPATCH,REGIONSUM,9,"2026/05/24 15:40:00",1,"NSW1",'
            "20260524140,0,8500.50,12000.00\n"
            "I,DISPATCH,PRICE,5,SETTLEMENTDATE,RUNNO,REGIONID,"
            "DISPATCHINTERVAL,INTERVENTION,RRP,EEP,ROP,APCFLAG,"
            "MARKETSUSPENDEDFLAG,LASTCHANGED\n"
            'D,DISPATCH,PRICE,5,"2026/05/24 15:40:00",1,"VIC1",'
            '20260524140,0,96.16181,0,96.16181,0,0,"2026/05/24 15:35:07"\n'
            'D,DISPATCH,PRICE,5,"2026/05/24 15:40:00",1,"NSW1",'
            '20260524140,0,75.0,0,75.0,0,0,"2026/05/24 15:35:07"\n'
        ).encode("utf-8")
        buf = _io.BytesIO()
        with _zipfile.ZipFile(buf, "w") as z:
            z.writestr("PUBLIC_DISPATCHIS_TEST.CSV", csv_text)
        payload = buf.getvalue()

        vic_result = parse_dispatch_zip_for_test(payload, "VIC1")
        assert vic_result is not None
        assert vic_result[0] == pytest.approx(9.616181), (
            f"VIC1: 96.16181 $/MWh = 9.616181 c/kWh. Got {vic_result[0]}."
        )

        nsw_result = parse_dispatch_zip_for_test(payload, "NSW1")
        assert nsw_result is not None
        assert nsw_result[0] == pytest.approx(7.5)


class TestRegionValidation:
    def test_invalid_region_raises(self):
        async def run():
            class _FakeSession:
                async def get(self, *a, **kw):
                    raise AssertionError("should not be called")

            await fetch_current_rrp(_FakeSession(), "INVALID")

        with pytest.raises(ValueError):
            asyncio.run(run())


# ---------------------------------------------------------------------------
# Async fetch helpers — exercise lines 83-96, 105-125, 148-171
# ---------------------------------------------------------------------------


def _make_resp(status: int, text: str | None = None, body: bytes | None = None) -> MagicMock:
    """Build a context-manager mock response for aiohttp.ClientSession.get."""
    resp = MagicMock()
    resp.status = status
    resp.text = AsyncMock(return_value=text or "")
    resp.read = AsyncMock(return_value=body or b"")

    @asynccontextmanager
    async def _cm(*_a, **_kw):
        yield resp

    return _cm


class TestFetchCurrentRrpIntegration:
    """Drive ``fetch_current_rrp`` end-to-end with a fake session."""

    def _session_for_listing_and_zip(self, listing_html: str, zip_bytes: bytes) -> MagicMock:
        """Session returns listing on first GET, zip on second."""
        call_count = 0

        @asynccontextmanager
        async def _get(url, **_kw):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            if call_count == 1:
                # directory listing
                resp.status = 200
                resp.text = AsyncMock(return_value=listing_html)
                resp.read = AsyncMock(return_value=b"")
            else:
                # ZIP download
                resp.status = 200
                resp.text = AsyncMock(return_value="")
                resp.read = AsyncMock(return_value=zip_bytes)
            yield resp

        session = MagicMock()
        session.get = _get
        return session

    def test_returns_rrp_on_success(self):
        # ARRANGE
        zip_bytes = build_test_dispatch_zip([{"region": "VIC1", "rrp_dollars_per_mwh": 100.0}])
        filename = "PUBLIC_DISPATCHIS_202605280800_0000000518999999.zip"
        listing_html = f'<A HREF="/Reports/Current/DispatchIS_Reports/{filename}">f</A>'
        session = self._session_for_listing_and_zip(listing_html, zip_bytes)

        # ACT
        result = asyncio.run(fetch_current_rrp(session, "VIC1"))

        # ASSERT
        assert result is not None
        rrp_c_kwh, settlement = result
        assert rrp_c_kwh == pytest.approx(10.0)
        assert "2026" in settlement

    def test_returns_none_when_listing_empty(self):
        # ARRANGE — no dispatch files in listing
        @asynccontextmanager
        async def _get(url, **_kw):
            resp = MagicMock()
            resp.status = 200
            resp.text = AsyncMock(return_value="<html>no files here</html>")
            yield resp

        session = MagicMock()
        session.get = _get

        # ACT
        result = asyncio.run(fetch_current_rrp(session, "VIC1"))

        # ASSERT
        assert result is None

    def test_returns_none_when_listing_fetch_fails(self):
        # ARRANGE — listing returns 404
        @asynccontextmanager
        async def _get(url, **_kw):
            resp = MagicMock()
            resp.status = 404
            resp.text = AsyncMock(return_value="")
            yield resp

        session = MagicMock()
        session.get = _get

        # ACT
        result = asyncio.run(fetch_current_rrp(session, "NSW1"))

        # ASSERT
        assert result is None


class TestFetchDirectoryListingRetry:
    """Cover retry + non-200/non-500 branches in ``_fetch_directory_listing``."""

    def test_non_500_non_200_returns_none(self):
        from custom_components.pricehawk.aemo_api import _fetch_directory_listing

        @asynccontextmanager
        async def _get(url, **_kw):
            resp = MagicMock()
            resp.status = 403
            yield resp

        session = MagicMock()
        session.get = _get

        result = asyncio.run(_fetch_directory_listing(session))
        assert result is None

    def test_client_error_all_retries_exhausted_returns_none(self):
        from custom_components.pricehawk.aemo_api import _fetch_directory_listing

        @asynccontextmanager
        async def _get(url, **_kw):
            if False:  # satisfy async generator requirement
                yield  # pragma: no cover
            raise aiohttp.ClientConnectionError("network down")

        session = MagicMock()
        session.get = _get

        # Patch sleep so retries don't actually wait
        with patch("custom_components.pricehawk.aemo_api.asyncio.sleep", new_callable=AsyncMock):
            result = asyncio.run(_fetch_directory_listing(session))

        assert result is None

    def test_500_retries_then_returns_none_after_exhaustion(self):
        from custom_components.pricehawk.aemo_api import _fetch_directory_listing

        @asynccontextmanager
        async def _get(url, **_kw):
            resp = MagicMock()
            resp.status = 503
            yield resp

        session = MagicMock()
        session.get = _get

        with patch("custom_components.pricehawk.aemo_api.asyncio.sleep", new_callable=AsyncMock):
            result = asyncio.run(_fetch_directory_listing(session))

        assert result is None


class TestFetchZip:
    """Cover non-200 / error branches in ``_fetch_zip``."""

    def test_non_500_non_200_returns_none(self):
        from custom_components.pricehawk.aemo_api import _fetch_zip

        @asynccontextmanager
        async def _get(url, **_kw):
            resp = MagicMock()
            resp.status = 404
            yield resp

        session = MagicMock()
        session.get = _get

        result = asyncio.run(_fetch_zip(session, "https://example.com/file.zip"))
        assert result is None

    def test_500_retries_then_returns_none(self):
        from custom_components.pricehawk.aemo_api import _fetch_zip

        @asynccontextmanager
        async def _get(url, **_kw):
            resp = MagicMock()
            resp.status = 500
            yield resp

        session = MagicMock()
        session.get = _get

        with patch("custom_components.pricehawk.aemo_api.asyncio.sleep", new_callable=AsyncMock):
            result = asyncio.run(_fetch_zip(session, "https://example.com/file.zip"))

        assert result is None

    def test_client_error_exhausted_returns_none(self):
        from custom_components.pricehawk.aemo_api import _fetch_zip

        @asynccontextmanager
        async def _get(url, **_kw):
            if False:  # satisfy async generator requirement
                yield  # pragma: no cover
            raise aiohttp.ClientConnectionError("gone")

        session = MagicMock()
        session.get = _get

        with patch("custom_components.pricehawk.aemo_api.asyncio.sleep", new_callable=AsyncMock):
            result = asyncio.run(_fetch_zip(session, "https://example.com/file.zip"))

        assert result is None

    def test_200_returns_bytes(self):
        from custom_components.pricehawk.aemo_api import _fetch_zip

        expected = b"zipdata"

        @asynccontextmanager
        async def _get(url, **_kw):
            resp = MagicMock()
            resp.status = 200
            resp.read = AsyncMock(return_value=expected)
            yield resp

        session = MagicMock()
        session.get = _get

        result = asyncio.run(_fetch_zip(session, "https://example.com/file.zip"))
        assert result == expected


class TestParseDispatchZipEdgeCases:
    """Cover lines 206, 222, 234-236 — no CSV inside ZIP; bad RRP value."""

    def test_zip_with_no_csv_file_returns_none(self):
        # ARRANGE — build a ZIP with a non-CSV member
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("README.txt", "no csv here")
        payload = buf.getvalue()

        # ACT / ASSERT
        assert parse_dispatch_zip_for_test(payload, "VIC1") is None

    def test_bad_rrp_value_skipped_returns_none_when_only_row(self):
        # ARRANGE — build a CSV with a non-numeric RRP at index 9
        csv_text = (
            "C,NEMP.WORLD,DISPATCHIS,AEMO,PUBLIC,2026/05/28,t,t,t,1\n"
            "I,DISPATCH,PRICE,5,SETTLEMENTDATE,RUNNO,REGIONID,"
            "DISPATCHINTERVAL,INTERVENTION,RRP,EEP,ROP,APCFLAG,"
            "MARKETSUSPENDEDFLAG,LASTCHANGED\n"
            'D,DISPATCH,PRICE,5,"2026/05/28 08:00:00",1,"VIC1",'
            '159000,0,NOT_A_NUMBER,0,0,0,0,"2026/05/28 07:55:07"\n'
        ).encode("utf-8")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("PUBLIC_DISPATCHIS_TEST.CSV", csv_text)

        # ACT / ASSERT — bad parse → skipped_rows=1, rrp stays None → None
        assert parse_dispatch_zip_for_test(buf.getvalue(), "VIC1") is None

    def test_row_too_short_skipped(self):
        # ARRANGE — row with fewer than 10 columns
        csv_text = (
            "C,NEMP.WORLD,DISPATCHIS,AEMO,PUBLIC,2026/05/28,t,t,t,1\nD,DISPATCH,PRICE,5,short\n"
        ).encode("utf-8")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("PUBLIC_DISPATCHIS_TEST.CSV", csv_text)

        assert parse_dispatch_zip_for_test(buf.getvalue(), "VIC1") is None
