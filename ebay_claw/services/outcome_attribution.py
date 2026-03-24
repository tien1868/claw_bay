"""
Derive action→outcome links from append-only operational history (read-only).

Primary rule: for each outcome event on a listing, pick the **most recent** qualifying
prior event in ``(outcome_ts - window, outcome_ts)``. Qualifying types:
``queue_approved``, ``relist_proposed``, ``bundle_proposed``.

Ambiguous when the two most recent candidates are within **120 seconds** of each other.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Set, Tuple

from ebay_claw.models.attribution import (
    ActionEffectivenessDashboard,
    ActionEffectivenessSummary,
    ActionOutcomeLink,
    EvidenceTier,
)
from ebay_claw.models.domain import ProposedActionType
from ebay_claw.services.operational_history_store import OperationalHistoryStore

PRIOR_TYPES: Set[str] = {"queue_approved", "relist_proposed", "bundle_proposed"}
OUTCOME_TYPES: Set[str] = {"listing_sold", "stale_cleared"}
AMBIGUITY_SECONDS = 120

# Evidence-weighted attribution (read-only): shrinkage + tier gates.
PRIOR_SALE_RATE = 0.12
PRIOR_STALE_CLEAR_RATE = 0.07
SHRINKAGE_PSEUDO_COUNT = 20.0
NEUTRAL_ATTRIBUTION_LIFT = 0.42
RAW_SIGNAL_SALE_WEIGHT = 0.72
RAW_SIGNAL_STALE_WEIGHT = 0.28

# Cohort size n → tier; weight scales how much raw (shrunk) signal moves lift from neutral.
EVIDENCE_TIER_THRESHOLDS: Tuple[Tuple[int, int, EvidenceTier, float], ...] = (
    (0, 5, "insufficient", 0.0),
    (5, 15, "weak", 0.25),
    (15, 40, "moderate", 0.55),
    (40, 10**9, "strong", 1.0),
)


def evidence_tier_and_weight(cohort_n: int) -> Tuple[EvidenceTier, float]:
    """Map cohort size to dashboard tier and weight for ranking lift."""
    for lo, hi, tier, w in EVIDENCE_TIER_THRESHOLDS:
        if lo <= cohort_n < hi:
            return tier, w
    return "strong", 1.0


def shrunk_binomial_rate(successes: float, cohort_n: int, prior: float, k: float) -> float:
    """Cohort-size-aware shrinkage toward a prior rate (empirical Bayes style)."""
    if cohort_n <= 0:
        return prior
    return (successes + prior * k) / (cohort_n + k)


def raw_outcome_signal(shrunk_sale: float, shrunk_stale: float) -> float:
    """Combine primary (sale) and secondary (stale exit) recovery signals."""
    return (
        RAW_SIGNAL_SALE_WEIGHT * shrunk_sale
        + RAW_SIGNAL_STALE_WEIGHT * shrunk_stale
    )


def recency_weight(
    outcome_ts: datetime,
    *,
    reference_end_utc: datetime,
    half_life_days: float,
) -> float:
    """
    Weight an outcome by how recent it is relative to ``reference_end_utc`` (typically as-of EOD).

    Uses exponential decay: exp(-age_days / half_life_days).
    """
    if half_life_days <= 0:
        return 1.0
    age_days = (reference_end_utc - _to_utc(outcome_ts)).total_seconds() / 86400.0
    if age_days < 0:
        return 0.0
    return math.exp(-age_days / half_life_days)


def blend_ranking_attribution_lift(
    *,
    all_time_lift: float,
    all_time_tier: EvidenceTier,
    all_time_den: int,
    recent_lift: float,
    recent_tier: EvidenceTier,
    recent_den: int,
) -> float:
    """
    Prefer recency-weighted lift when recent cohort has enough evidence; else all-time.

    Keeps read-only semantics; thresholds align with evidence tiers.
    """
    if recent_den < 5 or recent_tier == "insufficient":
        return all_time_lift
    if recent_tier == "weak":
        return min(1.0, max(0.0, 0.42 * recent_lift + 0.58 * all_time_lift))
    if recent_tier == "moderate":
        return min(1.0, max(0.0, 0.58 * recent_lift + 0.42 * all_time_lift))
    # strong
    return min(1.0, max(0.0, 0.68 * recent_lift + 0.32 * all_time_lift))


def compute_attribution_lift(
    *,
    cohort_n: int,
    attributed_sales: float,
    attributed_stale: float,
) -> Tuple[float, EvidenceTier, float, float]:
    """
    Lift in [0,1] used for blending; neutral when evidence is insufficient.

    Returns (lift_score, tier, weight, raw_outcome_signal).
    """
    tier, w = evidence_tier_and_weight(cohort_n)
    shr_s = shrunk_binomial_rate(
        attributed_sales, cohort_n, PRIOR_SALE_RATE, SHRINKAGE_PSEUDO_COUNT
    )
    shr_st = shrunk_binomial_rate(
        attributed_stale, cohort_n, PRIOR_STALE_CLEAR_RATE, SHRINKAGE_PSEUDO_COUNT
    )
    raw = raw_outcome_signal(shr_s, shr_st)
    # Ranking: weak tiers move less from neutral than strong (same raw signal).
    lift = NEUTRAL_ATTRIBUTION_LIFT + w * (raw - NEUTRAL_ATTRIBUTION_LIFT)
    return min(1.0, max(0.0, lift)), tier, w, raw


def _day_end(as_of: date) -> datetime:
    return datetime(as_of.year, as_of.month, as_of.day, 23, 59, 59, tzinfo=timezone.utc)


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _proposed_from_event(rec) -> Optional[str]:
    p = rec.payload.get("proposed_action_type")
    return str(p) if p is not None else None


def _sale_link_matches_action(link: ActionOutcomeLink, mode: str, match_val: str) -> bool:
    if mode == "relist_proposed":
        return (
            link.primary_kind == "relist_proposed"
            and link.primary_proposed_action_type == match_val
        )
    if mode == "bundle_proposed":
        return (
            link.primary_kind == "bundle_proposed"
            and link.primary_proposed_action_type == match_val
        )
    return (
        link.primary_kind == "queue_approved"
        and link.primary_proposed_action_type == match_val
    )


def _stale_link_matches_action(link: ActionOutcomeLink, mode: str, match_val: str) -> bool:
    if link.primary_proposed_action_type != match_val:
        return False
    if mode == "queue_approved":
        return link.primary_kind == "queue_approved"
    if mode == "relist_proposed":
        return link.primary_kind == "relist_proposed"
    if mode == "bundle_proposed":
        return link.primary_kind == "bundle_proposed"
    return False


def _collect_listing_events(store: OperationalHistoryStore) -> Dict[str, List]:
    """All events with listing_id, sorted by time."""
    by_listing: Dict[str, List] = defaultdict(list)
    for rec in store.iter_events():
        if not rec.listing_id:
            continue
        by_listing[rec.listing_id].append(rec)
    for lid in by_listing:
        by_listing[lid].sort(key=lambda r: _to_utc(r.occurred_at_utc))
    return by_listing


def build_action_outcome_links(
    store: OperationalHistoryStore,
    *,
    as_of: date,
    attribution_window_days: int = 90,
    history_start_days: int = 730,
) -> List[ActionOutcomeLink]:
    """
    Build attributed links for outcomes in [as_of - history_start_days, as_of].

    Each ``listing_sold`` / ``stale_cleared`` gets at most one primary prior action
    in the attribution window before the outcome.
    """
    end = _day_end(as_of)
    start = end - timedelta(days=history_start_days)
    by_listing = _collect_listing_events(store)
    links: List[ActionOutcomeLink] = []

    for listing_id, events in by_listing.items():
        for rec in events:
            if rec.event_type not in OUTCOME_TYPES:
                continue
            out_ts = _to_utc(rec.occurred_at_utc)
            if out_ts < start or out_ts > end:
                continue
            win_start = out_ts - timedelta(days=attribution_window_days)
            prior_candidates = [
                e
                for e in events
                if e.event_type in PRIOR_TYPES
                and win_start <= _to_utc(e.occurred_at_utc) < out_ts
            ]
            prior_candidates.sort(key=lambda e: _to_utc(e.occurred_at_utc), reverse=True)
            if not prior_candidates:
                continue
            top = prior_candidates[0]
            ambiguous = False
            note: Optional[str] = None
            if len(prior_candidates) > 1:
                second = prior_candidates[1]
                dt = abs(
                    (
                        _to_utc(top.occurred_at_utc) - _to_utc(second.occurred_at_utc)
                    ).total_seconds()
                )
                if dt < AMBIGUITY_SECONDS:
                    ambiguous = True
                    note = "two_prior_actions_within_120s"

            outcome_kind = "sale" if rec.event_type == "listing_sold" else "stale_cleared"
            days = (out_ts - _to_utc(top.occurred_at_utc)).total_seconds() / 86400.0
            links.append(
                ActionOutcomeLink(
                    listing_id=listing_id,
                    outcome_kind=outcome_kind,
                    outcome_event_id=rec.event_id,
                    outcome_at_utc=out_ts,
                    attribution_window_days=attribution_window_days,
                    primary_event_id=top.event_id,
                    primary_kind=top.event_type,  # type: ignore[arg-type]
                    primary_at_utc=_to_utc(top.occurred_at_utc),
                    primary_proposed_action_type=_proposed_from_event(top),
                    primary_review_item_id=top.review_item_id,
                    days_from_primary_to_outcome=round(days, 3),
                    ambiguous=ambiguous,
                    ambiguity_note=note,
                )
            )
    return links


def summarize_action_effectiveness(
    store: OperationalHistoryStore,
    *,
    as_of: date,
    attribution_window_days: int = 90,
    cohort_lookback_days: int = 180,
    observation_days_for_sale: int = 90,
    recent_cohort_days: int = 90,
    recency_half_life_days: float = 45.0,
) -> ActionEffectivenessDashboard:
    """
    Dashboard rollups: title update, markdown, relist, bundle.

    Cohort: queue_approved (or proposal-only for relist/bundle) in
    [as_of - cohort_lookback_days, as_of], with optional censoring note.
    """
    end = _day_end(as_of)
    cohort_start = end - timedelta(days=cohort_lookback_days)
    recent_start = end - timedelta(days=recent_cohort_days)
    ref_end = end

    links = build_action_outcome_links(
        store,
        as_of=as_of,
        attribution_window_days=attribution_window_days,
    )
    sale_links = [x for x in links if x.outcome_kind == "sale"]
    stale_links = [x for x in links if x.outcome_kind == "stale_cleared"]

    # Unattributed: outcomes with no qualifying prior in the attribution window
    by_listing = _collect_listing_events(store)
    unattributed_sales = 0
    for lid, evs in by_listing.items():
        for rec in evs:
            if rec.event_type != "listing_sold":
                continue
            ts = _to_utc(rec.occurred_at_utc)
            if ts < cohort_start or ts > end:
                continue
            win_start = ts - timedelta(days=attribution_window_days)
            priors = [
                e
                for e in evs
                if e.event_type in PRIOR_TYPES
                and win_start <= _to_utc(e.occurred_at_utc) < ts
            ]
            if not priors:
                unattributed_sales += 1

    unattributed_stale = 0
    for lid, evs in by_listing.items():
        for rec in evs:
            if rec.event_type != "stale_cleared":
                continue
            ts = _to_utc(rec.occurred_at_utc)
            if ts < cohort_start or ts > end:
                continue
            win_start = ts - timedelta(days=attribution_window_days)
            priors = [
                e
                for e in evs
                if e.event_type in PRIOR_TYPES
                and win_start <= _to_utc(e.occurred_at_utc) < ts
            ]
            if not priors:
                unattributed_stale += 1

    def count_cohort_approvals(action_value: str, *, since_start: datetime) -> int:
        n = 0
        for rec in store.iter_events(
            since_utc=since_start,
            until_utc=end,
            event_types={"queue_approved"},
        ):
            if rec.payload.get("proposed_action_type") == action_value:
                n += 1
        return n

    def count_cohort_proposals(ev: str, val: str, *, since_start: datetime) -> int:
        n = 0
        for rec in store.iter_events(
            since_utc=since_start,
            until_utc=end,
            event_types={ev},  # type: ignore[arg-type]
        ):
            if rec.payload.get("proposed_action_type") == val:
                n += 1
        return n

    specs: List[Tuple[str, str, str, str]] = [
        (
            ProposedActionType.UPDATE_TITLE.value,
            "Title improvement (approved)",
            "queue_approved",
            ProposedActionType.UPDATE_TITLE.value,
        ),
        (
            ProposedActionType.UPDATE_SAFE_SPECIFICS.value,
            "Safe inventory specifics (approved)",
            "queue_approved",
            ProposedActionType.UPDATE_SAFE_SPECIFICS.value,
        ),
        (
            ProposedActionType.MARKDOWN_LISTING.value,
            "Repricing / markdown (approved)",
            "queue_approved",
            ProposedActionType.MARKDOWN_LISTING.value,
        ),
        (
            ProposedActionType.RELIST_CANDIDATE.value,
            "Relist proposal",
            "relist_proposed",
            ProposedActionType.RELIST_CANDIDATE.value,
        ),
        (
            ProposedActionType.BUNDLE_LOT_CANDIDATE.value,
            "Bundle lot proposal",
            "bundle_proposed",
            ProposedActionType.BUNDLE_LOT_CANDIDATE.value,
        ),
    ]

    summaries: List[ActionEffectivenessSummary] = []

    for key, label, mode, match_val in specs:
        if mode == "queue_approved":
            denom = count_cohort_approvals(match_val, since_start=cohort_start)
            denom_recent = count_cohort_approvals(match_val, since_start=recent_start)
        else:
            denom = count_cohort_proposals(mode, match_val, since_start=cohort_start)
            denom_recent = count_cohort_proposals(mode, match_val, since_start=recent_start)

        asale = sum(
            1
            for x in sale_links
            if _sale_link_matches_action(x, mode, match_val)
        )

        astale = sum(
            1
            for x in stale_links
            if _stale_link_matches_action(x, mode, match_val)
        )

        w_sale_recent = 0.0
        for x in sale_links:
            if not _sale_link_matches_action(x, mode, match_val):
                continue
            ots = _to_utc(x.outcome_at_utc)
            if ots < recent_start:
                continue
            w_sale_recent += recency_weight(
                ots,
                reference_end_utc=ref_end,
                half_life_days=recency_half_life_days,
            )

        w_stale_recent = 0.0
        for x in stale_links:
            if not _stale_link_matches_action(x, mode, match_val):
                continue
            ots = _to_utc(x.outcome_at_utc)
            if ots < recent_start:
                continue
            w_stale_recent += recency_weight(
                ots,
                reference_end_utc=ref_end,
                half_life_days=recency_half_life_days,
            )

        sr = round(asale / max(1, denom), 4) if denom else None
        scr = round(astale / max(1, denom), 4) if denom else None
        shr_s = (
            round(
                shrunk_binomial_rate(asale, denom, PRIOR_SALE_RATE, SHRINKAGE_PSEUDO_COUNT),
                4,
            )
            if denom
            else None
        )
        shr_st = (
            round(
                shrunk_binomial_rate(
                    astale, denom, PRIOR_STALE_CLEAR_RATE, SHRINKAGE_PSEUDO_COUNT
                ),
                4,
            )
            if denom
            else None
        )
        lift_score, tier, lift_w, raw_sig = compute_attribution_lift(
            cohort_n=denom,
            attributed_sales=float(asale),
            attributed_stale=float(astale),
        )
        recent_lift, recent_tier, recent_w, recent_raw = compute_attribution_lift(
            cohort_n=denom_recent,
            attributed_sales=w_sale_recent,
            attributed_stale=w_stale_recent,
        )
        ranking_lift = blend_ranking_attribution_lift(
            all_time_lift=lift_score,
            all_time_tier=tier,
            all_time_den=denom,
            recent_lift=recent_lift,
            recent_tier=recent_tier,
            recent_den=denom_recent,
        )
        note = (
            f"Cohort ~{cohort_lookback_days}d of operational history; "
            f"outcomes attributed within {attribution_window_days}d before sale/stale exit. "
            f"Recent window ~{recent_cohort_days}d with exp decay (half-life {recency_half_life_days}d). "
            f"All-time tier {tier} (w={lift_w:.2f}); recent tier {recent_tier} (w={recent_w:.2f}). "
            f"Not causal proof."
        )
        summaries.append(
            ActionEffectivenessSummary(
                action_key=key,
                label=label,
                cohort_actions_count=denom,
                attributed_sales=asale,
                attributed_stale_cleared=astale,
                sale_rate=sr,
                stale_clear_rate=scr,
                shrunk_sale_rate=shr_s,
                shrunk_stale_clear_rate=shr_st,
                evidence_tier=tier,
                attribution_lift_weight=lift_w,
                attribution_lift_score=lift_score,
                recent_attribution_lift_score=recent_lift,
                recent_evidence_tier=recent_tier,
                recent_cohort_actions_count=denom_recent,
                ranking_attribution_lift_score=ranking_lift,
                raw_outcome_signal=round(raw_sig, 4),
                recent_raw_outcome_signal=round(recent_raw, 4),
                observation_note=note,
            )
        )

    return ActionEffectivenessDashboard(
        as_of=as_of.isoformat(),
        attribution_window_days=attribution_window_days,
        observation_cutoff_days=observation_days_for_sale,
        recent_cohort_days=recent_cohort_days,
        recency_half_life_days=recency_half_life_days,
        summaries=summaries,
        unattributed_sales_in_window=unattributed_sales,
        unattributed_stale_cleared_in_window=unattributed_stale,
    )


def compute_attributed_lift_scores(
    store: OperationalHistoryStore,
    as_of: date,
    *,
    attribution_window_days: int = 90,
    dashboard: Optional[ActionEffectivenessDashboard] = None,
) -> Dict[str, float]:
    """
    Map proposed_action_type value → [0,1] lift proxy (evidence-weighted, shrinkage-adjusted).

    Used to blend with proposal/approval ratios in ``history_scoring``.
    """
    dash = dashboard or summarize_action_effectiveness(
        store,
        as_of=as_of,
        attribution_window_days=attribution_window_days,
    )
    return {s.action_key: s.ranking_attribution_lift_score for s in dash.summaries}
