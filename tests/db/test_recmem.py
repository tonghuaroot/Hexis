import json

import pytest

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.db]


def _json(value):
    if isinstance(value, str):
        return json.loads(value)
    return value


async def _stub_get_embedding(conn, axis=1):
    await conn.execute(
        """
        CREATE OR REPLACE FUNCTION get_embedding(text_contents TEXT[])
        RETURNS vector[] AS $$
            SELECT COALESCE(
                array_agg((
                    array_fill(0.0::float, ARRAY[$func$1$func$::int - 1]) ||
                    ARRAY[1.0::float] ||
                    array_fill(0.0::float, ARRAY[embedding_dimension() - $func$1$func$::int])
                )::vector),
                ARRAY[]::vector[]
            )
            FROM unnest(text_contents)
        $$ LANGUAGE sql;
        """.replace("$func$1$func$", str(int(axis)))
    )


async def _insert_embedded_unit(conn, source_identity, *, axis=1, route_status="unrouted"):
    return await conn.fetchval(
        """
        INSERT INTO subconscious_units (
            content, user_text, assistant_text, embedding, embedding_status,
            route_status, idempotency_key
        )
        VALUES (
            $1,
            $2,
            $3,
            (
                array_fill(0.0::float, ARRAY[$4::int - 1]) ||
                ARRAY[1.0::float] ||
                array_fill(0.0::float, ARRAY[embedding_dimension() - $4::int])
            )::vector,
            'embedded',
            $5,
            $6
        )
        RETURNING id
        """,
        f"User: {source_identity}\n\nAssistant: ok",
        source_identity,
        "ok",
        int(axis),
        route_status,
        f"test:{source_identity}",
    )


async def _insert_memory(conn, content, *, mem_type="episodic", axis=1):
    return await conn.fetchval(
        """
        SELECT create_memory_with_embedding(
            $1::memory_type,
            $2::text,
            (
                array_fill(0.0::float, ARRAY[$3::int - 1]) ||
                ARRAY[1.0::float] ||
                array_fill(0.0::float, ARRAY[embedding_dimension() - $3::int])
            )::vector,
            0.6,
            NULL,
            0.9
        )
        """,
        mem_type,
        content,
        int(axis),
    )


async def test_recmem_normalization_preserves_internal_whitespace(db_pool):
    async with db_pool.acquire() as conn:
        text = " \ncode:\n    x  =  1   \n\tindent\t\n\n"
        normalized = await conn.fetchval("SELECT normalize_recmem_text($1)", text)
        assert normalized == "code:\n    x  =  1\n\tindent"


async def test_recmem_ingest_idempotency_and_claim(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            first = _json(await conn.fetchval(
                "SELECT recmem_ingest_turn('remember apples', 'noted', NULL, 'test-source-1')"
            ))
            second = _json(await conn.fetchval(
                "SELECT recmem_ingest_turn('remember apples', 'noted', NULL, 'test-source-1')"
            ))

            assert first["status"] == "stored"
            assert second["status"] == "duplicate"
            assert second["unit_id"] == first["unit_id"]

            claimed = _json(await conn.fetchval("SELECT claim_recmem_unembedded_batch(10)"))
            ids = {str(item["unit_id"]) for item in claimed}
            assert str(first["unit_id"]) in ids
        finally:
            await tr.rollback()


async def test_recmem_task_retry_uses_next_attempt_at(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            task_id = await conn.fetchval(
                """
                INSERT INTO recmem_consolidation_tasks (task_type, attempts, status)
                VALUES ('episode_create', 1, 'in_progress')
                RETURNING id
                """
            )
            created_at = await conn.fetchval(
                "SELECT created_at FROM recmem_consolidation_tasks WHERE id = $1",
                task_id,
            )

            result = _json(await conn.fetchval(
                "SELECT fail_recmem_consolidation_task($1::uuid, 'temporary failure')",
                task_id,
            ))
            row = await conn.fetchrow(
                "SELECT status, created_at, next_attempt_at, error FROM recmem_consolidation_tasks WHERE id = $1",
                task_id,
            )

            assert result["status"] == "pending"
            assert row["status"] == "pending"
            assert row["created_at"] == created_at
            assert row["next_attempt_at"] > created_at
            assert row["error"] == "temporary failure"

            stale_task = await conn.fetchval(
                """
                INSERT INTO recmem_consolidation_tasks (
                    task_type, status, started_at, next_attempt_at
                )
                VALUES (
                    'episode_create',
                    'in_progress',
                    CURRENT_TIMESTAMP - INTERVAL '30 minutes',
                    CURRENT_TIMESTAMP + INTERVAL '1 hour'
                )
                RETURNING id
                """
            )
            claimed = _json(await conn.fetchval("SELECT claim_recmem_consolidation_task(1)"))
            assert claimed["id"] == str(stale_task)
            assert claimed["status"] == "in_progress"
            assert claimed["attempts"] == 1
        finally:
            await tr.rollback()


async def test_recmem_embedding_and_routing_stale_recovery(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            raw = _json(await conn.fetchval(
                "SELECT recmem_ingest_turn('stale embed', 'ok', NULL, 'stale-embed')"
            ))
            await conn.execute(
                """
                UPDATE subconscious_units
                SET embedding_status = 'in_progress',
                    embedding_claimed_at = CURRENT_TIMESTAMP - INTERVAL '10 minutes'
                WHERE id = $1::uuid
                """,
                raw["unit_id"],
            )
            claimed = _json(await conn.fetchval("SELECT claim_recmem_unembedded_batch(5, 1)"))
            assert str(raw["unit_id"]) in {str(item["unit_id"]) for item in claimed}

            unit_id = await _insert_embedded_unit(conn, "stale-route", route_status="routing")
            await conn.execute(
                "UPDATE subconscious_units SET last_routed_at = CURRENT_TIMESTAMP - INTERVAL '10 minutes' WHERE id = $1",
                unit_id,
            )
            routed = _json(await conn.fetchval("SELECT claim_recmem_unrouted_batch(5, 1)"))
            assert str(unit_id) in {str(item["unit_id"]) for item in routed}
        finally:
            await tr.rollback()


async def test_recmem_route_merge_raw_only_and_create_paths(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute("SELECT set_config('memory.recmem_theta_count', '3'::jsonb)")
            target_mem = await _insert_memory(conn, "existing apple episode", axis=1)
            merge_unit = await _insert_embedded_unit(conn, "apple recurrence", axis=1, route_status="routing")

            merge_result = _json(await conn.fetchval("SELECT recmem_route_unit($1::uuid)", merge_unit))
            assert merge_result["status"] == "merge_queued"
            merge_status = await conn.fetchval("SELECT route_status FROM subconscious_units WHERE id = $1", merge_unit)
            assert merge_status == "merge_queued"
            task = await conn.fetchrow(
                "SELECT task_type, target_memory_id FROM recmem_consolidation_tasks WHERE id = $1",
                merge_result["task_id"],
            )
            assert task["task_type"] == "episode_merge"
            assert task["target_memory_id"] == target_mem

            isolated = await _insert_embedded_unit(conn, "isolated raw only", axis=2, route_status="routing")
            raw_only = _json(await conn.fetchval("SELECT recmem_route_unit($1::uuid)", isolated))
            assert raw_only["status"] == "raw_only"

            create_units = [
                await _insert_embedded_unit(conn, f"cluster-{idx}", axis=3, route_status="routing")
                for idx in range(3)
            ]
            create_result = _json(await conn.fetchval("SELECT recmem_route_unit($1::uuid)", create_units[0]))
            assert create_result["status"] == "create_queued"
            statuses = await conn.fetch(
                "SELECT route_status FROM subconscious_units WHERE id = ANY($1::uuid[])",
                create_units,
            )
            assert {row["route_status"] for row in statuses} == {"create_queued"}
        finally:
            await tr.rollback()


async def test_recmem_merge_rejection_falls_back_to_recurrence_create(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await _stub_get_embedding(conn)
            await conn.execute("SELECT set_config('memory.recmem_theta_count', '3'::jsonb)")
            target_mem = await _insert_memory(conn, "existing rejected episode", axis=1)
            units = [
                await _insert_embedded_unit(conn, f"merge-reject-{idx}", axis=1, route_status="raw_only")
                for idx in range(3)
            ]
            await conn.execute(
                "UPDATE subconscious_units SET route_status = 'merge_queued' WHERE id = $1",
                units[0],
            )
            merge_task = await conn.fetchval(
                """
                INSERT INTO recmem_consolidation_tasks (
                    task_type, trigger_unit_id, target_memory_id, source_unit_ids
                )
                VALUES ('episode_merge', $1, $2, ARRAY[$1]::uuid[])
                RETURNING id
                """,
                units[0],
                target_mem,
            )

            result = _json(await conn.fetchval(
                "SELECT apply_recmem_episode_merge($1::uuid, NULL, false)",
                merge_task,
            ))
            completed_merge = await conn.fetchrow(
                "SELECT status, result FROM recmem_consolidation_tasks WHERE id = $1",
                merge_task,
            )
            create_tasks = await conn.fetch(
                """
                SELECT id, source_unit_ids
                FROM recmem_consolidation_tasks
                WHERE task_type = 'episode_create'
                  AND status = 'pending'
                """
            )
            statuses = await conn.fetch(
                "SELECT id, route_status, route_result FROM subconscious_units WHERE id = ANY($1::uuid[])",
                units,
            )

            assert result["merged"] is False
            assert completed_merge["status"] == "completed"
            assert _json(completed_merge["result"])["merged"] is False
            assert len(create_tasks) == 1
            assert set(create_tasks[0]["source_unit_ids"]) == set(units)
            assert {row["route_status"] for row in statuses} == {"create_queued"}
            rejected = [_json(row["route_result"]) for row in statuses if row["id"] == units[0]][0]
            assert rejected["decision"] == "create_queued"
            assert rejected["merge_rejected"] is True
            assert rejected["merge_rejected_target_memory_id"] == str(target_mem)
        finally:
            await tr.rollback()


async def test_recmem_open_create_overlap_suppresses_duplicate_create(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute("SELECT set_config('memory.recmem_theta_count', '3'::jsonb)")
            units = [
                await _insert_embedded_unit(conn, f"overlap-create-{idx}", axis=2, route_status="raw_only")
                for idx in range(3)
            ]
            await conn.execute(
                "UPDATE subconscious_units SET route_status = 'routing' WHERE id = $1",
                units[0],
            )
            open_task = await conn.fetchval(
                """
                INSERT INTO recmem_consolidation_tasks (
                    task_type, trigger_unit_id, source_unit_ids, status
                )
                VALUES ('episode_create', $1, ARRAY[$2]::uuid[], 'pending')
                RETURNING id
                """,
                units[1],
                units[1],
            )

            result = _json(await conn.fetchval("SELECT recmem_route_unit($1::uuid)", units[0]))
            task_count = await conn.fetchval(
                "SELECT COUNT(*) FROM recmem_consolidation_tasks WHERE task_type = 'episode_create'"
            )
            route_row = await conn.fetchrow(
                "SELECT route_status, route_result FROM subconscious_units WHERE id = $1",
                units[0],
            )

            assert open_task
            assert result["status"] == "raw_only"
            assert result["reason"] == "open_create_overlap"
            assert task_count == 1
            assert route_row["route_status"] == "raw_only"
            assert _json(route_row["route_result"])["reason"] == "open_create_overlap"
        finally:
            await tr.rollback()


async def test_recmem_task_claim_and_apply_transitions(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await _stub_get_embedding(conn)
            source_unit = await _insert_embedded_unit(conn, "apply-merge", axis=1, route_status="merge_queued")
            target_mem = await _insert_memory(conn, "old episode", axis=1)
            merge_task = await conn.fetchval(
                """
                INSERT INTO recmem_consolidation_tasks (
                    task_type, trigger_unit_id, target_memory_id, source_unit_ids
                )
                VALUES ('episode_merge', $1, $2, ARRAY[$1]::uuid[])
                RETURNING id
                """,
                source_unit,
                target_mem,
            )
            merge_result = _json(await conn.fetchval(
                "SELECT apply_recmem_episode_merge($1::uuid, 'new merged episode', true)",
                merge_task,
            ))
            assert merge_result["merged"] is True
            assert await conn.fetchval("SELECT route_status FROM subconscious_units WHERE id = $1", source_unit) == "merged"

            create_units = [
                await _insert_embedded_unit(conn, f"apply-create-{idx}", axis=2, route_status="create_queued")
                for idx in range(2)
            ]
            create_task = await conn.fetchval(
                """
                INSERT INTO recmem_consolidation_tasks (
                    task_type, trigger_unit_id, source_unit_ids
                )
                VALUES ('episode_create', $1, $2::uuid[])
                RETURNING id
                """,
                create_units[0],
                create_units,
            )
            create_result = _json(await conn.fetchval(
                "SELECT apply_recmem_episode_create($1::uuid, $2::jsonb)",
                create_task,
                json.dumps([{"content": "created episode", "importance": 0.7}]),
            ))
            assert create_result["memory_ids"]
            statuses = await conn.fetch(
                "SELECT route_status FROM subconscious_units WHERE id = ANY($1::uuid[])",
                create_units,
            )
            assert {row["route_status"] for row in statuses} == {"episode_created"}

            empty_unit = await _insert_embedded_unit(conn, "apply-create-empty", axis=3, route_status="create_queued")
            empty_task = await conn.fetchval(
                """
                INSERT INTO recmem_consolidation_tasks (
                    task_type, trigger_unit_id, source_unit_ids
                )
                VALUES ('episode_create', $1, ARRAY[$1]::uuid[])
                RETURNING id
                """,
                empty_unit,
            )
            empty_result = _json(await conn.fetchval(
                "SELECT apply_recmem_episode_create($1::uuid, '[]'::jsonb)",
                empty_task,
            ))
            assert empty_result["empty"] is True
            assert await conn.fetchval("SELECT route_status FROM subconscious_units WHERE id = $1", empty_unit) == "raw_only"
        finally:
            await tr.rollback()


async def test_recmem_semantic_dedupe_unhealthy_and_recall_context(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await _stub_get_embedding(conn, axis=1)
            source_unit = await _insert_embedded_unit(conn, "semantic-source", axis=1)
            episode_id = await _insert_memory(conn, "episode from source", axis=1)
            await conn.fetchval("SELECT link_memory_to_source_unit($1, $2, 'source')", episode_id, source_unit)
            task_id = await conn.fetchval(
                """
                INSERT INTO recmem_consolidation_tasks (
                    task_type, target_memory_id, source_unit_ids
                )
                VALUES ('semantic_refine', $1, ARRAY[$2]::uuid[])
                RETURNING id
                """,
                episode_id,
                source_unit,
            )

            first = _json(await conn.fetchval(
                "SELECT apply_recmem_semantic_facts($1::uuid, $2::jsonb)",
                task_id,
                json.dumps([{"content": "User prefers apples", "importance": 0.8}]),
            ))
            assert len(first["memory_ids"]) == 1

            second_task = await conn.fetchval(
                """
                INSERT INTO recmem_consolidation_tasks (
                    task_type, target_memory_id, source_unit_ids
                )
                VALUES ('semantic_refine', $1, ARRAY[$2]::uuid[])
                RETURNING id
                """,
                episode_id,
                source_unit,
            )
            second = _json(await conn.fetchval(
                "SELECT apply_recmem_semantic_facts($1::uuid, $2::jsonb)",
                second_task,
                json.dumps([{"content": "User prefers apples", "importance": 0.8}]),
            ))
            assert second["memory_ids"] == []

            await conn.execute(
                "UPDATE subconscious_units SET embedding_status = 'failed', embedding_attempts = 3 WHERE id = $1",
                source_unit,
            )
            unhealthy = await conn.fetch("SELECT kind FROM recmem_unhealthy_items()")
            assert "embedding" in {row["kind"] for row in unhealthy}

            recall_rows = await conn.fetch("SELECT * FROM recmem_recall_context('apples', 5, 5, 5)")
            assert {"subconscious", "episodic", "semantic"} & {row["tier"] for row in recall_rows}
            assert any(source_unit in (row["source_unit_ids"] or []) for row in recall_rows)
        finally:
            await tr.rollback()


async def test_recmem_redaction_invalidates_derived_memory(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            raw = _json(await conn.fetchval(
                "SELECT recmem_ingest_turn('forget this later', 'ok', NULL, 'redact-source')"
            ))
            mem_id = await conn.fetchval(
                """
                SELECT create_memory_with_embedding(
                    'episodic'::memory_type,
                    'derived episode',
                    array_fill(0.1, ARRAY[embedding_dimension()])::vector,
                    0.5,
                    NULL,
                    0.9
                )
                """
            )
            await conn.fetchval(
                "SELECT link_memory_to_source_unit($1::uuid, $2::uuid, 'source')",
                mem_id,
                raw["unit_id"],
            )

            result = _json(await conn.fetchval(
                "SELECT recmem_redact_unit($1::uuid, 'test redaction', true)",
                raw["unit_id"],
            ))
            row = await conn.fetchrow(
                "SELECT status, valid_until, metadata#>>'{recmem,invalidation,reason}' AS reason FROM memories WHERE id = $1",
                mem_id,
            )

            assert str(mem_id) in {str(v) for v in result["invalidated_memory_ids"]}
            assert row["status"] == "active"
            assert row["valid_until"] is not None
            assert row["reason"] == "source_redacted"
        finally:
            await tr.rollback()


async def test_recmem_sweep_schedule_state(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute("SELECT set_config('memory.recmem_sweep_interval_seconds', '86400'::jsonb)")
            await conn.execute("DELETE FROM state WHERE key = 'recmem_state'")

            assert await conn.fetchval("SELECT should_run_recmem_sweep()") is True
            state = _json(await conn.fetchval("SELECT mark_recmem_sweep_run('{\"processed\": 2}'::jsonb)"))
            assert state["last_sweep_result"]["processed"] == 2
            assert await conn.fetchval("SELECT should_run_recmem_sweep()") is False

            await conn.execute(
                """
                UPDATE state
                SET value = jsonb_set(
                    value,
                    '{last_sweep_at}',
                    to_jsonb((CURRENT_TIMESTAMP - INTERVAL '2 days')::text)
                )
                WHERE key = 'recmem_state'
                """
            )
            assert await conn.fetchval("SELECT should_run_recmem_sweep()") is True
        finally:
            await tr.rollback()


