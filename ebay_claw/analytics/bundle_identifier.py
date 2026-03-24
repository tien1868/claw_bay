"""Bundle / lot candidates — proposal-only, no publish."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date
from typing import DefaultDict, List, Tuple

from ebay_claw.analytics.inventory_analyst import compute_days_active
from ebay_claw.models.domain import ListingRecord
from ebay_claw.models.recovery import BundleRecommendation


def _key(lst: ListingRecord) -> Tuple[str, str, str]:
    brand = (lst.brand or lst.item_specifics.get("Brand") or "unknown").strip().lower()
    dept = (lst.department or "unknown").strip().lower()
    gt = (lst.garment_type or "unknown").strip().lower()
    return (brand, dept, gt)


def identify_bundle_candidates(
    listings: List[ListingRecord],
    *,
    as_of: date | None = None,
    max_asp: float = 38.0,
    min_age_days: int = 60,
    min_bundle_size: int = 2,
    max_bundles: int = 12,
) -> List[BundleRecommendation]:
    """
    Group low-ASP, aged, low-engagement items by brand + department + garment type.
    Size compatibility: same size label only for MVP (reduces bad bundle suggestions).
    """
    buckets: DefaultDict[Tuple[str, str, str, str], List[ListingRecord]] = defaultdict(list)
    today = as_of or date.today()
    for lst in listings:
        if lst.price_amount > max_asp:
            continue
        days = compute_days_active(lst, today)
        if days < min_age_days:
            continue
        if (lst.watchers or 0) > 1:
            continue
        sz = (lst.size or lst.item_specifics.get("Size") or "").strip().lower() or "unknown"
        b, d, g = _key(lst)
        if b == "unknown":
            continue
        buckets[(b, d, g, sz)].append(lst)

    out: List[BundleRecommendation] = []
    for (b, d, g, sz), group in buckets.items():
        if len(group) < min_bundle_size:
            continue
        group = sorted(group, key=lambda x: x.price_amount)[:6]
        lids = [x.listing_id for x in group]
        total_low = sum(x.price_amount for x in group) * 0.88
        total_high = sum(x.price_amount for x in group) * 0.95
        title = f"Lot: {g.title()} — {b.title()} — size {sz} ({len(group)} pcs)"
        rationale = (
            f"Low ASP (${max_asp:.0f} cap) + {min_age_days}d+ age + weak engagement — "
            "moving as a lot can clear shelf faster than serial single-unit markdowns."
        )
        signals = [
            f"asp_under_{int(max_asp)}",
            f"age_{min_age_days}_plus",
            "watchers_lte_1",
            "brand_dept_garment_size_bucket",
        ]
        out.append(
            BundleRecommendation(
                bundle_id=str(uuid.uuid4()),
                listing_ids=lids,
                suggested_lot_title=title[:120],
                target_lot_price_low=round(total_low, 2),
                target_lot_price_high=round(total_high, 2),
                rationale=rationale,
                grouping_signals=signals,
                confidence=0.55,
            )
        )
        if len(out) >= max_bundles:
            break

    return out
