"""Streaming engine adapter for cdr.evaluate.

Bridges the streaming API the HA coordinator uses (`engine.update(power_w,
dt)` per power reading, properties read on demand) to the batch API of
`cdr.evaluate` (consumes a list of half-hour slots).

The legacy `tariff_engine.TariffEngine` is streaming-native. This adapter
mimics its public surface (update / reset_daily / properties / to_dict /
from_dict) so the GloBirdProvider can swap its internal engine to CDR-
driven logic without touching the coordinator or sensor wiring.

Slot buffer semantics:
- Power readings are accumulated into a "current slot" with start time
  aligned to the previous half-hour boundary (00:00 / 00:30 / 01:00 / ...).
- Each `update(power_w, now)` adds `(power_w / 1000) * delta_h kWh` to
  either the import or export side of the current slot.
- When `now` crosses into the next half-hour, the current slot is sealed
  and appended to `_slots_today`; a new current slot starts.
- The Phase 0 prototype's `GAP_PROTECTION_MAX_DELTA_H = 0.1h` cap is
  preserved (legacy behaviour: if HA misses readings for >6 min,
  accumulate only 6 min of energy to avoid runaway state).
- Property reads call `cdr.evaluate` over `_slots_today + [_current_slot]`
  and cache the CostBreakdown until the next `update()`.

The cached CostBreakdown is invalidated on every `update()` (lazy
recompute on next property read). For sensible HA polling cadence
(~30 s) and a 48-slot day, this is ~O(48) per recompute = trivial.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from .evaluator import CostBreakdown, evaluate

GAP_PROTECTION_MAX_DELTA_H = 0.1  # matches tariff_engine constant


def _slot_start(dt: datetime) -> datetime:
    """Round down to nearest half-hour boundary."""
    return dt.replace(minute=(dt.minute // 30) * 30, second=0, microsecond=0)


class CdrStreamingEngine:
    """Stateful streaming wrapper around `cdr.evaluate`.

    Public surface deliberately mirrors `tariff_engine.TariffEngine` so
    `GloBirdProvider` can swap internals without changing the Provider
    Protocol it satisfies.
    """

    def __init__(self, plan: dict) -> None:
        self._plan = plan
        self._slots_today: list[dict] = []
        self._current_slot_start: datetime | None = None
        self._current_slot_import_kwh: float = 0.0
        self._current_slot_export_kwh: float = 0.0
        self._last_update: datetime | None = None
        self._last_reset_date = None
        # Lazy cache of CostBreakdown over today's slots; invalidated by update()
        self._bd_cache: CostBreakdown | None = None

    # -- Streaming API -----------------------------------------------------

    def update(self, grid_power_w: float, now_local: datetime) -> None:
        """Ingest a power reading. Positive = import, negative = export."""
        if self._last_update is None:
            self._last_update = now_local
            self._current_slot_start = _slot_start(now_local)
            self._bd_cache = None
            return

        # Midnight reset detection (caller may have not called reset_daily yet)
        if self._last_reset_date is None:
            self._last_reset_date = now_local.date()
        elif now_local.date() != self._last_reset_date:
            # Auto-roll daily state on date change (defensive — coordinator
            # should call reset_daily but this prevents stale-state bugs)
            self.reset_daily()
            self._last_reset_date = now_local.date()
            self._current_slot_start = _slot_start(now_local)
            self._last_update = now_local
            self._bd_cache = None
            return

        delta_h = (now_local - self._last_update).total_seconds() / 3600
        if delta_h <= 0:
            return
        delta_h = min(delta_h, GAP_PROTECTION_MAX_DELTA_H)
        self._last_update = now_local

        # Energy this tick
        grid_kw = grid_power_w / 1000.0
        import_kwh = max(0.0, grid_kw) * delta_h
        export_kwh = max(0.0, -grid_kw) * delta_h

        # Roll to next slot if boundary crossed
        new_slot_start = _slot_start(now_local)
        if self._current_slot_start is None:
            self._current_slot_start = new_slot_start
        elif new_slot_start != self._current_slot_start:
            self._seal_current_slot()
            self._current_slot_start = new_slot_start

        self._current_slot_import_kwh += import_kwh
        self._current_slot_export_kwh += export_kwh
        self._bd_cache = None  # invalidate

    def reset_daily(self) -> None:
        """Zero today's slot buffer. Called at midnight by the coordinator."""
        self._slots_today = []
        self._current_slot_start = None
        self._current_slot_import_kwh = 0.0
        self._current_slot_export_kwh = 0.0
        # Keep _last_update so next update() computes delta correctly
        self._bd_cache = None

    # -- Internal helpers --------------------------------------------------

    def _seal_current_slot(self) -> None:
        """Append current accumulator as a finalised slot."""
        if self._current_slot_start is None:
            return
        if (self._current_slot_import_kwh + self._current_slot_export_kwh) == 0:
            self._current_slot_import_kwh = 0.0
            self._current_slot_export_kwh = 0.0
            return
        self._slots_today.append({
            "ts_local": self._current_slot_start.isoformat(),
            "grid_import_kwh": self._current_slot_import_kwh,
            "grid_export_kwh": self._current_slot_export_kwh,
            "solar_kwh": 0.0,  # not tracked in streaming; cdr.evaluate uses grid_export
        })
        self._current_slot_import_kwh = 0.0
        self._current_slot_export_kwh = 0.0

    def _live_slots(self) -> list[dict]:
        """Return slots-today + the in-flight current slot (if non-empty)."""
        slots = list(self._slots_today)
        if (
            self._current_slot_start is not None
            and (self._current_slot_import_kwh + self._current_slot_export_kwh) > 0
        ):
            slots.append({
                "ts_local": self._current_slot_start.isoformat(),
                "grid_import_kwh": self._current_slot_import_kwh,
                "grid_export_kwh": self._current_slot_export_kwh,
                "solar_kwh": 0.0,
            })
        return slots

    def _breakdown(self) -> CostBreakdown:
        if self._bd_cache is not None:
            return self._bd_cache
        slots = self._live_slots()
        self._bd_cache = evaluate(self._plan, {"slots": slots})
        return self._bd_cache

    def _current_tou_rate_ex_gst(
        self, now: datetime, side: str
    ) -> Decimal:
        """Look up current-clock-time TOU rate for `side` ∈ {"import","export"}.

        Returns ex-GST $/kWh. Used by `current_import_rate_c_kwh` /
        `current_export_rate_c_kwh` properties — fast lookup, no evaluator
        invocation.
        """
        from .evaluator import _resolve_tou_rate, slot_in_window  # noqa: F401
        plan_data = self._plan.get("data", self._plan)
        elec = plan_data.get("electricityContract", {}) or {}
        tps = elec.get("tariffPeriod", []) or []
        if not tps:
            return Decimal("0")
        tp = tps[0]
        if side == "import":
            if tp.get("rateBlockUType") == "singleRate":
                rates = (tp.get("singleRate") or {}).get("rates", []) or []
                return Decimal(str(rates[0].get("unitPrice", 0))) if rates else Decimal("0")
            tou_rates = tp.get("timeOfUseRates", []) or []
            entry = _resolve_tou_rate(now, tou_rates)
            if not entry:
                return Decimal("0")
            rates = entry.get("rates", []) or []
            return Decimal(str(rates[0].get("unitPrice", 0))) if rates else Decimal("0")
        # export side
        fits = elec.get("solarFeedInTariff", []) or []
        for fit in fits:
            utype = fit.get("tariffUType")
            if utype == "timeVaryingTariffs":
                for tvt in fit.get("timeVaryingTariffs") or []:
                    for tv in tvt.get("timeVariations") or []:
                        if slot_in_window(
                            now,
                            tv.get("days", []),
                            tv.get("startTime", "00:00"),
                            tv.get("endTime", "23:59"),
                        ):
                            rates = tvt.get("rates", []) or []
                            return Decimal(str(rates[0].get("unitPrice", 0))) if rates else Decimal("0")
            elif utype == "singleTariff":
                st = fit.get("singleTariff") or {}
                rates = st.get("rates", []) or []
                if rates:
                    return Decimal(str(rates[0].get("unitPrice", 0)))
        return Decimal("0")

    # -- Properties (TariffEngine-compatible) ------------------------------

    @property
    def current_import_rate_c_kwh(self) -> float:
        """Marginal import rate INC-GST cents/kWh at current clock time."""
        if self._last_update is None:
            return 0.0
        rate_ex = self._current_tou_rate_ex_gst(self._last_update, "import")
        return float(rate_ex * Decimal("1.10") * Decimal("100"))

    @property
    def current_export_rate_c_kwh(self) -> float:
        """Effective export rate INC-GST cents/kWh at current clock time."""
        if self._last_update is None:
            return 0.0
        rate_ex = self._current_tou_rate_ex_gst(self._last_update, "export")
        return float(rate_ex * Decimal("1.10") * Decimal("100"))

    @property
    def import_kwh_today(self) -> float:
        total = sum(s["grid_import_kwh"] for s in self._slots_today)
        total += self._current_slot_import_kwh
        return float(total)

    @property
    def export_kwh_today(self) -> float:
        total = sum(s["grid_export_kwh"] for s in self._slots_today)
        total += self._current_slot_export_kwh
        return float(total)

    @property
    def import_cost_today_c(self) -> float:
        """Import-only cost in cents INC-GST."""
        bd = self._breakdown()
        return float((bd.import_aud_ex_gst * Decimal("1.10") * Decimal("100")))

    @property
    def export_earnings_today_c(self) -> float:
        """FIT earnings in cents INC-GST (positive value)."""
        bd = self._breakdown()
        # export_aud_ex_gst is stored as NEGATIVE cost; flip sign for earnings
        return float((-bd.export_aud_ex_gst * Decimal("1.10") * Decimal("100")))

    @property
    def net_daily_cost_aud(self) -> float:
        """Net daily total INC-GST AUD."""
        bd = self._breakdown()
        return float(bd.total_aud_inc_gst)

    @property
    def zerohero_status(self) -> str:
        """Compatibility shim. Phase 1.2 doesn't expose the granular state
        machine; returns "earned" / "lost" / "pending" based on the
        evaluator's incentive trace.
        """
        bd = self._breakdown()
        for t in bd.trace:
            if t.get("incentive") == "zerohero":
                return "earned"
        # No credit yet — could be lost or pending (legacy semantics).
        # Without per-tick state we return "pending" until day ends; legacy's
        # rich state machine is deferred to v1.5.1 unless dashboard demands it.
        return "pending"

    @property
    def super_export_kwh(self) -> float:
        """Cumulative kWh credited to super-export today (PDF cap 10 kWh)."""
        bd = self._breakdown()
        # Reconstruct from incentive trace
        credited = 0.0
        for t in bd.trace:
            if t.get("incentive") == "super_export":
                credited += float(t.get("credited_kwh", 0))
        return credited

    # -- State serialisation ----------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "slots_today": self._slots_today,
            "current_slot_start": self._current_slot_start.isoformat() if self._current_slot_start else None,
            "current_slot_import_kwh": self._current_slot_import_kwh,
            "current_slot_export_kwh": self._current_slot_export_kwh,
            "last_update": self._last_update.isoformat() if self._last_update else None,
            "last_reset_date": self._last_reset_date.isoformat() if self._last_reset_date else None,
        }

    @classmethod
    def from_dict(cls, plan: dict, data: dict[str, Any], today) -> "CdrStreamingEngine":
        engine = cls(plan)
        # Restore today's accumulators only if stored date is today
        stored_reset = data.get("last_reset_date")
        if stored_reset:
            from datetime import date as _date
            stored_date = _date.fromisoformat(stored_reset)
            engine._last_reset_date = stored_date
            if stored_date == today:
                engine._slots_today = data.get("slots_today", []) or []
                css = data.get("current_slot_start")
                if css:
                    engine._current_slot_start = datetime.fromisoformat(css)
                engine._current_slot_import_kwh = float(data.get("current_slot_import_kwh", 0))
                engine._current_slot_export_kwh = float(data.get("current_slot_export_kwh", 0))
        lu = data.get("last_update")
        if lu:
            engine._last_update = datetime.fromisoformat(lu)
        return engine
