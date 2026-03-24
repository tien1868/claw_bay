"""Store-level velocity — event-based when operational history has coverage; else documented estimates."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, List, Optional

from ebay_claw.analytics.inventory_analyst import InventoryAnalyst
from ebay_claw.analytics.strategy_scoring import StrategyScorer
from ebay_claw.models.domain import ListingRecord
from ebay_claw.models.recovery import VelocityMetrics, VelocityWeekRollup
from ebay_claw.services.operational_history_store import OperationalHistoryStore

if TYPE_CHECKING:
    from ebay_claw.config.settings import Settings


def _day_end(as_of: date) -> datetime:
    return datetime(as_of.year, as_of.month, as_of.day, 23, 59, 59, tzinfo=timezone.utc)


def compute_velocity_metrics(
    listings: List[ListingRecord],
    *,
    as_of: Optional[date] = None,
    settings: Optional["Settings"] = None,
    history_store: Optional[OperationalHistoryStore] = None,
) -> VelocityMetrics:
    from ebay_claw.config.settings import get_settings

    today = as_of or date.today()
    analyst = InventoryAnalyst(settings=settings or get_settings())
    scorer = StrategyScorer(settings=settings or get_settings())
    s = settings or get_settings()
    store = history_store or OperationalHistoryStore(settings=s)

    if not listings:
        return VelocityMetrics(
            as_of=today,
            computed_at=datetime.now(),
            trend_notes=["No listings loaded."],
            metric_sources={},
            historical_coverage_ok=False,
        )

    end = _day_end(today)
    week_start_dt = end - timedelta(days=7)
    week_start_d = week_start_dt.date()
    prior_week_start_d = (end - timedelta(days=14)).date()
    win30_start = end - timedelta(days=30)

    # --- Estimated baselines (listing snapshot fields) ---
    created_last_7d_est = 0
    created_prior_7d_est = 0
    for lst in listings:
        lo = lst.listed_on
        if lo is None:
            continue
        if week_start_d < lo <= today:
            created_last_7d_est += 1
        elif prior_week_start_d < lo <= week_start_d:
            created_prior_7d_est += 1

    sold_90 = sum((x.sold_quantity_last_90_days or 0) for x in listings)
    sold_est_7d = sold_90 / 90.0 * 7.0
    net_est = created_last_7d_est - sold_est_7d

    stale_count = 0
    stale_inflow_est = 0
    at_risk = 0
    intervention = 0
    for lst in listings:
        a = analyst.analyze(lst, as_of=today)
        if a.is_stale:
            stale_count += 1
        d = a.days_active
        if a.is_stale and 75 <= d <= 82:
            stale_inflow_est += 1
        sc = scorer.score(lst, a, as_of=today)
        if d >= 75 or sc.sale_likelihood_before_90_days < 0.35:
            at_risk += 1
        if d >= 60 and (len(a.weak_title_signals) >= 2 or len(a.missing_critical_fields) >= 2):
            intervention += 1

    coverage_ok = store.has_recent_sync_signal(today, lookback_days=14)

    created_ev_7d = store.count_events(
        "listing_created", since_utc=week_start_dt, until_utc=end
    )
    sold_units_ev_7d = store.sum_payload_float(
        "listing_sold",
        "units",
        since_utc=week_start_dt,
        until_utc=end,
    )
    if sold_units_ev_7d <= 0 and coverage_ok:
        sold_units_ev_7d = float(
            store.count_events("listing_sold", since_utc=week_start_dt, until_utc=end)
        )

    stale_in_ev = store.count_events(
        "stale_crossed_90d", since_utc=week_start_dt, until_utc=end
    )
    stale_out_ev = store.count_events(
        "stale_cleared", since_utc=week_start_dt, until_utc=end
    )

    listings_created_last_7d = (
        created_ev_7d if coverage_ok else created_last_7d_est
    )
    net_display = (
        float(created_ev_7d - sold_units_ev_7d) if coverage_ok else net_est
    )
    stale_inflow_display = stale_in_ev if coverage_ok else stale_inflow_est
    stale_cleared_display = stale_out_ev if coverage_ok else None

    sources: dict[str, str] = {
        "listings_created_last_7d": "event" if coverage_ok else "estimated",
        "sold_units_last_7d": "event" if coverage_ok else "estimated",
        "net_inventory_last_7d": "event" if coverage_ok else "estimated",
        "stale_inflow_last_7d": "event" if coverage_ok else "estimated",
        "stale_cleared_last_7d": "event" if coverage_ok else "estimated",
    }

    appr_30 = store.count_events(
        "queue_approved", since_utc=win30_start, until_utc=end
    )
    rej_30 = store.count_events(
        "queue_rejected", since_utc=win30_start, until_utc=end
    )
    approval_rate: Optional[float] = None
    conv_note: Optional[str] = None
    if appr_30 + rej_30 > 0:
        approval_rate = round(appr_30 / (appr_30 + rej_30), 4)
        conv_note = (
            f"Last 30d: {appr_30} approvals vs {rej_30} rejections from operational history."
        )
    elif coverage_ok:
        conv_note = "No queue approval/rejection events in the last 30d window."
    else:
        conv_note = "Queue outcome rates need operational history (sync + queue transitions)."

    notes = [
        "sold_units_estimated_last_7d uses sold_quantity_last_90_days/90*7 when events are sparse.",
        "stale_new_inflow_estimated_7d counts stale listings with age 75–82d when event inflow unavailable.",
    ]
    if coverage_ok:
        notes.insert(
            0,
            "Historical coverage OK (recent listing_synced) — velocity uses event-derived movement where noted.",
        )
    else:
        notes.insert(
            0,
            "Limited operational history — metrics default to listing-field estimates until regular syncs populate events.",
        )

    stale_cleared_note = None
    if coverage_ok:
        stale_cleared_note = "stale_cleared uses operational events (staleness flag cleared)."
    else:
        stale_cleared_note = (
            "Stale cleared not estimated from snapshots alone — populate history via sync/movement tracker."
        )

    trend_rows: List[VelocityWeekRollup] = []
    for ws, we, _cnt in store.weekly_slices(as_of=today, num_weeks=4):
        w_end = _day_end(we)
        w_start_dt = w_end - timedelta(days=6)
        sold_sum = store.sum_payload_float(
            "listing_sold", "units", since_utc=w_start_dt, until_utc=w_end
        )
        if sold_sum <= 0:
            sold_sum = float(
                store.count_events(
                    "listing_sold", since_utc=w_start_dt, until_utc=w_end
                )
            )
        c_created = store.count_events(
            "listing_created", since_utc=w_start_dt, until_utc=w_end
        )
        st_i = store.count_events(
            "stale_crossed_90d", since_utc=w_start_dt, until_utc=w_end
        )
        st_o = store.count_events(
            "stale_cleared", since_utc=w_start_dt, until_utc=w_end
        )
        qa = store.count_events(
            "queue_approved", since_utc=w_start_dt, until_utc=w_end
        )
        qr = store.count_events(
            "queue_rejected", since_utc=w_start_dt, until_utc=w_end
        )
        sync_n = store.count_events(
            "listing_synced", since_utc=w_start_dt, until_utc=w_end
        )
        dq = "event_based" if sync_n else "partial"
        if sync_n == 0 and sum([c_created, sold_sum, st_i, st_o, qa, qr]) == 0:
            dq = "estimated_only"
        trend_rows.append(
            VelocityWeekRollup(
                week_start=ws,
                week_end=we,
                listings_created=c_created,
                sold_units=round(sold_sum, 2),
                stale_inflow=st_i,
                stale_cleared=st_o,
                net_inventory_change=round(float(c_created - sold_sum), 2),
                queue_approved=qa,
                queue_rejected=qr,
                data_quality=dq,
            )
        )

    return VelocityMetrics(
        as_of=today,
        computed_at=datetime.now(),
        listings_created_last_7d=listings_created_last_7d,
        listings_created_prior_7d=created_prior_7d_est,
        sold_units_estimated_last_7d=round(sold_est_7d, 2),
        sold_units_event_last_7d=round(sold_units_ev_7d, 2) if coverage_ok else None,
        net_inventory_change_estimated_7d=round(net_est, 2),
        net_inventory_change_last_7d=round(net_display, 2),
        stale_inventory_count=stale_count,
        stale_new_inflow_estimated_7d=stale_inflow_est,
        stale_inflow_event_last_7d=stale_in_ev if coverage_ok else None,
        stale_cleared_last_7d=stale_cleared_display,
        stale_cleared_event_last_7d=stale_out_ev if coverage_ok else None,
        stale_cleared_data_note=stale_cleared_note,
        at_risk_90d_listings_count=at_risk,
        intervention_needed_this_week_count=intervention,
        trend_notes=notes,
        metric_sources=sources,
        historical_coverage_ok=coverage_ok,
        weekly_trend_last_4=trend_rows,
        intervention_queue_approval_rate_30d=approval_rate,
        intervention_conversion_note=conv_note,
    )
