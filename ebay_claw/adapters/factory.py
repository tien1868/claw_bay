"""Construct ListingAdapter from settings (fixture vs live). Fail-closed: no silent live→fixture fallback."""

from __future__ import annotations

from ebay_claw.adapters.base import ListingAdapter
from ebay_claw.adapters.ebay_oauth import live_credentials_configured
from ebay_claw.adapters.ebay_rest import EbayInventoryListingAdapter
from ebay_claw.adapters.mock_json import MockJsonListingAdapter
from ebay_claw.config.settings import Settings
from ebay_claw.logging_config import get_logger
from ebay_claw.models.runtime_mode import ClawRuntimeMode
from ebay_claw.security.write_guard import allows_live_ingest
from ebay_claw.services.sync_state import SyncStateStore

logger = get_logger(__name__)


def build_listing_adapter(settings: Settings) -> ListingAdapter:
    sync = SyncStateStore(settings=settings)
    if settings.runtime_mode == ClawRuntimeMode.FIXTURE:
        return MockJsonListingAdapter(settings.fixture_path, sync_store=sync)

    if not allows_live_ingest(settings):
        raise ValueError("Invalid runtime_mode for live ingest (fail-closed).")

    if not live_credentials_configured(settings):
        logger.error(
            "runtime_mode=%s requires complete eBay OAuth — refusing silent fixture fallback",
            settings.runtime_mode.value,
        )
        raise ValueError(
            "Fail-closed: live runtime_mode requires configured eBay OAuth "
            "(access_token or client_id+client_secret+refresh_token)."
        )
    return EbayInventoryListingAdapter(settings, sync_store=sync)
