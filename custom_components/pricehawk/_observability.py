"""Shared observability helpers for parse/aggregate loops.

Constitution P14 (systemic > local): two prior PRs (#159 AEMO CSV parser,
#163 LocalVolts aggregator) shipped the same "count silently-dropped rows
and log a single aggregated line" pattern with *contradictory* threshold
semantics:

* #159 used a strict ``skipped * 10 > candidates`` (i.e. ``>``) so an
  exactly-10% drop stayed DEBUG, with denominator = candidate rows that
  reached the parse step.
* #163 used ``drop_rate >= 0.10`` (i.e. ``>=``) so 10% escalated to
  WARNING, with denominator = ``len(intervals)`` total input.

Both call sites were touching the same conceptual contract. Duplicating
the rule into each file invited (and demonstrably caused) drift. This
module is the single source of truth.

Threshold choice — inclusive ``>=`` (10% exactly escalates):
    The operationally-relevant question is "is the upstream feed
    degrading?". A sustained 1-in-10 failure rate is already enough
    signal that an operator should look — there is no engineering value
    in suppressing the WARNING at the exact boundary. The boundary is
    pinned by a regression test (``test_at_exactly_ten_percent_warns``)
    so future drift requires an explicit code change and an updated
    test, not a quiet refactor.

Denominator choice — candidates that reached the parse step, NOT total
input rows:
    "Drop rate" measures parser/decoder fragility, not pre-filter
    selectivity. For AEMO that means counting only ``D,DISPATCH,PRICE``
    rows for the target region (which is what ``_parse_dispatch_zip``
    increments before ``float()``). For LocalVolts that means counting
    only intervals whose ``end_str`` was present and reached the
    ``fromisoformat()`` attempt. Using ``len(intervals)`` as the
    denominator would dilute the rate with rows the parser intentionally
    skipped (e.g. wrong-region AEMO rows, missing-key LocalVolts rows),
    masking schema drift behind benign filtering.

Zero-total guard:
    If no row ever reached the parse step (``total == 0``), there is
    nothing to compare against — emit nothing and return. The surrounding
    code already handles the "no data" outcome via its existing
    ``rrp is None`` / ``not recent`` return path.
"""

from __future__ import annotations

import logging


def report_drop_rate(
    logger: logging.Logger,
    source: str,
    skipped: int,
    total: int,
    *,
    warn_threshold: float = 0.10,
) -> None:
    """Emit an aggregated drop-count log line.

    Behaviour:
      * ``skipped == 0`` — silent (no log line on the happy path).
      * ``total == 0`` — silent (no candidates reached the parse step;
        guards against ``ZeroDivisionError`` and avoids misleading
        "100% drop" messaging when there was nothing to parse).
      * ``skipped / total >= warn_threshold`` — WARNING.
      * otherwise — DEBUG.

    The ``source`` string is included verbatim in the log message so
    operators can grep by parser (e.g. ``"AEMO dispatch"``,
    ``"LocalVolts aggregator"``) without parsing the logger name.

    Args:
        logger: The caller's module logger. Caller-supplied so log
            records carry the originating module name for filtering.
        source: Human-readable parser identifier for the log message.
        skipped: Number of rows the parser dropped via ``continue``.
        total: Number of rows that *reached* the parse step (the
            denominator the rate is computed against — see module
            docstring for rationale).
        warn_threshold: Inclusive lower bound for WARNING escalation
            (default 0.10 = 10%). Kept as a kwarg so call sites can
            tighten or relax the bound with explicit intent, not so
            users can configure it at runtime.
    """
    if skipped <= 0:
        return
    if total <= 0:
        return

    drop_rate = skipped / total
    if drop_rate >= warn_threshold:
        logger.warning(
            "%s: skipped %d/%d rows (drop_rate=%.1f%%, threshold=%.0f%%) — "
            "investigate upstream data quality",
            source,
            skipped,
            total,
            drop_rate * 100,
            warn_threshold * 100,
        )
    else:
        logger.debug(
            "%s: skipped %d/%d rows (drop_rate=%.1f%%)",
            source,
            skipped,
            total,
            drop_rate * 100,
        )
