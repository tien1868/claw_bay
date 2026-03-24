"""Central gate: future apply paths must satisfy queue + policy discipline."""

from __future__ import annotations

from typing import TYPE_CHECKING, List

from ebay_claw.models.compliance_issue import ComplianceSeverity
from ebay_claw.models.domain import ReviewQueueItem, ReviewStatus
from ebay_claw.models.runtime_mode import ClawRuntimeMode
from ebay_claw.security.write_guard import WriteForbiddenError, assert_write_mutation_allowed

if TYPE_CHECKING:
    from ebay_claw.config.settings import Settings


class ApplyPreconditionError(RuntimeError):
    """Apply blocked — safe message for operators."""


def collect_state_machine_apply_blockers(settings: "Settings", item: ReviewQueueItem) -> List[str]:
    """Checks mirrored by assert_apply_state_machine_satisfied when mode is live_guarded_write."""
    blockers: List[str] = []
    if item.status != ReviewStatus.APPROVED:
        blockers.append("Apply requires queue status APPROVED.")
    else:
        if not item.reviewed_at or not item.approved_at:
            blockers.append(
                "Apply requires reviewed_at and approved_at (use queue transition API)."
            )
        if settings.require_manual_approval_for_write:
            if not (item.approved_by and str(item.approved_by).strip()):
                blockers.append("Apply requires approved_by (non-empty operator id).")
    if settings.require_dry_run_acknowledgement and not item.dry_run_acknowledged:
        blockers.append("Apply requires dry_run_acknowledged=True.")
    if item.is_stale_vs_live:
        blockers.append(
            "Queue row is stale_vs_live — re-run pipeline or refresh queue before apply."
        )
    if settings.compliance_checks_enabled:
        blocking = [
            i
            for i in item.compliance_issues
            if i.blocks_guarded_write or i.severity == ComplianceSeverity.BLOCKING
        ]
        if blocking:
            codes = ", ".join(sorted({i.code for i in blocking}))
            blockers.append(
                f"Compliance blocking issues present (guarded write blocked): {codes}. "
                "Resolve or update listing before apply."
            )
    return blockers


def assert_apply_state_machine_satisfied(settings: "Settings", item: ReviewQueueItem) -> None:
    """
    Validates workflow invariants for guarded write (and keeps read-only environments explicit).

    Call after assert_write_mutation_allowed and before policy/adapter work.
    """
    if settings.runtime_mode != ClawRuntimeMode.LIVE_GUARDED_WRITE:
        return

    blockers = collect_state_machine_apply_blockers(settings, item)
    if blockers:
        raise ApplyPreconditionError(blockers[0])


def list_apply_operator_blockers(
    settings: "Settings",
    item: ReviewQueueItem,
    *,
    policy_snapshot_verified: bool = False,
) -> List[str]:
    """
    Human-readable reasons guarded apply would fail, aligned with MockExecutor.apply order.
    Includes infrastructure (write guard, mode) and queue/compliance/policy signals.
    policy_snapshot_verified: set True only after PolicyEngine.must_pass_before_write succeeds.
    """
    out: List[str] = []
    try:
        assert_write_mutation_allowed(settings, caller="apply_readiness")
    except WriteForbiddenError as e:
        out.append(f"[write_guard] {e}")

    if not settings.execution_enabled:
        out.append("[executor] Execution disabled (EBAY_CLAW_EXECUTION_ENABLED=false).")

    if settings.runtime_mode == ClawRuntimeMode.LIVE_GUARDED_WRITE:
        if not settings.guarded_write_enabled:
            out.append("[executor] live_guarded_write requires GUARDED_WRITE_ENABLED=true.")
    else:
        out.append(
            f"[mode] runtime_mode is {settings.runtime_mode.value} — "
            "apply runs only in live_guarded_write."
        )

    if settings.runtime_mode == ClawRuntimeMode.LIVE_GUARDED_WRITE:
        for msg in collect_state_machine_apply_blockers(settings, item):
            out.append(f"[apply_guard] {msg}")
    else:
        qs = collect_state_machine_apply_blockers(settings, item)
        for msg in qs:
            out.append(f"[apply_guard:would_apply_if_guarded] {msg}")

    if item.policy_flags:
        shown = ", ".join(item.policy_flags[:10])
        out.append(f"[policy] policy_flags on item — cannot apply: {shown}")

    if settings.require_policy_pass_for_write and not policy_snapshot_verified:
        out.append(
            "[policy] require_policy_pass_for_write — live listing policy pre-check not "
            "verified in this preview (runs at apply time)."
        )

    return out


def executor_gate_blockers_before_policy(settings: "Settings", item: ReviewQueueItem) -> List[str]:
    """
    Infrastructure + queue gates for apply, excluding the live policy pre-check line.
    Used by GuardedApplyService: policy runs after live fetch with a fresh listing snapshot.
    """
    return [
        b
        for b in list_apply_operator_blockers(
            settings, item, policy_snapshot_verified=False
        )
        if not b.startswith("[policy]")
    ]


def apply_executor_ready(
    settings: "Settings",
    item: ReviewQueueItem,
    *,
    policy_snapshot_verified: bool = False,
) -> bool:
    """True when MockExecutor.apply would likely proceed past all local gates (same as guard logic)."""
    try:
        assert_write_mutation_allowed(settings, caller="apply_readiness")
    except WriteForbiddenError:
        return False
    if not settings.execution_enabled:
        return False
    if settings.runtime_mode != ClawRuntimeMode.LIVE_GUARDED_WRITE:
        return False
    if not settings.guarded_write_enabled:
        return False
    if collect_state_machine_apply_blockers(settings, item):
        return False
    if item.policy_flags:
        return False
    if settings.require_policy_pass_for_write and not policy_snapshot_verified:
        return False
    return True
