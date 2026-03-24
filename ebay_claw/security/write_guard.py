"""Single shared mutation guard — all execution/write paths must call this (fail-closed)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ebay_claw.config.settings import Settings


class WriteForbiddenError(RuntimeError):
    """Raised when a mutation or execution path is not permitted."""


def assert_write_mutation_allowed(settings: "Settings", *, caller: str = "unknown") -> None:
    """
    Raises WriteForbiddenError unless the runtime mode and config explicitly allow mutations.

    - fixture: always blocked
    - live_read_only: always blocked (live ingest only)
    - live_guarded_write: blocked unless guarded_write_enabled (writes still require executor gates)
    """
    from ebay_claw.models.runtime_mode import ClawRuntimeMode

    mode = settings.runtime_mode
    if isinstance(mode, ClawRuntimeMode):
        m = mode.value
    else:
        m = str(mode)

    if m == ClawRuntimeMode.FIXTURE.value:
        raise WriteForbiddenError(
            f"[{caller}] Mutations forbidden in fixture mode (server runtime_mode=fixture)."
        )
    if m == ClawRuntimeMode.LIVE_READ_ONLY.value:
        raise WriteForbiddenError(
            f"[{caller}] Mutations forbidden in live_read_only mode (fail-closed read-only)."
        )
    if m == ClawRuntimeMode.LIVE_GUARDED_WRITE.value:
        if not settings.guarded_write_enabled:
            raise WriteForbiddenError(
                f"[{caller}] live_guarded_write requires EBAY_CLAW_GUARDED_WRITE_ENABLED=true "
                "(writes are not enabled)."
            )
        return
    raise WriteForbiddenError(f"[{caller}] Unknown runtime_mode={m!r} (fail-closed).")


def is_mutation_precluded_by_mode(settings: "Settings") -> bool:
    """True when mode alone blocks any mutation (convenience for logging/tests)."""
    try:
        assert_write_mutation_allowed(settings, caller="probe")
        return False
    except WriteForbiddenError:
        return True


def allows_live_ingest(settings: "Settings") -> bool:
    from ebay_claw.models.runtime_mode import ClawRuntimeMode

    return settings.runtime_mode != ClawRuntimeMode.FIXTURE
