"""Tests for rest-cycle consolidation (db/47_functions_retention.sql) -- Phase 2
of docs/memory_retention_design.md. Protection, merge-to-gist, distill-upward.
Needs the embedding service (gist/lesson embeddings) + AGE (provenance edges)."""
from __future__ import annotations

import json

import pytest

pytestmark = [pytest.mark.asyncio(loop_scope="session")]

_DUMMY = "array_fill(0.1, ARRAY[embedding_dimension()])::vector"


def _j(v):
    return json.loads(v) if isinstance(v, str) else v


async def _mk(conn, content, *, importance=0.3, mtype="episodic", age_days=0, metadata="{}"):
    return await conn.fetchval(
        f"INSERT INTO memories (type, content, embedding, importance, trust_level, status, created_at, metadata) "
        f"VALUES ($1::memory_type, $2, {_DUMMY}, $3, 0.9, 'active', now() - ($4 || ' days')::interval, $5::jsonb) "
        f"RETURNING id",
        mtype, content, importance, str(age_days), metadata,
    )


async def test_is_memory_protected(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            mundane = await _mk(conn, "ordinary tuesday", importance=0.3)
            important = await _mk(conn, "the vital thing", importance=0.95)
            emotional = await _mk(conn, "intense", importance=0.3, metadata='{"emotional_context":{"intensity":0.9}}')
            pinned = await _mk(conn, "pinned", importance=0.3, metadata='{"protected":true}')
            worldview = await _mk(conn, "a belief", mtype="worldview")
            assert await conn.fetchval("SELECT is_memory_protected($1)", mundane) is False
            for prot in (important, emotional, pinned, worldview):
                assert await conn.fetchval("SELECT is_memory_protected($1)", prot) is True
        finally:
            await tr.rollback()


async def test_consolidate_group_creates_gist_and_archives(db_pool):
    async with db_pool.acquire() as conn:
        await conn.execute("LOAD 'age'")
        tr = conn.transaction()
        await tr.start()
        try:
            ids = [await _mk(conn, f"market trip detail {i}", age_days=60) for i in range(3)]
            gist = await conn.fetchval("SELECT consolidate_memory_group($1::uuid[])", ids)
            assert gist is not None
            rows = await conn.fetch("SELECT status, superseded_by FROM memories WHERE id = ANY($1::uuid[])", ids)
            assert all(r["status"] == "archived" and r["superseded_by"] == gist for r in rows)
            assert await conn.fetchval("SELECT status FROM memories WHERE id=$1", gist) == "active"
            assert await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM memory_summarization_queue WHERE memory_id=$1)", gist)
            # the gist holds the full concatenated content until summarized
            assert await conn.fetchval("SELECT (metadata->'consolidation'->>'summarized')::bool FROM memories WHERE id=$1", gist) is False
        finally:
            await tr.rollback()


async def test_consolidate_skips_protected_members(db_pool):
    async with db_pool.acquire() as conn:
        await conn.execute("LOAD 'age'")
        tr = conn.transaction()
        await tr.start()
        try:
            a = await _mk(conn, "detail a", age_days=60)
            b = await _mk(conn, "detail b", age_days=60)
            precious = await _mk(conn, "a precious moment", importance=0.95, age_days=60)
            await conn.fetchval("SELECT consolidate_memory_group($1::uuid[])", [a, b, precious])
            # the protected member is never archived
            assert await conn.fetchval("SELECT status FROM memories WHERE id=$1", precious) == "active"
            assert await conn.fetchval("SELECT superseded_by IS NULL FROM memories WHERE id=$1", precious) is True
        finally:
            await tr.rollback()


async def test_apply_summary_compacts_and_distills(db_pool):
    async with db_pool.acquire() as conn:
        await conn.execute("LOAD 'age'")
        tr = conn.transaction()
        await tr.start()
        try:
            ids = [await _mk(conn, f"detail {i}", age_days=60) for i in range(3)]
            gist = await conn.fetchval("SELECT consolidate_memory_group($1::uuid[])", ids)
            res = _j(await conn.fetchval(
                "SELECT apply_memory_summary($1, $2, $3::jsonb)", gist, "a concise gist",
                json.dumps([{"content": "a distinct durable lesson zzq", "kind": "semantic"}])))
            assert res["lessons_created"] == 1
            row = await conn.fetchrow("SELECT content, fidelity FROM memories WHERE id=$1", gist)
            assert row["content"] == "a concise gist"
            assert row["fidelity"] < 1.0          # lossiness recorded
            assert await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM memories WHERE type='semantic' AND content LIKE '%durable lesson zzq%')")
        finally:
            await tr.rollback()


async def test_run_memory_rest_noop_when_disabled(db_pool):
    async with db_pool.acquire() as conn:
        assert _j(await conn.fetchval("SELECT run_memory_rest()")).get("skipped") is True
        assert _j(await conn.fetchval("SELECT run_retention_gc()")).get("skipped") is True


async def test_retention_status_snapshot(db_pool):
    """The operator-facing snapshot summarizes every part of the system."""
    async with db_pool.acquire() as conn:
        st = _j(await conn.fetchval("SELECT retention_status()"))
        assert "enabled" in st
        for section in ("episodic", "consolidation", "conscious_review", "documents"):
            assert section in st, section
        assert "mass" in st["episodic"] and "capacity" in st["episodic"]
        assert "candidate_groups" in st["consolidation"]
        assert "protected" in st["documents"] and "approvals_pending" in st["documents"]
