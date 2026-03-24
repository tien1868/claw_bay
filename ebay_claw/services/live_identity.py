"""Compare queued review snapshot vs live listing — fail-closed apply revalidation."""

from __future__ import annotations

from typing import Any, Dict, List

from ebay_claw.models.domain import ListingRecord, ReviewQueueItem
from ebay_claw.review_queue.fingerprint import listing_snapshot_fingerprint


def collect_live_identity_blockers(
    item: ReviewQueueItem,
    live: ListingRecord,
    *,
    require_enqueue_fingerprint: bool = True,
    strict_live_identity: bool = True,
) -> List[str]:
    """
    Require listing_id, enqueue fingerprint when configured, sku/offer when live has them (strict),
    and fingerprint drift check when the queue row recorded a fingerprint at enqueue time.
    """
    blockers: List[str] = []
    if live.listing_id != item.listing_id:
        blockers.append(
            f"listing_id_mismatch queued={item.listing_id!r} live={live.listing_id!r}"
        )

    expected_fp = (item.listing_snapshot_fingerprint or "").strip()
    if require_enqueue_fingerprint and not expected_fp:
        blockers.append(
            "listing_snapshot_fingerprint_missing: enqueue fingerprint required before apply."
        )

    snap: Dict[str, Any] = dict(item.current_state_snapshot or {})
    snap_sku = snap.get("sku")
    live_sku_s = str(live.sku or "").strip()
    if strict_live_identity and live_sku_s:
        if snap_sku is None or str(snap_sku).strip() == "":
            blockers.append(
                "snapshot_sku_required: live listing has sku but enqueue snapshot does not record sku."
            )
        elif str(snap_sku) != live_sku_s:
            blockers.append(f"sku_mismatch snapshot={snap_sku!r} live={live.sku!r}")
    elif snap_sku is not None and str(snap_sku) != str(live.sku or ""):
        blockers.append(f"sku_mismatch snapshot={snap_sku!r} live={live.sku!r}")

    snap_ex = snap.get("extra") if isinstance(snap.get("extra"), dict) else {}
    if not isinstance(snap_ex, dict):
        snap_ex = {}
    live_ex = live.extra or {}
    snap_offer = snap_ex.get("ebay_offer_id")
    live_offer = live_ex.get("ebay_offer_id")
    if strict_live_identity and live_offer is not None and str(live_offer).strip() != "":
        if snap_offer is None or str(snap_offer).strip() == "":
            blockers.append(
                "snapshot_ebay_offer_id_required: live listing has offer_id but "
                "enqueue snapshot does not record ebay_offer_id."
            )
        elif snap_offer != live_offer:
            blockers.append(
                f"ebay_offer_id_mismatch snapshot={snap_offer!r} live={live_offer!r}"
            )
    elif snap_offer is not None or live_offer is not None:
        if snap_offer != live_offer:
            blockers.append(
                f"ebay_offer_id_mismatch snapshot={snap_offer!r} live={live_offer!r}"
            )

    if expected_fp:
        cur_fp = listing_snapshot_fingerprint(live)
        if cur_fp != expected_fp:
            blockers.append(
                "listing_fingerprint_drift: live listing title/price/sku/specifics no longer "
                f"match enqueue snapshot (expected {expected_fp[:16]}…, got {cur_fp[:16]}…)."
            )

    return blockers
