from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from core.tools.base import ToolContext, ToolExecutionContext
from core.tools.integrations import (
    ConfigureChannelIntegrationHandler,
    IntegrationSetupStatusHandler,
    StartIntegrationSetupHandler,
    VerifyChannelIntegrationHandler,
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


async def _snapshot_config(db_pool, keys: list[str]) -> dict[str, str]:
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT key, value FROM config WHERE key = ANY($1::text[])", keys)
    return {row["key"]: json.dumps(_j(row["value"])) for row in rows}


async def _restore_config(db_pool, keys: list[str], snapshot: dict[str, str]) -> None:
    async with db_pool.acquire() as conn:
        for key in keys:
            if key in snapshot:
                await conn.execute("SELECT set_config($1, $2::jsonb)", key, snapshot[key])
            else:
                await conn.execute("DELETE FROM config WHERE key = $1", key)


async def test_integration_setup_status_tool_is_registered(db_pool):
    async with db_pool.acquire() as conn:
        snapshot = await conn.fetchrow(
            "SELECT * FROM channel_adapter_runtime WHERE channel_type = 'telegram'"
        )
        await conn.execute(
            "SELECT record_channel_adapter_status('telegram', 'running', TRUE, TRUE)"
        )
    try:
        registry = create_default_registry(db_pool)
        result = await registry.execute(
            "integration_setup_status",
            {"connector_id": "telegram"},
            ToolExecutionContext(
                tool_context=ToolContext.CHAT,
                call_id="integration-status",
                session_id="integration-status",
            ),
        )
    finally:
        async with db_pool.acquire() as conn:
            if snapshot:
                await conn.execute(
                    """
                    INSERT INTO channel_adapter_runtime (
                        channel_type, status, configured, running, worker_id, pid,
                        last_checked_at, last_started_at, last_stopped_at, last_error,
                        metadata, created_at, updated_at
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6,
                        $7, $8, $9, $10,
                        $11::jsonb, $12, $13
                    )
                    ON CONFLICT (channel_type) DO UPDATE SET
                        status = EXCLUDED.status,
                        configured = EXCLUDED.configured,
                        running = EXCLUDED.running,
                        worker_id = EXCLUDED.worker_id,
                        pid = EXCLUDED.pid,
                        last_checked_at = EXCLUDED.last_checked_at,
                        last_started_at = EXCLUDED.last_started_at,
                        last_stopped_at = EXCLUDED.last_stopped_at,
                        last_error = EXCLUDED.last_error,
                        metadata = EXCLUDED.metadata,
                        created_at = EXCLUDED.created_at,
                        updated_at = EXCLUDED.updated_at
                    """,
                    snapshot["channel_type"],
                    snapshot["status"],
                    snapshot["configured"],
                    snapshot["running"],
                    snapshot["worker_id"],
                    snapshot["pid"],
                    snapshot["last_checked_at"],
                    snapshot["last_started_at"],
                    snapshot["last_stopped_at"],
                    snapshot["last_error"],
                    json.dumps(_j(snapshot["metadata"])),
                    snapshot["created_at"],
                    snapshot["updated_at"],
                )
            else:
                await conn.execute(
                    "DELETE FROM channel_adapter_runtime WHERE channel_type = 'telegram'"
                )

    assert result.success
    assert result.output["connectors"][0]["id"] == "telegram"
    assert result.output["connectors"][0]["status"] == "available"
    assert result.output["channel_runtime"][0]["status"] == "running"


async def test_integration_setup_status_tool_lists_connectors(db_pool):
    registry = create_default_registry(db_pool)
    result = await registry.execute(
        "integration_setup_status",
        {},
        ToolExecutionContext(
            tool_context=ToolContext.CHAT,
            call_id="integration-status-all",
            session_id="integration-status-all",
        ),
    )

    assert result.success
    ids = {item["id"] for item in result.output["connectors"]}
    assert {"gmail", "slack", "telegram", "signal"} <= ids


async def test_start_configure_and_verify_telegram_channel_setup(db_pool, monkeypatch):
    marker = get_test_identifier("telegram-setup")
    token_env = f"HEXIS_TEST_TELEGRAM_TOKEN_{marker.upper().replace('-', '_')}"
    monkeypatch.setenv(token_env, "telegram-test-token-value-that-resolves")
    config_keys = ["channel.telegram.bot_token", "channel.telegram.allowed_chat_ids"]
    config_snapshot = await _snapshot_config(db_pool, config_keys)

    try:
        started = await StartIntegrationSetupHandler().execute(
            {"connector_id": "telegram", "source_channel": "cli"},
            _ctx(db_pool, marker),
        )
        assert started.success
        assert started.output["connector_id"] == "telegram"
        assert started.output["status"] == "pending_user"
        assert "TELEGRAM_BOT_TOKEN" in started.output["next_step"]

        rejected = await ConfigureChannelIntegrationHandler().execute(
            {
                "connector_id": "telegram",
                "settings": {"bot_token": "123456789012345678901234567890"},
            },
            _ctx(db_pool, marker),
        )
        assert not rejected.success
        assert "environment variable name" in rejected.error

        configured = await ConfigureChannelIntegrationHandler().execute(
            {
                "connector_id": "telegram",
                "settings": {"bot_token": token_env, "allowed_chat_ids": "*"},
            },
            _ctx(db_pool, marker),
        )
        assert configured.success
        assert sorted(configured.output["applied"]) == ["allowed_chat_ids", "bot_token"]

        verified = await VerifyChannelIntegrationHandler().execute(
            {"connector_id": "telegram", "attempt_id": started.output["attempt_id"]},
            _ctx(db_pool, marker),
        )
        assert verified.success
        assert verified.output["connector_id"] == "telegram"
        assert verified.output["status"] == "connected"
        assert verified.output["account_key"] == "channel:telegram"
        assert verified.output["credential_ref"] == "config:channel.telegram"

        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT status, capabilities, credential_ref
                FROM integration_connections
                WHERE connector_id = 'telegram'
                  AND account_key = 'channel:telegram'
                """
            )
            assert row["status"] == "connected"
            assert _j(row["capabilities"]) == ["live_chat", "send", "ingest_live"]
            assert row["credential_ref"] == "config:channel.telegram"
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM integration_connections WHERE connector_id = 'telegram' AND account_key = 'channel:telegram'")
            await conn.execute("DELETE FROM connection_attempts WHERE source_session_id = $1", marker)
        await _restore_config(db_pool, config_keys, config_snapshot)


async def test_verify_channel_integration_reports_exact_setup_step_when_missing_config(db_pool):
    marker = get_test_identifier("missing-signal")
    config_keys = ["channel.signal.phone_number", "channel.signal.api_url", "channel.signal.allowed_numbers"]
    config_snapshot = await _snapshot_config(db_pool, config_keys)
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM config WHERE key LIKE 'channel.signal.%'")

    try:
        result = await VerifyChannelIntegrationHandler().execute(
            {"connector_id": "signal"},
            _ctx(db_pool, marker),
        )
    finally:
        await _restore_config(db_pool, config_keys, config_snapshot)

    assert not result.success
    assert result.error_type.value == "missing_config"
    assert "SIGNAL_PHONE_NUMBER" in result.error
