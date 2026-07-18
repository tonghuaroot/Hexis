"""
Agentic Heartbeat Runner

Runs a heartbeat cycle using the unified AgentLoop. Replaces the legacy
JSON-decision path with direct tool_use. The LLM uses real tools (recall,
remember, reflect, manage_goals, etc.) within its energy budget.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable, TYPE_CHECKING

from core.tools.config import ContextOverrides
from services.agent import run_agent
from services.heartbeat_prompt import render_heartbeat_decision_prompt_db

if TYPE_CHECKING:
    import asyncpg
    from core.agent_loop import AgentEventData
    from core.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


async def build_heartbeat_system_prompt(
    registry: "ToolRegistry | None" = None,
    *,
    has_backlog_tasks: bool = False,
) -> str:
    """Build the system prompt for an agentic heartbeat.

    Compatibility wrapper — delegates to services.agent.build_system_prompt().
    """
    from services.agent import build_system_prompt as _build
    from core.tools.registry import ToolRegistry as _TR

    if registry is None:
        # Create a minimal mock for the prompt builder
        class _NoopRegistry:
            async def get_specs(self, ctx):
                return []
        registry = _NoopRegistry()  # type: ignore[assignment]
    return await _build(
        "heartbeat",
        registry,  # type: ignore[arg-type]
        has_backlog_tasks=has_backlog_tasks,
    )


async def run_agentic_heartbeat(
    conn: "asyncpg.Connection",
    *,
    pool: "asyncpg.Pool",
    registry: "ToolRegistry",
    heartbeat_id: str,
    context: dict[str, Any],
    on_event: Callable[["AgentEventData"], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """
    Run a single heartbeat cycle using the unified agent runner.

    Returns a dict with:
    - completed: bool
    - text: str (final agent text)
    - tool_calls_made: list
    - energy_spent: int
    - stopped_reason: str
    - has_backlog_tasks: bool
    """
    # The DB owns the whole plan (db/68 heartbeat_agentic_plan): context
    # enrichment, the backlog gate, resource scaling, the shell/file-write
    # permission grant, and the protected-decision prompt fragments.
    plan_raw = await conn.fetchval(
        "SELECT heartbeat_agentic_plan($1::jsonb)", json.dumps(context, default=str)
    )
    plan = json.loads(plan_raw) if isinstance(plan_raw, str) else (plan_raw or {})
    context = plan.get("context") or context
    has_tasks = bool(plan.get("has_backlog_tasks"))
    if has_tasks:
        logger.info("Backlog has actionable items — scaling resources + granting permissions")

    user_message = await render_heartbeat_decision_prompt_db(conn, context)
    if plan.get("prompt_suffix"):
        user_message += "\n\n" + plan["prompt_suffix"]

    result = await run_agent(
        pool,
        registry,
        user_message=user_message,
        mode="heartbeat",
        energy_budget=plan.get("energy_budget", 20),
        heartbeat_id=heartbeat_id,
        heartbeat_context=context,
        has_backlog_tasks=has_tasks,
        timeout_seconds=float(plan.get("timeout_seconds", 120.0)),
        max_tokens=int(plan.get("max_tokens", 2048)),
        context_overrides=ContextOverrides(
            allow_shell=True,
            allow_file_write=True,
        ) if plan.get("allow_shell") else None,
        on_event=on_event,
    )

    return {
        "completed": result.stopped_reason == "completed",
        "text": result.text,
        "tool_calls_made": result.tool_calls_made,
        "energy_spent": result.energy_spent,
        "iterations": result.iterations,
        "stopped_reason": result.stopped_reason,
        "timed_out": result.timed_out,
        "has_backlog_tasks": has_tasks,
    }


async def finalize_heartbeat(
    conn: "asyncpg.Connection",
    *,
    heartbeat_id: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    """
    Finalize a heartbeat after the agentic loop completes.

    Records the heartbeat as an episodic memory and updates state.
    If backlog had tasks, auto-checkpoints in-progress items that were
    not explicitly completed on timeout/energy exhaustion.
    """
    text = result.get("text", "")
    tool_calls = result.get("tool_calls_made", [])
    energy_spent = result.get("energy_spent", 0)
    stopped_reason = result.get("stopped_reason", "completed")
    has_tasks = result.get("has_backlog_tasks", False)

    # Build a summary of what happened
    tool_names = [tc.get("name", "?") for tc in tool_calls]
    summary = text or f"Heartbeat completed: {len(tool_calls)} tool calls, {energy_spent} energy spent."
    if tool_names:
        summary += f" Tools used: {', '.join(tool_names)}."
    if has_tasks:
        summary += " [backlog active]"

    # Persist finalization in the DB (db/43 finalize_agentic_heartbeat):
    # episodic memory + heartbeat_state bump + auto-checkpoint of interrupted
    # in-progress backlog items — previously three inline SQL blocks here.
    memory_id = None
    try:
        raw = await conn.fetchval(
            "SELECT finalize_agentic_heartbeat($1::text, $2::text, $3::int, $4::int, $5::text, $6::boolean)",
            heartbeat_id,
            summary,
            int(energy_spent or 0),
            len(tool_calls),
            stopped_reason,
            has_tasks,
        )
        payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
        memory_id = payload.get("memory_id")
    except Exception:
        logger.debug("Failed to finalize heartbeat", exc_info=True)
        # finalize_agentic_heartbeat releases the claim itself; only the
        # failure path needs an explicit guarded release.
        await conn.fetchval("SELECT release_active_heartbeat($1)", heartbeat_id)

    return {
        "completed": True,
        "memory_id": memory_id,
        "energy_spent": energy_spent,
        "outbox_messages": [],
        "has_backlog_tasks": has_tasks,
    }
