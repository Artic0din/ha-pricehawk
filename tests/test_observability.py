"""Tests for the shared drop-rate observability helper.

Constitution P14: this module exists because two prior PRs (#159 + #163)
shipped the same pattern with contradictory thresholds. The boundary
behaviour (exactly-10% escalates to WARNING) and the zero-total guard
are pinned here so future drift requires deleting these tests, not just
quietly editing the helper.
"""

from __future__ import annotations

import logging

import pytest

from custom_components.pricehawk._observability import report_drop_rate

_LOGGER_NAME = "tests.test_observability.fixture"


def test_zero_skipped_emits_nothing(caplog: pytest.LogCaptureFixture) -> None:
    """Happy path — silent. A clean parse must not log anything;
    a 5-minute dispatch cadence would otherwise flood the log.
    """
    caplog.set_level(logging.DEBUG, logger=_LOGGER_NAME)
    logger = logging.getLogger(_LOGGER_NAME)

    report_drop_rate(logger, "test source", skipped=0, total=100)

    records = [r for r in caplog.records if r.name == _LOGGER_NAME]
    assert records == [], f"zero-skip path must be silent; got {[r.getMessage() for r in records]}"


def test_below_threshold_emits_debug(caplog: pytest.LogCaptureFixture) -> None:
    """5% drop rate stays DEBUG — single bad row in a healthy feed."""
    caplog.set_level(logging.DEBUG, logger=_LOGGER_NAME)
    logger = logging.getLogger(_LOGGER_NAME)

    report_drop_rate(logger, "test source", skipped=1, total=20)

    records = [r for r in caplog.records if r.name == _LOGGER_NAME]
    assert len(records) == 1, (
        f"expected exactly one log record; got {[(r.levelname, r.getMessage()) for r in records]}"
    )
    record = records[0]
    assert record.levelno == logging.DEBUG, f"5% drop must stay DEBUG; got {record.levelname}"
    msg = record.getMessage()
    assert "test source" in msg
    assert "1/20" in msg
    assert "5.0%" in msg


def test_at_exactly_ten_percent_warns(caplog: pytest.LogCaptureFixture) -> None:
    """Boundary pinning: exactly 10% MUST escalate to WARNING.

    The helper uses inclusive ``>=`` (not strict ``>``). This is the
    one operational-semantics decision that the prior two PRs disagreed
    on; this test exists to ensure the decision can't be silently
    reverted.
    """
    caplog.set_level(logging.DEBUG, logger=_LOGGER_NAME)
    logger = logging.getLogger(_LOGGER_NAME)

    report_drop_rate(logger, "test source", skipped=1, total=10)

    records = [r for r in caplog.records if r.name == _LOGGER_NAME]
    assert len(records) == 1
    record = records[0]
    assert record.levelno == logging.WARNING, (
        f"exactly-10% MUST escalate to WARNING (inclusive >=); got {record.levelname}"
    )
    msg = record.getMessage()
    assert "1/10" in msg
    assert "10.0%" in msg
    assert "investigate" in msg.lower()


def test_above_threshold_warns(caplog: pytest.LogCaptureFixture) -> None:
    """30% drop rate triggers WARNING — sustained upstream regression."""
    caplog.set_level(logging.DEBUG, logger=_LOGGER_NAME)
    logger = logging.getLogger(_LOGGER_NAME)

    report_drop_rate(logger, "test source", skipped=3, total=10)

    records = [r for r in caplog.records if r.name == _LOGGER_NAME]
    assert len(records) == 1
    record = records[0]
    assert record.levelno == logging.WARNING, f"30% drop must warn; got {record.levelname}"
    msg = record.getMessage()
    assert "3/10" in msg
    assert "30.0%" in msg


def test_zero_total_is_silent_and_does_not_divide(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """ZeroDivisionError guard.

    If no row reached the parse step (``total == 0``) the helper must
    return cleanly without computing ``skipped / total``. ``skipped``
    may still be a positive integer if the caller mis-accounts; the
    helper must not crash. The surrounding code path handles the
    "no data" outcome via its existing return contract.
    """
    caplog.set_level(logging.DEBUG, logger=_LOGGER_NAME)
    logger = logging.getLogger(_LOGGER_NAME)

    # The function must not raise ZeroDivisionError.
    report_drop_rate(logger, "test source", skipped=0, total=0)
    report_drop_rate(logger, "test source", skipped=5, total=0)

    records = [r for r in caplog.records if r.name == _LOGGER_NAME]
    assert records == [], f"zero-total path must be silent; got {[r.getMessage() for r in records]}"


def test_custom_threshold_respected(caplog: pytest.LogCaptureFixture) -> None:
    """A caller-supplied tighter threshold escalates earlier.

    Demonstrates that the kwarg is wired through correctly — useful as
    a regression check if a future caller wants a 5% threshold.
    """
    caplog.set_level(logging.DEBUG, logger=_LOGGER_NAME)
    logger = logging.getLogger(_LOGGER_NAME)

    # 5% drop — below the default 10% but at the custom 5% threshold.
    report_drop_rate(logger, "test source", skipped=1, total=20, warn_threshold=0.05)

    records = [r for r in caplog.records if r.name == _LOGGER_NAME]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
