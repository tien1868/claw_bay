"""Abstract adapter — swap mock vs real eBay API without touching domain logic."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from ebay_claw.models.domain import ListingRecord


class ListingAdapter(ABC):
    @abstractmethod
    def fetch_active_listings(self) -> List[ListingRecord]:
        raise NotImplementedError
