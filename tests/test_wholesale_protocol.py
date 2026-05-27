"""Contract test: AmberProvider must satisfy WholesaleProvider.

This test exists so that any future change to either the Protocol or
:class:`AmberProvider` that breaks the contract fails fast at CI time
rather than silently in coordinator wiring.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from custom_components.pricehawk.wholesale import WholesaleProvider
from custom_components.pricehawk.wholesale.amber import AmberProvider


def test_amber_provider_is_runtime_instance() -> None:
    """``isinstance`` check against the @runtime_checkable Protocol."""
    provider = AmberProvider()
    assert isinstance(provider, WholesaleProvider)


def test_amber_provider_exposes_required_methods() -> None:
    """Every Protocol method/property is reachable on the instance."""
    provider = AmberProvider(
        amber_network_daily_c=120.0,
        amber_subscription_daily_c=80.0,
    )
    provider.update(
        grid_power_w=1500.0,
        import_rate_c_kwh=25.5,
        export_rate_c_kwh=-7.2,
        now_local=datetime(2026, 5, 27, 12, 0),
    )
    # Force a second reading so delta_h is non-None and accumulators move.
    provider.update(
        grid_power_w=1500.0,
        import_rate_c_kwh=25.5,
        export_rate_c_kwh=-7.2,
        now_local=datetime(2026, 5, 27, 12, 1),
    )

    assert provider.current_import_rate_c_kwh == 25.5
    assert provider.current_export_rate_c_kwh == -7.2
    assert provider.import_kwh_today > 0
    assert provider.export_kwh_today == 0  # all power was import
    assert provider.import_cost_today_c > 0
    assert provider.export_earnings_today_c == 0
    assert provider.daily_fixed_charges_aud == (120.0 + 80.0) / 100
    # Net = import_cost - export_earnings + fixed, all in AUD.
    expected_net = (
        provider.import_cost_today_c - provider.export_earnings_today_c
    ) / 100 + provider.daily_fixed_charges_aud
    assert provider.net_daily_cost_aud == expected_net


def test_amber_provider_roundtrip_serialisation() -> None:
    """``to_dict`` → ``from_dict`` restores accumulators when date matches."""
    original = AmberProvider()
    base = datetime(2026, 5, 27, 9, 0)
    original.update(2000.0, 30.0, -8.0, base)
    original.update(2000.0, 30.0, -8.0, base + timedelta(minutes=30))

    restored = AmberProvider()
    restored.from_dict(original.to_dict(), today=date(2026, 5, 27))

    assert restored.import_kwh_today == original.import_kwh_today
    assert restored.import_cost_today_c == original.import_cost_today_c
    assert restored.current_import_rate_c_kwh == original.current_import_rate_c_kwh


def test_amber_provider_name() -> None:
    """Provider exposes a stable identifier for coordinator dispatch (PR 4)."""
    assert AmberProvider.name == "amber"
