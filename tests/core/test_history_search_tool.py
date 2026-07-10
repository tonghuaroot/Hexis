"""Typed API and agent-tool contracts for cross-session lexical search."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from core.cognitive_memory_api import HistorySearchResult, MemoryType
from core.tools import ToolContext, ToolErrorType, ToolExecutionContext
from core.tools.memory import SearchHistoryHandler

pytestmark = [pytest.mark.asyncio(loop_scope="session")]


def _context(session_id=None):
    registry = MagicMock()
    registry.pool = object()
    return ToolExecutionContext(
        tool_context=ToolContext.CHAT,
        call_id="history-search-test",
        session_id=str(session_id) if session_id else None,
        registry=registry,
    )


async def test_search_history_tool_maps_results_and_excludes_current_uuid_session():
    session_id = uuid4()
    result_id = uuid4()
    result = HistorySearchResult(
        source_kind="memory",
        item_id=result_id,
        session_id=uuid4(),
        content="A prior durable detail",
        user_text=None,
        assistant_text=None,
        memory_type=MemoryType.SEMANTIC,
        occurred_at=datetime.now(timezone.utc),
        rank=0.75,
        source_unit_ids=[uuid4()],
        source_attribution={"kind": "conversation"},
        metadata={"topic": "continuity"},
    )

    with patch(
        "core.cognitive_memory_api.CognitiveMemory.search_history",
        new_callable=AsyncMock,
        return_value=[result],
    ) as search:
        response = await SearchHistoryHandler().execute(
            {
                "query": '"durable detail"',
                "sources": ["memory"],
                "created_after": "2026-07-01",
            },
            _context(session_id),
        )

    assert response.success is True
    assert response.output["count"] == 1
    assert response.output["results"][0]["item_id"] == str(result_id)
    assert response.output["results"][0]["memory_type"] == "semantic"
    assert response.output["excluded_session_id"] == str(session_id)
    kwargs = search.await_args.kwargs
    assert kwargs["exclude_session_id"] == str(session_id)
    assert kwargs["created_after"].tzinfo is timezone.utc
    assert kwargs["sources"] == ["memory"]


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"query": ""}, "must not be empty"),
        (
            {
                "query": "detail",
                "created_after": "2026-08-01T00:00:00Z",
                "created_before": "2026-07-01T00:00:00Z",
            },
            "must be earlier",
        ),
        ({"query": "detail", "created_after": "not-a-date"}, "must be an ISO-8601"),
    ],
)
async def test_search_history_tool_rejects_invalid_requests(arguments, message):
    response = await SearchHistoryHandler().execute(arguments, _context())

    assert response.success is False
    assert response.error_type is ToolErrorType.INVALID_PARAMS
    assert message in (response.error or "")


async def test_core_memory_skill_exposes_history_search(db_pool):
    from core.tools import create_default_registry
    from services.skill_runtime import select_skills

    registry = create_default_registry(db_pool)
    selection = await select_skills(
        registry,
        ToolContext.CHAT,
        query="What did we decide in an earlier conversation?",
    )

    assert "search_history" in registry.list_names()
    assert "search_history" in selection.allowed_tool_names
    core_memory = next(
        skill for skill in selection.skills if skill.name == "core-memory"
    )
    assert "search_history" in core_memory.bound_tools
