"""Market-aware strategy overlay (read-only comps) — transitions and restraint rules."""

from datetime import date, timedelta

from ebay_claw.analytics.inventory_analyst import InventoryAnalyst
from ebay_claw.analytics.strategy_scoring import StrategyScorer
from ebay_claw.models.comps import MarketCompSummary, MarketPricePosition
from ebay_claw.models.domain import ListingRecord, StrategicPath

_VELOCITY = frozenset({StrategicPath.REPRICE_NOW, StrategicPath.FAST_MOVE})


def _asof():
    return date(2026, 3, 1)


def test_below_market_weak_demand_demotes_velocity_to_optimize():
    """Below median + weak demand: avoid default markdown-first strategy."""
    analyst = InventoryAnalyst()
    scorer = StrategyScorer()
    asof = _asof()
    lst = ListingRecord(
        listing_id="below1",
        title="Gap Mens Straight Jeans Size 32 Blue Denim Classic Fit Everyday Wear",
        price_amount=22.0,
        listed_on=asof - timedelta(days=82),
        brand="Gap",
        size="32",
        department="Men",
        garment_type="Jeans",
        watchers=0,
        view_count=10,
        item_specifics={"Brand": "Gap", "Size": "32", "Color": "Blue", "Material": "Denim"},
    )
    market = MarketCompSummary(
        median_sold_price=38.0,
        comp_count=5,
        recency_window_days=90,
        price_position=MarketPricePosition.BELOW_MARKET,
        comp_match_confidence=0.62,
        pct_vs_median=round((22.0 - 38.0) / 38.0 * 100.0, 2),
        comps_data_source="fixture",
    )
    an = analyst.analyze(lst, as_of=asof, market_summary=market)
    sc = scorer.score(lst, an, as_of=asof, market_summary=market)
    assert sc.baseline_strategy in _VELOCITY
    assert sc.recommended_strategy == StrategicPath.OPTIMIZE_AND_HOLD
    assert sc.strategy_changed_by_market is True
    assert sc.market_adjustment_note
    assert "below" in sc.market_adjustment_note.lower() or "median" in sc.market_adjustment_note.lower()


def test_low_comp_confidence_does_not_override_strategy():
    """Thin / low-confidence comps: no strong path override."""
    analyst = InventoryAnalyst()
    scorer = StrategyScorer()
    asof = _asof()
    lst = ListingRecord(
        listing_id="thin1",
        title="Old Navy Blue Cotton T Shirt Mens M Short Sleeve Crew Neck Summer",
        price_amount=15.0,
        listed_on=asof - timedelta(days=40),
        brand="Old Navy",
        size="M",
        department="Men",
        garment_type="T-Shirt",
        watchers=0,
        item_specifics={"Brand": "Old Navy", "Size": "M"},
    )
    market = MarketCompSummary(
        median_sold_price=12.0,
        comp_count=4,
        recency_window_days=90,
        price_position=MarketPricePosition.ABOVE_MARKET,
        comp_match_confidence=0.28,
        pct_vs_median=25.0,
        comps_data_source="fixture",
    )
    an = analyst.analyze(lst, as_of=asof, market_summary=market)
    sc = scorer.score(lst, an, as_of=asof, market_summary=market)
    assert sc.recommended_strategy == sc.baseline_strategy
    assert sc.strategy_changed_by_market is False
    assert sc.market_adjustment_note
    assert "thin" in sc.market_adjustment_note.lower() or "low-confidence" in sc.market_adjustment_note.lower()


def test_above_market_premium_watchers_holds_patience_vs_velocity():
    """Above median + premium + engagement: block repricing velocity."""
    analyst = InventoryAnalyst()
    scorer = StrategyScorer()
    asof = _asof()
    lst = ListingRecord(
        listing_id="prem1",
        title="Patagonia Better Sweater Fleece Jacket Mens L Navy Full Zip Outdoor",
        price_amount=120.0,
        listed_on=asof - timedelta(days=40),
        brand="patagonia",
        size="L",
        department="Men",
        garment_type="Jacket",
        watchers=3,
        view_count=80,
        item_specifics={"Brand": "Patagonia"},
    )
    market = MarketCompSummary(
        median_sold_price=95.0,
        comp_count=4,
        recency_window_days=90,
        price_position=MarketPricePosition.ABOVE_MARKET,
        comp_match_confidence=0.58,
        pct_vs_median=round((120.0 - 95.0) / 95.0 * 100.0, 2),
        comps_data_source="fixture",
    )
    an = analyst.analyze(lst, as_of=asof, market_summary=market)
    sc = scorer.score(lst, an, as_of=asof, market_summary=market)
    assert sc.baseline_strategy in (StrategicPath.PREMIUM_PATIENCE, StrategicPath.FAST_MOVE, StrategicPath.REPRICE_NOW)
    if sc.baseline_strategy in (StrategicPath.FAST_MOVE, StrategicPath.REPRICE_NOW):
        assert sc.recommended_strategy == StrategicPath.PREMIUM_PATIENCE
        assert sc.strategy_changed_by_market is True
    else:
        assert sc.recommended_strategy == StrategicPath.PREMIUM_PATIENCE


def test_above_market_aged_weak_engagement_biases_clearance_reprice():
    """Premium patience baseline can yield to repricing when comps + age + no engagement say so."""
    analyst = InventoryAnalyst()
    scorer = StrategyScorer()
    asof = _asof()
    lst = ListingRecord(
        listing_id="aged1",
        title="Patagonia Synchilla Snap T Fleece Pullover Mens M Red Classic Warm",
        price_amount=88.0,
        listed_on=asof - timedelta(days=88),
        brand="patagonia",
        size="M",
        department="Men",
        garment_type="Fleece",
        watchers=0,
        view_count=12,
        item_specifics={"Brand": "Patagonia", "Size": "M", "Color": "Red"},
    )
    market = MarketCompSummary(
        median_sold_price=62.0,
        comp_count=5,
        recency_window_days=90,
        price_position=MarketPricePosition.ABOVE_MARKET,
        comp_match_confidence=0.6,
        pct_vs_median=round((88.0 - 62.0) / 62.0 * 100.0, 2),
        comps_data_source="fixture",
    )
    an = analyst.analyze(lst, as_of=asof, market_summary=market)
    sc = scorer.score(lst, an, as_of=asof, market_summary=market)
    assert sc.baseline_strategy == StrategicPath.PREMIUM_PATIENCE
    assert sc.recommended_strategy == StrategicPath.REPRICE_NOW
    assert sc.strategy_changed_by_market is True
    assert "above" in sc.market_adjustment_note.lower()


def test_at_market_weak_listing_demotes_velocity():
    analyst = InventoryAnalyst()
    scorer = StrategyScorer()
    asof = _asof()
    lst = ListingRecord(
        listing_id="at1",
        # Two weak-title signals but age < 45 so baseline can stay velocity-first.
        title="nice cool shirt mens",
        price_amount=28.0,
        listed_on=asof - timedelta(days=40),
        brand="Gap",
        size="M",
        department="Men",
        garment_type="Shirt",
        watchers=0,
        description="x" * 100,
        item_specifics={"Brand": "Gap", "Size": "M"},
    )
    market = MarketCompSummary(
        median_sold_price=28.0,
        comp_count=5,
        recency_window_days=90,
        price_position=MarketPricePosition.AT_MARKET,
        comp_match_confidence=0.55,
        pct_vs_median=0.0,
        comps_data_source="fixture",
    )
    an = analyst.analyze(lst, as_of=asof, market_summary=market)
    sc = scorer.score(lst, an, as_of=asof, market_summary=market)
    assert sc.optimization_needed_score >= 0.35
    assert sc.baseline_strategy in _VELOCITY
    assert sc.recommended_strategy == StrategicPath.OPTIMIZE_AND_HOLD
    assert sc.strategy_changed_by_market is True
