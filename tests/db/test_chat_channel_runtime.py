import json
from uuid import uuid4

import pytest


pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.db]


def _json(value):
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


async def test_record_chat_turn_memory_db_owns_direct_promotion(db_pool):
    async with db_pool.acquire() as conn:
        await _stub_get_embedding(conn)
        await conn.execute("SELECT set_config('memory.recmem_enabled', 'true'::jsonb)")
        await conn.execute("SELECT set_config('chat.eager_memory_enabled', 'true'::jsonb)")
        await conn.execute("SELECT set_config('chat.recmem_salience_direct_promote', 'true'::jsonb)")
        await conn.execute("SELECT set_config('memory.recmem_dual_write_compare', 'false'::jsonb)")

        raw = await conn.fetchval(
            "SELECT record_chat_turn_memory($1, $2, $3, $4, '{}'::jsonb)",
            "remember this important preference",
            "noted",
            str(uuid4()),
            f"test:chat:{uuid4()}",
        )
        result = _json(raw)

        assert result["direct_promoted"] is True
        assert result["eager_written"] is False
        assert result["raw"]["status"] == "stored"
        linked = await conn.fetchval(
            "SELECT COUNT(*) FROM memory_source_units WHERE memory_id = $1::uuid AND subconscious_unit_id = $2::uuid",
            result["eager_memory_id"],
            result["raw_unit_id"],
        )
        assert int(linked) == 1


async def test_prepare_and_finalize_channel_turn_db_lifecycle(db_pool):
    async with db_pool.acquire() as conn:
        channel_id = f"chan-{uuid4()}"
        sender_id = f"sender-{uuid4()}"
        prepared_raw = await conn.fetchval(
            "SELECT prepare_channel_turn($1::jsonb)",
            json.dumps({
                "channel_type": "unit",
                "channel_id": channel_id,
                "sender_id": sender_id,
                "sender_name": "Tester",
                "content": "hello",
                "message_id": "m1",
            }),
        )
        prepared = _json(prepared_raw)
        assert prepared["allowed"] is True
        assert prepared["history"] == []

        history = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        finalized_raw = await conn.fetchval(
            "SELECT finalize_channel_turn($1::uuid, $2, $3, $4::jsonb)",
            prepared["session_id"],
            "hello",
            "hi",
            json.dumps({"history": history, "metadata": {"channel_type": "unit"}}),
        )
        finalized = _json(finalized_raw)

        assert finalized["history_count"] == 2
        outbound = await conn.fetchval(
            "SELECT COUNT(*) FROM channel_messages WHERE session_id = $1::uuid AND direction = 'outbound'",
            prepared["session_id"],
        )
        assert int(outbound) == 1
