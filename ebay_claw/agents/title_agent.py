"""Title optimization — deterministic MVP; LLM can replace `suggest` later."""

from __future__ import annotations

import re
from typing import List, Optional

from ebay_claw.models.domain import ListingRecord, TitleSuggestion


EBAY_TITLE_MAX = 80


def _truncate(s: str, max_len: int = EBAY_TITLE_MAX) -> str:
    s = s.strip()
    if len(s) <= max_len:
        return s
    return s[:max_len].rstrip()


def _clean_whitespace(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip()


def build_deterministic_title(listing: ListingRecord) -> str:
    brand = listing.brand or listing.item_specifics.get("Brand") or ""
    g = listing.garment_type or ""
    dept = listing.department or ""
    size = listing.size or listing.item_specifics.get("Size") or ""
    color = listing.color or listing.item_specifics.get("Color") or ""
    cond = listing.condition or ""

    parts: List[str] = []
    if brand:
        parts.append(brand.strip())
    if g:
        parts.append(g.strip())
    if dept and dept.lower() not in " ".join(parts).lower():
        parts.append(dept.strip())
    if color:
        parts.append(color.strip())
    if size:
        parts.append(f"Size {size.strip()}")
    if cond and any(
        x in cond.lower()
        for x in ("flaw", "hole", "damage", "stain", "tear", "wear")
    ):
        parts.append("Flaws disclosed")

    base = _clean_whitespace(" ".join(parts))
    if not base:
        base = _clean_whitespace(listing.title)[:EBAY_TITLE_MAX]

    out = _truncate(base, EBAY_TITLE_MAX)
    return out


class TitleAgent:
    def suggest(self, listing: ListingRecord, use_llm: bool = False) -> TitleSuggestion:
        _ = use_llm
        original = listing.title
        suggested = build_deterministic_title(listing)
        if suggested.lower() == original.strip().lower():
            suggested = _truncate(
                _clean_whitespace(
                    f"{original.strip()} {listing.garment_type or ''} {listing.brand or ''}"
                ),
                EBAY_TITLE_MAX,
            )

        warnings: List[str] = []
        if not listing.brand and not listing.item_specifics.get("Brand"):
            warnings.append("brand_unknown_title_may_underperform")
        if not (listing.size or listing.item_specifics.get("Size")):
            warnings.append("size_not_confirmed")
        if len((listing.description or "")) < 40:
            warnings.append("thin_description_corroborate_title")

        conf = 0.75
        if warnings:
            conf = 0.55

        rationale = (
            "Deterministic rebuild from known brand, garment type, department, size, color; "
            "preserves condition-disclosure cues when present in condition field."
        )

        return TitleSuggestion(
            listing_id=listing.listing_id,
            original_title=original,
            suggested_title=_truncate(suggested, EBAY_TITLE_MAX),
            rationale=rationale,
            confidence=conf,
            warnings=warnings,
            deterministic=True,
        )
