# eBay Claw

AI-assisted **eBay clothing resale** operations focused on **90-day sell-through**, **margin protection**, and **human-in-the-loop** listing changes.

## Principles

- All proposed listing mutations go through a **review queue** before execution.
- The execution layer is **off by default** (`EBAY_CLAW_EXECUTION_ENABLED=false`).
- Domain logic is decoupled from eBay wire formats via **normalized models** and **adapters** (`fixture` JSON or **read-only** eBay Inventory API).
- **Live ingest is read-only**: only `GET` Inventory calls; execution never writes to eBay when `data_source=live`.


## Setup

```bash
cd claw_bay
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -e ".[dev]"
copy .env.example .env   # optional; defaults work for fixtures
```

## Run the dashboard

```bash
streamlit run ebay_claw/app/streamlit_app.py
```

Set `EBAY_CLAW_FIXTURE_PATH` to point at your JSON fixture if not using the default `fixtures/sample_listings.json`.

## Live eBay (read-only Inventory API)

1. Create an eBay Developer app and complete the **authorization code** grant once to obtain a **refresh token** with scope `sell.inventory.readonly` (see eBay OAuth docs).
2. Set environment variables (or `.env`):

| Variable | Purpose |
|----------|---------|
| `EBAY_CLAW_DATA_SOURCE` | `fixture` (default) or `live` |
| `EBAY_CLAW_EBAY_ACCESS_TOKEN` | Optional short-lived bearer; if omitted, refresh is used |
| `EBAY_CLAW_EBAY_CLIENT_ID` / `EBAY_CLAW_EBAY_CLIENT_SECRET` | App credentials |
| `EBAY_CLAW_EBAY_REFRESH_TOKEN` | Long-lived refresh token |
| `EBAY_CLAW_EBAY_USE_SANDBOX` | `true` for sandbox API hosts |
| `EBAY_CLAW_EBAY_MARKETPLACE_ID` | e.g. `EBAY_US` |

3. If `data_source=live` but OAuth is incomplete, **`build_listing_adapter` falls back to fixtures** and the dashboard shows a warning.

4. **Sync**: `ClawOrchestrator.load_listings()` calls the adapter (fixture file read or eBay GETs). Status and timestamps are written to `EBAY_CLAW_SYNC_STATE_PATH` (no tokens).

5. **Execution**: `MockExecutor` **refuses success** when `data_source=live` so listing mutations cannot run against production from this MVP.

## Security & environments

| `EBAY_CLAW_CLAW_ENVIRONMENT` | Purpose |
|-----------------------------|--------|
| `sandbox` | Aligns with sandbox API hosts; can unlock **mock** execution for testing when `READ_ONLY_MODE=false`. |
| `production_read_only` | Default-safe: live ingest allowed, **all writes blocked**. |
| `production_guarded_write` | Requires `GUARDED_WRITE_ENABLED=true`, `APPROVED` queue items, policy re-check, dry-run ack, audit logging. |

- **Secrets:** use `ebay_claw.security.redaction` patterns; OAuth errors are redacted.  
- **Policy:** human-readable `.log` + structured `.jsonl` + **audit** `.jsonl` append-only.  
- **Compliance:** placeholder `EbayComplianceBoundary` — replace with official Compliance API when ready.

See [IMPLEMENTATION_REPORT.md](IMPLEMENTATION_REPORT.md) for the full security/compliance notes.

## Run tests


```bash
pytest tests -v
```

## Run the analysis pipeline (populate review queue)

From Python:

```python
from ebay_claw.services.orchestrator import ClawOrchestrator
orch = ClawOrchestrator()
items, listings = orch.run_pipeline()
```

Review items are persisted to `EBAY_CLAW_REVIEW_QUEUE_PATH` (default `.ebay_claw_review_queue.json`). Policy events append to `EBAY_CLAW_POLICY_LOG_PATH`.

## Project layout

| Area | Role |
|------|------|
| `ebay_claw/models/` | Pydantic schemas (`ListingRecord`, analysis, strategy, agents, queue, metrics) |
| `ebay_claw/adapters/` | `ListingAdapter`, fixtures, **read-only** REST Inventory client + normalizer |
| `ebay_claw/analytics/` | Inventory analyst, strategy scorer, store metrics |
| `ebay_claw/agents/` | Title, specifics, pricing (rule-driven MVP) |
| `ebay_claw/policies/` | Safety guardrails + logging |
| `ebay_claw/review_queue/` | JSON-backed queue |
| `ebay_claw/execution/` | Mock executor (real eBay later) |
| `ebay_claw/services/` | Ingestion, orchestrator, dashboard aggregates |
| `ebay_claw/app/` | Streamlit UI |
| `fixtures/` | Sample listings JSON |
| `tests/` | Business-logic tests |

## Safe repository publishing

**⚠️ This repository should ONLY be pushed to a PRIVATE GitHub repository.**

### Before first push

1. **Verify secrets are not committed:**
   ```bash
   python check_secrets.py
   ```

2. **Check git status:**
   ```bash
   git status
   ```
   Ensure no `.env`, `api.env`, `*.jsonl`, or `*.log` files are staged.

3. **Required after cloning:**
   - Copy `.env.example` to `api.env` or `.env`
   - Add your actual API keys and tokens
   - **NEVER commit these files** — they are in `.gitignore`

### What NOT to commit

- `api.env` / `.env` — contains real API keys and secrets
- `*.jsonl` — operational logs (sync history, audit trails, idempotency stores)
- `*.log` — runtime logs
- `.ebay_claw_*` state files — local sync state, review queue
- Any files with real OAuth tokens, client secrets, or access keys

### If secrets are accidentally exposed

1. **Rotate immediately:**
   - eBay: regenerate client ID/secret at [eBay Developer Portal](https://developer.ebay.com/)
   - AWS: deactivate and create new access keys in IAM Console
   - Google/Gemini: regenerate API key in Google Cloud Console
   - OpenAI: revoke and create new API key
   - Other APIs: follow provider's key rotation process

2. **Remove from git history** (if already pushed):
   ```bash
   # Use git filter-repo or BFG Repo-Cleaner
   # See: https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository
   ```

3. **Update your local `.env` / `api.env` with new credentials**

### Pre-push checklist

- [ ] Run `python check_secrets.py` — should show ✅ no secrets detected
- [ ] Run `git status` — no forbidden files staged
- [ ] Verify destination is a **private** GitHub repository
- [ ] Confirm `.gitignore` is present and properly configured
- [ ] Ensure no hardcoded credentials in code/docs/tests

## Roadmap (recommended)

1. **Comps / market signals** — sold listings or third-party comp data for price position.
2. **LLM title/specifics** — optional; keep deterministic fallback and policy checks.
3. **Controlled execution** — separate, audited write layer (still not enabled by default).
4. **Trading/legacy listings** — sellers not on Inventory API may need alternate read paths.
5. **Multi-store / operator roles** — auth, audit export.

See [IMPLEMENTATION_REPORT.md](IMPLEMENTATION_REPORT.md) for what is implemented vs. remaining gaps.
