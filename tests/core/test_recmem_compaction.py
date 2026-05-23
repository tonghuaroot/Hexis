from __future__ import annotations

from uuid import uuid4

import pytest

from tests.utils import _db_dsn

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.core]


async def test_compaction_flush_raw_ingest_dedupes_and_does_not_resurrect_redacted(db_pool):
    from channels.conversation import _flush_trimmed_to_memory

    session_id = str(uuid4())
    messages = [
        {"role": "user", "content": "remember my compaction fruit is pear"},
        {"role": "assistant", "content": "noted"},
    ]

    assert await _flush_trimmed_to_memory(_db_dsn(), messages, session_id) == 1
    assert await _flush_trimmed_to_memory(_db_dsn(), messages, session_id) == 1

    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, status
            FROM subconscious_units
            WHERE source_identity LIKE $1
            """,
            f"compaction:{session_id}:%",
        )
        assert len(rows) == 1
        assert rows[0]["status"] == "active"

        await conn.fetchval("SELECT recmem_redact_unit($1, 'compaction test', true)", rows[0]["id"])

    assert await _flush_trimmed_to_memory(_db_dsn(), messages, session_id) == 1

    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, status
            FROM subconscious_units
            WHERE source_identity LIKE $1
            """,
            f"compaction:{session_id}:%",
        )
        assert len(rows) == 1
        assert rows[0]["status"] == "redacted"

    async with db_pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM subconscious_units WHERE source_identity LIKE $1",
            f"compaction:{session_id}:%",
        )
