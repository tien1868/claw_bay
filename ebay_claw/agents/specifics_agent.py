"""Item specifics — only high-confidence fills from existing listing data."""

from __future__ import annotations

from typing import Dict, List, Optional

from ebay_claw.config.settings import Settings, get_settings
from ebay_claw.models.domain import (
    ListingRecord,
    SpecificsFieldOp,
    SpecificsFieldSuggestion,
    SpecificsSuggestion,
)


class SpecificsAgent:
    def __init__(self, settings: Optional[Settings] = None):
        self._s = settings or get_settings()

    def suggest(self, listing: ListingRecord) -> SpecificsSuggestion:
        existing: Dict[str, str] = dict(listing.item_specifics)
        additions: List[SpecificsFieldSuggestion] = []
        corrections: List[SpecificsFieldSuggestion] = []
        warnings: List[str] = []

        def add_if(
            key: str,
            value: Optional[str],
            source: str,
            confidence: float,
        ) -> None:
            if not value or not str(value).strip():
                return
            v = str(value).strip()
            cur = existing.get(key)
            if cur is None:
                additions.append(
                    SpecificsFieldSuggestion(
                        name=key,
                        current_value=None,
                        proposed_value=v,
                        operation=SpecificsFieldOp.PROPOSE_ADD,
                        confidence=confidence,
                        reason_code=f"from_{source}",
                    )
                )
            elif cur.strip().lower() != v.lower() and confidence >= 0.85:
                corrections.append(
                    SpecificsFieldSuggestion(
                        name=key,
                        current_value=cur,
                        proposed_value=v,
                        operation=SpecificsFieldOp.PROPOSE_CORRECT,
                        confidence=confidence,
                        reason_code=f"align_with_{source}",
                    )
                )

        brand = listing.brand
        add_if("Brand", brand, "listing.brand", 0.9 if brand else 0.0)
        add_if("Size", listing.size or existing.get("Size"), "listing.size", 0.88)
        add_if("Color", listing.color, "listing.color", 0.82)
        add_if("Department", listing.department, "listing.department", 0.8)

        if listing.garment_type:
            add_if("Type", listing.garment_type, "listing.garment_type", 0.78)

        if listing.material and len(listing.material) < 80:
            add_if("Material", listing.material, "listing.material", 0.75)

        low = [a for a in additions if a.confidence < 0.7]
        if low:
            warnings.append("some_fields_low_confidence_skipped")

        overall = 0.7
        if additions or corrections:
            scores = [x.confidence for x in additions + corrections]
            overall = sum(scores) / len(scores)

        return SpecificsSuggestion(
            listing_id=listing.listing_id,
            existing_specifics=existing,
            proposed_additions=additions,
            proposed_corrections=corrections,
            overall_confidence=overall,
            warnings=warnings,
        )
