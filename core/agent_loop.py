"""
Hexis Unified Agent Loop

A single agentic loop shared by both chat and heartbeat contexts.
The LLM calls tools via the standard tool_use API, with results fed
back into the conversation for self-correction.

Differences between contexts are confined to:
- System prompt (chat vs heartbeat)
- Energy budget (None = unlimited for chat; int for heartbeat)
- Approval mechanism (callback for interactive; DB-based for autonomous)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Awaitable, Callable, TYPE_CHECKING

from core.llm import chat_completion, stream_chat_completion
from core.tools.base import ToolContext, ToolExecutionContext
from core.usage import record_llm_usage

if TYPE_CHECKING:
    import asyncpg
    from core.tools.config import ContextOverrides
    from core.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


class AgentEvent(str, Enum):
    """Events emitted during the agent loop."""

    LOOP_START = "loop_start"
    TEXT_DELTA = "text_delta"
    TOOL_START = "tool_start"
    TOOL_RESULT = "tool_result"
    APPROVAL_REQUEST = "approval_request"
    ENERGY_EXHAUSTED = "energy_exhausted"
    LOOP_END = "loop_end"
    ERROR = "error"
    PHASE_CHANGE = "phase_change"
    CONTINUATION = "continuation"


@dataclass
class AgentEventData:
    """Payload for an agent loop event."""

    event: AgentEvent
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class AgentLoopConfig:
    """Configuration for an agent loop run."""

    tool_context: ToolContext
    system_prompt: str
    llm_config: dict[str, Any]  # {provider, model, endpoint, api_key}
    registry: "ToolRegistry"
    pool: "asyncpg.Pool"

    # Energy budget — None means unlimited (chat mode)
    energy_budget: int | None = None

    # Limits
    max_iterations: int | None = None  # None = timeout-based only
    timeout_seconds: float = 300.0

    # LLM params
    temperature: float = 0.7
    max_tokens: int = 4096

    # Session
    session_id: str | None = None
    heartbeat_id: str | None = None

    # Callbacks
    on_event: Callable[[AgentEventData], Awaitable[None]] | None = None
    on_approval: Callable[[str, dict[str, Any]], Awaitable[bool]] | None = None

    # Planning phases (Gap 1)
    enable_planning: bool = False
    planning_prompt: str | None = None
    verify_prompt: str | None = None

    # Runtime permission overrides (Gap 4)
    context_overrides: "ContextOverrides | None" = None

    # Continuation nudge (Gap 5)
    continuation_prompt: str | None = None
    max_continuations: int = 0


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass
class AgentLoopResult:
    """Result of a completed agent loop run."""

    text: str
    messages: list[dict[str, Any]]
    tool_calls_made: list[dict[str, Any]]
    iterations: int
    energy_spent: int
    timed_out: bool = False
    stopped_reason: str = "completed"
    plan_text: str = ""
    phases_completed: list[str] = field(default_factory=list)
    continuations_used: int = 0


# ---------------------------------------------------------------------------
# AgentLoop
# ---------------------------------------------------------------------------


class AgentLoop:
    """
    Unified agentic loop for Hexis.

    Chat and heartbeat share the same loop. The only differences are the
    system prompt and energy budget, configured via AgentLoopConfig.

    Usage::

        config = AgentLoopConfig(
            tool_context=ToolContext.CHAT,
            system_prompt="...",
            llm_config=normalized,
            registry=registry,
            pool=pool,
        )
        agent = AgentLoop(config)
        result = await agent.run("Hello!")
    """

    def __init__(self, config: AgentLoopConfig) -> None:
        self.config = config
        self._energy_spent: int = 0
        self._iteration_count: int = 0
        self._tool_calls_made: list[dict[str, Any]] = []
        self._last_text: str = ""
        self._streaming: bool = False
        self._continuations_used: int = 0
        self._plan_text: str = ""
        self._phases_completed: list[str] = []
        self._turn_id: str | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(
        self,
        user_message: str,
        history: list[dict[str, Any]] | None = None,
    ) -> AgentLoopResult:
        """Run the agent loop to completion."""
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.config.system_prompt},
        ]
        messages.extend(history or [])
        messages.append({"role": "user", "content": user_message})

        tools = await self.config.registry.get_specs(self.config.tool_context)
        await self._start_turn(user_message, messages)

        await self._emit(AgentEvent.LOOP_START, {
            "tool_context": self.config.tool_context.value,
            "energy_budget": self.config.energy_budget,
            "tool_count": len(tools),
        })

        try:
            result = await asyncio.wait_for(
                self._loop(messages, tools),
                timeout=self.config.timeout_seconds,
            )
        except asyncio.TimeoutError:
            result = AgentLoopResult(
                text=self._last_text,
                messages=messages,
                tool_calls_made=self._tool_calls_made,
                iterations=self._iteration_count,
                energy_spent=self._energy_spent,
                timed_out=True,
                stopped_reason="timeout",
            )

        await self._emit(AgentEvent.LOOP_END, {
            "stopped_reason": result.stopped_reason,
            "iterations": result.iterations,
            "energy_spent": result.energy_spent,
            "timed_out": result.timed_out,
        })
        await self._finish_turn(result)

        return result

    async def stream(
        self,
        user_message: str,
        history: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[AgentEventData]:
        """
        Streaming variant of run().

        Yields AgentEventData as they happen. Callers can filter by
        event type (e.g. TEXT_DELTA for text streaming).
        """
        queue: asyncio.Queue[AgentEventData | None] = asyncio.Queue()
        original_on_event = self.config.on_event

        async def _enqueue(event: AgentEventData) -> None:
            await queue.put(event)
            if original_on_event:
                await original_on_event(event)

        self.config.on_event = _enqueue
        self._streaming = True

        # Run loop in background task
        task = asyncio.create_task(self.run(user_message, history))

        # Signal completion via sentinel
        def _on_done(_: asyncio.Task) -> None:  # type: ignore[type-arg]
            queue.put_nowait(None)

        task.add_done_callback(_on_done)

        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield item
        finally:
            # Restore original callback
            self.config.on_event = original_on_event
            # Ensure task exceptions propagate
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            elif task.exception():
                raise task.exception()  # type: ignore[misc]

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------

    async def _loop(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AgentLoopResult:
        """Dispatcher: routes to planned or direct execution loop."""
        if not self.config.enable_planning:
            return await self._execute_loop(messages, tools)
        return await self._planned_loop(messages, tools)

    async def _llm_call(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """
        Dispatch a single LLM call (streaming or non-streaming).

        Returns the raw response dict with 'content' and 'tool_calls'.
        Raises on LLM failure (caller is responsible for error handling).
        """
        cfg = self.config
        llm = cfg.llm_config

        if self._streaming:
            async def _on_text_delta(token: str) -> None:
                await self._emit(AgentEvent.TEXT_DELTA, {
                    "text": token,
                    "iteration": self._iteration_count,
                })

            result = await stream_chat_completion(
                provider=llm["provider"],
                model=llm["model"],
                endpoint=llm.get("endpoint"),
                api_key=llm.get("api_key"),
                messages=messages,
                tools=tools if tools else None,
                temperature=cfg.temperature,
                max_tokens=cfg.max_tokens,
                on_text_delta=_on_text_delta,
                auth_mode=llm.get("auth_mode"),
            )
        else:
            result = await chat_completion(
                provider=llm["provider"],
                model=llm["model"],
                endpoint=llm.get("endpoint"),
                api_key=llm.get("api_key"),
                messages=messages,
                tools=tools if tools else None,
                temperature=cfg.temperature,
                max_tokens=cfg.max_tokens,
                auth_mode=llm.get("auth_mode"),
            )

        # Record API usage (fire-and-forget)
        source = "heartbeat" if cfg.heartbeat_id else "chat"
        session_key = cfg.session_id or cfg.heartbeat_id
        asyncio.ensure_future(record_llm_usage(
            provider=llm["provider"],
            model=llm["model"],
            raw_response=result.get("raw"),
            operation="stream" if self._streaming else "chat",
            session_key=session_key,
            source=source,
            pool=cfg.pool,
        ))

        return result

    async def _execute_loop(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AgentLoopResult:
        """Core agentic loop: LLM -> tool calls -> results -> LLM."""
        cfg = self.config

        while True:
            db_step = await self._next_agent_step()
            if db_step.get("action") == "stop":
                reason = db_step.get("reason") or "completed"
                if reason == "energy":
                    await self._emit(AgentEvent.ENERGY_EXHAUSTED, {
                        "budget": cfg.energy_budget,
                        "spent": self._energy_spent,
                    })
                return self._make_result(messages, reason)

            # Check iteration limit
            if cfg.max_iterations is not None and self._iteration_count >= cfg.max_iterations:
                return self._make_result(messages, "max_iterations")

            # Check energy budget
            if cfg.energy_budget is not None and self._energy_spent >= cfg.energy_budget:
                await self._emit(AgentEvent.ENERGY_EXHAUSTED, {
                    "budget": cfg.energy_budget,
                    "spent": self._energy_spent,
                })
                return self._make_result(messages, "energy")

            self._iteration_count += 1

            # LLM call
            try:
                response = await self._llm_call(messages, tools)
            except Exception as e:
                logger.error("LLM call failed at iteration %d: %s", self._iteration_count, e)
                await self._emit(AgentEvent.ERROR, {"error": str(e), "iteration": self._iteration_count})
                return self._make_result(messages, "error")

            text = response.get("content", "") or ""
            tool_calls = response.get("tool_calls") or []
            await self._apply_llm_result(response)

            if text:
                self._last_text = text
                # Only emit per-iteration TEXT_DELTA in non-streaming mode
                # (streaming mode emits per-token via the callback)
                if not self._streaming:
                    await self._emit(AgentEvent.TEXT_DELTA, {"text": text, "iteration": self._iteration_count})

            # Build assistant message with tool_calls in OpenAI format
            assistant_msg: dict[str, Any] = {"role": "assistant", "content": text}
            if tool_calls:
                assistant_msg["tool_calls"] = [
                    _to_openai_tool_call(tc) for tc in tool_calls
                ]
            messages.append(assistant_msg)

            if not tool_calls:
                if (
                    self.config.continuation_prompt is not None
                    and self._continuations_used < self.config.max_continuations
                ):
                    self._continuations_used += 1
                    await self._emit(AgentEvent.CONTINUATION, {
                        "continuation_number": self._continuations_used,
                        "max_continuations": self.config.max_continuations,
                    })
                    messages.append({
                        "role": "user",
                        "content": self.config.continuation_prompt,
                    })
                    continue
                return self._make_result(messages, "completed")

            # Process tool calls
            for call in tool_calls:
                # Check energy before each tool call
                if cfg.energy_budget is not None and self._energy_spent >= cfg.energy_budget:
                    # Skip remaining tools - budget exhausted mid-iteration
                    break

                tool_name = call.get("name", "")
                arguments = call.get("arguments", {})
                call_id = call.get("id") or str(uuid.uuid4())

                # Check approval via callback
                spec = cfg.registry.get_spec(tool_name)
                if spec and spec.requires_approval and cfg.on_approval:
                    await self._emit(AgentEvent.APPROVAL_REQUEST, {
                        "tool_name": tool_name,
                        "arguments": arguments,
                    })
                    try:
                        approved = await cfg.on_approval(tool_name, arguments)
                    except Exception:
                        approved = False

                    if not approved:
                        messages.append({
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": f"Tool call '{tool_name}' was denied by the user.",
                        })
                        self._tool_calls_made.append({
                            "name": tool_name,
                            "arguments": arguments,
                            "success": False,
                            "denied": True,
                            "energy_spent": 0,
                        })
                        continue

                # Build execution context
                exec_ctx = await self._build_exec_context(call_id)

                await self._emit(AgentEvent.TOOL_START, {
                    "tool_name": tool_name,
                    "arguments": arguments,
                    "iteration": self._iteration_count,
                })

                # Execute tool via registry (policy + hooks + audit)
                result = await cfg.registry.execute(tool_name, arguments, exec_ctx)
                self._energy_spent += result.energy_spent
                await self._apply_tool_result(call_id, tool_name, result)

                await self._emit(AgentEvent.TOOL_RESULT, {
                    "tool_name": tool_name,
                    "success": result.success,
                    "energy_spent": result.energy_spent,
                    "total_energy_spent": self._energy_spent,
                    "duration": result.duration_seconds,
                    "error": result.error,
                    "output": result.output,
                    "display_output": result.display_output,
                })

                self._tool_calls_made.append({
                    "name": tool_name,
                    "arguments": arguments,
                    "success": result.success,
                    "energy_spent": result.energy_spent,
                    "error": result.error,
                })

                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": result.to_model_output(),
                })

        # Should not reach here, but safety net
        return self._make_result(messages, "completed")  # pragma: no cover

    # ------------------------------------------------------------------
    # Planned loop (Gap 1: plan → execute → verify)
    # ------------------------------------------------------------------

    _DEFAULT_PLANNING_PROMPT = (
        "Before acting, think through your approach. What are the steps needed? "
        "What could go wrong? How will you verify success? Produce a brief plan."
    )
    _DEFAULT_VERIFY_PROMPT = (
        "Review what you just did. Did you achieve the goal? If something needs "
        "fixing, take action now. If everything looks good, summarize what was accomplished."
    )

    async def _planned_loop(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AgentLoopResult:
        """
        Three-phase agentic loop: Plan → Execute → Verify.

        - Plan: LLM thinks without tools, producing a plan
        - Execute: Normal tool-use loop (_execute_loop)
        - Verify: LLM reviews results, may call tools for corrections
        """
        # Phase 1: Plan
        await self._emit(AgentEvent.PHASE_CHANGE, {"phase": "plan"})
        self._phases_completed.append("plan")

        planning_prompt = self.config.planning_prompt or self._DEFAULT_PLANNING_PROMPT
        messages.append({"role": "user", "content": planning_prompt})
        self._iteration_count += 1

        try:
            response = await self._llm_call(messages, tools=None)
        except Exception as e:
            logger.error("Plan phase LLM call failed: %s", e)
            await self._emit(AgentEvent.ERROR, {"error": str(e), "phase": "plan"})
            return self._make_result(messages, "error")

        plan_text = response.get("content", "") or ""
        if plan_text:
            self._last_text = plan_text
            self._plan_text = plan_text
            if not self._streaming:
                await self._emit(AgentEvent.TEXT_DELTA, {"text": plan_text, "iteration": self._iteration_count})

        messages.append({"role": "assistant", "content": plan_text})

        # Phase 2: Execute
        await self._emit(AgentEvent.PHASE_CHANGE, {"phase": "execute"})
        self._phases_completed.append("execute")

        exec_result = await self._execute_loop(messages, tools)

        # If execute didn't complete normally, skip verify
        if exec_result.stopped_reason != "completed":
            return exec_result

        # Phase 3: Verify
        await self._emit(AgentEvent.PHASE_CHANGE, {"phase": "verify"})
        self._phases_completed.append("verify")

        verify_prompt = self.config.verify_prompt or self._DEFAULT_VERIFY_PROMPT
        messages.append({"role": "user", "content": verify_prompt})

        # Reset continuation counter for verify phase
        self._continuations_used = 0

        verify_result = await self._execute_loop(messages, tools)
        return verify_result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _build_exec_context(self, call_id: str) -> ToolExecutionContext:
        """Build ToolExecutionContext with config overrides and remaining energy."""
        cfg = self.config
        remaining_energy: int | None = None
        if cfg.energy_budget is not None:
            remaining_energy = max(0, cfg.energy_budget - self._energy_spent)

        ctx = ToolExecutionContext(
            tool_context=cfg.tool_context,
            call_id=call_id,
            session_id=cfg.session_id,
            heartbeat_id=cfg.heartbeat_id,
            energy_available=remaining_energy,
            allow_network=True,
            allow_shell=False,
            allow_file_read=True,
            allow_file_write=False,
        )

        # Apply overrides from ToolsConfig
        try:
            tc = await cfg.registry.get_config()
            overrides = tc.get_context_overrides(cfg.tool_context)
            ctx.allow_shell = overrides.allow_shell
            ctx.allow_file_write = overrides.allow_file_write
            if tc.workspace_path:
                ctx.workspace_path = tc.workspace_path
        except Exception as e:
            logger.debug("Failed to apply config overrides: %s", e)

        # Apply runtime overrides from AgentLoopConfig (additive only — can
        # grant permissions but never revoke what the DB config granted)
        if cfg.context_overrides is not None:
            rt = cfg.context_overrides
            if rt.allow_shell:
                ctx.allow_shell = True
            if rt.allow_file_write:
                ctx.allow_file_write = True
            if rt.allow_all:
                ctx.allow_shell = True
                ctx.allow_file_write = True

        return ctx

    def _session_uuid_or_none(self) -> str | None:
        if not self.config.session_id:
            return None
        try:
            return str(uuid.UUID(str(self.config.session_id)))
        except Exception:
            return None

    async def _start_turn(self, user_message: str, messages: list[dict[str, Any]]) -> None:
        try:
            async with self.config.pool.acquire() as conn:
                raw = await conn.fetchval(
                    "SELECT start_agent_turn($1::text, $2::text, $3::uuid, $4::jsonb)",
                    self.config.tool_context.value,
                    user_message,
                    self._session_uuid_or_none(),
                    json.dumps({
                        "messages": messages,
                        "energy_budget": self.config.energy_budget,
                        "max_iterations": self.config.max_iterations,
                        "max_continuations": self.config.max_continuations,
                        "heartbeat_id": self.config.heartbeat_id,
                    }),
                )
            payload = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(payload, dict) and payload.get("turn_id"):
                self._turn_id = str(payload["turn_id"])
        except Exception:
            logger.debug("DB agent turn start unavailable; continuing without DB turn state", exc_info=True)

    async def _next_agent_step(self) -> dict[str, Any]:
        if not self._turn_id:
            return {"action": "llm"}
        try:
            async with self.config.pool.acquire() as conn:
                raw = await conn.fetchval("SELECT next_agent_step($1::uuid)", self._turn_id)
            payload = json.loads(raw) if isinstance(raw, str) else raw
            return payload if isinstance(payload, dict) else {"action": "llm"}
        except Exception:
            logger.debug("DB agent next step unavailable; using local loop policy", exc_info=True)
            return {"action": "llm"}

    async def _apply_llm_result(self, response: dict[str, Any]) -> None:
        if not self._turn_id:
            return
        try:
            async with self.config.pool.acquire() as conn:
                await conn.fetchval(
                    "SELECT apply_agent_llm_result($1::uuid, $2::jsonb)",
                    self._turn_id,
                    json.dumps({
                        "content": response.get("content", "") or "",
                        "tool_calls": response.get("tool_calls") or [],
                    }),
                )
        except Exception:
            logger.debug("DB agent LLM result apply failed", exc_info=True)

    async def _apply_tool_result(self, call_id: str, tool_name: str, result: Any) -> None:
        if not self._turn_id:
            return
        try:
            async with self.config.pool.acquire() as conn:
                await conn.fetchval(
                    "SELECT apply_agent_tool_result($1::uuid, $2::text, $3::jsonb)",
                    self._turn_id,
                    call_id,
                    json.dumps({
                        "tool_name": tool_name,
                        "success": result.success,
                        "output": result.output,
                        "display_output": result.display_output,
                        "model_output": result.to_model_output(),
                        "error": result.error,
                        "error_type": result.error_type.value if result.error_type else None,
                        "energy_spent": result.energy_spent,
                        "duration_seconds": result.duration_seconds,
                    }),
                )
        except Exception:
            logger.debug("DB agent tool result apply failed", exc_info=True)

    async def _finish_turn(self, result: AgentLoopResult) -> None:
        if not self._turn_id:
            return
        try:
            async with self.config.pool.acquire() as conn:
                await conn.fetchval(
                    "SELECT finish_agent_turn($1::uuid, $2::jsonb)",
                    self._turn_id,
                    json.dumps({
                        "status": "completed",
                        "stopped_reason": result.stopped_reason,
                        "text": result.text,
                        "iterations": result.iterations,
                        "energy_spent": result.energy_spent,
                        "timed_out": result.timed_out,
                    }),
                )
        except Exception:
            logger.debug("DB agent finish failed", exc_info=True)

    async def _emit(self, event: AgentEvent, data: dict[str, Any] | None = None) -> None:
        """Emit an event via the configured callback."""
        if self._turn_id:
            try:
                async with self.config.pool.acquire() as conn:
                    await conn.fetchval(
                        "SELECT record_agent_turn_event($1::uuid, $2::text, $3::jsonb)",
                        self._turn_id,
                        event.value,
                        json.dumps(data or {}),
                    )
            except Exception:
                logger.debug("DB agent event record failed", exc_info=True)
        if self.config.on_event:
            try:
                await self.config.on_event(AgentEventData(
                    event=event,
                    data=data or {},
                ))
            except Exception:
                logger.debug("Event callback failed for %s", event, exc_info=True)

    def _make_result(self, messages: list[dict[str, Any]], stopped_reason: str) -> AgentLoopResult:
        """Build an AgentLoopResult from current state."""
        return AgentLoopResult(
            text=self._last_text,
            messages=messages,
            tool_calls_made=self._tool_calls_made,
            iterations=self._iteration_count,
            energy_spent=self._energy_spent,
            timed_out=False,
            stopped_reason=stopped_reason,
            plan_text=self._plan_text,
            phases_completed=list(self._phases_completed),
            continuations_used=self._continuations_used,
        )


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _to_openai_tool_call(call: dict[str, Any]) -> dict[str, Any]:
    """Convert simplified tool call dict to OpenAI assistant message format."""
    arguments = call.get("arguments", {})
    if isinstance(arguments, dict):
        arguments = json.dumps(arguments)
    return {
        "id": call.get("id") or str(uuid.uuid4()),
        "type": "function",
        "function": {
            "name": call.get("name", ""),
            "arguments": arguments,
        },
    }
