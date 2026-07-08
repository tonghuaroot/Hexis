"""Parity tests: SQL prompt renderers vs the Python formatters they replace.

The DB-owned render_* functions (db/39_functions_prompt_render.sql) must produce
the same prompt text as services/heartbeat_prompt.py so the heartbeat/decision
prompt can be assembled entirely in the DB (Phase 3) without behavior change.
"""
from __future__ import annotations

import json
import uuid

import pytest

from core.cognitive_memory_api import (
    HydratedContext,
    Memory,
    MemoryType,
    PartialActivation,
    format_context_for_prompt,
)
from services.agent import SubconsciousOutput, format_subconscious_signals
from services.heartbeat_prompt import build_heartbeat_decision_prompt

pytestmark = [pytest.mark.asyncio(loop_scope="session")]


# A context exercising every formatter + edge cases (avoids the two known,
# LLM-irrelevant divergences: multi-key embedded-JSON ordering and
# banker's-rounding halves — floats here format identically both ways).
_RICH = {
    "heartbeat_number": 42,
    "agent": {
        "objectives": ["Stay curious", {"title": "Help the user", "description": "always"}],
        "guardrails": ["No harm", {"name": "privacy", "description": "protect data"}],
        "tools": ["recall", {"name": "reflect", "description": "introspect"}],
        "budget": {"max_daily_tokens": 100000},
    },
    "environment": {
        "timestamp": "2026-07-07T12:00:00Z", "day_of_week": "Tuesday", "hour_of_day": 12,
        "time_since_user_hours": 3, "pending_events": 2,
    },
    "goals": {
        "counts": {"active": 2, "queued": 1},
        "active": [{"title": "Learn SQL"}, {"title": "Write tests"}],
        "queued": [{"title": "Refactor"}],
        "issues": [{"title": "Goal X", "issue": "blocked"}],
    },
    "narrative": {"current_chapter": {"name": "The Migration"}},
    "recent_memories": [{"content": "A" * 150}, {"content": "short one"}],
    "identity": [{"type": "core", "content": {"role": "assistant"}}],
    "self_model": [{"kind": "is", "concept": "helpful", "strength": 0.9}],
    "relationships": [{"entity": "Eric", "strength": 0.75}],
    "worldview": [{"category": "ethics", "belief": "B" * 100, "confidence": 0.8}],
    "contradictions": [{"content_a": "a" * 70, "content_b": "b" * 70}],
    "emotional_patterns": [{"pattern": "calm focus", "frequency": 3}],
    "active_transformations": [{
        "content": "I value clarity", "subcategory": "value",
        "progress": {
            "progress": {"reflections": {"current": 2, "required": 5},
                         "evidence": {"memory_count": 4, "current_strength": 0.5}},
            "requirements": {"min_heartbeats": 10, "evidence_threshold": 3, "max_change_per_attempt": 0.1},
            "evidence_samples": [{"content": "sample one"}, {"content": "sample two"}],
        },
    }],
    "transformations_ready": [],
    "emotional_state": {"primary_emotion": "content", "valence": 0.5, "arousal": 0.25},
    "urgent_drives": [{"name": "connection", "urgency_ratio": 1.5}, {"name": "rest", "level": "high"}],
    "energy": {"current": 14, "max": 20},
    "backlog": {"counts": {"todo": 5, "in_progress": 2},
                "actionable": [{"title": "Task A", "priority": "high", "owner": "agent",
                                "status": "todo", "has_checkpoint": True}]},
    "allowed_actions": ["observe", "recall", "reflect"],
    "action_costs": {"observe": 0, "recall": 1, "reflect": 2, "reach_out": 5},
}

_EDGE_CASES = {
    "empty": {},
    "nulls": {
        "agent": {"objectives": None, "guardrails": None, "tools": None, "budget": None},
        "goals": {"active": [], "queued": [], "issues": []},
        "allowed_actions": None, "action_costs": {}, "emotional_state": None,
        "narrative": None, "recent_memories": [], "urgent_drives": [],
    },
    "allowed_empty": {"allowed_actions": []},
    "drive_level_only": {"urgent_drives": [{"name": "rest", "level": "high"}, {"name": "solo"}]},
    "transform_minimal": {"active_transformations": [{"content": "", "category": "belief"}]},
    "backlog_empty_counts": {"backlog": {"counts": {}, "actionable": []}},
}


async def _render_sql(conn, ctx: dict) -> str:
    return await conn.fetchval(
        "SELECT render_heartbeat_decision_prompt($1::jsonb)", json.dumps(ctx)
    )


async def test_heartbeat_prompt_rich_context_parity(db_pool):
    """SQL renderer matches Python byte-for-byte on a fully-populated context."""
    async with db_pool.acquire() as conn:
        assert await _render_sql(conn, _RICH) == build_heartbeat_decision_prompt(_RICH)


@pytest.mark.parametrize("name", list(_EDGE_CASES))
async def test_heartbeat_prompt_edge_case_parity(db_pool, name):
    """Empty/null/absent-key defaults render identically to the Python formatter."""
    ctx = _EDGE_CASES[name]
    async with db_pool.acquire() as conn:
        assert await _render_sql(conn, ctx) == build_heartbeat_decision_prompt(ctx), name


# ---------------------------------------------------------------------------
# Chat memory context: render_chat_memory_context vs format_context_for_prompt
# ---------------------------------------------------------------------------


def _mk_ctx(j: dict) -> HydratedContext:
    def mem(d):
        return Memory(id=uuid.uuid4(), type=MemoryType.SEMANTIC, content=d.get("content", ""),
                      importance=0.5, similarity=d.get("similarity"), trust_level=d.get("trust_level"),
                      source_attribution=d.get("source_attribution"), tier=d.get("tier"))

    def pa(d):
        return PartialActivation(cluster_id=uuid.uuid4(), cluster_name=d.get("cluster_name", ""),
                                 keywords=d.get("keywords", []), emotional_signature=None,
                                 cluster_similarity=0.5, best_memory_similarity=0.5)

    return HydratedContext(
        memories=[mem(m) for m in j.get("memories", [])],
        partial_activations=[pa(x) for x in j.get("partial_activations", [])],
        identity=j.get("identity", []), worldview=j.get("worldview", []),
        emotional_state=j.get("emotional_state"), goals=j.get("goals"),
        urgent_drives=j.get("urgent_drives", []))


_CTX_CASES = {
    "tiered": {"memories": [
        {"content": "raw turn", "tier": "subconscious", "similarity": 0.91, "trust_level": 0.8},
        {"content": "an event", "tier": "episodic", "similarity": 0.7},
        {"content": "a fact", "tier": "semantic", "trust_level": 0.95}],
        "identity": [{"type": "core", "concept": "helpful", "strength": 0.9}],
        "worldview": [{"belief": "honesty matters", "confidence": 0.8}],
        "emotional_state": {"primary_emotion": "calm", "valence": 0.5, "arousal": 0.25},
        "goals": {"active": [{"title": "ship it", "source": "user"}], "queued": [{"title": "rest"}]},
        "urgent_drives": [{"name": "connection", "urgency_ratio": 1.5}, {"name": "rest", "level": "high"}]},
    "flat": {"memories": [{"content": "m1", "similarity": 0.88, "trust_level": 0.9,
                           "source_attribution": {"kind": "doc", "ref": "file.md"}},
                          {"content": "m2", "source_attribution": {"kind": "chat"}}],
             "partial_activations": [{"cluster_name": "themes", "keywords": ["a", "b", "c"]},
                                     {"cluster_name": "empty", "keywords": []}]},
    "empty": {},
    "drives_only": {"urgent_drives": [{"name": "x", "urgency_ratio": 0.05}]},
}


@pytest.mark.parametrize("name", list(_CTX_CASES))
async def test_chat_memory_context_parity(db_pool, name):
    j = _CTX_CASES[name]
    async with db_pool.acquire() as conn:
        sql = await conn.fetchval("SELECT render_chat_memory_context($1::jsonb)", json.dumps(j))
    assert sql == format_context_for_prompt(_mk_ctx(j)), name


# ---------------------------------------------------------------------------
# Subconscious signals: render_subconscious_signals vs format_subconscious_signals
# ---------------------------------------------------------------------------


def _mk_sub(j: dict) -> SubconsciousOutput:
    return SubconsciousOutput(
        salient_memories=j.get("salient_memories", []), memory_expansions=j.get("memory_expansions", []),
        instincts=j.get("instincts", []), emotional_state=j.get("emotional_state", {}),
        subconscious_response=j.get("subconscious_response", ""))


_SUB_CASES = {
    "full": {"instincts": [{"impulse": "reach out", "intensity": 0.8, "reason": "lonely"}],
             "emotional_state": {"primary_emotion": "warm", "valence": 0.6, "arousal": 0.3},
             "memory_expansions": [{"query": "past chats"}, {"query": ""}, {"query": "goals"}],
             "salient_memories": [{"memory_id": "abc", "reason": "relevant"}],
             "subconscious_response": "I feel curious " + "x" * 300},
    "empty": {},
    "only_emotion": {"emotional_state": {"primary_emotion": "neutral"}},
}


@pytest.mark.parametrize("name", list(_SUB_CASES))
async def test_subconscious_signals_parity(db_pool, name):
    j = _SUB_CASES[name]
    async with db_pool.acquire() as conn:
        sql = await conn.fetchval("SELECT render_subconscious_signals($1::jsonb)", json.dumps(j))
    assert sql == format_subconscious_signals(_mk_sub(j)), name
