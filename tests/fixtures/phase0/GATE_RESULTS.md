# Phase 0 Gate Results — Cross-Check Report

**Purpose:** Independent verification that the Phase 0 evaluator prototype
(`scripts/cdr_evaluator_proto.py`) reproduces a separate bucket-aggregation
pass over the same fixtures. The two code paths share no logic except input
parsing. If they agree, the evaluator's structural logic is internally
consistent.

**This does NOT replace human hand-calc** — that remains the canonical
ground-truth per locked decision D-P0-2 / design doc §F. Use this report
to drive what to hand-check first: focus on buckets with the largest kWh
contribution, validate the rate × kWh math against the plan PDF, sum the
buckets, apply × 1.10 for GST, compare to the evaluator total.

All dollar values shown GST-inclusive unless suffixed `_ex`.

## Summary

| Plan | Description | Days | Slots | Evaluator $ | Independent $ | Diff $ | Diff % |
|------|-------------|-----:|------:|------------:|--------------:|-------:|-------:|
| A | AGL Residential Smart Saver (SINGLE_RATE NSW) | 7 | 336 | $89.40 | $89.40 | $0.0000 | 0.0000% |
| B | Red Taronga Flex (TIME_OF_USE NSW Ausgrid) | 7 | 336 | $86.67 | $86.67 | $0.0000 | 0.0000% |
| C1 | Synthetic FLEXIBLE (stepped 24.6c -> 30.1c at 15 kWh/day) | 7 | 336 | $88.71 | $88.71 | $0.0000 | 0.0000% |
| C2 | GloBird ZEROHERO United Energy (FLEXIBLE + parser) | 7 | 336 | $65.42 | $65.42 | $0.0000 | 0.0000% |
| D | Red Taronga Flex × DST backward 2026-04-05 (25h day) | 1 | 50 | $6.86 | $6.86 | $0.0000 | 0.0000% |
| E | Red Taronga Flex × DST forward 2026-10-04 (23h day) | 1 | 46 | $6.48 | $6.48 | $0.0000 | 0.0000% |

## Per-plan bucket breakdown (ex-GST)

Each bucket = sum of half-hour kWh that fell into one TOU window slot × the applicable rate.
Useful for hand-spreadsheet replication: each row in your spreadsheet should match a row here.

### Plan A — AGL Residential Smart Saver (SINGLE_RATE NSW)
- plan_id: `AGL907738MRE6@EME`
- supply ex-GST: $5.5500  (7 days × daily supply)
- FIT credit ex-GST: $-0.0062  (negative = credit toward bill)
- Incentive credit ex-GST (parser output): $0.0000

| Bucket | kWh | Cost ex-GST |
|--------|----:|------------:|
| first 3900 kWh/period @ 0.2922/kWh | 259.192 | $75.7360 |

### Plan B — Red Taronga Flex (TIME_OF_USE NSW Ausgrid)
- plan_id: `RED552831MRE15@EME`
- supply ex-GST: $6.4200  (7 days × daily supply)
- FIT credit ex-GST: $-0.0128  (negative = credit toward bill)
- Incentive credit ex-GST (parser output): $0.0000

| Bucket | kWh | Cost ex-GST |
|--------|----:|------------:|
| OFF_PEAK flat 0.2198/kWh | 116.208 | $25.5424 |
| PEAK flat 0.4385/kWh | 32.099 | $14.0755 |
| SHOULDER flat 0.2955/kWh | 110.886 | $32.7667 |

### Plan C1 — Synthetic FLEXIBLE (stepped 24.6c -> 30.1c at 15 kWh/day)
- plan_id: `PHASE0-C1-FLEXIBLE-SYNTHETIC`
- supply ex-GST: $8.4000  (7 days × daily supply)
- FIT credit ex-GST: $0.0000  (negative = credit toward bill)
- Incentive credit ex-GST (parser output): $0.0000

| Bucket | kWh | Cost ex-GST |
|--------|----:|------------:|
| PEAK <15.0 kWh/day @ 0.246/kWh | 104.918 | $25.8097 |
| PEAK flat 0.301/kWh | 154.275 | $46.4367 |

### Plan C2 — GloBird ZEROHERO United Energy (FLEXIBLE + parser)
- plan_id: `GLO731031MR@VEC`
- supply ex-GST: $7.3500  (7 days × daily supply)
- FIT credit ex-GST: $-0.0006  (negative = credit toward bill)
- Incentive credit ex-GST (parser output): $-2.0005

| Bucket | kWh | Cost ex-GST |
|--------|----:|------------:|
| OFF_PEAK flat 0.000001/kWh | 54.760 | $0.0001 |
| PEAK flat 0.36/kWh | 25.743 | $9.2675 |
| SHOULDER flat 0.25/kWh | 178.689 | $44.6722 |

### Plan D — Red Taronga Flex × DST backward 2026-04-05 (25h day)
- plan_id: `RED552831MRE15@EME`
- supply ex-GST: $0.9200  (1 days × daily supply)
- FIT credit ex-GST: $-2.1690  (negative = credit toward bill)
- Incentive credit ex-GST (parser output): $0.0000

| Bucket | kWh | Cost ex-GST |
|--------|----:|------------:|
| OFF_PEAK flat 0.2198/kWh | 8.000 | $1.7584 |
| SHOULDER flat 0.2955/kWh | 19.400 | $5.7327 |

### Plan E — Red Taronga Flex × DST forward 2026-10-04 (23h day)
- plan_id: `RED552831MRE15@EME`
- supply ex-GST: $0.9200  (1 days × daily supply)
- FIT credit ex-GST: $-2.1690  (negative = credit toward bill)
- Incentive credit ex-GST (parser output): $0.0000

| Bucket | kWh | Cost ex-GST |
|--------|----:|------------:|
| OFF_PEAK flat 0.2198/kWh | 6.400 | $1.4067 |
| SHOULDER flat 0.2955/kWh | 19.400 | $5.7327 |

## Hand-calc gate criteria

Per `scripts/PHASE_0_GROUND_TRUTH.md` §6:
- Plans A / B / C1 / C2: within ±5% of hand-calc total_aud_inc_gst.
- Plans D / E: within ±$0.05 absolute (24h windows).
- C2 (GloBird ZEROHERO) is load-bearing — fail = Approach A fallback.

## How to read this report

1. For each plan, sum (Bucket cost_ex_gst) + supply_ex + fit_credit_ex + incentive_credit_inc.
2. Multiply the sum by 1.10 for GST.
3. The result should equal `Independent $` to 2 d.p.
4. `Diff $` between Evaluator and Independent should be ~$0.00 — the two are computing the same thing two ways. Non-zero diff indicates a bug in one path.
5. For the canonical Phase 0 gate, replace this report's bucket totals with your hand-calc spreadsheet values and re-check the per-plan diff.