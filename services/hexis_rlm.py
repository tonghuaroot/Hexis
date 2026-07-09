"""Vendored RLM engine for Hexis.

Implements the Recursive Language Model loop (Algorithm 1 from paper 2512.24601v2)
adapted for Hexis. Does NOT import the upstream `rlm` package.

Provides:
- run_heartbeat_decision(): RLM loop for heartbeat decisions
- run_chat_turn(): RLM loop for chat conversations
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Callable

from core.llm import chat_completion, normalize_llm_config
from core.memory_repo import MemoryRepo
from core.tools.repl_bridge import ReplToolBridge, call_records_to_actions_taken
from services.prompt_resources import (
    compose_compact_personhood_prompt,
    load_rlm_chat_prompt,
    load_rlm_heartbeat_prompt,
)
from services.rlm_memory_env import RLMMemoryEnv, RLMWorkspace, WorkspaceBudgets
from services.rlm_repl import HexisLocalREPL, REPLResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Parsing utilities (vendored from docs/reference/rlm-main/rlm/utils/parsing.py)
# ---------------------------------------------------------------------------

_CODE_BLOCK_RE = re.compile(r"```repl\s*\n(.*?)\n```", re.DOTALL)
_FINAL_VAR_RE = re.compile(r"^\s*FINAL_VAR\((.*?)\)", re.MULTILINE | re.DOTALL)
_FINAL_RE = re.compile(r"^\s*FINAL\((.*)\)\s*$", re.MULTILINE | re.DOTALL)

MAX_OUTPUT_CHARS = 20_000


def find_code_blocks(text: str) -> list[str]:
    """Find REPL code blocks wrapped in ```repl ... ```."""
    return [m.group(1).strip() for m in _CODE_BLOCK_RE.finditer(text)]


def find_final_answer(text: str, repl: HexisLocalREPL | None = None) -> str | None:
    """Find FINAL(...) or FINAL_VAR(...) in response text."""
    # Check FINAL_VAR first
    match = _FINAL_VAR_RE.search(text)
    if match:
        variable_name = match.group(1).strip().strip('"').strip("'")
        if repl is not None:
            result = repl.execute_code(f"print(FINAL_VAR({variable_name!r}))")
            answer = result.stdout.strip()
            return answer if answer else (result.stderr.strip() or None)
        return None

    # Check FINAL
    match = _FINAL_RE.search(text)
    if match:
        return match.group(1).strip()

    return None


def format_execution_result(result: REPLResult) -> str:
    """Format a REPLResult for inclusion in message history."""
    parts = []
    if result.stdout:
        parts.append(result.stdout)
    if result.stderr:
        parts.append(result.stderr)
    if result.local_vars:
        parts.append(f"REPL variables: {list(result.local_vars.keys())}")
    return "\n\n".join(parts) if parts else "No output"


def format_iteration(response: str, code_blocks: list[str], results: list[REPLResult]) -> list[dict[str, str]]:
    """Format an RLM iteration for the message history."""
    messages = [{"role": "assistant", "content": response}]

    for code, result in zip(code_blocks, results):
        formatted = format_execution_result(result)
        if len(formatted) > MAX_OUTPUT_CHARS:
            formatted = (
                formatted[:MAX_OUTPUT_CHARS]
                + f"... + [{len(formatted) - MAX_OUTPUT_CHARS} chars truncated]"
            )
        messages.append({
            "role": "user",
            "content": f"Code executed:\n```python\n{code}\n```\n\nREPL output:\n{formatted}",
        })

    return messages


# ---------------------------------------------------------------------------
# LLM adapter
# ---------------------------------------------------------------------------

async def _llm_completion(
    messages: list[dict[str, str]],
    llm_config: dict[str, Any],
    max_tokens: int = 4096,
) -> str:
    """Call Hexis LLM and return the text response."""
    result = await chat_completion(
        provider=llm_config["provider"],
        model=llm_config["model"],
        endpoint=llm_config.get("endpoint"),
        api_key=llm_config.get("api_key"),
        messages=messages,
        temperature=0.7,
        max_tokens=max_tokens,
    )
    import asyncio
    from core.usage import record_llm_usage
    asyncio.ensure_future(record_llm_usage(
        provider=llm_config["provider"],
        model=llm_config["model"],
        raw_response=result.get("raw"),
        source="rlm",
    ))
    return result.get("content", "")


def _make_sync_llm_query(
    llm_config: dict[str, Any],
    loop: asyncio.AbstractEventLoop,
) -> Callable[[str], str]:
    """Create a synchronous llm_query function for use in the REPL."""

    def llm_query(prompt: str) -> str:
        messages = [
            {"role": "system", "content": "You are a helpful assistant. Answer concisely."},
            {"role": "user", "content": prompt},
        ]
        try:
            future = asyncio.run_coroutine_threadsafe(
                _llm_completion(messages, llm_config, max_tokens=2048),
                loop,
            )
            return future.result(timeout=120)
        except Exception as e:
            return f"Error: LLM query failed - {e}"

    return llm_query


# ---------------------------------------------------------------------------
# User prompt (iteration suffix)
# ---------------------------------------------------------------------------

_USER_PROMPT_FIRST = (
    "You have not interacted with the REPL environment or seen your context yet. "
    "Your next action should be to examine the context variable and figure out how "
    "to approach the task. Don't provide a final answer yet.\n\n"
    "Think step-by-step on what to do using the REPL environment. "
    "Continue writing ```repl``` code blocks and determine your answer. Your next action:"
)

_USER_PROMPT_CONTINUE = (
    "The history above shows your previous interactions with the REPL environment. "
    "Continue using the REPL environment, which has the `context` variable and memory syscalls, "
    "and determine your answer. Your next action:"
)


def _build_user_prompt(iteration: int) -> dict[str, str]:
    if iteration == 0:
        return {"role": "user", "content": _USER_PROMPT_FIRST}
    return {"role": "user", "content": _USER_PROMPT_CONTINUE}


# ---------------------------------------------------------------------------
# Core RLM loop (synchronous, runs in thread pool)
# ---------------------------------------------------------------------------

def _run_loop(
    repl: HexisLocalREPL,
    llm_config: dict[str, Any],
    loop: asyncio.AbstractEventLoop,
    system_prompt: str,
    max_iterations: int,
) -> dict[str, Any]:
    """
    Synchronous RLM iteration loop (Algorithm 1).

    Runs in a thread pool executor to avoid blocking the async event loop.
    LLM calls bridge back to the async loop via run_coroutine_threadsafe.
    """
    message_history: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt},
    ]

    final_answer: str | None = None
    iteration_count = 0

    for i in range(max_iterations):
        iteration_count = i + 1

        # Build current prompt
        current_prompt = message_history + [_build_user_prompt(i)]

        # Call LLM
        future = asyncio.run_coroutine_threadsafe(
            _llm_completion(current_prompt, llm_config, max_tokens=4096),
            loop,
        )
        try:
            response = future.result(timeout=120)
        except Exception as e:
            logger.error("LLM call failed at iteration %d: %s", i, e)
            break

        if not response:
            logger.warning("Empty LLM response at iteration %d", i)
            break

        # Check for final answer BEFORE executing code
        final_answer = find_final_answer(response, repl)
        if final_answer is not None:
            logger.info("RLM loop completed at iteration %d with FINAL", i + 1)
            break

        # Extract and execute code blocks
        code_blocks = find_code_blocks(response)
        results: list[REPLResult] = []

        for code in code_blocks:
            result = repl.execute_code(code)
            results.append(result)

            # Check if code execution produced a FINAL_VAR via print
            if "FINAL_VAR" in code:
                # Re-check for final answer after execution
                final_answer = find_final_answer(response, repl)
                if final_answer is not None:
                    break

        if final_answer is not None:
            logger.info("RLM loop completed at iteration %d with FINAL_VAR", i + 1)
            break

        # Format iteration and append to history
        new_messages = format_iteration(response, code_blocks, results)
        message_history.extend(new_messages)

    # If we ran out of iterations without a FINAL, use the last response
    if final_answer is None:
        logger.warning(
            "RLM loop exhausted %d iterations without FINAL, using last response",
            max_iterations,
        )
        # Make one more call asking for a final answer
        message_history.append({
            "role": "user",
            "content": (
                "You have used all your iterations. You MUST provide your final answer NOW "
                "using FINAL(...). Based on everything you've learned, provide your answer."
            ),
        })
        future = asyncio.run_coroutine_threadsafe(
            _llm_completion(message_history, llm_config, max_tokens=4096),
            loop,
        )
        try:
            response = future.result(timeout=120)
            final_answer = find_final_answer(response, repl) or response
        except Exception:
            final_answer = '{"reasoning": "RLM loop timed out", "actions": [], "goal_changes": []}'

    return {
        "final_answer": final_answer,
        "iterations": iteration_count,
        "message_count": len(message_history),
    }


# ---------------------------------------------------------------------------
# Heartbeat decision entry point
# ---------------------------------------------------------------------------

async def run_heartbeat_decision(
    *,
    heartbeat_id: str,
    turn_snapshot: dict[str, Any],
    llm_config: dict[str, Any],
    dsn: str,
    tool_registry: Any,
    loop: asyncio.AbstractEventLoop,
    max_iterations: int = 10,
    timeout_seconds: int = 300,
    workspace_budgets: WorkspaceBudgets | None = None,
) -> dict[str, Any]:
    """
    Run the RLM loop for a heartbeat decision.

    Returns a dict with keys: kind, decision, heartbeat_id, rlm_repl_actions,
    raw_response, metrics.
    """
    time_start = time.perf_counter()

    # Normalize LLM config
    llm_cfg = normalize_llm_config(llm_config)

    # Create memory repo (psycopg2 sync)
    repo = MemoryRepo(dsn)

    # Create workspace
    budgets = workspace_budgets or WorkspaceBudgets()
    workspace = RLMWorkspace(
        task="heartbeat_decision",
        turn_snapshot=turn_snapshot,
        budgets=budgets,
    )

    # Create sync LLM query function for sub-calls
    llm_query_fn = _make_sync_llm_query(llm_cfg, loop)

    # Create memory env
    memory_env = RLMMemoryEnv(repo, workspace, llm_query_fn=llm_query_fn)

    # Create tool bridge
    initial_energy = turn_snapshot.get("energy", {}).get("current", 20.0)
    bridge = ReplToolBridge(
        registry=tool_registry,
        loop=loop,
        heartbeat_id=heartbeat_id,
        initial_energy=initial_energy,
    )

    # Create REPL
    repl = HexisLocalREPL()
    repl.setup(
        context_payload=turn_snapshot,
        memory_env=memory_env,
        tool_bridge=bridge,
        llm_query_fn=llm_query_fn,
    )

    # Build system prompt
    system_prompt = load_rlm_heartbeat_prompt()
    personhood_addendum = compose_compact_personhood_prompt("heartbeat")
    if personhood_addendum:
        system_prompt = system_prompt + "\n\n---\n\n" + personhood_addendum

    # Run RLM loop in thread pool
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                _run_loop,
                repl,
                llm_cfg,
                loop,
                system_prompt,
                max_iterations,
            ),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        logger.error("RLM heartbeat timed out after %ds", timeout_seconds)
        result = {
            "final_answer": '{"reasoning": "RLM heartbeat timed out", "actions": [{"action": "rest", "params": {}}], "goal_changes": []}',
            "iterations": 0,
            "message_count": 0,
        }
    finally:
        repl.cleanup()
        repo.close()

    # Parse decision from final answer
    raw_answer = result["final_answer"]
    try:
        decision = json.loads(raw_answer)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Failed to parse RLM final answer as JSON, wrapping as reasoning")
        decision = {
            "reasoning": str(raw_answer)[:2000],
            "actions": [],
            "goal_changes": [],
        }

    # Collect REPL tool call records
    repl_actions = call_records_to_actions_taken(bridge.get_call_records())

    duration = time.perf_counter() - time_start

    logger.info(
        "rlm_heartbeat_complete heartbeat_id=%s iterations=%d "
        "search_count=%d fetch_count=%d tool_calls=%d "
        "tool_energy=%d duration=%.1fs",
        heartbeat_id,
        result["iterations"],
        workspace.metrics.search_count,
        workspace.metrics.fetch_count,
        len(bridge.get_call_records()),
        bridge.get_total_energy_spent(),
        duration,
    )

    return {
        "kind": "heartbeat_decision",
        "decision": decision,
        "heartbeat_id": heartbeat_id,
        "rlm_repl_actions": repl_actions,
        "raw_response": raw_answer[:5000],
        "metrics": {
            "iterations": result["iterations"],
            "message_count": result["message_count"],
            "search_count": workspace.metrics.search_count,
            "fetch_count": workspace.metrics.fetch_count,
            "fetched_chars_total": workspace.metrics.fetched_chars_total,
            "summarize_events": workspace.metrics.summarize_events,
            "tool_calls": len(bridge.get_call_records()),
            "tool_energy_spent": bridge.get_total_energy_spent(),
            "total_duration_seconds": round(duration, 2),
        },
    }


# ---------------------------------------------------------------------------
# Chat entry point
# ---------------------------------------------------------------------------

# Session management for persistent multi-turn chat
_chat_sessions: dict[str, HexisLocalREPL] = {}
_session_last_used: dict[str, float] = {}
_session_lock = asyncio.Lock()
_SESSION_TTL = 300  # 5 minutes


async def _cleanup_stale_sessions() -> None:
    """Remove chat sessions idle longer than TTL."""
    now = time.time()
    async with _session_lock:
        stale = [
            sid for sid, ts in _session_last_used.items()
            if now - ts > _SESSION_TTL
        ]
        for sid in stale:
            session = _chat_sessions.pop(sid, None)
            if session:
                session.cleanup()
            _session_last_used.pop(sid, None)


async def run_chat_turn(
    *,
    user_message: str,
    history: list[dict[str, str]] | None = None,
    llm_config: dict[str, Any],
    dsn: str,
    session_id: str | None = None,
    max_iterations: int = 15,
    timeout_seconds: int = 120,
    workspace_budgets: WorkspaceBudgets | None = None,
) -> dict[str, Any]:
    """
    Run the RLM loop for a chat turn.

    Returns a dict with keys: response, history, metrics.
    """
    await _cleanup_stale_sessions()

    time_start = time.perf_counter()
    llm_cfg = normalize_llm_config(llm_config)
    loop = asyncio.get_running_loop()

    # Create memory repo
    repo = MemoryRepo(dsn)
    budgets = workspace_budgets or WorkspaceBudgets()
    workspace = RLMWorkspace(task="chat", budgets=budgets)
    llm_query_fn = _make_sync_llm_query(llm_cfg, loop)
    memory_env = RLMMemoryEnv(repo, workspace, llm_query_fn=llm_query_fn)

    # Get or create REPL session
    repl: HexisLocalREPL
    async with _session_lock:
        if session_id and session_id in _chat_sessions:
            repl = _chat_sessions[session_id]
            # Add new user message as context
            repl.load_context(user_message, index=repl._context_count)
        else:
            repl = HexisLocalREPL()
            repl.setup(
                context_payload=user_message,
                memory_env=memory_env,
                llm_query_fn=llm_query_fn,
            )
            if session_id:
                _chat_sessions[session_id] = repl

        if session_id:
            _session_last_used[session_id] = time.time()

    # Build system prompt
    system_prompt = load_rlm_chat_prompt()
    personhood_addendum = compose_compact_personhood_prompt("conversation")
    if personhood_addendum:
        system_prompt = system_prompt + "\n\n---\n\n" + personhood_addendum

    # Run RLM loop
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                _run_loop,
                repl,
                llm_cfg,
                loop,
                system_prompt,
                max_iterations,
            ),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        logger.error("RLM chat timed out after %ds", timeout_seconds)
        result = {
            "final_answer": "I apologize, but I need more time to think about that. Could you try again?",
            "iterations": 0,
            "message_count": 0,
        }
    finally:
        # Don't cleanup persistent sessions; do cleanup repo
        if not session_id:
            repl.cleanup()
        repo.close()

    duration = time.perf_counter() - time_start

    logger.info(
        "rlm_chat_complete session=%s iterations=%d "
        "search_count=%d fetch_count=%d duration=%.1fs",
        session_id or "ephemeral",
        result["iterations"],
        workspace.metrics.search_count,
        workspace.metrics.fetch_count,
        duration,
    )

    return {
        "response": result["final_answer"],
        "metrics": {
            "iterations": result["iterations"],
            "search_count": workspace.metrics.search_count,
            "fetch_count": workspace.metrics.fetch_count,
            "total_duration_seconds": round(duration, 2),
        },
    }
