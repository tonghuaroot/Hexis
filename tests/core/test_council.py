"""Tests for Multi-Agent Council tools (F.1, F.2, F.3)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.tools.council import (
    AggregateSignalsHandler,
    ListCouncilPersonasHandler,
    RunCouncilHandler,
    create_council_tools,
)
from core.tools.base import ToolCategory, ToolContext, ToolExecutionContext

pytestmark = [pytest.mark.asyncio(loop_scope="session")]

EXPECTED_PERSONAS = {
    "growth_strategist",
    "revenue_guardian",
    "skeptical_operator",
    "creative_innovator",
    "customer_advocate",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_context(pool=None):
    """Build a minimal ToolExecutionContext with a registry stub."""
    registry = MagicMock()
    registry.pool = pool
    return ToolExecutionContext(
        tool_context=ToolContext.CHAT,
        call_id="test-call",
        registry=registry,
    )


# ---------------------------------------------------------------------------
# F.1 -- Council persona catalog (DB data)
# ---------------------------------------------------------------------------


class TestCouncilPersonas:
    """The persona catalog is DB data: prompt_modules council.persona.*."""

    async def test_db_catalog_shape(self, db_pool):
        async with db_pool.acquire() as conn:
            raw = await conn.fetchval("SELECT get_council_personas()")
        personas = json.loads(raw) if isinstance(raw, str) else raw
        assert set(personas.keys()) == EXPECTED_PERSONAS
        for key, persona in personas.items():
            assert isinstance(persona["name"], str), key
            assert len(persona["system_prompt"]) > 10, key


# ---------------------------------------------------------------------------
# F.1 -- ListCouncilPersonasHandler
# ---------------------------------------------------------------------------


class TestListCouncilPersonasSpec:
    """Verify list_council_personas tool spec."""

    def test_spec_name(self):
        handler = ListCouncilPersonasHandler()
        assert handler.spec.name == "list_council_personas"

    def test_spec_category(self):
        handler = ListCouncilPersonasHandler()
        assert handler.spec.category == ToolCategory.MEMORY

    def test_spec_energy_cost_zero(self):
        handler = ListCouncilPersonasHandler()
        assert handler.spec.energy_cost == 0

    def test_spec_read_only(self):
        handler = ListCouncilPersonasHandler()
        assert handler.spec.is_read_only is True


class TestListCouncilPersonasExecution:
    """Verify list_council_personas execution."""

    async def test_returns_all_five(self, db_pool):
        handler = ListCouncilPersonasHandler()
        ctx = _make_context(db_pool)
        result = await handler.execute({}, ctx)

        assert result.success
        data = result.output
        assert data["count"] == 5
        assert len(data["personas"]) == 5

    async def test_persona_keys_match(self, db_pool):
        handler = ListCouncilPersonasHandler()
        ctx = _make_context(db_pool)
        result = await handler.execute({}, ctx)

        data = result.output
        assert set(data["personas"].keys()) == EXPECTED_PERSONAS

    async def test_each_persona_has_fields(self, db_pool):
        handler = ListCouncilPersonasHandler()
        ctx = _make_context(db_pool)
        result = await handler.execute({}, ctx)

        data = result.output
        for key, persona in data["personas"].items():
            assert "name" in persona
            assert "system_prompt" in persona


# ---------------------------------------------------------------------------
# F.2 -- RunCouncilHandler
# ---------------------------------------------------------------------------


class TestRunCouncilSpec:
    """Verify run_council tool spec."""

    def test_spec_name(self):
        handler = RunCouncilHandler()
        assert handler.spec.name == "run_council"

    def test_spec_category(self):
        handler = RunCouncilHandler()
        assert handler.spec.category == ToolCategory.MEMORY

    def test_spec_energy_cost(self):
        handler = RunCouncilHandler()
        assert handler.spec.energy_cost == 5

    def test_spec_read_only(self):
        handler = RunCouncilHandler()
        assert handler.spec.is_read_only is True

    def test_spec_optional(self):
        handler = RunCouncilHandler()
        assert handler.spec.optional is True

    def test_spec_requires_topic(self):
        handler = RunCouncilHandler()
        assert "topic" in handler.spec.parameters["required"]

    def test_spec_has_signal_limit(self):
        handler = RunCouncilHandler()
        assert "signal_limit" in handler.spec.parameters["properties"]


class TestRunCouncilExecution:
    """Verify run_council execution."""

    async def test_default_all_five_personas(self, db_pool):
        handler = RunCouncilHandler()
        ctx = _make_context(db_pool)
        result = await handler.execute({"topic": "Should we expand into APAC?"}, ctx)

        assert result.success
        data = result.output
        assert data["persona_count"] == 5
        assert len(data["council"]) == 5
        assert data["topic"] == "Should we expand into APAC?"

    async def test_custom_persona_list(self, db_pool):
        handler = RunCouncilHandler()
        ctx = _make_context(db_pool)
        result = await handler.execute({
            "topic": "Pricing decision",
            "personas": ["revenue_guardian", "customer_advocate"],
        }, ctx)

        assert result.success
        data = result.output
        assert data["persona_count"] == 2
        included = data["personas_included"]
        assert "revenue_guardian" in included
        assert "customer_advocate" in included
        assert "growth_strategist" not in included

    async def test_single_persona(self, db_pool):
        handler = RunCouncilHandler()
        ctx = _make_context(db_pool)
        result = await handler.execute({
            "topic": "Risk assessment",
            "personas": ["skeptical_operator"],
        }, ctx)

        assert result.success
        data = result.output
        assert data["persona_count"] == 1
        assert data["council"][0]["persona_key"] == "skeptical_operator"

    async def test_invalid_persona_returns_error(self, db_pool):
        handler = RunCouncilHandler()
        ctx = _make_context(db_pool)
        result = await handler.execute({
            "topic": "test",
            "personas": ["nonexistent_persona"],
        }, ctx)

        assert not result.success
        assert "nonexistent_persona" in result.error

    async def test_mixed_valid_invalid_personas_returns_error(self, db_pool):
        handler = RunCouncilHandler()
        ctx = _make_context(db_pool)
        result = await handler.execute({
            "topic": "test",
            "personas": ["growth_strategist", "bad_persona"],
        }, ctx)

        assert not result.success
        assert "bad_persona" in result.error

    async def test_empty_topic_returns_error(self, db_pool):
        handler = RunCouncilHandler()
        ctx = _make_context(db_pool)
        result = await handler.execute({"topic": ""}, ctx)

        assert not result.success
        assert "required" in result.error.lower()

    async def test_missing_topic_returns_error(self, db_pool):
        handler = RunCouncilHandler()
        ctx = _make_context(db_pool)
        result = await handler.execute({}, ctx)

        assert not result.success

    async def test_context_included_in_prompt(self, db_pool):
        handler = RunCouncilHandler()
        ctx = _make_context(db_pool)
        result = await handler.execute({
            "topic": "Go/no-go decision",
            "context": "Revenue is $5M ARR with 30% margins",
        }, ctx)

        assert result.success
        data = result.output
        for entry in data["council"]:
            assert "Revenue is $5M ARR" in entry["full_prompt"]
            assert "Go/no-go decision" in entry["full_prompt"]

    async def test_council_entry_structure(self, db_pool):
        handler = RunCouncilHandler()
        ctx = _make_context(db_pool)
        result = await handler.execute({"topic": "Structure test"}, ctx)

        assert result.success
        data = result.output
        for entry in data["council"]:
            assert "persona_key" in entry
            assert "persona_name" in entry
            assert "system_prompt" in entry
            assert "full_prompt" in entry

    async def test_instructions_present(self, db_pool):
        handler = RunCouncilHandler()
        ctx = _make_context(db_pool)
        result = await handler.execute({"topic": "test"}, ctx)

        data = result.output
        assert "instructions" in data
        assert len(data["instructions"]) > 0

    async def test_outputs_analysis_and_moderator_report(self, db_pool):
        handler = RunCouncilHandler()
        ctx = _make_context(db_pool)
        result = await handler.execute({"topic": "Expansion plan"}, ctx)

        assert result.success
        data = result.output
        assert isinstance(data.get("moderator_report"), str)
        assert data["moderator_report"]
        for entry in data["council"]:
            assert "analysis" in entry
            assert isinstance(entry["analysis"], str)

    async def test_collects_signals_when_db_available(self, db_pool):
        handler = RunCouncilHandler()
        mock_conn = AsyncMock()

        async def _fetch_side_effect(query: str, *_args):
            if "FROM gateway_events" in query:
                return [{"source": "chat", "payload": {"message": "hello", "intent": "plan"}}]
            if "WHERE type = 'episodic'" in query:
                return [{"content": "User asked for a launch plan"}]
            if "WHERE type = 'goal'" in query:
                return [{"content": "Increase retention this quarter"}]
            return []

        mock_conn.fetch = AsyncMock(side_effect=_fetch_side_effect)
        mock_conn.fetchval = AsyncMock(return_value=json.dumps({
            "growth_strategist": {
                "name": "Growth Strategist",
                "system_prompt": "You are a growth strategist for this test.",
            }
        }))
        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
        ctx = _make_context(pool=mock_pool)
        handler._load_llm_config = AsyncMock(return_value=None)

        result = await handler.execute({
            "topic": "Priorities",
            "personas": ["growth_strategist"],
            "signal_limit": 5,
        }, ctx)

        assert result.success
        data = result.output
        assert data["signals"]
        assert data["signals"][0].startswith("Event[")
        assert data["persona_count"] == 1


# ---------------------------------------------------------------------------
# F.3 -- AggregateSignalsHandler
# ---------------------------------------------------------------------------


class TestAggregateSignalsSpec:
    """Verify aggregate_signals tool spec."""

    def test_spec_name(self):
        handler = AggregateSignalsHandler()
        assert handler.spec.name == "aggregate_signals"

    def test_spec_category(self):
        handler = AggregateSignalsHandler()
        assert handler.spec.category == ToolCategory.MEMORY

    def test_spec_energy_cost(self):
        handler = AggregateSignalsHandler()
        assert handler.spec.energy_cost == 3

    def test_spec_read_only(self):
        handler = AggregateSignalsHandler()
        assert handler.spec.is_read_only is True

    def test_spec_no_required_params(self):
        handler = AggregateSignalsHandler()
        assert "required" not in handler.spec.parameters


class TestAggregateSignalsExecution:
    """Verify aggregate_signals execution with mock DB."""

    async def test_no_pool_returns_error(self):
        handler = AggregateSignalsHandler()
        ctx = _make_context(pool=None)
        ctx.registry.pool = None
        result = await handler.execute({}, ctx)

        assert not result.success
        assert "pool" in result.error.lower()

    async def test_with_db_pool(self, db_pool):
        """Integration test: runs against real DB (may return empty results)."""
        handler = AggregateSignalsHandler()
        ctx = _make_context(pool=db_pool)
        result = await handler.execute({"days": 1, "limit": 5}, ctx)

        assert result.success
        data = result.output
        assert "events" in data
        assert "memories" in data
        assert "goals" in data
        assert "summary" in data
        assert data["time_window_days"] == 1
        assert data["domain_filter"] is None

    async def test_with_domain_filter(self, db_pool):
        """Integration test with domain filter."""
        handler = AggregateSignalsHandler()
        ctx = _make_context(pool=db_pool)
        result = await handler.execute({
            "domain": "chat",
            "days": 7,
            "limit": 10,
        }, ctx)

        assert result.success
        data = result.output
        assert data["domain_filter"] == "chat"

    async def test_default_params(self, db_pool):
        """Default days=7, limit=20."""
        handler = AggregateSignalsHandler()
        ctx = _make_context(pool=db_pool)
        result = await handler.execute({}, ctx)

        assert result.success
        data = result.output
        assert data["time_window_days"] == 7

    async def test_summary_structure(self, db_pool):
        """Summary section has expected fields."""
        handler = AggregateSignalsHandler()
        ctx = _make_context(pool=db_pool)
        result = await handler.execute({}, ctx)

        assert result.success
        data = result.output
        summary = data["summary"]
        assert "total_signals" in summary
        assert "event_sources" in summary
        assert "highest_importance_goal" in summary

    async def test_limit_capped_at_100(self, db_pool):
        """Limit should be capped at 100."""
        handler = AggregateSignalsHandler()
        ctx = _make_context(pool=db_pool)
        result = await handler.execute({"limit": 500}, ctx)

        assert result.success
        # The handler should clamp to 100 internally; no error

    async def test_days_minimum_one(self, db_pool):
        """Days should be at least 1."""
        handler = AggregateSignalsHandler()
        ctx = _make_context(pool=db_pool)
        result = await handler.execute({"days": 0}, ctx)

        assert result.success
        data = result.output
        assert data["time_window_days"] == 1

    async def test_seeded_rows_counted(self, db_pool):
        """SQL-level: seeded event + memory + goal all appear in the snapshot."""
        async with db_pool.acquire() as conn:
            tr = conn.transaction()
            await tr.start()
            try:
                await conn.execute(
                    """
                    INSERT INTO gateway_events (source, status, session_key, payload)
                    VALUES ('chat', 'completed', 'sess-agg-test', '{"message": "hello"}'::jsonb)
                    """
                )
                await conn.execute(
                    """
                    INSERT INTO memories (type, content, embedding, importance, trust_level, status)
                    VALUES ('episodic', 'User asked about pricing',
                            array_fill(0.1, ARRAY[embedding_dimension()])::vector, 0.7, 0.9, 'active'),
                           ('goal', 'Increase user retention by 20%',
                            array_fill(0.1, ARRAY[embedding_dimension()])::vector, 0.9, 0.9, 'active')
                    """
                )
                raw = await conn.fetchval(
                    "SELECT aggregate_signals_tool('{\"days\": 7, \"limit\": 20}'::jsonb)"
                )
            finally:
                await tr.rollback()

        payload = json.loads(raw)
        assert payload["success"] is True
        data = payload["output"]
        assert data["events"]["count"] >= 1
        assert data["memories"]["count"] >= 1
        assert data["goals"]["count"] >= 1
        assert data["summary"]["total_signals"] >= 3
        assert "chat" in data["summary"]["event_sources"]


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestCouncilToolFactory:
    """Verify the create_council_tools factory."""

    def test_returns_three_handlers(self):
        tools = create_council_tools()
        assert len(tools) == 3

    def test_tool_names(self):
        tools = create_council_tools()
        names = [t.spec.name for t in tools]
        assert "list_council_personas" in names
        assert "run_council" in names
        assert "aggregate_signals" in names

    def test_all_memory_category(self):
        tools = create_council_tools()
        for tool in tools:
            assert tool.spec.category == ToolCategory.MEMORY


# ---------------------------------------------------------------------------
# Registry integration
# ---------------------------------------------------------------------------


class TestCouncilRegistration:
    """Verify council tools are registered in the default registry."""

    async def test_registered_in_default_registry(self, db_pool):
        from core.tools import create_default_registry

        registry = create_default_registry(db_pool)
        tool_names = [t.spec.name for t in registry._handlers.values()]
        assert "list_council_personas" in tool_names
        assert "run_council" in tool_names
        assert "aggregate_signals" in tool_names
