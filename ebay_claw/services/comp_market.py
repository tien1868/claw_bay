"""Derive MarketCompSummary from normalized sold comps (pure, read-only)."""

from __future__ import annotations

import statistics
from datetime import date
from typing import List, Optional

from ebay_claw.config.settings import Settings
from ebay_claw.models.comps import MarketCompSummary, MarketPricePosition, SoldCompRecord
from ebay_claw.models.domain import ListingRecord


def summarize_sold_comps(
    listing: ListingRecord,
    comps: List[SoldCompRecord],
    as_of: date,
    settings: Settings,
) -> MarketCompSummary:
    window = int(getattr(settings, "comps_recency_default_days", None) or 90)
    if not comps:
        return MarketCompSummary(
            recency_window_days=window,
            comps_data_source="disabled" if not settings.comps_enabled else "no_data",
        )

    cutoff = date.fromordinal(max(date(1970, 1, 1).toordinal(), as_of.toordinal() - window))
    filtered = [c for c in comps if c.sold_date >= cutoff]
    if not filtered:
        return MarketCompSummary(
            comp_count=0,
            recency_window_days=window,
            comps_data_source="fixture",
        )

    prices = [c.sold_price for c in filtered]
    median = float(statistics.median(prices))
    n = len(filtered)
    avg_mq = sum(c.match_quality for c in filtered) / n if n else 0.0
    size_factor = min(1.0, n / 4.0)
    confidence = min(1.0, 0.22 + 0.38 * size_factor + 0.4 * avg_mq)
    if n == 1:
        confidence = min(confidence, 0.55)

    ask = float(listing.price_amount)
    pct_vs: Optional[float] = None
    position = MarketPricePosition.UNKNOWN
    if median > 0:
        pct_vs = round((ask - median) / median * 100.0, 2)
        hi = 10.0 if n >= 2 else 18.0
        lo = -8.0 if n >= 2 else -12.0
        if confidence >= 0.35:
            if pct_vs > hi:
                position = MarketPricePosition.ABOVE_MARKET
            elif pct_vs < lo:
                position = MarketPricePosition.BELOW_MARKET
            else:
                position = MarketPricePosition.AT_MARKET

    return MarketCompSummary(
        median_sold_price=median,
        comp_count=n,
        recency_window_days=window,
        price_position=position,
        comp_match_confidence=round(confidence, 3),
        pct_vs_median=pct_vs,
        comps_data_source="fixture",
    )
