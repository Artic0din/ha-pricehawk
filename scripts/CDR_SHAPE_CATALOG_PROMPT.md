# Prompt — CDR PlanDetailV2 shape catalog (for sister Claude Code chat)

Copy everything below the divider into a fresh Claude Code session. The chat will probe live AER Consumer Data Right endpoints across **every published AU energy retailer**, fetch detail for **every plan** (not a sample), and bucket each plan by its JSON-shape signature so we get an exhaustive variant catalog.

The chat needs **no PriceHawk repo access** — it's a self-contained data-engineering task using only public endpoints. Allow **2-6 hours** for a full sweep depending on retailer responsiveness; the script must be **resumable** because some retailers throttle aggressively.

---

# CDR PlanDetailV2 shape catalog — task brief

## Why this exists

Australian energy retailers publish their plans via the AER Consumer Data Right. The `PlanDetailV2` schema is a spec — but every retailer ships their own JSON-shape dialect AND **the same retailer ships different shapes across different plans** (e.g. their flat-rate plans vs their TOU plans use different `rateBlockUType` blocks). I'm building a Home Assistant integration (`PriceHawk`, Python) that consumes these envelopes to render a plan-confirmation summary. Every new shape variant I encounter in production breaks the summariser. I need an **exhaustive catalog** so I can write a defensive parser once instead of patching shape-by-shape.

## Endpoints

- **Registry** (every AU retailer's CDR base URI):
  `https://raw.githubusercontent.com/jxeeno/energy-cdr-prd-endpoints/main/docs/energy-prd-endpoints.json`
  Top-level: `{"data": [{brandName, productReferenceDataBaseUri, ...}]}`. ~78 retailers.

- **Per-retailer plan list** (paginated):
  `{base_uri}/cds-au/v1/energy/plans?fuelType=ELECTRICITY&type=ALL&page-size=1000&effective=CURRENT`
  Header: `x-v: 1`
  Returns `{"data": {"plans": [...]}, "meta": {"totalRecords": N, "totalPages": M}}`.

- **Per-plan detail**:
  `{base_uri}/cds-au/v1/energy/plans/{planId}`
  Header: `x-v: 3`
  Returns `{"data": {electricityContract, ...}}`. **All shape variation lives here.**

## Methodology

### 1. Bootstrap

- Pull the registry. Build a worklist of `(retailer_brand, base_uri)` pairs.
- Skip retailers whose base URI 404s on a HEAD probe.

### 2. Per-retailer pass (resumable, polite)

For each retailer:

1. Pull the **complete** plan list, paginating until `meta.totalPages` exhausted.
2. Filter to `customerType == "RESIDENTIAL"` AND `fuelType == "ELECTRICITY"` AND `type in {MARKET, STANDING}`.
3. For each plan in that filtered set, fetch its detail.
   - **Cache to disk**: `/tmp/cdr-cache/{retailer_brand}/{planId}.json`. If the cache file exists, skip the network call entirely.
   - **Rate limit**: max 1 request/sec per retailer. Some data holders return 429 if you push faster.
   - **Retry on 429/5xx**: exponential backoff, max 3 attempts. After exhaust, log to a `failed.jsonl` and move on.
   - **Checkpoint**: every 100 successful fetches, write the current progress (`{retailer, last_planId, plans_done, plans_total}`) to `/tmp/cdr-cache/_progress.json` so a Ctrl-C resume picks up cleanly.

### 3. Shape-signature extraction

For each cached detail file, compute a **shape signature** — a deterministic string that captures every structural decision the JSON makes for the fields PriceHawk's summariser cares about. Two plans with the same signature can be parsed by identical code; two plans with different signatures cannot.

Build the signature by walking these paths and emitting one token per observation:

#### Top-level electricityContract
- `pricingModel:<value or MISSING>` (e.g. `pricingModel:SINGLE_RATE`)
- For each of these keys, emit `<key>:<TYPE>` where TYPE is `string` / `number` / `list[N]` / `dict` / `null` / `MISSING`:
  - `dailySupplyCharges` (plural)
  - `dailySupplyCharge` (singular)
  - `tariffPeriod`
  - `solarFeedInTariff`
  - `incentives`
  - `controlledLoad`
  - `greenPowerCharges`
  - `discounts`
  - `fees`

#### Per tariffPeriod[0]
- `tp[0].rateBlockUType:<value>`
- `tp[0].<rateBlockUType>:<TYPE>` (the actual nested block — record dict vs list, length if list)
- `tp[0].dailySupplyCharge:<TYPE>`
- `tp[0].dailySupplyCharges:<TYPE>`
- `tp[0].dailySupplyChargeType:<value>`
- For the rates inside the rate block: shape of `rates[0]` keys (sorted, comma-joined)

#### Per solarFeedInTariff[0]
- `fit[0].tariffUType:<value>`
- `fit[0].<tariffUType>:<TYPE>`
- `fit[0].scheme:<value>`
- `fit[0].payerType:<value>`
- For TOU FIT, the inner `rates[0]` key shape

#### Per incentives[0]
- Shape of incentive object keys (sorted, comma-joined)

Concatenate all tokens with `|`. Hash with sha1; first 12 hex chars is the **signature ID**.

### 4. Bucket + analyze

- Group every fetched plan by its signature ID.
- For each unique signature: pick **3 sample planIds** that produce it (one for the README, two for regression tests).
- For each unique signature: emit a **synthetic dict snapshot** — the actual JSON paths described by the signature, not full plan content (so the catalog is readable, not 200KB per row).

### 5. Cross-retailer roll-up

For each retailer × signature combination, count the plans. The interesting output is matrices like:

```
Signature SIG_a3b9c2: 4,217 plans across 12 retailers
  - AGL: 1,054 plans
  - Origin: 712 plans
  - …
Signature SIG_8f1d4a: 2,103 plans across 1 retailer
  - GloBird: 2,103 (FLEXIBLE pricingModel only)
```

This tells me which signatures are load-bearing (cover the mass) vs niche (one retailer's quirk).

## Output

Write a single markdown file `/tmp/cdr-shape-catalog.md` with these sections.

### 1. Sweep summary
- Total retailers probed / reachable / 404
- Total plans listed / fetched / cached / failed
- Total unique signatures discovered
- Wall-clock duration
- Cache size on disk

### 2. Per-retailer coverage table
| Retailer | Plans listed | Detail fetched | Failed | Distinct signatures |
|---|---|---|---|---|
| AGL | 1,105 | 1,103 | 2 | 4 |
| GloBird | 2,103 | 2,103 | 0 | 7 |
| … | | | | |

### 3. Signature catalog (the main deliverable)
For each distinct signature, in descending order of plan count:

```
### Signature SIG_a3b9c2 — 4,217 plans across 12 retailers
**Sample planIds:**
- AGL/AGL999912MR@VEC
- Origin/ORI8847@EME
- EnergyAustralia/EAU772MR@EME

**Token tokens:**
- pricingModel:SINGLE_RATE
- dailySupplyCharges:string
- tariffPeriod:list[1]
- tp[0].rateBlockUType:singleRate
- tp[0].singleRate:dict
- tp[0].rates[0].keys:unitPrice
- solarFeedInTariff:list[1]
- fit[0].tariffUType:singleTariff
- fit[0].singleTariff:dict
- fit[0].rates[0].keys:unitPrice
- incentives:list[3]
- incentives[0].keys:category,description,displayName

**Per-retailer count:**
- AGL: 1,054
- Origin: 712
- …
```

### 4. Field-presence heatmap
| Path | Of N=20 retailers, plans where field present |
|---|---|
| `electricityContract.dailySupplyCharges` (plural) | AGL: 0/1105, Origin: 712/890, GloBird: 0/2103 … |
| `electricityContract.dailySupplyCharge` (singular) | AGL: 0/1105, … |
| `tariffPeriod[].dailySupplyCharge` | AGL: 1103/1103, … |
| `tariffPeriod[].dailySupplyCharges` | … |

### 5. Daily-supply-charge location ranking
Ranked list of all locations the value can live, with retailer × plan-count totals.

### 6. rateBlockUType variants observed
Every observed value of `rateBlockUType`, plus whether the nested block is a dict or a list per signature.

### 7. solarFeedInTariff variants observed
Same treatment for `tariffUType`.

### 8. Surprise findings (free-form)
Bullet list of weirdness:
- Retailers that 404 detail despite listing the plan
- Plans where `tariffPeriod` is empty / missing
- Plans where `electricityContract` itself is missing (do these exist?)
- Numeric-typed fields where the spec says string
- Fields nested in places the spec doesn't document
- Plans whose detail returns a different `pricingModel` than the list says

### 9. Recommended parser shape
A Python function signature + docstring describing the union of every shape a defensive `_summarise_cdr_plan(detail) -> dict[str, str]` should handle. Reference each signature ID it covers.

## Constraints

- **Stdlib only** for Python (or built-in `fetch` for JS / `bun run`). No `requests`, no `httpx`, no npm deps.
- **Cache aggressively** so re-runs are free. Cache key = `{retailer}/{planId}.json`. Idempotent.
- **Be polite**: 1 request/sec per retailer maximum. Some data holders rate-limit.
- **Resumable**: checkpoint progress every 100 plans. A Ctrl-C should be safe; resume from `/tmp/cdr-cache/_progress.json`.
- **Continue on errors**: log `failed.jsonl`, never crash on a single bad plan.
- **Concurrency**: feel free to run 4-8 retailers in parallel (each retailer-thread sticks to its 1-req/sec budget). Don't pound a single retailer with parallel calls — they'll 429.
- **Estimated work**: ~78 retailers × avg 200 plans = 15,600 detail fetches. At 1 req/sec serial = 4-5 hours. With 6-way parallel = ~45 min. Cached re-run = seconds.

## Deliverable

The single file `/tmp/cdr-shape-catalog.md` plus the cache directory `/tmp/cdr-cache/` (which I'll keep — useful for regression test fixtures later). Print only a 5-line summary to stdout when done.

Once the markdown is written, paste its content back to the originating chat. Don't summarise — paste the whole file. Sections 3 (signature catalog) + 5 (supply location ranking) are the load-bearing parts.
