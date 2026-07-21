from __future__ import annotations

import json

import pytest

from services import channel_backfill
from tests.utils import get_test_identifier

pytestmark = [pytest.mark.asyncio(loop_scope="session")]


def _j(value):
    return json.loads(value) if isinstance(value, str) else value


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


async def _connected_channel(conn, connector_id: str, marker: str, account_key: str) -> None:
    attempt = _j(await conn.fetchval(
        """
        SELECT start_connection_attempt(
            $1,
            '["live_chat", "send", "ingest_live"]'::jsonb,
            ARRAY[]::text[],
            '{}'::jsonb,
            NULL,
            NULL,
            'test',
            $2,
            CURRENT_TIMESTAMP + INTERVAL '10 minutes'
        )
        """,
        connector_id,
        marker,
    ))
    await conn.fetchval(
        """
        SELECT complete_connection_attempt(
            $1::uuid,
            $2,
            $3,
            $4,
            ARRAY[]::text[],
            '["live_chat", "send", "ingest_live"]'::jsonb,
            '{"test": true}'::jsonb
        )
        """,
        attempt["attempt_id"],
        account_key,
        connector_id,
        f"config:channel.{connector_id}",
    )


async def test_slack_backfill_stores_history_as_connector_source_items(db_pool, monkeypatch):
    marker = get_test_identifier("slack-backfill")
    channel_id = f"C{marker.replace('-', '')[:10].upper()}"
    account = f"channel:slack:{marker}"
    token_env = f"HEXIS_TEST_SLACK_TOKEN_{marker.upper().replace('-', '_')}"
    monkeypatch.setenv(token_env, "xoxb-test-token-that-is-long-enough")
    config_keys = ["channel.slack.bot_token", "channel.slack.allowed_channels"]
    config_snapshot = await _snapshot_config(db_pool, config_keys)

    try:
        async with db_pool.acquire() as conn:
            await conn.execute("SELECT set_config('channel.slack.bot_token', $1::jsonb)", json.dumps(token_env))
            await conn.execute(
                "SELECT set_config('channel.slack.allowed_channels', $1::jsonb)",
                json.dumps([channel_id]),
            )
            await _connected_channel(conn, "slack", marker, account)
            queued = _j(await conn.fetchval(
                """
                SELECT enqueue_connector_backfill_job(
                    'slack',
                    $1,
                    'messages',
                    $2::jsonb,
                    '{"test": true}'::jsonb
                )
                """,
                account,
                json.dumps({"channel_id": channel_id, "max_messages": 2, "page_size": 2}),
            ))
            job = dict(await conn.fetchrow("SELECT * FROM connector_backfill_jobs WHERE id = $1::uuid", queued["job_id"]))

        async def fake_slack_get(token, path, *, params=None):
            assert token == "xoxb-test-token-that-is-long-enough"
            assert path == "/conversations.history"
            assert params["channel"] == channel_id
            assert params["limit"] == 2
            return {
                "ok": True,
                "messages": [
                    {"ts": "1710000000.000100", "user": "U1", "text": f"I prefer quiet planning {marker}."},
                    {"ts": "1710000001.000200", "user": "U2", "text": "The deadline is due today."},
                ],
                "response_metadata": {"next_cursor": "next-cursor"},
            }

        monkeypatch.setattr(channel_backfill, "_slack_get", fake_slack_get)
        result = await channel_backfill.process_slack_backfill_job(db_pool, job)

        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT provider_item_id, source_document_id, ingestion_job_id, sensitivity
                FROM connector_source_items
                WHERE account_key = $1
                ORDER BY provider_item_id
                """,
                account,
            )
            opened = _j(await conn.fetchval("SELECT open_source_document($1::uuid)", rows[0]["source_document_id"]))

        assert result["status"] == "completed"
        assert result["result"]["items_stored"] == 2
        assert result["result"]["truncated"] is True
        assert [row["provider_item_id"] for row in rows] == [
            f"{channel_id}:1710000000.000100",
            f"{channel_id}:1710000001.000200",
        ]
        assert rows[0]["ingestion_job_id"] is not None
        assert rows[0]["sensitivity"] == "shared"
        assert f"I prefer quiet planning {marker}." in opened["content"]
        assert opened["source_attribution"]["connector_id"] == "slack"
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM connector_backfill_jobs WHERE account_key = $1", account)
            await conn.execute("DELETE FROM connector_sync_cursors WHERE account_key = $1", account)
            await conn.execute("DELETE FROM integration_connections WHERE account_key = $1", account)
            await conn.execute("DELETE FROM connection_attempts WHERE source_session_id = $1", marker)
            await conn.execute(
                "DELETE FROM source_documents WHERE path LIKE $1",
                f"slack://{account}/message/{channel_id}:%",
            )
            await conn.execute(
                "DELETE FROM ingestion_jobs WHERE content LIKE $1",
                f"%{marker}%",
            )
        await _restore_config(db_pool, config_keys, config_snapshot)


async def test_telegram_backfill_fails_loudly_with_provider_limitation(db_pool):
    marker = get_test_identifier("telegram-backfill")
    account = f"channel:telegram:{marker}"

    try:
        async with db_pool.acquire() as conn:
            await _connected_channel(conn, "telegram", marker, account)
            queued = _j(await conn.fetchval(
                """
                SELECT enqueue_connector_backfill_job(
                    'telegram',
                    $1,
                    'messages',
                    '{}'::jsonb,
                    '{"test": true}'::jsonb,
                    1
                )
                """,
                account,
            ))
            claimed = _j(await conn.fetchval(
                "SELECT claim_connector_backfill_jobs_for('telegram', 1)"
            ))
            job = claimed[0]

        result = await channel_backfill.process_channel_backfill_job(db_pool, job)

        assert result["status"] == "failed"
        async with db_pool.acquire() as conn:
            error = await conn.fetchval(
                "SELECT error FROM connector_backfill_jobs WHERE id = $1::uuid",
                queued["job_id"],
            )
        assert "cannot retroactively fetch chat history" in error
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM connector_backfill_jobs WHERE account_key = $1", account)
            await conn.execute("DELETE FROM connector_sync_cursors WHERE account_key = $1", account)
            await conn.execute("DELETE FROM integration_connections WHERE account_key = $1", account)
            await conn.execute("DELETE FROM connection_attempts WHERE source_session_id = $1", marker)
