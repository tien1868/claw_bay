"""Small authenticated seam for server-side apply — delegates only to ``GuardedApplyService``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ebay_claw.services.guarded_apply import GuardedApplyResult, GuardedApplyService

if TYPE_CHECKING:
    from ebay_claw.config.settings import Settings


class ApplyApiError(RuntimeError):
    """Misconfiguration or auth failure for the apply API seam."""


class ApplyApiService:
    """
    Opt-in HTTP/cron caller surface: ``EBAY_CLAW_APPLY_API_ENABLED`` + shared secret.

    Always routes through ``GuardedApplyService.apply_approved_item`` (never a bare executor).
    Default builds still use ``MockExecutor``; real eBay executor requires separate wiring + flags.
    """

    @staticmethod
    def invoke_apply(
        settings: "Settings",
        *,
        shared_secret: str,
        guarded: GuardedApplyService,
        review_item_id: str,
        actor: str,
    ) -> GuardedApplyResult:
        if not settings.apply_api_enabled:
            raise ApplyApiError("apply API is disabled (EBAY_CLAW_APPLY_API_ENABLED=false).")
        cfg = (settings.apply_api_shared_secret or "").strip()
        if not cfg:
            raise ApplyApiError("apply API shared secret is not configured.")
        if (shared_secret or "").strip() != cfg:
            raise ApplyApiError("apply API unauthorized: invalid secret.")

        return guarded.apply_approved_item(review_item_id, actor=actor)
