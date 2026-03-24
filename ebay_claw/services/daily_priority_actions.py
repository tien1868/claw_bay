"""Rank top operator actions for the day — read-only scoring."""

from __future__ import annotations

from datetime import date
from typing import Callable, List, Optional

from ebay_claw.agents.pricing_agent import PricingAgent
from ebay_claw.agents.title_agent import TitleAgent
from ebay_claw.analytics.strategy_scoring import StrategyScorer
from ebay_claw.config.settings import Settings, get_settings
from ebay_claw.models.domain import ListingAnalysis, ListingRecord, ProposedActionType
from ebay_claw.analytics.history_scoring import (
    compute_action_track_scores,
    default_track_score,
    listing_history_movement_bonus,
    recent_listing_creation_bonus,
)
from ebay_claw.services.outcome_attribution import (
    compute_attributed_lift_scores,
    summarize_action_effectiveness,
)
from ebay_claw.models.recovery import DailyPriorityAction
from ebay_claw.services.operational_history_store import OperationalHistoryStore


def build_daily_priority_actions(
    listings: List[ListingRecord],
    *,
    enriched_fn: Callable[[ListingRecord], ListingAnalysis],
    as_of: Optional[date] = None,
    top_n: int = 10,
    settings: Optional[Settings] = None,
    history_store: Optional[OperationalHistoryStore] = None,
) -> List[DailyPriorityAction]:
    s = settings or get_settings()
    today = as_of or date.today()
    scorer = StrategyScorer(settings=s)
    pricing = PricingAgent(settings=s)
    title_agent = TitleAgent()
    candidates: List[DailyPriorityAction] = []
    store = history_store or OperationalHistoryStore(settings=s)
    use_history = store.has_recent_sync_signal(today, lookback_days=21)
    dash = summarize_action_effectiveness(store, as_of=today) if use_history else None
    lift_scores = (
        compute_attributed_lift_scores(store, today, dashboard=dash) if use_history else {}
    )
    by_action = {s.action_key: s for s in dash.summaries} if dash else {}
    track_scores = (
        compute_action_track_scores(store, today, lift_scores=lift_scores)
        if use_history
        else {}
    )
    default_track = default_track_score(track_scores) if use_history else 0.0

    for lst in listings:
        analysis = enriched_fn(lst)
        sc = scorer.score(lst, analysis, as_of=today)
        pr = pricing.recommend(lst, analysis, sc.recommended_strategy)

        urgency = min(1.0, analysis.days_active / 120.0)
        ease = 0.75
        if pr.recommended_action.value in ("MARKDOWN_10", "HOLD"):
            ease = 0.85
        if pr.recommended_action.value in ("IMPROVE_TITLE",):
            ease = 0.7

        lift = min(1.0, float(lst.price_amount) / 500.0) * 0.3
        st_delta = max(0.0, 0.55 - sc.sale_likelihood_before_90_days)
        conf = pr.confidence

        action_type = ProposedActionType.UPDATE_TITLE
        reason = pr.explanation[:400]
        if pr.recommended_action.value.startswith("MARKDOWN"):
            action_type = ProposedActionType.MARKDOWN_LISTING
        elif pr.recommended_action.value == "IMPROVE_TITLE":
            action_type = ProposedActionType.UPDATE_TITLE
            ts = title_agent.suggest(lst)
            reason = ts.rationale[:400]
        elif pr.recommended_action.value == "FILL_SPECIFICS":
            action_type = ProposedActionType.UPDATE_SAFE_SPECIFICS
        elif pr.recommended_action.value == "END_AND_SELL_SIMILAR":
            action_type = ProposedActionType.END_AND_SELL_SIMILAR
        elif pr.recommended_action.value == "BUNDLE_CANDIDATE":
            action_type = ProposedActionType.PREPARE_RELIST

        if use_history:
            track_component = 10.0 * track_scores.get(action_type.value, default_track)
            mov = listing_history_movement_bonus(store, lst.listing_id, today)
            rc = recent_listing_creation_bonus(store, lst.listing_id, today)
            listing_hist = 6.0 * mov + 4.0 * rc
        else:
            track_component = 0.0
            listing_hist = 0.0

        score = (
            22.0 * lift
            + 28.0 * st_delta
            + 18.0 * conf
            + 20.0 * urgency
            + 12.0 * ease
            + track_component
            + listing_hist
        )
        breakdown = {
            "revenue_lift": round(22.0 * lift, 2),
            "sell_through_gap": round(28.0 * st_delta, 2),
            "confidence": round(18.0 * conf, 2),
            "urgency": round(20.0 * urgency, 2),
            "ease": round(12.0 * ease, 2),
            "history_action_track": round(track_component, 2),
            "history_listing_movement": round(listing_hist, 2),
        }
        if use_history:
            srow = by_action.get(action_type.value)
            if srow is not None:
                breakdown["history_attribution_lift"] = round(
                    srow.ranking_attribution_lift_score, 4
                )
                breakdown["history_attribution_lift_all_time"] = round(
                    srow.attribution_lift_score, 4
                )
                breakdown["history_attribution_lift_recent"] = round(
                    srow.recent_attribution_lift_score, 4
                )
                breakdown["history_attribution_evidence_tier"] = srow.evidence_tier
                breakdown["history_attribution_recent_tier"] = srow.recent_evidence_tier
                breakdown["history_attribution_recent_cohort"] = float(
                    srow.recent_cohort_actions_count
                )
                breakdown["history_attribution_weight"] = round(srow.attribution_lift_weight, 4)
        if not use_history:
            breakdown["history_data_quality"] = 1.0

        candidates.append(
            DailyPriorityAction(
                rank=0,
                listing_id=lst.listing_id,
                title_snippet=(lst.title[:72] + "…") if len(lst.title) > 72 else lst.title,
                action_type=action_type,
                reason=reason,
                expected_impact_summary=pr.expected_effect_on_90_day_sell_through[:300],
                score=round(score, 2),
                score_breakdown=breakdown,
                urgency=round(urgency, 3),
                ease=round(ease, 3),
                confidence=round(conf, 3),
            )
        )

    candidates.sort(key=lambda x: -x.score)
    out: List[DailyPriorityAction] = []
    for i, c in enumerate(candidates[:top_n], start=1):
        out.append(c.model_copy(update={"rank": i}))
    return out
