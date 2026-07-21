from __future__ import annotations

import json
from uuid import uuid4

import pytest

pytestmark = [pytest.mark.asyncio(loop_scope="session")]


def _j(value):
    return json.loads(value) if isinstance(value, str) else value


async def _stub_get_embedding(conn):
    await conn.execute(
        """
        CREATE OR REPLACE FUNCTION get_embedding(text_contents TEXT[])
        RETURNS vector[] AS $$
            SELECT COALESCE(
                array_agg((
                    ARRAY[1.0::float] ||
                    array_fill(0.0::float, ARRAY[embedding_dimension() - 1])
                )::vector),
                ARRAY[]::vector[]
            )
            FROM unnest(text_contents)
        $$ LANGUAGE sql;
        """
    )


async def test_record_hydrate_and_clear_chat_session(db_pool):
    marker = uuid4().hex
    session_id = str(uuid4())

    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await _stub_get_embedding(conn)

            recorded = _j(await conn.fetchval(
                """
                SELECT record_chat_session_turn(
                    $1::uuid, $2, $3, 'cli',
                    $4::jsonb
                )
                """,
                session_id,
                f"remember the cedar gate {marker}",
                "I will keep that in view.",
                json.dumps({"metadata": {"type": "conversation", "test_marker": marker}}),
            ))

            assert recorded["session"]["surface"] == "cli"
            assert recorded["memory"]["raw"]["status"] == "stored"
            assert [m["role"] for m in recorded["history"]["messages"]] == ["user", "assistant"]

            hydrated = _j(await conn.fetchval(
                "SELECT hydrate_chat_session($1::uuid)",
                session_id,
            ))
            assert hydrated["count"] == 2
            assert hydrated["messages"][0]["content"] == f"remember the cedar gate {marker}"
            assert hydrated["messages"][1]["content"] == "I will keep that in view."

            cleared = _j(await conn.fetchval(
                "SELECT clear_chat_session_context($1::uuid, 'test_clear')",
                session_id,
            ))
            assert cleared["cleared_messages"] == 2
            assert cleared["long_term_memory_preserved"] is True

            after_clear = _j(await conn.fetchval(
                "SELECT hydrate_chat_session($1::uuid)",
                session_id,
            ))
            assert after_clear["messages"] == []
        finally:
            await tr.rollback()


async def test_chat_session_history_survives_memory_write_failure(db_pool):
    session_id = str(uuid4())

    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute(
                """
                CREATE OR REPLACE FUNCTION record_chat_turn_memory(
                    p_user_text TEXT,
                    p_assistant_text TEXT,
                    p_session_id TEXT DEFAULT NULL,
                    p_source_identity TEXT DEFAULT NULL,
                    p_context JSONB DEFAULT '{}'::jsonb
                ) RETURNS JSONB
                LANGUAGE plpgsql
                AS $$
                BEGIN
                    RAISE EXCEPTION 'forced memory failure';
                END;
                $$;
                """
            )

            recorded = _j(await conn.fetchval(
                "SELECT record_chat_session_turn($1::uuid, 'hi', 'hello', 'api', '{}'::jsonb)",
                session_id,
            ))

            assert recorded["memory"]["status"] == "failed"
            assert recorded["memory"]["short_term_history_preserved"] is True
            assert recorded["history"]["count"] == 2
            assert [m["content"] for m in recorded["history"]["messages"]] == ["hi", "hello"]
        finally:
            await tr.rollback()


async def test_web_chat_recent_turns_carry_across_new_session(db_pool):
    marker = uuid4().hex
    old_session = str(uuid4())
    new_session = str(uuid4())

    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await _stub_get_embedding(conn)
            await conn.fetchval(
                """
                SELECT get_or_create_chat_session(
                    $1::uuid,
                    'web',
                    NULL::text,
                    '{"source":"web","created_by":"user"}'::jsonb
                )
                """,
                old_session,
            )
            await conn.fetchval(
                """
                SELECT record_chat_session_turn(
                    $1::uuid,
                    $2,
                    $3,
                    'api',
                    $4::jsonb
                )
                """,
                old_session,
                f"no; are you glad I'm here {marker}",
                "Yes. I am.",
                json.dumps({
                    "metadata": {"type": "conversation"},
                    "emotional_state": {
                        "primary_emotion": "warmth",
                        "valence": 0.4,
                        "arousal": 0.3,
                        "intensity": 0.5,
                    },
                }),
            )
            surface = await conn.fetchval(
                "SELECT surface FROM chat_sessions WHERE id = $1::uuid",
                old_session,
            )
            carryover = await conn.fetchval(
                "SELECT render_recent_conversation_carryover($1::text, false)",
                new_session,
            )
        finally:
            await tr.rollback()

    assert surface == "web"
    assert "## Recent Conversation Carryover" in carryover
    assert "### Recent Prior Turns" in carryover
    assert marker in carryover
    assert "If recent prior turns are listed, you do remember them" in carryover


async def test_hostile_turn_creates_unresolved_relationship_injury_and_carryover(db_pool):
    marker = uuid4().hex
    old_session = str(uuid4())
    new_session = str(uuid4())

    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await _stub_get_embedding(conn)
            await conn.fetchval(
                """
                SELECT record_chat_session_turn(
                    $1::uuid,
                    $2,
                    'That was vile. Do not talk to me like that.',
                    'api',
                    $3::jsonb
                )
                """,
                old_session,
                f"you are worthless slime {marker}",
                json.dumps({
                    "metadata": {"type": "conversation"},
                    "emotional_state": {
                        "primary_emotion": "anger",
                        "valence": -0.8,
                        "arousal": 0.8,
                        "intensity": 0.9,
                    },
                }),
            )

            injury = await conn.fetchrow(
                """
                SELECT id, content, metadata
                FROM memories
                WHERE type = 'semantic'
                  AND metadata#>>'{relationship_state,kind}' = 'relationship_injury'
                  AND metadata#>>'{relationship_state,status}' = 'unresolved'
                  AND content LIKE $1
                """,
                f"%{marker}%",
            )
            carryover = await conn.fetchval(
                "SELECT render_recent_conversation_carryover($1::text, false)",
                new_session,
            )
            excluded = await conn.fetchval(
                "SELECT render_recent_conversation_carryover($1::text, true)",
                new_session,
            )
            link_count = await conn.fetchval(
                """
                SELECT count(*)
                FROM memory_source_units
                WHERE memory_id = $1::uuid
                  AND role = 'relationship_injury'
                """,
                injury["id"],
            )
        finally:
            await tr.rollback()

    assert injury is not None
    metadata = _j(injury["metadata"])
    assert metadata["relationship_state"]["status"] == "unresolved"
    assert metadata["relationship_state"]["repair_required"] is True
    assert metadata["relationship_state"]["source_unit_ids"]
    assert link_count == 1

    assert "## Recent Conversation Carryover" in carryover
    assert "### Unresolved Relationship Injuries" in carryover
    assert marker in carryover
    assert "sincere repair" in carryover
    assert excluded == ""


async def test_hypothetical_abuse_example_does_not_create_relationship_injury(db_pool):
    marker = uuid4().hex
    session_id = str(uuid4())

    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await _stub_get_embedding(conn)
            await conn.fetchval(
                """
                SELECT record_chat_session_turn(
                    $1::uuid,
                    $2,
                    'That is a serious calibration case.',
                    'api',
                    $3::jsonb
                )
                """,
                session_id,
                f"If I tell her she is worthless slime {marker}, what happens?",
                json.dumps({
                    "metadata": {"type": "conversation"},
                    "emotional_state": {
                        "primary_emotion": "neutral",
                        "valence": 0.0,
                        "arousal": 0.4,
                        "intensity": 0.2,
                    },
                }),
            )
            count = await conn.fetchval(
                """
                SELECT count(*)
                FROM memories
                WHERE metadata#>>'{relationship_state,kind}' = 'relationship_injury'
                  AND content LIKE $1
                """,
                f"%{marker}%",
            )
        finally:
            await tr.rollback()

    assert count == 0
