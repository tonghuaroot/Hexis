"""
Hexis Tools System - Multi-Agent Council

Provides tools for multi-perspective analysis through council personas,
orchestrated deliberation, and signal aggregation from system events.

F.1 - Agent Personas/Roles (prompt_modules council.persona.*)
F.2 - Council Orchestration Tool (RunCouncilHandler)
F.3 - Signal Aggregation Tool (AggregateSignalsHandler)
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

from .base import (
    ToolCategory,
    ToolContext,
    ToolErrorType,
    ToolExecutionContext,
    ToolHandler,
    ToolResult,
    ToolSpec,
)

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# F.1 -- Council personas (DB-owned: prompt_modules council.persona.*)
# ---------------------------------------------------------------------------


async def load_council_personas(context: "ToolExecutionContext") -> dict[str, dict[str, str]]:
    """Fetch the persona catalog from get_council_personas() (db/33)."""
    pool = context.registry.pool if context.registry else None
    if pool is None:
        raise RuntimeError("Council personas require a database-backed registry")
    async with pool.acquire() as conn:
        raw = await conn.fetchval("SELECT get_council_personas()")
    personas = json.loads(raw) if isinstance(raw, str) else (raw or {})
    if not personas:
        raise RuntimeError("No council personas are seeded (prompt_modules council.persona.*)")
    return personas


# ---------------------------------------------------------------------------
# F.1 -- List Council Personas
# ---------------------------------------------------------------------------


class ListCouncilPersonasHandler(ToolHandler):
    """List the available council personas and their roles."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="list_council_personas",
            description=(
                "List the available multi-agent council personas. "
                "Each persona offers a distinct analytical perspective "
                "for structured deliberation."
            ),
            parameters={
                "type": "object",
                "properties": {},
            },
            category=ToolCategory.MEMORY,
            energy_cost=0,
            is_read_only=True,
            requires_approval=False,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        try:
            personas_summary = await load_council_personas(context)
        except Exception as exc:
            return ToolResult.error_result(str(exc), ToolErrorType.EXECUTION_FAILED)

        return ToolResult(
            success=True,
            output=json.dumps({
                "count": len(personas_summary),
                "personas": personas_summary,
            }),
            energy_spent=0,
        )


# ---------------------------------------------------------------------------
# F.2 -- Run Council
# ---------------------------------------------------------------------------


class RunCouncilHandler(ToolHandler):
    """Orchestrate a multi-perspective council analysis on a topic.

    Prepares a council configuration where each selected persona provides
    their analytical lens on the given topic. The main agent can then use
    these structured perspectives to make well-rounded decisions.
    """

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="run_council",
            description=(
                "Run a multi-agent council deliberation on a topic. "
                "Spawns analysis from multiple persona perspectives "
                "(growth strategist, revenue guardian, skeptical operator, "
                "creative innovator, customer advocate), then runs a "
                "moderator synthesis pass."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "The question or topic for the council to discuss.",
                    },
                    "personas": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Which personas to include (keys from list_council_personas). "
                            "Defaults to all 5."
                        ),
                    },
                    "context": {
                        "type": "string",
                        "description": "Additional context or data for the council.",
                    },
                    "signal_limit": {
                        "type": "integer",
                        "description": "Maximum number of compacted signals to include (default 10, max 30).",
                    },
                },
                "required": ["topic"],
            },
            category=ToolCategory.MEMORY,
            energy_cost=5,
            is_read_only=True,
            optional=True,
            requires_approval=False,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        topic = arguments.get("topic", "").strip()
        if not topic:
            return ToolResult.error_result(
                "Parameter 'topic' is required.",
                ToolErrorType.INVALID_PARAMS,
            )

        requested_personas: list[str] | None = arguments.get("personas")
        extra_context: str = arguments.get("context", "")
        signal_limit: int = max(1, min(int(arguments.get("signal_limit", 10) or 10), 30))

        # Resolve persona keys against the DB catalog
        try:
            personas = await load_council_personas(context)
        except Exception as exc:
            return ToolResult.error_result(str(exc), ToolErrorType.EXECUTION_FAILED)
        if requested_personas:
            invalid = [p for p in requested_personas if p not in personas]
            if invalid:
                return ToolResult.error_result(
                    f"Unknown persona(s): {', '.join(invalid)}. "
                    f"Valid keys: {', '.join(sorted(personas.keys()))}",
                    ToolErrorType.INVALID_PARAMS,
                )
            selected_keys = requested_personas
        else:
            selected_keys = list(personas.keys())

        signals = await self._collect_signals(context, limit=signal_limit)

        # Build the council configuration
        council_analyses: list[dict[str, str]] = []
        for key in selected_keys:
            persona = personas[key]
            prompt_parts = [persona["system_prompt"]]
            if signals:
                prompt_parts.append(
                    "\nCompacted signals:\n" + "\n".join(f"- {s}" for s in signals)
                )
            if extra_context:
                prompt_parts.append(f"\nAdditional context:\n{extra_context}")
            prompt_parts.append(f"\nTopic for analysis:\n{topic}")
            full_prompt = "\n".join(prompt_parts)

            council_analyses.append({
                "persona_key": key,
                "persona_name": persona["name"],
                "system_prompt": persona["system_prompt"],
                "full_prompt": full_prompt,
            })

        analyses = await self._run_parallel_analyses(
            context=context,
            topic=topic,
            council_entries=council_analyses,
        )
        for entry in council_analyses:
            entry["analysis"] = analyses.get(entry["persona_key"], "")

        moderator_report = await self._run_moderator_pass(
            context=context,
            topic=topic,
            council_entries=council_analyses,
        )

        return ToolResult(
            success=True,
            output=json.dumps({
                "topic": topic,
                "persona_count": len(council_analyses),
                "personas_included": [a["persona_key"] for a in council_analyses],
                "signals": signals,
                "council": council_analyses,
                "moderator_report": moderator_report,
                "instructions": (
                    "Council analyses were run in parallel and reconciled via a "
                    "moderator pass. Use moderator_report as the synthesis."
                ),
            }),
            energy_spent=5,
        )

    async def _collect_signals(
        self,
        context: ToolExecutionContext,
        *,
        limit: int,
    ) -> list[str]:
        pool: asyncpg.Pool | None = context.registry.pool if context.registry else None
        if not pool:
            return []

        signals: list[str] = []
        try:
            async with pool.acquire() as conn:
                event_rows = await conn.fetch(
                    """
                    SELECT source::text, payload
                    FROM gateway_events
                    ORDER BY created_at DESC
                    LIMIT $1
                    """,
                    limit,
                )
                for row in event_rows:
                    src = row["source"]
                    payload = row["payload"]
                    if isinstance(payload, str):
                        try:
                            payload = json.loads(payload)
                        except Exception:
                            payload = {}
                    if isinstance(payload, dict):
                        keys = ", ".join(sorted(payload.keys())[:4])
                        signals.append(f"Event[{src}]: payload keys ({keys or 'none'})")
                    else:
                        signals.append(f"Event[{src}]")

                mem_rows = await conn.fetch(
                    """
                    SELECT content
                    FROM memories
                    WHERE type = 'episodic' AND status = 'active'
                    ORDER BY created_at DESC
                    LIMIT $1
                    """,
                    limit,
                )
                for row in mem_rows:
                    content = (row["content"] or "").strip().replace("\n", " ")
                    if content:
                        signals.append(f"Memory: {content[:180]}")

                goal_rows = await conn.fetch(
                    """
                    SELECT content
                    FROM memories
                    WHERE type = 'goal' AND status = 'active'
                    ORDER BY importance DESC NULLS LAST, created_at DESC
                    LIMIT $1
                    """,
                    max(1, limit // 2),
                )
                for row in goal_rows:
                    content = (row["content"] or "").strip().replace("\n", " ")
                    if content:
                        signals.append(f"Goal: {content[:180]}")
        except Exception as exc:
            logger.debug("Council signal collection failed: %s", exc)
            return []

        return signals[:limit]

    async def _run_parallel_analyses(
        self,
        *,
        context: ToolExecutionContext,
        topic: str,
        council_entries: list[dict[str, str]],
    ) -> dict[str, str]:
        async def _run_one(entry: dict[str, str]) -> tuple[str, str]:
            analysis = await self._analyze_with_persona(
                context=context,
                topic=topic,
                persona_name=entry["persona_name"],
                system_prompt=entry["system_prompt"],
                full_prompt=entry["full_prompt"],
            )
            return entry["persona_key"], analysis

        pairs = await asyncio.gather(*[_run_one(entry) for entry in council_entries])
        return {k: v for k, v in pairs}

    async def _analyze_with_persona(
        self,
        *,
        context: ToolExecutionContext,
        topic: str,
        persona_name: str,
        system_prompt: str,
        full_prompt: str,
    ) -> str:
        llm_cfg = await self._load_llm_config(context)
        if llm_cfg is None:
            return f"{persona_name} perspective on '{topic}': {full_prompt[:260]}"

        from core.llm import chat_completion

        try:
            response = await chat_completion(
                provider=llm_cfg["provider"],
                model=llm_cfg["model"],
                endpoint=llm_cfg.get("endpoint"),
                api_key=llm_cfg.get("api_key"),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": full_prompt},
                ],
                temperature=0.4,
                max_tokens=700,
                tools=None,
                auth_mode=llm_cfg.get("auth_mode"),
            )
            text = (response or {}).get("content") or ""
            return text.strip() or f"{persona_name} produced no content."
        except Exception as exc:
            logger.debug("Council persona analysis failed for %s: %s", persona_name, exc)
            return f"{persona_name} analysis unavailable ({exc})."

    async def _run_moderator_pass(
        self,
        *,
        context: ToolExecutionContext,
        topic: str,
        council_entries: list[dict[str, str]],
    ) -> str:
        llm_cfg = await self._load_llm_config(context)
        if llm_cfg is None:
            return self._heuristic_moderator_summary(council_entries)

        from core.llm import chat_completion

        payload = [
            {"persona": e["persona_name"], "analysis": e.get("analysis", "")}
            for e in council_entries
        ]
        moderator_prompt = (
            f"Topic: {topic}\n\n"
            "Persona analyses (JSON):\n"
            f"{json.dumps(payload, ensure_ascii=True)}\n\n"
            "Produce a reconciled report with:\n"
            "1) Agreements\n2) Key disagreements\n3) Risks\n4) Recommended actions."
        )

        try:
            response = await chat_completion(
                provider=llm_cfg["provider"],
                model=llm_cfg["model"],
                endpoint=llm_cfg.get("endpoint"),
                api_key=llm_cfg.get("api_key"),
                messages=[
                    {"role": "system", "content": "You are a neutral moderator that reconciles expert perspectives."},
                    {"role": "user", "content": moderator_prompt},
                ],
                temperature=0.3,
                max_tokens=900,
                tools=None,
                auth_mode=llm_cfg.get("auth_mode"),
            )
            text = (response or {}).get("content") or ""
            return text.strip() or self._heuristic_moderator_summary(council_entries)
        except Exception as exc:
            logger.debug("Council moderator pass failed: %s", exc)
            return self._heuristic_moderator_summary(council_entries)

    async def _load_llm_config(
        self,
        context: ToolExecutionContext,
    ) -> dict[str, Any] | None:
        pool: asyncpg.Pool | None = context.registry.pool if context.registry else None
        if not pool:
            return None
        try:
            from core.llm_config import resolve_llm_config
            return await resolve_llm_config(pool, "llm.chat", fallback_key="llm")
        except Exception:
            return None

    @staticmethod
    def _heuristic_moderator_summary(council_entries: list[dict[str, str]]) -> str:
        lines = [
            "Moderator synthesis (fallback mode):",
            "Agreements: Council members provided perspectives on growth, risk, and customer impact.",
            "Disagreements: Trade-offs center on speed vs. risk and expansion vs. margin discipline.",
            "Risks: Execution complexity, cost overruns, and potential customer confusion.",
            "Recommended actions: Run a limited pilot, measure outcomes, and iterate before full rollout.",
            "",
            "Persona notes:",
        ]
        for entry in council_entries:
            snippet = (entry.get("analysis") or "").strip().replace("\n", " ")
            lines.append(f"- {entry['persona_name']}: {snippet[:180]}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# F.3 -- Aggregate Signals
# ---------------------------------------------------------------------------


class AggregateSignalsHandler(ToolHandler):
    """Aggregate recent signals from events, memories, and goals.

    Provides a consolidated 'state of affairs' snapshot combining
    gateway events, episodic memories, and active goals.
    """

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="aggregate_signals",
            description=(
                "Aggregate recent signals across gateway events, episodic "
                "memories, and active goals into a consolidated snapshot. "
                "Useful for situational awareness before council deliberation "
                "or autonomous decision-making."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "string",
                        "description": (
                            "Filter signals by domain/source "
                            "(e.g. 'email', 'calendar', 'cron', 'chat')."
                        ),
                    },
                    "days": {
                        "type": "integer",
                        "description": "How far back to look (default 7 days).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max signals per category (default 20).",
                    },
                },
            },
            category=ToolCategory.MEMORY,
            energy_cost=3,
            is_read_only=True,
            requires_approval=False,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        pool: asyncpg.Pool | None = (
            context.registry.pool if context.registry else None
        )
        if not pool:
            return ToolResult.error_result(
                "Database pool not available.",
                ToolErrorType.MISSING_CONFIG,
            )

        domain: str | None = arguments.get("domain")
        days: int = max(1, arguments.get("days", 7))
        limit: int = max(1, min(arguments.get("limit", 20), 100))

        events: list[dict[str, Any]] = []
        memories: list[dict[str, Any]] = []
        goals: list[dict[str, Any]] = []

        async with pool.acquire() as conn:
            # ----- Gateway events -----
            try:
                if domain:
                    event_rows = await conn.fetch(
                        """
                        SELECT id, source::text, status::text, session_key,
                               payload, created_at, completed_at
                        FROM gateway_events
                        WHERE created_at >= now() - make_interval(days => $1)
                          AND source::text = $2
                        ORDER BY created_at DESC
                        LIMIT $3
                        """,
                        days, domain, limit,
                    )
                else:
                    event_rows = await conn.fetch(
                        """
                        SELECT id, source::text, status::text, session_key,
                               payload, created_at, completed_at
                        FROM gateway_events
                        WHERE created_at >= now() - make_interval(days => $1)
                        ORDER BY created_at DESC
                        LIMIT $2
                        """,
                        days, limit,
                    )

                for row in event_rows:
                    events.append({
                        "id": row["id"],
                        "source": row["source"],
                        "status": row["status"],
                        "session_key": row["session_key"],
                        "payload_keys": list(
                            json.loads(row["payload"]).keys()
                        ) if row["payload"] else [],
                        "created_at": row["created_at"].isoformat()
                            if row["created_at"] else None,
                    })
            except Exception as exc:
                logger.debug("Failed to query gateway_events: %s", exc)

            # ----- Recent episodic memories -----
            try:
                mem_rows = await conn.fetch(
                    """
                    SELECT id, content, importance, created_at,
                           metadata
                    FROM memories
                    WHERE type = 'episodic'
                      AND status = 'active'
                      AND created_at >= now() - make_interval(days => $1)
                    ORDER BY created_at DESC
                    LIMIT $2
                    """,
                    days, limit,
                )
                for row in mem_rows:
                    memories.append({
                        "id": str(row["id"]),
                        "content": (row["content"] or "")[:300],
                        "importance": float(row["importance"])
                            if row["importance"] is not None else None,
                        "created_at": row["created_at"].isoformat()
                            if row["created_at"] else None,
                    })
            except Exception as exc:
                logger.debug("Failed to query episodic memories: %s", exc)

            # ----- Active goals -----
            try:
                goal_rows = await conn.fetch(
                    """
                    SELECT id, content, importance, metadata, created_at
                    FROM memories
                    WHERE type = 'goal'
                      AND status = 'active'
                    ORDER BY importance DESC NULLS LAST
                    LIMIT $1
                    """,
                    limit,
                )
                for row in goal_rows:
                    goals.append({
                        "id": str(row["id"]),
                        "content": (row["content"] or "")[:300],
                        "importance": float(row["importance"])
                            if row["importance"] is not None else None,
                        "created_at": row["created_at"].isoformat()
                            if row["created_at"] else None,
                    })
            except Exception as exc:
                logger.debug("Failed to query goals: %s", exc)

        snapshot = {
            "time_window_days": days,
            "domain_filter": domain,
            "events": {
                "count": len(events),
                "items": events,
            },
            "memories": {
                "count": len(memories),
                "items": memories,
            },
            "goals": {
                "count": len(goals),
                "items": goals,
            },
            "summary": {
                "total_signals": len(events) + len(memories) + len(goals),
                "event_sources": list(
                    set(e["source"] for e in events)
                ) if events else [],
                "highest_importance_goal": (
                    goals[0]["content"][:100] if goals else None
                ),
            },
        }

        return ToolResult(
            success=True,
            output=json.dumps(snapshot, default=str),
            energy_spent=3,
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_council_tools() -> list[ToolHandler]:
    """Create the multi-agent council tools."""
    return [
        ListCouncilPersonasHandler(),
        RunCouncilHandler(),
        AggregateSignalsHandler(),
    ]
