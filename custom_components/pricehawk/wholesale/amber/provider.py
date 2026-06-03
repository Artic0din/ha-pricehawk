"""AmberProvider — WholesaleProvider implementation for Amber Electric.

Thin subclass of :class:`AmberCalculator` that exposes a stable
provider-level name and satisfies the :class:`WholesaleProvider` Protocol.
The actual calculation lives in :mod:`.calculator`; this class adds no
behaviour. PR 4 introduces coordinator-level provider dispatch — at that
point this class will either grow rate-fetching responsibility or stay
thin while the coordinator routes by :attr:`name`.
"""

from __future__ import annotations

from .calculator import AmberCalculator


class AmberProvider(AmberCalculator):
    """Wholesale provider backed by Amber Electric's REST price feed."""

    name = "amber"
