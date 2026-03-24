"""Live write visibility — audit rollups only; no apply path changes."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from ebay_claw.models.domain import ProposedActionType
from ebay_claw.models.live_write_visibility import (
    EXPANSION_REASON_FAILURES_EXCEED_PRIOR_WINDOW,
    EXPANSION_REASON_INSUFFICIENT_LIVE_ATTEMPTS,
    EXPANSION_REASON_MIXED_SIGNALS_MIDDLE_BAND,
    EXPANSION_REASON_READY_ADEQUATE_SIGNAL,
    EXPANSION_REASON_SUCCESS_RATE_BELOW_ADVISORY_FLOOR,
    EXPANSION_REASON_SUCCESS_RATE_BELOW_CRITICAL,
    EXPANSION_REASON_TRACKED_BUCKET_JUMP,
    LiveWriteExpansionAdvisoryPolicy,
)
from ebay_claw.services.live_write_visibility import (
    build_live_write_metrics,
    build_recent_live_title_history,
    build_trends_section,
    categorize_executor_failure,
    collect_other_bucket_samples,
    compute_expansion_advisory,
    count_other_bucket_failures,
    iter_audit_event_dicts,
)


def _ev(
    event_type: str,
    *,
    meta: dict,
    listing_id: str = "L1",
    ts: str = "2026-06-01T12:00:00+00:00",
    reason_codes: list | None = None,
) -> dict:
    return {
        "event_id": "e1",
        "event_type": event_type,
        "timestamp_utc": ts,
        "listing_id": listing_id,
        "review_item_id": "q1",
        "reason_codes": reason_codes or ["x"],
        "redacted_meta": {
            **meta,
            "proposed_action_type": ProposedActionType.UPDATE_TITLE.value,
        },
        "snapshot_before": {"sku": "SKU-1"},
    }


def test_rollup_live_attempts_success_failure_blocked_idempotency():
    events = [
        _ev(
            "apply_simulated_success",
            meta={
                "simulated": False,
                "live_write": True,
                "executor_message": "ok",
            },
        ),
        _ev(
            "apply_simulated_failure",
            meta={
                "simulated": False,
                "live_write": True,
                "retryable": False,
                "unsupported_action": True,
            },
            reason_codes=["not supported"],
            ts="2026-06-01T13:00:00+00:00",
        ),
        _ev(
            "apply_blocked",
            meta={"blocker_category": "idempotency"},
            ts="2026-06-01T14:00:00+00:00",
        ),
        _ev(
            "apply_blocked",
            meta={"blocker_category": "identity"},
            ts="2026-06-01T15:00:00+00:00",
        ),
    ]
    m = build_live_write_metrics(events)
    assert m.live_write_attempts == 2
    assert m.live_write_successes == 1
    assert m.live_write_failures == 1
    assert m.blocked_applies == 2
    assert m.idempotency_blocks == 1
    assert m.retryable_failures == 0
    assert m.non_retryable_failures == 1
    assert m.executor_failure_buckets.get("unsupported_action") == 1
    assert m.blocked_apply_buckets.get("idempotency") == 1
    assert m.blocked_apply_buckets.get("identity") == 1


def test_simulated_writes_excluded_from_live_metrics():
    events = [
        _ev(
            "apply_simulated_success",
            meta={"simulated": True, "live_write": False},
        ),
    ]
    m = build_live_write_metrics(events)
    assert m.live_write_attempts == 0


def test_categorize_executor_failure_buckets():
    assert (
        categorize_executor_failure({"unsupported_action": True}, []) == "unsupported_action"
    )
    assert (
        categorize_executor_failure({"validation": "inventory_title_drift"}, [])
        == "title_drift"
    )
    assert categorize_executor_failure({"missing_sku": True}, []) == "missing_identifier"
    assert categorize_executor_failure({"policy": "would_remove_condition_disclosure"}, []) == "policy_failure"
    assert (
        categorize_executor_failure({"error": "HTTP 401 unauthorized"}, [])
        == "auth_failure"
    )
    assert (
        categorize_executor_failure(
            {"retryable": True, "phase": "get_inventory_item"},
            [],
        )
        == "retryable_transport"
    )
    assert (
        categorize_executor_failure(
            {"api": "put_inventory_item", "http_status": 400, "retryable": False},
            [],
        )
        == "provider_failure"
    )


def test_recent_history_format_newest_first():
    events = [
        _ev(
            "apply_simulated_success",
            meta={
                "simulated": False,
                "live_write": True,
                "executor_message": "Title updated",
                "correlation_id": "cid-1",
                "target_sku": "SKU-9",
            },
            ts="2026-06-01T10:00:00+00:00",
        ),
        _ev(
            "apply_simulated_failure",
            meta={
                "simulated": False,
                "live_write": True,
                "retryable": True,
                "phase": "get_inventory_item",
                "correlation_id": "cid-2",
                "external_request_id": "req-2",
            },
            reason_codes=["transport"],
            ts="2026-06-01T11:00:00+00:00",
        ),
    ]
    rows = build_recent_live_title_history(events, limit=10)
    assert len(rows) == 2
    assert rows[0].success is False
    assert rows[0].retryable is True
    assert rows[0].failure_reason == "retryable_transport"
    assert rows[0].correlation_id == "cid-2"
    assert rows[0].external_request_id == "req-2"
    assert rows[1].success is True
    assert rows[1].sku == "SKU-9"


def test_iter_audit_event_dicts_reads_wrapped_and_plain(tmp_path: Path):
    p = tmp_path / "audit.jsonl"
    plain = {
        "event_type": "apply_blocked",
        "timestamp_utc": "2026-01-01T00:00:00+00:00",
        "redacted_meta": {
            "proposed_action_type": ProposedActionType.UPDATE_TITLE.value,
            "blocker_category": "policy",
        },
    }
    wrapped = {"event": plain}
    p.write_text(json.dumps(wrapped) + "\n" + json.dumps(plain) + "\n", encoding="utf-8")
    from ebay_claw.config.settings import Settings
    from ebay_claw.models.runtime_mode import ClawRuntimeMode

    s = Settings(
        runtime_mode=ClawRuntimeMode.FIXTURE,
        audit_log_path=p,
        operational_history_path=tmp_path / "op.jsonl",
        review_queue_path=tmp_path / "q.json",
        sync_state_path=tmp_path / "ss.json",
        sync_history_path=tmp_path / "sh.jsonl",
        fixture_path=Path("fixtures/sample_listings.json"),
    )
    evs = list(iter_audit_event_dicts(s))
    assert len(evs) == 2


def test_trend_windows_split_current_and_previous_7d():
    """Events land in last 7d vs prior 7d relative to as_of."""
    as_of = date(2026, 6, 20)
    # last 7d: [2026-06-14 00:00 UTC, 2026-06-21 00:00)
    events = [
        _ev(
            "apply_simulated_success",
            meta={"simulated": False, "live_write": True},
            ts="2026-06-18T12:00:00+00:00",
        ),
        _ev(
            "apply_simulated_success",
            meta={"simulated": False, "live_write": True},
            ts="2026-06-10T12:00:00+00:00",
        ),
    ]
    tr = build_trends_section(events, as_of=as_of, policy=LiveWriteExpansionAdvisoryPolicy())
    assert tr.last_7d.metrics.live_write_attempts == 1
    assert tr.previous_7d.metrics.live_write_attempts == 1
    assert tr.last_30d.metrics.live_write_attempts == 2
    deltas = {d.metric: d for d in tr.seven_day_vs_prior}
    assert deltas["attempts"].current_7d == 1
    assert deltas["attempts"].previous_7d == 1
    assert deltas["attempts"].delta == 0


def test_seven_day_vs_prior_comparison_direction():
    as_of = date(2026, 6, 20)
    events = [
        _ev(
            "apply_simulated_failure",
            meta={
                "simulated": False,
                "live_write": True,
                "retryable": False,
                "validation": "inventory_title_drift",
            },
            reason_codes=["drift"],
            ts="2026-06-18T12:00:00+00:00",
        ),
        _ev(
            "apply_simulated_failure",
            meta={
                "simulated": False,
                "live_write": True,
                "retryable": False,
                "validation": "inventory_title_drift",
            },
            reason_codes=["drift"],
            ts="2026-06-10T12:00:00+00:00",
        ),
    ]
    tr = build_trends_section(events, as_of=as_of, policy=LiveWriteExpansionAdvisoryPolicy())
    td = next(x for x in tr.failure_bucket_trends if x.bucket == "title_drift")
    assert td.current_7d == 1
    assert td.previous_7d == 1
    assert td.direction == "flat"


def test_other_bucket_sampling_safe_and_deduped():
    as_of = date(2026, 6, 20)
    events = [
        _ev(
            "apply_simulated_failure",
            meta={"simulated": False, "live_write": True, "retryable": False},
            reason_codes=["  Mystery X  "],
            ts="2026-06-18T12:00:00+00:00",
        ),
        _ev(
            "apply_simulated_failure",
            meta={"simulated": False, "live_write": True, "retryable": False},
            reason_codes=["  Mystery X  "],
            ts="2026-06-18T13:00:00+00:00",
        ),
    ]
    tr = build_trends_section(events, as_of=as_of, policy=LiveWriteExpansionAdvisoryPolicy())
    assert tr.other_bucket.current_7d_count == 2
    assert len(tr.other_bucket.sampled_normalized_messages) == 1
    assert "mystery x" in tr.other_bucket.sampled_normalized_messages[0]


def test_count_other_bucket_matches_collect():
    as_of = date(2026, 6, 20)
    end_excl = datetime(2026, 6, 21, 0, 0, tzinfo=timezone.utc)
    w7 = end_excl - timedelta(days=7)
    events = [
        _ev(
            "apply_simulated_failure",
            meta={"simulated": False, "live_write": True, "retryable": False},
            reason_codes=["x"],
            ts="2026-06-18T12:00:00+00:00",
        ),
    ]
    n, samples = collect_other_bucket_samples(
        events,
        window_start_utc=w7,
        window_end_exclusive_utc=end_excl,
    )
    assert n == count_other_bucket_failures(
        events,
        window_start_utc=w7,
        window_end_exclusive_utc=end_excl,
    )
    assert n >= len(samples)


def test_expansion_advisory_insufficient_and_ready():
    from ebay_claw.models.live_write_visibility import (
        LiveWriteBucketTrend,
        LiveWriteOperationsMetrics,
    )

    m0 = LiveWriteOperationsMetrics()
    p0 = LiveWriteOperationsMetrics()
    empty_trends: list[LiveWriteBucketTrend] = []
    for b in ("title_drift", "auth_failure", "provider_failure", "missing_identifier"):
        empty_trends.append(
            LiveWriteBucketTrend(
                bucket=b, current_7d=0, previous_7d=0, delta=0, direction="flat"
            )
        )
    a0 = compute_expansion_advisory(m7=m0, p7=p0, bucket_trends=empty_trends)
    assert a0.readiness == "insufficient_data"
    assert a0.reason_codes == [EXPANSION_REASON_INSUFFICIENT_LIVE_ATTEMPTS]
    assert a0.primary_reason_code == EXPANSION_REASON_INSUFFICIENT_LIVE_ATTEMPTS

    m_ok = LiveWriteOperationsMetrics(
        live_write_attempts=6,
        live_write_successes=5,
        live_write_failures=1,
    )
    a_ok = compute_expansion_advisory(m7=m_ok, p7=p0, bucket_trends=empty_trends)
    assert a_ok.readiness == "ready"
    assert EXPANSION_REASON_READY_ADEQUATE_SIGNAL in a_ok.reason_codes
    assert a_ok.primary_reason_code == EXPANSION_REASON_READY_ADEQUATE_SIGNAL


def test_expansion_advisory_configurable_thresholds_change_outcome():
    from ebay_claw.models.live_write_visibility import (
        LiveWriteBucketTrend,
        LiveWriteOperationsMetrics,
    )

    m = LiveWriteOperationsMetrics(
        live_write_attempts=6,
        live_write_successes=5,
        live_write_failures=1,
    )
    p0 = LiveWriteOperationsMetrics()
    flat = LiveWriteBucketTrend(
        bucket="title_drift", current_7d=0, previous_7d=0, delta=0, direction="flat"
    )
    trends = [
        LiveWriteBucketTrend(
            bucket=b, current_7d=0, previous_7d=0, delta=0, direction="flat"
        )
        for b in ("auth_failure", "provider_failure", "missing_identifier")
    ]
    trends.insert(0, flat)

    strict = LiveWriteExpansionAdvisoryPolicy(success_rate_ready_floor=0.99)
    a_strict = compute_expansion_advisory(m7=m, p7=p0, bucket_trends=trends, policy=strict)
    assert a_strict.readiness == "not_ready"
    assert a_strict.primary_reason_code == EXPANSION_REASON_MIXED_SIGNALS_MIDDLE_BAND

    loose = LiveWriteExpansionAdvisoryPolicy(
        min_attempts_for_rate_evaluation=3,
        success_rate_ready_floor=0.5,
    )
    m_small = LiveWriteOperationsMetrics(
        live_write_attempts=4,
        live_write_successes=3,
        live_write_failures=1,
    )
    a_loose = compute_expansion_advisory(m7=m_small, p7=p0, bucket_trends=trends, policy=loose)
    assert a_loose.readiness == "ready"


def test_expansion_advisory_reason_codes_match_conditions():
    from ebay_claw.models.live_write_visibility import LiveWriteBucketTrend, LiveWriteOperationsMetrics

    p0 = LiveWriteOperationsMetrics(live_write_attempts=2, live_write_failures=0)
    m_fail = LiveWriteOperationsMetrics(
        live_write_attempts=6,
        live_write_successes=2,
        live_write_failures=4,
    )
    flat = [
        LiveWriteBucketTrend(
            bucket=b, current_7d=0, previous_7d=0, delta=0, direction="flat"
        )
        for b in ("title_drift", "auth_failure", "provider_failure", "missing_identifier")
    ]
    a_rate = compute_expansion_advisory(m7=m_fail, p7=p0, bucket_trends=flat)
    assert EXPANSION_REASON_SUCCESS_RATE_BELOW_CRITICAL in a_rate.reason_codes
    assert EXPANSION_REASON_SUCCESS_RATE_BELOW_ADVISORY_FLOOR in a_rate.reason_codes
    assert a_rate.primary_reason_code == EXPANSION_REASON_SUCCESS_RATE_BELOW_CRITICAL

    m_prior = LiveWriteOperationsMetrics(
        live_write_attempts=6,
        live_write_successes=4,
        live_write_failures=2,
    )
    p_high = LiveWriteOperationsMetrics(live_write_attempts=2, live_write_failures=1)
    a_prior = compute_expansion_advisory(m7=m_prior, p7=p_high, bucket_trends=flat)
    assert EXPANSION_REASON_FAILURES_EXCEED_PRIOR_WINDOW in a_prior.reason_codes

    m_clean = LiveWriteOperationsMetrics(
        live_write_attempts=6,
        live_write_successes=6,
        live_write_failures=0,
    )
    jump = [
        LiveWriteBucketTrend(
            bucket="title_drift", current_7d=2, previous_7d=0, delta=2, direction="rising"
        ),
        *flat[1:],
    ]
    a_jump = compute_expansion_advisory(m7=m_clean, p7=p0, bucket_trends=jump)
    assert EXPANSION_REASON_TRACKED_BUCKET_JUMP in a_jump.reason_codes
    assert a_jump.primary_reason_code == EXPANSION_REASON_TRACKED_BUCKET_JUMP


def test_load_live_write_snapshot_includes_policy_from_settings(tmp_path: Path):
    from ebay_claw.config.settings import Settings
    from ebay_claw.models.runtime_mode import ClawRuntimeMode
    from ebay_claw.services.live_write_visibility import load_live_write_operations_snapshot

    audit = tmp_path / "audit.jsonl"
    audit.write_text("", encoding="utf-8")
    s = Settings(
        runtime_mode=ClawRuntimeMode.FIXTURE,
        audit_log_path=audit,
        operational_history_path=tmp_path / "op.jsonl",
        review_queue_path=tmp_path / "q.json",
        sync_state_path=tmp_path / "ss.json",
        sync_history_path=tmp_path / "sh.jsonl",
        fixture_path=Path("fixtures/sample_listings.json"),
        live_write_expansion_min_attempts_readiness=9,
        live_write_expansion_bucket_jump_min_delta=3,
    )
    snap = load_live_write_operations_snapshot(settings=s, include_trends=True)
    assert snap.expansion_advisory_policy.min_attempts_for_readiness == 9
    assert snap.expansion_advisory_policy.bucket_jump_min_delta == 3
    assert snap.trends is not None
    assert snap.trends.expansion_advisory.readiness == "insufficient_data"


def test_dashboard_style_escape_for_advisory_strings():
    import html

    malicious = "<script>x</script>"
    assert "<script>" not in html.escape(malicious)
    assert "&lt;script&gt;" in html.escape(malicious)
