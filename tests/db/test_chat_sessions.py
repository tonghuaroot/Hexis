from __future__ import annotations

import json
from uuid import uuid4

import pytest

pytestmark = [pytest.mark.asyncio(loop_scope="session")]


def _j(value):
    return json.loads(value) if isinstance(value, str) else value


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


async def test_record_hydrate_and_clear_chat_session(db_pool):
    marker = uuid4().hex
    session_id = str(uuid4())

    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await _stub_get_embedding(conn)

            recorded = _j(await conn.fetchval(
                """
                SELECT record_chat_session_turn(
                    $1::uuid, $2, $3, 'cli',
                    $4::jsonb
                )
                """,
                session_id,
                f"remember the cedar gate {marker}",
                "I will keep that in view.",
                json.dumps({"metadata": {"type": "conversation", "test_marker": marker}}),
            ))

            assert recorded["session"]["surface"] == "cli"
            assert recorded["memory"]["raw"]["status"] == "stored"
            assert [m["role"] for m in recorded["history"]["messages"]] == ["user", "assistant"]

            hydrated = _j(await conn.fetchval(
                "SELECT hydrate_chat_session($1::uuid)",
                session_id,
            ))
            assert hydrated["count"] == 2
            assert hydrated["messages"][0]["content"] == f"remember the cedar gate {marker}"
            assert hydrated["messages"][1]["content"] == "I will keep that in view."

            cleared = _j(await conn.fetchval(
                "SELECT clear_chat_session_context($1::uuid, 'test_clear')",
                session_id,
            ))
            assert cleared["cleared_messages"] == 2
            assert cleared["long_term_memory_preserved"] is True

            after_clear = _j(await conn.fetchval(
                "SELECT hydrate_chat_session($1::uuid)",
                session_id,
            ))
            assert after_clear["messages"] == []
        finally:
            await tr.rollback()


async def test_chat_session_history_survives_memory_write_failure(db_pool):
    session_id = str(uuid4())

    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute(
                """
                CREATE OR REPLACE FUNCTION record_chat_turn_memory(
                    p_user_text TEXT,
                    p_assistant_text TEXT,
                    p_session_id TEXT DEFAULT NULL,
                    p_source_identity TEXT DEFAULT NULL,
                    p_context JSONB DEFAULT '{}'::jsonb
                ) RETURNS JSONB
                LANGUAGE plpgsql
                AS $$
                BEGIN
                    RAISE EXCEPTION 'forced memory failure';
                END;
                $$;
                """
            )

            recorded = _j(await conn.fetchval(
                "SELECT record_chat_session_turn($1::uuid, 'hi', 'hello', 'api', '{}'::jsonb)",
                session_id,
            ))

            assert recorded["memory"]["status"] == "failed"
            assert recorded["memory"]["short_term_history_preserved"] is True
            assert recorded["history"]["count"] == 2
            assert [m["content"] for m in recorded["history"]["messages"]] == ["hi", "hello"]
        finally:
            await tr.rollback()
