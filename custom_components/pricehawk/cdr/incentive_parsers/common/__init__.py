"""Shared incentive-rule helpers used by per-retailer parser files.

Each helper in this package is retailer-agnostic. It extracts a rule
from CDR free-text and applies math to a CostBreakdown. Per-retailer
modules (agl.py, globird.py, origin.py, etc.) wire these helpers up
based on the specific incentive patterns their retailer publishes.

See scripts/CDR_INCENTIVE_CATALOG.md for the catalog of incentive
shapes observed across all 78 AU energy retailers.
"""
