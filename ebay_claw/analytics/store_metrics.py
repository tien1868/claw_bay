"""Store-level 90-day intelligence."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Dict, List, Optional

from ebay_claw.analytics.inventory_analyst import InventoryAnalyst, weak_title_signals
from ebay_claw.analytics.strategy_scoring import StrategyScorer
from ebay_claw.models.domain import BrandCount, ListingRecord, StoreMetrics


class StoreMetricsCalculator:
    def __init__(self):
        self._analyst = InventoryAnalyst()
        self._scorer = StrategyScorer()

    def compute(
        self,
        listings: List[ListingRecord],
        as_of: Optional[date] = None,
    ) -> StoreMetrics:
        today = as_of or date.today()
        if not listings:
            return StoreMetrics(
                computed_at=datetime.now(),
                inventory_count=0,
                age_distribution={},
                stale_inventory_count=0,
                average_listing_age_days=0.0,
                listings_missing_critical_specifics=0,
                listings_weak_titles=0,
                pct_likely_sell_within_90_days=0.0,
                pct_at_risk_past_90_days=0.0,
                sell_through_rate=None,
                top_brands_by_count=[],
                worst_performing_buckets=[],
                intervention_needed_this_week_count=0,
            )

        age_dist: Dict[str, int] = defaultdict(int)
        stale = 0
        total_days = 0
        missing_crit = 0
        weak_titles = 0
        likely_ok = 0
        at_risk = 0
        intervention = 0
        brand_counts: Counter[str] = Counter()

        for lst in listings:
            a = self._analyst.analyze(lst, as_of=today)
            age_dist[a.age_bucket.value] += 1
            total_days += a.days_active
            if a.is_stale:
                stale += 1
            if a.missing_critical_fields:
                missing_crit += 1
            if weak_title_signals(lst.title):
                weak_titles += 1
            sc = self._scorer.score(lst, a, as_of=today)
            if sc.sale_likelihood_before_90_days >= 0.45:
                likely_ok += 1
            if a.days_active >= 75 or sc.sale_likelihood_before_90_days < 0.35:
                at_risk += 1
            if a.days_active >= 60 and (
                len(a.weak_title_signals) >= 2 or len(a.missing_critical_fields) >= 2
            ):
                intervention += 1
            b = (lst.brand or lst.item_specifics.get("Brand") or "Unknown").strip()
            brand_counts[b] += 1

        n = len(listings)
        top = [
            BrandCount(brand=k, count=v)
            for k, v in brand_counts.most_common(8)
        ]

        bucket_scores: Dict[str, float] = defaultdict(float)
        bucket_n: Dict[str, int] = defaultdict(int)
        for lst in listings:
            a = self._analyst.analyze(lst, as_of=today)
            key = f"{a.group_keys.get('brand','?')}|{a.age_bucket.value}"
            sc = self._scorer.score(lst, a, as_of=today)
            bucket_scores[key] += sc.sale_likelihood_before_90_days
            bucket_n[key] += 1
        worst: List[str] = []
        for k, tot in bucket_scores.items():
            avg = tot / max(1, bucket_n[k])
            worst.append((k, avg))
        worst.sort(key=lambda x: x[1])
        worst_buckets = [k for k, _ in worst[:5]]

        sold_units = sum((x.sold_quantity_last_90_days or 0) for x in listings)
        sell_through = None
        if sold_units > 0:
            sell_through = sold_units / max(n, 1)

        return StoreMetrics(
            computed_at=datetime.now(),
            inventory_count=n,
            age_distribution=dict(age_dist),
            stale_inventory_count=stale,
            average_listing_age_days=total_days / n,
            listings_missing_critical_specifics=missing_crit,
            listings_weak_titles=weak_titles,
            pct_likely_sell_within_90_days=100.0 * likely_ok / n,
            pct_at_risk_past_90_days=100.0 * at_risk / n,
            sell_through_rate=sell_through,
            top_brands_by_count=top,
            worst_performing_buckets=worst_buckets,
            intervention_needed_this_week_count=intervention,
        )
