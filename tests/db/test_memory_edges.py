"""Tests for the relational sub-knowledge-graph substrate (db/44_functions_memory_edges.sql).

Covers upsert_memory_edge (dual-write hook), build_context_subgraph (seeded,
bounded, weighted subgraph assembly), and render_subgraph. Uses synthetic edges
inserted directly via upsert_memory_edge, so no embedding service or AGE graph
is required -- the assembly is pure relational recursion.
"""
from __future__ import annotations

import json

import pytest

pytestmark = [pytest.mark.asyncio(loop_scope="session")]

# Synthetic node ids (never inserted into `memories`, so no triggers fire and the
# graph is fully deterministic; labels fall back to the node id).
A = "0aaaaaaa-0000-0000-0000-000000000001"
B = "0aaaaaaa-0000-0000-0000-000000000002"
C = "0aaaaaaa-0000-0000-0000-000000000003"
CLUSTER = "0ccccccc-0000-0000-0000-000000000001"
CONCEPT = "topic_x"


def _j(v):
    return json.loads(v) if isinstance(v, str) else v


async def _seed(conn):
    """A --causes--> B --causes--> C ; A --instance_of--> concept ; A --member_of--> cluster."""
    async def edge(st, si, rel, dt, di, w=1.0):
        await conn.execute(
            "SELECT upsert_memory_edge($1::text,$2::text,$3::text,$4::text,$5::text,$6::float,"
            "NULL::text,NULL::text,'{}'::jsonb)",
            st, si, rel, dt, di, w,
        )
    await edge("memory", A, "CAUSES", "memory", B, 0.8)
    await edge("memory", B, "CAUSES", "memory", C, 0.9)
    await edge("memory", A, "INSTANCE_OF", "concept", CONCEPT, 0.7)
    await edge("memory", A, "MEMBER_OF", "cluster", CLUSTER, 0.6)


async def _subgraph(conn, seeds, depth, rel_types, budget):
    return _j(await conn.fetchval(
        "SELECT build_context_subgraph($1::uuid[], $2, $3::text[], $4)",
        seeds, depth, rel_types, budget,
    ))


def _ids(sg):
    return {n["id"] for n in sg["nodes"]}


def _typed(sg):
    return {(n["type"], n["id"]) for n in sg["nodes"]}


async def test_dual_write_upsert_is_idempotent(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute(
                "SELECT upsert_memory_edge('memory',$1,'CAUSES','memory',$2,0.5,NULL,NULL,'{}'::jsonb)", A, B)
            await conn.execute(
                "SELECT upsert_memory_edge('memory',$1,'CAUSES','memory',$2,0.9,NULL,NULL,'{}'::jsonb)", A, B)
            # Same natural key -> one row, weight updated to the latest.
            n = await conn.fetchval(
                "SELECT count(*) FROM memory_edges WHERE src_id=$1 AND dst_id=$2 AND rel_type='CAUSES'", A, B)
            w = await conn.fetchval(
                "SELECT weight FROM memory_edges WHERE src_id=$1 AND dst_id=$2 AND rel_type='CAUSES'", A, B)
            assert n == 1
            assert abs(w - 0.9) < 1e-9
        finally:
            await tr.rollback()


async def test_full_subgraph_reaches_causal_chain_and_bridges(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await _seed(conn)
            sg = await _subgraph(conn, [A], 3, None, 40)
            # A (seed) + B + C (2-hop causal) + concept + cluster.
            assert _ids(sg) == {A, B, C, CONCEPT, CLUSTER}
            # heterogeneous-node resolution: correct node types.
            assert ("concept", CONCEPT) in _typed(sg)
            assert ("cluster", CLUSTER) in _typed(sg)
            assert ("memory", C) in _typed(sg)
            # every edge connects two kept nodes (no dangling edges).
            kept = _ids(sg)
            for e in sg["edges"]:
                assert e["src_id"] in kept and e["dst_id"] in kept
        finally:
            await tr.rollback()


async def test_depth_bound(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await _seed(conn)
            sg = await _subgraph(conn, [A], 1, None, 40)
            # depth 1 from A reaches direct neighbours only: B, concept, cluster -- not C.
            assert _ids(sg) == {A, B, CONCEPT, CLUSTER}
            assert C not in _ids(sg)
        finally:
            await tr.rollback()


async def test_rel_type_filter(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await _seed(conn)
            sg = await _subgraph(conn, [A], 3, ["CAUSES"], 40)
            # CAUSES-only expansion: the causal chain, no concept/cluster bridges.
            assert _ids(sg) == {A, B, C}
            assert all(e["rel"] == "CAUSES" for e in sg["edges"])
        finally:
            await tr.rollback()


async def test_budget_cap_keeps_seed(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await _seed(conn)
            sg = await _subgraph(conn, [A], 3, None, 1)
            # budget 1 -> only the seed (seeds sort first, depth 0).
            assert _ids(sg) == {A}
        finally:
            await tr.rollback()


async def test_empty_seed_returns_empty(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            sg = await _subgraph(conn, [], 3, None, 40)
            assert sg["nodes"] == [] and sg["edges"] == []
        finally:
            await tr.rollback()


async def test_render_subgraph_shows_typed_edges(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await _seed(conn)
            rendered = await conn.fetchval(
                "SELECT render_subgraph(build_context_subgraph($1::uuid[], 3, ARRAY['CAUSES'], 40))", [A])
            # One line per causal edge, using node ids as labels (no memories rows).
            assert rendered is not None
            assert "causes" in rendered
            assert rendered.count("—causes→") == 2
        finally:
            await tr.rollback()
