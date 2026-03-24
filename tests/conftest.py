from datetime import date
from pathlib import Path

import pytest

from ebay_claw.config.settings import Settings
from ebay_claw.models.domain import ListingRecord


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        fixture_path=tmp_path / "x.json",
        review_queue_path=tmp_path / "q.json",
        policy_log_path=tmp_path / "policy.log",
        sync_history_path=tmp_path / "sync_history.jsonl",
        audit_log_path=tmp_path / "audit.jsonl",
    )


@pytest.fixture
def sample_listing() -> ListingRecord:
    return ListingRecord(
        listing_id="T1",
        title="cool shirt mens M blue cotton",
        price_amount=25.0,
        listed_on=date(2025, 1, 1),
        brand="Nike",
        size="M",
        department="Men",
        garment_type="T-Shirt",
        color="Blue",
        condition="Pre-owned - Good",
        description="A" * 100,
        item_specifics={"Brand": "Nike"},
        watchers=1,
    )
