from __future__ import annotations

import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from core.auth.google_gmail import (
    GMAIL_CLIENT_SECRET_REF,
    GMAIL_DEFAULT_CREDENTIAL_REF,
    GMAIL_PENDING_PREFIX,
)
from core.auth.store import load_auth, save_auth
from core.auth.utils import now_ms
from core.tools.base import ToolContext, ToolExecutionContext
from core.tools.integrations import (
    CompleteGmailConnectionHandler,
    ConnectGmailHandler,
    ControlGmailBackfillHandler,
    ConnectorActionPolicyStatusHandler,
    GmailBackfillStatusHandler,
    GrantConnectorActionPolicyHandler,
    RevokeConnectorActionPolicyHandler,
    StartGmailBackfillHandler,
)
from core.tools.registry import create_default_registry
from tests.utils import get_test_identifier

pytestmark = [pytest.mark.asyncio(loop_scope="session")]


def _j(value):
    return json.loads(value) if isinstance(value, str) else value


def _ctx(db_pool, marker: str) -> ToolExecutionContext:
    return ToolExecutionContext(
        tool_context=ToolContext.CHAT,
        call_id=f"call-{marker}",
        session_id=marker,
        registry=SimpleNamespace(pool=db_pool),
    )


def _client_secret() -> dict[str, object]:
    return {
        "installed": {
            "client_id": "gmail-test-client",
            "client_secret": "gmail-test-secret",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }


async def _seed_connected_gmail(db_pool, marker: str, account: str) -> None:
    async with db_pool.acquire() as conn:
        attempt = _j(await conn.fetchval(
            """
            SELECT start_connection_attempt(
                'gmail',
                '["read", "search", "ingest"]'::jsonb,
                ARRAY[]::text[],
                '{}'::jsonb,
                NULL,
                NULL,
                'test',
                $1,
                CURRENT_TIMESTAMP + INTERVAL '10 minutes'
            )
            """,
            marker,
        ))
        await conn.fetchval(
            """
            SELECT complete_connection_attempt(
                $1::uuid,
                $2,
                $2,
                'integration.gmail.default',
                $3::text[],
                '["read", "search", "ingest"]'::jsonb,
                '{"test": true}'::jsonb
            )
            """,
            attempt["attempt_id"],
            account,
            [
                "https://www.googleapis.com/auth/userinfo.email",
                "https://www.googleapis.com/auth/gmail.readonly",
            ],
        )


async def test_connect_gmail_without_client_secret_returns_next_step(monkeypatch, tmp_path):
    import core.auth.store as auth_store

    monkeypatch.setattr(auth_store, "AUTH_DIR", tmp_path / "auth")
    for name in (
        "GOOGLE_GMAIL_CLIENT_SECRET_JSON",
        "GOOGLE_CLIENT_SECRET_JSON",
        "GOOGLE_GMAIL_CLIENT_SECRET_PATH",
        "GOOGLE_CLIENT_SECRET_PATH",
    ):
        monkeypatch.delenv(name, raising=False)

    result = await ConnectGmailHandler().execute(
        {},
        ToolExecutionContext(
            tool_context=ToolContext.CHAT,
            call_id="gmail-no-secret",
            registry=SimpleNamespace(pool=object()),
        ),
    )

    assert result.success
    assert result.output["status"] == "needs_client_secret"
    assert "client_secret_path" in result.output["accepted_inputs"]
    assert "connect_gmail" in result.output["next_step"]


async def test_gmail_setup_status_executes_through_registry(db_pool, monkeypatch, tmp_path):
    import core.auth.store as auth_store

    monkeypatch.setattr(auth_store, "AUTH_DIR", tmp_path / "auth")
    registry = create_default_registry(db_pool)

    result = await registry.execute(
        "gmail_setup_status",
        {},
        ToolExecutionContext(
            tool_context=ToolContext.CHAT,
            call_id="gmail-status-registry",
            session_id="gmail-status-registry",
        ),
    )

    assert result.success
    assert result.output["connectors"][0]["id"] == "gmail"
    assert result.output["client_secret_saved"] is False
    assert result.output["credentials_saved"] is False


async def test_connect_and_complete_gmail_oauth_round_trip(db_pool, monkeypatch, tmp_path):
    import core.auth.google_gmail as google_gmail
    import core.auth.store as auth_store

    monkeypatch.setattr(auth_store, "AUTH_DIR", tmp_path / "auth")
    save_auth(GMAIL_CLIENT_SECRET_REF, _client_secret())

    marker = get_test_identifier("gmail-connector")
    email = f"eric-{marker}@example.com"

    async def fake_exchange_code(**kwargs):
        assert kwargs["code"] == "oauth-code"
        assert kwargs["client_id"] == "gmail-test-client"
        assert kwargs["client_secret"] == "gmail-test-secret"
        assert kwargs["redirect_uri"] == "http://localhost:1"
        return {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "expires_in": 3600,
            "scope": (
                "https://www.googleapis.com/auth/userinfo.email "
                "https://www.googleapis.com/auth/gmail.readonly"
            ),
        }

    async def fake_fetch_account_email(access_token: str):
        assert access_token == "access-token"
        return email

    monkeypatch.setattr(google_gmail, "_exchange_code", fake_exchange_code)
    monkeypatch.setattr(google_gmail, "_fetch_account_email", fake_fetch_account_email)

    try:
        started = await ConnectGmailHandler().execute(
            {"capabilities": ["read", "search"], "source_channel": "cli"},
            _ctx(db_pool, marker),
        )

        assert started.success
        assert started.output["connector_id"] == "gmail"
        assert started.output["status"] == "pending_user"
        assert started.output["source_channel"] == "cli"
        assert started.output["source_session_id"] == marker
        assert "authorization_url" in started.output

        auth_params = parse_qs(urlparse(started.output["authorization_url"]).query)
        assert auth_params["client_id"] == ["gmail-test-client"]
        assert auth_params["redirect_uri"] == ["http://localhost:1"]

        attempt_id = started.output["attempt_id"]
        pending = load_auth(f"{GMAIL_PENDING_PREFIX}{attempt_id}")
        assert pending["capabilities"] == ["read", "search"]

        completed = await CompleteGmailConnectionHandler().execute(
            {
                "attempt_id": attempt_id,
                "authorization_response": f"http://localhost:1/?code=oauth-code&state={pending['state']}",
            },
            _ctx(db_pool, marker),
        )

        assert completed.success
        assert completed.output["status"] == "connected"
        assert completed.output["account_key"] == email
        assert completed.output["capabilities"] == ["read", "search"]

        credentials = load_auth(GMAIL_DEFAULT_CREDENTIAL_REF)
        assert credentials["refresh_token"] == "refresh-token"
        assert credentials["account_email"] == email

        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT account_key, status, capabilities, credential_ref
                FROM integration_connections
                WHERE connector_id = 'gmail'
                  AND account_key = $1
                """,
                email,
            )
        assert row is not None
        assert row["status"] == "connected"
        assert row["credential_ref"] == GMAIL_DEFAULT_CREDENTIAL_REF
        assert _j(row["capabilities"]) == ["read", "search"]
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM integration_connections WHERE account_key = $1", email)
            await conn.execute("DELETE FROM connection_attempts WHERE source_session_id = $1", marker)


async def test_gmail_backfill_tools_queue_status_and_control(db_pool, monkeypatch, tmp_path):
    import core.auth.store as auth_store

    monkeypatch.setattr(auth_store, "AUTH_DIR", tmp_path / "auth")
    marker = get_test_identifier("gmail-backfill-tool")
    account = f"eric-{marker}@example.com"
    save_auth(
        GMAIL_DEFAULT_CREDENTIAL_REF,
        {
            "type": "authorized_user",
            "token": "access-token",
            "refresh_token": "refresh-token",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "client",
            "client_secret": "secret",
            "expires_ms": now_ms() + 3_600_000,
            "account_email": account,
            "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
        },
    )
    await _seed_connected_gmail(db_pool, marker, account)

    try:
        queued = await StartGmailBackfillHandler().execute(
            {
                "account_key": account,
                "query": "newer_than:7d",
                "label_ids": ["INBOX"],
                "max_messages": 5,
                "source_channel": "cli",
            },
            _ctx(db_pool, marker),
        )
        assert queued.success
        assert queued.output["status"] == "pending"
        assert queued.output["connector_id"] == "gmail"
        assert queued.output["requested_range"]["query"] == "newer_than:7d"

        status = await GmailBackfillStatusHandler().execute(
            {"account_key": account},
            _ctx(db_pool, marker),
        )
        assert status.success
        assert status.output["jobs"][0]["job_id"] == queued.output["job_id"]
        assert "1 active jobs" in status.display_output

        paused = await ControlGmailBackfillHandler().execute(
            {"job_id": queued.output["job_id"], "action": "pause", "reason": "test pause"},
            _ctx(db_pool, marker),
        )
        resumed = await ControlGmailBackfillHandler().execute(
            {"job_id": queued.output["job_id"], "action": "resume"},
            _ctx(db_pool, marker),
        )
        cancelled = await ControlGmailBackfillHandler().execute(
            {"job_id": queued.output["job_id"], "action": "cancel", "reason": "test cancel"},
            _ctx(db_pool, marker),
        )
        assert paused.output["status"] == "paused"
        assert resumed.output["status"] == "pending"
        assert cancelled.output["status"] == "cancelled"
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM connector_backfill_jobs WHERE account_key = $1", account)
            await conn.execute("DELETE FROM connector_sync_cursors WHERE account_key = $1", account)
            await conn.execute("DELETE FROM integration_connections WHERE account_key = $1", account)
            await conn.execute("DELETE FROM connection_attempts WHERE source_session_id = $1", marker)


async def test_connector_action_policy_tools_grant_status_and_revoke(db_pool):
    marker = get_test_identifier("connector-policy-tool")
    ctx = _ctx(db_pool, marker)

    try:
        granted = await GrantConnectorActionPolicyHandler().execute(
            {
                "connector_id": "slack",
                "action_kind": "send",
                "constraints": {"allowed_targets": ["#ops"], "max_per_day": 2},
                "allow_autonomous": True,
                "requires_per_action_approval": False,
                "contexts": ["heartbeat"],
                "rationale": "Test operational alert grant",
            },
            ctx,
        )
        assert granted.success
        assert granted.output["connector_id"] == "slack"
        assert granted.output["allow_autonomous"] is True

        status = await ConnectorActionPolicyStatusHandler().execute(
            {"connector_id": "slack"},
            ctx,
        )
        assert status.success
        assert any(item["policy_id"] == granted.output["policy_id"] for item in status.output["policies"])

        revoked = await RevokeConnectorActionPolicyHandler().execute(
            {"policy_id": granted.output["policy_id"], "reason": "test cleanup"},
            ctx,
        )
        assert revoked.success
        assert revoked.output["status"] == "revoked"
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM connector_action_audit WHERE context->>'session_id' = $1", marker)
            await conn.execute("DELETE FROM connector_action_policies WHERE source_session_id = $1", marker)
