from __future__ import annotations

import pytest

from services import recmem

pytestmark = [pytest.mark.asyncio(loop_scope="session")]


async def _stub_get_embedding(conn, axis=1):
    await conn.execute(
        """
        CREATE OR REPLACE FUNCTION get_embedding(text_contents TEXT[])
        RETURNS vector[] AS $$
            SELECT COALESCE(
                array_agg((
                    array_fill(0.0::float, ARRAY[$axis$::int - 1]) ||
                    ARRAY[1.0::float] ||
                    array_fill(0.0::float, ARRAY[embedding_dimension() - $axis$::int])
                )::vector),
                ARRAY[]::vector[]
            )
            FROM unnest(text_contents)
        $$ LANGUAGE sql;
        """.replace("$axis$", str(int(axis)))
    )


async def test_recmem_embed_and_route_steps(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await _stub_get_embedding(conn, axis=1)
            await conn.execute("SELECT set_config('memory.recmem_embed_batch_size', '2'::jsonb)")
            await conn.execute("SELECT set_config('memory.recmem_route_batch_size', '2'::jsonb)")
            await conn.execute("SELECT set_config('memory.recmem_theta_count', '3'::jsonb)")
            unit = await conn.fetchval(
                "SELECT (recmem_ingest_turn('worker embed route', 'ok', NULL, 'worker-embed-route')->>'unit_id')::uuid"
            )

            embed_result = await recmem.run_recmem_embed_step(conn)
            assert embed_result["embedded"] == 1
            assert await conn.fetchval("SELECT embedding_status FROM subconscious_units WHERE id = $1", unit) == "embedded"

            route_result = await recmem.run_recmem_route_step(conn)
            assert route_result["outcomes"]["raw_only"] == 1
            assert await conn.fetchval("SELECT route_status FROM subconscious_units WHERE id = $1", unit) == "raw_only"
        finally:
            await tr.rollback()


async def test_recmem_consolidation_worker_create_and_refine(db_pool, monkeypatch):
    calls = [
        {"episodes": [{"content": "worker-created episode", "importance": 0.7}]},
        {"facts": [{"content": "User prefers worker apples", "importance": 0.8}]},
    ]

    async def fake_chat_json(**_kwargs):
        return calls.pop(0), "{}"

    async def fake_load_llm_config(_conn, *_args, **_kwargs):
        return {"provider": "test", "model": "test"}

    monkeypatch.setattr(recmem, "chat_json", fake_chat_json)
    monkeypatch.setattr(recmem, "load_llm_config", fake_load_llm_config)

    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await _stub_get_embedding(conn, axis=1)
            source_unit = await conn.fetchval(
                """
                INSERT INTO subconscious_units (
                    content, user_text, assistant_text, embedding, embedding_status,
                    route_status, idempotency_key
                )
                VALUES (
                    'User: worker create\n\nAssistant: ok',
                    'worker create',
                    'ok',
                    (
                        array_fill(0.0::float, ARRAY[0]) ||
                        ARRAY[1.0::float] ||
                        array_fill(0.0::float, ARRAY[embedding_dimension() - 1])
                    )::vector,
                    'embedded',
                    'create_queued',
                    'worker:create'
                )
                RETURNING id
                """
            )
            task_id = await conn.fetchval(
                """
                INSERT INTO recmem_consolidation_tasks (
                    task_type, trigger_unit_id, source_unit_ids
                )
                VALUES ('episode_create', $1, ARRAY[$1]::uuid[])
                RETURNING id
                """,
                source_unit,
            )

            create_result = await recmem.run_recmem_consolidation_step(conn)
            assert create_result["task_id"] == str(task_id)
            assert create_result["memory_ids"]
            assert await conn.fetchval("SELECT route_status FROM subconscious_units WHERE id = $1", source_unit) == "episode_created"

            refine_result = await recmem.run_recmem_consolidation_step(conn)
            assert refine_result["memory_ids"]
            facts = await conn.fetchval(
                "SELECT COUNT(*) FROM memories WHERE type = 'semantic' AND content = 'User prefers worker apples'"
            )
            assert facts == 1
        finally:
            await tr.rollback()
