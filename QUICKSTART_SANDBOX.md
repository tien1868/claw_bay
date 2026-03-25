# 🚀 Quick Start - eBay Sandbox Testing

**Get Claw Bay running in Sandbox mode in 5 minutes.**

---

## Prerequisites

- Python 3.10+
- eBay Developer Account with Sandbox access
- Git (repository already cloned)

---

## Step 1: Get eBay Sandbox Credentials (5 minutes)

### A. Get Sandbox App Keys

1. Go to https://developer.ebay.com/my/keys
2. Select your application
3. Click **Sandbox Keys** tab
4. Copy:
   - **Client ID** (App ID)
   - **Client Secret** (Cert ID)

### B. Create Sandbox Test User

1. Go to https://developer.ebay.com/my/account/sandbox
2. Click **Create a test user**
3. Create a **Seller** account
4. Remember the test user credentials

### C. Get OAuth Refresh Token

**Option 1: Use eBay OAuth Tool (Easiest)**

1. Go to https://developer.ebay.com/my/auth/
2. Select your **Sandbox** application
3. Choose **Sandbox** environment
4. Select scope: `https://api.ebay.com/oauth/api_scope/sell.inventory.readonly`
5. Sign in with your Sandbox test user
6. Copy the **refresh token**

**Done!** You now have:
- Client ID
- Client Secret
- Refresh Token

---

## Step 2: Setup Environment (2 minutes)

```powershell
# Create virtual environment
python -m venv .venv

# Activate (Windows PowerShell)
.venv\Scripts\Activate.ps1

# Or bash/WSL
source .venv/bin/activate

# Install Claw Bay
pip install -e ".[dev]"
```

---

## Step 3: Test OAuth Credentials (Optional but Recommended)

**Before configuring the full app**, verify your credentials work:

```powershell
# Copy template
cp api.env.sandbox.template api.env

# Edit with your Sandbox credentials
notepad api.env

# Test OAuth
python scripts/test_ebay_sandbox_oauth.py
```

If you see **[PASS]**, your credentials are valid. If **[FAIL]**, follow the remediation steps.

See `OAUTH_DIAGNOSTIC.md` for detailed troubleshooting.

---

## Step 4: Configure Sandbox Credentials (1 minute)

```powershell
# Copy template
cp api.env.sandbox.template api.env

# Edit api.env with your credentials
notepad api.env
```

Replace these lines:
```env
EBAY_CLAW_EBAY_CLIENT_ID=YOUR_SANDBOX_CLIENT_ID_HERE
EBAY_CLAW_EBAY_CLIENT_SECRET=YOUR_SANDBOX_CLIENT_SECRET_HERE
EBAY_CLAW_EBAY_REFRESH_TOKEN=YOUR_SANDBOX_REFRESH_TOKEN_HERE
```

With your actual Sandbox credentials:
```env
EBAY_CLAW_EBAY_CLIENT_ID=YourActual-Sandbox-ClientID
EBAY_CLAW_EBAY_CLIENT_SECRET=YOUR-ACTUAL-SANDBOX-SECRET
EBAY_CLAW_EBAY_REFRESH_TOKEN=v^1.1#i^1#...your_actual_token
```

Save and close.

---

## Step 5: Add Sandbox Inventory (Optional)

If your Sandbox test user has no listings:

1. Go to https://www.sandbox.ebay.com/sh/ovw
2. Sign in with your Sandbox test user
3. Create a few test listings (clothing items recommended)

Or just use fixture mode (Claw Bay will use sample data).

---

## Step 6: Run Claw Bay (30 seconds)

**Windows PowerShell:**
```powershell
.\run_sandbox.ps1
```

**Bash/WSL/Mac:**
```bash
./run_sandbox.sh
```

**Or manually:**
```powershell
streamlit run ebay_claw/app/streamlit_app.py
```

---

## Step 7: Verify Sandbox Connection

1. Dashboard opens at http://localhost:8501

2. **Check Adapter Info:**
   - `runtime_mode: live_read_only` ✅
   - `ebay_use_sandbox: true` ✅
   - `data_source: live` ✅

3. **Sync Listings:**
   - Click **Sync Listings** button
   - Should authenticate with Sandbox
   - Listings load from your Sandbox test user

4. **Verify Read-Only:**
   - Go to **Queue** tab
   - Try to **Apply** a change
   - Should block with "read-only mode" ✅

---

## Troubleshooting

### OAuth 401 Error

**Problem:** Unauthorized when syncing

**Diagnostic:**
```powershell
python scripts/test_ebay_sandbox_oauth.py
```

**Fix:**
- Run the OAuth diagnostic tool (see above)
- Follow the specific remediation steps it provides
- Verify you copied the **Sandbox** Client ID/Secret (not production)
- Confirm `EBAY_CLAW_EBAY_USE_SANDBOX=true` in api.env
- Get a fresh refresh token from https://developer.ebay.com/my/auth/

See `OAUTH_DIAGNOSTIC.md` for detailed troubleshooting.

### No Listings Found

**Problem:** Sync succeeds but no listings show

**Fix:**
- Add test listings to your Sandbox Seller Hub: https://www.sandbox.ebay.com/sh/ovw
- Or switch to fixture mode: `EBAY_CLAW_RUNTIME_MODE=fixture` in api.env

### Import Error

**Problem:** `ModuleNotFoundError: No module named 'streamlit'`

**Fix:**
```powershell
pip install -e ".[dev]"
```

### Wrong Python Version

**Problem:** `requires python >=3.10`

**Fix:**
- Install Python 3.10 or newer
- Recreate venv: `python3.10 -m venv .venv`

---

## What's Next?

### After Local Sandbox Works:

1. **Test Different Scenarios:**
   - Stale listings (90+ days old)
   - High-value items
   - Premium brands
   - Low watcher count

2. **Review Queue Behavior:**
   - See what the agents propose
   - Check policy warnings
   - Review confidence scores

3. **Read Audit Logs:**
   - `.ebay_claw_sandbox_audit.jsonl` - Events
   - `.ebay_claw_sandbox_policy.log` - Policy decisions

### Phase 2: Sandbox Guarded Write (Later)

After you're comfortable with read-only:

1. See `SANDBOX_SETUP.md` → Phase 2
2. Enable guarded write mode
3. Test ONE mutation at a time
4. Verify in Sandbox Seller Hub

### Phase 3: AWS Deployment (Later)

Deploy to AWS App Runner in Sandbox mode:
- Use AWS Secrets Manager for credentials
- Keep `live_read_only` initially
- Monitor CloudWatch logs

---

## Security Checklist

- [x] Using Sandbox credentials (not production)
- [x] `EBAY_CLAW_EBAY_USE_SANDBOX=true`
- [x] `EBAY_CLAW_RUNTIME_MODE=live_read_only`
- [x] All write flags OFF
- [x] `api.env` is gitignored

---

## Quick Commands

```powershell
# Start dashboard
.\run_sandbox.ps1

# Run tests
pytest tests -v

# Check for secrets
python check_secrets.py

# View logs
tail -f .ebay_claw_sandbox_audit.jsonl

# Stop dashboard
Ctrl + C
```

---

## Resources

- **Full Setup Guide:** `SANDBOX_SETUP.md`
- **Security Guide:** `SECURITY_CLEARANCE.md`
- **Main README:** `README.md`
- **eBay Sandbox Docs:** https://developer.ebay.com/api-docs/static/sandbox-test-users.html

---

**You're ready to test!** 🎉

Start with: `.\run_sandbox.ps1` (Windows) or `./run_sandbox.sh` (Mac/Linux)
