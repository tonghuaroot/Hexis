from contextlib import asynccontextmanager
import asyncio
from uuid import uuid4

import pytest

from services import chat as chat_mod
from core.cognitive_memory_api import HydratedContext, Memory, MemoryType, RecallResult

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.core]


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_exc):
        return False


class _ConfigPool:
    def __init__(self, values):
        self.values = values

    def acquire(self):
        return _Acquire(self)

    async def fetchval(self, query):
        for key, value in self.values.items():
            if key in query:
                return value
        return None


class _RememberMem:
    def __init__(self, config=None):
        self._pool = _ConfigPool(config or {})
        self.raw_calls = []
        self.remember_calls = []
        self.link_calls = []
        self.record_chat_turn_memory_calls = []

    async def remember_turn_raw(self, *args, **kwargs):
        self.raw_calls.append((args, kwargs))
        return {"unit_id": str(uuid4()), "status": "stored"}

    async def remember(self, *args, **kwargs):
        self.remember_calls.append((args, kwargs))
        return uuid4()

    async def link_to_source_unit(self, *args, **kwargs):
        self.link_calls.append((args, kwargs))
        return True

    async def record_chat_turn_memory(self, *args, **kwargs):
        self.record_chat_turn_memory_calls.append((args, kwargs))
        return {"direct_promoted": True, "raw": {"status": "stored"}}


async def test_remember_conversation_calls_record_chat_turn_memory():
    mem = _RememberMem()

    await chat_mod._remember_conversation(  # noqa: SLF001
        mem,
        user_message="remember this important preference",
        assistant_message="noted",
        session_id=str(uuid4()),
        source_identity="chat:test",
    )

    assert len(mem.record_chat_turn_memory_calls) == 1
    assert mem.record_chat_turn_memory_calls[0][0][0] == "remember this important preference"


async def test_chat_turn_basic_flow(monkeypatch, db_pool):
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO config (key, value, description) VALUES ('chat.use_rlm', 'false', 'test override') "
            "ON CONFLICT (key) DO UPDATE SET value = 'false'"
        )

    class DummyMem:
        def __init__(self):
            self.remembered = []
            self.touched = []

        async def hydrate(self, *_args, **_kwargs):
            return HydratedContext(
                memories=[],
                partial_activations=[],
                identity=[],
                worldview=[],
                emotional_state=None,
                goals=None,
                urgent_drives=[],
            )

        async def touch_memories(self, ids):
            self.touched.extend(ids)

        async def remember(self, content, **_kwargs):
            self.remembered.append(content)
            return uuid4()

        async def record_chat_turn_memory(self, *_args, **_kwargs):
            self.remembered.append("turn_memory")
            return {"direct_promoted": False, "raw": {"status": "stored"}}

    mem = DummyMem()

    @asynccontextmanager
    async def fake_connect(_dsn, **_kwargs):
        yield mem

    async def fake_chat_completion(**_kwargs):
        return {"content": "hello there", "tool_calls": []}

    monkeypatch.setattr(chat_mod.CognitiveMemory, "connect", fake_connect)
    monkeypatch.setattr("core.agent_loop.chat_completion", fake_chat_completion)
    async def fake_agent_profile(_dsn=None, **_kwargs):
        return {}

    monkeypatch.setattr(chat_mod, "get_agent_profile_context", fake_agent_profile)

    result = await chat_mod.chat_turn(
        user_message="hi",
        history=[],
        llm_config={"provider": "openai", "model": "gpt-4o"},
        dsn="postgresql://unused",
        pool=db_pool,
    )

    assert result["assistant"] == "hello there"
    assert mem.remembered


async def test_chat_turn_tool_loop(monkeypatch, db_pool):
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO config (key, value, description) VALUES ('chat.use_rlm', 'false', 'test override') "
            "ON CONFLICT (key) DO UPDATE SET value = 'false'"
        )

    class DummyMem:
        async def hydrate(self, *_args, **_kwargs):
            return HydratedContext(
                memories=[],
                partial_activations=[],
                identity=[],
                worldview=[],
                emotional_state=None,
                goals=None,
                urgent_drives=[],
            )

        async def touch_memories(self, _ids):
            return None

        async def remember(self, *_args, **_kwargs):
            return uuid4()

        async def record_chat_turn_memory(self, *_args, **_kwargs):
            return {"direct_promoted": False, "raw": {"status": "stored"}}

    mem = DummyMem()

    @asynccontextmanager
    async def fake_connect(_dsn, **_kwargs):
        yield mem

    responses = [
        {"content": "", "tool_calls": [{"id": "tool-1", "name": "recall", "arguments": {"query": "x"}}]},
        {"content": "final response", "tool_calls": []},
    ]

    async def fake_chat_completion(**_kwargs):
        return responses.pop(0)

    monkeypatch.setattr(chat_mod.CognitiveMemory, "connect", fake_connect)
    monkeypatch.setattr("core.agent_loop.chat_completion", fake_chat_completion)
    async def fake_agent_profile(_dsn=None, **_kwargs):
        return {}

    monkeypatch.setattr(chat_mod, "get_agent_profile_context", fake_agent_profile)

    result = await chat_mod.chat_turn(
        user_message="hi",
        history=[],
        llm_config={"provider": "openai", "model": "gpt-4o"},
        dsn="postgresql://unused",
        pool=db_pool,
    )

    assert result["assistant"] == "final response"
