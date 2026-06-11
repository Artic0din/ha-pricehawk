"""Extended backfill tests — covers the 74% gap in backfill.py.

Targets:
  - fetch_amber_price_history: 7-day chunking, error handling, auth header
  - _local_date_string: AEST-safe date formatting
  - _states_to_tuples: State object path, dict path, filtering
  - _merge_into_history: insert, overwrite, cap, sort
"""

from __future__ import annotations

import json
import urllib.error
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch


from custom_components.pricehawk.backfill import (
    _local_date_string,
    _merge_into_history,
    _states_to_tuples,
    fetch_amber_price_history,
)

AEST = timezone(timedelta(hours=10))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_response(data: list, status: int = 200):
    """Build a mock urllib response context manager."""
    body = json.dumps(data).encode()
    resp = MagicMock()
    resp.status = status
    resp.read.return_value = body
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


# ---------------------------------------------------------------------------
# _local_date_string
# ---------------------------------------------------------------------------


class TestLocalDateString:
    def test_zero_pads_month_and_day(self):
        # ARRANGE
        dt = datetime(2026, 1, 3, 12, 30, 0)

        # ACT + ASSERT
        assert _local_date_string(dt) == "2026-01-03"

    def test_december_31(self):
        # ARRANGE
        dt = datetime(2025, 12, 31, 23, 59, 59)

        # ACT + ASSERT
        assert _local_date_string(dt) == "2025-12-31"

    def test_january_1(self):
        # ARRANGE
        dt = datetime(2026, 1, 1, 0, 0, 0)

        # ACT + ASSERT
        assert _local_date_string(dt) == "2026-01-01"

    def test_tz_aware_datetime_uses_local_components(self):
        """For tz-aware datetimes, .year/.month/.day reflect local time, not UTC."""
        # ARRANGE — midnight AEST = 14:00 UTC previous day
        dt = datetime(2026, 4, 10, 0, 0, 0, tzinfo=AEST)

        # ACT + ASSERT: should be 2026-04-10, not 2026-04-09
        assert _local_date_string(dt) == "2026-04-10"


# ---------------------------------------------------------------------------
# fetch_amber_price_history — 7-day chunking and error handling
# ---------------------------------------------------------------------------


class TestFetchAmberPriceHistory:
    def test_single_chunk_for_3_day_range(self):
        """A 3-day range produces exactly one API call."""
        # ARRANGE
        start = datetime(2026, 4, 10, 0, 0, 0, tzinfo=AEST)
        end = datetime(2026, 4, 13, 0, 0, 0, tzinfo=AEST)
        fake_data = [{"channelType": "general", "perKwh": 20.0}]

        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value = _fake_response(fake_data)

            # ACT
            result = fetch_amber_price_history("key", "site1", start, end)

        # ASSERT
        assert mock_open.call_count == 1
        assert len(result) == 1

    def test_two_chunks_for_10_day_range(self):
        """A 10-day range produces two API calls (7 + 3)."""
        # ARRANGE
        start = datetime(2026, 4, 1, 0, 0, 0, tzinfo=AEST)
        end = datetime(2026, 4, 11, 0, 0, 0, tzinfo=AEST)

        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value = _fake_response([])

            # ACT
            fetch_amber_price_history("key", "site1", start, end)

        # ASSERT
        assert mock_open.call_count == 2

    def test_results_from_all_chunks_concatenated(self):
        """Results from multiple chunks are merged into one list."""
        # ARRANGE
        start = datetime(2026, 4, 1, 0, 0, 0, tzinfo=AEST)
        end = datetime(2026, 4, 11, 0, 0, 0, tzinfo=AEST)
        chunk1 = [{"channelType": "general", "perKwh": 10.0}]
        chunk2 = [{"channelType": "general", "perKwh": 20.0}]

        with patch("urllib.request.urlopen") as mock_open:
            mock_open.side_effect = [
                _fake_response(chunk1),
                _fake_response(chunk2),
            ]

            # ACT
            result = fetch_amber_price_history("key", "site1", start, end)

        # ASSERT
        assert len(result) == 2
        rates = {r["perKwh"] for r in result}
        assert rates == {10.0, 20.0}

    def test_non_200_response_returns_empty(self):
        """Non-200 response is skipped — no exception, no items."""
        # ARRANGE
        start = datetime(2026, 4, 1, 0, 0, 0, tzinfo=AEST)
        end = datetime(2026, 4, 4, 0, 0, 0, tzinfo=AEST)

        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value = _fake_response([], status=429)

            # ACT
            result = fetch_amber_price_history("key", "site1", start, end)

        # ASSERT
        assert result == []

    def test_url_error_returns_empty(self):
        """URLError is caught — no exception propagated, no items returned."""
        # ARRANGE
        start = datetime(2026, 4, 1, 0, 0, 0, tzinfo=AEST)
        end = datetime(2026, 4, 4, 0, 0, 0, tzinfo=AEST)

        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timeout")):
            # ACT
            result = fetch_amber_price_history("key", "site1", start, end)

        # ASSERT
        assert result == []

    def test_bearer_token_in_auth_header(self):
        """API call uses Authorization: Bearer <api_key>."""
        # ARRANGE
        start = datetime(2026, 4, 1, 0, 0, 0, tzinfo=AEST)
        end = datetime(2026, 4, 2, 0, 0, 0, tzinfo=AEST)
        captured_headers: list[dict] = []

        original_request = __import__("urllib.request", fromlist=["Request"]).Request

        def capture_req(url, headers=None):
            if headers:
                captured_headers.append(dict(headers))
            return original_request(url, headers=headers or {})

        with (
            patch("urllib.request.urlopen") as mock_open,
            patch("urllib.request.Request", side_effect=capture_req),
        ):
            mock_open.return_value = _fake_response([])
            fetch_amber_price_history("MY_SECRET_KEY", "site_abc", start, end)

        # ASSERT
        assert len(captured_headers) >= 1
        assert captured_headers[0].get("Authorization") == "Bearer MY_SECRET_KEY"

    def test_url_uses_zero_padded_date_format(self):
        """URL query params use YYYY-MM-DD zero-padded format."""
        # ARRANGE
        start = datetime(2026, 4, 5, 0, 0, 0, tzinfo=AEST)
        end = datetime(2026, 4, 6, 0, 0, 0, tzinfo=AEST)
        captured_urls: list[str] = []

        def capture_req(url, headers=None):
            captured_urls.append(url)
            m = MagicMock()
            m.full_url = url
            return m

        with (
            patch("urllib.request.urlopen") as mock_open,
            patch("urllib.request.Request", side_effect=capture_req),
        ):
            mock_open.return_value = _fake_response([])
            fetch_amber_price_history("key", "site1", start, end)

        # ASSERT
        assert len(captured_urls) == 1
        assert "startDate=2026-04-05" in captured_urls[0]
        assert "endDate=2026-04-06" in captured_urls[0]


# ---------------------------------------------------------------------------
# _states_to_tuples
# ---------------------------------------------------------------------------


class TestStatesToTuples:
    def test_dict_shaped_state_with_iso_string_ts(self):
        """Dict-shaped state with ISO string last_changed is parsed correctly."""
        # ARRANGE
        ts_str = "2026-04-10T12:00:00+10:00"
        states = [{"state": 1500.0, "last_changed": ts_str, "unit": "W"}]

        # ACT
        result = _states_to_tuples(states)

        # ASSERT
        assert len(result) == 1
        assert result[0][1] == 1500.0
        assert result[0][2] == "W"

    def test_dict_shaped_state_with_datetime_ts(self):
        """Dict-shaped state with datetime last_changed is accepted directly."""
        # ARRANGE
        ts = datetime(2026, 4, 10, 12, 0, 0, tzinfo=AEST)
        states = [{"state": 2000.0, "last_changed": ts, "unit": "kW"}]

        # ACT
        result = _states_to_tuples(states)

        # ASSERT
        assert len(result) == 1
        assert result[0][1] == 2000.0
        assert result[0][2] == "kW"

    def test_unavailable_state_filtered_out(self):
        """State objects with value 'unavailable' are silently dropped."""
        # ARRANGE — simulate a HA State object
        s = MagicMock()
        s.state = "unavailable"
        s.last_changed = datetime(2026, 4, 10, 12, 0, 0, tzinfo=AEST)

        # ACT
        result = _states_to_tuples([s])

        # ASSERT
        assert result == []

    def test_unknown_state_filtered_out(self):
        """State objects with value 'unknown' are silently dropped."""
        # ARRANGE
        s = MagicMock()
        s.state = "unknown"
        s.last_changed = datetime(2026, 4, 10, 12, 0, 0, tzinfo=AEST)

        # ACT
        result = _states_to_tuples([s])

        # ASSERT
        assert result == []

    def test_state_object_with_unit_of_measurement(self):
        """HA State object with unit_of_measurement attribute is parsed."""
        # ARRANGE
        s = MagicMock()
        s.state = "3500.5"
        s.last_changed = datetime(2026, 4, 10, 12, 0, 0, tzinfo=AEST)
        s.attributes = {"unit_of_measurement": "W"}

        # ACT
        result = _states_to_tuples([s])

        # ASSERT
        assert len(result) == 1
        assert result[0][1] == "3500.5"
        assert result[0][2] == "W"

    def test_dict_invalid_iso_timestamp_filtered_out(self):
        """Dict-shaped state with unparseable last_changed is dropped."""
        # ARRANGE
        states = [{"state": 1000.0, "last_changed": "not-a-date", "unit": "W"}]

        # ACT
        result = _states_to_tuples(states)

        # ASSERT
        assert result == []

    def test_dict_missing_last_changed_filtered_out(self):
        """Dict-shaped state with no last_changed field is dropped."""
        # ARRANGE — no last_changed key (ts_raw will be None)
        states = [{"state": 1000.0, "unit": "W"}]

        # ACT
        result = _states_to_tuples(states)

        # ASSERT
        assert result == []

    def test_non_dict_non_state_skipped(self):
        """Non-dict, non-State objects are silently skipped."""
        # ARRANGE
        states = ["not_a_state", 42, None]

        # ACT
        result = _states_to_tuples(states)  # type: ignore[arg-type]

        # ASSERT
        assert result == []

    def test_unit_from_attributes_dict(self):
        """Unit falls back to attributes.unit_of_measurement for dict-shaped states."""
        # ARRANGE
        ts = datetime(2026, 4, 10, 12, 0, 0, tzinfo=AEST)
        states = [
            {
                "state": 500.0,
                "last_changed": ts,
                "attributes": {"unit_of_measurement": "kW"},
            }
        ]

        # ACT
        result = _states_to_tuples(states)

        # ASSERT
        assert len(result) == 1
        assert result[0][2] == "kW"


# ---------------------------------------------------------------------------
# _merge_into_history
# ---------------------------------------------------------------------------


class TestMergeIntoHistory:
    def test_new_date_inserted_into_empty_history(self):
        """New date not in existing_history is inserted."""
        # ARRANGE
        new_rows = {"2026-04-10": {"amber": 5.00}}
        existing: list = []

        # ACT
        result = _merge_into_history(new_rows, existing)

        # ASSERT
        assert len(result) == 1
        assert result[0]["date"] == "2026-04-10"
        assert result[0]["amber"] == 5.00

    def test_existing_date_receives_new_plan_key(self):
        """Backfill adds a new plan_key to an existing history row."""
        # ARRANGE — existing has amber, backfill adds globird
        new_rows = {"2026-04-10": {"globird": 4.50}}
        existing = [{"date": "2026-04-10", "amber": 5.00}]

        # ACT
        result = _merge_into_history(new_rows, existing)

        # ASSERT: both keys present
        assert len(result) == 1
        assert result[0]["amber"] == 5.00  # preserved from existing
        assert result[0]["globird"] == 4.50  # added from backfill

    def test_backfill_overwrites_matching_plan_key(self):
        """Backfill value replaces existing value for the same plan_key."""
        # ARRANGE
        new_rows = {"2026-04-10": {"amber": 3.00}}
        existing = [{"date": "2026-04-10", "amber": 99.99}]

        # ACT
        result = _merge_into_history(new_rows, existing)

        # ASSERT
        assert result[0]["amber"] == 3.00

    def test_result_sorted_by_date_ascending(self):
        """Merged result is sorted oldest→newest by date."""
        # ARRANGE — deliberately out-of-order
        new_rows = {"2026-04-12": {"amber": 4.00}}
        existing = [
            {"date": "2026-04-14", "amber": 6.00},
            {"date": "2026-04-11", "amber": 3.00},
        ]

        # ACT
        result = _merge_into_history(new_rows, existing)

        # ASSERT
        dates = [r["date"] for r in result]
        assert dates == ["2026-04-11", "2026-04-12", "2026-04-14"]

    def test_result_capped_at_180_entries(self):
        """Merged list is trimmed to 180 entries (oldest dropped)."""
        # ARRANGE — 175 existing + 10 new = 185 total → trimmed to 180
        existing = [
            {"date": f"2025-{(i // 30) + 1:02d}-{(i % 30) + 1:02d}", "amber": 5.0}
            for i in range(175)
        ]
        new_rows = {f"2026-04-{i:02d}": {"amber": float(i)} for i in range(1, 11)}

        # ACT
        result = _merge_into_history(new_rows, existing)

        # ASSERT
        assert len(result) <= 180

    def test_entry_without_date_key_skipped(self):
        """Existing entries missing the 'date' key are silently dropped."""
        # ARRANGE
        new_rows = {"2026-04-10": {"amber": 5.00}}
        existing = [{"amber": 99.99}]  # no date key

        # ACT
        result = _merge_into_history(new_rows, existing)

        # ASSERT: only the new row appears
        assert len(result) == 1
        assert result[0]["date"] == "2026-04-10"

    def test_empty_new_rows_returns_existing(self):
        """No backfill rows → existing history returned unchanged."""
        # ARRANGE
        existing = [{"date": "2026-04-10", "amber": 5.00}]

        # ACT
        result = _merge_into_history({}, existing)

        # ASSERT
        assert len(result) == 1
        assert result[0]["amber"] == 5.00
