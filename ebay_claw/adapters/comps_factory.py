"""Construct read-only sold-comps adapter from settings."""

from __future__ import annotations

from ebay_claw.adapters.comps_base import SoldCompsAdapter
from ebay_claw.adapters.comps_fixture import FixtureSoldCompsAdapter
from ebay_claw.adapters.comps_null import NullSoldCompsAdapter
from ebay_claw.config.settings import Settings


def build_sold_comps_adapter(settings: Settings) -> SoldCompsAdapter:
    if not settings.comps_enabled:
        return NullSoldCompsAdapter()
    if settings.comps_fixture_path and settings.comps_fixture_path.exists():
        return FixtureSoldCompsAdapter(settings.comps_fixture_path, settings)
    return NullSoldCompsAdapter()
