"""Pricing / aging recommendations — rules + read-only sold-comps market layer."""

from __future__ import annotations

from typing import List, Optional, Tuple

from ebay_claw.analytics.inventory_analyst import weak_title_signals
from ebay_claw.analytics.strategy_scoring import _is_premium_brand
from ebay_claw.config.settings import Settings, get_settings
from ebay_claw.models.comps import MarketPricePosition
from ebay_claw.models.domain import (
    ListingAnalysis,
    ListingRecord,
    PricingAction,
    PricingRecommendation,
    StrategicPath,
)


class PricingAgent:
    def __init__(self, settings: Optional[Settings] = None):
        self._s = settings or get_settings()

    def recommend(
        self,
        listing: ListingRecord,
        analysis: ListingAnalysis,
        strategy: StrategicPath,
    ) -> PricingRecommendation:
        days = analysis.days_active
        brand = listing.brand or listing.item_specifics.get("Brand")
        premium = _is_premium_brand(brand, self._s)
        watchers = listing.watchers or 0
        weak_t = weak_title_signals(listing.title)
        missing = analysis.missing_critical_fields

        factors: List[str] = [
            f"days_active={days}",
            f"strategy={strategy.value}",
            f"watchers={watchers}",
            f"premium_brand={premium}",
        ]
        mk = analysis.market
        if mk and mk.comp_count > 0:
            factors.append(
                f"market_pos={mk.price_position.value},n={mk.comp_count},"
                f"match_conf={round(mk.comp_match_confidence, 3)},"
                f"median={mk.median_sold_price}"
            )

        action = PricingAction.HOLD
        conf = 0.55
        explain = "Default hold — no urgent signal."
        exp_90 = "Neutral impact on 90-day trajectory."
        profit_note = "No forced discount."

        if strategy == StrategicPath.END_AND_SELL_SIMILAR:
            action = PricingAction.END_AND_SELL_SIMILAR
            conf = 0.78
            explain = "Stale performance — end/relist or sell-similar for freshness."
            exp_90 = "Strong positive if relisted with improvements."
            profit_note = "May sacrifice short-term rank for long-term velocity."

        elif strategy == StrategicPath.REPRICE_NOW:
            pct = self._markdown_pct(days, premium, watchers)
            action = (
                PricingAction.MARKDOWN_30
                if pct >= 30
                else PricingAction.MARKDOWN_20
                if pct >= 20
                else PricingAction.MARKDOWN_10
            )
            conf = 0.72
            explain = "Behind 90-day pace with limited listing upside — markdown to recover momentum."
            exp_90 = "Improves sell-through probability materially."
            profit_note = "Trade margin for time; revisit if premium signals appear."

        elif strategy == StrategicPath.OPTIMIZE_AND_HOLD or (
            len(weak_t) >= 2 or len(missing) >= 2
        ):
            if len(weak_t) >= 1:
                action = PricingAction.IMPROVE_TITLE
                conf = 0.68
                explain = "Title/specifics gaps likely suppress demand — improve before deeper cuts."
                exp_90 = "Moderate positive once fixed."
                profit_note = "Preserves margin vs blind discount."
            else:
                action = PricingAction.FILL_SPECIFICS
                conf = 0.65
                explain = "Fill missing specifics to improve discovery."
                exp_90 = "Moderate positive on conversion."
                profit_note = "Low risk to margin."

        elif strategy == StrategicPath.PREMIUM_PATIENCE:
            action = PricingAction.HOLD if watchers >= 2 else PricingAction.REVIEW
            conf = 0.6
            explain = "Premium profile — avoid early discount; monitor."
            exp_90 = "Preserves margin; small 90-day risk if demand fades."
            profit_note = "Margin protection prioritized."

        elif strategy == StrategicPath.FAST_MOVE:
            action = PricingAction.MARKDOWN_10
            conf = 0.7
            explain = "Velocity-first SKU — light markdown nudges 90-day sell-through."
            exp_90 = "Small but meaningful improvement."
            profit_note = "Controlled margin erosion."

        elif strategy == StrategicPath.REPACKAGE:
            action = PricingAction.BUNDLE_CANDIDATE
            conf = 0.62
            explain = "Better as lot/bundle or alternate presentation."
            exp_90 = "Variable — depends on bundle fit."
            profit_note = "May improve net if paired well."

        elif strategy == StrategicPath.AGING_RISK:
            action = PricingAction.MARKDOWN_20
            conf = 0.74
            explain = "Aging risk — stronger markdown or exit plan."
            exp_90 = "High impact on moving stock."
            profit_note = "Balance against sunk time cost."

        if watchers >= 3 and days < 90 and action in (
            PricingAction.MARKDOWN_20,
            PricingAction.MARKDOWN_30,
        ):
            action = PricingAction.SEND_OFFER
            explain += " Watchers present — test offers before deeper public markdown."
            factors.append("converted_to_offer_due_to_watchers")
            exp_90 = "May convert without public price drop."
            profit_note = "Protects shelf price while testing demand."

        action, conf, explain, exp_90, profit_note, factors, segment = self._apply_market_layer(
            listing,
            analysis,
            strategy,
            premium,
            action,
            conf,
            explain,
            exp_90,
            profit_note,
            factors,
        )

        return PricingRecommendation(
            listing_id=listing.listing_id,
            recommended_action=action,
            confidence=conf,
            explanation=explain,
            factors_used=factors,
            expected_effect_on_90_day_sell_through=exp_90,
            profit_protection_note=profit_note,
            pricing_segment=segment,
        )

    def _apply_market_layer(
        self,
        listing: ListingRecord,
        analysis: ListingAnalysis,
        strategy: StrategicPath,
        premium: bool,
        action: PricingAction,
        conf: float,
        explain: str,
        exp_90: str,
        profit_note: str,
        factors: List[str],
    ) -> Tuple[PricingAction, float, str, str, str, List[str], Optional[str]]:
        m = analysis.market
        if not m or m.comp_count == 0:
            factors.append("comps:none_or_empty")
            return action, conf, explain, exp_90, profit_note, factors, None

        med = m.median_sold_price
        factors.append(
            f"comps:n={m.comp_count},median={med},pct_vs_median={m.pct_vs_median},"
            f"pos={m.price_position.value},match_conf={m.comp_match_confidence}"
        )

        thin = m.comp_match_confidence < 0.35 or m.comp_count < 2
        if thin:
            factors.append("comps_thin_sample")
            seg = "low_comp_confidence"
            exp_90 = (
                exp_90 + " Sold comp coverage is thin — market position is directional, not definitive."
            )
            return action, conf, explain, exp_90, profit_note, factors, seg

        strong = m.comp_match_confidence >= 0.45 and m.comp_count >= 2
        segment: Optional[str] = None
        days = analysis.days_active
        weak_listing = len(analysis.weak_title_signals) >= 2 or len(analysis.missing_critical_fields) >= 2
        watchers = listing.watchers or 0

        if m.price_position == MarketPricePosition.BELOW_MARKET and strong:
            segment = "underpriced_vs_comps"
            if action in (
                PricingAction.MARKDOWN_10,
                PricingAction.MARKDOWN_20,
                PricingAction.MARKDOWN_30,
            ):
                action = PricingAction.REVIEW
                conf = max(0.55, conf - 0.08)
                explain = (
                    f"Ask is below recent sold median (~${med:.0f}); "
                    "pause automatic markdowns — you may already be buyer-friendly vs comps."
                )
                exp_90 = (
                    "Skipping discount preserves margin; 90-day velocity may be limited by discovery, "
                    "not price vs market."
                )

        if m.price_position == MarketPricePosition.AT_MARKET and strong and weak_listing:
            segment = segment or "fairly_priced_weak_listing"
            if strategy != StrategicPath.END_AND_SELL_SIMILAR and action in (
                PricingAction.MARKDOWN_10,
                PricingAction.MARKDOWN_20,
            ):
                action = PricingAction.IMPROVE_TITLE
                conf = max(conf, 0.68)
                explain = (
                    "Sold comps support your ask — weak title/specifics likely cap demand. "
                    "Fix discovery before cutting price."
                )
                exp_90 = (
                    "Improving structured data/title often raises conversion without margin loss "
                    "when pricing matches the market."
                )
                profit_note = "Comps say price is fair; protect margin while improving listing quality."

        if m.price_position == MarketPricePosition.ABOVE_MARKET and strong:
            segment = segment or "overpriced_vs_comps"
            if premium and watchers >= 2 and days >= 45:
                segment = "premium_hold_despite_age"
                action = PricingAction.HOLD
                conf = max(conf, 0.64)
                explain = (
                    f"Above ~${med:.0f} sold median but strong watcher interest — "
                    "market may tolerate premium; hold before matching comps."
                )
                exp_90 = (
                    "Demand signal suggests inelastic buyers; markdown could forfeit margin without "
                    "large 90-day sell-through gain."
                )
                profit_note = "Premium + engagement outweighs age-vs-comps pressure for now."
            elif not weak_listing and days >= 45 and action == PricingAction.HOLD:
                action = PricingAction.MARKDOWN_10
                conf = max(conf, 0.66)
                explain = (
                    f"Ask is materially above recent sold median (~${med:.0f}); "
                    "a modest cut aligns with cleared market pricing."
                )
                exp_90 = (
                    "Bringing ask closer to sold comps typically improves 90-day clearance when "
                    "listing quality is already decent."
                )
            elif weak_listing:
                segment = "overpriced_vs_comps_weak_discovery"
                if action in (
                    PricingAction.MARKDOWN_10,
                    PricingAction.MARKDOWN_20,
                    PricingAction.MARKDOWN_30,
                ):
                    action = PricingAction.IMPROVE_TITLE
                    conf = max(conf, 0.67)
                    explain = (
                        f"Above sold median (~${med:.0f}) but listing quality is weak — "
                        "improve discovery first; price may be less of the bottleneck than visibility."
                    )
                    exp_90 = (
                        "Combined comp+quality signal: test title/specifics fixes before deep discounts."
                    )
                else:
                    explain = (
                        explain + f" Market check: ask sits above ~${med:.0f} median for matched solds."
                    )
                    exp_90 = exp_90 + " Comps reinforce pricing pressure if demand stays soft."
            else:
                explain = (
                    explain + f" Sold comps cluster near ~${med:.0f}; your ask is elevated vs that band."
                )
                exp_90 = exp_90 + " Markdown impact on 90-day sell-through is reinforced by comp data."

        return action, conf, explain, exp_90, profit_note, factors, segment

    def _markdown_pct(self, days: int, premium: bool, watchers: int) -> int:
        base = 10
        if days >= 100:
            base = 30
        elif days >= 85:
            base = 20
        elif days >= 75:
            base = 20
        if premium and watchers >= 2:
            base = max(10, base - 10)
        return min(base, self._s.max_auto_markdown_pct)
