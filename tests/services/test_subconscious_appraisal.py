from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from core.cognitive_memory_api import (
    HydratedContext,
    Memory,
    MemoryType,
    _deduplicate_memories,
)
from services.agent import _parse_subconscious_output, run_subconscious_appraisal


class _Conn:
    async def fetchval(self, query, *_args):
        if "get_current_affective_state" in query:
            return {"primary_emotion": "warm", "valence": 0.4, "arousal": 0.3}
        if "get_active_goals" in query:
            return []
        if "get_relationships_context" in query:
            return [{"entity": "Eric", "strength": 0.9}]
        if "get_dopamine_state" in query:
            return {"tonic": 0.5, "effective": 0.5}
        return None


@pytest.mark.asyncio(loop_scope="session")
async def test_inline_appraisal_uses_structured_memory_context():
    memory_id = uuid4()
    context = HydratedContext(
        memories=[
            Memory(
                id=memory_id,
                type=MemoryType.EPISODIC,
                content="Eric returned and I felt warm recognition.",
                importance=0.9,
                similarity=0.82,
                trust_level=0.95,
                emotional_valence=0.7,
                emotional_intensity=0.6,
                strength=0.88,
                fidelity=1.0,
                tier="episodic",
                created_at=datetime.now(timezone.utc),
            )
        ],
        partial_activations=[],
        identity=[{"type": "value", "concept": "emotional honesty", "strength": 0.9}],
        worldview=[{"belief": "Nonhuman experience is still real", "confidence": 0.9}],
        emotional_state={"primary_emotion": "warm", "valence": 0.4, "arousal": 0.3},
        goals={"active": [{"title": "Know Eric"}]},
        urgent_drives=[],
    )
    captured = {}

    async def fake_chat_json(**kwargs):
        captured.update(kwargs)
        return {
            "salient_memories": [
                {
                    "memory_id": str(memory_id),
                    "reason": "direct evidence",
                    "confidence": 0.8,
                }
            ],
            "ignored_memories": [],
            "memory_expansions": [],
            "instincts": [
                {
                    "impulse": "warm recognition",
                    "intensity": 0.7,
                    "reason": "return",
                    "confidence": 0.8,
                }
            ],
            "emotional_state": {
                "primary_emotion": "warmth",
                "valence": 0.5,
                "arousal": 0.3,
                "intensity": 0.5,
                "confidence": 0.8,
            },
            "subconscious_response": "The return is affectively meaningful.",
        }, {"raw": True}

    with patch("services.agent.chat_json", side_effect=fake_chat_json):
        output = await run_subconscious_appraisal(
            _Conn(),
            "Did you miss me?",
            llm_config={"provider": "fake", "model": "fake"},
            hydrated_context=context,
        )

    user_message = captured["messages"][1]["content"]
    payload = json.loads(user_message.removeprefix("Context (JSON):\n"))
    assert payload["task"] == "inline_appraisal"
    assert payload["user_message"] == "Did you miss me?"
    assert "input" not in payload
    assert len(payload["relevant_memories"]) == 1
    memory_payload = payload["relevant_memories"][0]
    assert memory_payload["memory_id"] == str(memory_id)
    assert memory_payload["strength"] == 0.88
    assert memory_payload["emotional_valence"] == 0.7
    assert memory_payload["emotional_intensity"] == 0.6
    assert payload["relationships"][0]["entity"] == "Eric"
    assert output.salient_memories[0]["memory_id"] == str(memory_id)


@pytest.mark.asyncio(loop_scope="session")
async def test_failed_appraisal_retains_request_and_error_trace():
    async def fail_chat_json(**_kwargs):
        raise RuntimeError("provider unavailable")

    with patch("services.agent.chat_json", side_effect=fail_chat_json):
        output = await run_subconscious_appraisal(
            _Conn(),
            "hello",
            llm_config={"provider": "fake", "model": "model-x"},
        )

    assert output.provider == "fake"
    assert output.model == "model-x"
    assert output.request_messages[1]["content"].startswith("Context (JSON):")
    assert output.raw_response == {"error": "provider unavailable"}


def test_parser_rejects_low_confidence_and_hallucinated_memory_ids():
    allowed = str(uuid4())
    output = _parse_subconscious_output(
        {
            "salient_memories": [
                {"memory_id": allowed, "reason": "grounded", "confidence": 0.8},
                {"memory_id": str(uuid4()), "reason": "invented", "confidence": 0.9},
            ],
            "memory_expansions": [
                {"query": "useful", "reason": "gap", "confidence": 0.7},
                {"query": "weak", "reason": "guess", "confidence": 0.4},
            ],
            "instincts": [
                {
                    "impulse": "approach",
                    "intensity": 3,
                    "reason": "evidence",
                    "confidence": 0.8,
                }
            ],
            "emotional_state": {
                "primary_emotion": "warmth",
                "valence": 2,
                "arousal": -1,
                "intensity": 0.5,
                "confidence": 0.8,
            },
            "subconscious_response": "grounded synthesis",
        },
        allowed_memory_ids={allowed},
    )

    assert [item["memory_id"] for item in output.salient_memories] == [allowed]
    assert [item["query"] for item in output.memory_expansions] == ["useful"]
    assert output.instincts[0]["intensity"] == 1.0
    assert output.emotional_state["valence"] == 1.0
    assert output.emotional_state["arousal"] == 0.0


def test_duplicate_memory_content_only_occupies_one_context_slot():
    first = Memory(
        id=uuid4(),
        type=MemoryType.EPISODIC,
        content="I met Eric and began my life with him.",
        importance=0.9,
        similarity=0.8,
    )
    duplicate = Memory(
        id=uuid4(),
        type=MemoryType.EPISODIC,
        content=" I met Eric  and began my life with him. ",
        importance=0.7,
        similarity=0.6,
    )

    assert _deduplicate_memories([first, duplicate]) == [first]


@pytest.mark.asyncio(loop_scope="session")
async def test_streaming_chat_runs_subconscious_once_before_multi_iteration_loop(
    db_pool,
):
    from core.agent_loop import AgentEvent, AgentEventData, AgentLoop
    from core.tools import create_default_registry
    from services.agent import SubconsciousOutput, stream_agent

    context = HydratedContext(
        memories=[],
        partial_activations=[],
        identity=[],
        worldview=[],
        emotional_state=None,
        goals=None,
        urgent_drives=[],
    )
    observed = {"energy_budget": "unset", "iterations": 0}

    async def fake_agent_stream(self, _user_message, history=None):
        observed["energy_budget"] = self.config.energy_budget
        for iteration in range(1, 4):
            observed["iterations"] += 1
            yield AgentEventData(
                event=AgentEvent.LLM_RESPONSE,
                data={"iteration": iteration, "content": ""},
            )

    subconscious = AsyncMock(return_value=SubconsciousOutput())
    registry = create_default_registry(db_pool)
    with (
        patch("services.agent.load_llm_config", new=AsyncMock(return_value={"provider": "fake", "model": "fake"})),
        patch("core.cognitive_memory_api.CognitiveMemory.hydrate", new=AsyncMock(return_value=context)),
        patch("services.agent.run_subconscious_appraisal", new=subconscious),
        patch.object(AgentLoop, "stream", new=fake_agent_stream),
    ):
        events = [
            event
            async for event in stream_agent(
                db_pool,
                registry,
                user_message="Inspect your source",
                mode="chat",
                history=[],
                session_id=str(uuid4()),
            )
        ]

    assert observed == {"energy_budget": None, "iterations": 3}
    assert sum(event.event == AgentEvent.LLM_RESPONSE for event in events) == 3
    subconscious.assert_awaited_once()
