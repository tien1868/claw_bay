# 🧪 eBay Sandbox Testing Setup

**Goal:** Run Claw Bay locally against eBay Sandbox before any production use.

---

## Phase 1: Local Sandbox Read-Only

### Prerequisites

1. **eBay Developer Account** with Sandbox access
2. **Sandbox Application Keyset** (separate from production)
3. **Sandbox Test User** (virtual account for testing)
4. **Python 3.10+** and `venv`

---

## Step 1: Get eBay Sandbox Credentials

### A. Create Sandbox Application Keyset

1. Go to: https://developer.ebay.com/my/keys
2. Select your application
3. Switch to **Sandbox Keys** tab
4. Note your:
   - **Sandbox Client ID** (App ID)
   - **Sandbox Client Secret** (Cert ID)

**Important:** Sandbox and Production use completely separate keysets.

### B. Create Sandbox Test User

1. Go to: https://developer.ebay.com/my/account/sandbox
2. Click **Create a test user**
3. Create a seller account with inventory
4. Note the test user credentials

### C. Get OAuth Refresh Token (Sandbox)

eBay uses OAuth 2.0 for the Inventory API. You need a refresh token.

**Option 1: Use eBay's OAuth Tool**
1. Go to: https://developer.ebay.com/my/auth/
2. Select your Sandbox application
3. Select the **Sandbox** environment
4. Choose scope: `https://api.ebay.com/oauth/api_scope/sell.inventory.readonly`
5. Sign in with your **Sandbox test user**
6. Authorize and save the refresh token

**Option 2: OAuth Authorization Code Grant (Manual)**
```
https://auth.sandbox.ebay.com/oauth2/authorize
  ?client_id=YOUR_SANDBOX_CLIENT_ID
  &response_type=code
  &redirect_uri=YOUR_REDIRECT_URI
  &scope=https://api.ebay.com/oauth/api_scope/sell.inventory.readonly
```

Then exchange the code for a refresh token via:
```bash
curl -X POST 'https://api.sandbox.ebay.com/identity/v1/oauth2/token' \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -H 'Authorization: Basic BASE64(CLIENT_ID:CLIENT_SECRET)' \
  -d 'grant_type=authorization_code' \
  -d 'code=YOUR_AUTH_CODE' \
  -d 'redirect_uri=YOUR_REDIRECT_URI'
```

**Save the `refresh_token` from the response.**

---

## Step 2: Configure Local Environment

### Create `api.env` for Sandbox

```bash
cp .env.example api.env
```

Edit `api.env` with your Sandbox credentials:

```env
# ═══════════════════════════════════════════════════════════════════
# SANDBOX TESTING CONFIGURATION
# ═══════════════════════════════════════════════════════════════════

# Runtime mode: live_read_only (safe for testing)
EBAY_CLAW_RUNTIME_MODE=live_read_only

# eBay Sandbox credentials
EBAY_CLAW_EBAY_USE_SANDBOX=true
EBAY_CLAW_EBAY_CLIENT_ID=YOUR_SANDBOX_CLIENT_ID
EBAY_CLAW_EBAY_CLIENT_SECRET=YOUR_SANDBOX_CLIENT_SECRET
EBAY_CLAW_EBAY_REFRESH_TOKEN=YOUR_SANDBOX_REFRESH_TOKEN
EBAY_CLAW_EBAY_MARKETPLACE_ID=EBAY_US

# OAuth scope (read-only for now)
EBAY_CLAW_EBAY_OAUTH_SCOPE=https://api.ebay.com/oauth/api_scope/sell.inventory.readonly

# Write controls: ALL OFF for initial testing
EBAY_CLAW_EXECUTION_ENABLED=false
EBAY_CLAW_GUARDED_WRITE_ENABLED=false
EBAY_CLAW_EBAY_REAL_WRITES_ENABLED=false
EBAY_CLAW_APPLY_API_ALLOW_LIVE_EXECUTOR=false

# API budget (conservative for Sandbox)
EBAY_CLAW_API_BUDGET_MAX_CALLS_PER_RUN=50

# Logging
EBAY_CLAW_POLICY_LOG_PATH=.ebay_claw_sandbox_policy.log
EBAY_CLAW_AUDIT_LOG_PATH=.ebay_claw_sandbox_audit.jsonl
EBAY_CLAW_SYNC_HISTORY_PATH=.ebay_claw_sandbox_sync_history.jsonl

# Use default fixture paths (will fallback to these if Sandbox is empty)
EBAY_CLAW_FIXTURE_PATH=fixtures/sample_listings.json
```

**Security reminder:** `api.env` is gitignored. Never commit it.

---

## Step 3: Install and Run Locally

### Install Dependencies

```powershell
# Create virtual environment
python -m venv .venv

# Activate (Windows PowerShell)
.venv\Scripts\Activate.ps1

# Or Windows CMD
.venv\Scripts\activate.bat

# Install package with dev dependencies
pip install -e ".[dev]"
```

### Verify Installation

```powershell
pytest tests -v
```

Expected: All tests should pass.

---

## Step 4: Run Claw Bay Dashboard (Sandbox)

```powershell
streamlit run ebay_claw/app/streamlit_app.py
```

The dashboard should open at: http://localhost:8501

---

## Step 5: Verify Sandbox Connection

### What to Check:

1. **Adapter Info Panel**
   - Should show: `data_source: live`
   - Should show: `runtime_mode: live_read_only`
   - Should show: `ebay_use_sandbox: true`

2. **Sync Listings**
   - Click **Sync Listings** button
   - Watch for OAuth token refresh
   - Verify listings load from your Sandbox test user

3. **Dashboard Tabs**
   - **Overview:** Should show listing counts
   - **Listings:** Should display Sandbox inventory
   - **Queue:** Should show analysis results
   - **Security:** Should show read-only enforcement

4. **Check Logs**
   - `.ebay_claw_sandbox_audit.jsonl` should log sync events
   - `.ebay_claw_sandbox_policy.log` should show policy decisions

---

## Step 6: Test Read-Only Enforcement

### Verify Writes Are Blocked:

1. Navigate to **Queue** tab
2. Select a review item
3. Try to **Apply** a change
4. Should see: **"Blocked - read-only mode"**

This confirms the fail-safe write guards are working.

---

## Step 7: Troubleshooting

### OAuth Errors

**401 Unauthorized:**
- Check your Sandbox refresh token is valid
- Verify you're using Sandbox Client ID/Secret (not production)
- Confirm `EBAY_CLAW_EBAY_USE_SANDBOX=true`

**Scope errors:**
- Ensure OAuth scope includes `sell.inventory.readonly`
- Re-authorize if needed

### Empty Inventory

**No listings found:**
- Your Sandbox test user needs inventory
- Add test listings via Sandbox Seller Hub: https://www.sandbox.ebay.com/sh/ovw
- Or use fixture mode: `EBAY_CLAW_RUNTIME_MODE=fixture`

### Connection Issues

**Timeout or network errors:**
- Verify Sandbox API endpoint: `api.sandbox.ebay.com`
- Check `EBAY_CLAW_EBAY_HTTP_TIMEOUT_SEC` (default 60s)
- Review `EBAY_CLAW_EBAY_MAX_RETRIES` (default 5)

---

## Phase 2: Enable Sandbox Guarded Write (Later)

**Only after Phase 1 is stable.**

When ready to test the first write operation in Sandbox:

1. Update `api.env`:
```env
EBAY_CLAW_RUNTIME_MODE=live_guarded_write
EBAY_CLAW_EXECUTION_ENABLED=true
EBAY_CLAW_GUARDED_WRITE_ENABLED=true
EBAY_CLAW_EBAY_REAL_WRITES_ENABLED=true
EBAY_CLAW_APPLY_API_ALLOW_LIVE_EXECUTOR=true

# Update OAuth scope to allow writes
EBAY_CLAW_EBAY_OAUTH_SCOPE=https://api.ebay.com/oauth/api_scope/sell.inventory
```

2. **Re-authorize** with write scope (get new refresh token)

3. Test ONE change at a time:
   - Review queue item must be **APPROVED**
   - Item must have `dry_run_acknowledged=true`
   - Policy checks must pass
   - Audit log will record the attempt

4. Verify in Sandbox Seller Hub that the change applied

---

## Phase 3: AWS Sandbox Deployment (Next)

After local Sandbox testing is stable:
- Deploy to AWS App Runner in Sandbox mode
- Use AWS Secrets Manager for credentials
- Keep `live_read_only` initially
- Monitor CloudWatch logs

See `AWS_DEPLOYMENT.md` (to be created) for AWS setup.

---

## Security Checklist

- [x] Using Sandbox credentials (not production)
- [x] `EBAY_CLAW_EBAY_USE_SANDBOX=true`
- [x] `EBAY_CLAW_RUNTIME_MODE=live_read_only` (initially)
- [x] All write flags OFF
- [x] `api.env` is gitignored
- [x] Audit logging enabled
- [x] Read-only enforcement verified

---

## Quick Reference

### eBay Sandbox URLs
- **Seller Hub:** https://www.sandbox.ebay.com/sh/ovw
- **Developer Keys:** https://developer.ebay.com/my/keys
- **Test Users:** https://developer.ebay.com/my/account/sandbox
- **OAuth Tool:** https://developer.ebay.com/my/auth/

### API Endpoints
- **Sandbox REST:** `https://api.sandbox.ebay.com`
- **Sandbox OAuth:** `https://api.sandbox.ebay.com/identity/v1/oauth2/token`

### Documentation
- **Inventory API:** https://developer.ebay.com/api-docs/sell/inventory/overview.html
- **OAuth Guide:** https://developer.ebay.com/api-docs/static/oauth-tokens.html
- **Sandbox Guide:** https://developer.ebay.com/api-docs/static/gs_create-a-sandbox-user.html

---

## Next Steps

1. ✅ Get Sandbox credentials (Client ID, Secret, Refresh Token)
2. ✅ Configure `api.env` for Sandbox
3. ✅ Run locally: `streamlit run ebay_claw/app/streamlit_app.py`
4. ✅ Verify Sandbox listings load
5. ✅ Test read-only enforcement
6. ⏳ (Later) Enable Sandbox guarded write for ONE operation
7. ⏳ (Later) Deploy to AWS in Sandbox mode

---

**Status:** Ready for local Sandbox testing
**Risk Level:** Very Low (read-only, Sandbox environment)
**Next:** Get your Sandbox credentials and start testing!
