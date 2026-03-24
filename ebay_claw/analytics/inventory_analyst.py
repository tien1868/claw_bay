"""Listing age, staleness, missing fields, and leverage actions."""

from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional, Tuple

from ebay_claw.config.settings import Settings, get_settings
from ebay_claw.models.comps import MarketCompSummary, MarketPricePosition
from ebay_claw.models.domain import AgeBucket, ListingAnalysis, ListingRecord


def compute_days_active(listing: ListingRecord, as_of: date) -> int:
    start: Optional[date] = None
    if listing.listed_on:
        start = listing.listed_on
    elif listing.listed_at:
        start = listing.listed_at.date()
    if start is None:
        return 0
    return max(0, (as_of - start).days)


def age_to_bucket(days: int, settings: Settings) -> AgeBucket:
    if days < settings.age_threshold_days_30:
        return AgeBucket.D0_29
    if days < settings.age_threshold_days_60:
        return AgeBucket.D30_59
    if days < settings.age_threshold_days_75:
        return AgeBucket.D60_74
    if days < settings.age_threshold_days_90:
        return AgeBucket.D75_89
    if days < settings.age_threshold_days_120:
        return AgeBucket.D90_119
    if days < settings.age_threshold_days_180:
        return AgeBucket.D120_179
    return AgeBucket.D180_PLUS


def weak_title_signals(title: str) -> List[str]:
    t = (title or "").strip()
    signals: List[str] = []
    if len(t) < 35:
        signals.append("title_too_short")
    lower = t.lower()
    vague = ("nice", "cool", "cute", "vintage style", "mens", "womens")
    if any(w in lower for w in vague) and len(t) < 50:
        signals.append("vague_or_generic_words")
    word_count = len(t.split())
    if word_count < 6:
        signals.append("low_keyword_density")
    if not any(x in lower for x in ("size", "sz", "m ", " l", " xl", "s ")):
        if "x" not in lower:
            signals.append("size_not_obvious_in_title")
    return signals


def missing_critical_fields(listing: ListingRecord) -> List[str]:
    missing: List[str] = []
    if not listing.brand and not listing.item_specifics.get("Brand"):
        missing.append("brand")
    if not listing.size and not listing.item_specifics.get("Size"):
        missing.append("size")
    if not listing.department:
        missing.append("department")
    if not listing.garment_type:
        missing.append("garment_type")
    if not listing.color and not listing.item_specifics.get("Color"):
        missing.append("color")
    if not listing.material and not listing.item_specifics.get("Material"):
        missing.append("material")
    style_kw = listing.extra.get("style_keywords") if listing.extra else None
    if not style_kw:
        desc = (listing.description or "") + " " + listing.title
        if not any(k in desc.lower() for k in ("streetwear", "minimal", "grunge", "workwear")):
            if len((listing.description or "")) < 80:
                missing.append("style_keywords")
    return missing


class InventoryAnalyst:
    def __init__(self, settings: Optional[Settings] = None):
        self._s = settings or get_settings()

    def analyze(
        self,
        listing: ListingRecord,
        as_of: Optional[date] = None,
        market_summary: Optional[MarketCompSummary] = None,
    ) -> ListingAnalysis:
        today = as_of or date.today()
        days = compute_days_active(listing, today)
        bucket = age_to_bucket(days, self._s)
        wt = weak_title_signals(listing.title)
        mf = missing_critical_fields(listing)
        desc = listing.description or ""
        weak_desc = len(desc.strip()) < 60

        stale_reasons: List[str] = []
        if days >= self._s.age_threshold_days_90:
            stale_reasons.append("age_90_plus")
        if days >= self._s.age_threshold_days_60 and (listing.watchers or 0) == 0:
            stale_reasons.append("no_watchers_mid_age")
        if wt:
            stale_reasons.append("weak_title")
        if mf:
            stale_reasons.append("missing_fields")

        is_stale = bool(
            days >= self._s.age_threshold_days_75
            or (days >= self._s.age_threshold_days_60 and len(wt) >= 2)
        )

        dead = days >= self._s.age_threshold_days_180 and (listing.watchers or 0) <= 1

        on_track, leverage = self._track_and_leverage(
            days, listing, wt, mf, weak_desc
        )

        group_keys = {
            "brand": (listing.brand or listing.item_specifics.get("Brand") or "unknown").lower(),
            "garment_type": (listing.garment_type or "unknown").lower(),
            "size": (listing.size or listing.item_specifics.get("Size") or "unknown").lower(),
            "age_bucket": bucket.value,
        }

        price_note = None
        if listing.price_amount > 500 and days > 60 and (listing.watchers or 0) < 2:
            price_note = "high_price_low_engagement"
        if (
            market_summary
            and market_summary.price_position == MarketPricePosition.ABOVE_MARKET
            and market_summary.comp_match_confidence >= 0.35
        ):
            price_note = price_note or "above_sold_comps_median"
        if (
            market_summary
            and market_summary.price_position == MarketPricePosition.BELOW_MARKET
            and market_summary.comp_match_confidence >= 0.35
        ):
            price_note = price_note or "below_sold_comps_median"

        return ListingAnalysis(
            listing_id=listing.listing_id,
            days_active=days,
            age_bucket=bucket,
            is_stale=is_stale,
            stale_reasons=stale_reasons,
            missing_critical_fields=mf,
            weak_title_signals=wt,
            weak_description=weak_desc,
            price_outlier_note=price_note,
            dead_stock_likely=dead,
            on_track_90_day_sale=on_track,
            highest_leverage_action=leverage,
            group_keys=group_keys,
            market=market_summary,
        )

    def _track_and_leverage(
        self,
        days: int,
        listing: ListingRecord,
        weak_title: List[str],
        missing: List[str],
        weak_desc: bool,
    ) -> Tuple[bool, str]:
        watchers = listing.watchers or 0
        pace_ok = days <= 60 or (days <= 85 and watchers >= 2)
        if pace_ok and not weak_title and len(missing) <= 1:
            return True, "maintain_and_monitor"

        if days < 45 and (weak_title or missing):
            return False, "optimize_listing_quality_first"

        if days >= 75 and watchers == 0 and (weak_title or len(missing) >= 2):
            return False, "reprice_or_refresh_after_optimization"

        if days >= 90 and watchers <= 1:
            return False, "end_and_sell_similar_or_markdown"

        if days >= 60:
            return False, "intervention_bundle_or_markdown"

        return False, "optimize_listing_quality_first"


# Optional threshold for 45 — add to settings for clarity