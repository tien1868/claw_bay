"""Canonical Claw runtime mode — single source of truth for ingest vs mutation policy."""

from __future__ import annotations

from enum import Enum


class ClawRuntimeMode(str, Enum):
    """Server-side mode; UI must not override."""

    FIXTURE = "fixture"
    LIVE_READ_ONLY = "live_read_only"
    LIVE_GUARDED_WRITE = "live_guarded_write"
