"""Tests for eBay Sandbox OAuth diagnostic tool."""

from __future__ import annotations

import json
from unittest.mock import Mock, patch

import httpx
import pytest

from scripts.test_ebay_sandbox_oauth import (
    classify_oauth_error,
    mask_client_id,
    mask_secret,
    run_oauth_flow_test,
)


def test_mask_secret():
    """Test secret masking shows only first/last chars."""
    assert mask_secret("abcdefghijklmnop", show_chars=4) == "abcd...mnop"
    assert mask_secret("short", show_chars=4) == "*****"  # Masked fully
    assert mask_secret("", show_chars=4) == "[NOT SET]"
    assert mask_secret(None, show_chars=4) == "[NOT SET]"
    assert mask_secret("   ", show_chars=4) == "[NOT SET]"


def test_mask_client_id():
    """Test client ID masking for identification."""
    assert "..." in mask_client_id("MyApp-12345-SBX-abcdefgh")
    assert mask_client_id("") == "[NOT SET]"
    assert mask_client_id(None) == "[NOT SET]"
    assert mask_client_id("   ") == "[NOT SET]"


def test_classify_oauth_error_invalid_grant():
    """Test classification of invalid_grant error."""
    response_text = json.dumps({
        "error": "invalid_grant",
        "error_description": "refresh token invalid"
    })

    bucket, explanation = classify_oauth_error(400, response_text)

    assert bucket == "invalid_grant"
    assert "invalid, expired, revoked" in explanation.lower()
    assert "different client" in explanation.lower()


def test_classify_oauth_error_auth_header():
    """Test classification of authorization header error."""
    response_text = json.dumps({
        "error": "invalid_client",
        "error_description": "Authorization header is malformed"
    })

    bucket, explanation = classify_oauth_error(401, response_text)

    assert bucket == "auth_header_invalid"
    assert "authorization header" in explanation.lower()


def test_classify_oauth_error_server_error():
    """Test classification of server errors."""
    bucket, explanation = classify_oauth_error(500, "Internal Server Error")

    assert bucket == "server_error"
    assert "500" in explanation


def test_classify_oauth_error_network():
    """Test classification of network errors."""
    bucket, explanation = classify_oauth_error(0, "network connection failed")

    assert bucket == "network_error"
    assert "network" in explanation.lower()


def test_classify_oauth_error_unknown():
    """Test classification of unknown errors."""
    bucket, explanation = classify_oauth_error(418, "I'm a teapot")

    assert bucket == "other_oauth_error"
    assert "418" in explanation


@patch("scripts.test_ebay_sandbox_oauth.httpx.Client")
def test_oauth_flow_success_case(mock_client_class):
    """Test successful OAuth flow."""
    # Mock successful response
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "access_token": "v^1.1#i^1#f^0#p^3#r^0#I^3#t^Ul4xMF84OjEwMzBGQzNCNEE0RDQwQzU4QzQ1NTdBQkIzNzRBRkYw...",
        "expires_in": 7200,
        "token_type": "Bearer"
    }

    mock_client = Mock()
    mock_client.post.return_value = mock_response
    mock_client_class.return_value.__enter__.return_value = mock_client

    result = run_oauth_flow_test(
        client_id="TestApp-SBX-12345",
        client_secret="SBX-12345-secret",
        refresh_token="v^1.1#i^1#test_refresh_token",
        use_sandbox=True,
        oauth_scope="https://api.ebay.com/oauth/api_scope/sell.inventory.readonly"
    )

    assert result["success"] is True
    assert result["status_code"] == 200
    assert result["token_type"] == "Bearer"
    assert result["expires_in"] == 7200
    assert result["access_token_preview"] is not None
    assert "..." in result["access_token_preview"]  # Masked
    assert result["environment"] == "Sandbox"


@patch("scripts.test_ebay_sandbox_oauth.httpx.Client")
def test_oauth_flow_invalid_grant_case(mock_client_class):
    """Test OAuth flow with invalid_grant error."""
    # Mock invalid_grant response
    mock_response = Mock()
    mock_response.status_code = 400
    mock_response.text = json.dumps({
        "error": "invalid_grant",
        "error_description": "refresh token invalid"
    })

    mock_client = Mock()
    mock_client.post.return_value = mock_response
    mock_client_class.return_value.__enter__.return_value = mock_client

    result = run_oauth_flow_test(
        client_id="TestApp-SBX-12345",
        client_secret="SBX-12345-secret",
        refresh_token="invalid_token",
        use_sandbox=True,
        oauth_scope="https://api.ebay.com/oauth/api_scope/sell.inventory.readonly"
    )

    assert result["success"] is False
    assert result["status_code"] == 400
    assert result["error_bucket"] == "invalid_grant"
    assert "invalid, expired, revoked" in result["error_explanation"].lower()


@patch("scripts.test_ebay_sandbox_oauth.httpx.Client")
def test_oauth_flow_auth_header_invalid_case(mock_client_class):
    """Test OAuth flow with invalid auth header."""
    # Mock 401 auth error
    mock_response = Mock()
    mock_response.status_code = 401
    mock_response.text = json.dumps({
        "error": "invalid_client",
        "error_description": "Authorization header is invalid"
    })

    mock_client = Mock()
    mock_client.post.return_value = mock_response
    mock_client_class.return_value.__enter__.return_value = mock_client

    result = run_oauth_flow_test(
        client_id="WrongClient",
        client_secret="WrongSecret",
        refresh_token="token",
        use_sandbox=True,
        oauth_scope="https://api.ebay.com/oauth/api_scope/sell.inventory.readonly"
    )

    assert result["success"] is False
    assert result["status_code"] == 401
    assert result["error_bucket"] == "auth_header_invalid"


@patch("scripts.test_ebay_sandbox_oauth.httpx.Client")
def test_oauth_flow_network_error_case(mock_client_class):
    """Test OAuth flow with network error."""
    mock_client = Mock()
    mock_client.post.side_effect = httpx.NetworkError("Connection refused")
    mock_client_class.return_value.__enter__.return_value = mock_client

    result = run_oauth_flow_test(
        client_id="TestApp",
        client_secret="secret",
        refresh_token="token",
        use_sandbox=True,
        oauth_scope="https://api.ebay.com/oauth/api_scope/sell.inventory.readonly"
    )

    assert result["success"] is False
    assert result["error_bucket"] == "network_error"
    assert "network error" in result["error_explanation"].lower()


@patch("scripts.test_ebay_sandbox_oauth.httpx.Client")
def test_oauth_flow_timeout_case(mock_client_class):
    """Test OAuth flow with timeout."""
    mock_client = Mock()
    mock_client.post.side_effect = httpx.TimeoutException("Timeout")
    mock_client_class.return_value.__enter__.return_value = mock_client

    result = run_oauth_flow_test(
        client_id="TestApp",
        client_secret="secret",
        refresh_token="token",
        use_sandbox=True,
        oauth_scope="https://api.ebay.com/oauth/api_scope/sell.inventory.readonly"
    )

    assert result["success"] is False
    assert result["error_bucket"] == "timeout"
    assert "timed out" in result["error_explanation"].lower()


def test_oauth_flow_uses_correct_endpoint():
    """Test that sandbox/production endpoints are correctly selected."""
    with patch("scripts.test_ebay_sandbox_oauth.httpx.Client") as mock_client_class:
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "token",
            "expires_in": 7200,
            "token_type": "Bearer"
        }

        mock_client = Mock()
        mock_client.post.return_value = mock_response
        mock_client_class.return_value.__enter__.return_value = mock_client

        # Test Sandbox endpoint
        result = run_oauth_flow_test(
            client_id="TestApp",
            client_secret="secret",
            refresh_token="token",
            use_sandbox=True,
            oauth_scope="scope"
        )
        assert "sandbox.ebay.com" in result["endpoint"]
        assert result["environment"] == "Sandbox"

        # Test Production endpoint
        result = run_oauth_flow_test(
            client_id="TestApp",
            client_secret="secret",
            refresh_token="token",
            use_sandbox=False,
            oauth_scope="scope"
        )
        assert "sandbox" not in result["endpoint"]
        assert result["environment"] == "Production"
