"""Tests for DB-owned ingestion routing (db/41_functions_ingest.sql).

The dedup/related/create policy (thresholds + nearest-neighbor decision) moved
out of services/ingest.py:_create_semantic_memories into SQL. These exercise the
policy with controlled dummy vectors + threshold config, so they need no
embedding service.
"""
from __future__ import annotations

import json

import pytest

pytestmark = [pytest.mark.asyncio(loop_scope="session")]


async def _route(conn, fill: float) -> dict:
    raw = await conn.fetchval(
        "SELECT ingest_route_embedding(array_fill($1::float8, ARRAY[embedding_dimension()])::vector)",
        fill,
    )
    return json.loads(raw) if isinstance(raw, str) else raw


async def test_routing_thresholds_are_config_driven(db_pool):
    """sim vs (theta_dup, theta_related) decides duplicate / related / create."""
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            mid = await conn.fetchval(
                """
                INSERT INTO memories (type, content, embedding, importance, trust_level, status)
                VALUES ('semantic', 'phase5 routing probe',
                        array_fill(0.1, ARRAY[embedding_dimension()])::vector, 0.8, 0.9, 'active')
                RETURNING id
                """
            )

            # Identical vector => cosine similarity 1.0 => duplicate (default dup=0.92).
            r = await _route(conn, 0.1)
            assert r["decision"] == "duplicate"
            assert r["matched_memory_id"] == str(mid)
            assert float(r["similarity"]) == pytest.approx(1.0, abs=1e-6)

            # Push the dup threshold above 1.0 => the same 1.0 sim is now "related".
            await conn.execute("SELECT set_config('memory.ingest_theta_dup', '1.5')")
            assert (await _route(conn, 0.1))["decision"] == "related"

            # Push related above 1.0 too => "create".
            await conn.execute("SELECT set_config('memory.ingest_theta_related', '1.5')")
            assert (await _route(conn, 0.1))["decision"] == "create"

            # Opposite vector (cosine -1.0) is always below threshold => create.
            await conn.execute("SELECT set_config('memory.ingest_theta_dup', '0.92')")
            await conn.execute("SELECT set_config('memory.ingest_theta_related', '0.8')")
            far = await _route(conn, -0.1)
            assert far["decision"] == "create"
            assert far["matched_memory_id"] is None
        finally:
            await tr.rollback()


async def test_route_extractions_drops_below_confidence(db_pool):
    """ingest_route_extractions filters by confidence before embedding — a fully
    below-threshold batch returns an empty plan without calling get_embedding."""
    async with db_pool.acquire() as conn:
        raw = await conn.fetchval(
            "SELECT ingest_route_extractions($1::jsonb, $2::float)",
            json.dumps([
                {"content": "too uncertain", "confidence": 0.1},
                {"content": "also uncertain", "confidence": 0.2},
            ]),
            0.5,
        )
        plan = json.loads(raw) if isinstance(raw, str) else raw
        assert plan == []

        empty = await conn.fetchval(
            "SELECT ingest_route_extractions('[]'::jsonb, 0.0)"
        )
        assert (json.loads(empty) if isinstance(empty, str) else empty) == []
