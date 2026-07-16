"""Slow-ingest corroboration (#34): near-duplicate facts must become evidence
on the matched memory (add_evidence with the encounter as evidence node), not
silently dropped or re-created; failures must be logged, never swallowed.
"""
from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.slow_ingest_rlm import run_slow_ingest

pytestmark = [pytest.mark.asyncio(loop_scope="session")]


def _assessment(facts: list[str]) -> dict[str, Any]:
    return {
        "assessment": {
            "acceptance": "accept",
            "trust_assessment": 0.7,
            "importance": 0.6,
            "analysis": "test analysis",
            "extracted_facts": facts,
            "connections": [],
            "rejection_reasons": [],
            "worldview_impact": "neutral",
            "emotional_reaction": {},
        }
    }


def _make_pipeline(plan: list[dict[str, Any]]) -> MagicMock:
    pipeline = MagicMock()
    pipeline._skip_section.return_value = False
    pipeline._source_payload.return_value = {"kind": "document", "ref": "test-doc.md"}
    pipeline._create_encounter_memory.return_value = "encounter-1"
    store = pipeline.store
    store.client = object()  # already "connected"
    store.fetch_appraisal_context.return_value = {
        "worldview": [], "emotional_state": {}, "goals": [],
    }
    store.route_texts.return_value = plan
    store.create_semantic_memory.return_value = "new-memory-1"
    store.add_evidence.return_value = {"applied": True, "prior": 0.5, "posterior": 0.64}
    return pipeline


def _section() -> MagicMock:
    section = MagicMock()
    section.index = 0
    section.title = "Body"
    section.content = "some section text"
    return section


def _doc() -> MagicMock:
    doc = MagicMock()
    doc.title = "Test Doc"
    return doc


async def _run(pipeline) -> dict[str, Any]:
    facts = ["fact zero is long enough", "fact one is long enough", "fact two is long enough"]
    with patch(
        "services.slow_ingest_rlm.run_slow_ingest_chunk",
        new=AsyncMock(return_value=_assessment(facts)),
    ):
        return await run_slow_ingest(
            pipeline=pipeline,
            doc=_doc(),
            sections=[_section()],
            llm_config={"provider": "openai", "model": "test"},
            dsn="postgresql://unused",
        )


async def test_duplicate_becomes_evidence_not_new_memory():
    plan = [
        {"index": 0, "decision": "duplicate", "matched_memory_id": "existing-1"},
        {"index": 1, "decision": "related", "matched_memory_id": "existing-2"},
        {"index": 2, "decision": "create", "matched_memory_id": None},
    ]
    pipeline = _make_pipeline(plan)
    result = await _run(pipeline)

    # The duplicate corroborates the matched memory via the evidence policy...
    pipeline.store.add_evidence.assert_called_once()
    kwargs = pipeline.store.add_evidence.call_args
    assert kwargs.args[0] == "existing-1"
    assert kwargs.args[1] == "supports"
    assert kwargs.kwargs["evidence_memory_id"] == "encounter-1"
    assert kwargs.kwargs["context"] == "slow_ingest"

    # ...and only the related + create facts become new memories.
    assert pipeline.store.create_semantic_memory.call_count == 2
    assert result["memories_created"] == 2

    # The related fact gains an ASSOCIATED edge to its router match.
    associated = [
        c for c in pipeline.store.connect_memories.call_args_list
        if c.args[1] == "existing-2"
    ]
    assert len(associated) == 1


async def test_corroboration_failure_is_logged_not_swallowed(caplog):
    plan = [
        {"index": 0, "decision": "duplicate", "matched_memory_id": "existing-1"},
        {"index": 1, "decision": "create", "matched_memory_id": None},
        {"index": 2, "decision": "create", "matched_memory_id": None},
    ]
    pipeline = _make_pipeline(plan)
    pipeline.store.add_evidence.side_effect = RuntimeError("db unavailable")

    with caplog.at_level(logging.ERROR, logger="ingest"):
        result = await _run(pipeline)

    # The document still processes; the failure is loudly visible.
    assert result["memories_created"] == 2
    assert any("corroboration failed" in r.message for r in caplog.records)


async def test_router_failure_falls_back_to_creating_all_facts(caplog):
    pipeline = _make_pipeline([])
    pipeline.store.route_texts.side_effect = RuntimeError("router down")

    with caplog.at_level(logging.ERROR, logger="ingest"):
        result = await _run(pipeline)

    assert result["memories_created"] == 3
    pipeline.store.add_evidence.assert_not_called()
    assert any("fact routing failed" in r.message for r in caplog.records)
