from datetime import date
from typing import Optional

from ebay_claw.agents.pricing_agent import PricingAgent
from ebay_claw.config.settings import Settings
from ebay_claw.models.comps import MarketCompSummary, MarketPricePosition
from ebay_claw.models.domain import (
    AgeBucket,
    ListingAnalysis,
    ListingRecord,
    PricingAction,
    StrategicPath,
)


def _base_analysis(listing_id: str, market: Optional[MarketCompSummary], **kwargs) -> ListingAnalysis:
    return ListingAnalysis(
        listing_id=listing_id,
        days_active=kwargs.get("days_active", 70),
        age_bucket=kwargs.get("age_bucket", AgeBucket.D60_74),
        is_stale=True,
        stale_reasons=[],
        missing_critical_fields=kwargs.get("missing_critical_fields", []),
        weak_title_signals=kwargs.get("weak_title_signals", []),
        weak_description=False,
        on_track_90_day_sale=False,
        highest_leverage_action="test",
        group_keys={},
        market=market,
    )


def test_underpriced_strong_comps_suppresses_markdown():
    s = Settings()
    m = MarketCompSummary(
        median_sold_price=30.0,
        comp_count=3,
        comp_match_confidence=0.82,
        price_position=MarketPricePosition.BELOW_MARKET,
        pct_vs_median=-15.0,
    )
    lst = ListingRecord(
        listing_id="u1",
        title="some tee medium blue cotton long enough title",
        price_amount=25.5,
        listed_on=date(2024, 1, 1),
        garment_type="T-Shirt",
    )
    an = _base_analysis("u1", m, weak_title_signals=[], missing_critical_fields=[])
    pr = PricingAgent(settings=s).recommend(lst, an, StrategicPath.FAST_MOVE)
    assert pr.recommended_action == PricingAction.REVIEW
    assert pr.pricing_segment == "underpriced_vs_comps"


def test_fair_price_weak_listing_prefers_title_over_light_markdown():
    s = Settings()
    m = MarketCompSummary(
        median_sold_price=40.0,
        comp_count=3,
        comp_match_confidence=0.8,
        price_position=MarketPricePosition.AT_MARKET,
        pct_vs_median=2.0,
    )
    lst = ListingRecord(
        listing_id="f1",
        title="shirt",
        price_amount=41.0,
        listed_on=date(2024, 1, 1),
        garment_type="T-Shirt",
        brand="Gap",
    )
    an = _base_analysis(
        "f1",
        m,
        weak_title_signals=["title_too_short", "low_keyword_density"],
        missing_critical_fields=["brand", "size"],
    )
    pr = PricingAgent(settings=s).recommend(lst, an, StrategicPath.FAST_MOVE)
    assert pr.recommended_action == PricingAction.IMPROVE_TITLE
    assert pr.pricing_segment == "fairly_priced_weak_listing"


def test_low_comp_confidence_segment():
    s = Settings()
    m = MarketCompSummary(
        median_sold_price=20.0,
        comp_count=1,
        comp_match_confidence=0.5,
        price_position=MarketPricePosition.UNKNOWN,
    )
    lst = ListingRecord(
        listing_id="l1",
        title="x",
        price_amount=25.0,
        listed_on=date(2025, 1, 1),
    )
    an = _base_analysis("l1", m)
    pr = PricingAgent(settings=s).recommend(lst, an, StrategicPath.REPRICE_NOW)
    assert pr.pricing_segment == "low_comp_confidence"


def test_premium_hold_despite_age_when_above_median_but_hot():
    s = Settings()
    m = MarketCompSummary(
        median_sold_price=350.0,
        comp_count=2,
        comp_match_confidence=0.86,
        price_position=MarketPricePosition.ABOVE_MARKET,
        pct_vs_median=8.5,
    )
    lst = ListingRecord(
        listing_id="p1",
        title="designer pants excellent condition size 48",
        price_amount=380.0,
        listed_on=date(2024, 6, 1),
        brand="Rick Owens",
        garment_type="Pants",
        size="48",
        watchers=4,
    )
    an = _base_analysis(
        "p1",
        m,
        days_active=120,
        age_bucket=AgeBucket.D90_119,
        weak_title_signals=[],
        missing_critical_fields=[],
    )
    pr = PricingAgent(settings=s).recommend(lst, an, StrategicPath.PREMIUM_PATIENCE)
    assert pr.recommended_action == PricingAction.HOLD
    assert pr.pricing_segment == "premium_hold_despite_age"


def test_end_and_sell_similar_unaffected_without_strong_comps(monkeypatch):
    """Regression: strategy-specific path when comps absent."""
    s = Settings(comps_enabled=False)
    lst = ListingRecord(
        listing_id="e",
        title="old thing",
        price_amount=10,
        listed_on=date(2023, 1, 1),
        watchers=0,
    )
    an = _base_analysis("e", None)
    from ebay_claw.analytics.inventory_analyst import InventoryAnalyst

    an_full = InventoryAnalyst(settings=s).analyze(lst, as_of=date(2025, 6, 1))
    pr = PricingAgent(settings=s).recommend(lst, an_full, StrategicPath.END_AND_SELL_SIMILAR)
    assert pr.recommended_action == PricingAction.END_AND_SELL_SIMILAR
