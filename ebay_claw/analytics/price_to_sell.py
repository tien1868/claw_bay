"""Price-to-sell guidance from comps — avoids false precision when match confidence is low."""

from __future__ import annotations

from typing import Optional

from ebay_claw.models.comps import MarketPricePosition
from ebay_claw.models.domain import ListingAnalysis, ListingRecord
from ebay_claw.models.recovery import ConfidenceBand, PriceToSellRecommendation


def compute_price_to_sell(
    listing: ListingRecord,
    analysis: ListingAnalysis,
) -> PriceToSellRecommendation:
    m = analysis.market
    ask = float(listing.price_amount)
    if not m or m.comp_count == 0:
        return PriceToSellRecommendation(
            listing_id=listing.listing_id,
            explanation=(
                "No sold comps attached — use manual comps or wait for comp coverage before repricing."
            ),
            caution_note="Insufficient comp data for numeric target.",
            confidence_band=ConfidenceBand.LOW,
            is_directional_only=True,
        )

    med = m.median_sold_price
    conf = m.comp_match_confidence
    n = m.comp_count
    low_conf = conf < 0.35 or n < 2

    caution = None
    directional = False
    band = ConfidenceBand.MEDIUM
    if low_conf:
        band = ConfidenceBand.LOW
        directional = True
        caution = (
            "Comp sample is thin or low-confidence — treat prices as directional, not exact targets."
        )
    elif conf < 0.45:
        band = ConfidenceBand.MEDIUM
        caution = "Comp match is moderate — prefer small steps and re-check after listing fixes."

    if med is None or med <= 0:
        return PriceToSellRecommendation(
            listing_id=listing.listing_id,
            median_sold_price=med,
            comp_count=n,
            comp_match_confidence=conf,
            explanation="Median sold price unavailable — cannot derive target.",
            caution_note=caution,
            confidence_band=band,
            is_directional_only=True,
        )

    spread = 0.12 if not low_conf else 0.22
    target = med
    if m.price_position == MarketPricePosition.ABOVE_MARKET and not low_conf:
        target = round(med * 1.02, 2)
    elif m.price_position == MarketPricePosition.BELOW_MARKET and not low_conf:
        target = round(min(ask, med * 0.98), 2)

    lo = round(target * (1.0 - spread), 2)
    hi = round(target * (1.0 + spread), 2)

    expl_parts = [
        f"Sold median ~${med:.0f} from {n} comp(s), match confidence {conf:.2f}.",
        f"Ask position vs comps: {m.price_position.value}.",
    ]
    if analysis.is_stale or (analysis.price_outlier_note and "above" in (analysis.price_outlier_note or "")):
        expl_parts.append("Stale or overpriced signal — aligning toward cleared market pricing can lift 90-day sell-through.")

    return PriceToSellRecommendation(
        listing_id=listing.listing_id,
        target_price=target if not directional else None,
        recommended_range_low=lo,
        recommended_range_high=hi,
        median_sold_price=med,
        comp_count=n,
        comp_match_confidence=conf,
        confidence_band=band,
        explanation=" ".join(expl_parts),
        caution_note=caution,
        is_directional_only=directional,
    )
