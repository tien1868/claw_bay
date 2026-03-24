"""90-day strategy path scoring — deterministic rules + read-only sold-comp overlay."""

from __future__ import annotations

from datetime import date
from typing import Optional, Tuple

from ebay_claw.analytics.inventory_analyst import weak_title_signals
from ebay_claw.config.settings import Settings, get_settings
from ebay_claw.models.comps import MarketCompSummary, MarketPricePosition
from ebay_claw.models.domain import (
    ListingAnalysis,
    ListingRecord,
    ListingStrategyScore,
    StrategicPath,
)


def _is_premium_brand(brand: Optional[str], settings: Settings) -> bool:
    if not brand:
        return False
    return brand.strip().lower() in settings.premium_brand_set


_VELOCITY_STRATEGIES: frozenset[StrategicPath] = frozenset(
    {StrategicPath.REPRICE_NOW, StrategicPath.FAST_MOVE}
)


def _market_sample_too_thin_for_override(m: Optional[MarketCompSummary]) -> bool:
    if not m or m.comp_count == 0:
        return True
    return m.comp_count < 2 or m.comp_match_confidence < 0.35


def _market_sample_reliable(m: Optional[MarketCompSummary]) -> bool:
    """Aligns with PricingAgent 'strong' comps — overrides allowed."""
    return bool(
        m
        and m.comp_count >= 2
        and m.comp_match_confidence >= 0.45
        and m.price_position != MarketPricePosition.UNKNOWN
    )


class StrategyScorer:
    def __init__(self, settings: Optional[Settings] = None):
        self._s = settings or get_settings()

    def score(
        self,
        listing: ListingRecord,
        analysis: ListingAnalysis,
        as_of: Optional[date] = None,
        *,
        market_summary: Optional[MarketCompSummary] = None,
    ) -> ListingStrategyScore:
        """
        Scores 90-day strategic path. Sold-market context may be supplied explicitly;
        otherwise ``analysis.market`` is used (typically from enriched / comps pipeline).
        """
        _ = as_of or date.today()
        days = analysis.days_active
        brand = listing.brand or listing.item_specifics.get("Brand")
        premium = _is_premium_brand(brand, self._s)
        watchers = listing.watchers or 0
        views = listing.view_count or 0

        stale_risk = self._stale_risk(days, analysis, watchers)
        opt_need = min(
            1.0,
            0.25 * len(analysis.weak_title_signals)
            + 0.12 * len(analysis.missing_critical_fields)
            + (0.2 if analysis.weak_description else 0),
        )
        profit_prot = self._profit_protection(premium, watchers, listing.price_amount, days)

        sale_like = self._sale_likelihood(
            days, stale_risk, opt_need, premium, watchers, views
        )

        baseline_path, baseline_rationale = self._pick_path_baseline(
            listing, analysis, premium, stale_risk, opt_need, sale_like
        )

        m = market_summary if market_summary is not None else analysis.market
        final_path, final_rationale, changed, adj_note, snap = self._apply_market_strategy_layer(
            listing=listing,
            analysis=analysis,
            market=m,
            premium=premium,
            sale_like=sale_like,
            opt_need=opt_need,
            baseline_path=baseline_path,
            baseline_rationale=baseline_rationale,
        )

        return ListingStrategyScore(
            listing_id=listing.listing_id,
            days_active=days,
            age_bucket=analysis.age_bucket,
            stale_risk_score=stale_risk,
            profit_protection_score=profit_prot,
            optimization_needed_score=opt_need,
            sale_likelihood_before_90_days=sale_like,
            recommended_strategy=final_path,
            rationale=final_rationale,
            baseline_strategy=baseline_path,
            strategy_changed_by_market=changed,
            market_adjustment_note=adj_note,
            comp_count=snap[0],
            comp_match_confidence=snap[1],
            median_sold_price=snap[2],
            price_position_vs_market=snap[3],
            comps_recency_window_days=snap[4],
        )

    def _apply_market_strategy_layer(
        self,
        *,
        listing: ListingRecord,
        analysis: ListingAnalysis,
        market: Optional[MarketCompSummary],
        premium: bool,
        sale_like: float,
        opt_need: float,
        baseline_path: StrategicPath,
        baseline_rationale: str,
    ) -> Tuple[
        StrategicPath,
        str,
        bool,
        Optional[str],
        tuple[int, float, Optional[float], str, int],
    ]:
        """Returns final path, rationale, changed flag, optional operator note, market snapshot tuple."""
        m = market
        snap = (
            m.comp_count if m else 0,
            m.comp_match_confidence if m else 0.0,
            m.median_sold_price if m else None,
            m.price_position.value if m else "unknown",
            m.recency_window_days if m else 0,
        )

        if not m or m.comp_count == 0:
            return baseline_path, baseline_rationale, False, None, snap

        if _market_sample_too_thin_for_override(m):
            note = (
                "Sold comps are thin or low-confidence — strategy follows listing/age signals only "
                "(no market override)."
            )
            return baseline_path, f"{baseline_rationale} ({note})", False, note, snap

        if not _market_sample_reliable(m):
            note = (
                "Comp match confidence is moderate — avoiding strong market-based strategy overrides "
                "(listing-first path kept)."
            )
            return baseline_path, f"{baseline_rationale} ({note})", False, note, snap

        days = analysis.days_active
        watchers = listing.watchers or 0
        position = m.price_position
        weak_listing = (
            opt_need >= 0.35
            or len(analysis.weak_title_signals) >= 2
            or len(analysis.missing_critical_fields) >= 2
        )
        weak_demand = sale_like < 0.42 and watchers <= 1

        path = baseline_path
        notes: list[str] = []

        # Above median + premium + engagement → patience (do not force velocity vs comps).
        if (
            position == MarketPricePosition.ABOVE_MARKET
            and premium
            and watchers >= 2
            and baseline_path in _VELOCITY_STRATEGIES
        ):
            path = StrategicPath.PREMIUM_PATIENCE
            notes.append(
                "Sold comps show the ask above the median, but premium positioning + watcher interest "
                "supports patience before matching cleared prices."
            )

        # Above median + age + weak engagement → realignment (clearance vs premium hold).
        terminal = frozenset(
            {
                StrategicPath.END_AND_SELL_SIMILAR,
                StrategicPath.REPACKAGE,
            }
        )
        if (
            path == baseline_path
            and position == MarketPricePosition.ABOVE_MARKET
            and days >= 75
            and watchers <= 1
            and baseline_path not in terminal
        ):
            if baseline_path in (
                StrategicPath.PREMIUM_PATIENCE,
                StrategicPath.OPTIMIZE_AND_HOLD,
                StrategicPath.FAST_MOVE,
                StrategicPath.AGING_RISK,
            ):
                path = StrategicPath.REPRICE_NOW
                notes.append(
                    "Ask is above recent sold prices with weak engagement and meaningful age — "
                    "bias toward repricing vs holding the premium."
                )

        # Below median + weak demand → discovery/quality, not default markdown velocity.
        if (
            path == baseline_path
            and position == MarketPricePosition.BELOW_MARKET
            and weak_demand
            and baseline_path in _VELOCITY_STRATEGIES
        ):
            path = StrategicPath.OPTIMIZE_AND_HOLD
            notes.append(
                "Ask is already below the sold median; weak traction is unlikely fixed by deeper "
                "markdowns alone — optimize discovery first."
            )

        # At median + weak listing → optimize before price cuts.
        if (
            path == baseline_path
            and position == MarketPricePosition.AT_MARKET
            and weak_listing
            and baseline_path in _VELOCITY_STRATEGIES
        ):
            path = StrategicPath.OPTIMIZE_AND_HOLD
            notes.append(
                "Pricing lines up with sold medians; listing quality gaps should be addressed before "
                "price-driven velocity."
            )

        if not notes:
            return baseline_path, baseline_rationale, False, None, snap

        note_text = " ".join(notes)
        rationale = f"{baseline_rationale} **Market-adjusted:** {note_text}"
        return path, rationale, True, note_text, snap

    def _stale_risk(
        self, days: int, analysis: ListingAnalysis, watchers: int
    ) -> float:
        base = self._s.stale_risk_base
        age_factor = min(1.0, days / 120.0)
        w_factor = 0.0 if watchers >= 2 else 0.15
        dead_boost = 0.25 if analysis.dead_stock_likely else 0.0
        return min(1.0, base + age_factor * 0.55 + w_factor + dead_boost)

    def _profit_protection(
        self, premium: bool, watchers: int, price: float, days: int
    ) -> float:
        score = 0.35
        if premium:
            score += 0.35
        if watchers >= 3:
            score += 0.2
        if price >= self._s.high_value_price_usd:
            score += 0.1
        if days > 100:
            score -= 0.15
        return max(0.0, min(1.0, score))

    def _sale_likelihood(
        self,
        days: int,
        stale_risk: float,
        opt_need: float,
        premium: bool,
        watchers: int,
        views: int,
    ) -> float:
        remaining = max(0, 90 - days)
        time_factor = min(1.0, remaining / 90.0)
        demand = min(1.0, 0.1 * watchers + 0.002 * views)
        base = time_factor * (1.0 - 0.5 * stale_risk) * (1.0 - 0.35 * opt_need) + 0.15 * demand
        if premium:
            base += 0.08
        return max(0.0, min(1.0, base))

    def _pick_path_baseline(
        self,
        listing: ListingRecord,
        analysis: ListingAnalysis,
        premium: bool,
        stale_risk: float,
        opt_need: float,
        sale_like: float,
    ) -> tuple[StrategicPath, str]:
        days = analysis.days_active
        watchers = listing.watchers or 0
        weak_titles = weak_title_signals(listing.title)

        if premium and watchers >= 2 and days < 100:
            return (
                StrategicPath.PREMIUM_PATIENCE,
                "Premium brand with engagement; protect margin unless age forces action.",
            )

        if days >= 95 and listing.price_amount < 35 and watchers == 0 and not premium:
            return (
                StrategicPath.REPACKAGE,
                "Low ticket + stale — bundle/lot may beat single-unit discount.",
            )

        if days >= 120 and stale_risk > 0.65 and watchers <= 1:
            return (
                StrategicPath.END_AND_SELL_SIMILAR,
                "Long age, low engagement — freshness reset likely highest leverage.",
            )

        if days >= 75 and sale_like < 0.35 and opt_need < 0.35:
            return (
                StrategicPath.REPRICE_NOW,
                "Past key aging window with limited optimization upside — price action.",
            )

        if days >= 60 and opt_need >= 0.45:
            return (
                StrategicPath.OPTIMIZE_AND_HOLD,
                "Weak listing quality — improve title/specifics before aggressive discount.",
            )

        if days >= 90 and analysis.dead_stock_likely:
            return (
                StrategicPath.AGING_RISK,
                "Dead-stock risk — needs intervention this cycle.",
            )

        if days >= 45 and opt_need >= 0.5:
            return (
                StrategicPath.OPTIMIZE_AND_HOLD,
                "Multiple quality gaps — optimize before repricing.",
            )

        if premium and days < 90:
            return (
                StrategicPath.PREMIUM_PATIENCE,
                "Premium profile — avoid race-to-bottom; monitor weekly.",
            )

        if days >= 30 and sale_like < 0.45 and not premium:
            return (
                StrategicPath.FAST_MOVE,
                "Non-premium, slipping 90-day window — bias to velocity.",
            )

        if len(weak_titles) >= 2 and days >= 45:
            return (
                StrategicPath.OPTIMIZE_AND_HOLD,
                "Title/specifics likely constraining demand.",
            )

        return (
            StrategicPath.FAST_MOVE,
            "Default path: favor turnover within 90-day objective.",
        )
