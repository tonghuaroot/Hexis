from __future__ import annotations

import asyncio
import logging
import hashlib
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from core.agent_api import db_dsn_from_env, get_agent_profile_context, pool_sizes_from_env
from core.agent_loop import AgentEvent
from core.cognitive_memory_api import CognitiveMemory, MemoryType
from core.llm import normalize_llm_config
from core.tools import create_default_registry, ToolContext, ToolExecutionContext, ToolRegistry
from services.agent import run_agent, stream_agent

logger = logging.getLogger(__name__)


async def _log_dual_write_comparison(
    mem_client: CognitiveMemory,
    *,
    query: str,
    session_id: str | None,
) -> None:
    try:
        eager = await mem_client.recall(query, limit=10, include_partial=False)
        recmem = await mem_client.hydrate_recmem(query, session_id=session_id)
        logger.info(
            "RecMem dual-write comparison: session=%s eager=%s recmem=%s",
            session_id,
            [str(m.id) for m in eager.memories],
            [str(m.id) for m in recmem],
        )
    except Exception:
        logger.debug("RecMem dual-write comparison failed", exc_info=True)


def _schedule_dual_write_comparison(
    mem_client: CognitiveMemory,
    *,
    query: str,
    session_id: str | None,
) -> None:
    try:
        task = asyncio.create_task(_log_dual_write_comparison(mem_client, query=query, session_id=session_id))
    except RuntimeError:
        return

    def _consume_exception(done: asyncio.Task[None]) -> None:
        try:
            done.exception()
        except asyncio.CancelledError:
            pass

    task.add_done_callback(_consume_exception)


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


def _estimate_importance(user_message: str, assistant_message: str) -> float:
    importance = 0.5
    combined = (user_message + "\n" + assistant_message).lower()
    learning_signals = [
        "remember",
        "don't forget",
        "important",
        "note that",
        "my name is",
        "i prefer",
        "i like",
        "i don't like",
        "always",
        "never",
        "make sure",
        "keep in mind",
    ]
    if len(user_message) > 200 or len(assistant_message) > 500:
        importance = max(importance, 0.7)
    if any(signal in combined for signal in learning_signals):
        importance = max(importance, 0.8)
    return max(0.15, min(float(importance), 1.0))


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


async def _remember_conversation(
    mem_client: CognitiveMemory,
    *,
    user_message: str,
    assistant_message: str,
    session_id: str | None = None,
    source_identity: str | None = None,
) -> None:
    if not user_message and not assistant_message:
        return
    content = f"User: {user_message}\n\nAssistant: {assistant_message}"
    importance = _estimate_importance(user_message, assistant_message)
    source_attribution = {
        "kind": "conversation",
        "ref": "conversation_turn",
        "label": "conversation turn",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "trust": 0.95,
    }
    recmem_enabled = False
    eager_enabled = True
    salience_promote = True
    dual_compare = False
    try:
        async with mem_client._pool.acquire() as conn:
            recmem_enabled = bool(await conn.fetchval("SELECT COALESCE(get_config_bool('memory.recmem_enabled'), false)"))
            eager_enabled = bool(await conn.fetchval("SELECT COALESCE(get_config_bool('chat.eager_memory_enabled'), true)"))
            salience_promote = bool(await conn.fetchval("SELECT COALESCE(get_config_bool('chat.recmem_salience_direct_promote'), true)"))
            dual_compare = bool(await conn.fetchval("SELECT COALESCE(get_config_bool('memory.recmem_dual_write_compare'), false)"))
    except Exception:
        recmem_enabled = False
        eager_enabled = True

    raw: dict[str, Any] | None = None
    if recmem_enabled:
        raw = await mem_client.remember_turn_raw(
            user_message,
            assistant_message,
            session_id=session_id,
            source_identity=source_identity,
            importance=importance,
            source_attribution=source_attribution,
            metadata={"type": "conversation"},
        )

    promoted = False
    if recmem_enabled and salience_promote and importance >= 0.8:
        mem_id = await mem_client.remember(
            content,
            type=MemoryType.EPISODIC,
            importance=importance,
            emotional_valence=0.0,
            context={"type": "conversation", "recmem": {"direct_promoted": True}},
            source_attribution=source_attribution,
            source_references=None,
            trust_level=0.95,
        )
        promoted = True
        if raw and raw.get("unit_id"):
            await mem_client.link_to_source_unit(mem_id, raw["unit_id"], role="direct_promotion")

    if eager_enabled and not promoted:
        await mem_client.remember(
            content,
            type=MemoryType.EPISODIC,
            importance=importance,
            emotional_valence=0.0,
            context={"type": "conversation"},
            source_attribution=source_attribution,
            source_references=None,
            trust_level=0.95,
        )

    if recmem_enabled and eager_enabled and dual_compare:
        _schedule_dual_write_comparison(mem_client, query=user_message, session_id=session_id)


def _conversation_source_identity(session_id: str | None, history: list[dict[str, Any]] | None, user_message: str, assistant_message: str) -> str | None:
    if not session_id:
        return None
    digest = hashlib.sha256(f"{user_message}\x1e{assistant_message}".encode("utf-8")).hexdigest()[:16]
    turn_index = len(history or [])
    return f"chat:{session_id}:{turn_index}:{digest}"


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
    is_group: bool = False,
) -> dict[str, Any]:
    dsn = dsn or db_dsn_from_env()
    normalized = normalize_llm_config(llm_config)
    history = history or []

    # Check if RLM is enabled for chat
    use_rlm = False
    try:
        if pool is not None:
            async with pool.acquire() as _conn:
                use_rlm_raw = await _conn.fetchval("SELECT get_config_bool('chat.use_rlm')")
                use_rlm = bool(use_rlm_raw)
        else:
            import asyncpg
            _conn = await asyncpg.connect(dsn)
            try:
                use_rlm_raw = await _conn.fetchval("SELECT get_config_bool('chat.use_rlm')")
                use_rlm = bool(use_rlm_raw)
            finally:
                await _conn.close()
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
        # Still form memory from the turn
        if pool is not None:
            mem_client = CognitiveMemory(pool)
            await _remember_conversation(
                mem_client,
                user_message=user_message,
                assistant_message=assistant_text,
                session_id=session_id,
                source_identity=_conversation_source_identity(session_id, history, user_message, assistant_text),
            )
        else:
            async with CognitiveMemory.connect(dsn) as mem_client:
                await _remember_conversation(
                    mem_client,
                    user_message=user_message,
                    assistant_message=assistant_text,
                    session_id=session_id,
                    source_identity=_conversation_source_identity(session_id, history, user_message, assistant_text),
                )
        new_history = list(history)
        new_history.append({"role": "user", "content": user_message})
        new_history.append({"role": "assistant", "content": assistant_text})
        return {"assistant": assistant_text, "history": new_history}

    # Create or use provided pool for tool registry
    import asyncpg

    own_pool = pool is None
    if own_pool:
        _min, _max = pool_sizes_from_env(1, 3)
        pool = await asyncpg.create_pool(dsn, min_size=_min, max_size=_max)

    try:
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

        async with CognitiveMemory.connect(dsn) as mem_client:
            await _remember_conversation(
                mem_client,
                user_message=user_message,
                assistant_message=assistant_text,
                session_id=session_id,
                source_identity=_conversation_source_identity(session_id, history, user_message, assistant_text),
            )

        new_history = list(history)
        new_history.append({"role": "user", "content": user_message})
        new_history.append({"role": "assistant", "content": assistant_text})
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
    is_group: bool = False,
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
        registry = create_default_registry(pool)
        agent_profile = await get_agent_profile_context(pool=pool)

        collected: list[str] = []
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

        full_text = "".join(collected)
        if full_text:
            async with CognitiveMemory.connect(dsn) as mem_client:
                await _remember_conversation(
                    mem_client,
                    user_message=user_message,
                    assistant_message=full_text,
                    session_id=session_id,
                    source_identity=_conversation_source_identity(session_id, history, user_message, full_text),
                )
    finally:
        if own_pool:
            await pool.close()


def chat_turn_sync(**kwargs: Any) -> dict[str, Any]:
    from core.sync_utils import run_sync

    return run_sync(chat_turn(**kwargs))
