"""Formal compliance seam — placeholder for future eBay Compliance API integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ebay_claw.compliance.boundary import ComplianceResult
    from ebay_claw.models.domain import ListingRecord


@runtime_checkable
class ComplianceCheckService(Protocol):
    """Interface for listing-level compliance checks (no writes; review-domain warnings only)."""

    def check_listing(self, listing: "ListingRecord") -> "ComplianceResult":
        ...
