from __future__ import annotations

import pytest

from core.tools import ToolContext, ToolExecutionContext, create_default_registry
from core.tools.self_inspection import InspectSourceHandler
from services.skill_runtime import select_skills


pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_source_inspection_searches_repository_and_blocks_traversal():
    handler = InspectSourceHandler()
    context = ToolExecutionContext(tool_context=ToolContext.CHAT, call_id="source-test")

    result = await handler.execute(
        {
            "action": "search",
            "path": "core/tools",
            "file_pattern": "*.py",
            "query": "create_self_inspection_tools",
            "limit": 10,
        },
        context,
    )
    assert result.success
    assert any(
        match["path"] == "core/tools/self_inspection.py"
        for match in result.output["matches"]
    )

    denied = await handler.execute(
        {"action": "read", "path": "../.env"}, context
    )
    assert not denied.success
    assert denied.error_type.value == "path_not_allowed"


async def test_live_schema_inspection_describes_memory_invariants(db_pool):
    registry = create_default_registry(db_pool)
    context = ToolExecutionContext(
        tool_context=ToolContext.CHAT,
        call_id="schema-test",
        registry=registry,
    )
    result = await registry.execute(
        "inspect_database_schema",
        {"action": "describe_relation", "schema": "public", "relation": "memories"},
        context,
    )

    assert result.success, result.error
    assert any(column["name"] == "importance" for column in result.output["columns"])
    assert any(
        constraint["name"] == "memories_importance_range"
        for constraint in result.output["constraints"]
    )


async def test_self_inspection_skill_activates_shared_read_only_tools(db_pool):
    registry = create_default_registry(db_pool)
    selection = await select_skills(
        registry,
        ToolContext.CHAT,
        query="Browse your Hexis source code and inspect the database schema",
    )

    assert "self-inspection" in {skill.name for skill in selection.skills}
    assert {"inspect_source", "inspect_database_schema"}.issubset(
        selection.allowed_tool_names
    )


async def test_read_result_carries_retention_hint():
    """Read results remind the agent that inspection is not retention (#32).
    Without a pool the hint defaults on (advisory, fail-open)."""
    handler = InspectSourceHandler()
    context = ToolExecutionContext(tool_context=ToolContext.CHAT, call_id="retention-test")

    result = await handler.execute(
        {"action": "read", "path": "README.md", "limit": 5}, context
    )
    assert result.success
    assert "in-context only" in result.output["retention"]

    listing = await handler.execute(
        {"action": "list", "path": "core/tools", "file_pattern": "*.py"}, context
    )
    assert listing.success
    assert "retention" not in listing.output


async def test_retention_hint_config_gate(db_pool):
    registry = create_default_registry(db_pool)
    context = ToolExecutionContext(
        tool_context=ToolContext.CHAT, call_id="retention-gate", registry=registry
    )
    handler = InspectSourceHandler()
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE config SET value = 'false'::jsonb WHERE key = 'inspection.retention_hint_enabled'"
        )
    try:
        result = await handler.execute(
            {"action": "read", "path": "README.md", "limit": 5}, context
        )
        assert result.success
        assert "retention" not in result.output
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE config SET value = 'true'::jsonb WHERE key = 'inspection.retention_hint_enabled'"
            )
