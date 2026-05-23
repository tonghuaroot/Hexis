"""Anthropic OAuth (PKCE) and Claude Code credential auto-detection.

Supports three credential sources (checked in priority order):
1. Hexis-native PKCE OAuth (stored in ~/.hexis/auth/)
2. Claude Code credentials (~/.claude/.credentials.json or macOS Keychain)
3. Anthropic setup token (delegated to anthropic_setup_token module)

The PKCE flow uses the same client ID and endpoints as Claude Code / pi-ai.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from core.auth.utils import advisory_lock_key, generate_pkce, needs_refresh, now_ms

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (from Claude Code / pi-ai / hermes-agent)
# ---------------------------------------------------------------------------

ANTHROPIC_OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
ANTHROPIC_OAUTH_AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
ANTHROPIC_OAUTH_TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
ANTHROPIC_OAUTH_REDIRECT_URI = "https://console.anthropic.com/oauth/code/callback"
ANTHROPIC_OAUTH_SCOPES = "org:create_api_key user:profile user:inference"

ANTHROPIC_OAUTH_TOKEN_ENDPOINTS = [
    "https://console.anthropic.com/v1/oauth/token",
    "https://platform.claude.com/v1/oauth/token",
]

ANTHROPIC_OAUTH_CONFIG_KEY = "oauth.anthropic"
_ANTHROPIC_OAUTH_LOCK_KEY = advisory_lock_key(ANTHROPIC_OAUTH_CONFIG_KEY)


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AnthropicOAuthCredentials:
    access: str
    refresh: str
    expires_ms: int
    source: str = "hexis_pkce"


# ---------------------------------------------------------------------------
# Token detection
# ---------------------------------------------------------------------------

def is_oauth_token(key: str) -> bool:
    """Check if a key is an Anthropic OAuth/setup token (not a regular API key)."""
    if not key:
        return False
    if key.startswith("sk-ant-api"):
        return False
    if key.startswith("sk-ant-"):
        return True
    if key.startswith("eyJ"):
        return True
    if key.startswith("cc-"):
        return True
    return False


# ---------------------------------------------------------------------------
# PKCE authorization URL
# ---------------------------------------------------------------------------

def build_authorize_url(*, challenge: str, state: str) -> str:
    params = {
        "code": "true",
        "client_id": ANTHROPIC_OAUTH_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": ANTHROPIC_OAUTH_REDIRECT_URI,
        "scope": ANTHROPIC_OAUTH_SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    return f"{ANTHROPIC_OAUTH_AUTHORIZE_URL}?{urlencode(params)}"


def parse_authorization_input(value: str) -> tuple[str | None, str | None]:
    """Accept code#state, full URL, or raw code."""
    v = (value or "").strip()
    if not v:
        return None, None

    if "#" in v:
        code, st = v.split("#", 1)
        return code or None, st or None

    return v, None


# ---------------------------------------------------------------------------
# Token exchange / refresh
# ---------------------------------------------------------------------------

async def exchange_authorization_code(
    *,
    code: str,
    verifier: str,
    state: str,
) -> AnthropicOAuthCredentials:
    body = {
        "grant_type": "authorization_code",
        "client_id": ANTHROPIC_OAUTH_CLIENT_ID,
        "code": code,
        "state": state,
        "redirect_uri": ANTHROPIC_OAUTH_REDIRECT_URI,
        "code_verifier": verifier,
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            ANTHROPIC_OAUTH_TOKEN_URL,
            headers={"Content-Type": "application/json"},
            json=body,
        )

    if resp.status_code < 200 or resp.status_code >= 300:
        raise RuntimeError(f"Anthropic token exchange failed: HTTP {resp.status_code}: {resp.text}")

    data = resp.json()
    access = data.get("access_token")
    refresh = data.get("refresh_token")
    expires_in = data.get("expires_in")
    if not isinstance(access, str) or not isinstance(refresh, str) or not isinstance(expires_in, (int, float)):
        raise RuntimeError("Anthropic token exchange failed: missing fields in response.")

    return AnthropicOAuthCredentials(
        access=access,
        refresh=refresh,
        expires_ms=now_ms() + int(expires_in * 1000),
        source="hexis_pkce",
    )


async def refresh_anthropic_token(refresh_token: str) -> AnthropicOAuthCredentials:
    """Refresh an Anthropic OAuth token, trying multiple endpoints."""
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": ANTHROPIC_OAUTH_CLIENT_ID,
    }

    last_exc: Exception | None = None
    for endpoint in ANTHROPIC_OAUTH_TOKEN_ENDPOINTS:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    endpoint,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    data=data,
                )
            if resp.status_code < 200 or resp.status_code >= 300:
                last_exc = RuntimeError(f"HTTP {resp.status_code}: {resp.text}")
                logger.debug("Anthropic token refresh failed at %s: %s", endpoint, last_exc)
                continue

            result = resp.json()
            access = result.get("access_token")
            if not isinstance(access, str) or not access:
                raise RuntimeError("Anthropic refresh response was missing access_token")

            return AnthropicOAuthCredentials(
                access=access,
                refresh=result.get("refresh_token", refresh_token),
                expires_ms=now_ms() + int(result.get("expires_in", 3600)) * 1000,
            )
        except RuntimeError:
            raise
        except Exception as exc:
            last_exc = exc
            logger.debug("Anthropic token refresh failed at %s: %s", endpoint, exc)
            continue

    raise last_exc or RuntimeError("Anthropic token refresh failed at all endpoints")


# ---------------------------------------------------------------------------
# Claude Code credential auto-detection
# ---------------------------------------------------------------------------

def _read_claude_code_keychain() -> dict[str, Any] | None:
    """Read Claude Code OAuth from macOS Keychain (Claude Code >= 2.1.114)."""
    if platform.system() != "Darwin":
        return None

    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    if result.returncode != 0 or not result.stdout.strip():
        return None

    try:
        data = json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        return None

    oauth_data = data.get("claudeAiOauth")
    if isinstance(oauth_data, dict) and oauth_data.get("accessToken"):
        return {
            "accessToken": oauth_data["accessToken"],
            "refreshToken": oauth_data.get("refreshToken", ""),
            "expiresAt": oauth_data.get("expiresAt", 0),
            "source": "macos_keychain",
        }
    return None


def _read_claude_code_file() -> dict[str, Any] | None:
    """Read Claude Code OAuth from ~/.claude/.credentials.json."""
    cred_path = Path.home() / ".claude" / ".credentials.json"
    try:
        data = json.loads(cred_path.read_text(encoding="utf-8"))
        oauth_data = data.get("claudeAiOauth")
        if isinstance(oauth_data, dict) and oauth_data.get("accessToken"):
            return {
                "accessToken": oauth_data["accessToken"],
                "refreshToken": oauth_data.get("refreshToken", ""),
                "expiresAt": oauth_data.get("expiresAt", 0),
                "source": "claude_code_file",
            }
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return None


def read_claude_code_credentials() -> dict[str, Any] | None:
    """Read Claude Code OAuth credentials (Keychain first, then file)."""
    return _read_claude_code_keychain() or _read_claude_code_file()


def _write_claude_code_credentials(
    access_token: str,
    refresh_token: str,
    expires_at_ms: int,
) -> None:
    """Write refreshed credentials back to ~/.claude/.credentials.json."""
    cred_path = Path.home() / ".claude" / ".credentials.json"
    try:
        existing: dict[str, Any] = {}
        if cred_path.exists():
            existing = json.loads(cred_path.read_text(encoding="utf-8"))

        oauth_data: dict[str, Any] = {
            "accessToken": access_token,
            "refreshToken": refresh_token,
            "expiresAt": expires_at_ms,
        }
        # Preserve existing scopes
        prev = existing.get("claudeAiOauth")
        if isinstance(prev, dict) and "scopes" in prev:
            oauth_data["scopes"] = prev["scopes"]

        existing["claudeAiOauth"] = oauth_data

        cred_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = cred_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        tmp.replace(cred_path)
        cred_path.chmod(0o600)
    except (OSError, IOError) as exc:
        logger.debug("Failed to write Claude Code credentials: %s", exc)


def _is_claude_code_token_valid(creds: dict[str, Any]) -> bool:
    expires_at = creds.get("expiresAt", 0)
    if not expires_at:
        return bool(creds.get("accessToken"))
    return now_ms() < (expires_at - 60_000)


async def _refresh_claude_code_token(creds: dict[str, Any]) -> str | None:
    """Attempt to refresh an expired Claude Code token. Returns new access token or None."""
    refresh_token = creds.get("refreshToken", "")
    if not refresh_token:
        return None

    try:
        refreshed = await refresh_anthropic_token(refresh_token)
        _write_claude_code_credentials(
            refreshed.access,
            refreshed.refresh,
            refreshed.expires_ms,
        )
        logger.debug("Refreshed Claude Code OAuth token")
        return refreshed.access
    except Exception as exc:
        logger.debug("Failed to refresh Claude Code token: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Unified token resolution
# ---------------------------------------------------------------------------

async def resolve_anthropic_token() -> tuple[str | None, str]:
    """Resolve an Anthropic token from all available sources.

    Returns (token, auth_mode) where auth_mode is one of:
    - "oauth" for OAuth tokens (setup-token or PKCE)
    - "setup-token" for setup tokens
    - "api-key" for regular API keys
    - "" if nothing found

    Priority:
    1. Hexis-native PKCE OAuth (~/.hexis/auth/)
    2. Claude Code credentials (~/.claude/.credentials.json or macOS Keychain)
    3. CLAUDE_CODE_OAUTH_TOKEN env var
    4. Anthropic setup token (~/.hexis/auth/)
    5. ANTHROPIC_API_KEY env var
    """
    # 1. Hexis-native PKCE OAuth
    hexis_creds = load_credentials()
    if hexis_creds:
        if not needs_refresh(hexis_creds.expires_ms, 120):
            return hexis_creds.access, "setup-token"
        # Try to refresh
        try:
            refreshed = await refresh_anthropic_token(hexis_creds.refresh)
            save_credentials(refreshed)
            return refreshed.access, "setup-token"
        except Exception as exc:
            logger.debug("Hexis Anthropic token expired and refresh failed: %s", exc)

    # 2. Claude Code credentials
    cc_creds = read_claude_code_credentials()
    if cc_creds:
        access = cc_creds.get("accessToken", "")
        if _is_claude_code_token_valid(cc_creds):
            return access, "setup-token"
        refreshed_token = await _refresh_claude_code_token(cc_creds)
        if refreshed_token:
            return refreshed_token, "setup-token"

    # 3. CLAUDE_CODE_OAUTH_TOKEN env var
    cc_env = os.getenv("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
    if cc_env:
        return cc_env, "setup-token"

    # 4. Anthropic setup token
    from core.auth.anthropic_setup_token import load_credentials as load_setup_token
    setup_creds = load_setup_token()
    if setup_creds:
        return setup_creds.token, "setup-token"

    # 5. ANTHROPIC_API_KEY env var
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if api_key:
        if is_oauth_token(api_key):
            return api_key, "setup-token"
        return api_key, "api-key"

    return None, ""


# ---------------------------------------------------------------------------
# Persistence (filesystem – survives DB resets)
# ---------------------------------------------------------------------------

def credentials_to_dict(creds: AnthropicOAuthCredentials) -> dict[str, Any]:
    return {
        "access": creds.access,
        "refresh": creds.refresh,
        "expires_ms": creds.expires_ms,
        "source": creds.source,
    }


def credentials_from_value(value: Any) -> AnthropicOAuthCredentials | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return None
    if not isinstance(value, dict):
        return None
    access = value.get("access")
    refresh = value.get("refresh")
    expires_ms = value.get("expires_ms")
    if not isinstance(access, str) or not isinstance(refresh, str):
        return None
    if not isinstance(expires_ms, (int, float)):
        return None
    return AnthropicOAuthCredentials(
        access=access,
        refresh=refresh,
        expires_ms=int(expires_ms),
        source=value.get("source", "hexis_pkce"),
    )


def load_credentials() -> AnthropicOAuthCredentials | None:
    from core.auth.store import load_auth
    return credentials_from_value(load_auth(ANTHROPIC_OAUTH_CONFIG_KEY))


def save_credentials(creds: AnthropicOAuthCredentials) -> None:
    from core.auth.store import save_auth
    save_auth(ANTHROPIC_OAUTH_CONFIG_KEY, credentials_to_dict(creds))


def delete_credentials() -> None:
    from core.auth.store import delete_auth
    delete_auth(ANTHROPIC_OAUTH_CONFIG_KEY)


async def ensure_fresh_credentials(*, skew_seconds: int = 120) -> AnthropicOAuthCredentials:
    """Return valid Hexis-native credentials, refreshing under file lock if needed."""
    from core.auth.store import auth_lock

    with auth_lock(ANTHROPIC_OAUTH_CONFIG_KEY):
        creds = load_credentials()
        if not creds:
            raise RuntimeError(
                "Anthropic OAuth is not configured. Run: `hexis auth anthropic login`"
            )
        if not needs_refresh(creds.expires_ms, skew_seconds):
            return creds

        refreshed = await refresh_anthropic_token(creds.refresh)
        save_credentials(refreshed)
        return refreshed
