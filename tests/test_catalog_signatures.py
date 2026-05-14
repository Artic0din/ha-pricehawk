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
    return {"tariffPeriod": [{
        "rateBlockUType": rate_block_u_type,
        rate_block_u_type: block,
    }]}


class TestSingleRateVariants:
    """4 sub-shapes observed in catalog. All should produce a numeric rate."""

    def test_keys_description_displayName_period_rates(self):
        block = {"description": "X", "displayName": "Rate", "period": "P1D",
                 "rates": [{"unitPrice": "0.30"}]}
        result = _summarise_import_rate(_wrap("singleRate", block))
        assert "33.0" in result, result

    def test_keys_displayName_period_rates(self):
        # Most common (AGL/Amber/Arcline cohort).
        block = {"displayName": "Rate", "period": "P1D",
                 "rates": [{"unitPrice": "0.30"}]}
        result = _summarise_import_rate(_wrap("singleRate", block))
        assert "33.0" in result, result

    def test_keys_displayName_rates(self):
        # Blue NRG / Origin sub-shape — no period, no description.
        block = {"displayName": "Rate", "rates": [{"unitPrice": "0.30"}]}
        result = _summarise_import_rate(_wrap("singleRate", block))
        assert "33.0" in result, result

    def test_keys_description_displayName_rates(self):
        # Flow Power sub-shape.
        block = {"description": "X", "displayName": "Rate",
                 "rates": [{"unitPrice": "0.30"}]}
        result = _summarise_import_rate(_wrap("singleRate", block))
        assert "33.0" in result, result


class TestTimeOfUseRatesVariants:
    """3 sub-shapes observed in catalog. All should produce TOU summary."""

    def test_with_description_period_displayName_type_timeOfUse(self):
        # Most common 26-retailer shape.
        blocks = [{"description": "X", "displayName": "Peak", "period": "P1D",
                   "rates": [{"unitPrice": "0.36"}], "timeOfUse": [],
                   "type": "PEAK"}]
        result = _summarise_import_rate(_wrap("timeOfUseRates", blocks))
        assert "39.6" in result, result
        assert "PEAK" in result, result

    def test_without_description(self):
        # 4-retailer shape (Dodo/GloBird/MYOB/Sumo).
        blocks = [{"displayName": "Peak", "period": "P1D",
                   "rates": [{"unitPrice": "0.36"}], "timeOfUse": [],
                   "type": "PEAK"}]
        result = _summarise_import_rate(_wrap("timeOfUseRates", blocks))
        assert "39.6" in result, result

    def test_without_description_or_period(self):
        # Blue NRG / Flow / Lumo / Origin sub-shape.
        blocks = [{"displayName": "Peak", "rates": [{"unitPrice": "0.36"}],
                   "timeOfUse": [], "type": "PEAK"}]
        result = _summarise_import_rate(_wrap("timeOfUseRates", blocks))
        assert "39.6" in result, result


# ---------------------------------------------------------------------------
# Section 4 — solarFeedInTariff variants
# ---------------------------------------------------------------------------


class TestFitSingleTariffVariants:
    def test_period_rates_with_measureUnit_25_retailers(self):
        elec = {"solarFeedInTariff": [{
            "tariffUType": "singleTariff",
            "singleTariff": {"period": "P1D", "rates": [{"unitPrice": "0.05", "measureUnit": None}]},
        }]}
        result = _summarise_fit(elec)
        assert "5.50" in result, result

    def test_rates_only_12_retailers_AGL_EnergyAustralia_Origin(self):
        elec = {"solarFeedInTariff": [{
            "tariffUType": "singleTariff",
            "singleTariff": {"rates": [{"unitPrice": "0.05"}]},
        }]}
        result = _summarise_fit(elec)
        assert "5.50" in result, result


class TestFitTimeVaryingTariffsVariants:
    def test_displayName_period_rates_timeVariations_type_5_retailers(self):
        elec = {"solarFeedInTariff": [{
            "tariffUType": "timeVaryingTariffs",
            "timeVaryingTariffs": [
                {"displayName": "Peak", "period": "P1D",
                 "rates": [{"unitPrice": "0.03"}], "timeVariations": [],
                 "type": "PEAK"},
                {"displayName": "Shoulder", "period": "P1D",
                 "rates": [{"unitPrice": "0.001"}], "timeVariations": [],
                 "type": "SHOULDER"},
            ],
        }]}
        result = _summarise_fit(elec)
        assert "PEAK 3.3" in result, result
        assert "SHOULDER 0.1" in result, result

    def test_displayName_rates_timeVariations_type_no_period_Flow(self):
        elec = {"solarFeedInTariff": [{
            "tariffUType": "timeVaryingTariffs",
            "timeVaryingTariffs": [
                {"displayName": "Peak", "rates": [{"unitPrice": "0.03"}],
                 "timeVariations": [], "type": "PEAK"},
            ],
        }]}
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
        elec = {"solarFeedInTariff": [
            {"tariffUType": "singleTariff", "singleTariff": {"rates": [{"unitPrice": "0.10"}]}},
            {"tariffUType": "singleTariff", "singleTariff": {"rates": [{"unitPrice": "0.05"}]}},
            {"tariffUType": "singleTariff", "singleTariff": {"rates": [{"unitPrice": "0.03"}]}},
        ]}
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
        elec = {"controlledLoad": [{
            "displayName": "Hot Water",
            "rateBlockUType": "singleRate",
            "singleRate": {"rates": [{"unitPrice": "0.15"}]},
        }]}
        result = _summarise_controlled_load(elec)
        assert "Hot Water" in result
        # 0.15 × 110 = 16.5 c/kWh inc-GST
        assert "16.5" in result

    def test_tou_cl_block(self):
        elec = {"controlledLoad": [{
            "displayName": "CL TOU",
            "rateBlockUType": "timeOfUseRates",
            "timeOfUseRates": [
                {"type": "OFF_PEAK", "rates": [{"unitPrice": "0.10"}]},
            ],
        }]}
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
