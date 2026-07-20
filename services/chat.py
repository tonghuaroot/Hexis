from __future__ import annotations

import logging
import json
from typing import Any, AsyncIterator
from uuid import UUID

from core.agent_api import db_dsn_from_env, get_agent_profile_context, pool_sizes_from_env
from core.agent_loop import AgentEvent
from core.cognitive_memory_api import CognitiveMemory, MemoryType
from core.llm import normalize_llm_config
from core.tools import create_default_registry, ToolContext, ToolExecutionContext, ToolRegistry
from services.agent import run_agent, stream_agent

logger = logging.getLogger(__name__)


async def _build_system_prompt(
    agent_profile: dict[str, Any],
    registry: ToolRegistry | None = None,
    *,
    is_group: bool = False,
) -> str:
    from services.agent import build_system_prompt
    return await build_system_prompt(
        "chat", registry, agent_profile, is_group=is_group,
    )


def _extract_allowed_tools(raw_tools: Any) -> list[str] | None:
    if raw_tools is None:
        return None
    if not isinstance(raw_tools, list):
        return None
    names: list[str] = []
    for item in raw_tools:
        if isinstance(item, str):
            name = item.strip()
            if name:
                names.append(name)
        elif isinstance(item, dict):
            name = item.get("name") or item.get("tool")
            enabled = item.get("enabled", True)
            if isinstance(name, str) and name.strip() and enabled is not False:
                names.append(name.strip())
    return names


def _uuid_text_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return str(UUID(str(value)))
    except Exception:
        return None


def _message_history_only(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role in {"system", "user", "assistant"} and isinstance(content, str):
            normalized.append({"role": role, "content": content})
    return normalized


async def _hydrate_chat_history(
    pool: Any,
    session_id: str | None,
    fallback_history: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    parsed = _uuid_text_or_none(session_id)
    if not parsed:
        return _message_history_only(fallback_history or [])
    try:
        async with pool.acquire() as conn:
            raw = await conn.fetchval("SELECT hydrate_chat_session($1::uuid)", parsed)
        payload = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(payload, dict) and isinstance(payload.get("messages"), list) and payload["messages"]:
            return _message_history_only(payload["messages"])
    except Exception:
        logger.debug("DB chat-session hydration failed; falling back to caller history", exc_info=True)
    return _message_history_only(fallback_history or [])


async def _remember_conversation(
    mem_client: CognitiveMemory,
    *,
    user_message: str,
    assistant_message: str,
    session_id: str | None = None,
    source_identity: str | None = None,
    user_label: str | None = None,
    background_dsn: str | None = None,
    emotional_state: dict[str, Any] | None = None,
    surface: str = "chat",
) -> dict[str, Any]:
    if not user_message and not assistant_message:
        return {}
    context: dict[str, Any] = {"metadata": {"type": "conversation"}}
    if user_label and user_label.strip():
        context["user_label"] = user_label.strip()
    # This turn's appraisal, so the stored turn carries the moment's feeling
    # (#81); the DB snapshots current state when the appraisal is absent.
    if emotional_state:
        context["emotional_state"] = emotional_state
    context["surface"] = surface
    if source_identity:
        context["source_identity"] = source_identity
    parsed_session = _uuid_text_or_none(session_id)
    if parsed_session:
        return await mem_client.record_chat_session_turn(
            user_message,
            assistant_message,
            session_id=parsed_session,
            surface=surface,
            context=context,
        )
    return await mem_client.record_chat_turn_memory(
        user_message,
        assistant_message,
        session_id=session_id,
        source_identity=source_identity,
        context=context,
    )


async def _hydrate_after_persist(
    mem_client: CognitiveMemory,
    session_id: str | None,
    fallback_history: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    parsed = _uuid_text_or_none(session_id)
    if not parsed:
        return _message_history_only(fallback_history)
    try:
        messages = await mem_client.hydrate_chat_session(parsed)
        if messages:
            return _message_history_only(messages)
    except Exception:
        logger.debug("Post-write chat-session hydration failed", exc_info=True)
    return _message_history_only(fallback_history)


async def _build_execution_context(
    registry: ToolRegistry,
    call_id: str,
    session_id: str | None = None,
) -> ToolExecutionContext:
    """Build a ToolExecutionContext with config overrides for chat."""
    ctx = ToolExecutionContext(
        tool_context=ToolContext.CHAT,
        call_id=call_id,
        session_id=session_id,
        allow_network=True,
        allow_shell=False,
        allow_file_write=False,
        allow_file_read=True,
    )
    try:
        config = await registry.get_config()
        overrides = config.get_context_overrides(ToolContext.CHAT)
        ctx.allow_shell = overrides.allow_shell
        ctx.allow_file_write = overrides.allow_file_write
        if config.workspace_path:
            ctx.workspace_path = config.workspace_path
    except Exception:
        pass  # Use defaults
    return ctx


async def chat_turn(
    *,
    user_message: str,
    history: list[dict[str, Any]] | None = None,
    llm_config: dict[str, Any],
    dsn: str | None = None,
    memory_limit: int = 10,
    max_tool_iterations: int = 5,
    session_id: str | None = None,
    pool: Any | None = None,
    user_label: str | None = None,
    is_group: bool = False,
    surface: str = "chat",
) -> dict[str, Any]:
    dsn = dsn or db_dsn_from_env()
    normalized = normalize_llm_config(llm_config)
    history = history or []
    import asyncpg

    # Create or use provided pool before any chat-state decisions: DB session
    # history is the source of truth when present.
    own_pool = pool is None
    if own_pool:
        _min, _max = pool_sizes_from_env(1, 3)
        pool = await asyncpg.create_pool(dsn, min_size=_min, max_size=_max)

    try:
        history = await _hydrate_chat_history(pool, session_id, history)

        # Check if RLM is enabled for chat
        use_rlm = False
        try:
            async with pool.acquire() as _conn:
                use_rlm_raw = await _conn.fetchval("SELECT get_config_bool('chat.use_rlm')")
                use_rlm = bool(use_rlm_raw)
        except Exception:
            use_rlm = False

        if use_rlm:
            from services.hexis_rlm import run_chat_turn
            result = await run_chat_turn(
                user_message=user_message,
                history=history,
                llm_config=normalized,
                dsn=dsn,
                session_id=session_id,
            )
            assistant_text = result["response"]
            fallback_history = [
                *history,
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": assistant_text},
            ]
            mem_client = CognitiveMemory(pool)
            await _remember_conversation(
                mem_client,
                user_message=user_message,
                assistant_message=assistant_text,
                session_id=session_id,
                user_label=user_label,
                background_dsn=dsn,
                surface=surface,
            )
            new_history = await _hydrate_after_persist(mem_client, session_id, fallback_history)
            return {"assistant": assistant_text, "history": new_history}

        registry = create_default_registry(pool)
        agent_profile = await get_agent_profile_context(pool=pool)

        loop_result = await run_agent(
            pool,
            registry,
            user_message=user_message,
            mode="chat",
            history=history,
            session_id=session_id,
            agent_profile=agent_profile,
            is_group=is_group,
            dsn=dsn,
            max_iterations=max_tool_iterations,
        )
        assistant_text = loop_result.text

        fallback_history = [
            *history,
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": assistant_text},
        ]
        mem_client = CognitiveMemory(pool)
        await _remember_conversation(
            mem_client,
            user_message=user_message,
            assistant_message=assistant_text,
            session_id=session_id,
            user_label=user_label,
            background_dsn=dsn,
            surface=surface,
        )
        new_history = await _hydrate_after_persist(mem_client, session_id, fallback_history)
        return {"assistant": assistant_text, "history": new_history}
    finally:
        if own_pool:
            await pool.close()


async def stream_chat_turn(
    *,
    user_message: str,
    history: list[dict[str, Any]] | None = None,
    llm_config: dict[str, Any],
    dsn: str | None = None,
    memory_limit: int = 10,
    max_tool_iterations: int = 5,
    session_id: str | None = None,
    pool: Any | None = None,
    user_label: str | None = None,
    is_group: bool = False,
    surface: str = "chat",
) -> AsyncIterator[str]:
    """
    Streaming variant of chat_turn().

    Yields text chunks as they arrive from the unified agent runner. The
    caller receives the same enriched conversation flow (hydrate +
    subconscious + tools + memory formation) — just delivered as a stream.
    """
    dsn = dsn or db_dsn_from_env()
    history = history or []

    import asyncpg

    own_pool = pool is None
    if own_pool:
        _min, _max = pool_sizes_from_env(1, 3)
        pool = await asyncpg.create_pool(dsn, min_size=_min, max_size=_max)

    try:
        history = await _hydrate_chat_history(pool, session_id, history)
        registry = create_default_registry(pool)
        agent_profile = await get_agent_profile_context(pool=pool)

        collected: list[str] = []
        timed_out = False
        error_message: str | None = None
        async for event in stream_agent(
            pool,
            registry,
            user_message=user_message,
            mode="chat",
            history=history,
            session_id=session_id,
            agent_profile=agent_profile,
            is_group=is_group,
            dsn=dsn,
        ):
            if event.event == AgentEvent.TEXT_DELTA:
                text = event.data.get("text", "")
                if text:
                    collected.append(text)
                    yield text
            elif event.event == AgentEvent.LOOP_END:
                stopped = str(event.data.get("stopped_reason") or "")
                timed_out = stopped == "timeout" or bool(event.data.get("timed_out"))
            elif event.event == AgentEvent.ERROR:
                error_message = str(event.data.get("error") or "Unknown agent error")

        full_text = "".join(collected)
        if timed_out:
            if full_text:
                yield "\n\n[Response timed out before completion.]"
            else:
                yield (
                    "Request timed out before a response arrived. Try again, "
                    "or run `hexis doctor --llm` if it keeps happening."
                )
            return
        if not full_text and error_message:
            yield f"Request failed: {error_message}"
            return
        if full_text:
            await _remember_conversation(
                CognitiveMemory(pool),
                user_message=user_message,
                assistant_message=full_text,
                session_id=session_id,
                user_label=user_label,
                background_dsn=dsn,
                surface=surface,
            )
    finally:
        if own_pool:
            await pool.close()


def chat_turn_sync(**kwargs: Any) -> dict[str, Any]:
    from core.sync_utils import run_sync

    return run_sync(chat_turn(**kwargs))
