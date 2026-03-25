# OAuth Diagnostic Tool - Usage Examples

Quick reference for running the eBay Sandbox OAuth diagnostic tool.

---

## Basic Usage

### Test Your Credentials

```powershell
python scripts/test_ebay_sandbox_oauth.py
```

---

## Example Outputs

### ✅ Success (Valid Credentials)

```
eBay Sandbox OAuth Diagnostic Tool

Testing OAuth refresh token exchange...
Environment: Sandbox
Client ID:   MyApp-Agent-...d3e7b


======================================================================
eBay OAuth Diagnostic Result
======================================================================

[PASS] OAuth refresh token exchange successful!

Endpoint:       https://api.sandbox.ebay.com/identity/v1/oauth2/token
Environment:    Sandbox
Scope:          https://api.ebay.com/oauth/api_scope/sell.inventory.readonly
Token Type:     Bearer
Expires In:     7200 seconds
Access Token:   v^1.1#...#Ul4x

Your Sandbox OAuth credentials are VALID.
You can proceed with running the Claw Bay application.

======================================================================
```

**Exit Code:** 0

---

### ❌ Invalid Grant (Wrong Token)

```
eBay Sandbox OAuth Diagnostic Tool

Testing OAuth refresh token exchange...
Environment: Sandbox
Client ID:   MyApp-Agent-...d3e7b


======================================================================
eBay OAuth Diagnostic Result
======================================================================

[FAIL] OAuth refresh token exchange failed

Endpoint:       https://api.sandbox.ebay.com/identity/v1/oauth2/token
Environment:    Sandbox
Scope:          https://api.ebay.com/oauth/api_scope/sell.inventory.readonly
HTTP Status:    400
Error Type:     invalid_grant

Explanation:
The refresh token is invalid, expired, revoked, or was issued
to a different client. Common causes:
  - Refresh token from Production used with Sandbox credentials
  - Refresh token from different eBay app
  - Refresh token expired or revoked
  - Client ID/Secret don't match the app that issued the token

What to do:
  1. Verify you're using Sandbox credentials (not Production)
  2. Get a new refresh token from:
     https://developer.ebay.com/my/auth/ (select Sandbox environment)
  3. Ensure Client ID, Client Secret, and Refresh Token are from the SAME app
  4. Check that EBAY_CLAW_EBAY_USE_SANDBOX=true in your api.env

======================================================================
```

**Exit Code:** 1

---

### ❌ Authorization Header Invalid

```
======================================================================
eBay OAuth Diagnostic Result
======================================================================

[FAIL] OAuth refresh token exchange failed

Endpoint:       https://api.sandbox.ebay.com/identity/v1/oauth2/token
Environment:    Sandbox
Scope:          https://api.ebay.com/oauth/api_scope/sell.inventory.readonly
HTTP Status:    401
Error Type:     auth_header_invalid

Explanation:
Authorization header invalid. Common causes:
  - Client ID or Client Secret is incorrect
  - Wrong format for Basic auth encoding
  - Sandbox credentials used with Production endpoint (or vice versa)

What to do:
  1. Check your Client ID and Client Secret are correct
  2. Verify they are from the Sandbox Keys tab at:
     https://developer.ebay.com/my/keys
  3. Ensure EBAY_CLAW_EBAY_USE_SANDBOX=true

======================================================================
```

**Exit Code:** 1

---

### ❌ Missing Environment Variables

```
eBay Sandbox OAuth Diagnostic Tool

[ERROR] Missing required environment variables:
  - EBAY_CLAW_EBAY_CLIENT_ID
  - EBAY_CLAW_EBAY_CLIENT_SECRET
  - EBAY_CLAW_EBAY_REFRESH_TOKEN

Set these in your api.env file.
See SANDBOX_SETUP.md for detailed setup instructions.
```

**Exit Code:** 1

---

### ❌ Not in Sandbox Mode

```
eBay Sandbox OAuth Diagnostic Tool

[ERROR] This diagnostic requires Sandbox mode

Set EBAY_CLAW_EBAY_USE_SANDBOX=true in your api.env

This tool is for testing Sandbox credentials only.
Do not use Production credentials with this diagnostic.
```

**Exit Code:** 1

---

## PowerShell Environment Variable Setup

### Set Variables Directly (Temporary)

```powershell
$env:EBAY_CLAW_EBAY_USE_SANDBOX="true"
$env:EBAY_CLAW_EBAY_CLIENT_ID="YourSandboxClientID"
$env:EBAY_CLAW_EBAY_CLIENT_SECRET="YourSandboxClientSecret"
$env:EBAY_CLAW_EBAY_REFRESH_TOKEN="v^1.1#i^1#..."

python scripts/test_ebay_sandbox_oauth.py
```

**Note:** These only last for the current PowerShell session.

---

### Use api.env File (Recommended)

```powershell
# Create api.env
cp api.env.sandbox.template api.env

# Edit api.env with your credentials
notepad api.env

# Run diagnostic (reads from api.env automatically)
python scripts/test_ebay_sandbox_oauth.py
```

---

## Show Current Configuration

```powershell
python scripts/test_ebay_sandbox_oauth.py --show-config
```

**Output:**
```
======================================================================
eBay OAuth Configuration
======================================================================

Environment:     Sandbox
Client ID:       MyApp-Agent-...d3e7b
Client Secret:   PRD-...48cf
Refresh Token:   v^1.1#...#Ul4x
OAuth Scope:     https://api.ebay.com/oauth/api_scope/sell.inventory.readonly

======================================================================
```

---

## Integration with Sandbox Testing Workflow

### Step 1: Get Credentials
1. Go to https://developer.ebay.com/my/keys → Sandbox tab
2. Copy Client ID and Client Secret
3. Go to https://developer.ebay.com/my/auth/ → Sandbox environment
4. Get refresh token

### Step 2: Configure
```powershell
cp api.env.sandbox.template api.env
notepad api.env  # Paste credentials
```

### Step 3: Test Credentials
```powershell
python scripts/test_ebay_sandbox_oauth.py
```

### Step 4: If PASS, Run App
```powershell
.\run_sandbox.ps1
```

### Step 5: If FAIL, Fix Issues
- Follow the specific remediation steps in the diagnostic output
- See `OAUTH_DIAGNOSTIC.md` for detailed troubleshooting
- Re-run diagnostic after fixing

---

## Automation Example

### Pre-flight Check Script

```powershell
# pre_flight_check.ps1

Write-Host "Running OAuth pre-flight check..."

python scripts/test_ebay_sandbox_oauth.py

if ($LASTEXITCODE -eq 0) {
    Write-Host "✓ OAuth check passed - starting app..." -ForegroundColor Green
    .\run_sandbox.ps1
} else {
    Write-Host "✗ OAuth check failed - fix credentials before running app" -ForegroundColor Red
    exit 1
}
```

---

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success - credentials are valid |
| 1 | Failure - see output for specific error |

Use in scripts:
```powershell
python scripts/test_ebay_sandbox_oauth.py
if ($LASTEXITCODE -eq 0) {
    # Proceed
} else {
    # Handle error
}
```

---

## Related Commands

```powershell
# Run full test suite
pytest tests/test_oauth_diagnostic.py -v

# Check for secrets before push
python check_secrets.py

# Start Sandbox app
.\run_sandbox.ps1

# Run Streamlit manually
streamlit run ebay_claw/app/streamlit_app.py
```

---

**See Also:**
- `OAUTH_DIAGNOSTIC.md` - Full troubleshooting guide
- `SANDBOX_SETUP.md` - Complete Sandbox setup
- `QUICKSTART_SANDBOX.md` - 5-minute quick start
