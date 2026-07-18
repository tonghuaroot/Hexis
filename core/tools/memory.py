"""
Hexis Tools System - Memory Tools

Tools for memory operations (recall, remember, etc.).
These wrap the existing CognitiveMemory API.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from .base import (
    ToolCategory,
    ToolContext,
    ToolErrorType,
    ToolExecutionContext,
    ToolHandler,
    ToolResult,
    ToolSpec,
)


async def _try_db_memory_tool(tool_name: str, arguments: dict[str, Any], context: ToolExecutionContext) -> ToolResult | None:
    pool = context.registry.pool if context.registry else None
    if not pool:
        return None
    try:
        async with pool.acquire() as conn:
            raw = await conn.fetchval(
                "SELECT execute_memory_tool($1::text, $2::jsonb)",
                tool_name,
                json.dumps(arguments),
            )
        payload = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(payload, dict) and "success" in payload:
            if payload.get("success"):
                return ToolResult.success_result(payload.get("output"), payload.get("display_output"))
            try:
                error_type = ToolErrorType(payload.get("error_type") or ToolErrorType.EXECUTION_FAILED.value)
            except ValueError:
                error_type = ToolErrorType.EXECUTION_FAILED
            return ToolResult.error_result(payload.get("error") or "Memory tool failed", error_type)
    except Exception:
        return None
    return None


class RecallHandler(ToolHandler):
    """Search memories by semantic similarity."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="recall",
            description=(
                "Search memories by semantic similarity and/or structured filters. "
                "Use this to find memories related to a topic, concept, or question. "
                "Supports filtering by source, date range, concept graph, and metadata. "
                "Can be used with just filters (no query) for targeted retrieval."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language query describing what you want to remember. Optional if using filters.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": (
                            "Memory-count budget for this recall. Defaults and ceiling "
                            "are config-driven (memory.recall_default_limit / "
                            "memory.recall_max_limit); ask for more when the question "
                            "genuinely spans many memories."
                        ),
                        "minimum": 1,
                    },
                    "min_score": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 1.0,
                        "description": (
                            "Relevance floor: drop results scoring below this instead "
                            "of relying on count alone."
                        ),
                    },
                    "memory_types": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["episodic", "semantic", "procedural", "strategic", "worldview", "goal"],
                        },
                        "description": (
                            "Filter by memory types. Omit to search ALL types (recommended for most queries). "
                            "Types: episodic (events/experiences), semantic (facts/knowledge), "
                            "procedural (how-to), strategic (patterns/plans), "
                            "worldview (identity, values, beliefs, boundaries, interests), "
                            "goal (objectives/aspirations)."
                        ),
                    },
                    "min_importance": {
                        "type": "number",
                        "description": "Minimum importance score (0.0-1.0).",
                        "default": 0.0,
                        "minimum": 0.0,
                        "maximum": 1.0,
                    },
                    "source_path": {
                        "type": "string",
                        "description": "Filter by source path (partial match). E.g., 'hexis/db' for all DB schema memories.",
                    },
                    "source_kind": {
                        "type": "string",
                        "description": "Filter by source kind. E.g., 'code', 'conversation', 'web', 'document'.",
                    },
                    "created_after": {
                        "type": "string",
                        "description": "Only memories created after this ISO date (e.g. '2025-01-15').",
                    },
                    "created_before": {
                        "type": "string",
                        "description": "Only memories created before this ISO date.",
                    },
                    "concept": {
                        "type": "string",
                        "description": "Find memories linked to this concept in the knowledge graph.",
                    },
                },
                "required": [],
            },
            category=ToolCategory.MEMORY,
            energy_cost=1,
            is_read_only=True,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        args = dict(arguments)
        if context.is_group:
            # Group rooms recall without private memories (#92/#96) — the
            # same wall hydrate already enforces, applied to the tool path.
            args["exclude_sensitive"] = True
        db_result = await _try_db_memory_tool("recall", args, context)
        if db_result is not None:
            return db_result
        # execute_memory_tool (db/38) owns this tool; the former Python
        # compatibility path was deleted.
        return ToolResult.error_result(
            "execute_memory_tool dispatch failed (database unavailable or errored)",
            ToolErrorType.EXECUTION_FAILED,
        )


class SearchHistoryHandler(ToolHandler):
    """Run free lexical search across prior turns and consolidated memories."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="search_history",
            description=(
                "Search exact words, names, phrases, and operators across stored "
                "conversation turns and consolidated memories using Postgres full-text "
                "search. This is cross-session and does not require embeddings. "
                "For a pure timeline ('what happened yesterday?'), pass created_after/"
                "created_before with an empty query — a time window alone returns "
                "everything in it, newest first."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Postgres web-search query. Supports quoted phrases, OR, "
                            "and minus-prefixed exclusions. Leave empty to browse a "
                            "time window chronologically."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 200,
                        "default": 20,
                        "description": (
                            "Up to 50 for keyword search; time-window browsing "
                            "(no keywords) allows up to 200 preview-grain rows."
                        ),
                    },
                    "sources": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["turn", "memory"]},
                        "minItems": 1,
                        "uniqueItems": True,
                        "default": ["turn", "memory"],
                    },
                    "created_after": {
                        "type": "string",
                        "description": "Optional inclusive ISO-8601 lower time bound.",
                    },
                    "created_before": {
                        "type": "string",
                        "description": "Optional exclusive ISO-8601 upper time bound.",
                    },
                    "exclude_current_session": {
                        "type": "boolean",
                        "default": True,
                        "description": (
                            "Exclude raw turns from the current UUID session when one "
                            "is available. Consolidated memories remain searchable."
                        ),
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            category=ToolCategory.MEMORY,
            energy_cost=0,
            is_read_only=True,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        # The session id is I/O context only Python holds; everything else —
        # validation, browse-vs-keyword limits, shaping, the truncation
        # note — is owned by execute_memory_tool (db/38).
        args = dict(arguments)
        if context.is_group:
            args["exclude_sensitive"] = True
        if args.pop("exclude_current_session", True) and context.session_id:
            try:
                args["exclude_session_id"] = str(UUID(str(context.session_id)))
            except ValueError:
                pass
        db_result = await _try_db_memory_tool("search_history", args, context)
        if db_result is not None:
            return db_result
        return ToolResult.error_result(
            "execute_memory_tool dispatch failed (database unavailable or errored)",
            ToolErrorType.EXECUTION_FAILED,
        )


class RememberHandler(ToolHandler):
    """Store a new memory."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="remember",
            description=(
                "Store a new memory. Use this to save important information, "
                "events, or learnings for future recall. When the memory comes "
                "from a document, conversation, or other source, cite it in "
                "`sources` — provenance is what makes a belief revisable and "
                "explainable later."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The content to remember.",
                    },
                    "type": {
                        "type": "string",
                        "enum": ["episodic", "semantic", "procedural", "strategic"],
                        "default": "episodic",
                        "description": "Type of memory to create.",
                    },
                    "importance": {
                        "type": "number",
                        "description": "Importance score (0.0-1.0).",
                        "default": 0.5,
                        "minimum": 0.0,
                        "maximum": 1.0,
                    },
                    "concepts": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Concepts to link this memory to.",
                    },
                    "confidence": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 1.0,
                        "default": 0.5,
                        "description": (
                            "How confident you are the content is true, given the "
                            "evidence (semantic memories only)."
                        ),
                    },
                    "sources": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "kind": {
                                    "type": "string",
                                    "description": (
                                        "Evidence kind, e.g. user_testimony, "
                                        "repository_document, web_page, self_observation."
                                    ),
                                },
                                "ref": {
                                    "type": "string",
                                    "description": "Path, URL, or identifier of the source.",
                                },
                                "label": {"type": "string"},
                                "author": {"type": "string"},
                                "trust": {
                                    "type": "number",
                                    "minimum": 0.0,
                                    "maximum": 1.0,
                                },
                            },
                        },
                        "description": (
                            "Where this came from. Semantic memories record every "
                            "source and derive trust from them; other types use "
                            "the first as attribution."
                        ),
                    },
                },
                "required": ["content"],
            },
            category=ToolCategory.MEMORY,
            energy_cost=1,
            is_read_only=False,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        db_result = await _try_db_memory_tool("remember", arguments, context)
        if db_result is not None:
            return db_result
        # execute_memory_tool (db/38) owns this tool; the former Python
        # compatibility path was deleted.
        return ToolResult.error_result(
            "execute_memory_tool dispatch failed (database unavailable or errored)",
            ToolErrorType.EXECUTION_FAILED,
        )


class AddEvidenceHandler(ToolHandler):
    """Attach evidence to an existing belief and revise its confidence."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="add_evidence",
            description=(
                "Attach new evidence to an existing semantic memory (belief) and "
                "revise its confidence through the calibrated evidence policy. "
                "Use this when something you read or were told corroborates or "
                "contradicts a belief you already hold, instead of creating a "
                "duplicate memory. Returns prior and posterior confidence so you "
                "can report honestly how much the evidence moved you; duplicate "
                "sources are merged without changing confidence."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "memory_id": {
                        "type": "string",
                        "description": "UUID of the semantic memory the evidence bears on (from recall).",
                    },
                    "stance": {
                        "type": "string",
                        "enum": ["supports", "contradicts"],
                        "description": "Whether the evidence supports or contradicts the belief.",
                    },
                    "source": {
                        "type": "object",
                        "properties": {
                            "kind": {
                                "type": "string",
                                "description": (
                                    "Evidence kind, e.g. user_testimony, "
                                    "repository_document, web_page, self_observation."
                                ),
                            },
                            "ref": {
                                "type": "string",
                                "description": "Path, URL, or identifier of the source.",
                            },
                            "label": {"type": "string"},
                            "author": {"type": "string"},
                            "trust": {
                                "type": "number",
                                "minimum": 0.0,
                                "maximum": 1.0,
                            },
                        },
                        "description": "The evidence source; must include at least ref or label.",
                    },
                    "note": {
                        "type": "string",
                        "description": (
                            "Optional short observation of what the evidence says; "
                            "stored as an episodic evidence memory linked to the belief."
                        ),
                    },
                },
                "required": ["memory_id", "stance", "source"],
            },
            category=ToolCategory.MEMORY,
            energy_cost=1,
            is_read_only=False,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        db_result = await _try_db_memory_tool("add_evidence", arguments, context)
        if db_result is not None:
            return db_result
        return ToolResult.error_result(
            "execute_memory_tool dispatch failed (database unavailable or errored)",
            ToolErrorType.EXECUTION_FAILED,
        )


class BeliefHistoryHandler(ToolHandler):
    """Explain why a belief is held: revision history, evidence, sources."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="belief_history",
            description=(
                "Explain why you believe something: given a semantic memory's id "
                "(from recall), returns its current confidence and trust, its truth "
                "profile (sources, reinforcement, worldview alignment), the full "
                "audited revision history (what evidence moved confidence, when, "
                "and by how much), linked supporting/contradicting evidence, and "
                "any contradicting sources. Use this when asked why you believe "
                "something or what changed your mind."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "memory_id": {
                        "type": "string",
                        "description": "UUID of the memory to explain (from recall).",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "default": 20,
                        "description": "Maximum revision entries to return (newest first).",
                    },
                },
                "required": ["memory_id"],
            },
            category=ToolCategory.MEMORY,
            energy_cost=0,
            is_read_only=True,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        db_result = await _try_db_memory_tool("belief_history", arguments, context)
        if db_result is not None:
            return db_result
        return ToolResult.error_result(
            "execute_memory_tool dispatch failed (database unavailable or errored)",
            ToolErrorType.EXECUTION_FAILED,
        )


class OpenMemoryHandler(ToolHandler):
    """Graded recall's drill-down: the verbatim experience behind a memory."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="open_memory",
            description=(
                "Open a memory to its verbatim grain: given a memory's id (from "
                "recall or search_history), returns the exact source turns behind "
                "it time-ordered, the pre-summary full text if it has been gisted, "
                "and any archived originals a consolidation superseded. Recall "
                "gives you the shape of a memory; open_memory gives you the exact "
                "words — reach for it when precise wording, quotes, or the full "
                "moment matter."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "memory_id": {
                        "type": "string",
                        "description": "UUID of the memory to open.",
                    },
                    "max_units": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "default": 40,
                        "description": "Maximum verbatim source turns to return.",
                    },
                },
                "required": ["memory_id"],
            },
            category=ToolCategory.MEMORY,
            energy_cost=1,
            is_read_only=True,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        db_result = await _try_db_memory_tool("open_memory", arguments, context)
        if db_result is not None:
            return db_result
        return ToolResult.error_result(
            "execute_memory_tool dispatch failed (database unavailable or errored)",
            ToolErrorType.EXECUTION_FAILED,
        )


class SenseMemoryAvailabilityHandler(ToolHandler):
    """Quick feeling-of-knowing check before full recall."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="sense_memory_availability",
            description=(
                "Sense whether you likely have relevant memories before doing a full recall. "
                "Use this for a quick feeling-of-knowing check."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Topic to check memory availability for.",
                    },
                },
                "required": ["query"],
            },
            category=ToolCategory.MEMORY,
            energy_cost=0,  # Free - lightweight check
            is_read_only=True,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        db_result = await _try_db_memory_tool("sense_memory_availability", arguments, context)
        if db_result is not None:
            return db_result
        # execute_memory_tool (db/38) owns this tool; the former Python
        # compatibility path was deleted.
        return ToolResult.error_result(
            "execute_memory_tool dispatch failed (database unavailable or errored)",
            ToolErrorType.EXECUTION_FAILED,
        )


class ExploreConceptHandler(ToolHandler):
    """Explore memories connected to a concept."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="explore_concept",
            internal=True,  # folded into `associate` (#99); one-release alias
            description=(
                "Explore memories connected to a specific concept. Shows how different "
                "memories relate to an idea and what other concepts are connected."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "concept": {
                        "type": "string",
                        "description": "The concept to explore.",
                    },
                    "include_related": {
                        "type": "boolean",
                        "description": "Also return memories linked to related concepts.",
                        "default": True,
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum memories to return.",
                        "default": 5,
                        "minimum": 1,
                        "maximum": 20,
                    },
                },
                "required": ["concept"],
            },
            category=ToolCategory.MEMORY,
            energy_cost=1,
            is_read_only=True,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        db_result = await _try_db_memory_tool("explore_concept", arguments, context)
        if db_result is not None:
            return db_result
        return ToolResult.error_result(
            "execute_memory_tool dispatch failed (database unavailable or errored)",
            ToolErrorType.EXECUTION_FAILED,
        )


class AssociateHandler(ToolHandler):
    """Free association over the memory graph (agent-facing name: associate)."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="associate",
            description=(
                "Follow what something reminds you of: free association through "
                "your memory's own connections. Start from a question or from "
                "specific memories and let the associations unfold — what "
                "supports what, what contradicts, what led to what, what an "
                "idea connects to. Use this when the question is about how "
                "things relate rather than what a single memory says."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Recall seed memories matching this text (used if 'seeds' omitted).",
                    },
                    "seeds": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Explicit seed memory ids (uuid). Use if you already have them.",
                    },
                    "rel_types": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Restrict expansion to these edge types (e.g. ['CAUSES','SUPPORTS']). Omit for all.",
                    },
                    "depth": {
                        "type": "integer",
                        "description": "Max hops from a seed.",
                        "default": 2,
                        "minimum": 1,
                        "maximum": 4,
                    },
                    "budget": {
                        "type": "integer",
                        "description": "Max nodes in the result.",
                        "default": 30,
                        "minimum": 1,
                        "maximum": 100,
                    },
                },
            },
            category=ToolCategory.MEMORY,
            energy_cost=1,
            is_read_only=True,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        db_result = await _try_db_memory_tool("explore_subgraph", arguments, context)
        if db_result is not None:
            return db_result
        return ToolResult.error_result(
            "execute_memory_tool dispatch failed (database unavailable or errored)",
            ToolErrorType.EXECUTION_FAILED,
        )


class GetProceduresHandler(ToolHandler):
    """Retrieve procedural memories for a task."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="get_procedures",
            description=(
                "Retrieve procedural memories (how-to knowledge) for a specific task. "
                "Returns step-by-step instructions and prerequisites."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "The task you want to know how to do.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum procedures to return.",
                        "default": 3,
                        "minimum": 1,
                        "maximum": 10,
                    },
                },
                "required": ["task"],
            },
            category=ToolCategory.MEMORY,
            energy_cost=1,
            is_read_only=True,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        db_result = await _try_db_memory_tool("get_procedures", arguments, context)
        if db_result is not None:
            return db_result
        return ToolResult.error_result(
            "execute_memory_tool dispatch failed (database unavailable or errored)",
            ToolErrorType.EXECUTION_FAILED,
        )


class GetStrategiesHandler(ToolHandler):
    """Retrieve strategic memories for a situation."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="get_strategies",
            description=(
                "Retrieve strategic memories (patterns, heuristics, lessons learned) "
                "applicable to a situation. These are meta-level insights about what works."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "situation": {
                        "type": "string",
                        "description": "The situation you need strategic guidance for.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum strategies to return.",
                        "default": 3,
                        "minimum": 1,
                        "maximum": 10,
                    },
                },
                "required": ["situation"],
            },
            category=ToolCategory.MEMORY,
            energy_cost=1,
            is_read_only=True,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        db_result = await _try_db_memory_tool("get_strategies", arguments, context)
        if db_result is not None:
            return db_result
        return ToolResult.error_result(
            "execute_memory_tool dispatch failed (database unavailable or errored)",
            ToolErrorType.EXECUTION_FAILED,
        )


class CreateGoalHandler(ToolHandler):
    """Create a new goal for the agent."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="create_goal",
            description=(
                "Create a new goal for the agent to pursue. Use this for reminders, "
                "TODOs, or longer-term objectives."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Short goal title.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional longer description.",
                    },
                    "priority": {
                        "type": "string",
                        "enum": ["active", "queued", "backburner"],
                        "default": "queued",
                        "description": "Desired priority.",
                    },
                    "source": {
                        "type": "string",
                        "enum": ["curiosity", "user_request", "identity", "derived", "external"],
                        "default": "user_request",
                        "description": "Why this goal exists.",
                    },
                },
                "required": ["title"],
            },
            category=ToolCategory.MEMORY,
            energy_cost=1,
            is_read_only=False,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        title = arguments["title"]
        description = arguments.get("description")
        priority = arguments.get("priority", "queued")
        source = arguments.get("source", "user_request")

        try:
            async with context.registry.pool.acquire() as conn:
                goal_id = await conn.fetchval(
                    """
                    SELECT create_goal(
                        p_title := $1,
                        p_description := $2,
                        p_priority := $3,
                        p_source := $4
                    )
                    """,
                    title,
                    description,
                    priority,
                    source,
                )

            return ToolResult.success_result(
                output={"goal_id": str(goal_id), "title": title, "priority": priority},
                display_output=f"Created goal: {title}",
            )

        except Exception as e:
            return ToolResult.error_result(str(e), ToolErrorType.EXECUTION_FAILED)


class ScheduleTaskHandler(ToolHandler):
    """Create a scheduled (cron-like) task."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="schedule_task",
            description=(
                "Create a scheduled task (cron-like). Use for recurring reminders or timed actions."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Short task name."},
                    "description": {"type": "string", "description": "Optional longer description."},
                    "schedule_kind": {
                        "type": "string",
                        "enum": ["once", "interval", "daily", "weekly"],
                        "description": "Schedule type.",
                    },
                    "schedule": {"type": "object", "description": "Schedule details for the selected type."},
                    "timezone": {
                        "type": "string",
                        "description": "IANA timezone name (e.g., America/Los_Angeles).",
                    },
                    "action_kind": {
                        "type": "string",
                        "enum": ["queue_user_message", "create_goal"],
                        "description": "Action to perform when the schedule fires.",
                    },
                    "action_payload": {"type": "object", "description": "Action payload."},
                    "max_runs": {
                        "type": "integer",
                        "description": "Optional max number of runs before auto-disable.",
                    },
                },
                "required": ["name", "schedule_kind", "schedule", "action_kind", "action_payload"],
            },
            category=ToolCategory.MEMORY,
            energy_cost=1,
            is_read_only=False,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        import json

        name = arguments["name"]
        schedule_kind = arguments["schedule_kind"]
        schedule = arguments.get("schedule") or {}
        action_kind = arguments["action_kind"]
        action_payload = arguments.get("action_payload") or {}
        timezone = arguments.get("timezone")
        description = arguments.get("description")
        max_runs = arguments.get("max_runs")

        try:
            async with context.registry.pool.acquire() as conn:
                task_id = await conn.fetchval(
                    """
                    SELECT create_scheduled_task(
                        $1,
                        $2,
                        $3::jsonb,
                        $4,
                        $5::jsonb,
                        $6,
                        $7,
                        'active',
                        $8,
                        'agent'
                    )
                    """,
                    name,
                    schedule_kind,
                    json.dumps(schedule),
                    action_kind,
                    json.dumps(action_payload),
                    timezone,
                    description,
                    max_runs,
                )

            return ToolResult.success_result(
                output={"task_id": str(task_id), "name": name},
                display_output=f"Scheduled task: {name}",
            )

        except Exception as e:
            return ToolResult.error_result(str(e), ToolErrorType.EXECUTION_FAILED)


class ListScheduledTasksHandler(ToolHandler):
    """List scheduled tasks."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="list_scheduled_tasks",
            description="List scheduled tasks with optional filters.",
            parameters={
                "type": "object",
                "properties": {
                    "status": {"type": "string", "description": "Optional status filter"},
                    "due_before": {"type": "string", "description": "Optional ISO8601 cutoff"},
                    "limit": {"type": "integer", "default": 50},
                },
                "required": [],
            },
            category=ToolCategory.MEMORY,
            energy_cost=0,
            is_read_only=True,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        status = arguments.get("status")
        due_before = arguments.get("due_before")
        limit = int(arguments.get("limit", 50))

        try:
            async with context.registry.pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT * FROM list_scheduled_tasks($1, $2::timestamptz, $3)",
                    status,
                    due_before,
                    limit,
                )
            tasks = [dict(row) for row in rows]
            return ToolResult.success_result(
                output={"tasks": tasks, "count": len(tasks)},
                display_output=f"Found {len(tasks)} scheduled task(s)",
            )
        except Exception as e:
            return ToolResult.error_result(str(e), ToolErrorType.EXECUTION_FAILED)


class UpdateScheduledTaskHandler(ToolHandler):
    """Update a scheduled task."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="update_scheduled_task",
            description="Update a scheduled task (schedule/action/status).",
            parameters={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "schedule_kind": {"type": "string"},
                    "schedule": {"type": "object"},
                    "timezone": {"type": "string"},
                    "action_kind": {"type": "string"},
                    "action_payload": {"type": "object"},
                    "status": {"type": "string"},
                    "max_runs": {"type": "integer"},
                },
                "required": ["task_id"],
            },
            category=ToolCategory.MEMORY,
            energy_cost=1,
            is_read_only=False,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        import json

        task_id = arguments["task_id"]
        try:
            async with context.registry.pool.acquire() as conn:
                updated = await conn.fetchval(
                    """
                    SELECT update_scheduled_task(
                        $1::uuid,
                        $2,
                        $3,
                        $4,
                        $5::jsonb,
                        $6,
                        $7,
                        $8::jsonb,
                        $9,
                        $10
                    )
                    """,
                    task_id,
                    arguments.get("name"),
                    arguments.get("description"),
                    arguments.get("schedule_kind"),
                    json.dumps(arguments.get("schedule")) if arguments.get("schedule") is not None else None,
                    arguments.get("timezone"),
                    arguments.get("action_kind"),
                    json.dumps(arguments.get("action_payload")) if arguments.get("action_payload") is not None else None,
                    arguments.get("status"),
                    arguments.get("max_runs"),
                )

            return ToolResult.success_result(
                output={"task": updated},
                display_output=f"Updated task {task_id}",
            )
        except Exception as e:
            return ToolResult.error_result(str(e), ToolErrorType.EXECUTION_FAILED)


class DeleteScheduledTaskHandler(ToolHandler):
    """Disable or delete a scheduled task."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="delete_scheduled_task",
            description="Disable or delete a scheduled task.",
            parameters={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "hard_delete": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": ["task_id"],
            },
            category=ToolCategory.MEMORY,
            energy_cost=1,
            is_read_only=False,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        task_id = arguments["task_id"]
        hard_delete = bool(arguments.get("hard_delete", False))
        reason = arguments.get("reason")
        try:
            async with context.registry.pool.acquire() as conn:
                ok = await conn.fetchval(
                    "SELECT delete_scheduled_task($1::uuid, $2::boolean, $3)",
                    task_id,
                    hard_delete,
                    reason,
                )
            return ToolResult.success_result(
                output={"deleted": bool(ok), "task_id": task_id},
                display_output=f"Deleted task {task_id}" if hard_delete else f"Disabled task {task_id}",
            )
        except Exception as e:
            return ToolResult.error_result(str(e), ToolErrorType.EXECUTION_FAILED)


class PonderHandler(ToolHandler):
    """Let a question simmer: file a background memory search (#98)."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="request_background_search",
            description=(
                "Let a question simmer in the back of your mind. Files a "
                "background memory search; if the subconscious finds it "
                "later, the answer rises as spontaneous recall — and "
                "reaches the user as an 'it came back to me' note when it "
                "resolves strongly. Use when something feels familiar but "
                "will not surface right now."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What you are trying to remember.",
                    },
                },
                "required": ["query"],
            },
            category=ToolCategory.MEMORY,
            energy_cost=0,
            is_read_only=False,
            allowed_contexts={ToolContext.CHAT, ToolContext.HEARTBEAT},
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        query = str(arguments.get("query") or "").strip()
        if not query:
            return ToolResult.error_result("query is required", ToolErrorType.INVALID_PARAMS)
        try:
            async with context.registry.pool.acquire() as conn:
                activation_id = await conn.fetchval(
                    "SELECT request_background_search($1::text)", query
                )
            return ToolResult.success_result(
                {"activation_id": str(activation_id), "query": query},
                display_output=f"Letting it simmer: {query[:80]}",
            )
        except Exception as e:
            return ToolResult.error_result(str(e), ToolErrorType.EXECUTION_FAILED)


class QueueUserMessageHandler(ToolHandler):
    """Queue a message for the user."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="queue_user_message",
            description="Queue a message for external delivery to the user.",
            parameters={
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Message body for the user.",
                    },
                    "intent": {
                        "type": "string",
                        "description": "Optional intent/category (e.g. 'reminder', 'status', 'question').",
                    },
                },
                "required": ["message"],
            },
            category=ToolCategory.MEMORY,
            energy_cost=0,  # Free - just queuing
            is_read_only=False,
            allowed_contexts={ToolContext.HEARTBEAT},  # Only for autonomous use
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        message = arguments["message"]
        intent = arguments.get("intent")

        try:
            # Durably enqueue in the DB-native outbox; the maintenance worker
            # drains it to the RabbitMQ outbox for delivery.
            async with context.registry.pool.acquire() as conn:
                await conn.fetchval(
                    "SELECT queue_outbox_message($1::text, $2::text, 'tool')",
                    message,
                    intent,
                )

            return ToolResult.success_result(
                output={"queued": True, "message": message[:50]},
                display_output=f"Queued message: {message[:50]}...",
            )

        except Exception as e:
            return ToolResult.error_result(str(e), ToolErrorType.EXECUTION_FAILED)


class ExploreSubgraphAliasHandler(AssociateHandler):
    """One-release alias for the old name (internal: hidden, unbound)."""

    @property
    def spec(self) -> ToolSpec:
        spec = super().spec
        spec.name = "explore_subgraph"
        spec.internal = True
        return spec


class TraceWhyHandler(ToolHandler):
    """Introspective causation: why do I think/feel this?"""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="trace_why",
            description=(
                "Ask yourself why: trace where a memory or belief came from — "
                "the chain of causes, evidence, and prior experiences behind "
                "it. Give the memory id (from recall or open_memory) and get "
                "its ancestry, nearest first."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "memory_id": {
                        "type": "string",
                        "description": "The memory/belief to trace (uuid).",
                    },
                    "depth": {
                        "type": "integer",
                        "description": "How far back to trace.",
                        "default": 3,
                        "minimum": 1,
                        "maximum": 6,
                    },
                },
                "required": ["memory_id"],
            },
            category=ToolCategory.MEMORY,
            energy_cost=1,
            is_read_only=True,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        try:
            memory_id = UUID(str(arguments["memory_id"]))
        except (ValueError, KeyError):
            return ToolResult.error_result("memory_id must be a uuid", ToolErrorType.INVALID_PARAMS)
        depth = max(1, min(int(arguments.get("depth") or 3), 6))
        try:
            async with context.registry.pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT cause_id, cause_content, relationship, distance "
                    "FROM find_causal_chain($1::uuid, $2)", memory_id, depth,
                )
            causes = [
                {"memory_id": str(r["cause_id"]), "content": r["cause_content"],
                 "relationship": r["relationship"], "distance": r["distance"]}
                for r in rows
            ]
            display = (
                f"{len(causes)} step(s) in the chain behind {str(memory_id)[:8]}"
                if causes else "No recorded causes behind this memory — it may be a root experience."
            )
            return ToolResult.success_result({"causes": causes, "count": len(causes)}, display)
        except Exception as e:
            return ToolResult.error_result(str(e), ToolErrorType.EXECUTION_FAILED)


def create_memory_tools() -> list[ToolHandler]:
    """Create memory tool handlers.

    Note: Goal management is handled by core/tools/goals.py (manage_goals).
    Schedule management is handled by core/tools/cron.py (manage_schedule).
    Those unified tools replace the individual create_goal, schedule_task, etc.
    """
    return [
        RecallHandler(),
        SearchHistoryHandler(),
        RememberHandler(),
        AddEvidenceHandler(),
        BeliefHistoryHandler(),
        OpenMemoryHandler(),
        SenseMemoryAvailabilityHandler(),
        ExploreConceptHandler(),
        AssociateHandler(),
        GetProceduresHandler(),
        GetStrategiesHandler(),
        QueueUserMessageHandler(),
        PonderHandler(),
        TraceWhyHandler(),
        ExploreSubgraphAliasHandler(),
    ]
