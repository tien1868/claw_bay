"""No-op comps — when comps are disabled or no fixture is configured."""

from __future__ import annotations

from typing import List

from ebay_claw.adapters.comps_base import SoldCompsAdapter
from ebay_claw.models.comps import SoldCompRecord
from ebay_claw.models.domain import ListingRecord


class NullSoldCompsAdapter(SoldCompsAdapter):
    def fetch_comps_for_listing(self, listing: ListingRecord) -> List[SoldCompRecord]:
        return []
