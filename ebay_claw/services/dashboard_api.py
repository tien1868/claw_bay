"""Aggregates for dashboard / Streamlit — no HTTP server in MVP."""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from ebay_claw.analytics.inventory_analyst import InventoryAnalyst
from ebay_claw.analytics.strategy_scoring import StrategyScorer
from ebay_claw.adapters.auth_codes import (
    AUTH_ACCESS_TOKEN_REJECTED,
    AUTH_MISSING_CREDENTIALS,
    AUTH_REFRESH_FAILED,
    AUTH_REFRESH_UNAVAILABLE,
)
from ebay_claw.adapters.ebay_oauth import live_credentials_configured
from ebay_claw.models.domain import ListingRecord, ReviewStatus, StoreMetrics
from ebay_claw.models.sync_state import SyncState
from ebay_claw.services.orchestrator import ClawOrchestrator
from ebay_claw.services.sync_history import SyncHistoryStore
from ebay_claw.services.sync_state import SyncStateStore


class DashboardAPI:
    def __init__(self, orchestrator: Optional[ClawOrchestrator] = None):
        self._orch = orchestrator or ClawOrchestrator()
        self._analyst = InventoryAnalyst()
        self._scorer = StrategyScorer()

    def get_sync_state(self) -> SyncState:
        return SyncStateStore(settings=self._orch.settings).read()

    def _auth_operational_snapshot(self, ss: SyncState) -> Dict[str, Any]:
        s = self._orch.settings
        if s.data_source == "fixture":
            return {
                "state": "fixture_mode",
                "code": None,
                "hint": "No live OAuth used in fixture mode.",
                "readable_reason": None,
            }
        if not live_credentials_configured(s):
            return {
                "state": "missing_credentials",
                "code": AUTH_MISSING_CREDENTIALS,
                "hint": "Set EBAY_CLAW_EBAY_ACCESS_TOKEN or client_id+secret+refresh_token.",
                "readable_reason": "Live ingest is configured but OAuth credentials are incomplete.",
            }
        if ss.status == "error" and ss.last_auth_failure_code:
            code = ss.last_auth_failure_code
            reasons = {
                AUTH_MISSING_CREDENTIALS: "OAuth is incomplete for live ingest.",
                AUTH_REFRESH_UNAVAILABLE: "Access token was rejected and no refresh_token is configured.",
                AUTH_REFRESH_FAILED: "Token refresh failed at eBay identity — check secrets and scopes.",
                AUTH_ACCESS_TOKEN_REJECTED: "eBay returned 401 — token may be expired or revoked.",
            }
            return {
                "state": "auth_failure_on_last_sync",
                "code": code,
                "hint": reasons.get(code, "See last sync message and audit sync_failed events."),
                "readable_reason": reasons.get(code, code),
            }
        return {
            "state": "ok",
            "code": None,
            "hint": "Last sync did not record an auth failure.",
            "readable_reason": None,
        }

    def operational_overview(self) -> Dict[str, Any]:
        """Single call for operator dashboards: sync, queue, compliance, auth, recent history."""
        ss = self.get_sync_state()
        items = self._orch.queue.list_all()
        qcounts = {
            "pending": sum(1 for i in items if i.status == ReviewStatus.PENDING),
            "approved": sum(1 for i in items if i.status == ReviewStatus.APPROVED),
            "rejected": sum(1 for i in items if i.status == ReviewStatus.REJECTED),
            "superseded": sum(1 for i in items if i.status == ReviewStatus.SUPERSEDED),
            "applied": sum(1 for i in items if i.status == ReviewStatus.APPLIED),
            "failed": sum(1 for i in items if i.status == ReviewStatus.FAILED),
            "stale_pending": sum(
                1 for i in items if i.is_stale_vs_live and i.status == ReviewStatus.PENDING
            ),
        }
        hist = list(reversed(SyncHistoryStore(settings=self._orch.settings).last_n(10)))
        comp = self.compliance_summary()
        auth = self._auth_operational_snapshot(ss)
        budget_max = ss.api_budget_max or 1
        return {
            "sync": {
                "status": ss.status,
                "duration_seconds": ss.duration_seconds,
                "listing_count": ss.listing_count,
                "api_calls_used": ss.api_calls_used,
                "api_budget_max": ss.api_budget_max,
                "api_budget_ratio": round(ss.api_calls_used / budget_max, 5),
                "cache_hits": ss.cache_hits,
                "cache_misses": ss.cache_misses,
                "partial_sync": ss.partial_sync,
                "warnings": list(ss.warnings[:12]),
                "last_auth_failure_code": ss.last_auth_failure_code,
            },
            "queue_counts": qcounts,
            "last_10_syncs": [h.model_dump(mode="json") for h in hist],
            "compliance": comp,
            "auth": auth,
        }

    def adapter_info(self) -> Dict[str, Any]:
        s = self._orch.settings
        live_ok = live_credentials_configured(s)
        effective = (
            "ebay_inventory_readonly"
            if s.data_source == "live" and live_ok
            else "fixture_json"
        )
        ss = self.get_sync_state()
        auth = self._auth_operational_snapshot(ss)
        return {
            "configured_data_source": s.data_source,
            "effective_adapter": effective,
            "live_oauth_ready": live_ok,
            "runtime_mode": s.runtime_mode.value,
            "read_only_mode": s.read_only_mode,
            "guarded_write_enabled": s.guarded_write_enabled,
            "sync_status": ss.status,
            "sync_partial": ss.partial_sync,
            "sync_warnings": list(ss.warnings[:12]),
            "api_calls_used": ss.api_calls_used,
            "api_budget_max": ss.api_budget_max,
            "sync_duration_seconds": ss.duration_seconds,
            "cache_hits": ss.cache_hits,
            "cache_misses": ss.cache_misses,
            "last_auth_failure_code": ss.last_auth_failure_code,
            "auth_operational": auth,
        }

    def compliance_summary(self) -> Dict[str, Any]:
        from ebay_claw.compliance.boundary import EbayComplianceBoundary

        boundary = EbayComplianceBoundary(settings=self._orch.settings)
        results = [
            boundary.check_listing(lst) for lst in self._orch.load_listings()
        ]
        summ = boundary.summarize_for_dashboard(results)
        return {
            **summ,
            "blocking_count": summ.get("blocking_listing_count", 0),
            "warning_count": summ.get("warning_signal_count", 0),
            "info_count": summ.get("info_signal_count", 0),
            "sample_warnings": sorted({w for r in results for w in r.warnings})[:12],
            "blocking_listings": [r.listing_id for r in results if r.blocking_issues],
            "sample_guarded_write_blockers": summ.get("sample_guarded_write_blockers", []),
        }

    def store_metrics(self, as_of: Optional[date] = None) -> StoreMetrics:
        listings = self._orch.load_listings()
        return self._orch.metrics_calc.compute(listings, as_of=as_of)

    def velocity_metrics(self, as_of: Optional[date] = None) -> Dict[str, Any]:
        from ebay_claw.analytics.velocity_metrics import compute_velocity_metrics

        return compute_velocity_metrics(
            self._orch.load_listings(),
            as_of=as_of,
            settings=self._orch.settings,
        ).model_dump(mode="json")

    def daily_priority_actions(self, as_of: Optional[date] = None, top_n: int = 10) -> List[Dict[str, Any]]:
        from ebay_claw.services.daily_priority_actions import build_daily_priority_actions

        today = as_of or date.today()
        rows = build_daily_priority_actions(
            self._orch.load_listings(),
            enriched_fn=lambda lst: self._orch.enriched_analysis(lst, as_of=today),
            as_of=today,
            top_n=top_n,
            settings=self._orch.settings,
        )
        return [r.model_dump(mode="json") for r in rows]

    def action_effectiveness_summaries(
        self,
        as_of: Optional[date] = None,
        *,
        attribution_window_days: int = 90,
        cohort_lookback_days: int = 180,
        recent_cohort_days: int = 90,
        recency_half_life_days: float = 45.0,
    ) -> Dict[str, Any]:
        """Read-only: attributed sale/stale rates by action class from operational history."""
        from ebay_claw.services.outcome_attribution import summarize_action_effectiveness
        from ebay_claw.services.operational_history_store import OperationalHistoryStore

        today = as_of or date.today()
        store = OperationalHistoryStore(settings=self._orch.settings)
        dash = summarize_action_effectiveness(
            store,
            as_of=today,
            attribution_window_days=attribution_window_days,
            cohort_lookback_days=cohort_lookback_days,
            recent_cohort_days=recent_cohort_days,
            recency_half_life_days=recency_half_life_days,
        )
        return dash.model_dump(mode="json")

    def live_write_operations_visibility(self, as_of: Optional[date] = None) -> Dict[str, Any]:
        """Read-only: live UPDATE_TITLE metrics, trends, and recent outcomes from audit JSONL."""
        from ebay_claw.services.live_write_visibility import load_live_write_operations_snapshot

        today = as_of or date.today()
        return load_live_write_operations_snapshot(
            settings=self._orch.settings,
            as_of=today,
        ).model_dump(mode="json")

    def price_to_sell_recommendations(self, as_of: Optional[date] = None) -> List[Dict[str, Any]]:
        from ebay_claw.analytics.price_to_sell import compute_price_to_sell

        today = as_of or date.today()
        out: List[Dict[str, Any]] = []
        for lst in self._orch.load_listings():
            a = self._orch.enriched_analysis(lst, as_of=today)
            if not (
                a.is_stale
                or (a.price_outlier_note and "above" in (a.price_outlier_note or "").lower())
            ):
                continue
            out.append(compute_price_to_sell(lst, a).model_dump(mode="json"))
        return out

    def relist_proposals_preview(self, as_of: Optional[date] = None) -> List[Dict[str, Any]]:
        from ebay_claw.analytics.relist_accelerator import (
            build_relist_proposal,
            is_relist_candidate,
        )

        today = as_of or date.today()
        props: List[Dict[str, Any]] = []
        for lst in self._orch.load_listings():
            a = self._orch.enriched_analysis(lst, as_of=today)
            sc = self._scorer.score(lst, a, as_of=today)
            if is_relist_candidate(lst, a, sc):
                props.append(
                    build_relist_proposal(
                        lst, a, sc, settings=self._orch.settings
                    ).model_dump(mode="json")
                )
        return props

    def bundle_recommendations_preview(self, as_of: Optional[date] = None) -> List[Dict[str, Any]]:
        from ebay_claw.analytics.bundle_identifier import identify_bundle_candidates

        today = as_of or date.today()
        return [
            b.model_dump(mode="json")
            for b in identify_bundle_candidates(self._orch.load_listings(), as_of=today)
        ]

    def stale_table(self, as_of: Optional[date] = None) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        today = as_of or date.today()
        for lst in self._orch.load_listings():
            a = self._orch.enriched_analysis(lst, as_of=today)
            if a.is_stale or a.days_active >= 60:
                sc = self._scorer.score(lst, a, as_of=today)
                out.append(
                    {
                        "listing_id": lst.listing_id,
                        "title": lst.title,
                        "days_active": a.days_active,
                        "age_bucket": a.age_bucket.value,
                        "stale_reasons": a.stale_reasons,
                        "strategy": sc.recommended_strategy.value,
                        "strategy_baseline": sc.baseline_strategy.value,
                        "strategy_changed_by_market": sc.strategy_changed_by_market,
                        "market_strategy_note": sc.market_adjustment_note,
                        "price_position_vs_market": sc.price_position_vs_market,
                        "sale_likelihood_90d": round(sc.sale_likelihood_before_90_days, 3),
                    }
                )
        return sorted(out, key=lambda x: -x["days_active"])

    def weak_titles(self) -> List[Dict[str, Any]]:
        from ebay_claw.analytics.inventory_analyst import weak_title_signals

        rows = []
        for lst in self._orch.load_listings():
            sigs = weak_title_signals(lst.title)
            if sigs:
                rows.append(
                    {
                        "listing_id": lst.listing_id,
                        "title": lst.title,
                        "signals": sigs,
                    }
                )
        return rows

    def missing_specifics(self) -> List[Dict[str, Any]]:
        rows = []
        for lst in self._orch.load_listings():
            a = self._analyst.analyze(lst)
            if a.missing_critical_fields:
                rows.append(
                    {
                        "listing_id": lst.listing_id,
                        "title": lst.title,
                        "missing": a.missing_critical_fields,
                    }
                )
        return rows

    def pricing_recommendations(self, as_of: Optional[date] = None) -> List[Dict[str, Any]]:
        today = as_of or date.today()
        pa = self._orch.pricing
        rows = []
        for lst in self._orch.load_listings():
            a = self._orch.enriched_analysis(lst, as_of=today)
            sc = self._scorer.score(lst, a, as_of=today)
            strat = sc.recommended_strategy
            pr = pa.recommend(lst, a, strat)
            m = a.market
            rows.append(
                {
                    "listing_id": lst.listing_id,
                    "title": lst.title,
                    "ask_price": lst.price_amount,
                    "action": pr.recommended_action.value,
                    "confidence": round(pr.confidence, 2),
                    "explanation": pr.explanation,
                    "strategy": strat.value,
                    "strategy_baseline": sc.baseline_strategy.value,
                    "strategy_changed_by_market": sc.strategy_changed_by_market,
                    "market_strategy_note": sc.market_adjustment_note,
                    "pricing_segment": pr.pricing_segment,
                    "median_sold": m.median_sold_price if m else None,
                    "comp_count": m.comp_count if m else 0,
                    "pct_vs_median": m.pct_vs_median if m else None,
                    "comp_match_confidence": m.comp_match_confidence if m else None,
                    "price_position": m.price_position.value if m else "unknown",
                    "comps_recency_days": m.recency_window_days if m else None,
                    "comps_source": m.comps_data_source if m else None,
                }
            )
        return rows

    def market_pricing_table(self, as_of: Optional[date] = None) -> List[Dict[str, Any]]:
        """All listings with market + pricing fields for operator review."""
        return self.pricing_recommendations(as_of=as_of)

    def market_overpriced_focus(self, as_of: Optional[date] = None) -> List[Dict[str, Any]]:
        rows = self.market_pricing_table(as_of=as_of)
        return [
            r
            for r in rows
            if r.get("price_position") == "above_market"
            and (r.get("comp_match_confidence") or 0) >= 0.45
        ]

    def market_hold_despite_age(self, as_of: Optional[date] = None) -> List[Dict[str, Any]]:
        return [
            r
            for r in self.market_pricing_table(as_of=as_of)
            if r.get("pricing_segment") == "premium_hold_despite_age"
        ]

    def market_low_comp_confidence(self, as_of: Optional[date] = None) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for r in self.market_pricing_table(as_of=as_of):
            if r.get("pricing_segment") == "low_comp_confidence":
                out.append(r)
                continue
            n = r.get("comp_count") or 0
            mc = r.get("comp_match_confidence")
            if n >= 1 and (n < 2 or (mc is not None and mc < 0.35)):
                out.append(r)
        return out

    def intervention_week(self, as_of: Optional[date] = None) -> List[Dict[str, Any]]:
        today = as_of or date.today()
        rows = []
        for lst in self._orch.load_listings():
            a = self._orch.enriched_analysis(lst, as_of=today)
            sc = self._scorer.score(lst, a, as_of=today)
            if a.days_active >= 60 and (
                len(a.weak_title_signals) >= 2 or len(a.missing_critical_fields) >= 2
            ):
                rows.append(
                    {
                        "listing_id": lst.listing_id,
                        "title": lst.title,
                        "days_active": a.days_active,
                        "leverage": a.highest_leverage_action,
                        "strategy": sc.recommended_strategy.value,
                        "strategy_baseline": sc.baseline_strategy.value,
                        "price_position_vs_market": sc.price_position_vs_market,
                        "strategy_changed_by_market": sc.strategy_changed_by_market,
                        "market_strategy_note": sc.market_adjustment_note,
                    }
                )
        return sorted(rows, key=lambda x: -x["days_active"])

    def review_queue(self) -> List[Dict[str, Any]]:
        q = self._orch.queue
        return [i.model_dump(mode="json") for i in q.list_all()]

    def review_queue_item(self, item_id: str) -> Optional[Dict[str, Any]]:
        item = self._orch.queue.get(item_id)
        if not item:
            return None
        return item.model_dump(mode="json")

    def queue_acknowledge_dry_run(self, item_id: str, *, actor: str) -> Dict[str, Any]:
        """Operator confirms diff reviewed (PENDING only) — audited."""
        from ebay_claw.review_queue.state_machine import QueueTransitionError

        try:
            item = self._orch.queue.acknowledge_dry_run(item_id, actor=actor)
            return {"ok": True, "item": item.model_dump(mode="json")}
        except QueueTransitionError as e:
            return {"ok": False, "error": str(e)}

    def queue_transition_ui(
        self,
        item_id: str,
        target: ReviewStatus,
        *,
        actor: str,
        dry_run_acknowledged: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Approve / reject / etc. — always via ReviewQueueStore.transition (audited)."""
        from ebay_claw.review_queue.state_machine import QueueTransitionError

        try:
            out = self._orch.queue.transition(
                item_id,
                target,
                actor=actor,
                dry_run_acknowledged=dry_run_acknowledged,
            )
            if out is None:
                return {"ok": False, "error": "Unknown queue item."}
            return {"ok": True, "item": out.model_dump(mode="json")}
        except QueueTransitionError as e:
            return {"ok": False, "error": str(e)}

    def apply_readiness_for_queue_item(self, item_id: str) -> Dict[str, Any]:
        from ebay_claw.review_queue.apply_guard import apply_executor_ready, list_apply_operator_blockers

        item = self._orch.queue.get(item_id)
        if not item:
            return {
                "executor_ready": False,
                "blockers": ["Unknown queue item id."],
            }
        s = self._orch.settings
        blockers = list_apply_operator_blockers(s, item)
        ready = apply_executor_ready(s, item)
        return {"executor_ready": ready, "blockers": blockers}

    def listing_detail(self, listing_id: str) -> Optional[Dict[str, Any]]:
        lst = next((x for x in self._orch.load_listings() if x.listing_id == listing_id), None)
        if not lst:
            return None
        today = date.today()
        a = self._orch.enriched_analysis(lst, as_of=today)
        sc = self._scorer.score(lst, a, as_of=today)
        from ebay_claw.agents.title_agent import TitleAgent
        from ebay_claw.agents.specifics_agent import SpecificsAgent

        ta = TitleAgent().suggest(lst)
        sa = SpecificsAgent().suggest(lst)
        pr = self._orch.pricing.recommend(lst, a, sc.recommended_strategy)
        return {
            "listing": lst.model_dump(),
            "analysis": a.model_dump(),
            "strategy_score": sc.model_dump(),
            "title": ta.model_dump(),
            "specifics": sa.model_dump(),
            "pricing": pr.model_dump(),
        }
