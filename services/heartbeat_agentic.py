"""
Agentic Heartbeat Runner

Runs a heartbeat cycle using the unified AgentLoop. Replaces the legacy
JSON-decision path with direct tool_use. The LLM uses real tools (recall,
remember, reflect, manage_goals, etc.) within its energy budget.
"""

from __future__ import annotations

import json
import logging
from typing import Any, TYPE_CHECKING

from core.tools.config import ContextOverrides
from services.agent import run_agent
from services.heartbeat_prompt import render_heartbeat_decision_prompt_db

if TYPE_CHECKING:
    import asyncpg
    from core.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


def _has_backlog_tasks(context: dict[str, Any]) -> bool:
    """Check if backlog has actionable items requiring elevated resources/permissions."""
    backlog = context.get("backlog", {})
    if not isinstance(backlog, dict):
        return False
    actionable = backlog.get("actionable", [])
    if isinstance(actionable, list) and len(actionable) > 0:
        return True
    counts = backlog.get("counts", {})
    if isinstance(counts, dict):
        todo = counts.get("todo", 0) or 0
        in_progress = counts.get("in_progress", 0) or 0
        if todo + in_progress > 0:
            return True
    return False


def _get_checkpoint_context(context: dict[str, Any]) -> str:
    """Extract checkpoint info from in-progress backlog items for prompt inclusion."""
    backlog = context.get("backlog", {})
    if not isinstance(backlog, dict):
        return ""
    actionable = backlog.get("actionable", [])
    if not isinstance(actionable, list):
        return ""

    checkpoint_parts: list[str] = []
    for item in actionable:
        if not isinstance(item, dict):
            continue
        if item.get("status") != "in_progress":
            continue
        checkpoint = item.get("checkpoint")
        if not isinstance(checkpoint, dict) or not checkpoint:
            continue
        title = item.get("title", "Untitled")
        step = checkpoint.get("step", "unknown")
        progress = checkpoint.get("progress", "")
        next_action = checkpoint.get("next_action", "")
        checkpoint_parts.append(
            f"### Resuming: {title}\n"
            f"- Last step: {step}\n"
            f"- Progress: {progress}\n"
            f"- Next action: {next_action}"
        )
    if not checkpoint_parts:
        return ""
    return "\n\n## Checkpoint Resume\n\n" + "\n\n".join(checkpoint_parts)


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
    try:
        pending_review = await conn.fetchval("SELECT hmx_pending_review_summary()")
        if isinstance(pending_review, str):
            pending_review = json.loads(pending_review)
        if not isinstance(pending_review, dict):
            raise TypeError("pending HMX review summary was not an object")
        context = dict(context)
        context["pending_import_review"] = pending_review or {
            "count": 0,
            "by_section": {},
        }
    except Exception as exc:
        logger.warning("Could not load pending HMX review summary: %s", exc)

    try:
        pending_skills = await conn.fetchval("SELECT skill_improvement_pending_summary()")
        if isinstance(pending_skills, str):
            pending_skills = json.loads(pending_skills)
        if not isinstance(pending_skills, dict):
            raise TypeError("pending skill proposal summary was not an object")
        context = dict(context)
        context["pending_skill_proposals"] = pending_skills or {
            "count": 0,
            "proposals": [],
        }
    except Exception as exc:
        logger.warning("Could not load pending skill proposal summary: %s", exc)

    try:
        pending_replacements = await conn.fetchval("SELECT hmx_pending_replacements()")
        if isinstance(pending_replacements, str):
            pending_replacements = json.loads(pending_replacements)
        if not isinstance(pending_replacements, dict):
            raise TypeError("pending protected replacements were not an object")
        context = dict(context)
        context["pending_protected_replacements"] = pending_replacements or {
            "total": 0,
            "records": [],
        }
    except Exception as exc:
        logger.warning("Could not load pending protected replacements: %s", exc)

    try:
        open_reversions = await conn.fetchval("SELECT hmx_open_reversion_windows()")
        if isinstance(open_reversions, str):
            open_reversions = json.loads(open_reversions)
        if not isinstance(open_reversions, dict):
            raise TypeError("open protected reversion windows were not an object")
        context = dict(context)
        context["open_protected_reversions"] = open_reversions or {
            "total": 0,
            "records": [],
        }
    except Exception as exc:
        logger.warning("Could not load protected reversion windows: %s", exc)

    # Check if backlog has actionable tasks (gates resources + permissions)
    has_tasks = _has_backlog_tasks(context)
    if has_tasks:
        logger.info("Backlog has actionable items — scaling resources + granting permissions")

    # Build the user message (heartbeat context snapshot) — rendered in the DB.
    user_message = await render_heartbeat_decision_prompt_db(conn, context)
    pending_prompt = _format_pending_protected_replacements(
        context.get("pending_protected_replacements")
    )
    if pending_prompt:
        user_message += "\n\n" + pending_prompt
    reversion_prompt = _format_open_protected_reversions(
        context.get("open_protected_reversions")
    )
    if reversion_prompt:
        user_message += "\n\n" + reversion_prompt

    # Append checkpoint resume context if there are in-progress items with checkpoints
    if has_tasks:
        checkpoint_ctx = _get_checkpoint_context(context)
        if checkpoint_ctx:
            user_message += "\n" + checkpoint_ctx

    # Extract energy budget from context
    energy = context.get("energy", {})
    energy_budget = energy.get("current", 20)

    # Scale resources when backlog has work
    if has_tasks:
        energy_budget = energy_budget * 2
        logger.info("Backlog energy boost: %d → %d", energy_budget // 2, energy_budget)

    result = await run_agent(
        pool,
        registry,
        user_message=user_message,
        mode="heartbeat",
        energy_budget=energy_budget,
        heartbeat_id=heartbeat_id,
        heartbeat_context=context,
        has_backlog_tasks=has_tasks,
        timeout_seconds=300.0 if has_tasks else 120.0,
        max_tokens=4096 if has_tasks else 2048,
        context_overrides=ContextOverrides(
            allow_shell=True,
            allow_file_write=True,
        ) if has_tasks else None,
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


def _format_pending_protected_replacements(value: Any) -> str:
    if not isinstance(value, dict) or not value.get("total"):
        return ""
    records = value.get("records") or []
    lines = [
        "## Protected Replacement Decisions",
        "These requests cannot change protected state until you explicitly decide.",
    ]
    for record in records[:5]:
        lines.append(
            "- [{replacement_id}] {section}: {rationale}".format(
                replacement_id=record.get("replacement_id", "?"),
                section=record.get("section", "unknown section"),
                rationale=record.get("rationale", "(no rationale)"),
            )
        )
    lines.append(
        "Load the memory-exchange skill, refresh open requests with "
        "protected_replacement_list, inspect each request with "
        "protected_replacement_inspect, then use protected_replacement_review "
        "with accept, refuse, request_modification, or defer. Operator override "
        "is not available to the agent."
    )
    return "\n".join(lines)


def _format_open_protected_reversions(value: Any) -> str:
    if not isinstance(value, dict) or not value.get("total"):
        return ""
    records = value.get("records") or []
    lines = [
        "## Protected Replacement Reversion Windows",
        "Reversion is optional and never automatic. Each window closes when either limit expires.",
    ]
    for record in records[:5]:
        lines.append(
            "- replacement [{replacement_id}] audit [{audit_id}] {section}: "
            "{heartbeats} heartbeats remain; wall-clock deadline {deadline}".format(
                replacement_id=record.get("replacement_id", "?"),
                audit_id=record.get("audit_id", "?"),
                section=record.get("section", "unknown section"),
                heartbeats=record.get("heartbeats_remaining", "?"),
                deadline=record.get("wall_clock_expires_at", "unknown"),
            )
        )
    lines.append(
        "Load the memory-exchange skill and inspect the replacement first. Use "
        "protected_replacement_revert with its audit ID and an explicit rationale "
        "only if restoring the snapshot is your chosen action."
    )
    return "\n".join(lines)


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

    return {
        "completed": True,
        "memory_id": memory_id,
        "energy_spent": energy_spent,
        "outbox_messages": [],
        "has_backlog_tasks": has_tasks,
    }
