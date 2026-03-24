"""Relist candidate identification and proposal payloads — proposal-only (queue), no execution."""

from __future__ import annotations

from typing import List, Optional

from ebay_claw.agents.title_agent import TitleAgent
from ebay_claw.analytics.inventory_analyst import InventoryAnalyst
from ebay_claw.analytics.strategy_scoring import StrategyScorer
from ebay_claw.config.settings import Settings, get_settings
from ebay_claw.models.comps import MarketPricePosition
from ebay_claw.models.domain import ListingRecord, StrategicPath
from ebay_claw.models.recovery import RelistProposal


def is_relist_candidate(
    listing: ListingRecord,
    analysis,
    score,
) -> bool:
    """Heuristic: long-aged, poor 90d trajectory, or strategy already favors refresh."""
    d = analysis.days_active
    if score.recommended_strategy in (
        StrategicPath.END_AND_SELL_SIMILAR,
        StrategicPath.REPACKAGE,
    ):
        return True
    if d >= 100 and (listing.watchers or 0) <= 1:
        return True
    if d >= 85 and score.sale_likelihood_before_90_days < 0.32:
        return True
    return False


def build_relist_proposal(
    listing: ListingRecord,
    analysis,
    score,
    *,
    settings: Optional[Settings] = None,
) -> RelistProposal:
    s = settings or get_settings()
    ta = TitleAgent()
    ts = ta.suggest(listing)
    med = None
    if analysis.market and analysis.market.median_sold_price:
        med = analysis.market.median_sold_price
    pos = analysis.market.price_position if analysis.market else None
    conf = analysis.market.comp_match_confidence if analysis.market else 0.0

    target = None
    lo = None
    hi = None
    if med and med > 0 and conf >= 0.35:
        target = round(med * 1.03, 2)
        lo = round(med * 0.92, 2)
        hi = round(med * 1.12, 2)
    elif med and med > 0:
        lo = round(med * 0.85, 2)
        hi = round(med * 1.15, 2)

    why = (
        f"Age {analysis.days_active}d with limited 90-day sell likelihood "
        f"({score.sale_likelihood_before_90_days:.2f}); a fresh listing can reset discovery and buyer trust."
    )
    if pos == MarketPricePosition.ABOVE_MARKET:
        why += " Ask sits above recent sold medians — relist lets you reposition price and photos together."
    elif pos == MarketPricePosition.BELOW_MARKET:
        why += " Ask is already buyer-friendly vs comps — relist mainly refreshes rank and trust signals."

    vs_alt = (
        "Markdown alone often keeps stale rank history; holding extends time-on-market. "
        "Bundling works for low-ASP lots but this SKU may deserve a clean single-SKU relist to recover margin. "
        "Relist is preferred when title/specifics fixes are not enough and engagement stayed flat."
    )

    summary = (
        f"{listing.title[:80]} · ${listing.price_amount:.0f} · "
        f"{listing.brand or listing.item_specifics.get('Brand', 'unknown brand')}"
    )

    return RelistProposal(
        listing_id=listing.listing_id,
        listing_title=listing.title,
        current_summary=summary,
        why_relist_recommended=why,
        suggested_refreshed_title=ts.suggested_title,
        suggested_target_price=target,
        suggested_price_range_low=lo,
        suggested_price_range_high=hi,
        why_relist_vs_markdown_hold_bundle=vs_alt,
        confidence=min(0.85, max(0.45, ts.confidence)),
        strategy_context=score.recommended_strategy,
    )


def find_relist_candidates(
    listings: List[ListingRecord],
    enriched_analyses: dict,
    scores: dict,
) -> List[RelistProposal]:
    """enriched_analyses and scores keyed by listing_id."""
    out: List[RelistProposal] = []
    for lst in listings:
        a = enriched_analyses.get(lst.listing_id)
        sc = scores.get(lst.listing_id)
        if not a or not sc:
            continue
        if is_relist_candidate(lst, a, sc):
            out.append(build_relist_proposal(lst, a, sc))
    return out
