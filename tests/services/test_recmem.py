from __future__ import annotations

import pytest

from services import recmem
from services import worker_service

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


async def test_maintenance_worker_runs_due_recmem_sweep(db_pool, monkeypatch):
    sweep_calls = []

    async def fake_embed(_conn):
        return {"skipped": True}

    async def fake_route(_conn):
        return {"skipped": True}

    async def fake_sweep(_conn):
        sweep_calls.append(True)
        return {"processed": 0}

    monkeypatch.setattr(worker_service, "run_recmem_embed_step", fake_embed)
    monkeypatch.setattr(worker_service, "run_recmem_route_step", fake_route)
    monkeypatch.setattr(worker_service, "run_recmem_sweep_step", fake_sweep)

    worker = worker_service.MaintenanceWorker()
    worker.pool = db_pool

    async with db_pool.acquire() as conn:
        old_recmem = await conn.fetchval("SELECT value FROM config WHERE key = 'memory.recmem_enabled'")
        old_worker = await conn.fetchval("SELECT value FROM config WHERE key = 'memory.recmem_worker_enabled'")
        old_interval = await conn.fetchval("SELECT value FROM config WHERE key = 'memory.recmem_sweep_interval_seconds'")
        old_state = await conn.fetchval("SELECT value FROM state WHERE key = 'recmem_state'")
        await conn.execute("SELECT set_config('memory.recmem_enabled', 'true'::jsonb)")
        await conn.execute("SELECT set_config('memory.recmem_worker_enabled', 'false'::jsonb)")
        await conn.execute("SELECT set_config('memory.recmem_sweep_interval_seconds', '86400'::jsonb)")
        await conn.execute("DELETE FROM state WHERE key = 'recmem_state'")

    try:
        await worker._run_recmem_if_enabled()  # noqa: SLF001

        assert sweep_calls == [True]
        async with db_pool.acquire() as conn:
            assert await conn.fetchval("SELECT should_run_recmem_sweep()") is False
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE config SET value = $1::jsonb, updated_at = CURRENT_TIMESTAMP WHERE key = 'memory.recmem_enabled'",
                old_recmem,
            )
            await conn.execute(
                "UPDATE config SET value = $1::jsonb, updated_at = CURRENT_TIMESTAMP WHERE key = 'memory.recmem_worker_enabled'",
                old_worker,
            )
            await conn.execute(
                "UPDATE config SET value = $1::jsonb, updated_at = CURRENT_TIMESTAMP WHERE key = 'memory.recmem_sweep_interval_seconds'",
                old_interval,
            )
            if old_state is None:
                await conn.execute("DELETE FROM state WHERE key = 'recmem_state'")
            else:
                await conn.execute(
                    """
                    INSERT INTO state (key, value, updated_at)
                    VALUES ('recmem_state', $1::jsonb, CURRENT_TIMESTAMP)
                    ON CONFLICT (key) DO UPDATE
                    SET value = EXCLUDED.value,
                        updated_at = EXCLUDED.updated_at
                    """,
                    old_state,
                )
