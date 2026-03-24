"""Outcome attribution from operational history — read-only analytics."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from ebay_claw.analytics.history_scoring import compute_action_track_scores
from ebay_claw.config.settings import Settings
from ebay_claw.models.domain import ProposedActionType
from ebay_claw.models.runtime_mode import ClawRuntimeMode
from ebay_claw.services.operational_history_store import OperationalHistoryStore
from ebay_claw.services.outcome_attribution import (
    EVIDENCE_TIER_THRESHOLDS,
    NEUTRAL_ATTRIBUTION_LIFT,
    blend_ranking_attribution_lift,
    build_action_outcome_links,
    compute_attribution_lift,
    compute_attributed_lift_scores,
    evidence_tier_and_weight,
    recency_weight,
    shrunk_binomial_rate,
    summarize_action_effectiveness,
)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        runtime_mode=ClawRuntimeMode.FIXTURE,
        require_dry_run_acknowledgement=False,
        operational_history_path=tmp_path / "op_hist.jsonl",
        inventory_movement_snapshot_path=tmp_path / "inv_snap.json",
        audit_log_path=tmp_path / "audit.jsonl",
        review_queue_path=tmp_path / "q.json",
        sync_history_path=tmp_path / "sh.jsonl",
        sync_state_path=tmp_path / "ss.json",
        policy_structured_log_path=tmp_path / "pol.jsonl",
        policy_log_path=tmp_path / "pol.log",
        fixture_path=Path("fixtures/sample_listings.json"),
    )


def test_attribution_respects_window_not_too_narrow(tmp_path: Path):
    store = OperationalHistoryStore(settings=_settings(tmp_path))
    base = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)
    store.append_event(
        "queue_approved",
        source="q",
        listing_id="L1",
        payload={"proposed_action_type": ProposedActionType.UPDATE_TITLE.value},
        occurred_at_utc=base,
    )
    store.append_event(
        "listing_sold",
        source="t",
        listing_id="L1",
        payload={"units": 1.0},
        occurred_at_utc=base + timedelta(days=5),
    )
    as_of = (base + timedelta(days=5)).date()
    narrow = build_action_outcome_links(
        store, as_of=as_of, attribution_window_days=3, history_start_days=30
    )
    wide = build_action_outcome_links(
        store, as_of=as_of, attribution_window_days=7, history_start_days=30
    )
    assert len(narrow) == 0
    assert len(wide) == 1
    assert wide[0].primary_kind == "queue_approved"
    assert wide[0].ambiguous is False


def test_ambiguous_when_two_priors_within_120s(tmp_path: Path):
    store = OperationalHistoryStore(settings=_settings(tmp_path))
    base = datetime(2026, 2, 1, 10, 0, tzinfo=timezone.utc)
    store.append_event(
        "queue_approved",
        source="q",
        listing_id="L2",
        payload={"proposed_action_type": ProposedActionType.UPDATE_TITLE.value},
        occurred_at_utc=base,
    )
    store.append_event(
        "queue_approved",
        source="q",
        listing_id="L2",
        payload={"proposed_action_type": ProposedActionType.MARKDOWN_LISTING.value},
        occurred_at_utc=base + timedelta(seconds=60),
    )
    store.append_event(
        "listing_sold",
        source="t",
        listing_id="L2",
        payload={},
        occurred_at_utc=base + timedelta(days=10),
    )
    as_of = (base + timedelta(days=10)).date()
    links = build_action_outcome_links(store, as_of=as_of, attribution_window_days=30)
    assert len(links) == 1
    assert links[0].ambiguous is True
    assert links[0].ambiguity_note == "two_prior_actions_within_120s"


def test_dashboard_rollups_and_unattributed(tmp_path: Path):
    store = OperationalHistoryStore(settings=_settings(tmp_path))
    end = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
    cohort_start = end - timedelta(days=30)
    store.append_event(
        "listing_synced",
        source="sync",
        payload={"listing_count": 1},
        occurred_at_utc=cohort_start,
    )
    # Cohort: title approval + later sale
    store.append_event(
        "queue_approved",
        source="q",
        listing_id="A",
        payload={"proposed_action_type": ProposedActionType.UPDATE_TITLE.value},
        occurred_at_utc=cohort_start + timedelta(days=1),
    )
    store.append_event(
        "listing_sold",
        source="t",
        listing_id="A",
        payload={},
        occurred_at_utc=cohort_start + timedelta(days=5),
    )
    # Sale with no prior in window (unattributed)
    store.append_event(
        "listing_sold",
        source="t",
        listing_id="B",
        payload={},
        occurred_at_utc=cohort_start + timedelta(days=2),
    )
    dash = summarize_action_effectiveness(
        store,
        as_of=end.date(),
        attribution_window_days=90,
        cohort_lookback_days=180,
    )
    title_row = next(x for x in dash.summaries if x.action_key == ProposedActionType.UPDATE_TITLE.value)
    assert title_row.cohort_actions_count >= 1
    assert title_row.attributed_sales >= 1
    assert dash.unattributed_sales_in_window >= 1


def test_history_scoring_blends_lift_vs_base(tmp_path: Path):
    store = OperationalHistoryStore(settings=_settings(tmp_path))
    end = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)
    start = end - timedelta(days=60)
    store.append_event(
        "listing_synced",
        source="sync",
        payload={"listing_count": 1},
        occurred_at_utc=start,
    )
    for i in range(6):
        lid = f"X{i}"
        store.append_event(
            "relist_proposed",
            source="r",
            listing_id=lid,
            payload={"proposed_action_type": ProposedActionType.RELIST_CANDIDATE.value},
            occurred_at_utc=start + timedelta(hours=i),
        )
        store.append_event(
            "queue_approved",
            source="q",
            listing_id=lid,
            payload={"proposed_action_type": ProposedActionType.RELIST_CANDIDATE.value},
            occurred_at_utc=start + timedelta(hours=i, minutes=5),
        )
        store.append_event(
            "listing_sold",
            source="t",
            listing_id=lid,
            payload={},
            occurred_at_utc=start + timedelta(days=10, hours=i),
        )
    lift = compute_attributed_lift_scores(store, end.date(), attribution_window_days=90)
    assert ProposedActionType.RELIST_CANDIDATE.value in lift
    base_only = compute_action_track_scores(
        store,
        end.date(),
        lift_scores={k: 0.15 for k in lift},
        attribution_window_days=90,
    )
    blended = compute_action_track_scores(
        store,
        end.date(),
        lift_scores=lift,
        attribution_window_days=90,
    )
    assert blended[ProposedActionType.RELIST_CANDIDATE.value] != pytest.approx(
        base_only[ProposedActionType.RELIST_CANDIDATE.value], abs=1e-6
    )


def test_shrinkage_pulls_high_raw_rate_toward_prior_with_small_cohort():
    # n=4, all attributed sales → raw rate 1.0, but shrunk rate much lower
    shr = shrunk_binomial_rate(4, 4, 0.12, 20.0)
    assert shr < 0.5
    assert shr > 0.12
    lift, tier, w, raw = compute_attribution_lift(
        cohort_n=4, attributed_sales=4, attributed_stale=0
    )
    assert tier == "insufficient"
    assert w == 0.0
    assert lift == pytest.approx(NEUTRAL_ATTRIBUTION_LIFT)


def test_evidence_tier_thresholds_match_documented_bands():
    assert evidence_tier_and_weight(0)[0] == "insufficient"
    assert evidence_tier_and_weight(4)[0] == "insufficient"
    assert evidence_tier_and_weight(5)[0] == "weak"
    assert evidence_tier_and_weight(14)[0] == "weak"
    assert evidence_tier_and_weight(15)[0] == "moderate"
    assert evidence_tier_and_weight(39)[0] == "moderate"
    assert evidence_tier_and_weight(40)[0] == "strong"
    assert len(EVIDENCE_TIER_THRESHOLDS) == 4


def test_moderate_large_cohort_can_outrank_weak_small_sample_signal():
    """Strong cohort with good (shrunk) outcomes beats weak tier even if small-n cohort looks perfect."""
    lift_weak, _, w_weak, _ = compute_attribution_lift(
        cohort_n=10, attributed_sales=10, attributed_stale=0
    )
    assert w_weak == 0.25
    lift_strong, _, w_strong, _ = compute_attribution_lift(
        cohort_n=45, attributed_sales=40, attributed_stale=0
    )
    assert w_strong == 1.0
    assert lift_strong > lift_weak


def test_dashboard_summary_includes_evidence_tier(tmp_path: Path):
    store = OperationalHistoryStore(settings=_settings(tmp_path))
    end = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
    cohort_start = end - timedelta(days=30)
    store.append_event(
        "listing_synced",
        source="sync",
        payload={"listing_count": 1},
        occurred_at_utc=cohort_start,
    )
    store.append_event(
        "queue_approved",
        source="q",
        listing_id="A",
        payload={"proposed_action_type": ProposedActionType.UPDATE_TITLE.value},
        occurred_at_utc=cohort_start + timedelta(days=1),
    )
    dash = summarize_action_effectiveness(
        store,
        as_of=end.date(),
        attribution_window_days=90,
        cohort_lookback_days=180,
    )
    title_row = next(x for x in dash.summaries if x.action_key == ProposedActionType.UPDATE_TITLE.value)
    assert title_row.evidence_tier in ("insufficient", "weak", "moderate", "strong")
    assert 0.0 <= title_row.attribution_lift_weight <= 1.0
    assert title_row.evidence_tier == evidence_tier_and_weight(title_row.cohort_actions_count)[0]
    assert title_row.ranking_attribution_lift_score == pytest.approx(
        title_row.attribution_lift_score, abs=1e-6
    )


def test_blend_ranking_falls_back_to_all_time_when_recent_evidence_insufficient():
    r = blend_ranking_attribution_lift(
        all_time_lift=0.35,
        all_time_tier="moderate",
        all_time_den=30,
        recent_lift=0.95,
        recent_tier="strong",
        recent_den=3,
    )
    assert r == pytest.approx(0.35)


def test_blend_prefers_recent_when_recent_cohort_strong():
    r = blend_ranking_attribution_lift(
        all_time_lift=0.35,
        all_time_tier="moderate",
        all_time_den=30,
        recent_lift=0.82,
        recent_tier="strong",
        recent_den=45,
    )
    assert r > 0.35
    assert r > 0.5


def test_recency_weight_favors_newer_outcomes():
    ref = datetime(2026, 6, 30, 23, 59, 59, tzinfo=timezone.utc)
    w_new = recency_weight(
        datetime(2026, 6, 29, 12, 0, tzinfo=timezone.utc),
        reference_end_utc=ref,
        half_life_days=45.0,
    )
    w_old = recency_weight(
        datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
        reference_end_utc=ref,
        half_life_days=45.0,
    )
    assert w_new > w_old


def test_low_recent_cohort_ranking_matches_all_time_lift(tmp_path: Path):
    """Sparse recent approvals → recent tier insufficient → ranking uses all-time lift."""
    store = OperationalHistoryStore(settings=_settings(tmp_path))
    as_of_d = date(2026, 3, 15)
    end = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
    cohort_start = end - timedelta(days=30)
    store.append_event(
        "listing_synced",
        source="sync",
        payload={"listing_count": 1},
        occurred_at_utc=cohort_start,
    )
    store.append_event(
        "queue_approved",
        source="q",
        listing_id="A",
        payload={"proposed_action_type": ProposedActionType.UPDATE_TITLE.value},
        occurred_at_utc=cohort_start + timedelta(days=1),
    )
    dash = summarize_action_effectiveness(
        store,
        as_of=as_of_d,
        attribution_window_days=90,
        cohort_lookback_days=180,
        recent_cohort_days=90,
    )
    title_row = next(x for x in dash.summaries if x.action_key == ProposedActionType.UPDATE_TITLE.value)
    assert title_row.recent_cohort_actions_count <= 1
    assert title_row.ranking_attribution_lift_score == pytest.approx(
        title_row.attribution_lift_score, abs=1e-5
    )
