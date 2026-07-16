"""Memory tool behavior through the DB dispatcher.

execute_memory_tool (db/38) owns retrieval policy: plain-query recalls route
to the hybrid retriever (and label rows with retrieval_source), filtered
recalls route to recall_memories_structured, and source_attribution is
flattened into source_* keys. The former Python fallback was deleted.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from core.tools.base import ToolContext, ToolExecutionContext
from core.tools.memory import RecallHandler, RememberHandler
from tests.utils import get_test_identifier

pytestmark = [pytest.mark.asyncio(loop_scope="session")]


def _ctx(db_pool) -> ToolExecutionContext:
    registry = MagicMock()
    registry.pool = db_pool
    return ToolExecutionContext(
        tool_context=ToolContext.CHAT,
        call_id="test-call",
        registry=registry,
    )


async def _cleanup(db_pool, marker: str) -> None:
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM memories WHERE content LIKE $1", f"%{marker}%")


class TestRecallThroughDbDispatcher:
    async def test_query_only_uses_hybrid(self, db_pool, ensure_embedding_service):
        marker = get_test_identifier("hybridrecall")
        try:
            remembered = await RememberHandler().execute(
                {"content": f"The zephyr protocol codename is {marker}", "type": "semantic"},
                _ctx(db_pool),
            )
            assert remembered.success, remembered.error

            result = await RecallHandler().execute(
                {"query": f"zephyr protocol codename {marker}", "limit": 5},
                _ctx(db_pool),
            )
            assert result.success, result.error
            hits = [m for m in result.output["memories"] if marker in m["content"]]
            assert hits, result.output
            # The plain-query path is the hybrid retriever, which labels rows.
            assert hits[0].get("retrieval_source"), hits[0]
        finally:
            await _cleanup(db_pool, marker)

    async def test_structured_filters_use_structured_query(self, db_pool, ensure_embedding_service):
        marker = get_test_identifier("structuredrecall")
        kind = f"web_{marker}"
        try:
            async with db_pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO memories (type, content, embedding, importance, trust_level, status, source_attribution)
                    VALUES ('semantic', $1, array_fill(0.1, ARRAY[embedding_dimension()])::vector,
                            0.8, 0.9, 'active', $2::jsonb)
                    """,
                    f"Structured recall subject {marker}",
                    json.dumps({"kind": kind, "label": "Doc"}),
                )

            result = await RecallHandler().execute({"source_kind": kind}, _ctx(db_pool))
            assert result.success, result.error
            assert result.output["count"] == 1, result.output
            memory = result.output["memories"][0]
            # Filtered recalls take the structured path: no retrieval_source,
            # and source_attribution is flattened into source_* keys.
            assert "retrieval_source" not in memory
            assert memory["source_kind"] == kind
            assert memory["source_label"] == "Doc"
        finally:
            await _cleanup(db_pool, marker)

    async def test_recall_requires_query_or_filter(self, db_pool):
        result = await RecallHandler().execute({}, _ctx(db_pool))
        assert not result.success
        assert "query or one filter" in (result.error or "")


class TestProvenanceTooling:
    def test_remember_schema_supports_sources_and_confidence(self):
        spec = RememberHandler().spec
        props = spec.parameters["properties"]
        assert "confidence" in props
        assert props["sources"]["type"] == "array"
        source_props = props["sources"]["items"]["properties"]
        assert {"kind", "ref", "label", "author", "trust"} <= set(source_props)

    def test_add_evidence_handler_registered(self):
        from core.tools.memory import AddEvidenceHandler, create_memory_tools

        names = [handler.spec.name for handler in create_memory_tools()]
        assert "add_evidence" in names
        spec = AddEvidenceHandler().spec
        assert spec.parameters["required"] == ["memory_id", "stance", "source"]
        assert spec.is_read_only is False
        assert "prior" in spec.description
