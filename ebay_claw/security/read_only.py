"""Backward-compatible read/write checks — prefer security.write_guard for new code."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ebay_claw.config.settings import Settings

from ebay_claw.security.write_guard import WriteForbiddenError, assert_write_mutation_allowed

__all__ = ["WriteForbiddenError", "assert_write_path_allowed", "is_write_blocked"]


def is_write_blocked(settings: Settings) -> bool:
    """True if central write guard blocks any mutation for this config."""
    try:
        assert_write_mutation_allowed(settings, caller="is_write_blocked")
    except WriteForbiddenError:
        return True
    return False


def assert_write_path_allowed(
    settings: Settings,
    *,
    reason: str = "",
) -> None:
    try:
        assert_write_mutation_allowed(settings, caller="assert_write_path_allowed")
    except WriteForbiddenError as e:
        msg = str(e)
        if reason:
            msg = f"{msg} Context: {reason}"
        raise WriteForbiddenError(msg) from e
