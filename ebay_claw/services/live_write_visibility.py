"""
Read-only visibility for live guarded writes (title + safe specifics) — audit JSONL rollups.

Does not invoke apply, executor, or policy; does not change permissions.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from ebay_claw.config.settings import Settings, get_settings
from ebay_claw.models.domain import ProposedActionType
from ebay_claw.models.live_write_visibility import (
    EXPANSION_REASON_FAILURES_EXCEED_PRIOR_WINDOW,
    EXPANSION_REASON_INSUFFICIENT_LIVE_ATTEMPTS,
    EXPANSION_REASON_MIXED_SIGNALS_MIDDLE_BAND,
    EXPANSION_REASON_READY_ADEQUATE_SIGNAL,
    EXPANSION_REASON_SUCCESS_RATE_BELOW_ADVISORY_FLOOR,
    EXPANSION_REASON_SUCCESS_RATE_BELOW_CRITICAL,
    EXPANSION_REASON_TRACKED_BUCKET_JUMP,
    LiveTitleWriteHistoryRow,
    LiveWriteBucketTrend,
    LiveWriteExpansionAdvisory,
    LiveWriteExpansionAdvisoryPolicy,
    LiveWriteFailureMessageCount,
    LiveWriteMetricDelta,
    LiveWriteOperationsMetrics,
    LiveWriteOperationsSnapshot,
    LiveWriteOtherBucketDiagnostics,
    LiveWriteTrendsSection,
    LiveWriteWindowBlock,
    TrendDirection,
)

UPDATE_TITLE = ProposedActionType.UPDATE_TITLE.value
UPDATE_SAFE_SPECIFICS = ProposedActionType.UPDATE_SAFE_SPECIFICS.value

LIVE_GUARDED_WRITE_ACTIONS = frozenset({UPDATE_TITLE, UPDATE_SAFE_SPECIFICS})

TREND_BUCKETS = ("title_drift", "auth_failure", "provider_failure", "missing_identifier")

# Order for picking the dominant not_ready driver (first match wins).
_PRIMARY_NOT_READY_REASON_ORDER = (
    EXPANSION_REASON_SUCCESS_RATE_BELOW_CRITICAL,
    EXPANSION_REASON_TRACKED_BUCKET_JUMP,
    EXPANSION_REASON_FAILURES_EXCEED_PRIOR_WINDOW,
    EXPANSION_REASON_SUCCESS_RATE_BELOW_ADVISORY_FLOOR,
)


def live_write_expansion_policy_from_settings(s: Settings) -> LiveWriteExpansionAdvisoryPolicy:
    """Map Settings fields to the advisory policy model (single source for dashboard + scoring)."""
    return LiveWriteExpansionAdvisoryPolicy(
        min_attempts_for_readiness=s.live_write_expansion_min_attempts_readiness,
        min_attempts_for_rate_evaluation=s.live_write_expansion_min_attempts_rate_eval,
        success_rate_critical_below=s.live_write_expansion_success_rate_critical_below,
        success_rate_advisory_floor=s.live_write_expansion_success_rate_advisory_floor,
        success_rate_ready_floor=s.live_write_expansion_success_rate_ready_floor,
        bucket_jump_min_current_7d=s.live_write_expansion_bucket_jump_min_current_7d,
        bucket_jump_min_delta=s.live_write_expansion_bucket_jump_min_delta,
        prior_window_min_attempts=s.live_write_expansion_prior_window_min_attempts,
    )


def _pick_primary_not_ready(codes: List[str]) -> Optional[str]:
    for key in _PRIMARY_NOT_READY_REASON_ORDER:
        if key in codes:
            return key
    return codes[0] if codes else None


def _dedupe_codes_preserve_order(codes: List[str]) -> List[str]:
    out: List[str] = []
    for c in codes:
        if c not in out:
            out.append(c)
    return out

METRIC_DELTA_FIELDS: Tuple[Tuple[str, str], ...] = (
    ("live_write_attempts", "attempts"),
    ("live_write_successes", "successes"),
    ("live_write_failures", "failures"),
    ("blocked_applies", "blocked_applies"),
    ("retryable_failures", "retryable_failures"),
    ("non_retryable_failures", "non_retryable_failures"),
)

EXECUTOR_BUCKET_KEYS = (
    "unsupported_action",
    "identity_mismatch",
    "missing_identifier",
    "policy_failure",
    "auth_failure",
    "provider_failure",
    "retryable_transport",
    "title_drift",
    "idempotency_duplicate",
    "other",
)


def _parse_audit_line(line: str) -> Optional[dict[str, Any]]:
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    if "event" in obj and isinstance(obj["event"], dict):
        return obj["event"]
    return obj


def iter_audit_event_dicts(settings: Optional[Settings] = None) -> Iterator[dict[str, Any]]:
    """Yield audit event dicts from active + rotated JSONL (order not guaranteed)."""
    s = settings or get_settings()
    base = s.audit_log_path
    if not base.parent.exists():
        return
    candidates = list(base.parent.glob(f"{base.stem}*{base.suffix}"))
    for path in sorted(candidates, key=lambda p: p.stat().st_mtime if p.exists() else 0):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            ev = _parse_audit_line(line)
            if ev:
                yield ev


def _meta(ev: dict) -> dict:
    m = ev.get("redacted_meta")
    return dict(m) if isinstance(m, dict) else {}


def _end_exclusive_utc(as_of: date) -> datetime:
    """Start of the day after ``as_of`` (UTC) — exclusive upper bound for windows."""
    nxt = as_of + timedelta(days=1)
    return datetime(nxt.year, nxt.month, nxt.day, tzinfo=timezone.utc)


def _direction(curr: int, prev: int) -> TrendDirection:
    if curr > prev:
        return "rising"
    if curr < prev:
        return "falling"
    return "flat"


def _ts(ev: dict) -> datetime:
    raw = ev.get("timestamp_utc")
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.min.replace(tzinfo=timezone.utc)


def _is_guarded_live_write_action(ev: dict) -> bool:
    pat = _meta(ev).get("proposed_action_type")
    return pat in LIVE_GUARDED_WRITE_ACTIONS


def categorize_executor_failure(meta: dict, reason_codes: List[str]) -> str:
    """
    Map apply_simulated_failure / executor meta to a stable bucket (live guarded writes).
    """
    m = dict(meta)
    msg = (reason_codes[0] if reason_codes else "").lower()
    err = (m.get("error") or m.get("response_preview") or "").lower()
    if m.get("unsupported_action"):
        return "unsupported_action"
    if m.get("validation") == "safe_specifics_merge_conflict":
        return "policy_failure"
    if m.get("validation") == "safe_specifics_patch":
        return "policy_failure"
    if m.get("validation") == "inventory_title_drift":
        return "title_drift"
    if m.get("missing_sku") or m.get("missing_snapshot"):
        return "missing_identifier"
    if m.get("policy") == "would_remove_condition_disclosure" or m.get("write_guard"):
        return "policy_failure"
    if m.get("fail_closed"):
        return "policy_failure"
    if "401" in err or "401" in msg or "unauthorized" in err:
        return "auth_failure"
    if "auth" in msg and "failure" in msg:
        return "auth_failure"
    phase = str(m.get("phase") or "")
    if m.get("retryable") is True:
        if phase == "get_inventory_item" or "timeout" in msg or "connection" in msg:
            return "retryable_transport"
        if m.get("api") == "put_inventory_item":
            return "retryable_transport"
        return "retryable_transport"
    if m.get("api") == "put_inventory_item" and m.get("http_status"):
        return "provider_failure"
    if phase == "get_inventory_item":
        return "provider_failure"
    if m.get("validation") == "missing_product":
        return "provider_failure"
    return "other"


def build_live_write_metrics_windowed(
    events: List[dict],
    *,
    window_start_utc: Optional[datetime] = None,
    window_end_exclusive_utc: Optional[datetime] = None,
) -> LiveWriteOperationsMetrics:
    """Aggregate live UPDATE_TITLE metrics; optional ``[start, end)`` filter by event time."""
    if window_start_utc is not None and window_end_exclusive_utc is not None:
        events = [
            e
            for e in events
            if window_start_utc <= _ts(e) < window_end_exclusive_utc
        ]
    return _accumulate_live_write_metrics(events)


def build_live_write_metrics(events: List[dict]) -> LiveWriteOperationsMetrics:
    """All-time (no time filter) live UPDATE_TITLE metrics."""
    return build_live_write_metrics_windowed(events)


def _accumulate_live_write_metrics(events: List[dict]) -> LiveWriteOperationsMetrics:
    metrics = LiveWriteOperationsMetrics()
    exec_buckets: Dict[str, int] = {k: 0 for k in EXECUTOR_BUCKET_KEYS}
    blocked_buckets: Dict[str, int] = {}

    for ev in events:
        et = ev.get("event_type")
        if et not in ("apply_simulated_success", "apply_simulated_failure", "apply_blocked"):
            continue
        if not _is_guarded_live_write_action(ev):
            continue
        meta = _meta(ev)

        if et == "apply_blocked":
            metrics.blocked_applies += 1
            cat = str(meta.get("blocker_category") or "unknown")
            blocked_buckets[cat] = blocked_buckets.get(cat, 0) + 1
            if cat == "idempotency":
                metrics.idempotency_blocks += 1
            continue

        if et == "apply_simulated_success":
            sim = meta.get("simulated", True)
            live = meta.get("live_write", not sim)
            if not live:
                continue
            metrics.live_write_attempts += 1
            metrics.live_write_successes += 1
            continue

        if et == "apply_simulated_failure":
            sim = meta.get("simulated", True)
            live = meta.get("live_write", not sim)
            if not live:
                continue
            metrics.live_write_attempts += 1
            metrics.live_write_failures += 1
            if meta.get("retryable"):
                metrics.retryable_failures += 1
            else:
                metrics.non_retryable_failures += 1
            rc = ev.get("reason_codes") or []
            if not isinstance(rc, list):
                rc = []
            bucket = categorize_executor_failure(meta, [str(x) for x in rc])
            exec_buckets[bucket] = exec_buckets.get(bucket, 0) + 1

    metrics.executor_failure_buckets = {k: exec_buckets.get(k, 0) for k in EXECUTOR_BUCKET_KEYS}
    metrics.blocked_apply_buckets = blocked_buckets
    return metrics


def _normalize_message_sample(text: str, *, max_len: int = 120) -> str:
    s = (text or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    if len(s) > max_len:
        s = s[: max_len - 1] + "…"
    return s


def collect_other_bucket_samples(
    events: List[dict],
    *,
    window_start_utc: datetime,
    window_end_exclusive_utc: datetime,
    max_samples: int = 8,
) -> Tuple[int, List[str]]:
    seen: set[str] = set()
    out: List[str] = []
    n = 0
    for ev in events:
        if _ts(ev) < window_start_utc or _ts(ev) >= window_end_exclusive_utc:
            continue
        if ev.get("event_type") != "apply_simulated_failure":
            continue
        if not _is_guarded_live_write_action(ev):
            continue
        meta = _meta(ev)
        sim = meta.get("simulated", True)
        live = meta.get("live_write", not sim)
        if not live:
            continue
        rc = ev.get("reason_codes") or []
        rc_list = [str(x) for x in rc] if isinstance(rc, list) else []
        bucket = categorize_executor_failure(meta, rc_list)
        if bucket != "other":
            continue
        n += 1
        msg = rc_list[0] if rc_list else ""
        norm = _normalize_message_sample(msg)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(norm)
        if max_samples > 0 and len(out) >= max_samples:
            break
    return n, out


def count_other_bucket_failures(
    events: List[dict],
    *,
    window_start_utc: datetime,
    window_end_exclusive_utc: datetime,
) -> int:
    """Count live executor failures categorized as ``other`` in ``[start, end)``."""
    n = 0
    for ev in events:
        if _ts(ev) < window_start_utc or _ts(ev) >= window_end_exclusive_utc:
            continue
        if ev.get("event_type") != "apply_simulated_failure":
            continue
        if not _is_guarded_live_write_action(ev):
            continue
        meta = _meta(ev)
        sim = meta.get("simulated", True)
        live = meta.get("live_write", not sim)
        if not live:
            continue
        rc = ev.get("reason_codes") or []
        rc_list = [str(x) for x in rc] if isinstance(rc, list) else []
        if categorize_executor_failure(meta, rc_list) == "other":
            n += 1
    return n


def top_failure_messages_7d(
    events: List[dict],
    *,
    window_start_utc: datetime,
    window_end_exclusive_utc: datetime,
    top_n: int = 8,
) -> List[LiveWriteFailureMessageCount]:
    counts: Dict[str, int] = {}
    for ev in events:
        if _ts(ev) < window_start_utc or _ts(ev) >= window_end_exclusive_utc:
            continue
        if ev.get("event_type") != "apply_simulated_failure":
            continue
        if not _is_guarded_live_write_action(ev):
            continue
        meta = _meta(ev)
        sim = meta.get("simulated", True)
        live = meta.get("live_write", not sim)
        if not live:
            continue
        rc = ev.get("reason_codes") or []
        rc_list = [str(x) for x in rc] if isinstance(rc, list) else []
        bucket = categorize_executor_failure(meta, rc_list)
        if bucket == "other":
            continue
        msg = _normalize_message_sample(rc_list[0] if rc_list else "")
        if not msg:
            continue
        counts[msg] = counts.get(msg, 0) + 1
    ranked = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
    return [
        LiveWriteFailureMessageCount(message=m, count=c) for m, c in ranked[:top_n]
    ]


def build_metric_deltas(
    curr: LiveWriteOperationsMetrics, prev: LiveWriteOperationsMetrics
) -> List[LiveWriteMetricDelta]:
    out: List[LiveWriteMetricDelta] = []
    for attr, label in METRIC_DELTA_FIELDS:
        a = int(getattr(curr, attr))
        b = int(getattr(prev, attr))
        d = a - b
        out.append(
            LiveWriteMetricDelta(
                metric=label,
                current_7d=a,
                previous_7d=b,
                delta=d,
                direction=_direction(a, b),
            )
        )
    return out


def build_bucket_trends(
    curr: LiveWriteOperationsMetrics, prev: LiveWriteOperationsMetrics
) -> List[LiveWriteBucketTrend]:
    out: List[LiveWriteBucketTrend] = []
    for b in TREND_BUCKETS:
        c = int(curr.executor_failure_buckets.get(b, 0))
        p = int(prev.executor_failure_buckets.get(b, 0))
        out.append(
            LiveWriteBucketTrend(
                bucket=b,
                current_7d=c,
                previous_7d=p,
                delta=c - p,
                direction=_direction(c, p),
            )
        )
    return out


def compute_expansion_advisory(
    *,
    m7: LiveWriteOperationsMetrics,
    p7: LiveWriteOperationsMetrics,
    bucket_trends: Sequence[LiveWriteBucketTrend],
    policy: Optional[LiveWriteExpansionAdvisoryPolicy] = None,
) -> LiveWriteExpansionAdvisory:
    """
    Readiness hint from configurable policy — read-only; does not change apply or policy gates.
    """
    p = policy or LiveWriteExpansionAdvisoryPolicy()
    att = m7.live_write_attempts
    succ = m7.live_write_successes
    fail = m7.live_write_failures

    if att < p.min_attempts_for_readiness:
        return LiveWriteExpansionAdvisory(
            readiness="insufficient_data",
            summary="Not enough live title activity in the last 7 days to judge stability.",
            reasons=[
                f"Need at least {p.min_attempts_for_readiness} live attempts in the rolling 7d window "
                "for a minimal signal (insufficient_data threshold)."
            ],
            reason_codes=[EXPANSION_REASON_INSUFFICIENT_LIVE_ATTEMPTS],
            primary_reason_code=EXPANSION_REASON_INSUFFICIENT_LIVE_ATTEMPTS,
        )

    succ_rate = succ / max(1, att)
    reasons: List[str] = []
    codes: List[str] = []

    if att >= p.min_attempts_for_rate_evaluation and succ_rate < p.success_rate_critical_below:
        codes.append(EXPANSION_REASON_SUCCESS_RATE_BELOW_CRITICAL)
        reasons.append(
            f"Success rate {succ_rate:.0%} is below {p.success_rate_critical_below:.0%} "
            f"with {att} attempts (minimum for rate evaluation: {p.min_attempts_for_rate_evaluation})."
        )

    if (
        att >= p.min_attempts_for_readiness
        and fail > p7.live_write_failures
        and p7.live_write_attempts >= p.prior_window_min_attempts
    ):
        codes.append(EXPANSION_REASON_FAILURES_EXCEED_PRIOR_WINDOW)
        reasons.append(
            f"Failures in the last 7d ({fail}) exceed the prior 7d ({p7.live_write_failures}); "
            f"prior window had at least {p.prior_window_min_attempts} attempt(s) for comparison."
        )

    for t in bucket_trends:
        if t.bucket not in TREND_BUCKETS:
            continue
        if t.current_7d >= p.bucket_jump_min_current_7d and t.delta >= p.bucket_jump_min_delta:
            codes.append(EXPANSION_REASON_TRACKED_BUCKET_JUMP)
            reasons.append(
                f"{t.bucket} failures rose from {t.previous_7d} to {t.current_7d} "
                f"(jump threshold: current≥{p.bucket_jump_min_current_7d}, "
                f"Δ≥{p.bucket_jump_min_delta})."
            )

    if att >= p.min_attempts_for_rate_evaluation and succ_rate < p.success_rate_advisory_floor:
        codes.append(EXPANSION_REASON_SUCCESS_RATE_BELOW_ADVISORY_FLOOR)
        reasons.append(
            f"Success rate {succ_rate:.0%} is under the {p.success_rate_advisory_floor:.0%} advisory floor "
            f"(minimum attempts: {p.min_attempts_for_rate_evaluation})."
        )

    u_codes = _dedupe_codes_preserve_order(codes)
    if reasons:
        return LiveWriteExpansionAdvisory(
            readiness="not_ready",
            summary="Hold off on adding a second live action until trends improve.",
            reasons=reasons,
            reason_codes=u_codes,
            primary_reason_code=_pick_primary_not_ready(u_codes),
        )

    if att >= p.min_attempts_for_rate_evaluation and succ_rate >= p.success_rate_ready_floor:
        return LiveWriteExpansionAdvisory(
            readiness="ready",
            summary="Live title updates show adequate volume and success rate for cautious expansion review.",
            reasons=[
                f"{att} attempts in 7d with {succ_rate:.0%} success (ready floor "
                f"{p.success_rate_ready_floor:.0%}) — review still required before enabling new writes."
            ],
            reason_codes=[EXPANSION_REASON_READY_ADEQUATE_SIGNAL],
            primary_reason_code=EXPANSION_REASON_READY_ADEQUATE_SIGNAL,
        )

    return LiveWriteExpansionAdvisory(
        readiness="not_ready",
        summary="Mixed signals — keep monitoring before expanding live writes.",
        reasons=[
            "Volume or success rate is in a middle band; collect more 7d runs or improve stability first."
        ],
        reason_codes=[EXPANSION_REASON_MIXED_SIGNALS_MIDDLE_BAND],
        primary_reason_code=EXPANSION_REASON_MIXED_SIGNALS_MIDDLE_BAND,
    )


def build_trends_section(
    events: List[dict],
    *,
    as_of: date,
    policy: LiveWriteExpansionAdvisoryPolicy,
) -> LiveWriteTrendsSection:
    end_excl = _end_exclusive_utc(as_of)
    w7_start = end_excl - timedelta(days=7)
    w14_start = end_excl - timedelta(days=14)
    w30_start = end_excl - timedelta(days=30)

    m7 = build_live_write_metrics_windowed(events, window_start_utc=w7_start, window_end_exclusive_utc=end_excl)
    p7 = build_live_write_metrics_windowed(
        events, window_start_utc=w14_start, window_end_exclusive_utc=w7_start
    )
    m30 = build_live_write_metrics_windowed(
        events, window_start_utc=w30_start, window_end_exclusive_utc=end_excl
    )

    deltas = build_metric_deltas(m7, p7)
    b_trends = build_bucket_trends(m7, p7)
    top_msgs = top_failure_messages_7d(events, window_start_utc=w7_start, window_end_exclusive_utc=end_excl)
    other_curr, other_samples = collect_other_bucket_samples(
        events,
        window_start_utc=w7_start,
        window_end_exclusive_utc=end_excl,
    )
    other_prev_n = count_other_bucket_failures(
        events,
        window_start_utc=w14_start,
        window_end_exclusive_utc=w7_start,
    )

    advisory = compute_expansion_advisory(m7=m7, p7=p7, bucket_trends=b_trends, policy=policy)

    return LiveWriteTrendsSection(
        as_of=as_of.isoformat(),
        last_7d=LiveWriteWindowBlock(
            period_start_utc=w7_start.isoformat(),
            period_end_utc=end_excl.isoformat(),
            window_days=7,
            metrics=m7,
        ),
        previous_7d=LiveWriteWindowBlock(
            period_start_utc=w14_start.isoformat(),
            period_end_utc=w7_start.isoformat(),
            window_days=7,
            metrics=p7,
        ),
        last_30d=LiveWriteWindowBlock(
            period_start_utc=w30_start.isoformat(),
            period_end_utc=end_excl.isoformat(),
            window_days=30,
            metrics=m30,
        ),
        seven_day_vs_prior=deltas,
        failure_bucket_trends=b_trends,
        top_failure_messages_7d=top_msgs,
        top_blocked_categories_7d=dict(m7.blocked_apply_buckets),
        other_bucket=LiveWriteOtherBucketDiagnostics(
            current_7d_count=other_curr,
            previous_7d_count=other_prev_n,
            sampled_normalized_messages=other_samples,
        ),
        expansion_advisory=advisory,
    )


def build_recent_live_title_history(
    events: List[dict],
    *,
    limit: int = 50,
) -> List[LiveTitleWriteHistoryRow]:
    rows: List[LiveTitleWriteHistoryRow] = []
    for ev in sorted(events, key=_ts, reverse=True):
        et = ev.get("event_type")
        if et not in ("apply_simulated_success", "apply_simulated_failure"):
            continue
        if not _is_guarded_live_write_action(ev):
            continue
        meta = _meta(ev)
        sim = meta.get("simulated", True)
        live = meta.get("live_write", not sim)
        if not live:
            continue
        rc = ev.get("reason_codes") or []
        rc_list = [str(x) for x in rc] if isinstance(rc, list) else []
        msg = rc_list[0] if rc_list else ""
        if et == "apply_simulated_success":
            msg = str(meta.get("executor_message") or "ok")
        sku = meta.get("target_sku")
        if sku is None:
            snap = ev.get("snapshot_before")
            if isinstance(snap, dict):
                sku = snap.get("sku")
        fr = None
        if et == "apply_simulated_failure":
            fr = categorize_executor_failure(meta, rc_list)
        pat = str(meta.get("proposed_action_type") or UPDATE_TITLE)
        rows.append(
            LiveTitleWriteHistoryRow(
                timestamp_utc=_ts(ev).isoformat(),
                event_type=str(et),
                proposed_action_type=pat,
                listing_id=ev.get("listing_id"),
                sku=str(sku) if sku is not None else None,
                success=et == "apply_simulated_success",
                retryable=bool(meta.get("retryable")),
                user_safe_message=msg[:500],
                correlation_id=meta.get("correlation_id"),
                external_request_id=meta.get("external_request_id"),
                failure_reason=fr,
            )
        )
        if len(rows) >= limit:
            break
    return rows


def load_live_write_operations_snapshot(
    *,
    settings: Optional[Settings] = None,
    recent_limit: int = 50,
    as_of: Optional[date] = None,
    include_trends: bool = True,
) -> LiveWriteOperationsSnapshot:
    s = settings or get_settings()
    events = list(iter_audit_event_dicts(s))
    metrics = build_live_write_metrics(events)
    recent = build_recent_live_title_history(events, limit=recent_limit)
    day = as_of or date.today()
    expansion_policy = live_write_expansion_policy_from_settings(s)
    trends = (
        build_trends_section(events, as_of=day, policy=expansion_policy)
        if include_trends
        else None
    )
    note = (
        "Counts use audit events for live guarded writes (update_title, update_safe_specifics). "
        "Live writes are rows where simulated=false (or live_write=true in meta). "
        "Expansion advisory thresholds come from EBAY_CLAW_LIVE_WRITE_EXPANSION_* settings."
    )
    return LiveWriteOperationsSnapshot(
        metrics=metrics,
        recent_live_title_writes=recent,
        trends=trends,
        note=note,
        expansion_advisory_policy=expansion_policy,
    )
