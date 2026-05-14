"""Phase 0 CDR plan fixture fetcher.

Pulls 4 candidate retailer plan lists, applies predicates from
PHASE_0_GROUND_TRUTH.md §4, prints candidates with displayName + planId,
optionally fetches PlanDetailV2 for confirmed IDs.

NOT integration code. Standalone CLI prototype. stdlib only.
Integration HTTP client uses aiohttp via async_get_clientsession(hass)
per locked architecture decision §I.1.

Usage:
    python3 scripts/cdr_pull_plans.py list           # print candidates per retailer
    python3 scripts/cdr_pull_plans.py detail <retailer> <planId>
    python3 scripts/cdr_pull_plans.py search <retailer> <substring>
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# CDR base URLs — energymadeeasy.gov.au is the AER comparison tool which
# proxies major retailers' CDR data. If a retailer 404s here, swap to their
# own DH per jxeeno/energy-cdr-prd-endpoints registry (Phase 1 work).
BASES = {
    "agl": "https://cdr.energymadeeasy.gov.au/agl",
    "red-energy": "https://cdr.energymadeeasy.gov.au/red-energy",
    "globird": "https://cdr.energymadeeasy.gov.au/globird",
}

FIXTURE_DIR = Path(__file__).parent.parent / "tests" / "fixtures" / "phase0"

# Predicates per PHASE_0_GROUND_TRUTH.md §4.
# Note: list endpoint's `type` field is contract type (MARKET/STANDING/
# REGULATED), NOT pricingModel. pricingModel is only in PlanDetailV2.
# So list filtering uses displayName heuristics + customerType + fuelType.
# Confirm pricingModel after fetching detail.

def _residential_elec(p: dict) -> bool:
    return (
        p.get("customerType") == "RESIDENTIAL"
        and p.get("fuelType") == "ELECTRICITY"
        and p.get("type") == "MARKET"
    )


def _name_contains(p: dict, needles: list[str]) -> bool:
    name = (p.get("displayName") or "").upper()
    return any(n.upper() in name for n in needles)


CANDIDATES = [
    (
        "agl",
        "Plan A — AGL flat residential (Value Saver / Standing Offer heuristic)",
        lambda p: _residential_elec(p) and _name_contains(p, ["VALUE SAVER", "STANDING OFFER", "RESIDENTIAL SAVERS", "VIC RESIDENTIAL"]),
    ),
    (
        "red-energy",
        "Plan B — Red Energy TOU residential (Living Energy / Easy Saver heuristic)",
        lambda p: _residential_elec(p) and _name_contains(p, ["LIVING ENERGY", "EASY SAVER", "TIME OF USE", "TIME-OF-USE", "TOU"]),
    ),
    (
        "red-energy",
        "Plans D/E — Red Energy NSW (filter by displayName state)",
        lambda p: _residential_elec(p) and _name_contains(p, ["NSW", "AUSGRID", "ENDEAVOUR", "ESSENTIAL ENERGY"]),
    ),
    (
        "globird",
        "Plan C2 — GloBird ZEROHERO Residential (Flexible Rate) United Energy",
        lambda p: _residential_elec(p)
        and "ZEROHERO" in (p.get("displayName") or "").upper()
        and "UNITED ENERGY" in (p.get("displayName") or "").upper()
        and "VPP" not in (p.get("displayName") or "").upper()
        and "CTL" not in (p.get("displayName") or "").upper(),
    ),
]


def _http_get_json(url: str, x_v: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "x-v": x_v,
            "Accept": "application/json",
            "User-Agent": "PriceHawk-Phase0-Fixture-Pull/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status} from {url}")
        return json.loads(resp.read().decode("utf-8"))


def fetch_list(retailer: str) -> list[dict]:
    base = BASES[retailer]
    plans: list[dict] = []
    page = 1
    while True:
        params = urllib.parse.urlencode({
            "type": "ALL",
            "fuelType": "ELECTRICITY",
            "page": page,
            "page-size": 1000,
        })
        url = f"{base}/cds-au/v1/energy/plans?{params}"
        data = _http_get_json(url, x_v="1")
        chunk = data.get("data", {}).get("plans", [])
        plans.extend(chunk)
        meta = data.get("meta", {})
        total_pages = meta.get("totalPages", 1)
        if page >= total_pages or not chunk:
            break
        page += 1
    return plans


def fetch_detail(retailer: str, plan_id: str) -> dict:
    base = BASES[retailer]
    url = f"{base}/cds-au/v1/energy/plans/{plan_id}"
    return _http_get_json(url, x_v="3")


def cmd_list() -> int:
    """List candidate plans per Phase 0 predicate, print first 5 hits each."""
    seen: dict[str, list[dict]] = {}
    for retailer, label, _ in CANDIDATES:
        if retailer not in seen:
            print(f"\n=== Fetching list for {retailer} ===", file=sys.stderr)
            try:
                seen[retailer] = fetch_list(retailer)
            except urllib.error.HTTPError as e:
                print(f"  ERROR: {e}", file=sys.stderr)
                seen[retailer] = []
            print(f"  fetched {len(seen[retailer])} plans", file=sys.stderr)

    for retailer, label, filter_fn in CANDIDATES:
        matches = [p for p in seen[retailer] if filter_fn(p)]
        print(f"\n--- {label} ---")
        if not matches:
            print("  NO MATCHES — relax predicate or pick manually")
            continue
        for p in matches[:5]:
            pid = p.get("planId", "?")
            name = p.get("displayName", "?")
            ptype = p.get("type", "?")
            eff_from = p.get("effectiveFrom", "?")
            print(f"  {pid:<32} {ptype:<28} effectiveFrom={eff_from}")
            print(f"    {name}")
        if len(matches) > 5:
            print(f"  ... and {len(matches) - 5} more")
    return 0


def cmd_detail(retailer: str, plan_id: str) -> int:
    """Fetch PlanDetailV2 for a single plan and save to fixtures."""
    if retailer not in BASES:
        print(f"unknown retailer: {retailer}. options: {list(BASES)}", file=sys.stderr)
        return 2
    print(f"fetching detail {retailer}/{plan_id}", file=sys.stderr)
    detail = fetch_detail(retailer, plan_id)
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    out = FIXTURE_DIR / f"plan_{retailer}_{plan_id}.json"
    out.write_text(json.dumps(detail, indent=2, sort_keys=True))
    plan = detail.get("data", {})
    pricing_model = plan.get("electricityContract", {}).get("pricingModel", "?")
    eff_from = plan.get("effectiveFrom", "?")
    eff_to = plan.get("effectiveTo", "?")
    print(f"  wrote {out}")
    print(f"  pricingModel: {pricing_model}")
    print(f"  effective: {eff_from} -> {eff_to}")
    return 0


def cmd_search(retailer: str, needle: str) -> int:
    """Print plans whose displayName contains the substring (case-insensitive)."""
    if retailer not in BASES:
        print(f"unknown retailer: {retailer}. options: {list(BASES)}", file=sys.stderr)
        return 2
    needle_u = needle.upper()
    plans = fetch_list(retailer)
    hits = [
        p for p in plans
        if needle_u in (p.get("displayName") or "").upper()
        and p.get("customerType") == "RESIDENTIAL"
        and p.get("fuelType") == "ELECTRICITY"
    ]
    print(f"{len(hits)} residential-electricity matches for '{needle}' in {retailer}")
    for p in hits[:30]:
        pid = p.get("planId", "?")
        name = p.get("displayName", "?")
        ctype = p.get("type", "?")
        print(f"  {pid:<32} [{ctype}] {name}")
    if len(hits) > 30:
        print(f"  ... and {len(hits) - 30} more")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd = argv[1]
    if cmd == "list":
        return cmd_list()
    if cmd == "detail":
        if len(argv) != 4:
            print("usage: detail <retailer> <planId>", file=sys.stderr)
            return 2
        return cmd_detail(argv[2], argv[3])
    if cmd == "search":
        if len(argv) != 4:
            print("usage: search <retailer> <substring>", file=sys.stderr)
            return 2
        return cmd_search(argv[2], argv[3])
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
