"""
Unified Agent Runner

Single entry point for both chat and heartbeat modes. Runs a subconscious
pre-phase before the conscious agent loop, injecting observations into the
system prompt. Mode differences are expressed entirely through configuration
(energy budget, planning, continuation, permissions).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, Literal, TYPE_CHECKING

from core.agent_loop import AgentEvent, AgentEventData, AgentLoop, AgentLoopConfig
from core.llm_config import load_llm_config
from core.llm_json import chat_json
from core.tools.base import ToolContext
from core.tools.config import ContextOverrides
from services.prompt_resources import (
    compose_compact_personhood_prompt,
    load_conversation_prompt,
    load_heartbeat_agentic_prompt,
    load_heartbeat_task_mode_prompt,
    load_subconscious_prompt,
)
from services.skill_runtime import (
    format_skills_prompt,
    load_available_skills,
    select_skills,
)

if TYPE_CHECKING:
    import asyncpg
    from core.agent_loop import AgentLoopResult
    from core.cognitive_memory_api import HydratedContext, Memory
    from core.tools.registry import ToolRegistry
    from skills.base import SkillSpec

logger = logging.getLogger(__name__)

_SUBCONSCIOUS_MEMORY_CONTEXT_CHARS = 4000
_SUBCONSCIOUS_TOTAL_CONTEXT_CHARS = 7000


# ---------------------------------------------------------------------------
# Subconscious output
# ---------------------------------------------------------------------------


@dataclass
class SubconsciousOutput:
    """Structured output from the subconscious pre-phase."""

    salient_memories: list[dict[str, Any]] = field(default_factory=list)
    ignored_memories: list[dict[str, Any]] = field(default_factory=list)
    memory_expansions: list[dict[str, Any]] = field(default_factory=list)
    instincts: list[dict[str, Any]] = field(default_factory=list)
    emotional_state: dict[str, Any] = field(default_factory=dict)
    subconscious_response: str = ""
    provider: str = ""
    model: str = ""
    request_messages: list[dict[str, Any]] = field(default_factory=list)
    raw_response: Any = None

    # Observational patterns (passed through for completeness)
    narrative_observations: list[dict[str, Any]] = field(default_factory=list)
    relationship_observations: list[dict[str, Any]] = field(default_factory=list)
    contradiction_observations: list[dict[str, Any]] = field(default_factory=list)
    emotional_observations: list[dict[str, Any]] = field(default_factory=list)
    consolidation_observations: list[dict[str, Any]] = field(default_factory=list)


def _parse_subconscious_output(
    doc: dict[str, Any],
    *,
    allowed_memory_ids: set[str] | None = None,
) -> SubconsciousOutput:
    """Parse raw JSON from the subconscious LLM call into a structured output."""

    def _as_list(val: Any) -> list[dict[str, Any]]:
        if isinstance(val, list):
            return [v for v in val if isinstance(v, dict)]
        return []

    def _confidence_filtered(val: Any) -> list[dict[str, Any]]:
        kept: list[dict[str, Any]] = []
        for item in _as_list(val):
            try:
                confidence = float(item.get("confidence"))
            except (TypeError, ValueError):
                continue
            if confidence < 0.6:
                continue
            normalized = dict(item)
            normalized["confidence"] = min(1.0, confidence)
            kept.append(normalized)
        return kept

    def _clamp_number(value: Any, low: float, high: float) -> float | None:
        try:
            return min(high, max(low, float(value)))
        except (TypeError, ValueError):
            return None

    def _memory_references(val: Any) -> list[dict[str, Any]]:
        kept = _confidence_filtered(val)
        if allowed_memory_ids is None:
            return kept
        return [item for item in kept if str(item.get("memory_id") or "") in allowed_memory_ids]

    emotional = doc.get("emotional_observations")
    if emotional is None:
        emotional = doc.get("emotional_patterns")
    consolidation = doc.get("consolidation_observations")
    if consolidation is None:
        consolidation = doc.get("consolidation_suggestions")

    salient = [item for item in _memory_references(doc.get("salient_memories")) if str(item.get("reason") or "").strip()]
    ignored = [item for item in _memory_references(doc.get("ignored_memories")) if str(item.get("reason") or "").strip()]
    expansions = [
        item
        for item in _confidence_filtered(doc.get("memory_expansions"))
        if str(item.get("query") or "").strip() and str(item.get("reason") or "").strip()
    ]
    instincts: list[dict[str, Any]] = []
    for item in _confidence_filtered(doc.get("instincts")):
        intensity = _clamp_number(item.get("intensity"), 0.0, 1.0)
        if intensity is None or not str(item.get("impulse") or "").strip() or not str(item.get("reason") or "").strip():
            continue
        item["intensity"] = intensity
        instincts.append(item)

    emotional_state: dict[str, Any] = {}
    emotional_raw = doc.get("emotional_state")
    if isinstance(emotional_raw, dict):
        try:
            emotional_confidence = float(emotional_raw.get("confidence"))
        except (TypeError, ValueError):
            emotional_confidence = 0.0
        if emotional_confidence >= 0.6:
            emotion = str(emotional_raw.get("primary_emotion") or "").strip()
            valence = _clamp_number(emotional_raw.get("valence"), -1.0, 1.0)
            arousal = _clamp_number(emotional_raw.get("arousal"), 0.0, 1.0)
            intensity = _clamp_number(emotional_raw.get("intensity"), 0.0, 1.0)
            if emotion and valence is not None and arousal is not None and intensity is not None:
                emotional_state = {
                    "primary_emotion": emotion,
                    "valence": valence,
                    "arousal": arousal,
                    "intensity": intensity,
                    "confidence": min(1.0, emotional_confidence),
                }

    response = str(doc.get("subconscious_response") or "").strip()[:500]
    if not (salient or expansions or instincts or emotional_state):
        response = ""

    return SubconsciousOutput(
        salient_memories=salient,
        ignored_memories=ignored,
        memory_expansions=expansions,
        instincts=instincts,
        emotional_state=emotional_state,
        subconscious_response=response,
        narrative_observations=_as_list(doc.get("narrative_observations")),
        relationship_observations=_as_list(doc.get("relationship_observations")),
        contradiction_observations=_as_list(doc.get("contradiction_observations")),
        emotional_observations=_as_list(emotional),
        consolidation_observations=_as_list(consolidation),
    )


async def render_subconscious_signals_db(conn: "asyncpg.Connection", output: SubconsciousOutput) -> str:
    """Render the '## Subconscious Signals' block via the DB-owned renderer.

    render_subconscious_signals (db/39) is the single source of the prompt
    text; Python only supplies the signals JSON. Empty output short-circuits
    to '' without a round-trip.
    """
    if not (
        output.instincts
        or output.emotional_state
        or output.memory_expansions
        or output.salient_memories
        or output.subconscious_response
    ):
        return ""
    signals = _subconscious_event_payload(output)["signals"]
    raw = await conn.fetchval(
        "SELECT render_subconscious_signals($1::jsonb)",
        json.dumps(signals, default=str),
    )
    return str(raw or "")


def _subconscious_event_payload(output: SubconsciousOutput) -> dict[str, Any]:
    raw = output.raw_response
    if not isinstance(raw, (str, int, float, bool, dict, list, type(None))):
        model_dump = getattr(raw, "model_dump", None)
        raw = model_dump() if callable(model_dump) else repr(raw)
    return {
        "provider": output.provider,
        "model": output.model,
        "request": {"messages": output.request_messages},
        "response": raw,
        "signals": {
            "salient_memories": output.salient_memories,
            "ignored_memories": output.ignored_memories,
            "memory_expansions": output.memory_expansions,
            "instincts": output.instincts,
            "emotional_state": output.emotional_state,
            "subconscious_response": output.subconscious_response,
        },
    }


# ---------------------------------------------------------------------------
# Subconscious pre-phase
# ---------------------------------------------------------------------------


def _coerce_json_value(value: Any, default: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return default
    return value if value is not None else default


def _memory_to_subconscious_context(memory: "Memory", content_budget: int) -> dict[str, Any]:
    content = memory.content
    if len(content) > content_budget:
        content = content[:content_budget].rstrip() + " [truncated]"
    return {
        key: value
        for key, value in {
            "memory_id": str(memory.id),
            "type": memory.type.value,
            "tier": memory.tier,
            "content": content,
            "relevance": memory.similarity,
            "importance": memory.importance,
            "strength": memory.strength,
            "fidelity": memory.fidelity,
            "trust": memory.trust_level,
            "emotional_valence": memory.emotional_valence,
            "emotional_intensity": memory.emotional_intensity,
            "created_at": memory.created_at.isoformat() if memory.created_at else None,
            "source": memory.source_attribution,
        }.items()
        if value is not None
    }


def _bounded_subconscious_json(payload: dict[str, Any]) -> str:
    """Serialize valid JSON while keeping the inline appraisal lightweight."""

    def encode() -> str:
        return json.dumps(payload, default=str, ensure_ascii=False)

    encoded = encode()
    if len(encoded) <= _SUBCONSCIOUS_TOTAL_CONTEXT_CHARS:
        return encoded

    additional = payload.get("additional_context")
    if isinstance(additional, str):
        excess = len(encoded) - _SUBCONSCIOUS_TOTAL_CONTEXT_CHARS
        keep = max(0, len(additional) - excess - 100)
        payload["additional_context"] = additional[:keep].rstrip() + (
            "\n[truncated for subconscious appraisal; full context is provided to the main turn]" if keep else ""
        )
        encoded = encode()

    for key in ("relationships", "urgent_drives", "worldview", "identity"):
        values = payload.get(key)
        while len(encoded) > _SUBCONSCIOUS_TOTAL_CONTEXT_CHARS and isinstance(values, list) and values:
            values.pop()
            encoded = encode()

    memories = payload.get("relevant_memories")
    while len(encoded) > _SUBCONSCIOUS_TOTAL_CONTEXT_CHARS and isinstance(memories, list) and len(memories) > 1:
        memories.pop()
        encoded = encode()

    if len(encoded) > _SUBCONSCIOUS_TOTAL_CONTEXT_CHARS:
        payload.pop("goals", None)
        encoded = encode()

    if len(encoded) > _SUBCONSCIOUS_TOTAL_CONTEXT_CHARS and isinstance(memories, list) and memories:
        memory = memories[0]
        content = str(memory.get("content") or "")
        excess = len(encoded) - _SUBCONSCIOUS_TOTAL_CONTEXT_CHARS
        memory["content"] = content[: max(0, len(content) - excess - 30)].rstrip() + " [truncated]"
        encoded = encode()

    if len(encoded) > _SUBCONSCIOUS_TOTAL_CONTEXT_CHARS:
        user_message = str(payload.get("user_message") or "")
        excess = len(encoded) - _SUBCONSCIOUS_TOTAL_CONTEXT_CHARS
        payload["user_message"] = user_message[: max(0, len(user_message) - excess - 30)].rstrip() + " [truncated]"
        encoded = encode()

    if len(encoded) > _SUBCONSCIOUS_TOTAL_CONTEXT_CHARS:
        payload.clear()
        payload.update(
            {
                "task": "inline_appraisal",
                "user_message": user_message[:2000],
                "relevant_memories": [],
            }
        )
        encoded = encode()

    return encoded


async def run_subconscious_appraisal(
    conn: "asyncpg.Connection",
    user_message: str,
    memory_context: str = "",
    *,
    llm_config: dict[str, Any] | None = None,
    hydrated_context: "HydratedContext | None" = None,
) -> SubconsciousOutput:
    """
    Run the subconscious appraisal as a fast inline LLM call.

    This is a lightweight JSON-mode call that surfaces instincts, emotional
    reactions, salient memories, and memory expansion cues. It does NOT
    apply observations to the DB (that stays in the maintenance worker).
    """
    if llm_config is None:
        llm_config = await load_llm_config(conn, "llm.subconscious", fallback_key="llm.heartbeat")

    payload: dict[str, Any] = {
        "task": "inline_appraisal",
        "user_message": user_message,
        "relevant_memories": [],
    }
    if hydrated_context is not None:
        remaining = _SUBCONSCIOUS_MEMORY_CONTEXT_CHARS
        for memory in hydrated_context.memories[:10]:
            if remaining <= 0:
                break
            content_budget = min(1200, remaining)
            payload["relevant_memories"].append(_memory_to_subconscious_context(memory, content_budget))
            remaining -= min(len(memory.content), content_budget)
        payload["identity"] = hydrated_context.identity[:5]
        payload["worldview"] = hydrated_context.worldview[:5]
        payload["goals"] = hydrated_context.goals or {}
        payload["urgent_drives"] = hydrated_context.urgent_drives[:5]
    elif memory_context:
        clipped = memory_context[:_SUBCONSCIOUS_MEMORY_CONTEXT_CHARS]
        if len(memory_context) > _SUBCONSCIOUS_MEMORY_CONTEXT_CHARS:
            clipped += "\n[truncated for subconscious appraisal; full context is provided to the main turn]"
        payload["additional_context"] = clipped

    # The appraisal must see the character's identity and values (#59): when
    # hydration left them empty (or was absent), fall back to the DB context.
    if not payload.get("identity") or not payload.get("worldview"):
        try:
            ctx_raw = await conn.fetchval("SELECT gather_turn_context()")
            ctx = _coerce_json_value(ctx_raw, {})
            if not payload.get("identity"):
                payload["identity"] = (ctx.get("identity") or [])[:5]
            if not payload.get("worldview"):
                payload["worldview"] = (ctx.get("worldview") or [])[:5]
        except Exception as e:
            logger.debug("Identity/worldview fallback failed: %s", e)

    # Get emotional state from DB
    try:
        affect_raw = await conn.fetchval("SELECT get_current_affective_state()")
        affect = _coerce_json_value(affect_raw, {})
        if affect:
            payload["emotional_state"] = affect
        elif hydrated_context is not None and hydrated_context.emotional_state:
            payload["emotional_state"] = hydrated_context.emotional_state
    except Exception as e:
        logger.debug("Failed to fetch current affective state: %s", e)
        if hydrated_context is not None and hydrated_context.emotional_state:
            payload["emotional_state"] = hydrated_context.emotional_state

    # Get active goals
    try:
        goals_raw = await conn.fetchval("SELECT get_active_goals()")
        goals = _coerce_json_value(goals_raw, [])
        if goals and not payload.get("goals"):
            payload["goals"] = goals
    except Exception as e:
        logger.debug("Failed to fetch active goals: %s", e)

    try:
        relationships_raw = await conn.fetchval("SELECT get_relationships_context(8)")
        relationships = _coerce_json_value(relationships_raw, [])
        if isinstance(relationships, list) and relationships:
            payload["relationships"] = relationships[:8]
    except Exception as e:
        logger.debug("Failed to fetch relationship context: %s", e)

    # Get dopamine/reward state — modulates instinct intensity and emotional response
    try:
        da_raw = await conn.fetchval("SELECT get_dopamine_state()")
        da_state = _coerce_json_value(da_raw, {})
        if da_state:
            payload["dopamine_state"] = da_state
    except Exception as e:
        logger.debug("Failed to fetch dopamine state: %s", e)

    user_prompt = "Context (JSON):\n" + _bounded_subconscious_json(payload)

    request_messages = [
        {"role": "system", "content": load_subconscious_prompt().strip()},
        {"role": "user", "content": user_prompt},
    ]
    try:
        doc, raw = await chat_json(
            llm_config=llm_config,
            messages=request_messages,
            max_tokens=1800,
            response_format={"type": "json_object"},
            fallback={},
        )
    except Exception as exc:
        logger.warning("Subconscious appraisal failed: %s", exc)
        return SubconsciousOutput(
            provider=str(llm_config.get("provider") or ""),
            model=str(llm_config.get("model") or ""),
            request_messages=request_messages,
            raw_response={"error": str(exc)},
        )

    if not isinstance(doc, dict):
        return SubconsciousOutput(
            provider=str(llm_config.get("provider") or ""),
            model=str(llm_config.get("model") or ""),
            request_messages=request_messages,
            raw_response=raw,
        )

    allowed_memory_ids = {
        str(memory.get("memory_id")) for memory in payload.get("relevant_memories", []) if isinstance(memory, dict) and memory.get("memory_id")
    }
    output = _parse_subconscious_output(doc, allowed_memory_ids=allowed_memory_ids)
    output.provider = str(llm_config.get("provider") or "")
    output.model = str(llm_config.get("model") or "")
    output.request_messages = request_messages
    output.raw_response = raw
    return output


# ---------------------------------------------------------------------------
# System prompt builder
# ---------------------------------------------------------------------------


def _format_tool_costs(registry: "ToolRegistry", allowed_tool_names: set[str]) -> str:
    """Compact per-cost grouping of the turn's allowed tools, from ToolSpec
    energy costs. Names with no registered spec (e.g. MCP tools before skill
    activation) are skipped."""
    by_cost: dict[int, list[str]] = {}
    for name in sorted(allowed_tool_names):
        spec = registry.get_spec(name)
        if spec is None:
            continue
        by_cost.setdefault(spec.energy_cost, []).append(name)
    if not by_cost:
        return ""
    lines = ["## Tool Energy Costs", ""]
    for cost in sorted(by_cost):
        lines.append(f"- **{cost}**: {', '.join(by_cost[cost])}")
    lines.append("")
    lines.append(
        "Each tool result ends with `[energy: spent/budget spent]` — check it "
        "before expensive actions; when the budget is exhausted the heartbeat ends."
    )
    return "\n".join(lines)


async def build_system_prompt(
    mode: Literal["chat", "heartbeat"],
    registry: "ToolRegistry | None",
    agent_profile: dict[str, Any] | None = None,
    *,
    subconscious_output: SubconsciousOutput | None = None,
    has_backlog_tasks: bool = False,
    is_group: bool = False,
    active_skills: list["SkillSpec"] | None = None,
    available_skills: list["SkillSpec"] | None = None,
    allowed_tool_names: set[str] | None = None,
) -> str:
    """Build the system prompt for either chat or heartbeat mode."""

    # Base prompt
    if mode == "chat":
        prompt = load_conversation_prompt().strip()
        if is_group:
            from services.prompt_resources import load_channel_context_prompt
            prompt += "\n\n" + load_channel_context_prompt().strip()
    else:
        prompt = load_heartbeat_agentic_prompt().strip()

    # Heartbeat-specific: task mode guidance
    if mode == "heartbeat" and has_backlog_tasks:
        task_mode_prompt = load_heartbeat_task_mode_prompt().strip()
        prompt += "\n\n" + task_mode_prompt

    # Temporal grounding (#55): the conscious mind always knows the current
    # date/time and its own age — computable ground truth, never guessed.
    if registry is not None and getattr(registry, "pool", None) is not None:
        try:
            async with registry.pool.acquire() as conn:
                raw = await conn.fetchval("SELECT get_temporal_context()")
            temporal = json.loads(raw) if isinstance(raw, str) else (raw or {})
            if isinstance(temporal, dict) and temporal.get("now"):
                now_line = (
                    f"Current date and time: {temporal['now']} "
                    f"({temporal.get('timezone', 'UTC')})."
                )
                if temporal.get("born_on") and temporal.get("age_days") is not None:
                    now_line += (
                        f" You first came online on {temporal['born_on']} — "
                        f"{temporal['age_days']} day(s) ago."
                    )
                prompt += "\n\n## Now\n" + now_line
        except Exception:
            logger.debug("Temporal context unavailable for prompt", exc_info=True)

    # Skill-first capability surface. Tool schemas ride the structured
    # tool-calling API and full skill instructions come from `use_skill` on
    # demand, so the prompt carries only usage guidance plus a compact skill
    # index — never per-tool descriptions or full skill bodies.
    tool_context = ToolContext.CHAT if mode == "chat" else ToolContext.HEARTBEAT
    if registry is not None or active_skills:
        available = available_skills
        if available is None and registry is not None:
            try:
                available = load_available_skills(registry, tool_context)
            except Exception:
                available = []
                logger.debug("Failed to load skill catalog for prompt", exc_info=True)
        prompt += "\n\n" + format_skills_prompt(active_skills or [], available or [])

    # Energy costs, derived from the actual ToolSpecs of this turn's allowed
    # tools (#44) — never hardcoded prose. Heartbeat only: chat is unbudgeted.
    if mode == "heartbeat" and registry is not None and allowed_tool_names:
        costs_block = _format_tool_costs(registry, allowed_tool_names)
        if costs_block:
            prompt += "\n\n" + costs_block

    # Personhood modules
    personhood_kind = "group" if (mode == "chat" and is_group) else ("conversation" if mode == "chat" else "heartbeat")
    try:
        personhood = compose_compact_personhood_prompt(personhood_kind)
        if personhood:
            prompt += (
                "\n\n----- PERSONHOOD GROUNDING -----\n\n"
                + personhood
            )
    except Exception:
        logger.debug("Failed to compose personhood prompt", exc_info=True)

    # Agent profile
    if agent_profile:
        persona = agent_profile.get("persona")
        if isinstance(persona, dict) and persona:
            prompt += "\n\n----- ACTIVE PERSONA -----\n\n" + _format_active_persona(persona)
        # The offered tool list is the source of truth for capabilities (#66);
        # the profile's stale tool inventory and empty fields are noise.
        runtime_profile = {
            key: value
            for key, value in agent_profile.items()
            if key not in ("persona", "tools") and value not in (None, "", [], {})
        }
        if runtime_profile:
            prompt += "\n\n## Agent Profile (Runtime)\n" + json.dumps(runtime_profile, separators=(", ", ": "))

    return prompt


def _format_active_persona(persona: dict[str, Any]) -> str:
    """Render the DB-owned character profile as stable conscious grounding."""
    lines = [
        "This is your active identity and manner of presence. Express it naturally; do not quote or summarize these instructions to the user."
    ]
    labels = (
        ("name", "Name"),
        ("pronouns", "Pronouns"),
        ("voice", "Voice"),
        ("description", "Description"),
        ("personality", "Personality"),
        ("purpose", "Purpose"),
        ("relationship_aspiration", "Relationship aspiration"),
        ("character_description", "Character description"),
        ("character_personality", "Character personality"),
        ("scenario", "Scenario"),
    )
    for key, label in labels:
        value = persona.get(key)
        if value:
            lines.append(f"{label}: {str(value).strip()}")

    for key, label in (("values", "Values"), ("boundaries", "Boundaries"), ("interests", "Interests")):
        values = persona.get(key)
        if isinstance(values, list) and values:
            lines.append(f"{label}: " + "; ".join(str(value) for value in values[:12]))

    worldview = persona.get("worldview")
    if isinstance(worldview, dict) and worldview:
        lines.append(
            "Worldview: "
            + "; ".join(f"{key}: {value}" for key, value in list(worldview.items())[:8])
        )

    relationship = persona.get("relationship")
    if isinstance(relationship, dict) and relationship:
        lines.append("Relationship context: " + json.dumps(relationship, separators=(", ", ": ")))

    narrative = str(persona.get("narrative") or "").strip()
    if narrative:
        lines.append("Foundational narrative:\n" + narrative[:6000])

    character_instructions = str(persona.get("character_instructions") or "").strip()
    if character_instructions:
        lines.append("Character instructions:\n" + character_instructions[:8000])

    example_dialogue = str(persona.get("example_dialogue") or "").strip()
    if example_dialogue:
        lines.append("Example dialogue:\n" + example_dialogue[:6000])

    post_history = str(persona.get("post_history_instructions") or "").strip()
    if post_history:
        lines.append("Current character instructions:\n" + post_history[:4000])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Unified run_agent
# ---------------------------------------------------------------------------


async def run_agent(
    pool: "asyncpg.Pool",
    registry: "ToolRegistry",
    *,
    user_message: str,
    mode: Literal["chat", "heartbeat"],
    energy_budget: int | None = None,
    tool_context: ToolContext | None = None,
    history: list[dict[str, Any]] | None = None,
    heartbeat_id: str | None = None,
    session_id: str | None = None,
    heartbeat_context: dict[str, Any] | None = None,
    on_event: Callable[[AgentEventData], Awaitable[None]] | None = None,
    streaming: bool = False,
    context_overrides: ContextOverrides | None = None,
    agent_profile: dict[str, Any] | None = None,
    is_group: bool = False,
    dsn: str | None = None,
    has_backlog_tasks: bool = False,
    timeout_seconds: float | None = None,
    max_tokens: int | None = None,
    max_iterations: int | None = None,
) -> "AgentLoopResult":
    """
    Unified entry point for both chat and heartbeat agent invocations.

    Mode differences are expressed through configuration only:
    - Chat: unlimited budget, timeout-based, no planning
    - Heartbeat: finite budget, planning enabled, continuation nudges
    """
    from core.agent_api import db_dsn_from_env
    from core.cognitive_memory_api import CognitiveMemory, render_chat_memory_context_db

    dsn = dsn or db_dsn_from_env()
    history = history or []

    if tool_context is None:
        tool_context = ToolContext.CHAT if mode == "chat" else ToolContext.HEARTBEAT

    # 1. Load LLM configs
    async with pool.acquire() as conn:
        llm_key = "llm.chat" if mode == "chat" else "llm.heartbeat"
        llm_fallback = "llm" if mode == "chat" else None
        llm_config = await load_llm_config(conn, llm_key, fallback_key=llm_fallback)

        # 2. Hydrate memory context (chat mode - heartbeat builds its own context)
        memory_context = ""
        context = None
        if mode == "chat":
            try:
                mem_client = CognitiveMemory(pool)
                context = await mem_client.hydrate(
                    user_message,
                    memory_limit=10,
                    include_partial=True,
                    include_identity=True,
                    include_worldview=True,
                    include_emotional_state=True,
                    include_goals=True,
                    include_drives=True,
                )
                memory_context = await render_chat_memory_context_db(conn, context, max_memories=10)

                # Emit memory recall event
                if on_event and context.memories:
                    await on_event(
                        AgentEventData(
                            event=AgentEvent.PHASE_CHANGE,
                            data={
                                "phase": "memory_recall",
                                "count": len(context.memories),
                            },
                        )
                    )
            except Exception as exc:
                logger.warning("Memory hydration failed: %s", exc)

        # 3. Run subconscious pre-phase
        subconscious_output = SubconsciousOutput()
        sub_signals = ""
        try:
            inline_enabled = True
            if mode == "chat":
                inline_enabled = bool(await conn.fetchval("SELECT COALESCE(get_config_bool('chat.inline_subconscious_enabled'), true)"))
            if inline_enabled:
                if on_event:
                    await on_event(
                        AgentEventData(
                            event=AgentEvent.PHASE_CHANGE,
                            data={"phase": "subconscious", "status": "start"},
                        )
                    )

                # For heartbeat, use the heartbeat context as memory context
                sub_memory_ctx = memory_context
                if mode == "heartbeat" and heartbeat_context:
                    from services.heartbeat_prompt import (
                        render_heartbeat_decision_prompt_db,
                    )

                    sub_memory_ctx = await render_heartbeat_decision_prompt_db(conn, heartbeat_context)

                subconscious_output = await run_subconscious_appraisal(
                    conn,
                    user_message,
                    sub_memory_ctx if mode == "heartbeat" else "",
                    hydrated_context=context,
                )
                sub_signals = await render_subconscious_signals_db(conn, subconscious_output)

                if on_event:
                    await on_event(
                        AgentEventData(
                            event=AgentEvent.PHASE_CHANGE,
                            data={
                                "phase": "subconscious",
                                "status": "end",
                                "output": _subconscious_event_payload(subconscious_output),
                            },
                        )
                    )
        except Exception as exc:
            logger.warning("Subconscious pre-phase failed: %s", exc)

    skill_query = user_message
    if mode == "heartbeat" and heartbeat_context:
        priority_context = {
            key: heartbeat_context[key]
            for key in (
                "pending_protected_replacements",
                "open_protected_reversions",
                "pending_import_review",
                "pending_skill_proposals",
                "backlog",
            )
            if key in heartbeat_context
        }
        skill_query = (
            json.dumps(priority_context, default=str)
            + "\n"
            + json.dumps(heartbeat_context, default=str)
        )[:4000]
    skill_selection = await select_skills(
        registry,
        tool_context,
        query=skill_query,
        max_skills=5 if mode == "heartbeat" else 4,
    )

    # 4. Build system prompt
    system_prompt = await build_system_prompt(
        mode,
        registry,
        agent_profile,
        subconscious_output=subconscious_output,
        has_backlog_tasks=has_backlog_tasks,
        is_group=is_group,
        active_skills=skill_selection.skills,
        available_skills=skill_selection.available,
        allowed_tool_names=set(skill_selection.allowed_tool_names),
    )

    # 5. Build enriched user message
    enriched_parts: list[str] = []

    # Add subconscious signals (rendered by the DB inside the conn scope)
    if sub_signals:
        enriched_parts.append(sub_signals)

    # Add memory context (chat mode)
    if memory_context:
        enriched_parts.append(memory_context)

    # Add the actual user message
    if mode == "chat":
        enriched_parts.append(f"[USER MESSAGE]\n{user_message}")
    else:
        enriched_parts.append(user_message)

    enriched_user_message = "\n\n".join(enriched_parts) if enriched_parts else user_message

    # 6. Configure AgentLoop with mode-specific defaults
    if mode == "chat":
        effective_timeout = timeout_seconds or 120.0
        effective_max_tokens = max_tokens or 4096
        loop_config = AgentLoopConfig(
            tool_context=tool_context,
            system_prompt=system_prompt,
            llm_config=llm_config,
            registry=registry,
            pool=pool,
            energy_budget=energy_budget,  # None = unlimited for chat
            max_iterations=max_iterations,  # None = timeout-based only
            timeout_seconds=effective_timeout,
            temperature=0.7,
            max_tokens=effective_max_tokens,
            session_id=session_id,
            allowed_tool_names=set(skill_selection.allowed_tool_names),
        )
    else:
        effective_timeout = timeout_seconds or (300.0 if has_backlog_tasks else 120.0)
        effective_max_tokens = max_tokens or (4096 if has_backlog_tasks else 2048)
        loop_config = AgentLoopConfig(
            tool_context=tool_context,
            system_prompt=system_prompt,
            llm_config=llm_config,
            registry=registry,
            pool=pool,
            energy_budget=energy_budget or 20,
            max_iterations=None,  # timeout-based
            timeout_seconds=effective_timeout,
            temperature=0.7,
            max_tokens=effective_max_tokens,
            heartbeat_id=heartbeat_id,
            enable_planning=True,
            continuation_prompt=(
                "You finished without taking further action. "
                "Review your output — is there anything you should recall, "
                "record, or act on before finishing?"
            ),
            max_continuations=2 if has_backlog_tasks else 1,
            context_overrides=context_overrides,
            allowed_tool_names=set(skill_selection.allowed_tool_names),
        )

    # 7. Run agent loop
    agent = AgentLoop(loop_config)
    if on_event:
        loop_config.on_event = on_event

    if streaming:
        # For streaming, we need to collect results while yielding events.
        # The caller should use stream_agent() instead.
        # Fall back to non-streaming here.
        pass

    result = await agent.run(enriched_user_message, history=history)
    return result


async def stream_agent(
    pool: "asyncpg.Pool",
    registry: "ToolRegistry",
    *,
    user_message: str,
    mode: Literal["chat", "heartbeat"] = "chat",
    energy_budget: int | None = None,
    tool_context: ToolContext | None = None,
    history: list[dict[str, Any]] | None = None,
    session_id: str | None = None,
    agent_profile: dict[str, Any] | None = None,
    is_group: bool = False,
    dsn: str | None = None,
    has_backlog_tasks: bool = False,
    timeout_seconds: float | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    on_approval: "Callable[[str, dict[str, Any]], Awaitable[bool]] | None" = None,
) -> AsyncIterator[AgentEventData]:
    """
    Streaming variant of run_agent(). Yields AgentEventData as they happen.

    ``on_approval(tool_name, arguments) -> bool`` is called before any tool that
    ``requires_approval`` runs; return False to deny. In interactive chat this is
    the human's [y/N] gate for side-effecting tools (email, DMs, shell, …).

    Used by the SSE chat endpoint to stream tokens to the frontend.
    """
    from core.agent_api import db_dsn_from_env
    from core.cognitive_memory_api import CognitiveMemory, render_chat_memory_context_db

    dsn = dsn or db_dsn_from_env()
    history = history or []

    if tool_context is None:
        tool_context = ToolContext.CHAT if mode == "chat" else ToolContext.HEARTBEAT

    # 1. Load LLM config and run pre-phases
    async with pool.acquire() as conn:
        llm_key = "llm.chat" if mode == "chat" else "llm.heartbeat"
        llm_fallback = "llm" if mode == "chat" else None
        llm_config = await load_llm_config(conn, llm_key, fallback_key=llm_fallback)

        # Hydrate memory
        memory_context = ""
        context = None
        if mode == "chat":
            try:
                yield AgentEventData(
                    event=AgentEvent.PHASE_CHANGE,
                    data={"phase": "memory_recall", "status": "start"},
                )
                mem_client = CognitiveMemory(pool)
                context = await mem_client.hydrate(
                    user_message,
                    memory_limit=10,
                    include_partial=True,
                    include_identity=True,
                    include_worldview=True,
                    include_emotional_state=True,
                    include_goals=True,
                    include_drives=True,
                )
                memory_context = await render_chat_memory_context_db(conn, context, max_memories=10)

                yield AgentEventData(
                    event=AgentEvent.PHASE_CHANGE,
                    data={
                        "phase": "memory_recall",
                        "status": "end",
                        "count": len(context.memories),
                    },
                )
            except Exception as exc:
                logger.warning("Memory hydration failed: %s", exc)

        # Run subconscious
        subconscious_output = SubconsciousOutput()
        sub_signals = ""
        try:
            inline_enabled = bool(await conn.fetchval("SELECT COALESCE(get_config_bool('chat.inline_subconscious_enabled'), true)"))
            if inline_enabled:
                yield AgentEventData(
                    event=AgentEvent.PHASE_CHANGE,
                    data={"phase": "subconscious", "status": "start"},
                )
                subconscious_output = await run_subconscious_appraisal(
                    conn,
                    user_message,
                    hydrated_context=context,
                )
                sub_signals = await render_subconscious_signals_db(conn, subconscious_output)
                yield AgentEventData(
                    event=AgentEvent.PHASE_CHANGE,
                    data={
                        "phase": "subconscious",
                        "status": "end",
                        "output": _subconscious_event_payload(subconscious_output),
                    },
                )
        except Exception as exc:
            logger.warning("Subconscious pre-phase failed: %s", exc)

    skill_selection = await select_skills(
        registry,
        tool_context,
        query=user_message,
        max_skills=4,
    )

    # Build system prompt
    system_prompt = await build_system_prompt(
        mode,
        registry,
        agent_profile,
        subconscious_output=subconscious_output,
        has_backlog_tasks=has_backlog_tasks,
        is_group=is_group,
        active_skills=skill_selection.skills,
        available_skills=skill_selection.available,
        allowed_tool_names=set(skill_selection.allowed_tool_names),
    )

    # Build enriched user message (signals were rendered by the DB in-scope)
    enriched_parts: list[str] = []
    if sub_signals:
        enriched_parts.append(sub_signals)
    if memory_context:
        enriched_parts.append(memory_context)
    enriched_parts.append(f"[USER MESSAGE]\n{user_message}")
    enriched_user_message = "\n\n".join(enriched_parts)

    # Configure loop. Limits derive from config (Bar #1) with sane fallbacks;
    # an explicit caller arg still wins.
    def _cfg_num(key: str, fallback: float) -> float:
        val = llm_config.get(key) if isinstance(llm_config, dict) else None
        try:
            return float(val) if val else fallback
        except (TypeError, ValueError):
            return fallback

    effective_timeout = timeout_seconds or _cfg_num("timeout_seconds", 120.0)
    effective_max_tokens = int(max_tokens or _cfg_num("max_tokens", 4096))
    effective_temperature = (
        temperature if temperature is not None else _cfg_num("temperature", 0.7)
    )
    loop_config = AgentLoopConfig(
        tool_context=tool_context,
        system_prompt=system_prompt,
        llm_config=llm_config,
        registry=registry,
        pool=pool,
        energy_budget=energy_budget,
        max_iterations=None,
        timeout_seconds=effective_timeout,
        temperature=effective_temperature,
        max_tokens=effective_max_tokens,
        session_id=session_id,
        on_approval=on_approval,
        allowed_tool_names=set(skill_selection.allowed_tool_names),
    )

    agent = AgentLoop(loop_config)
    async for event in agent.stream(enriched_user_message, history=history):
        yield event
