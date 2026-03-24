"""Read-only sold-comps adapters — official data sources plug in here later."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from ebay_claw.models.comps import SoldCompRecord
from ebay_claw.models.domain import ListingRecord


class SoldCompsAdapter(ABC):
    """Strictly read-only: fetch historical sold comps for pricing intelligence."""

    @abstractmethod
    def fetch_comps_for_listing(self, listing: ListingRecord) -> List[SoldCompRecord]:
        raise NotImplementedError
