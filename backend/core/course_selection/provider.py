"""Future data-source boundary for the new course-selection system."""

from __future__ import annotations

from typing import Protocol

from .model import MarketSnapshot


class CourseSelectionProvider(Protocol):
    """Normalize a remote or unified-store payload into the pure model."""

    def load_market_snapshot(self) -> MarketSnapshot:
        """Return one complete, account-scoped market snapshot."""

