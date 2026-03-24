"""End-to-end: ingest → analyze → agents → policy → review queue."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import List, Optional, Tuple

from ebay_claw.adapters.comps_factory import build_sold_comps_adapter
from ebay_claw.adapters.factory import build_listing_adapter
from ebay_claw.agents.pricing_agent import PricingAgent
from ebay_claw.agents.specifics_agent import SpecificsAgent
from ebay_claw.agents.title_agent import TitleAgent
from ebay_claw.analytics.inventory_analyst import InventoryAnalyst
from ebay_claw.analytics.store_metrics import StoreMetricsCalculator
from ebay_claw.analytics.strategy_scoring import StrategyScorer
from ebay_claw.config.settings import Settings, get_settings
from ebay_claw.models.domain import (
    ListingAnalysis,
    ListingRecord,
    PricingAction,
    ProposedActionType,
    ReviewQueueItem,
    ReviewStatus,
    StrategicPath,
)
from ebay_claw.compliance.boundary import EbayComplianceBoundary
from ebay_claw.models.compliance_issue import ComplianceSeverity
from ebay_claw.models.runtime_mode import ClawRuntimeMode
from ebay_claw.review_queue.state_machine import build_transition_update, utc_now
from ebay_claw.policies.safety import PolicyEngine
from ebay_claw.review_queue.store import ReviewQueueStore
from ebay_claw.security.write_guard import WriteForbiddenError
from ebay_claw.services.comp_market import summarize_sold_comps
from ebay_claw.services.ingestion import IngestionService
from ebay_claw.services.inventory_movement_recorder import InventoryMovementRecorder
from ebay_claw.services.operational_history_store import OperationalHistoryStore


def _pricing_to_proposed(pa: PricingAction) -> ProposedActionType:
    mapping = {
        PricingAction.MARKDOWN_10: ProposedActionType.MARKDOWN_LISTING,
        PricingAction.MARKDOWN_20: ProposedActionType.MARKDOWN_LISTING,
        PricingAction.MARKDOWN_30: ProposedActionType.MARKDOWN_LISTING,
        PricingAction.IMPROVE_TITLE: ProposedActionType.UPDATE_TITLE,
        PricingAction.FILL_SPECIFICS: ProposedActionType.UPDATE_SAFE_SPECIFICS,
        PricingAction.SEND_OFFER: ProposedActionType.SEND_OFFER,
        PricingAction.END_AND_SELL_SIMILAR: ProposedActionType.END_AND_SELL_SIMILAR,
        PricingAction.BUNDLE_CANDIDATE: ProposedActionType.PREPARE_RELIST,
    }
    return mapping.get(pa, ProposedActionType.UPDATE_TITLE)


def _markdown_pct(action: PricingAction) -> int:
    return {
        PricingAction.MARKDOWN_10: 10,
        PricingAction.MARKDOWN_20: 20,
        PricingAction.MARKDOWN_30: 30,
    }.get(action, 0)


class ClawOrchestrator:
    def __init__(
        self,
        settings: Optional[Settings] = None,
        queue: Optional[ReviewQueueStore] = None,
        data_source_override: Optional[str] = None,
    ):
        self._s = settings or get_settings()
        if data_source_override is not None:
            if not self._s.allow_dev_runtime_override:
                raise WriteForbiddenError(
                    "Server runtime_mode is canonical — UI/data_source override is disabled "
                    "(set EBAY_CLAW_ALLOW_DEV_RUNTIME_OVERRIDE=true for tests only)."
                )
            rm = (
                ClawRuntimeMode.FIXTURE
                if data_source_override == "fixture"
                else ClawRuntimeMode.LIVE_READ_ONLY
            )
            self._s = self._s.model_copy(update={"runtime_mode": rm})
        self.queue = queue or ReviewQueueStore(settings=self._s)
        self.ingestion = IngestionService(build_listing_adapter(self._s))
        self.analyst = InventoryAnalyst(self._s)
        self.scorer = StrategyScorer(self._s)
        self.title_agent = TitleAgent()
        self.specifics_agent = SpecificsAgent(self._s)
        self.pricing = PricingAgent(self._s)
        self.policy = PolicyEngine(self._s)
        self.compliance = EbayComplianceBoundary(self._s)
        self.metrics_calc = StoreMetricsCalculator()
        self._comps_adapter = build_sold_comps_adapter(self._s)

    @property
    def settings(self) -> Settings:
        return self._s

    def enriched_analysis(
        self,
        listing: ListingRecord,
        as_of: Optional[date] = None,
    ) -> ListingAnalysis:
        """Listing analysis with read-only sold comps summary attached (no writes)."""
        today = as_of or date.today()
        raw = self._comps_adapter.fetch_comps_for_listing(listing)
        msum = summarize_sold_comps(listing, raw, today, self._s)
        return self.analyst.analyze(listing, as_of=today, market_summary=msum)

    def load_listings(
        self,
        as_of: Optional[date] = None,
        *,
        record_operational_movement: bool = False,
    ) -> List[ListingRecord]:
        listings = self.ingestion.load_listings()
        self.queue.flag_stale_vs_live({l.listing_id: l for l in listings})
        if record_operational_movement:
            today = as_of or date.today()
            InventoryMovementRecorder(settings=self._s).record_after_ingest(
                listings,
                as_of=today,
                analyst=self.analyst,
                data_source=self._s.data_source,
            )
        return listings

    def _enqueue_item(
        self,
        lst: ListingRecord,
        snapshot: dict,
        strat: StrategicPath,
        proposed: ProposedActionType,
        diff: dict,
        confidence: float,
        rationale: str,
        impact_90: str,
    ) -> ReviewQueueItem:
        item = self.queue.create(
            listing_id=lst.listing_id,
            listing_title=lst.title,
            snapshot=snapshot,
            proposed=proposed,
            strategy=strat,
            diff=diff,
            confidence=confidence,
            rationale=rationale,
            impact_90=impact_90,
        )
        pol = self.policy.evaluate_review_item(item, lst)
        warns = list(pol.warnings)
        pa: PricingAction | None = None
        if "pricing_recommendation" in diff:
            from ebay_claw.models.domain import PricingRecommendation

            pr = PricingRecommendation.model_validate(diff["pricing_recommendation"])
            pa = pr.recommended_action
        pw = self.policy.should_warn_premium_early_discount(
            lst, pa if pa is not None else PricingAction.HOLD
        )
        if pw:
            warns.append(pw)

        cr = self.compliance.check_listing(lst)

        upd: dict = {
            "policy_flags": pol.blocked_reasons,
            "policy_warnings": warns,
            "compliance_issues": list(cr.issues),
            "compliance_warnings": [
                i.message for i in cr.issues if i.severity != ComplianceSeverity.INFO
            ],
            "compliance_checked_at": cr.checked_at_utc,
        }
        if not pol.allowed:
            upd.update(
                build_transition_update(
                    item,
                    ReviewStatus.REJECTED,
                    now=utc_now(),
                    actor=self._s.default_actor,
                    settings=self._s,
                )
            )
        else:
            upd["status"] = ReviewStatus.PENDING

        updated = item.model_copy(update=upd)
        self.queue.add(updated)
        if updated.status == ReviewStatus.REJECTED:
            OperationalHistoryStore(settings=self._s).append_event(
                "queue_rejected",
                source="pipeline",
                listing_id=updated.listing_id,
                review_item_id=updated.id,
                actor=self._s.default_actor,
                payload={
                    "proposed_action_type": updated.proposed_action_type.value,
                    "reason": "policy_or_automated_reject_on_enqueue",
                },
                occurred_at_utc=datetime.now(timezone.utc),
            )
        return updated

    def run_pipeline(
        self,
        as_of: Optional[date] = None,
    ) -> Tuple[List[ReviewQueueItem], List[ListingRecord]]:
        today = as_of or date.today()
        listings = self.load_listings(
            as_of=today, record_operational_movement=True
        )
        created: List[ReviewQueueItem] = []

        for lst in listings:
            analysis = self.enriched_analysis(lst, as_of=today)
            strat = self.scorer.score(lst, analysis, as_of=today).recommended_strategy
            title_s = self.title_agent.suggest(lst)
            spec_s = self.specifics_agent.suggest(lst)
            price_r = self.pricing.recommend(lst, analysis, strat)

            snap = lst.model_dump()
            snap["days_active"] = analysis.days_active

            if title_s.suggested_title.strip().lower() != lst.title.strip().lower():
                diff = {
                    "title_before": lst.title,
                    "title_after": title_s.suggested_title,
                }
                created.append(
                    self._enqueue_item(
                        lst,
                        snap,
                        strat,
                        ProposedActionType.UPDATE_TITLE,
                        diff,
                        title_s.confidence,
                        title_s.rationale,
                        "Improves discovery; verify keywords against garment.",
                    )
                )

            if spec_s.proposed_additions or spec_s.proposed_corrections:
                from ebay_claw.policies.safe_inventory_specifics import (
                    safe_inventory_patch_from_specifics_suggestion,
                )

                safe_patch = safe_inventory_patch_from_specifics_suggestion(spec_s)
                if safe_patch:
                    diff = {
                        "specifics_before": spec_s.existing_specifics,
                        "additions": [a.model_dump() for a in spec_s.proposed_additions],
                        "corrections": [c.model_dump() for c in spec_s.proposed_corrections],
                        "safe_inventory_specifics_patch": safe_patch,
                    }
                    created.append(
                        self._enqueue_item(
                            lst,
                            snap,
                            strat,
                            ProposedActionType.UPDATE_SAFE_SPECIFICS,
                            diff,
                            spec_s.overall_confidence,
                            "Fill high-confidence, whitelist-only inventory specifics (safe pass).",
                            "Better structured data improves matching.",
                        )
                    )

            pa = price_r.recommended_action
            if pa in (
                PricingAction.HOLD,
                PricingAction.REVIEW,
                PricingAction.IMPROVE_TITLE,
                PricingAction.FILL_SPECIFICS,
            ):
                continue

            prop = _pricing_to_proposed(pa)
            diff = {"pricing_recommendation": price_r.model_dump()}
            if prop == ProposedActionType.MARKDOWN_LISTING:
                pct = _markdown_pct(pa)
                diff["markdown_pct"] = pct
                diff["price_before"] = lst.price_amount
                diff["price_after"] = round(lst.price_amount * (1 - pct / 100.0), 2)

            created.append(
                self._enqueue_item(
                    lst,
                    snap,
                    strat,
                    prop,
                    diff,
                    price_r.confidence,
                    price_r.explanation,
                    price_r.expected_effect_on_90_day_sell_through,
                )
            )

        return created, listings

    def run_recovery_proposals(self, as_of: Optional[date] = None) -> List[ReviewQueueItem]:
        """
        Proposal-only queue rows: relist candidates and bundle lots. No marketplace execution.
        """
        from ebay_claw.analytics.bundle_identifier import identify_bundle_candidates
        from ebay_claw.analytics.relist_accelerator import (
            build_relist_proposal,
            is_relist_candidate,
        )

        today = as_of or date.today()
        listings = self.load_listings(
            as_of=today, record_operational_movement=True
        )
        created: List[ReviewQueueItem] = []
        hist = OperationalHistoryStore(settings=self._s)

        for lst in listings:
            analysis = self.enriched_analysis(lst, as_of=today)
            sc = self.scorer.score(lst, analysis, as_of=today)
            if not is_relist_candidate(lst, analysis, sc):
                continue
            rp = build_relist_proposal(lst, analysis, sc, settings=self._s)
            diff = {"relist_proposal": rp.model_dump(mode="json")}
            snap = lst.model_dump()
            snap["days_active"] = analysis.days_active
            row = self._enqueue_item(
                lst,
                snap,
                StrategicPath.END_AND_SELL_SIMILAR,
                ProposedActionType.RELIST_CANDIDATE,
                diff,
                rp.confidence,
                rp.why_relist_recommended[:500],
                rp.why_relist_vs_markdown_hold_bundle[:280],
            )
            created.append(row)
            if row.status != ReviewStatus.REJECTED:
                hist.append_event(
                    "relist_proposed",
                    source="recovery",
                    listing_id=lst.listing_id,
                    review_item_id=row.id,
                    payload={
                        "proposed_action_type": ProposedActionType.RELIST_CANDIDATE.value,
                    },
                )

        for br in identify_bundle_candidates(listings, as_of=today):
            primary = next((l for l in listings if l.listing_id == br.listing_ids[0]), None)
            if primary is None:
                continue
            analysis = self.enriched_analysis(primary, as_of=today)
            strat = self.scorer.score(primary, analysis, as_of=today).recommended_strategy
            diff = {"bundle_recommendation": br.model_dump(mode="json")}
            snap = primary.model_dump()
            snap["days_active"] = analysis.days_active
            snap["bundle_member_ids"] = br.listing_ids
            row = self._enqueue_item(
                primary,
                snap,
                strat,
                ProposedActionType.BUNDLE_LOT_CANDIDATE,
                diff,
                br.confidence,
                br.rationale,
                "Multi-SKU lot — proposal only; operator builds the lot off-eBay.",
            )
            created.append(row)
            if row.status != ReviewStatus.REJECTED:
                hist.append_event(
                    "bundle_proposed",
                    source="recovery",
                    listing_id=primary.listing_id,
                    review_item_id=row.id,
                    payload={
                        "proposed_action_type": ProposedActionType.BUNDLE_LOT_CANDIDATE.value,
                        "bundle_id": br.bundle_id,
                        "member_listing_ids": br.listing_ids,
                    },
                )

        return created

    def guarded_apply_service(self):
        """Audited simulated apply — re-fetches live listing, identity + policy gates, MockExecutor."""
        from ebay_claw.services.guarded_apply import build_guarded_apply_for_orchestrator

        return build_guarded_apply_for_orchestrator(
            settings=self._s,
            queue=self.queue,
            load_listings=self.load_listings,
        )

    def simulate_guarded_apply(self, review_item_id: str, *, actor: str):
        """Convenience: run guarded apply pipeline (mock execution only; no real eBay writes)."""
        return self.guarded_apply_service().apply_approved_item(review_item_id, actor=actor)
