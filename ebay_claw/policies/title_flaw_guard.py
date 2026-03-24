"""Shared title disclosure guard — condition/flaw language must not be stripped."""

from __future__ import annotations

# Keep in sync with PolicyEngine UPDATE_TITLE checks.
FLAW_WORDS: tuple[str, ...] = (
    "flaw",
    "hole",
    "damage",
    "stain",
    "tear",
    "wear",
    "as is",
)


def title_flaw_disclosure_preserved(title_before: str, title_after: str) -> bool:
    """True when new title retains flaw/condition cues present in the prior title."""
    old_t = (title_before or "").lower()
    new_t = (title_after or "").lower()
    if any(w in old_t for w in FLAW_WORDS) and not any(w in new_t for w in FLAW_WORDS):
        return False
    return True
