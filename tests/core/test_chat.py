from contextlib import asynccontextmanager
import asyncio
from uuid import uuid4

import pytest

from services import chat as chat_mod
from core.cognitive_memory_api import HydratedContext, Memory, MemoryType, RecallResult

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.core]


async def test_estimate_importance_uses_learning_signals():
    score = chat_mod._estimate_importance("remember this", "ok")  # noqa: SLF001
    assert score >= 0.8


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
    def __init__(self, config):
        self._pool = _ConfigPool(config)
        self.raw_calls = []
        self.remember_calls = []
        self.link_calls = []
        self.recall_started = asyncio.Event()
        self.recall_release = asyncio.Event()

    async def remember_turn_raw(self, *args, **kwargs):
        self.raw_calls.append((args, kwargs))
        return {"unit_id": str(uuid4()), "status": "stored"}

    async def remember(self, *args, **kwargs):
        self.remember_calls.append((args, kwargs))
        return uuid4()

    async def link_to_source_unit(self, *args, **kwargs):
        self.link_calls.append((args, kwargs))
        return True

    async def recall(self, *_args, **_kwargs):
        self.recall_started.set()
        await self.recall_release.wait()
        return RecallResult(memories=[], partial_activations=[], query="q")

    async def hydrate_recmem(self, *_args, **_kwargs):
        return [
            Memory(
                id=uuid4(),
                type=MemoryType.EPISODIC,
                content="recmem",
                importance=0.5,
            )
        ]


async def test_remember_conversation_direct_promotion_suppresses_legacy_eager():
    mem = _RememberMem({
        "memory.recmem_enabled": True,
        "chat.eager_memory_enabled": True,
        "chat.recmem_salience_direct_promote": True,
        "memory.recmem_dual_write_compare": False,
    })

    await chat_mod._remember_conversation(  # noqa: SLF001
        mem,
        user_message="remember this important preference",
        assistant_message="noted",
        session_id=str(uuid4()),
        source_identity="chat:test",
    )

    assert len(mem.raw_calls) == 1
    assert len(mem.remember_calls) == 1
    assert len(mem.link_calls) == 1


async def test_remember_conversation_dual_write_comparison_is_nonblocking():
    mem = _RememberMem({
        "memory.recmem_enabled": True,
        "chat.eager_memory_enabled": True,
        "chat.recmem_salience_direct_promote": False,
        "memory.recmem_dual_write_compare": True,
    })

    await asyncio.wait_for(
        chat_mod._remember_conversation(  # noqa: SLF001
            mem,
            user_message="ordinary chat",
            assistant_message="ordinary response",
            session_id=str(uuid4()),
            source_identity="chat:test:ordinary",
        ),
        timeout=0.2,
    )

    assert len(mem.remember_calls) == 1
    await asyncio.wait_for(mem.recall_started.wait(), timeout=1.0)
    mem.recall_release.set()
    await asyncio.sleep(0)


async def test_build_system_prompt_includes_profile():
    prompt = await chat_mod._build_system_prompt({"name": "Hexis"})  # noqa: SLF001
    assert "Hexis" in prompt


async def test_chat_turn_basic_flow(monkeypatch, db_pool):
    # Disable RLM so the test exercises the AgentLoop path
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
    # Disable RLM so the test exercises the AgentLoop path
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
