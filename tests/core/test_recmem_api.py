from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from core.cognitive_memory_api import CognitiveMemory, MemoryType

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.core]


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_exc):
        return False


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _Acquire(self.conn)


class _Conn:
    def __init__(self):
        self.fetchval_calls = []
        self.fetch_calls = []
        self.fetchval_result = None
        self.fetch_rows = []

    async def fetchval(self, query, *args):
        self.fetchval_calls.append((query, args))
        return self.fetchval_result

    async def fetch(self, query, *args):
        self.fetch_calls.append((query, args))
        return self.fetch_rows


async def test_remember_turn_raw_passes_sql_args():
    unit_id = uuid4()
    conn = _Conn()
    conn.fetchval_result = json.dumps({"unit_id": str(unit_id), "status": "stored"})
    mem = CognitiveMemory(_Pool(conn))

    result = await mem.remember_turn_raw(
        "remember apples",
        "noted",
        session_id="not-a-uuid",
        source_identity="chat:1",
        importance=0.7,
        metadata={"channel": "test"},
    )

    assert result == {"unit_id": str(unit_id), "status": "stored"}
    _query, args = conn.fetchval_calls[0]
    assert args[0] == "remember apples"
    assert args[1] == "noted"
    assert args[2] is None
    assert args[3] == "chat:1"
    assert args[5] == 0.7
    assert json.loads(args[7]) == {"channel": "test"}


async def test_hydrate_recmem_maps_tiers_and_dedupes_raw_sources():
    raw_id = uuid4()
    derived_id = uuid4()
    standalone_raw_id = uuid4()
    conn = _Conn()
    conn.fetch_rows = [
        {
            "tier": "subconscious",
            "item_id": raw_id,
            "memory_type": "episodic",
            "content": "raw source should be hidden",
            "score": 0.9,
            "trust_level": 0.95,
            "source_attribution": {},
            "created_at": datetime.now(timezone.utc),
            "source_unit_ids": [],
        },
        {
            "tier": "episodic",
            "item_id": derived_id,
            "memory_type": "episodic",
            "content": "derived episode",
            "score": 0.8,
            "trust_level": 0.9,
            "source_attribution": {},
            "created_at": datetime.now(timezone.utc),
            "source_unit_ids": [raw_id],
        },
        {
            "tier": "subconscious",
            "item_id": standalone_raw_id,
            "memory_type": "episodic",
            "content": "standalone raw",
            "score": 0.7,
            "trust_level": 0.95,
            "source_attribution": {},
            "created_at": datetime.now(timezone.utc),
            "source_unit_ids": [],
        },
    ]
    mem = CognitiveMemory(_Pool(conn))

    memories = await mem.hydrate_recmem("apples", sub_limit=5, epi_limit=5, sem_limit=5)

    assert [m.id for m in memories] == [derived_id, standalone_raw_id]
    assert memories[0].tier == "episodic"
    assert memories[0].source_unit_ids == [raw_id]
    assert memories[0].type == MemoryType.EPISODIC
    assert conn.fetch_calls[0][1][0] == "apples"


async def test_link_and_redact_unit_call_recmem_sql():
    conn = _Conn()
    mem = CognitiveMemory(_Pool(conn))
    memory_id = uuid4()
    unit_id = uuid4()

    conn.fetchval_result = True
    assert await mem.link_to_source_unit(memory_id, unit_id, role="source") is True

    conn.fetchval_result = json.dumps({"redacted_unit_id": str(unit_id), "invalidated_memory_ids": [str(memory_id)]})
    result = await mem.redact_unit(unit_id, reason="test", cascade=True)

    assert result["redacted_unit_id"] == str(unit_id)
    assert "link_memory_to_source_unit" in conn.fetchval_calls[0][0]
    assert "recmem_redact_unit" in conn.fetchval_calls[1][0]
