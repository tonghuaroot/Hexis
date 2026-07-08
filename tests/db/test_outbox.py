"""Tests for the DB-native transactional outbox (db/42_functions_outbox.sql).

Replaces the broken `INSERT INTO external_calls` path in the queue_user_message
tool: tools durably enqueue via queue_outbox_message; the maintenance worker
claims → publishes → marks published (or requeues on failure).
"""
from __future__ import annotations

import json

import pytest

pytestmark = [pytest.mark.asyncio(loop_scope="session")]


def _j(v):
    return json.loads(v) if isinstance(v, str) else v


async def test_queue_claim_publish_lifecycle(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            mid = await conn.fetchval(
                "SELECT queue_outbox_message($1::text, $2::text, 'tool')",
                "Hello Eric, checking in.", "status",
            )
            assert mid is not None

            # Row is pending with a build_user_message envelope.
            row = await conn.fetchrow("SELECT status, envelope FROM outbox_messages WHERE id = $1", mid)
            assert row["status"] == "pending"
            env = _j(row["envelope"])
            assert env["kind"] == "user"
            assert env["payload"]["message"] == "Hello Eric, checking in."
            assert env["payload"]["intent"] == "status"

            # Claim marks it publishing and returns the envelope for delivery.
            claimed = _j(await conn.fetchval("SELECT claim_pending_outbox(50)"))
            assert [c["id"] for c in claimed] == [str(mid)]
            assert claimed[0]["envelope"]["payload"]["message"] == "Hello Eric, checking in."
            assert await conn.fetchval("SELECT status FROM outbox_messages WHERE id = $1", mid) == "publishing"

            # A second claim returns nothing (already publishing, not stale).
            assert _j(await conn.fetchval("SELECT claim_pending_outbox(50)")) == []

            # Marking published closes it out.
            n = await conn.fetchval("SELECT mark_outbox_published($1::uuid[])", [mid])
            assert n == 1
            assert await conn.fetchval("SELECT status FROM outbox_messages WHERE id = $1", mid) == "published"
        finally:
            await tr.rollback()


async def test_requeue_returns_to_pending(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            mid = await conn.fetchval("SELECT queue_outbox_message('retry me', NULL, 'tool')")
            await conn.fetchval("SELECT claim_pending_outbox(50)")  # -> publishing
            n = await conn.fetchval("SELECT requeue_outbox($1::uuid[])", [mid])
            assert n == 1
            assert await conn.fetchval("SELECT status FROM outbox_messages WHERE id = $1", mid) == "pending"
            # It is claimable again.
            claimed = _j(await conn.fetchval("SELECT claim_pending_outbox(50)"))
            assert [c["id"] for c in claimed] == [str(mid)]
        finally:
            await tr.rollback()


async def test_stale_publishing_is_reclaimed(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            mid = await conn.fetchval("SELECT queue_outbox_message('stuck', NULL, 'tool')")
            await conn.fetchval("SELECT claim_pending_outbox(50)")  # -> publishing
            # Simulate a publisher that died mid-flight.
            await conn.execute(
                "UPDATE outbox_messages SET claimed_at = CURRENT_TIMESTAMP - interval '10 minutes' WHERE id = $1",
                mid,
            )
            reclaimed = _j(await conn.fetchval("SELECT claim_pending_outbox(50, 60)"))
            assert [c["id"] for c in reclaimed] == [str(mid)]
        finally:
            await tr.rollback()


async def test_empty_message_rejected(db_pool):
    async with db_pool.acquire() as conn:
        with pytest.raises(Exception):
            await conn.fetchval("SELECT queue_outbox_message('   ', NULL, 'tool')")
