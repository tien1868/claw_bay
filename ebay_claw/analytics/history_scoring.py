"""Historical movement signals for daily priority ranking — read-only."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Dict, Optional

from ebay_claw.models.domain import ProposedActionType
from ebay_claw.models.operational_history import OperationalEventType
from ebay_claw.services.operational_history_store import OperationalHistoryStore
from ebay_claw.services.outcome_attribution import compute_attributed_lift_scores


def _day_end(as_of: date) -> datetime:
    return datetime(as_of.year, as_of.month, as_of.day, 23, 59, 59, tzinfo=timezone.utc)


def compute_action_track_scores(
    store: OperationalHistoryStore,
    as_of: date,
    *,
    lookback_days: int = 90,
    lift_scores: Optional[Dict[str, float]] = None,
    attribution_window_days: int = 90,
) -> Dict[str, float]:
    """
    Blend proposal/approval ratios with attributed outcome lift (sale / stale recovery).

    Relist and bundle use approvals vs proposals in the lookback window. Title and markdown
    have no proposal stream here — neutral base (0.15) blended with attributed lift when present.
    Values in [0, 1].
    """
    end = _day_end(as_of)
    start = end - timedelta(days=lookback_days)
    lift = (
        lift_scores
        if lift_scores is not None
        else compute_attributed_lift_scores(
            store, as_of, attribution_window_days=attribution_window_days
        )
    )

    base_scores: Dict[str, float] = {}
    pairs: list[tuple[str, OperationalEventType, str]] = [
        (ProposedActionType.RELIST_CANDIDATE.value, "relist_proposed", "proposed_action_type"),
        (ProposedActionType.BUNDLE_LOT_CANDIDATE.value, "bundle_proposed", "proposed_action_type"),
    ]
    for val, ev, key in pairs:
        prop, appr = store.proposals_and_approvals_for_action(
            ev, key, val, since_utc=start, until_utc=end
        )
        base_scores[val] = min(1.0, appr / (prop + 1.0))

    for val in (
        ProposedActionType.UPDATE_TITLE.value,
        ProposedActionType.UPDATE_SAFE_SPECIFICS.value,
        ProposedActionType.MARKDOWN_LISTING.value,
    ):
        base_scores.setdefault(val, 0.15)

    out: Dict[str, float] = {}
    for k, base in base_scores.items():
        l = lift.get(k)
        if l is not None:
            out[k] = min(1.0, 0.55 * base + 0.45 * l)
        else:
            out[k] = base
    return out


def default_track_score(track: Dict[str, float]) -> float:
    if not track:
        return 0.15
    return min(1.0, sum(track.values()) / len(track))


def listing_history_movement_bonus(
    store: OperationalHistoryStore,
    listing_id: str,
    as_of: date,
    *,
    lookback_days: int = 180,
) -> float:
    """0..1 boost from repeated stale-band stress on this listing."""
    end = _day_end(as_of)
    start = end - timedelta(days=lookback_days)
    n_stale_cross = store.listing_event_counts(
        listing_id, {"stale_crossed_90d"}, since_utc=start, until_utc=end
    )
    # Clearing stale after crossing shows prior intervention worked — slight positive signal.
    n_cleared = store.listing_event_counts(
        listing_id, {"stale_cleared"}, since_utc=start, until_utc=end
    )
    stress = n_stale_cross + 0.25 * min(n_cleared, 4)
    return min(1.0, stress / 3.0)


def recent_listing_creation_bonus(
    store: OperationalHistoryStore,
    listing_id: str,
    as_of: date,
) -> float:
    """Small boost for net-new listings in the last 30 days (event-recorded)."""
    end = _day_end(as_of)
    start = end - timedelta(days=30)
    for rec in store.iter_events(
        since_utc=start,
        until_utc=end,
        event_types={"listing_created"},
    ):
        if rec.listing_id == listing_id:
            return 0.35
    return 0.0
