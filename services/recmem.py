"""RecMem nearline and consolidation workers."""

from __future__ import annotations

import json
import logging
from typing import Any

from core.llm_config import load_llm_config
from core.llm_json import chat_json
from services.prompt_resources import (
    load_recmem_episode_create_prompt,
    load_recmem_episode_merge_prompt,
    load_recmem_semantic_refine_prompt,
)

logger = logging.getLogger("recmem")


def _coerce_json(val: Any) -> Any:
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return val
    return val


def _as_list(val: Any) -> list[Any]:
    if isinstance(val, list):
        return val
    return []


async def run_recmem_embed_step(conn) -> dict[str, Any]:
    """Claim and embed one batch of raw units."""
    batch_size = await conn.fetchval("SELECT COALESCE(get_config_int('memory.recmem_embed_batch_size'), 32)")
    raw = await conn.fetchval("SELECT claim_recmem_unembedded_batch($1::int)", int(batch_size or 32))
    items = _as_list(_coerce_json(raw))
    if not items:
        return {"skipped": True, "reason": "no_unembedded_units"}

    embedded = 0
    failed = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        unit_id = item.get("unit_id")
        content = item.get("content") or ""
        try:
            await conn.execute(
                """
                UPDATE subconscious_units
                SET embedding = (get_embedding(ARRAY[$2::text]))[1],
                    embedded_at = CURRENT_TIMESTAMP,
                    embedding_status = 'embedded',
                    embedding_claimed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = $1::uuid
                  AND embedding_status = 'in_progress'
                """,
                str(unit_id),
                content,
            )
            embedded += 1
        except Exception as exc:
            failed += 1
            await conn.fetchval("SELECT fail_recmem_embedding($1::uuid, $2::text)", str(unit_id), str(exc))

    return {"claimed": len(items), "embedded": embedded, "failed": failed}


async def run_recmem_route_step(conn) -> dict[str, Any]:
    """Claim and route one batch of embedded raw units."""
    batch_size = await conn.fetchval("SELECT COALESCE(get_config_int('memory.recmem_route_batch_size'), 32)")
    raw = await conn.fetchval("SELECT claim_recmem_unrouted_batch($1::int)", int(batch_size or 32))
    items = _as_list(_coerce_json(raw))
    if not items:
        return {"skipped": True, "reason": "no_unrouted_units"}

    outcomes: dict[str, int] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        unit_id = item.get("unit_id")
        try:
            result_raw = await conn.fetchval("SELECT recmem_route_unit($1::uuid)", str(unit_id))
            result = _coerce_json(result_raw)
            status = result.get("status", "unknown") if isinstance(result, dict) else "unknown"
            outcomes[status] = outcomes.get(status, 0) + 1
        except Exception as exc:
            await conn.fetchval("SELECT fail_recmem_routing($1::uuid, $2::text)", str(unit_id), str(exc))
            outcomes["failed"] = outcomes.get("failed", 0) + 1

    return {"claimed": len(items), "outcomes": outcomes}


async def run_recmem_sweep_step(conn) -> dict[str, Any]:
    raw = await conn.fetchval("SELECT recmem_periodic_sweep()")
    result = _coerce_json(raw)
    return dict(result) if isinstance(result, dict) else {}


async def _load_task_context(conn, task: dict[str, Any]) -> dict[str, Any]:
    source_ids = [str(v) for v in _as_list(task.get("source_unit_ids"))]
    sources = await conn.fetch(
        """
        SELECT id, content, user_text, assistant_text, turn_at
        FROM subconscious_units
        WHERE id = ANY($1::uuid[])
        ORDER BY turn_at, created_at
        """,
        source_ids,
    )
    target = None
    if task.get("target_memory_id"):
        target = await conn.fetchrow(
            "SELECT id, content, type::text, trust_level FROM memories WHERE id = $1::uuid",
            str(task["target_memory_id"]),
        )
    return {
        "task": task,
        "sources": [dict(row) for row in sources],
        "target_memory": dict(target) if target else None,
    }


def _normalize_episodes(doc: Any) -> list[dict[str, Any]]:
    if isinstance(doc, dict):
        raw = doc.get("episodes", [])
    else:
        raw = doc
    episodes: list[dict[str, Any]] = []
    for item in _as_list(raw):
        if isinstance(item, str):
            episodes.append({"content": item})
        elif isinstance(item, dict) and (item.get("content") or item.get("episode")):
            episodes.append(item)
    return episodes


def _normalize_facts(doc: Any) -> list[dict[str, Any]]:
    if isinstance(doc, dict):
        raw = doc.get("facts", [])
    else:
        raw = doc
    facts: list[dict[str, Any]] = []
    for item in _as_list(raw):
        if isinstance(item, str):
            facts.append({"content": item})
        elif isinstance(item, dict) and (item.get("content") or item.get("fact")):
            facts.append(item)
    return facts


async def _handle_episode_merge(conn, task: dict[str, Any], llm_config: dict[str, Any]) -> dict[str, Any]:
    context = await _load_task_context(conn, task)
    doc, _raw = await chat_json(
        llm_config=llm_config,
        messages=[
            {"role": "system", "content": load_recmem_episode_merge_prompt().strip()},
            {"role": "user", "content": json.dumps(context, default=str)[:20000]},
        ],
        max_tokens=1800,
        temperature=0.1,
        response_format={"type": "json_object"},
        fallback={"should_merge": False},
    )
    if not isinstance(doc, dict):
        doc = {"should_merge": False}
    result_raw = await conn.fetchval(
        "SELECT apply_recmem_episode_merge($1::uuid, $2::text, $3::boolean)",
        str(task["id"]),
        doc.get("content"),
        bool(doc.get("should_merge", False)),
    )
    result = _coerce_json(result_raw)
    return dict(result) if isinstance(result, dict) else {}


async def _handle_episode_create(conn, task: dict[str, Any], llm_config: dict[str, Any]) -> dict[str, Any]:
    context = await _load_task_context(conn, task)
    doc, _raw = await chat_json(
        llm_config=llm_config,
        messages=[
            {"role": "system", "content": load_recmem_episode_create_prompt().strip()},
            {"role": "user", "content": json.dumps(context, default=str)[:24000]},
        ],
        max_tokens=2200,
        temperature=0.1,
        response_format={"type": "json_object"},
        fallback={"episodes": []},
    )
    episodes = _normalize_episodes(doc)
    result_raw = await conn.fetchval(
        "SELECT apply_recmem_episode_create($1::uuid, $2::jsonb)",
        str(task["id"]),
        json.dumps(episodes),
    )
    result = _coerce_json(result_raw)
    return dict(result) if isinstance(result, dict) else {}


async def _handle_semantic_refine(conn, task: dict[str, Any], llm_config: dict[str, Any]) -> dict[str, Any]:
    context = await _load_task_context(conn, task)
    doc, _raw = await chat_json(
        llm_config=llm_config,
        messages=[
            {"role": "system", "content": load_recmem_semantic_refine_prompt().strip()},
            {"role": "user", "content": json.dumps(context, default=str)[:24000]},
        ],
        max_tokens=1800,
        temperature=0.1,
        response_format={"type": "json_object"},
        fallback={"facts": []},
    )
    facts = _normalize_facts(doc)
    result_raw = await conn.fetchval(
        "SELECT apply_recmem_semantic_facts($1::uuid, $2::jsonb)",
        str(task["id"]),
        json.dumps(facts),
    )
    result = _coerce_json(result_raw)
    return dict(result) if isinstance(result, dict) else {}


async def run_recmem_consolidation_step(conn) -> dict[str, Any]:
    raw = await conn.fetchval("SELECT claim_recmem_consolidation_task()")
    task = _coerce_json(raw)
    if not isinstance(task, dict) or not task.get("id"):
        return {"skipped": True, "reason": "no_pending_tasks"}

    try:
        llm_config = await load_llm_config(conn, "llm.recmem", fallback_key="llm.subconscious")
        task_type = task.get("task_type")
        if task_type == "episode_merge":
            result = await _handle_episode_merge(conn, task, llm_config)
        elif task_type == "episode_create":
            result = await _handle_episode_create(conn, task, llm_config)
        elif task_type == "semantic_refine":
            result = await _handle_semantic_refine(conn, task, llm_config)
        else:
            raise ValueError(f"unknown RecMem task type: {task_type}")
        logger.info("RecMem task completed: %s", result)
        return result
    except Exception as exc:
        logger.error("RecMem task failed: task=%s error=%s", task.get("id"), exc)
        await conn.fetchval("SELECT fail_recmem_consolidation_task($1::uuid, $2::text)", str(task["id"]), str(exc))
        return {"error": str(exc), "task_id": str(task["id"])}
