"""Regression snapshot: AmberProvider behaves identically to AmberCalculator.

PR 2 moves :mod:`amber_calculator` under :mod:`wholesale.amber` and adds
:class:`AmberProvider` as a wrapper for :class:`AmberCalculator`. The
explicit invariant is *zero behaviour change* — replaying the same update
sequence through both classes must produce byte-identical state.

If a future PR diverges :class:`AmberProvider` from the calculator (e.g.
PR 4 adds rate-fetching), this test should be updated deliberately or
deleted with rationale — never patched to silence a failure.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from custom_components.pricehawk.wholesale.amber.calculator import AmberCalculator
from custom_components.pricehawk.wholesale.amber.provider import AmberProvider


def _replay(instance: AmberCalculator) -> None:
    """Drive a deterministic 24-hour sequence: changing rates, mixed import/export."""
    base = datetime(2026, 5, 27, 0, 0)
    rate_table = [
        (30.0, -5.0),
        (45.0, -12.0),
        (25.0, -3.0),
        (-15.0, -20.0),  # negative-import-rate window (Amber feed-in)
    ]
    power_table = [3500.0, -2000.0, 1200.0, -800.0]

    for hour in range(24):
        rate_idx = hour // 6
        import_c, export_c = rate_table[rate_idx]
        power_w = power_table[rate_idx]
        instance.update(power_w, import_c, export_c, base + timedelta(hours=hour))


def test_amber_provider_state_matches_calculator() -> None:
    calc = AmberCalculator(amber_network_daily_c=110.0, amber_subscription_daily_c=70.0)
    provider = AmberProvider(amber_network_daily_c=110.0, amber_subscription_daily_c=70.0)

    _replay(calc)
    _replay(provider)

    assert provider.to_dict() == calc.to_dict()
    assert provider.net_daily_cost_aud == calc.net_daily_cost_aud
    assert provider.daily_fixed_charges_aud == calc.daily_fixed_charges_aud
    assert provider.import_kwh_today == calc.import_kwh_today
    assert provider.export_kwh_today == calc.export_kwh_today


def test_amber_provider_from_dict_matches_calculator() -> None:
    """Persistence restore path is identical between calculator and provider."""
    seed = AmberCalculator()
    _replay(seed)
    snapshot = seed.to_dict()

    calc = AmberCalculator()
    provider = AmberProvider()
    today = date(2026, 5, 27)
    calc.from_dict(snapshot, today=today)
    provider.from_dict(snapshot, today=today)

    assert provider.to_dict() == calc.to_dict()


def test_amber_provider_midnight_reset_matches_calculator() -> None:
    """Midnight rollover behaviour is preserved across the move."""
    calc = AmberCalculator()
    provider = AmberProvider()

    yesterday = datetime(2026, 5, 26, 23, 30)
    today_dt = datetime(2026, 5, 27, 0, 30)
    for instance in (calc, provider):
        instance.update(2000.0, 30.0, -5.0, yesterday)
        instance.update(2000.0, 30.0, -5.0, today_dt)

    assert provider.to_dict() == calc.to_dict()
    # Daily accumulators reset to today's half-hour usage only.
    assert calc.import_kwh_today > 0
    assert calc.import_kwh_today < 2.0  # < 30 minutes of 2 kW = < 1 kWh
