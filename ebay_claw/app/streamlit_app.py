"""eBay Claw — Streamlit dashboard MVP."""

from __future__ import annotations

import html
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import json

import streamlit as st

from ebay_claw.config.settings import get_settings
from ebay_claw.models.domain import ReviewStatus
from ebay_claw.services.dashboard_api import DashboardAPI
from ebay_claw.services.orchestrator import ClawOrchestrator

st.set_page_config(page_title="eBay Claw", layout="wide", initial_sidebar_state="expanded")

st.title("eBay Claw")
st.caption("Clothing resale — 90-day turnover & profit protection")

base_settings = get_settings()

if "orch" not in st.session_state or st.session_state.get("settings_fingerprint") != base_settings.runtime_mode.value:
    st.session_state.settings_fingerprint = base_settings.runtime_mode.value
    st.session_state.orch = ClawOrchestrator(settings=base_settings)
    st.session_state.api = DashboardAPI(st.session_state.orch)

api: DashboardAPI = st.session_state.api

with st.sidebar:
    st.subheader("Ingest")
    info = api.adapter_info()
    st.caption(
        f"Data source (server): **{info['configured_data_source']}** · "
        f"Effective adapter: **{info['effective_adapter']}** · "
        f"Runtime mode: **{info['runtime_mode']}**"
    )
    if info["configured_data_source"] == "live" and not info["live_oauth_ready"]:
        st.error("Live ingest requires OAuth — configuration is incomplete (fail-closed).")

    ao = info.get("auth_operational") or {}
    if ao.get("state") not in ("ok", "fixture_mode", None):
        st.warning(ao.get("readable_reason") or ao.get("hint") or "Auth check required.")

    ss = api.get_sync_state()
    st.metric("Sync status", ss.status)
    if ss.partial_sync or ss.warnings:
        st.warning("Partial sync or warnings: " + "; ".join(ss.warnings[:5]))
    if ss.completed_at:
        dur = (
            f"{ss.duration_seconds:.1f}s"
            if ss.duration_seconds is not None
            else "n/a"
        )
        st.caption(
            f"Last completed (UTC): `{ss.completed_at}` · duration: {dur} · "
            f"listings: {ss.listing_count} · API calls: {ss.api_calls_used}/{ss.api_budget_max} · "
            f"cache H/M: {ss.cache_hits}/{ss.cache_misses}"
        )
    if ss.message_safe and ss.status in ("ok", "error", "partial"):
        st.caption(ss.message_safe[:200])

    if st.button("Reload listings (sync)"):
        try:
            st.session_state.orch.load_listings()
        except Exception as e:
            st.session_state["ingest_error"] = str(e)
        else:
            st.session_state["ingest_error"] = None
        st.rerun()
    if st.session_state.get("ingest_error"):
        st.error(st.session_state["ingest_error"])

    st.subheader("Security / rollout")
    inf = api.adapter_info()
    st.caption(
        f"Server runtime_mode: **{inf.get('runtime_mode')}** · "
        f"read_only (mutations blocked): **{inf.get('read_only_mode')}** · "
        f"Guarded write flag: **{inf.get('guarded_write_enabled')}**"
    )
    st.caption("The UI cannot change runtime mode — set EBAY_CLAW_RUNTIME_MODE on the server process.")

    st.subheader("Live title update operations")
    st.caption(
        "Read-only visibility from audit log (live guarded writes: title + safe specifics). "
        "No apply actions here — use the review queue section below."
    )
    try:
        lw = api.live_write_operations_visibility()
        m = lw.get("metrics") or {}
        r1, r2, r3, r4 = st.columns(4)
        r1.metric("Live attempts", m.get("live_write_attempts", 0))
        r2.metric("Live successes", m.get("live_write_successes", 0))
        r3.metric("Live failures", m.get("live_write_failures", 0))
        r4.metric("Blocked applies", m.get("blocked_applies", 0))
        r5, r6, r7, r8 = st.columns(4)
        r5.metric("Idempotency blocks", m.get("idempotency_blocks", 0))
        r6.metric("Retryable failures", m.get("retryable_failures", 0))
        r7.metric("Non-retryable failures", m.get("non_retryable_failures", 0))
        r8.metric("Executor buckets (rows)", sum((m.get("executor_failure_buckets") or {}).values()))
        fb = m.get("executor_failure_buckets") or {}
        if any(fb.values()):
            st.caption("Executor failure reasons (live guarded writes)")
            st.json(fb)
        bb = m.get("blocked_apply_buckets") or {}
        if any(bb.values()):
            st.caption("Blocked apply categories")
            st.json(bb)
        hist = lw.get("recent_live_title_writes") or []
        if hist:
            st.caption("Recent live title writes (newest first)")
            st.dataframe(hist, use_container_width=True, hide_index=True)
        else:
            st.info("No live title write rows in audit yet (or all activity is simulated / other actions).")
        if lw.get("note"):
            st.caption(lw["note"])
        pol = lw.get("expansion_advisory_policy")
        if pol:
            with st.expander("Live write expansion advisory — active thresholds (read-only policy aid)"):
                st.json(pol)
                st.caption(
                    "Configure with EBAY_CLAW_LIVE_WRITE_EXPANSION_* settings. "
                    "Advisory only — not an apply gate."
                )
        tr = lw.get("trends") or {}
        if tr:
            adv = tr.get("expansion_advisory") or {}
            rd = adv.get("readiness") or "unknown"
            prim = adv.get("primary_reason_code") or ""
            summary_esc = html.escape(str(adv.get("summary") or ""))
            head = f"**Expansion advisory (policy):** `{html.escape(str(rd))}`"
            if prim:
                head += f" · primary driver: `{html.escape(str(prim))}`"
            head += f" — {summary_esc}"
            if rd == "ready":
                st.success(head)
            elif rd == "not_ready":
                st.warning(head)
            else:
                st.info(head)
            rc = adv.get("reason_codes") or []
            if rc:
                st.caption(
                    "Reason codes: "
                    + ", ".join(f"`{html.escape(str(c))}`" for c in rc)
                )
            for r in adv.get("reasons") or []:
                st.caption(f"• {html.escape(str(r))}")
            st.caption("7d vs prior 7d (rolling from as-of date)")
            st.dataframe(tr.get("seven_day_vs_prior") or [], use_container_width=True, hide_index=True)
            m7 = (tr.get("last_7d") or {}).get("metrics") or {}
            m30 = (tr.get("last_30d") or {}).get("metrics") or {}
            t1, t2 = st.columns(2)
            t1.metric("Attempts (7d)", m7.get("live_write_attempts", 0))
            t2.metric("Attempts (30d)", m30.get("live_write_attempts", 0))
            st.caption("Failure buckets (title_drift, auth, provider, missing id) — 7d vs prior 7d")
            st.dataframe(tr.get("failure_bucket_trends") or [], use_container_width=True, hide_index=True)
            if tr.get("top_failure_messages_7d"):
                st.caption("Top non-other failure messages (last 7d, normalized)")
                st.dataframe(
                    tr.get("top_failure_messages_7d"),
                    use_container_width=True,
                    hide_index=True,
                )
            ob = tr.get("other_bucket") or {}
            if ob.get("current_7d_count") or ob.get("previous_7d_count"):
                st.caption(
                    f"Other bucket: {ob.get('current_7d_count', 0)} (7d) vs "
                    f"{ob.get('previous_7d_count', 0)} (prior 7d) — sample messages"
                )
                for line in ob.get("sampled_normalized_messages") or []:
                    st.caption(f"• {line}")
    except Exception as elw:
        st.caption(f"Live write visibility unavailable: {elw}")

    st.subheader("Operations snapshot")
    try:
        ops = api.operational_overview()
        qc = ops["queue_counts"]
        st.caption(
            f"Queue · pending: **{qc['pending']}** · superseded: **{qc['superseded']}** · "
            f"stale (pending): **{qc['stale_pending']}** · applied: **{qc['applied']}**"
        )
        hist = ops.get("last_10_syncs") or []
        if hist:
            st.caption("Last 10 sync runs (newest first) — source: sync history JSONL.")
            st.dataframe(hist, use_container_width=True, hide_index=True)
    except Exception as ex:
        st.caption(f"Operations snapshot unavailable: {ex}")

    st.subheader("Compliance (placeholder)")
    try:
        cs = api.compliance_summary()
        st.caption(
            f"Checked {cs.get('listings_checked', 0)} listings · "
            f"info {cs.get('info_count', 0)} · warnings {cs.get('warning_count', 0)} · "
            f"blocking listings {cs.get('blocking_count', 0)}"
        )
        if cs.get("sample_guarded_write_blockers"):
            st.caption(
                "Guarded-write block examples: "
                + " · ".join(cs["sample_guarded_write_blockers"][:3])
            )
        if cs.get("sample_warnings"):
            st.caption("Sample warnings: " + ", ".join(cs["sample_warnings"]))
    except Exception as ex:
        st.caption(f"Compliance summary unavailable: {ex}")

    st.subheader("Pipeline")
    if st.button("Run analysis pipeline → review queue"):
        with st.spinner("Running…"):
            st.session_state.orch.run_pipeline()
        st.success("Pipeline complete.")

    st.subheader("Recovery proposals")
    st.caption("Enqueue relist + bundle-lot rows (review queue only — no listing writes).")
    if st.button("Enqueue relist + bundle proposals"):
        with st.spinner("Building proposals…"):
            st.session_state.orch.run_recovery_proposals()
        st.success("Recovery proposals added to the review queue.")

st.subheader("Store load")
try:
    metrics = api.store_metrics()
except Exception as e:
    st.error(f"Could not load store data: {e}")
    st.stop()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Active listings", metrics.inventory_count)
c2.metric("Avg age (days)", f"{metrics.average_listing_age_days:.1f}")
c3.metric("Stale (flagged)", metrics.stale_inventory_count)
c4.metric("Intervention this week (est.)", metrics.intervention_needed_this_week_count)

st.subheader("90-day outlook")
p1, p2 = st.columns(2)
p1.metric("Est. likely sell ≤90d", f"{metrics.pct_likely_sell_within_90_days:.1f}%")
p2.metric("At-risk (75d+ or low model score)", f"{metrics.pct_at_risk_past_90_days:.1f}%")

st.subheader("Velocity dashboard (event-based + estimates)")
st.caption(
    "Primary row uses operational history when recent sync coverage exists; "
    "parallel columns show listing-field estimates. Read-only — no marketplace writes."
)
try:
    vm = api.velocity_metrics()
    cov = vm.get("historical_coverage_ok", False)
    src = vm.get("metric_sources") or {}
    va, vb, vc, vd = st.columns(4)
    va.metric(
        "Listings created (7d, primary)",
        vm["listings_created_last_7d"],
        help=f"Source: {src.get('listings_created_last_7d', 'n/a')}",
    )
    sold_e = vm.get("sold_units_event_last_7d")
    vb.metric(
        "Sold units (7d, primary)",
        f"{sold_e:.1f}" if sold_e is not None and cov else f"{vm['sold_units_estimated_last_7d']:.1f}",
        help=f"Primary source: {src.get('sold_units_last_7d', 'n/a')}; "
        f"estimated fallback={vm['sold_units_estimated_last_7d']:.1f}",
    )
    net_p = vm.get("net_inventory_change_last_7d")
    vc.metric(
        "Net inventory Δ (7d, primary)",
        f"{net_p:.1f}" if net_p is not None else f"{vm['net_inventory_change_estimated_7d']:.1f}",
        help=f"Source: {src.get('net_inventory_last_7d', 'n/a')}",
    )
    vd.metric("At-risk (90d model)", vm["at_risk_90d_listings_count"])
    ve, vf, vg = st.columns(3)
    st_in_e = vm.get("stale_inflow_event_last_7d")
    ve.metric(
        "Stale inflow (7d, primary)",
        st_in_e if st_in_e is not None and cov else vm["stale_new_inflow_estimated_7d"],
        help=f"Source: {src.get('stale_inflow_last_7d', 'n/a')}; "
        f"age-band estimate={vm['stale_new_inflow_estimated_7d']}",
    )
    sc_e = vm.get("stale_cleared_event_last_7d")
    vf.metric(
        "Stale cleared (7d)",
        sc_e if sc_e is not None else "—",
        help=vm.get("stale_cleared_data_note") or "",
    )
    approval_rate = vm.get("intervention_queue_approval_rate_30d")
    vg.metric(
        "Queue approval rate (30d)",
        f"{approval_rate:.0%}" if approval_rate is not None else "—",
        help=vm.get("intervention_conversion_note") or "",
    )
    vh = st.columns(1)[0]
    vh.metric("Intervention this week (model)", vm["intervention_needed_this_week_count"])
    for note in vm.get("trend_notes") or []:
        st.caption(note)
    if vm.get("stale_cleared_data_note"):
        st.caption(vm["stale_cleared_data_note"])
    trends = vm.get("weekly_trend_last_4") or []
    if trends:
        st.caption("Last 4 weeks (operational events where available)")
        st.dataframe(trends, use_container_width=True, hide_index=True)
except Exception as ev:
    st.warning(f"Velocity metrics unavailable: {ev}")

st.subheader("Action effectiveness (attributed outcomes)")
st.caption(
    "Read-only links from operational history: prior approvals/proposals → later sale or stale exit. "
    "Rates use cohort-size shrinkage; tiers gate how much outcomes move rankings. Not causal proof."
)
try:
    eff = api.action_effectiveness_summaries()
    rows = []
    for s in eff.get("summaries") or []:
        rows.append(
            {
                "action": s.get("label"),
                "cohort_all_time": s.get("cohort_actions_count"),
                "lift_all_time": s.get("attribution_lift_score"),
                "tier_all_time": s.get("evidence_tier"),
                "cohort_recent": s.get("recent_cohort_actions_count"),
                "lift_recent": s.get("recent_attribution_lift_score"),
                "tier_recent": s.get("recent_evidence_tier"),
                "lift_ranking": s.get("ranking_attribution_lift_score"),
                "attributed_sales": s.get("attributed_sales"),
                "sale_rate_raw": s.get("sale_rate"),
                "sale_rate_shrunk": s.get("shrunk_sale_rate"),
                "lift_weight": s.get("attribution_lift_weight"),
            }
        )
    if rows:
        st.dataframe(rows, use_container_width=True, hide_index=True)
        st.caption(
            f"Attribution window {eff.get('attribution_window_days')}d · "
            f"recent cohort {eff.get('recent_cohort_days', 90)}d · "
            f"recency half-life {eff.get('recency_half_life_days', 45)}d · "
            f"unattributed sales: {eff.get('unattributed_sales_in_window', 0)}"
        )
    else:
        st.info("No effectiveness rows — add operational history events (sync, queue, outcomes).")
except Exception as ee:
    st.caption(f"Effectiveness summaries unavailable: {ee}")

st.subheader("Today's priority actions")
try:
    st.dataframe(api.daily_priority_actions(), use_container_width=True, hide_index=True)
except Exception as ed:
    st.caption(str(ed))

st.subheader("Price-to-sell (stale / above comps)")
try:
    pts = api.price_to_sell_recommendations()
    if pts:
        st.dataframe(pts, use_container_width=True, hide_index=True)
    else:
        st.info("No rows matched stale/above-comps filters.")
except Exception as ep:
    st.caption(str(ep))

with st.expander("Relist proposals (preview)", expanded=False):
    try:
        rp = api.relist_proposals_preview()
        if rp:
            st.dataframe(rp, use_container_width=True, hide_index=True)
        else:
            st.caption("No relist candidates under current rules.")
    except Exception as er:
        st.caption(str(er))

with st.expander("Bundle lot candidates (preview)", expanded=False):
    try:
        bd = api.bundle_recommendations_preview()
        if bd:
            st.dataframe(bd, use_container_width=True, hide_index=True)
        else:
            st.caption("No bundle clusters matched.")
    except Exception as eb:
        st.caption(str(eb))

st.subheader("Age distribution")
st.bar_chart(metrics.age_distribution)

st.subheader("Stale inventory")
st.dataframe(api.stale_table(), use_container_width=True, hide_index=True)

t1, t2 = st.columns(2)
with t1:
    st.subheader("Weak titles")
    st.dataframe(api.weak_titles(), use_container_width=True, hide_index=True)
with t2:
    st.subheader("Missing specifics")
    st.dataframe(api.missing_specifics(), use_container_width=True, hide_index=True)

st.subheader("Pricing recommendations")
st.caption(
    "Strategy and pricing both consume the same read-only comp summary. "
    "Columns **strategy_baseline** vs **strategy** show when sold-market context changed the path; "
    "**market_strategy_note** explains the overlay."
)
st.dataframe(api.pricing_recommendations(), use_container_width=True, hide_index=True)

st.subheader("Market comps (read-only)")
st.caption("Sold comps power pricing suggestions only — no listing or account writes.")
try:
    ovr = api.market_overpriced_focus()
    if ovr:
        st.write("**Above recent sold median (usable comp confidence)**")
        st.dataframe(ovr, use_container_width=True, hide_index=True)
    holdm = api.market_hold_despite_age()
    if holdm:
        st.write("**Premium-style hold despite age / vs comps**")
        st.dataframe(holdm, use_container_width=True, hide_index=True)
    lowc = api.market_low_comp_confidence()
    if lowc:
        st.write("**Thin or low-confidence comp sample**")
        st.dataframe(lowc, use_container_width=True, hide_index=True)
    if not ovr and not holdm and not lowc:
        st.info("No market rows matched filters — add `fixtures/sold_comps.json` or widen inventory.")
except Exception as mex:
    st.caption(f"Market section unavailable: {mex}")

st.subheader("Intervention needed (this week)")
st.caption("Uses enriched comps + market-aware strategy (same as pricing rows).")
st.dataframe(api.intervention_week(), use_container_width=True, hide_index=True)

st.subheader("Review queue (state machine)")
st.caption(
    "All listing actions use the review queue API only — no direct status edits. "
    "Applies are not run from this UI (execution stays server-gated)."
)
_s = st.session_state.orch.settings
_queue_actor_default = (_s.default_actor or "").strip() or ""
_queue_actor = st.text_input(
    "Operator identity (required for queue actions)",
    value=_queue_actor_default,
    key="queue_actor_input",
    help="Recorded on approvals, rejections, and dry-run acknowledgements (audit).",
)
actor_ok = bool(_queue_actor.strip())

rq = api.review_queue()

def _queue_overview_rows(raw: list) -> list:
    out = []
    for i in raw:
        stale = bool(i.get("is_stale_vs_live"))
        out.append(
            {
                "id": i.get("id", ""),
                "listing_id": i.get("listing_id", ""),
                "status": i.get("status", ""),
                "stale": "yes" if stale else "",
                "dry_run_ack": "yes" if i.get("dry_run_acknowledged") else "",
                "action": i.get("proposed_action_type", ""),
                "title": (i.get("listing_title") or "")[:60],
            }
        )
    return out


if not rq:
    st.info("Queue is empty — run the pipeline from the sidebar.")
else:
    st.dataframe(_queue_overview_rows(rq), use_container_width=True, hide_index=True)
    labels_to_id = {
        f"{r.get('listing_id')} · {r.get('status')} · {str(r.get('id', ''))[:8]}…": r["id"]
        for r in rq
    }
    pick_label = st.selectbox("Select queue item", list(labels_to_id.keys()))
    sel_id = labels_to_id[pick_label]
    item = api.review_queue_item(sel_id)
    if item:
        status = str(item.get("status", ""))
        stale = bool(item.get("is_stale_vs_live"))
        if stale:
            st.error(
                "This row is **stale vs live** (listing fingerprint drifted). "
                "Resolve before relying on it — apply readiness will stay blocked."
            )
        c_state, c_dry, c_ap = st.columns([1, 1, 2])
        with c_state:
            st.metric("Queue state", status)
        with c_dry:
            st.metric("Dry-run ack", "yes" if item.get("dry_run_acknowledged") else "no")
        with c_ap:
            readiness = api.apply_readiness_for_queue_item(sel_id)
            ready = readiness.get("executor_ready")
            st.metric("Apply readiness (executor)", "ready" if ready else "blocked")

        with st.expander("Apply readiness detail (guard / policy / compliance)", expanded=not ready):
            for line in readiness.get("blockers") or []:
                st.write(f"- {line}")

        with st.expander("Proposed change — before / after (required for approval discipline)", expanded=True):
            st.caption("current_state_snapshot (before)")
            st.code(
                json.dumps(item.get("current_state_snapshot") or {}, indent=2, default=str)[:12000]
                or "{}",
                language="json",
            )
            st.caption("before_after_diff (after)")
            st.code(
                json.dumps(item.get("before_after_diff") or {}, indent=2, default=str)[:12000] or "{}",
                language="json",
            )

        require_dry = _s.require_dry_run_acknowledgement
        acked = bool(item.get("dry_run_acknowledged"))
        approve_disabled = (
            not actor_ok
            or status != ReviewStatus.PENDING.value
            or (require_dry and not acked)
        )
        if require_dry and status == ReviewStatus.PENDING.value and not acked:
            st.warning(
                "Approval is disabled until dry-run is acknowledged — use **Mark reviewed** after reading the diff."
            )

        b1, b2, b3 = st.columns(3)
        with b1:
            if st.button(
                "Mark reviewed (dry-run ack)",
                disabled=not actor_ok or status != ReviewStatus.PENDING.value or acked,
                help="Records audited dry-run acknowledgement while the item stays PENDING.",
            ):
                r = api.queue_acknowledge_dry_run(sel_id, actor=_queue_actor.strip())
                if r.get("ok"):
                    st.success("Dry-run acknowledged — you may Approve when policy allows.")
                else:
                    st.error(r.get("error", "Failed"))
                st.rerun()
        with b2:
            if st.button(
                "Approve",
                type="primary",
                disabled=approve_disabled,
                help="Transitions pending → approved via ReviewQueueStore.transition (audited).",
            ):
                dry_kw = True if require_dry else None
                r = api.queue_transition_ui(
                    sel_id,
                    ReviewStatus.APPROVED,
                    actor=_queue_actor.strip(),
                    dry_run_acknowledged=dry_kw,
                )
                if r.get("ok"):
                    st.success("Approved.")
                else:
                    st.error(r.get("error", "Failed"))
                st.rerun()
        with b3:
            if st.button(
                "Reject",
                disabled=not actor_ok or status != ReviewStatus.PENDING.value,
                help="Transitions pending → rejected via ReviewQueueStore.transition (audited).",
            ):
                r = api.queue_transition_ui(
                    sel_id,
                    ReviewStatus.REJECTED,
                    actor=_queue_actor.strip(),
                )
                if r.get("ok"):
                    st.success("Rejected.")
                else:
                    st.error(r.get("error", "Failed"))
                st.rerun()

        if status == ReviewStatus.SUPERSEDED.value:
            st.caption("Superseded items are terminal — select another row to act on a current proposal.")
        elif status not in (ReviewStatus.PENDING.value,):
            st.caption(
                "Only **pending** rows accept Mark reviewed / Approve / Reject. "
                "Applied/failed transitions are reserved for the execution path (not exposed here)."
            )

st.subheader("Listing detail")
ids = [x.listing_id for x in st.session_state.orch.load_listings()]
pick = None
if not ids:
    st.info("No listings loaded — check fixture path, OAuth, or sync error in the sidebar.")
else:
    pick = st.selectbox("Listing", ids, index=0)
if ids and pick is not None:
    d = api.listing_detail(pick)
    if d:
        st.json(d)
