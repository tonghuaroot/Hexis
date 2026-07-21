from __future__ import annotations

import json

import pytest

from services.connector_cognition import (
    extract_user_model_claims,
    run_connector_importance_step,
    run_user_model_synthesis_step,
)
from tests.utils import get_test_identifier

pytestmark = [pytest.mark.asyncio(loop_scope="session")]


def _j(value):
    return json.loads(value) if isinstance(value, str) else value


async def test_user_model_rules_ignore_explicit_test_filler():
    claims = extract_user_model_claims({
        "content": "Message:\nThis is just a test. I like green buttons in this sample conversation.",
    })
    assert claims == []


async def _stub_get_embedding(conn):
    await conn.execute(
        """
        CREATE OR REPLACE FUNCTION get_embedding(text_contents TEXT[])
        RETURNS vector[] AS $$
            SELECT COALESCE(
                array_agg((
                    ARRAY[1.0::float] ||
                    array_fill(0.0::float, ARRAY[embedding_dimension() - 1])
                )::vector),
                ARRAY[]::vector[]
            )
            FROM unnest(text_contents)
        $$ LANGUAGE sql;
        """
    )


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


async def test_connector_source_items_become_claims_and_importance_notifications(db_pool):
    marker = get_test_identifier("connector-cognition")
    account = f"channel:slack:{marker}"
    provider_item_id = f"msg-{marker}"
    content = (
        f"Slack channel: CCOGNITION\n"
        f"Slack timestamp: 1710000000.000100\n"
        f"Sender: U1\n\n"
        f"Message:\n"
        f"I prefer quiet morning planning {marker}. "
        "The deadline is due today. Can you please flag this?"
    )

    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await _stub_get_embedding(conn)
            await _connected_channel(conn, "slack", marker, account)
            source_item = _j(await conn.fetchval(
                """
                SELECT upsert_connector_source_item(
                    'slack',
                    $1,
                    $2,
                    'Cognition message',
                    $3,
                    'message',
                    NULL,
                    CURRENT_TIMESTAMP,
                    ARRAY['slack', 'CCOGNITION']::text[],
                    '[{"role": "sender", "id": "U1"}]'::jsonb,
                    '[]'::jsonb,
                    '{"test": true}'::jsonb,
                    'private',
                    TRUE
                )
                """,
                account,
                provider_item_id,
                content,
            ))
            await conn.execute(
                "UPDATE connector_source_items SET status = 'archived' WHERE id <> $1::uuid",
                source_item["source_item_id"],
            )

            synthesis = await run_user_model_synthesis_step(conn, limit=5)
            importance = await run_connector_importance_step(conn, limit=5)

            claim = await conn.fetchrow(
                """
                SELECT c.claim_key, c.claim, c.memory_id, m.type::text AS memory_type,
                       c.evidence_refs
                FROM user_model_claims c
                JOIN memories m ON m.id = c.memory_id
                WHERE c.claim LIKE $1
                """,
                f"%{marker}%",
            )
            progress = await conn.fetchrow(
                """
                SELECT status, result
                FROM user_model_source_progress
                WHERE source_item_id = $1::uuid
                """,
                source_item["source_item_id"],
            )
            item_importance = await conn.fetchrow(
                """
                SELECT score, label, status, notification_queued_at, metadata
                FROM connector_item_importance
                WHERE source_item_id = $1::uuid
                """,
                source_item["source_item_id"],
            )
            outbox = await conn.fetchrow(
                """
                SELECT source, envelope
                FROM outbox_messages
                WHERE source = 'connector_importance'
                  AND envelope->'payload'->>'intent' = 'connector_importance'
                ORDER BY created_at DESC
                LIMIT 1
                """
            )
        finally:
            await tr.rollback()

    assert synthesis["claimed"] == 1
    assert synthesis["completed"] == 1
    assert synthesis["claims"] >= 1
    assert importance["claimed"] == 1
    assert importance["completed"] == 1
    assert importance["notified"] == 1

    assert claim is not None
    assert claim["claim_key"].startswith("preference:")
    assert claim["memory_type"] == "semantic"
    assert source_item["source_item_id"] in json.dumps(_j(claim["evidence_refs"]))
    assert progress["status"] == "completed"
    assert _j(progress["result"])["claim_count"] >= 1

    assert item_importance["status"] == "completed"
    assert item_importance["label"] == "important"
    assert float(item_importance["score"]) >= 0.85
    assert item_importance["notification_queued_at"] is not None
    assert "outbox_message_id" in _j(item_importance["metadata"])

    assert outbox is not None
    envelope = _j(outbox["envelope"])
    assert envelope["payload"]["delivery"]["mode"] == "web_inbox"
    assert envelope["payload"]["delivery"]["source_item_id"] == source_item["source_item_id"]
