from ebay_claw.security.redaction import redact_string


def test_redacts_bearer():
    s = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIn0.sig"
    out = redact_string(s)
    assert "Bearer [REDACTED]" in out or "[REDACTED]" in out
    assert "eyJ" not in out


def test_redacts_refresh_token_literal():
    s = "error refresh_token=abc123secret token expired"
    out = redact_string(s)
    assert "abc123secret" not in out


def test_redacts_basic_auth_header_value():
    s = "upstream said Basic dXNlcjpzdXBlcnNlY3JldA=="
    out = redact_string(s)
    assert "dXNlcjpzdXBlcnNlY3JldA==" not in out
