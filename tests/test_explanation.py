"""Tests for the explanation engine."""

from __future__ import annotations

import statistics
import time

import pytest

from custom_components.pricehawk.explanation import (
    build_explanation,
)


class TestDwtWinnerBullets:
    """Live UAT 2026-05-24: DWT winners produced an empty bullets list
    because every winner-type branch in build_explanation matched on a
    literal provider id ("amber", "globird", etc.) but DWT providers
    are "dwt_aemo_direct" / "dwt_openelectricity". This test pins the
    new ``startswith("dwt_")`` branch + the bullet shape.
    """

    @staticmethod
    def _dwt_providers(
        *,
        import_kwh: float = 8.5,
        import_cost: float = 4.32,
        wholesale_mwh: float = 432.0,
        region: str = "VIC1",
        price_age_seconds: int = 60,
    ):
        return {
            "dwt_aemo_direct": {
                "name": "Dynamic Wholesale Tariff — AEMO Direct",
                "import_rate_c_kwh": 43.2,
                "export_rate_c_kwh": 43.2,
                "import_kwh_today": import_kwh,
                "export_kwh_today": 0.0,
                "import_cost_today_aud": import_cost,
                "export_credit_today_aud": 0.0,
                "daily_fixed_charges_aud": 1.10,
                "net_daily_cost_aud": import_cost + 1.10,
                "extras": {
                    "wholesale_price_aud_per_mwh": wholesale_mwh,
                    "wholesale_price_age_seconds": price_age_seconds,
                    "region": region,
                    "daily_supply_aud": 1.10,
                },
            },
            "amber": {
                "name": "Amber",
                "import_rate_c_kwh": 45.0,
                "export_rate_c_kwh": 5.0,
                "import_kwh_today": 8.5,
                "export_kwh_today": 0.0,
                "import_cost_today_aud": 4.50,
                "export_credit_today_aud": 0.0,
                "daily_fixed_charges_aud": 1.30,
                "net_daily_cost_aud": 5.80,
                "extras": {},
            },
        }

    def test_dwt_winner_produces_non_empty_bullets(self):
        explanation = build_explanation(self._dwt_providers())
        assert explanation.winner_id == "dwt_aemo_direct"
        assert len(explanation.bullets) > 0, (
            "DWT winners must produce bullets — empty list was the live "
            "UAT bug surfaced 2026-05-24."
        )

    def test_dwt_winner_bullets_mention_wholesale_spot_rate(self):
        explanation = build_explanation(
            self._dwt_providers(wholesale_mwh=432.0),
        )
        # 432 $/MWh = 43.2 c/kWh; matches the DWT provider conversion
        assert any(
            "43.20" in b.text or "43.2c/kWh" in b.text
            for b in explanation.bullets
        ), (
            "Wholesale spot rate should appear in the explanation. "
            f"Got bullets: {[b.text for b in explanation.bullets]}"
        )

    def test_dwt_winner_bullets_mention_region(self):
        explanation = build_explanation(
            self._dwt_providers(region="NSW1"),
        )
        assert any(
            "NSW1" in b.text for b in explanation.bullets
        ), "Region should appear in at least one bullet for context."

    def test_dwt_stale_price_warning_when_over_10_minutes_old(self):
        explanation = build_explanation(
            self._dwt_providers(price_age_seconds=700),
        )
        assert any(
            b.sentiment == "bad" and "old" in b.text.lower()
            for b in explanation.bullets
        ), "A stale price (>10min) should surface a 'bad' bullet."

    def test_dwt_openelectricity_winner_also_handled(self):
        providers = self._dwt_providers()
        providers["dwt_openelectricity"] = providers.pop("dwt_aemo_direct")
        providers["dwt_openelectricity"]["name"] = (
            "Dynamic Wholesale Tariff — OpenElectricity"
        )
        explanation = build_explanation(providers)
        assert explanation.winner_id == "dwt_openelectricity"
        assert len(explanation.bullets) > 0


def _provider_snapshot(
    name: str,
    *,
    cost: float,
    import_rate: float = 25.0,
    export_rate: float = 5.0,
    import_kwh: float = 10.0,
    export_kwh: float = 5.0,
    extras: dict | None = None,
):
    return {
        "name": name,
        "import_rate_c_kwh": import_rate,
        "export_rate_c_kwh": export_rate,
        "import_kwh_today": import_kwh,
        "export_kwh_today": export_kwh,
        "import_cost_today_aud": import_kwh * import_rate / 100.0,
        "export_credit_today_aud": export_kwh * export_rate / 100.0,
        "daily_fixed_charges_aud": 1.0,
        "net_daily_cost_aud": cost,
        "extras": extras or {},
    }


class TestEmpty:
    def test_no_providers_returns_placeholder(self):
        result = build_explanation({})
        assert result.winner_id == ""
        assert result.bullets == []


class TestWinnerSelection:
    def test_picks_lowest_cost(self):
        result = build_explanation(
            {
                "amber": _provider_snapshot("Amber", cost=6.42),
                "globird": _provider_snapshot("GloBird", cost=5.18),
                "flow_power": _provider_snapshot(
                    "Flow Power", cost=2.34, extras={"happy_hour_export_kwh": 0}
                ),
                "localvolts": _provider_snapshot("LocalVolts", cost=4.92),
            }
        )
        assert result.winner_id == "flow_power"
        assert result.section_label == "Why Flow Power won"

    def test_margin_calculation(self):
        result = build_explanation(
            {
                "amber": _provider_snapshot("Amber", cost=6.42),
                "globird": _provider_snapshot("GloBird", cost=5.18),
            }
        )
        assert result.margin_aud == pytest.approx(6.42 - 5.18)
        assert any(
            "Beat next-best" in b.text and "$1.24" in b.text
            for b in result.bullets
        )


class TestGloBirdBullets:
    def test_zerohero_credit_earned_emits_good_bullet(self):
        result = build_explanation(
            {
                "globird": _provider_snapshot(
                    "GloBird",
                    cost=5.0,
                    extras={"zerohero_status": "earned", "super_export_kwh": 0},
                ),
                "amber": _provider_snapshot("Amber", cost=8.0),
            }
        )
        assert result.winner_id == "globird"
        good_bullets = [b for b in result.bullets if b.sentiment == "good"]
        assert any("$1/day credit earned" in b.text for b in good_bullets)

    def test_super_export_emits_good_bullet(self):
        result = build_explanation(
            {
                "globird": _provider_snapshot(
                    "GloBird",
                    cost=5.0,
                    extras={
                        "zerohero_status": "pending",
                        "super_export_kwh": 12.0,
                    },
                ),
                "amber": _provider_snapshot("Amber", cost=8.0),
            }
        )
        good = [b for b in result.bullets if b.sentiment == "good"]
        assert any(
            "Super export" in b.text and "12.00 kWh" in b.text for b in good
        )

    def test_zerohero_lost_with_peak_import_emits_bad_bullet(self):
        result = build_explanation(
            {
                "globird": _provider_snapshot(
                    "GloBird",
                    cost=5.0,
                    extras={"zerohero_status": "lost", "super_export_kwh": 0},
                ),
                "amber": _provider_snapshot("Amber", cost=8.0),
            },
            peak_import_kwh_6_9pm=0.5,
        )
        bad = [b for b in result.bullets if b.sentiment == "bad"]
        assert any("$1 credit not earned" in b.text for b in bad)

    def test_amber_spot_above_globird_rate(self):
        result = build_explanation(
            {
                "globird": _provider_snapshot(
                    "GloBird",
                    cost=5.0,
                    import_rate=27.5,
                    extras={"zerohero_status": "pending"},
                ),
                "amber": _provider_snapshot(
                    "Amber", cost=8.0, import_kwh=10.0
                ),
            },
            avg_amber_spot_c_kwh=35.0,
        )
        good = [b for b in result.bullets if b.sentiment == "good"]
        assert any(
            "spot avg 35.0c/kWh was above GloBird" in b.text for b in good
        )


class TestFlowPowerBullets:
    def test_happy_hour_export_earns_good_bullet(self):
        result = build_explanation(
            {
                "flow_power": _provider_snapshot(
                    "Flow Power",
                    cost=2.0,
                    extras={
                        "happy_hour_export_kwh": 3.2,
                        "happy_hour_rate_c_kwh": 45.0,
                        "wholesale_c_kwh": 8.0,
                    },
                ),
                "amber": _provider_snapshot("Amber", cost=6.0),
            }
        )
        good = [b for b in result.bullets if b.sentiment == "good"]
        assert any(
            "Happy Hour FiT" in b.text
            and "3.20 kWh" in b.text
            and "$1.44" in b.text
            for b in good
        )

    def test_export_outside_happy_hour_emits_bad_bullet(self):
        result = build_explanation(
            {
                "flow_power": _provider_snapshot(
                    "Flow Power",
                    cost=2.0,
                    export_kwh=5.0,
                    extras={
                        "happy_hour_export_kwh": 0.0,
                        "happy_hour_rate_c_kwh": 45.0,
                    },
                ),
                "amber": _provider_snapshot("Amber", cost=6.0),
            }
        )
        bad = [b for b in result.bullets if b.sentiment == "bad"]
        assert any("none during the 5:30–7:30pm Happy Hour" in b.text for b in bad)


class TestLocalVoltsBullets:
    def test_negative_export_emits_bad_bullet(self):
        result = build_explanation(
            {
                "localvolts": _provider_snapshot(
                    "LocalVolts",
                    cost=2.0,
                    extras={
                        "negative_export_kwh": 1.5,
                        "negative_export_cost_aud": 0.42,
                    },
                ),
                "amber": _provider_snapshot("Amber", cost=6.0),
            }
        )
        bad = [b for b in result.bullets if b.sentiment == "bad"]
        assert any(
            "Negative spot pricing" in b.text and "1.50 kWh" in b.text
            for b in bad
        )

    def test_sell_floor_active_emits_neu_bullet(self):
        result = build_explanation(
            {
                "localvolts": _provider_snapshot(
                    "LocalVolts",
                    cost=2.0,
                    extras={
                        "sell_floor_c_kwh": 12.0,
                        "negative_export_kwh": 0.0,
                    },
                ),
                "amber": _provider_snapshot("Amber", cost=6.0),
            }
        )
        neu = [b for b in result.bullets if b.sentiment == "neu"]
        assert any("Sell floor 12.0c/kWh active" in b.text for b in neu)


class TestAmberBullets:
    def test_strong_feedin_emits_good_bullet(self):
        result = build_explanation(
            {
                "amber": _provider_snapshot(
                    "Amber",
                    cost=2.0,
                    export_kwh=15.0,
                    export_rate=12.0,  # → $1.80 credit
                ),
                "globird": _provider_snapshot("GloBird", cost=5.0),
            }
        )
        good = [b for b in result.bullets if b.sentiment == "good"]
        assert any("Strong feed-in income" in b.text for b in good)

    def test_amber_below_competitor_rate(self):
        result = build_explanation(
            {
                "amber": _provider_snapshot(
                    "Amber", cost=3.0, import_kwh=10.0
                ),
                "globird": _provider_snapshot(
                    "GloBird", cost=5.0, import_rate=30.0
                ),
            },
            avg_amber_spot_c_kwh=15.0,
        )
        good = [b for b in result.bullets if b.sentiment == "good"]
        assert any(
            "spot avg 15.0c/kWh was below their 30.0c/kWh" in b.text
            for b in good
        )


class TestPerTickPerformance:
    """Constitution P18: per-tick rebuild cost MUST be measured, not estimated.

    ``coordinator._async_update_data`` calls ``build_explanation`` on every
    30s tick (since beta.4). The original comment described the cost as
    "a handful of dict comprehensions" — an estimate, not a measurement.
    This test pins an empirical ceiling so a future refactor that
    accidentally adds a quadratic loop, an I/O call, or a heavy
    dependency surfaces immediately rather than silently bloating the
    HA event loop budget.

    Methodology:
    - Realistic providers block: 4 providers (Amber, GloBird, Flow Power,
      DWT) with full extras, mirroring a configured user mid-day.
    - 50-iteration warmup to populate caches, then 500 timed iterations.
    - Median is the assertion target (robust to GC / scheduler jitter).
    - Ceiling: 10ms. This is ~3 orders of magnitude above the measured
      median; the headroom absorbs slow CI runners without false fails.
    - Failure indicates a genuine regression worth investigating (e.g.
      someone added an aiohttp call, a deep copy, or an O(n^2) loop).
    """

    @staticmethod
    def _realistic_providers():
        """4-provider snapshot mirroring a mid-day user with everything on."""
        return {
            "amber": {
                "name": "Amber",
                "import_rate_c_kwh": 28.5,
                "export_rate_c_kwh": 8.2,
                "import_kwh_today": 12.4,
                "export_kwh_today": 8.6,
                "import_cost_today_aud": 3.534,
                "export_credit_today_aud": 0.7052,
                "daily_fixed_charges_aud": 1.20,
                "net_daily_cost_aud": 4.029,
                "extras": {},
            },
            "globird": {
                "name": "GloBird",
                "import_rate_c_kwh": 27.5,
                "export_rate_c_kwh": 5.0,
                "import_kwh_today": 12.4,
                "export_kwh_today": 8.6,
                "import_cost_today_aud": 3.41,
                "export_credit_today_aud": 0.43,
                "daily_fixed_charges_aud": 1.30,
                "net_daily_cost_aud": 4.28,
                "extras": {
                    "zerohero_status": "earned",
                    "super_export_kwh": 2.1,
                },
            },
            "flow_power": {
                "name": "Flow Power",
                "import_rate_c_kwh": 22.0,
                "export_rate_c_kwh": 6.0,
                "import_kwh_today": 12.4,
                "export_kwh_today": 8.6,
                "import_cost_today_aud": 2.728,
                "export_credit_today_aud": 0.516,
                "daily_fixed_charges_aud": 1.10,
                "net_daily_cost_aud": 3.312,
                "extras": {
                    "happy_hour_export_kwh": 3.2,
                    "happy_hour_rate_c_kwh": 45.0,
                    "wholesale_c_kwh": 8.0,
                },
            },
            "dwt_aemo_direct": {
                "name": "Dynamic Wholesale Tariff — AEMO Direct",
                "import_rate_c_kwh": 9.6,
                "export_rate_c_kwh": 9.6,
                "import_kwh_today": 12.4,
                "export_kwh_today": 8.6,
                "import_cost_today_aud": 1.19,
                "export_credit_today_aud": 0.825,
                "daily_fixed_charges_aud": 1.10,
                "net_daily_cost_aud": 1.465,
                "extras": {
                    "wholesale_price_aud_per_mwh": 96.0,
                    "wholesale_price_age_seconds": 60,
                    "region": "VIC1",
                    "daily_supply_aud": 1.10,
                },
            },
        }

    def test_per_tick_rebuild_under_10ms(self):
        """Median build_explanation runtime must be well under the 10ms ceiling.

        Measured on dev hardware (Apple Silicon, Python 3.13) the median
        sits in the low hundreds of microseconds — see the comment in
        ``coordinator.py`` around line 1167 for the recorded value. The
        10ms ceiling exists to catch regressions on slower CI runners
        without flaking on jitter.
        """
        providers = self._realistic_providers()

        # Warmup — first calls populate any lazy caches inside the
        # interpreter (interning, branch prediction warm-up). Discarding
        # them gives a more stable median.
        for _ in range(50):
            build_explanation(providers, avg_amber_spot_c_kwh=25.0)

        samples_ns: list[int] = []
        for _ in range(500):
            start = time.perf_counter_ns()
            build_explanation(providers, avg_amber_spot_c_kwh=25.0)
            samples_ns.append(time.perf_counter_ns() - start)

        median_ms = statistics.median(samples_ns) / 1_000_000
        # Conservative ceiling — 10ms is ~3 orders of magnitude above
        # the measured median (see coordinator.py:1167-1176 comment).
        # Failing this means something materially changed.
        assert median_ms < 10.0, (
            f"build_explanation median runtime {median_ms:.3f}ms "
            f"exceeds the 10ms per-tick budget. Constitution P18: "
            f"investigate the regression before merging."
        )
        # Emit measurement for the developer running `pytest -s`. The
        # recorded median in coordinator.py:1167-1176 was captured by
        # running this test locally; rerun + update if hardware changes
        # invalidate it.
        sorted_samples = sorted(samples_ns)
        p95_us = sorted_samples[int(len(sorted_samples) * 0.95)] / 1000
        p99_us = sorted_samples[int(len(sorted_samples) * 0.99)] / 1000
        print(
            f"\nbuild_explanation perf — n={len(samples_ns)} "
            f"median={median_ms * 1000:.2f}us "
            f"p95={p95_us:.2f}us p99={p99_us:.2f}us"
        )


class TestSerialisation:
    def test_to_dict_round_trips(self):
        result = build_explanation(
            {
                "amber": _provider_snapshot("Amber", cost=6.0),
                "globird": _provider_snapshot(
                    "GloBird",
                    cost=4.0,
                    extras={"zerohero_status": "earned", "super_export_kwh": 0},
                ),
            }
        )
        d = result.to_dict()
        assert d["winner_id"] == "globird"
        assert d["margin_aud"] == 2.0
        assert all(
            isinstance(b, dict) and "sentiment" in b and "text" in b
            for b in d["bullets"]
        )
