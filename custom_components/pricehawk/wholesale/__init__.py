"""Wholesale spot-price provider namespace.

Houses the WholesaleProvider Protocol and one subpackage per provider
implementation (Amber today; Flow Power lands in PR 4).
"""

from __future__ import annotations

from .protocol import WholesaleProvider

__all__ = ["WholesaleProvider"]
