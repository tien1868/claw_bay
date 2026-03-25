#!/usr/bin/env python3
"""
eBay Sandbox OAuth Diagnostic Tool

Tests whether your Sandbox OAuth credentials are valid by attempting
a refresh token exchange against the eBay Sandbox token endpoint.

This is a troubleshooting tool only - no listings are read or modified.

Usage:
    python scripts/test_ebay_sandbox_oauth.py
    python scripts/test_ebay_sandbox_oauth.py --show-config
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from typing import Optional

import httpx

from ebay_claw.config.settings import get_settings


def mask_secret(value: Optional[str], show_chars: int = 4) -> str:
    """Mask a secret, showing only first/last few characters."""
    if not value or not value.strip():
        return "[NOT SET]"
    v = value.strip()
    if len(v) <= show_chars * 2:
        return "*" * len(v)
    return f"{v[:show_chars]}...{v[-show_chars:]}"


def mask_client_id(client_id: Optional[str]) -> str:
    """Mask client ID - show prefix for identification."""
    if not client_id or not client_id.strip():
        return "[NOT SET]"
    v = client_id.strip()
    # eBay client IDs often have format like "AppName-AppId-SBX-..."
    # Show enough to identify but not full value
    if len(v) <= 20:
        return v[:8] + "..." + v[-4:]
    return v[:12] + "..." + v[-6:]


def classify_oauth_error(status_code: int, response_text: str) -> tuple[str, str]:
    """
    Classify OAuth error into a bucket with helpful explanation.

    Returns: (error_bucket, explanation)
    """
    try:
        error_data = json.loads(response_text)
        error_code = error_data.get("error", "unknown")
        error_desc = error_data.get("error_description", "")
    except Exception:
        error_code = "unknown"
        error_desc = response_text[:200]

    # Classify based on error code and status
    if error_code == "invalid_grant":
        explanation = (
            "The refresh token is invalid, expired, revoked, or was issued "
            "to a different client. Common causes:\n"
            "  - Refresh token from Production used with Sandbox credentials\n"
            "  - Refresh token from different eBay app\n"
            "  - Refresh token expired or revoked\n"
            "  - Client ID/Secret don't match the app that issued the token"
        )
        return "invalid_grant", explanation

    if status_code == 401:
        if "authorization header" in error_desc.lower():
            explanation = (
                "Authorization header invalid. Common causes:\n"
                "  - Client ID or Client Secret is incorrect\n"
                "  - Wrong format for Basic auth encoding\n"
                "  - Sandbox credentials used with Production endpoint (or vice versa)"
            )
            return "auth_header_invalid", explanation

        explanation = (
            "Authentication failed (401). Check that your Client ID and "
            "Client Secret are correct for the Sandbox environment."
        )
        return "auth_failed", explanation

    if status_code == 400:
        explanation = (
            f"Bad request (400): {error_desc}\n"
            "Check that all required parameters are present and valid."
        )
        return "bad_request", explanation

    if status_code >= 500:
        explanation = (
            f"eBay server error ({status_code}). This is usually temporary.\n"
            "Try again in a few moments."
        )
        return "server_error", explanation

    if "network" in error_desc.lower() or "connection" in error_desc.lower():
        explanation = "Network error connecting to eBay. Check your internet connection."
        return "network_error", explanation

    explanation = f"OAuth error ({status_code}): {error_desc}"
    return "other_oauth_error", explanation


def run_oauth_flow_test(
    client_id: str,
    client_secret: str,
    refresh_token: str,
    use_sandbox: bool,
    oauth_scope: str,
) -> dict:
    """
    Test OAuth refresh token flow against eBay.

    Returns a result dictionary with:
    - success: bool
    - endpoint: str
    - status_code: Optional[int]
    - error_bucket: Optional[str]
    - error_explanation: Optional[str]
    - token_type: Optional[str]
    - expires_in: Optional[int]
    - access_token_preview: Optional[str]
    - scope: str
    """
    # Determine endpoint
    if use_sandbox:
        base = "https://api.sandbox.ebay.com/identity/v1"
        env_name = "Sandbox"
    else:
        base = "https://api.ebay.com/identity/v1"
        env_name = "Production"

    url = f"{base}/oauth2/token"

    # Build Basic auth header
    auth_string = f"{client_id}:{client_secret}"
    encoded = base64.b64encode(auth_string.encode("utf-8")).decode("ascii")

    headers = {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "scope": oauth_scope,
    }

    result = {
        "success": False,
        "endpoint": url,
        "environment": env_name,
        "scope": oauth_scope,
        "status_code": None,
        "error_bucket": None,
        "error_explanation": None,
        "token_type": None,
        "expires_in": None,
        "access_token_preview": None,
    }

    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(url, headers=headers, data=data)

        result["status_code"] = response.status_code

        if response.status_code == 200:
            payload = response.json()
            result["success"] = True
            result["token_type"] = payload.get("token_type", "Bearer")
            result["expires_in"] = payload.get("expires_in")

            # Mask access token
            access_token = payload.get("access_token", "")
            if access_token:
                result["access_token_preview"] = mask_secret(access_token, show_chars=6)

        else:
            # Classify error
            bucket, explanation = classify_oauth_error(
                response.status_code,
                response.text
            )
            result["error_bucket"] = bucket
            result["error_explanation"] = explanation

    except httpx.TimeoutException:
        result["error_bucket"] = "timeout"
        result["error_explanation"] = (
            "Request timed out after 30 seconds. Check your network connection."
        )
    except httpx.NetworkError as e:
        result["error_bucket"] = "network_error"
        result["error_explanation"] = f"Network error: {str(e)}"
    except Exception as e:
        result["error_bucket"] = "unexpected_error"
        result["error_explanation"] = f"Unexpected error: {type(e).__name__}: {str(e)}"

    return result


def print_config(settings) -> None:
    """Print current configuration (masked)."""
    print("=" * 70)
    print("eBay OAuth Configuration")
    print("=" * 70)
    print()
    print(f"Environment:     {'Sandbox' if settings.ebay_use_sandbox else 'Production'}")
    print(f"Client ID:       {mask_client_id(settings.ebay_client_id)}")
    print(f"Client Secret:   {mask_secret(settings.ebay_client_secret, show_chars=4)}")
    print(f"Refresh Token:   {mask_secret(settings.ebay_refresh_token, show_chars=6)}")
    print(f"OAuth Scope:     {settings.ebay_oauth_scope}")
    print()

    # Check for missing values
    issues = []
    if not settings.ebay_client_id:
        issues.append("- Client ID is not set")
    if not settings.ebay_client_secret:
        issues.append("- Client Secret is not set")
    if not settings.ebay_refresh_token:
        issues.append("- Refresh Token is not set")

    if issues:
        print("MISSING VALUES:")
        for issue in issues:
            print(issue)
        print()

    print("=" * 70)


def print_result(result: dict) -> None:
    """Print test result in a clear format."""
    print()
    print("=" * 70)
    print("eBay OAuth Diagnostic Result")
    print("=" * 70)
    print()

    if result["success"]:
        print("[PASS] OAuth refresh token exchange successful!")
        print()
        print(f"Endpoint:       {result['endpoint']}")
        print(f"Environment:    {result['environment']}")
        print(f"Scope:          {result['scope']}")
        print(f"Token Type:     {result['token_type']}")
        print(f"Expires In:     {result['expires_in']} seconds")
        print(f"Access Token:   {result['access_token_preview']}")
        print()
        print("Your Sandbox OAuth credentials are VALID.")
        print("You can proceed with running the Claw Bay application.")
    else:
        print("[FAIL] OAuth refresh token exchange failed")
        print()
        print(f"Endpoint:       {result['endpoint']}")
        print(f"Environment:    {result['environment']}")
        print(f"Scope:          {result['scope']}")
        if result["status_code"]:
            print(f"HTTP Status:    {result['status_code']}")
        print(f"Error Type:     {result['error_bucket']}")
        print()
        print("Explanation:")
        print(result["error_explanation"])
        print()
        print("What to do:")

        if result["error_bucket"] == "invalid_grant":
            print("  1. Verify you're using Sandbox credentials (not Production)")
            print("  2. Get a new refresh token from:")
            print("     https://developer.ebay.com/my/auth/ (select Sandbox environment)")
            print("  3. Ensure Client ID, Client Secret, and Refresh Token are from the SAME app")
            print("  4. Check that EBAY_CLAW_EBAY_USE_SANDBOX=true in your api.env")
        elif result["error_bucket"] == "auth_header_invalid":
            print("  1. Check your Client ID and Client Secret are correct")
            print("  2. Verify they are from the Sandbox Keys tab at:")
            print("     https://developer.ebay.com/my/keys")
            print("  3. Ensure EBAY_CLAW_EBAY_USE_SANDBOX=true")
        elif result["error_bucket"] in ("network_error", "timeout"):
            print("  1. Check your internet connection")
            print("  2. Try again in a few moments")
            print("  3. Check if eBay Sandbox is accessible")
        else:
            print("  1. Review the error explanation above")
            print("  2. Check your api.env configuration")
            print("  3. See SANDBOX_SETUP.md for detailed setup instructions")

    print()
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(
        description="Test eBay Sandbox OAuth credentials",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/test_ebay_sandbox_oauth.py
  python scripts/test_ebay_sandbox_oauth.py --show-config

Environment Variables:
  EBAY_CLAW_EBAY_CLIENT_ID        - Sandbox Client ID (App ID)
  EBAY_CLAW_EBAY_CLIENT_SECRET    - Sandbox Client Secret (Cert ID)
  EBAY_CLAW_EBAY_REFRESH_TOKEN    - Sandbox Refresh Token
  EBAY_CLAW_EBAY_USE_SANDBOX      - Must be 'true' for this tool
  EBAY_CLAW_EBAY_OAUTH_SCOPE      - OAuth scope (optional)

Get Sandbox credentials:
  Keys:   https://developer.ebay.com/my/keys (Sandbox tab)
  Token:  https://developer.ebay.com/my/auth/ (Sandbox environment)
        """
    )

    parser.add_argument(
        "--show-config",
        action="store_true",
        help="Show current configuration (masked) without testing"
    )

    args = parser.parse_args()

    print()
    print("eBay Sandbox OAuth Diagnostic Tool")
    print()

    # Load settings
    try:
        settings = get_settings()
    except Exception as e:
        print(f"[ERROR] Failed to load settings: {e}")
        print()
        print("Make sure you have created api.env with your Sandbox credentials.")
        print("See SANDBOX_SETUP.md for instructions.")
        sys.exit(1)

    # Show config if requested
    if args.show_config:
        print_config(settings)
        sys.exit(0)

    # Validate environment
    if not settings.ebay_use_sandbox:
        print("[ERROR] This diagnostic requires Sandbox mode")
        print()
        print("Set EBAY_CLAW_EBAY_USE_SANDBOX=true in your api.env")
        print()
        print("This tool is for testing Sandbox credentials only.")
        print("Do not use Production credentials with this diagnostic.")
        sys.exit(1)

    # Check for missing credentials
    missing = []
    if not settings.ebay_client_id:
        missing.append("EBAY_CLAW_EBAY_CLIENT_ID")
    if not settings.ebay_client_secret:
        missing.append("EBAY_CLAW_EBAY_CLIENT_SECRET")
    if not settings.ebay_refresh_token:
        missing.append("EBAY_CLAW_EBAY_REFRESH_TOKEN")

    if missing:
        print("[ERROR] Missing required environment variables:")
        for var in missing:
            print(f"  - {var}")
        print()
        print("Set these in your api.env file.")
        print("See SANDBOX_SETUP.md for detailed setup instructions.")
        sys.exit(1)

    print("Testing OAuth refresh token exchange...")
    print(f"Environment: Sandbox")
    print(f"Client ID:   {mask_client_id(settings.ebay_client_id)}")
    print()

    # Run the test
    result = run_oauth_flow_test(
        client_id=settings.ebay_client_id,
        client_secret=settings.ebay_client_secret,
        refresh_token=settings.ebay_refresh_token,
        use_sandbox=settings.ebay_use_sandbox,
        oauth_scope=settings.ebay_oauth_scope,
    )

    # Print result
    print_result(result)

    # Exit with appropriate code
    sys.exit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
