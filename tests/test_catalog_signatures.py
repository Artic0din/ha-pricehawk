"""Verify _summarise_* helpers handle every CDR shape signature observed in
the live shape catalog (scripts/CDR_SHAPE_CATALOG_PROMPT.md output).

Each test pins one variant from sections 3 + 4 of the catalog. Failures
here are signature drift the parser can't handle yet.
"""

from __future__ import annotations

from custom_components.pricehawk.config_flow import (
    _summarise_cdr_plan,
    _summarise_controlled_load,
    _summarise_fit,
    _summarise_import_rate,
)


# ---------------------------------------------------------------------------
# Section 3 — rateBlockUType variants
# ---------------------------------------------------------------------------


def _wrap(rate_block_u_type: str, block):
    return {
        "tariffPeriod": [
            {
                "rateBlockUType": rate_block_u_type,
                rate_block_u_type: block,
            }
        ]
    }


class TestSingleRateVariants:
    """4 sub-shapes observed in catalog. All should produce a numeric rate."""

    def test_keys_description_displayName_period_rates(self):
        block = {
            "description": "X",
            "displayName": "Rate",
            "period": "P1D",
            "rates": [{"unitPrice": "0.30"}],
        }
        result = _summarise_import_rate(_wrap("singleRate", block))
        assert "33.0" in result, result

    def test_keys_displayName_period_rates(self):
        # Most common (AGL/Amber/Arcline cohort).
        block = {"displayName": "Rate", "period": "P1D", "rates": [{"unitPrice": "0.30"}]}
        result = _summarise_import_rate(_wrap("singleRate", block))
        assert "33.0" in result, result
        # Phase 2.10.4 polish — generic "Rate" displayName is stripped
        # because the surrounding "Import rate:" form prefix supplies it.
        assert "Rate" not in result.split("c/kWh")[0], result

    def test_keys_displayName_rates(self):
        # Blue NRG / Origin sub-shape — no period, no description.
        block = {"displayName": "Rate", "rates": [{"unitPrice": "0.30"}]}
        result = _summarise_import_rate(_wrap("singleRate", block))
        assert "33.0" in result, result

    def test_keys_description_displayName_rates(self):
        # Flow Power sub-shape.
        block = {"description": "X", "displayName": "Rate", "rates": [{"unitPrice": "0.30"}]}
        result = _summarise_import_rate(_wrap("singleRate", block))
        assert "33.0" in result, result


class TestTimeOfUseRatesVariants:
    """3 sub-shapes observed in catalog. All should produce TOU summary."""

    def test_with_description_period_displayName_type_timeOfUse(self):
        # Most common 26-retailer shape.
        blocks = [
            {
                "description": "X",
                "displayName": "Peak",
                "period": "P1D",
                "rates": [{"unitPrice": "0.36"}],
                "timeOfUse": [],
                "type": "PEAK",
            }
        ]
        result = _summarise_import_rate(_wrap("timeOfUseRates", blocks))
        assert "39.6" in result, result
        assert "PEAK" in result, result

    def test_without_description(self):
        # 4-retailer shape (Dodo/GloBird/MYOB/Sumo).
        blocks = [
            {
                "displayName": "Peak",
                "period": "P1D",
                "rates": [{"unitPrice": "0.36"}],
                "timeOfUse": [],
                "type": "PEAK",
            }
        ]
        result = _summarise_import_rate(_wrap("timeOfUseRates", blocks))
        assert "39.6" in result, result

    def test_without_description_or_period(self):
        # Blue NRG / Flow / Lumo / Origin sub-shape.
        blocks = [
            {
                "displayName": "Peak",
                "rates": [{"unitPrice": "0.36"}],
                "timeOfUse": [],
                "type": "PEAK",
            }
        ]
        result = _summarise_import_rate(_wrap("timeOfUseRates", blocks))
        assert "39.6" in result, result


# ---------------------------------------------------------------------------
# Section 4 — solarFeedInTariff variants
# ---------------------------------------------------------------------------


class TestFitSingleTariffVariants:
    def test_period_rates_with_measureUnit_25_retailers(self):
        elec = {
            "solarFeedInTariff": [
                {
                    "tariffUType": "singleTariff",
                    "singleTariff": {
                        "period": "P1D",
                        "rates": [{"unitPrice": "0.05", "measureUnit": None}],
                    },
                }
            ]
        }
        result = _summarise_fit(elec)
        assert "5.50" in result, result

    def test_rates_only_12_retailers_AGL_EnergyAustralia_Origin(self):
        elec = {
            "solarFeedInTariff": [
                {
                    "tariffUType": "singleTariff",
                    "singleTariff": {"rates": [{"unitPrice": "0.05"}]},
                }
            ]
        }
        result = _summarise_fit(elec)
        assert "5.50" in result, result


class TestFitTimeVaryingTariffsVariants:
    def test_displayName_period_rates_timeVariations_type_5_retailers(self):
        elec = {
            "solarFeedInTariff": [
                {
                    "tariffUType": "timeVaryingTariffs",
                    "timeVaryingTariffs": [
                        {
                            "displayName": "Peak",
                            "period": "P1D",
                            "rates": [{"unitPrice": "0.03"}],
                            "timeVariations": [],
                            "type": "PEAK",
                        },
                        {
                            "displayName": "Shoulder",
                            "period": "P1D",
                            "rates": [{"unitPrice": "0.001"}],
                            "timeVariations": [],
                            "type": "SHOULDER",
                        },
                    ],
                }
            ]
        }
        result = _summarise_fit(elec)
        assert "PEAK 3.3" in result, result
        assert "SHOULDER 0.1" in result, result

    def test_displayName_rates_timeVariations_type_no_period_Flow(self):
        elec = {
            "solarFeedInTariff": [
                {
                    "tariffUType": "timeVaryingTariffs",
                    "timeVaryingTariffs": [
                        {
                            "displayName": "Peak",
                            "rates": [{"unitPrice": "0.03"}],
                            "timeVariations": [],
                            "type": "PEAK",
                        },
                    ],
                }
            ]
        }
        result = _summarise_fit(elec)
        assert "PEAK 3.3" in result, result


class TestFitMissing:
    def test_solarFeedInTariff_key_absent_6_retailers(self):
        # Amber, Diamond, ERC, GEE, Real Utilities, ZEN
        result = _summarise_fit({})
        assert result == "none"

    def test_solarFeedInTariff_null(self):
        result = _summarise_fit({"solarFeedInTariff": None})
        assert result == "none"

    def test_solarFeedInTariff_empty_list(self):
        result = _summarise_fit({"solarFeedInTariff": []})
        assert result == "none"


class TestFitMultiTier:
    """Sumo Power + Red Energy ship FIT lists of length 3-9 — multi-tier
    solar bands. Parser must surface ALL entries, not just [0]."""

    def test_three_tiers_summed(self):
        elec = {
            "solarFeedInTariff": [
                {"tariffUType": "singleTariff", "singleTariff": {"rates": [{"unitPrice": "0.10"}]}},
                {"tariffUType": "singleTariff", "singleTariff": {"rates": [{"unitPrice": "0.05"}]}},
                {"tariffUType": "singleTariff", "singleTariff": {"rates": [{"unitPrice": "0.03"}]}},
            ]
        }
        result = _summarise_fit(elec)
        # Each tier shown, joined by " + ".
        assert "11.00" in result
        assert "5.50" in result
        assert "3.30" in result


# ---------------------------------------------------------------------------
# Section 6 — surprise findings, edge cases
# ---------------------------------------------------------------------------


class TestControlledLoadSummary:
    """6 retailers ship CL `timeOfUseRates`; others ship CL `singleRate`.
    Catalog: Energy Locals, ENGIE, GloBird, Lumo, Powershop, ZEN."""

    def test_no_controlled_load_returns_none(self):
        assert _summarise_controlled_load({}) == "none"
        assert _summarise_controlled_load({"controlledLoad": []}) == "none"
        assert _summarise_controlled_load({"controlledLoad": None}) == "none"

    def test_single_rate_cl_block(self):
        elec = {
            "controlledLoad": [
                {
                    "displayName": "Hot Water",
                    "rateBlockUType": "singleRate",
                    "singleRate": {"rates": [{"unitPrice": "0.15"}]},
                }
            ]
        }
        result = _summarise_controlled_load(elec)
        assert "Hot Water" in result
        # 0.15 × 110 = 16.5 c/kWh inc-GST
        assert "16.5" in result

    def test_generic_cl_label_stripped(self):
        # Phase 2.10.4 polish — "Controlled Load" displayName is dropped
        # because the surrounding "Controlled load:" form prefix supplies it.
        elec = {
            "controlledLoad": [
                {
                    "displayName": "Controlled Load",
                    "rateBlockUType": "singleRate",
                    "singleRate": {"rates": [{"unitPrice": "0.13"}]},
                }
            ]
        }
        result = _summarise_controlled_load(elec)
        # Just the rate, no "Controlled Load: Controlled Load 14.3..." dup.
        assert result.count("Controlled Load") == 0
        assert "14.3" in result

    def test_tou_cl_block(self):
        elec = {
            "controlledLoad": [
                {
                    "displayName": "CL TOU",
                    "rateBlockUType": "timeOfUseRates",
                    "timeOfUseRates": [
                        {"type": "OFF_PEAK", "rates": [{"unitPrice": "0.10"}]},
                    ],
                }
            ]
        }
        result = _summarise_controlled_load(elec)
        assert "CL TOU" in result
        assert "11.0" in result

    def test_full_summary_includes_controlled_load_key(self):
        # The CL field must appear in the placeholder dict so the form
        # description doesn't error on missing placeholder.
        out = _summarise_cdr_plan({"data": {"electricityContract": {}}})
        assert "controlled_load" in out
        assert out["controlled_load"] == "none"


class TestEdgeCases:
    def test_empty_tariffPeriod(self):
        # Catalog: "Plans where tariffPeriod is empty / missing" surprise.
        result = _summarise_import_rate({"tariffPeriod": []})
        assert result == "?"

    def test_missing_tariffPeriod(self):
        result = _summarise_import_rate({})
        assert result == "?"

    def test_unitPrice_as_number_not_string(self):
        # Catalog: "Numeric-typed fields where the spec says string" surprise.
        block = {"displayName": "Rate", "rates": [{"unitPrice": 0.30}]}
        result = _summarise_import_rate(_wrap("singleRate", block))
        assert "33.0" in result, result


# ---------------------------------------------------------------------------
# Catalog v2 full-sweep pins (78 retailers, 10,266 plans, 1,724 sigs)
# Each test pins a finding from the v2 catalog so future schema drift
# surfaces as a CI failure, not a UAT bug.
# ---------------------------------------------------------------------------


class TestCatalogV2FullSweep:
    """Pins from /tmp/cdr-shape-catalog-full.md (sweep dated 2026-05-15).

    These are belts-AND-braces tests: most behaviours are also covered by
    the section-3/4 variants above, but pinning the catalog statistics
    explicitly makes regressions traceable to a specific sweep finding.
    """

    def test_supply_charge_at_tariffPeriod_singular_only(self):
        # Catalog §5: 10,262/10,266 plans put dailySupplyCharge (singular)
        # inside tariffPeriod[0]. The 3 spec-allowed alternatives are 0/10,266.
        out = _summarise_cdr_plan(
            {
                "data": {
                    "electricityContract": {
                        "tariffPeriod": [{"dailySupplyCharge": "0.95"}],
                    }
                }
            }
        )
        # 0.95 × 110 = 104.50 c/day
        assert "104.50" in out["daily_supply"], out["daily_supply"]
        assert "inc-GST" in out["daily_supply"]

    def test_supply_charge_missing_returns_not_published(self):
        # Catalog §5: 4 plans miss dailySupplyCharge in all 4 locations
        # (likely embedded-network niche). Must not crash, must say so.
        out = _summarise_cdr_plan(
            {
                "data": {
                    "electricityContract": {
                        "tariffPeriod": [{"singleRate": {"rates": [{"unitPrice": "0.30"}]}}],
                    }
                }
            }
        )
        assert out["daily_supply"] == "not published", out["daily_supply"]

    def test_singleRate_always_dict_per_full_sweep(self):
        # Catalog §6: 4,405 plans across 35 retailers — singleRate is ALWAYS
        # dict, no exceptions in 10,266 plans. Pin the dict path.
        block = {"displayName": "Anytime", "rates": [{"unitPrice": "0.28"}]}
        result = _summarise_import_rate(_wrap("singleRate", block))
        assert "30.8" in result, result

    def test_timeOfUseRates_always_list_per_full_sweep(self):
        # Catalog §6: 5,857 plans across 31 retailers — timeOfUseRates is
        # ALWAYS list. Length distribution: list[3](3060), list[2](2783), list[4](14).
        blocks = [
            {"type": "PEAK", "rates": [{"unitPrice": "0.40"}]},
            {"type": "SHOULDER", "rates": [{"unitPrice": "0.30"}]},
            {"type": "OFF_PEAK", "rates": [{"unitPrice": "0.20"}]},
        ]
        result = _summarise_import_rate(_wrap("timeOfUseRates", blocks))
        assert "PEAK 44.0" in result
        assert "SHOULDER 33.0" in result
        assert "OFF_PEAK 22.0" in result

    def test_timeOfUseRates_list4_max_observed(self):
        # Catalog §6: 14 plans ship timeOfUseRates of length 4 (max observed).
        # Parser must surface ALL 4 entries.
        blocks = [
            {"type": "PEAK", "rates": [{"unitPrice": "0.40"}]},
            {"type": "SHOULDER_AM", "rates": [{"unitPrice": "0.32"}]},
            {"type": "SHOULDER_PM", "rates": [{"unitPrice": "0.28"}]},
            {"type": "OFF_PEAK", "rates": [{"unitPrice": "0.18"}]},
        ]
        result = _summarise_import_rate(_wrap("timeOfUseRates", blocks))
        for label in ("PEAK 44.0", "SHOULDER_AM 35.2", "SHOULDER_PM 30.8", "OFF_PEAK 19.8"):
            assert label in result, f"{label} missing from {result}"

    def test_fit_missing_for_345_plans_across_10_retailers(self):
        # Catalog §7: solarFeedInTariff key absent for 345 plans (3.4% of all).
        # Retailers: Real Utilities, ERC, GEE, ZEN, all-of-Diamond + subsets
        # of Amber, MYOB/OVO, Powershop, etc. Must return "none", not crash.
        for elec in (
            {},
            {"solarFeedInTariff": None},
            {"solarFeedInTariff": []},
        ):
            assert _summarise_fit(elec) == "none"

    def test_fit_singleTariff_dominant_9441_plans(self):
        # Catalog §7: 9,441 plans (95% of FIT-equipped) ship singleTariff.
        elec = {
            "solarFeedInTariff": [
                {
                    "tariffUType": "singleTariff",
                    "singleTariff": {"rates": [{"unitPrice": "0.075"}]},
                }
            ]
        }
        result = _summarise_fit(elec)
        # 0.075 × 110 = 8.25 c/kWh inc-GST
        assert "8.25" in result, result

    def test_fit_timeVaryingTariffs_list3_max_observed(self):
        # Catalog §7: 161 plans ship timeVaryingTariffs of length 3 (PEAK +
        # SHOULDER + OFF_PEAK FIT). Parser must walk all 3.
        elec = {
            "solarFeedInTariff": [
                {
                    "tariffUType": "timeVaryingTariffs",
                    "timeVaryingTariffs": [
                        {"type": "PEAK", "rates": [{"unitPrice": "0.06"}]},
                        {"type": "SHOULDER", "rates": [{"unitPrice": "0.04"}]},
                        {"type": "OFF_PEAK", "rates": [{"unitPrice": "0.02"}]},
                    ],
                }
            ]
        }
        result = _summarise_fit(elec)
        assert "PEAK 6.6" in result
        assert "SHOULDER 4.4" in result
        assert "OFF_PEAK 2.2" in result

    def test_fit_multi_tier_9_bands_max_observed(self):
        # Catalog §3: Sumo Power + Red Energy ship FIT lists up to 9 entries
        # (multi-tier solar bands at decreasing rates). Parser must surface
        # all 9 entries, not just [0].
        elec = {
            "solarFeedInTariff": [
                {
                    "tariffUType": "singleTariff",
                    "singleTariff": {"rates": [{"unitPrice": f"0.{i:02d}"}]},
                }
                for i in range(15, 6, -1)  # 9 tiers: 0.15 → 0.07
            ]
        }
        result = _summarise_fit(elec)
        # First tier 0.15 × 110 = 16.50, last tier 0.07 × 110 = 7.70
        assert "16.50" in result
        assert "7.70" in result
        # 9 tiers means 8 " + " separators
        assert result.count(" + ") == 8, result

    def test_fit_scheme_OTHER_freeform_not_rejected(self):
        # Catalog §7: scheme:OTHER dominates (6,656 plans). Spec enum doesn't
        # include OTHER but registry-wide convention does. Parser ignores
        # scheme entirely (display walks rates only) — pin that behaviour.
        elec = {
            "solarFeedInTariff": [
                {
                    "tariffUType": "singleTariff",
                    "scheme": "OTHER",  # not in spec enum
                    "payerType": "RETAILER",
                    "singleTariff": {"rates": [{"unitPrice": "0.05"}]},
                }
            ]
        }
        result = _summarise_fit(elec)
        assert "5.50" in result, result

    def test_incentive_category_GIFT_freeform_not_rejected(self):
        # Catalog §8: 50 AGL plans ship category:GIFT (not in CDR docs;
        # docs claim DISCOUNT/BONUS/OTHER only). Parser uses displayName,
        # not category, so freeform values must not break the summary.
        out = _summarise_cdr_plan(
            {
                "data": {
                    "electricityContract": {
                        "tariffPeriod": [{"dailySupplyCharge": "0.85"}],
                        "incentives": [
                            {"displayName": "Welcome Gift", "category": "GIFT"},
                            {"displayName": "Free Movie", "category": "ACCOUNT_CREDIT"},
                        ],
                    }
                }
            }
        )
        assert "Welcome Gift" in out["incentives"]
        assert "Free Movie" in out["incentives"]

    def test_volume_field_as_number_not_string(self):
        # Catalog §8: SIG_caccf1fa28bc — 52 Origin plans ship rates[].volume
        # as a number (spec says string). Parser doesn't read volume but
        # unitPrice can also be number; pin that float() coerces both.
        block = {"displayName": "Anytime", "rates": [{"unitPrice": 0.275, "volume": 1500}]}
        result = _summarise_import_rate(_wrap("singleRate", block))
        # 0.275 × 110 = 30.25, displayed via :.1f → "30.3" (banker's rounding)
        assert "30.3" in result, result

    def test_full_summary_handles_origin_top_signature(self):
        # Catalog §3: SIG_f12c7686760c — 78 plans, Origin's most common
        # shape. Pin that the full _summarise_cdr_plan walks it cleanly.
        out = _summarise_cdr_plan(
            {
                "data": {
                    "brandName": "Origin Energy",
                    "displayName": "Anytime Plus",
                    "effectiveFrom": "2025-12-01",
                    "electricityContract": {
                        "pricingModel": "TIME_OF_USE_CONT_LOAD",
                        "tariffPeriod": [
                            {
                                "dailySupplyCharge": "0.95",
                                "dailySupplyChargeType": "SINGLE",
                                "rateBlockUType": "timeOfUseRates",
                                "timeOfUseRates": [
                                    {
                                        "type": "PEAK",
                                        "displayName": "Peak",
                                        "period": "P1D",
                                        "rates": [{"unitPrice": "0.42"}],
                                        "timeOfUse": [],
                                    },
                                    {
                                        "type": "SHOULDER",
                                        "displayName": "Shoulder",
                                        "period": "P1D",
                                        "rates": [{"unitPrice": "0.30"}],
                                        "timeOfUse": [],
                                    },
                                    {
                                        "type": "OFF_PEAK",
                                        "displayName": "Off Peak",
                                        "period": "P1D",
                                        "rates": [{"unitPrice": "0.20"}],
                                        "timeOfUse": [],
                                    },
                                ],
                            }
                        ],
                        "solarFeedInTariff": [
                            {
                                "tariffUType": "singleTariff",
                                "scheme": "OTHER",
                                "payerType": "RETAILER",
                                "singleTariff": {"period": "P1D", "rates": [{"unitPrice": "0.05"}]},
                            }
                        ],
                        "incentives": [{"category": "OTHER", "displayName": "Loyalty Credit"}],
                        "controlledLoad": [
                            {
                                "displayName": "Hot Water",
                                "rateBlockUType": "singleRate",
                                "singleRate": {"rates": [{"unitPrice": "0.18"}]},
                            }
                        ],
                    },
                }
            }
        )
        assert out["brand"] == "Origin Energy"
        assert out["plan_name"] == "Anytime Plus"
        assert out["effective"] == "2025-12-01"
        assert "104.50" in out["daily_supply"]
        assert "PEAK 46.2" in out["import_rate"]
        assert "SHOULDER 33.0" in out["import_rate"]
        assert "OFF_PEAK 22.0" in out["import_rate"]
        assert "5.50" in out["feed_in"]
        assert "Loyalty Credit" in out["incentives"]
        assert "Hot Water" in out["controlled_load"]
        assert "19.8" in out["controlled_load"]  # 0.18 × 110
