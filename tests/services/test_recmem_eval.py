from __future__ import annotations

import json

import pytest

from services.recmem_eval import run_recmem_eval_set

pytestmark = [pytest.mark.asyncio(loop_scope="session")]


async def _stub_get_embedding(conn):
    await conn.execute(
        """
        CREATE OR REPLACE FUNCTION get_embedding(text_contents TEXT[])
        RETURNS vector[] AS $$
            SELECT COALESCE(
                array_agg((
                    array_fill(0.0::float, ARRAY[0]) ||
                    ARRAY[1.0::float] ||
                    array_fill(0.0::float, ARRAY[embedding_dimension() - 1])
                )::vector),
                ARRAY[]::vector[]
            )
            FROM unnest(text_contents)
        $$ LANGUAGE sql;
        """
    )


async def test_run_recmem_eval_set_scores_expected_memory_hits(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await _stub_get_embedding(conn)
            memory_id = await conn.fetchval(
                """
                SELECT create_memory_with_embedding(
                    'semantic'::memory_type,
                    'The user prefers pears in the eval fixture.',
                    (
                        array_fill(0.0::float, ARRAY[0]) ||
                        ARRAY[1.0::float] ||
                        array_fill(0.0::float, ARRAY[embedding_dimension() - 1])
                    )::vector,
                    0.8,
                    NULL,
                    0.95
                )
                """
            )
            raw_id = await conn.fetchval(
                """
                INSERT INTO subconscious_units (
                    content, user_text, assistant_text, embedding, embedding_status,
                    route_status, idempotency_key
                )
                VALUES (
                    'User: I prefer pears\n\nAssistant: noted',
                    'I prefer pears',
                    'noted',
                    (
                        array_fill(0.0::float, ARRAY[0]) ||
                        ARRAY[1.0::float] ||
                        array_fill(0.0::float, ARRAY[embedding_dimension() - 1])
                    )::vector,
                    'embedded',
                    'episode_created',
                    'eval:raw:pear'
                )
                RETURNING id
                """
            )
            await conn.fetchval("SELECT link_memory_to_source_unit($1, $2, 'source')", memory_id, raw_id)

            eval_set = await conn.fetchval(
                "INSERT INTO recmem_eval_sets (name) VALUES ('service-eval') RETURNING id"
            )
            await conn.execute(
                """
                INSERT INTO recmem_eval_items (
                    eval_set_id, category, query_text, reference_answer, metadata
                )
                VALUES ($1, 'preference', 'What fruit does the user prefer?', 'pears', $2::jsonb)
                """,
                eval_set,
                json.dumps({"expected_memory_ids": [str(memory_id)]}),
            )

            summary = await run_recmem_eval_set(conn, "service-eval", label="unit", limit=5)
            result = await conn.fetchrow(
                """
                SELECT verdict, judge_score, baseline_memory_ids, recmem_memory_ids, metadata
                FROM recmem_eval_results
                WHERE run_id = $1::uuid
                """,
                summary["run_id"],
            )

            assert summary["status"] == "completed"
            assert summary["result_count"] == 1
            assert result["verdict"] == "pass"
            assert result["judge_score"] == 1.0
            assert memory_id in result["baseline_memory_ids"]
            assert memory_id in result["recmem_memory_ids"]
            metadata = result["metadata"]
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            assert metadata["baseline_hit_rate"] == 1.0
        finally:
            await tr.rollback()


async def test_run_recmem_eval_set_marks_unjudged_without_expectations(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await _stub_get_embedding(conn)
            eval_set = await conn.fetchval(
                "INSERT INTO recmem_eval_sets (name) VALUES ('service-eval-unjudged') RETURNING id"
            )
            await conn.execute(
                """
                INSERT INTO recmem_eval_items (eval_set_id, category, query_text)
                VALUES ($1, 'general', 'Unjudged query')
                """,
                eval_set,
            )

            summary = await run_recmem_eval_set(conn, "service-eval-unjudged", label="unit", limit=3)
            result = await conn.fetchrow(
                "SELECT verdict, judge_score FROM recmem_eval_results WHERE run_id = $1::uuid",
                summary["run_id"],
            )

            assert summary["status"] == "completed"
            assert result["verdict"] == "unjudged"
            assert result["judge_score"] is None
        finally:
            await tr.rollback()
