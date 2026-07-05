"""Tests for core.auth.anthropic_oauth."""

import json
import time

import pytest

from core.auth.anthropic_oauth import (
    ANTHROPIC_OAUTH_CLIENT_ID,
    build_authorize_url,
    credentials_from_value,
    credentials_to_dict,
    is_oauth_token,
    parse_authorization_input,
)
from core.auth.utils import now_ms

pytestmark = pytest.mark.core


def test_build_authorize_url_contains_expected_params():
    url = build_authorize_url(challenge="CHAL", state="STATE")
    assert "claude.ai/oauth/authorize" in url
    assert "code_challenge=CHAL" in url
    assert "state=STATE" in url
    assert f"client_id={ANTHROPIC_OAUTH_CLIENT_ID}" in url
    assert "code_challenge_method=S256" in url
    assert "user%3Ainference" in url or "user:inference" in url


def test_parse_authorization_input_code_and_state():
    code, state = parse_authorization_input("mycode#mystate")
    assert code == "mycode"
    assert state == "mystate"


def test_parse_authorization_input_raw_code():
    code, state = parse_authorization_input("justcode")
    assert code == "justcode"
    assert state is None


def test_parse_authorization_input_empty():
    code, state = parse_authorization_input("")
    assert code is None
    assert state is None


def test_is_oauth_token_jwt():
    assert is_oauth_token("eyJhbGciOiJIUzI1NiJ9.payload.sig") is True


def test_is_oauth_token_setup_token():
    assert is_oauth_token("sk-ant-oat01-xxxx") is True


def test_is_oauth_token_claude_code():
    assert is_oauth_token("cc-abc123") is True


def test_is_oauth_token_regular_api_key():
    assert is_oauth_token("sk-ant-api03-xxxx") is False


def test_is_oauth_token_empty():
    assert is_oauth_token("") is False


def test_credentials_roundtrip():
    from core.auth.anthropic_oauth import AnthropicOAuthCredentials

    creds = AnthropicOAuthCredentials(
        access="tok",
        refresh="ref",
        expires_ms=now_ms() + 3600_000,
        source="test",
    )
    d = credentials_to_dict(creds)
    restored = credentials_from_value(d)
    assert restored is not None
    assert restored.access == "tok"
    assert restored.refresh == "ref"
    assert restored.source == "test"


def test_credentials_from_value_rejects_incomplete():
    assert credentials_from_value({"access": "tok"}) is None
    assert credentials_from_value(None) is None
    assert credentials_from_value("not json") is None


# ── Hexis-owned store only (no Claude Code / Keychain / env borrowing) ─────────

def test_resolve_uses_only_hexis_store(monkeypatch):
    """resolve_anthropic_token must ignore Claude Code creds and env tokens."""
    import asyncio

    from core.auth import anthropic_oauth as mod

    # A Claude Code login + env token both exist...
    monkeypatch.setattr(mod, "read_claude_code_credentials",
                        lambda: {"accessToken": "sk-ant-oat01-CLAUDECODE", "expiresAt": 0})
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-ENVTOKEN")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api-ENVKEY")

    # ...but Hexis's own store is empty → resolves to nothing.
    monkeypatch.setattr(mod, "load_credentials", lambda: None)
    monkeypatch.setattr("core.auth.anthropic_setup_token.load_credentials", lambda: None)
    token, mode = asyncio.run(mod.resolve_anthropic_token())
    assert token is None and mode == "", (token, mode)

    # With a Hexis-native token present, that (and only that) is returned.
    creds = mod.AnthropicOAuthCredentials(
        access="sk-ant-oat01-HEXISOWN", refresh="r", expires_ms=now_ms() + 3_600_000,
    )
    monkeypatch.setattr(mod, "load_credentials", lambda: creds)
    token, mode = asyncio.run(mod.resolve_anthropic_token())
    assert token == "sk-ant-oat01-HEXISOWN" and mode == "setup-token"
