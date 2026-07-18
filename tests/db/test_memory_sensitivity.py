"""Privacy enforcement (#92): the 'sensitivity' marking survives every write
path, keeps private memories out of group-channel recall (while 1:1 recall
sees everything), propagates from sources to derived memories, and gates HMX
export behind an explicit opt-in.
"""
from __future__ import annotations

import json
import uuid

import pytest

from tests.utils import get_test_identifier

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.db]


async def _stub_get_embedding(conn):
    """Axis-orthogonal deterministic embeddings: distinct texts land on
    distinct axes so the ingest router never dedups across them."""
    await conn.execute(
        """
        CREATE OR REPLACE FUNCTION get_embedding(text_contents TEXT[])
        RETURNS vector[] AS $$
            SELECT COALESCE(array_agg((
                array_fill(0.01::float, ARRAY[2 + abs(hashtext(t)) % (embedding_dimension() - 2)]) ||
                ARRAY[1.0::float] ||
                array_fill(0.01::float, ARRAY[embedding_dimension() - 3 - abs(hashtext(t)) % (embedding_dimension() - 2)])
            )::vector), ARRAY[]::vector[])
            FROM unnest(text_contents) t
        $$ LANGUAGE sql;
        """
    )


async def _seed_memory(conn, content: str, *, mem_type: str = "semantic",
                       sensitivity: str | None = None, query_text: str | None = None):
    """Seed an active memory whose embedding matches get_embedding(query_text)
    so it ranks at the top for that query."""
    attribution = {"kind": "conversation", "trust": 0.8}
    if sensitivity:
        attribution["sensitivity"] = sensitivity
    return await conn.fetchval(
        """
        INSERT INTO memories (type, content, embedding, importance, trust_level, status, source_attribution)
        VALUES ($1::memory_type, $2, (get_embedding(ARRAY[$3]))[1], 0.5, 0.8, 'active', $4::jsonb)
        RETURNING id
        """,
        mem_type, content, query_text or content, json.dumps(attribution),
    )


async def _seed_unit(conn, content: str, *, sensitivity: str | None = None,
                     query_text: str | None = None):
    attribution = {"kind": "conversation", "trust": 0.8}
    if sensitivity:
        attribution["sensitivity"] = sensitivity
    return await conn.fetchval(
        """
        INSERT INTO subconscious_units
            (content, user_text, assistant_text, embedding, embedding_status,
             status, importance, source_attribution, idempotency_key)
        VALUES ($1, $1, '', (get_embedding(ARRAY[$2]))[1], 'embedded',
                'active', 0.5, $3::jsonb, $4)
        RETURNING id
        """,
        content, query_text or content, json.dumps(attribution),
        f"test:{uuid.uuid4().hex}",
    )


async def test_normalizer_preserves_private(db_pool):
    async with db_pool.acquire() as conn:
        normalized = json.loads(await conn.fetchval(
            "SELECT normalize_source_reference('{\"kind\": \"conversation\", \"sensitivity\": \"private\"}'::jsonb)"
        ))
        undefined_level = json.loads(await conn.fetchval(
            "SELECT normalize_source_reference('{\"kind\": \"conversation\", \"sensitivity\": \"secret\"}'::jsonb)"
        ))
    assert normalized["sensitivity"] == "private"
    # 'private' is the one defined level; anything else is dropped rather
    # than stored as a marking that nothing enforces.
    assert "sensitivity" not in undefined_level


async def test_group_recall_excludes_private_one_to_one_includes(db_pool):
    query = f"sensitivity recall probe {get_test_identifier("sensitivity")}"
    private_content = f"private diary entry {get_test_identifier("sensitivity")}"
    public_content = f"shareable note {get_test_identifier("sensitivity")}"
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await _stub_get_embedding(conn)
            await _seed_memory(conn, private_content, sensitivity="private", query_text=query)
            await _seed_memory(conn, public_content, query_text=query)
            await _seed_unit(conn, private_content, sensitivity="private", query_text=query)
            await _seed_unit(conn, public_content, query_text=query)

            everything = await conn.fetch(
                "SELECT content FROM recmem_recall_context($1, 10, 5, 10, NULL, FALSE)", query
            )
            filtered = await conn.fetch(
                "SELECT content FROM recmem_recall_context($1, 10, 5, 10, NULL, TRUE)", query
            )
        finally:
            await tr.rollback()

    everything_contents = " || ".join(r["content"] or "" for r in everything)
    filtered_contents = " || ".join(r["content"] or "" for r in filtered)
    assert private_content in everything_contents  # 1:1 keeps full recall
    assert public_content in everything_contents
    assert private_content not in filtered_contents  # group room recall
    assert public_content in filtered_contents


async def test_chat_turn_context_marks_unit_private(db_pool):
    marker = get_test_identifier("sensitivity")
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await _stub_get_embedding(conn)
            result = json.loads(await conn.fetchval(
                "SELECT record_chat_turn_memory($1, $2, NULL, NULL, $3::jsonb)",
                f"a private aside {marker}", f"acknowledged {marker}",
                json.dumps({"sensitivity": "private"}),
            ))
            attribution = json.loads(await conn.fetchval(
                "SELECT source_attribution FROM subconscious_units WHERE id = $1",
                uuid.UUID(result["raw_unit_id"]),
            ))
        finally:
            await tr.rollback()
    assert attribution["sensitivity"] == "private"


async def test_extraction_propagates_sensitivity(db_pool):
    fact = f"the user's private project is codenamed {get_test_identifier("sensitivity")}"
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await _stub_get_embedding(conn)
            unit_id = await _seed_unit(
                conn, f"private turn {get_test_identifier("sensitivity")}", sensitivity="private"
            )
            result = json.loads(await conn.fetchval(
                "SELECT apply_conscious_extraction(ARRAY[$1]::uuid[], $2::jsonb)",
                unit_id,
                json.dumps([{"content": fact, "confidence": 0.9,
                             "kind": "user_testimony", "unit_id": str(unit_id)}]),
            ))
            assert result.get("created", 0) >= 1 or result.get("created_ids"), result
            attribution = json.loads(await conn.fetchval(
                "SELECT source_attribution FROM memories WHERE content = $1", fact
            ))
        finally:
            await tr.rollback()
    assert attribution["sensitivity"] == "private"


async def test_scene_consolidation_propagates_sensitivity(db_pool):
    scene = f"an evening walk we agreed to keep between us {get_test_identifier("sensitivity")}"
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await _stub_get_embedding(conn)
            private_unit = await _seed_unit(
                conn, f"private half {get_test_identifier("sensitivity")}", sensitivity="private"
            )
            public_unit = await _seed_unit(conn, f"public half {get_test_identifier("sensitivity")}")
            task_id = await conn.fetchval(
                """
                INSERT INTO recmem_consolidation_tasks (task_type, source_unit_ids, status)
                VALUES ('episode_create', ARRAY[$1, $2]::uuid[], 'in_progress')
                RETURNING id
                """,
                private_unit, public_unit,
            )
            result = json.loads(await conn.fetchval(
                "SELECT apply_recmem_episode_create($1, $2::jsonb)",
                task_id, json.dumps([{"content": scene, "importance": 0.6}]),
            ))
            memory_ids = result.get("memory_ids") or []
            assert memory_ids, result
            attribution = json.loads(await conn.fetchval(
                "SELECT source_attribution FROM memories WHERE id = $1",
                uuid.UUID(memory_ids[0]),
            ))
        finally:
            await tr.rollback()
    assert attribution["sensitivity"] == "private"


async def test_retention_gist_inherits_private(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await _stub_get_embedding(conn)
            first = await _seed_memory(
                conn, f"quiet confession {get_test_identifier("sensitivity")}",
                mem_type="episodic", sensitivity="private",
            )
            second = await _seed_memory(
                conn, f"ordinary tuesday {get_test_identifier("sensitivity")}", mem_type="episodic"
            )
            gist_id = await conn.fetchval(
                "SELECT consolidate_memory_group(ARRAY[$1, $2]::uuid[])", first, second
            )
            assert gist_id is not None
            attribution = json.loads(await conn.fetchval(
                "SELECT source_attribution FROM memories WHERE id = $1", gist_id
            ))
        finally:
            await tr.rollback()
    assert attribution["sensitivity"] == "private"


async def test_hmx_export_requires_opt_in_for_private(db_pool):
    private_content = f"private export probe {get_test_identifier("sensitivity")}"
    unit_content = f"private unit probe {get_test_identifier("sensitivity")}"
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await _stub_get_embedding(conn)
            await _seed_memory(conn, private_content, sensitivity="private")
            await _seed_unit(conn, unit_content, sensitivity="private")

            default_memories = await conn.fetchval(
                "SELECT hmx_export_memories(NULL, NULL, NULL, FALSE)::text"
            )
            opted_memories = await conn.fetchval(
                "SELECT hmx_export_memories(NULL, NULL, NULL, TRUE)::text"
            )
            default_units = await conn.fetchval(
                "SELECT hmx_export_raw_units(FALSE)::text"
            )
            opted_units = await conn.fetchval(
                "SELECT hmx_export_raw_units(TRUE)::text"
            )
        finally:
            await tr.rollback()

    assert private_content not in default_memories
    assert private_content in opted_memories
    assert unit_content not in default_units
    assert unit_content in opted_units
