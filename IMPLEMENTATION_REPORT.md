# eBay Claw — implementation report

## Security, compliance & rollout (latest)

### Files added

- `ebay_claw/security/redaction.py` — redact secrets from strings and nested dicts for logs/audit.
- `ebay_claw/security/read_only.py` — `READ_ONLY_MODE` / environment write blocking (`WriteForbiddenError`).
- `ebay_claw/security/config_validation.py` — `validate_settings`, `validate_settings_or_raise`.
- `ebay_claw/security/policy_structured.py` — append-only JSONL policy decisions.
- `ebay_claw/models/audit.py` — `AuditEvent` schema.
- `ebay_claw/audit/store.py` — append-only audit JSONL (redacted payloads).
- `ebay_claw/compliance/boundary.py` — **placeholder** compliance seam (official API only; no scraping).
- Tests: `test_secret_redaction.py`, `test_read_only_enforcement.py`, `test_execution_sandbox_success.py`, `test_policy_material_low_confidence.py`, `test_api_budget.py`, `test_audit_append.py`, `test_rate_limit_retry.py`.

### Files modified

- `ebay_claw/config/settings.py` — `claw_environment`, `read_only_mode`, guarded-write gates, API budget/cache, audit/policy paths, `strict_config`, `get_settings` env alignment.
- `ebay_claw/models/domain.py` — `ReviewQueueItem.compliance_*`, `dry_run_acknowledged`, `approved_by`.
- `ebay_claw/policies/safety.py` — structured + audit policy logs; brand/category/material confidence blocks.
- `ebay_claw/execution/mock_executor.py` — read-only / environment / guarded-write / compliance gates; audit on attempts & results.
- `ebay_claw/adapters/ebay_readonly_http.py` — per-run **budget**, optional **TTL cache**, capped retries, redacted errors.
- `ebay_claw/adapters/ebay_rest.py` — builds client with budget/cache; logs usage summary.
- `ebay_claw/adapters/ebay_oauth.py` — redacted OAuth errors.
- `ebay_claw/services/orchestrator.py` — compliance check on enqueue.
- `ebay_claw/services/dashboard_api.py` — rollout fields in `adapter_info`, `compliance_summary`.
- `ebay_claw/app/streamlit_app.py` — security + compliance panels.
- `ebay_claw/security/__init__.py` — slim exports (avoid import cycles).
- `.env.example` — rollout variables.

### Security architecture (short)

- **Config:** single `Settings` via `get_settings()`; optional `STRICT_CONFIG` raises on `validate_settings` errors.
- **Secrets:** never logged; `redact_string` / `redact_mapping` on OAuth errors, API logs, audit writes.
- **Writes:** `read_only_mode` defaults **true**; `production_read_only` forces read-only in `get_settings()` alignment; executor blocks live production regardless; sandbox allows **mock** success only when explicitly unlocked and policy re-check passes.
- **API:** official Inventory REST **GET** only; per-run call **budget**, optional cache, retry cap, 429/5xx backoff.

### Compliance architecture (short)

- **`EbayComplianceBoundary`** is the only intended compliance integration point; MVP is heuristic warnings; **no** HTML/scraping.
- Pipeline runs compliance on enqueue; warnings stored on `ReviewQueueItem`; dashboard rolls up counts; **production_guarded_write** blocks apply if warnings remain.

### Remaining risks

- Heuristic compliance ≠ eBay Compliance API; policy/listing rules can drift from live platform policy.
- `dry_run_acknowledged` / `approved_by` are fields only — UI must set them for real guarded rollout.
- Audit JSONL is file-based — protect filesystem permissions in production.

### Recommended rollout order

1. `sandbox` + fixtures → validate dashboards and policy logs.  
2. `sandbox` + live sandbox OAuth → validate ingest + budgets only.  
3. `production_read_only` + production OAuth → operate indefinitely without writes.  
4. `production_guarded_write` + `GUARDED_WRITE_ENABLED` + manual approval workflow + audit review → pilot mock apply, then real write adapter (future).

---

## Latest update: read-only eBay + queue versioning

### Files added (this update)

- `ebay_claw/adapters/read_only.py` — read-only HTTP guard helpers; safe error strings.
- `ebay_claw/adapters/ebay_oauth.py` — OAuth refresh + `resolve_access_token`; sandbox/production identity hosts.
- `ebay_claw/adapters/ebay_readonly_http.py` — `ReadOnlyEbayInventoryClient` (GET-only, retries, 429/5xx backoff).
- `ebay_claw/adapters/ebay_normalize.py` — inventory item + offer JSON → `ListingRecord` (no raw payload on domain objects).
- `ebay_claw/adapters/ebay_rest.py` — `EbayInventoryListingAdapter` (pagination, per-SKU offers, sync state).
- `ebay_claw/adapters/factory.py` — `build_listing_adapter(settings)` (live vs fixture + OAuth fallback).
- `ebay_claw/models/sync_state.py` — `SyncState` schema for last ingest.
- `ebay_claw/review_queue/fingerprint.py` — snapshot fingerprint for stale detection.
- `ebay_claw/services/sync_state.py` — JSON persistence for sync metadata.
- `tests/test_ebay_normalize.py`, `test_ebay_adapter_pagination.py`, `test_read_only.py`, `test_queue_dedupe.py`, `test_queue_stale_fingerprint.py`, `test_execution_live_readonly.py`.

### Files changed (this update)

- `ebay_claw/config/settings.py` — `data_source`, eBay OAuth/API tuning, `sync_state_path`.
- `ebay_claw/models/domain.py` — `ReviewStatus.SUPERSEDED`; queue item `version`, `superseded_by`, fingerprint, `is_stale_vs_live`.
- `ebay_claw/adapters/mock_json.py` — optional `SyncStateStore` updates for fixture loads.
- `ebay_claw/review_queue/store.py` — deduped `create` / `create_deduped`; `flag_stale_vs_live`.
- `ebay_claw/services/orchestrator.py` — `build_listing_adapter`, `data_source_override`, `load_listings` flags stale queue rows.
- `ebay_claw/services/dashboard_api.py` — `get_sync_state`, `adapter_info`.
- `ebay_claw/services/__init__.py` — emptied to avoid import cycles (import submodules directly).
- `ebay_claw/app/streamlit_app.py` — fixture/live toggle, sync panel, reload button.
- `ebay_claw/execution/mock_executor.py` — blocks successful apply when `data_source=live`.
- `ebay_claw/adapters/__init__.py` — exports `build_listing_adapter`.
- `pyproject.toml`, `requirements.txt`, `.env.example`, `README.md`.

### Architecture summary

1. **ListingAdapter** unchanged as the boundary; **fixture** adapter still returns `ListingRecord[]`.
2. **Live path:** `EbayInventoryListingAdapter` uses **only** `ReadOnlyEbayInventoryClient.get_json` against Inventory API (`/inventory_item` paginated, `/offer?sku=` per item). Responses are passed through **`ebay_normalize.merge_inventory_and_offer`** → **`ListingRecord`**. Raw eBay JSON is not stored on `ListingRecord` (only normalized fields + optional `extra` summaries like offer id / status).
3. **`build_listing_adapter`**: `live` + complete OAuth → REST adapter; otherwise **fixture** adapter (logged warning).
4. **Sync state** (`SyncStateStore`): `running` / `ok` / `error`, UTC timestamps, listing count, safe message (tokens redacted on errors).
5. **Review queue:** new pending row for the same `(listing_id, proposed_action_type)` **supersedes** older pending rows (`SUPERSEDED`, `superseded_by`, monotonic `version`). **`flag_stale_vs_live`** sets `is_stale_vs_live` when the current listing fingerprint ≠ fingerprint captured at enqueue.
6. **Execution:** `MockExecutor` **never succeeds** for `data_source=live`, independent of `execution_enabled`.

### Auth flow summary

1. App credentials: `EBAY_CLAW_EBAY_CLIENT_ID`, `EBAY_CLAW_EBAY_CLIENT_SECRET`.
2. Either set **`EBAY_CLAW_EBAY_ACCESS_TOKEN`** (user-managed refresh) or **`EBAY_CLAW_EBAY_REFRESH_TOKEN`** to obtain an access token via **`POST /identity/v1/oauth2/token`** (`grant_type=refresh_token`, scope `sell.inventory.readonly` by default).
3. **Sandbox:** `EBAY_CLAW_EBAY_USE_SANDBOX=true` switches **both** identity and REST API hosts to `api.sandbox.ebay.com`.
4. Tokens are **never** written to sync state or logged in full; errors from eBay responses are scrubbed before persistence.

### How live sync works

1. User sets `EBAY_CLAW_DATA_SOURCE=live` (or chooses **live** in Streamlit; settings are `model_copy`’d).
2. `fetch_active_listings` runs: marks sync **running**, then pages `GET /sell/inventory/v1/inventory_item` (`limit`/`offset` page index per eBay docs).
3. For each SKU, `GET /sell/inventory/v1/offer?sku=...`; keep **PUBLISHED** offers that are **ACTIVE** on the listing, preferring **FIXED_PRICE**.
4. Each pair (inventory row + offer) is normalized to **`ListingRecord`**; duplicates by `listing_id` are possible if multiple offers pass filters — rare.
5. On completion, sync state **ok** + counts; on exception, **error** + safe message, then re-raise so callers can surface failure.
6. **Every** `load_listings()` also runs **`flag_stale_vs_live`** against the current listing set.

### Known gaps

- **Inventory API only:** sellers using legacy flows without inventory items/offers are not covered; would need Trading/Browse or another read strategy.
- **Watchers / views:** not available from these Inventory reads; remain `None`.
- **Token refresh:** 401 handling reuses retry/backoff but does not yet force refresh-token rotation on expiry when a static access token is set.
- **Rate limits:** honors `Retry-After` on 429; no long-term quota analytics.
- **Offer selection:** multiple active fixed-price offers per SKU use all that pass filters (edge case).

### Next best step after read-only integration

**Comps or sold signals** (CSV, analytics export, or Marketing/Feed data) attached to `ListingRecord.extra` so pricing and stale risk use market position, not only age and listing quality.

---

## Original greenfield scope (historical)

Initial modules: models, analytics, agents, policies, mock execution, Streamlit, fixture adapter, tests. See git history for the full original file list.
