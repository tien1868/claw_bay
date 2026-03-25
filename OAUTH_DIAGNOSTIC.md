# eBay Sandbox OAuth Diagnostic Tool

**Purpose:** Test your eBay Sandbox OAuth credentials before running the full application.

This is a standalone troubleshooting tool that verifies whether your Sandbox Client ID, Client Secret, and Refresh Token are valid by attempting a real OAuth token exchange with eBay's Sandbox endpoint.

**No listings are read or modified** - this tool only tests authentication.

---

## Quick Start

### 1. Set Environment Variables

Make sure your `api.env` file contains Sandbox credentials:

```env
EBAY_CLAW_EBAY_USE_SANDBOX=true
EBAY_CLAW_EBAY_CLIENT_ID=YOUR_SANDBOX_CLIENT_ID
EBAY_CLAW_EBAY_CLIENT_SECRET=YOUR_SANDBOX_CLIENT_SECRET
EBAY_CLAW_EBAY_REFRESH_TOKEN=YOUR_SANDBOX_REFRESH_TOKEN
```

**Important:** All three credentials must be from the **same Sandbox application**.

---

### 2. Run the Diagnostic

**Windows PowerShell:**
```powershell
python scripts/test_ebay_sandbox_oauth.py
```

**Bash/Mac/Linux:**
```bash
python scripts/test_ebay_sandbox_oauth.py
```

---

## Sample Output

### ✅ Success (PASS)

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
Access Token:   v^1.1#...#t^Ul4x

Your Sandbox OAuth credentials are VALID.
You can proceed with running the Claw Bay application.

======================================================================
```

---

### ❌ Failure (invalid_grant)

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

---

## Common Issues

### Invalid Grant Error

**Problem:** `error_bucket: invalid_grant`

**Causes:**
- Refresh token from **Production** used with **Sandbox** credentials (or vice versa)
- Refresh token from a **different eBay app**
- Client ID/Secret don't match the app that issued the token
- Refresh token expired or revoked

**Fix:**
1. Go to https://developer.ebay.com/my/keys
2. Verify you're on the **Sandbox Keys** tab
3. Copy your Sandbox Client ID and Client Secret
4. Go to https://developer.ebay.com/my/auth/
5. Select your **Sandbox** application
6. Choose **Sandbox** environment
7. Select scope: `https://api.ebay.com/oauth/api_scope/sell.inventory.readonly`
8. Authorize and get a new refresh token
9. Update all three values in `api.env`

**Critical:** Client ID, Client Secret, and Refresh Token must all be from the **same Sandbox app**.

---

### Authorization Header Invalid

**Problem:** `error_bucket: auth_header_invalid`

**Causes:**
- Client ID or Client Secret is incorrect
- Using Production credentials with Sandbox endpoint

**Fix:**
1. Verify Client ID from: https://developer.ebay.com/my/keys (Sandbox tab)
2. Verify Client Secret from same page
3. Ensure `EBAY_CLAW_EBAY_USE_SANDBOX=true` in api.env

---

### Missing Environment Variables

**Problem:** Script exits with "Missing required environment variables"

**Fix:**
1. Create `api.env` if it doesn't exist:
   ```powershell
   cp api.env.sandbox.template api.env
   ```

2. Edit `api.env` with your actual Sandbox credentials

3. Verify the file is in the repository root directory (same folder as `README.md`)

---

### Network Error

**Problem:** `error_bucket: network_error` or `timeout`

**Causes:**
- No internet connection
- Firewall blocking eBay API
- eBay Sandbox temporarily down

**Fix:**
1. Check your internet connection
2. Try accessing https://api.sandbox.ebay.com in a browser
3. Wait a few minutes and try again
4. Check if corporate firewall is blocking the request

---

## Advanced Usage

### Show Configuration (Masked)

View your current configuration without making an API call:

```powershell
python scripts/test_ebay_sandbox_oauth.py --show-config
```

Output:
```
======================================================================
eBay OAuth Configuration
======================================================================

Environment:     Sandbox
Client ID:       MyApp-Agent-...d3e7b
Client Secret:   PRD-...48cf
Refresh Token:   v^1.1#...#t^Ul4x
OAuth Scope:     https://api.ebay.com/oauth/api_scope/sell.inventory.readonly

======================================================================
```

---

## How It Works

1. **Reads Environment Variables:**
   - `EBAY_CLAW_EBAY_CLIENT_ID`
   - `EBAY_CLAW_EBAY_CLIENT_SECRET`
   - `EBAY_CLAW_EBAY_REFRESH_TOKEN`
   - `EBAY_CLAW_EBAY_USE_SANDBOX`
   - `EBAY_CLAW_EBAY_OAUTH_SCOPE`

2. **Constructs OAuth Request:**
   - Endpoint: `https://api.sandbox.ebay.com/identity/v1/oauth2/token`
   - Method: `POST`
   - Headers: `Authorization: Basic <base64(client_id:client_secret)>`
   - Body: `grant_type=refresh_token&refresh_token=...&scope=...`

3. **Tests Token Exchange:**
   - Sends request to eBay Sandbox
   - Attempts to get a new access token

4. **Reports Results:**
   - **Success:** Shows token type, expiration, masked access token
   - **Failure:** Classifies error and provides specific remediation steps

---

## Security Notes

### What Gets Masked

The diagnostic tool **never prints full secrets**:

- ✅ **Client ID:** Shows prefix/suffix for identification (e.g., `MyApp-...7b`)
- ✅ **Client Secret:** Shows first/last 4 chars (e.g., `PRD-...48cf`)
- ✅ **Refresh Token:** Shows first/last 6 chars (e.g., `v^1.1#...#Ul4x`)
- ✅ **Access Token:** Shows first/last 6 chars (e.g., `v^1.1#...#t^Ul`)

### Safe to Share

You can safely share the diagnostic output:
- Screenshots of PASS/FAIL results
- Error classifications
- Masked credential previews

**Do NOT share:**
- Your full `api.env` file
- Complete Client Secret
- Complete Refresh Token
- Complete Access Token

---

## Troubleshooting Checklist

Run through this checklist if the diagnostic fails:

- [ ] `EBAY_CLAW_EBAY_USE_SANDBOX=true` is set
- [ ] All three credentials (Client ID, Secret, Token) are from **Sandbox Keys** tab
- [ ] All three credentials are from the **same eBay app**
- [ ] Refresh token was generated **after** creating the Sandbox app
- [ ] OAuth scope includes `sell.inventory.readonly` (or `sell.inventory` for writes)
- [ ] `api.env` file exists in repository root
- [ ] No typos in environment variable names
- [ ] Virtual environment is activated
- [ ] Internet connection is working

---

## Get Help

If the diagnostic continues to fail after following all troubleshooting steps:

1. Run with `--show-config` to verify configuration (masked)
2. Check the **Error Type** and follow the specific remediation steps
3. Review `SANDBOX_SETUP.md` for detailed OAuth setup instructions
4. Verify your Sandbox app exists at: https://developer.ebay.com/my/keys

---

## Related Documentation

- **SANDBOX_SETUP.md** - Complete Sandbox setup guide
- **QUICKSTART_SANDBOX.md** - 5-minute quick start
- **eBay OAuth Guide** - https://developer.ebay.com/api-docs/static/oauth-tokens.html
- **eBay Sandbox Guide** - https://developer.ebay.com/api-docs/static/gs_create-a-sandbox-user.html

---

**Status:** Diagnostic tool ready
**Purpose:** OAuth credential validation only
**Risk:** None (read-only authentication test)
