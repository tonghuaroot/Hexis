from __future__ import annotations

import json

import pytest

pytestmark = [pytest.mark.asyncio(loop_scope="session")]


def _coerce_json(value):
    if isinstance(value, str):
        return json.loads(value)
    return value


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


async def test_execute_goals_tool_create_and_list(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await _stub_get_embedding(conn)
            created = _coerce_json(
                await conn.fetchval(
                    "SELECT execute_goals_tool($1::jsonb)",
                    json.dumps({"action": "create", "title": "DB-native goal", "priority": "queued"}),
                )
            )
            listed = _coerce_json(
                await conn.fetchval("SELECT execute_goals_tool('{\"action\":\"list\"}'::jsonb)")
            )

            assert created["success"] is True
            assert created["output"]["title"] == "DB-native goal"
            assert listed["success"] is True
        finally:
            await tr.rollback()


async def test_execute_backlog_tool_create_status_and_get(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            created = _coerce_json(
                await conn.fetchval(
                    "SELECT execute_backlog_tool($1::jsonb, $2::jsonb)",
                    json.dumps({"action": "create", "title": "DB-native backlog", "priority": "high"}),
                    json.dumps({"tool_context": "heartbeat"}),
                )
            )
            item_id = created["output"]["item_id"]
            status = _coerce_json(
                await conn.fetchval(
                    "SELECT execute_backlog_tool($1::jsonb, $2::jsonb)",
                    json.dumps({"action": "set_status", "item_id": item_id, "status": "in_progress"}),
                    json.dumps({"tool_context": "heartbeat"}),
                )
            )
            got = _coerce_json(
                await conn.fetchval(
                    "SELECT execute_backlog_tool($1::jsonb, '{}'::jsonb)",
                    json.dumps({"action": "get", "item_id": item_id}),
                )
            )

            assert created["success"] is True
            assert status["success"] is True
            assert status["output"]["new_status"] == "in_progress"
            assert got["output"]["status"] == "in_progress"
        finally:
            await tr.rollback()


async def test_execute_contact_tool_create_search_get_update(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            created = _coerce_json(
                await conn.fetchval(
                    "SELECT execute_contact_tool('create_contact', $1::jsonb)",
                    json.dumps({"name": "Ada Lovelace", "email": "ada@example.com", "company": "Analytical"}),
                )
            )
            contact_id = created["output"]["id"]
            searched = _coerce_json(
                await conn.fetchval(
                    "SELECT execute_contact_tool('search_contacts', $1::jsonb)",
                    json.dumps({"query": "Ada", "limit": 5}),
                )
            )
            updated = _coerce_json(
                await conn.fetchval(
                    "SELECT execute_contact_tool('update_contact', $1::jsonb)",
                    json.dumps({"id": contact_id, "role": "Mathematician"}),
                )
            )
            got = _coerce_json(
                await conn.fetchval(
                    "SELECT execute_contact_tool('get_contact', $1::jsonb)",
                    json.dumps({"id": contact_id}),
                )
            )

            assert created["success"] is True
            assert searched["output"]["count"] >= 1
            assert updated["success"] is True
            assert got["output"]["contact"]["role"] == "Mathematician"
        finally:
            await tr.rollback()


async def test_execute_memory_tool_remember_sense_and_recall(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await _stub_get_embedding(conn)
            remembered = _coerce_json(
                await conn.fetchval(
                    "SELECT execute_memory_tool('remember', $1::jsonb)",
                    json.dumps({"content": "DB-native memory likes plums", "type": "semantic", "importance": 0.8}),
                )
            )
            recalled = _coerce_json(
                await conn.fetchval(
                    "SELECT execute_memory_tool('recall', $1::jsonb)",
                    json.dumps({"query": "plums", "limit": 5}),
                )
            )

            assert remembered["success"] is True
            assert recalled["success"] is True, recalled
            assert recalled["output"]["count"] >= 1
        finally:
            await tr.rollback()
