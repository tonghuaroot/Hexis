"""
Tests for heartbeat agentic features in services/heartbeat_agentic.py.

Covers: backlog task detection, checkpoint context extraction, energy boost,
system prompt augmentation, always-on planning/continuation, permission
gating, and finalization auto-checkpoint.
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.heartbeat_agentic import (
    _has_backlog_tasks,
    _get_checkpoint_context,
    build_heartbeat_system_prompt,
    finalize_heartbeat,
    run_agentic_heartbeat,
)

pytestmark = [pytest.mark.asyncio(loop_scope="session")]


# ============================================================================
# Helpers
# ============================================================================


def _mock_registry(tool_names: list[str] | None = None) -> MagicMock:
    registry = MagicMock()
    registry.pool = MagicMock()
    specs = [
        {"type": "function", "function": {"name": n, "description": f"{n} tool", "parameters": {}}}
        for n in (tool_names or ["recall", "remember", "manage_goals", "manage_backlog"])
    ]
    registry.get_specs = AsyncMock(return_value=specs)
    registry.get_spec = MagicMock(return_value=None)
    registry.execute = AsyncMock()
    registry.get_config = AsyncMock(return_value=MagicMock(
        get_context_overrides=MagicMock(return_value=MagicMock(
            allow_shell=False, allow_file_write=False
        )),
        workspace_path=None,
    ))
    return registry


def _mock_conn(prompt: str = "[heartbeat decision prompt]") -> AsyncMock:
    """A connection mock whose fetchval returns a string, so the DB-rendered
    heartbeat prompt (render_heartbeat_decision_prompt_db) yields a real value."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=prompt)
    return conn


def _base_context(**overrides: Any) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "agent": {"objectives": ["Test"], "guardrails": [], "tools": [], "budget": {}},
        "environment": {
            "timestamp": "2025-01-15T12:00:00Z",
            "day_of_week": "Wednesday",
            "hour_of_day": 12,
            "time_since_user_hours": 1.0,
            "pending_events": 0,
        },
        "goals": {"counts": {"active": 0, "queued": 0}, "active": [], "queued": [], "issues": []},
        "recent_memories": [],
        "identity": [],
        "worldview": [],
        "self_model": [],
        "narrative": {},
        "urgent_drives": [],
        "emotional_state": {},
        "relationships": [],
        "contradictions": [],
        "emotional_patterns": [],
        "active_transformations": [],
        "transformations_ready": [],
        "energy": {"current": 15, "max": 20},
        "allowed_actions": [],
        "action_costs": {},
        "backlog": {},
        "heartbeat_number": 42,
    }
    ctx.update(overrides)
    return ctx


# ============================================================================
# Unit: _has_backlog_tasks
# ============================================================================


class TestDetectTaskMode:
    def test_no_backlog_returns_false(self):
        ctx = _base_context(backlog={})
        assert _has_backlog_tasks(ctx) is False

    def test_empty_counts_returns_false(self):
        ctx = _base_context(backlog={"counts": {"todo": 0, "in_progress": 0}, "actionable": []})
        assert _has_backlog_tasks(ctx) is False

    def test_actionable_items_returns_true(self):
        ctx = _base_context(backlog={
            "counts": {"todo": 1},
            "actionable": [{"title": "Deploy config", "priority": "high", "status": "todo"}],
        })
        assert _has_backlog_tasks(ctx) is True

    def test_in_progress_count_returns_true(self):
        ctx = _base_context(backlog={
            "counts": {"todo": 0, "in_progress": 1},
            "actionable": [],
        })
        assert _has_backlog_tasks(ctx) is True

    def test_non_dict_backlog_returns_false(self):
        ctx = _base_context(backlog="not a dict")
        assert _has_backlog_tasks(ctx) is False

    def test_missing_backlog_returns_false(self):
        ctx = _base_context()
        del ctx["backlog"]
        assert _has_backlog_tasks(ctx) is False

    def test_only_done_items_returns_false(self):
        ctx = _base_context(backlog={
            "counts": {"done": 5, "todo": 0, "in_progress": 0},
            "actionable": [],
        })
        assert _has_backlog_tasks(ctx) is False

    def test_null_counts_with_actionable_returns_true(self):
        ctx = _base_context(backlog={
            "counts": {},
            "actionable": [{"title": "Task", "status": "todo"}],
        })
        assert _has_backlog_tasks(ctx) is True


# ============================================================================
# Unit: _get_checkpoint_context
# ============================================================================


class TestGetCheckpointContext:
    def test_no_backlog_returns_empty(self):
        ctx = _base_context(backlog={})
        assert _get_checkpoint_context(ctx) == ""

    def test_no_in_progress_returns_empty(self):
        ctx = _base_context(backlog={
            "actionable": [{"title": "Task", "status": "todo", "checkpoint": None}],
        })
        assert _get_checkpoint_context(ctx) == ""

    def test_in_progress_without_checkpoint_returns_empty(self):
        ctx = _base_context(backlog={
            "actionable": [{"title": "Task", "status": "in_progress"}],
        })
        assert _get_checkpoint_context(ctx) == ""

    def test_in_progress_with_checkpoint_returns_context(self):
        ctx = _base_context(backlog={
            "actionable": [{
                "title": "Deploy config",
                "status": "in_progress",
                "checkpoint": {
                    "step": "step 2",
                    "progress": "Wrote config file",
                    "next_action": "Run deploy script",
                },
            }],
        })
        result = _get_checkpoint_context(ctx)
        assert "## Checkpoint Resume" in result
        assert "Deploy config" in result
        assert "step 2" in result
        assert "Run deploy script" in result

    def test_multiple_checkpoints_included(self):
        ctx = _base_context(backlog={
            "actionable": [
                {
                    "title": "Task A",
                    "status": "in_progress",
                    "checkpoint": {"step": "1", "progress": "done", "next_action": "verify"},
                },
                {
                    "title": "Task B",
                    "status": "in_progress",
                    "checkpoint": {"step": "3", "progress": "half", "next_action": "finish"},
                },
            ],
        })
        result = _get_checkpoint_context(ctx)
        assert "Task A" in result
        assert "Task B" in result

    def test_empty_checkpoint_dict_ignored(self):
        ctx = _base_context(backlog={
            "actionable": [{"title": "Task", "status": "in_progress", "checkpoint": {}}],
        })
        assert _get_checkpoint_context(ctx) == ""


# ============================================================================
# Unit: build_heartbeat_system_prompt with has_backlog_tasks
# ============================================================================


class TestBuildSystemPromptBacklog:
    async def test_no_backlog_no_task_prompt(self):
        prompt = await build_heartbeat_system_prompt(None, has_backlog_tasks=False)
        assert "Task Mode" not in prompt

    async def test_backlog_includes_task_prompt(self):
        prompt = await build_heartbeat_system_prompt(None, has_backlog_tasks=True)
        assert "Task Mode" in prompt
        assert "PICK" in prompt
        assert "CHECKPOINT" in prompt

    async def test_backlog_with_registry(self):
        registry = _mock_registry()
        prompt = await build_heartbeat_system_prompt(registry, has_backlog_tasks=True)
        assert "Task Mode" in prompt
        assert "manage_backlog" in prompt

    async def test_default_is_no_backlog(self):
        prompt = await build_heartbeat_system_prompt()
        assert "Task Mode" not in prompt


# ============================================================================
# Unit: run_agentic_heartbeat resource scaling
# ============================================================================


class TestRunAgenticHeartbeatScaling:
    @patch("services.heartbeat_agentic.run_agent")
    async def test_backlog_doubles_energy(self, mock_run_agent):
        mock_run_agent.return_value = MagicMock(
            text="Done.", tool_calls_made=[], iterations=1,
            energy_spent=0, timed_out=False, stopped_reason="completed",
        )

        ctx = _base_context(backlog={
            "counts": {"todo": 1},
            "actionable": [{"title": "Task", "status": "todo"}],
        })
        ctx["energy"]["current"] = 10

        result = await run_agentic_heartbeat(
            _mock_conn(), pool=MagicMock(), registry=_mock_registry(),
            heartbeat_id="hb-tm-001", context=ctx,
        )

        call_kwargs = mock_run_agent.call_args[1]
        assert call_kwargs["energy_budget"] == 20  # 10 * 2
        assert result["has_backlog_tasks"] is True

    @patch("services.heartbeat_agentic.run_agent")
    async def test_no_backlog_normal_energy(self, mock_run_agent):
        mock_run_agent.return_value = MagicMock(
            text="Done.", tool_calls_made=[], iterations=1,
            energy_spent=0, timed_out=False, stopped_reason="completed",
        )

        ctx = _base_context(backlog={"counts": {}, "actionable": []})
        ctx["energy"]["current"] = 10

        result = await run_agentic_heartbeat(
            _mock_conn(), pool=MagicMock(), registry=_mock_registry(),
            heartbeat_id="hb-tm-002", context=ctx,
        )

        call_kwargs = mock_run_agent.call_args[1]
        assert call_kwargs["energy_budget"] == 10  # unchanged
        assert result["has_backlog_tasks"] is False

    @patch("services.heartbeat_agentic.run_agent")
    async def test_backlog_extends_timeout(self, mock_run_agent):
        mock_run_agent.return_value = MagicMock(
            text="Done.", tool_calls_made=[], iterations=1,
            energy_spent=0, timed_out=False, stopped_reason="completed",
        )

        ctx = _base_context(backlog={
            "counts": {"todo": 1},
            "actionable": [{"title": "Task", "status": "todo"}],
        })

        await run_agentic_heartbeat(
            _mock_conn(), pool=MagicMock(), registry=_mock_registry(),
            heartbeat_id="hb-tm-003", context=ctx,
        )

        call_kwargs = mock_run_agent.call_args[1]
        assert call_kwargs["timeout_seconds"] == 300.0

    @patch("services.heartbeat_agentic.run_agent")
    async def test_backlog_increases_max_tokens(self, mock_run_agent):
        mock_run_agent.return_value = MagicMock(
            text="Done.", tool_calls_made=[], iterations=1,
            energy_spent=0, timed_out=False, stopped_reason="completed",
        )

        ctx = _base_context(backlog={
            "counts": {"todo": 1},
            "actionable": [{"title": "Task", "status": "todo"}],
        })

        await run_agentic_heartbeat(
            _mock_conn(), pool=MagicMock(), registry=_mock_registry(),
            heartbeat_id="hb-tm-004", context=ctx,
        )

        call_kwargs = mock_run_agent.call_args[1]
        assert call_kwargs["max_tokens"] == 4096

    @patch("services.heartbeat_agentic.run_agent")
    async def test_checkpoint_context_appended_to_user_message(self, mock_run_agent):
        mock_run_agent.return_value = MagicMock(
            text="Done.", tool_calls_made=[], iterations=1,
            energy_spent=0, timed_out=False, stopped_reason="completed",
        )

        ctx = _base_context(backlog={
            "counts": {"in_progress": 1},
            "actionable": [{
                "title": "Deploy",
                "status": "in_progress",
                "checkpoint": {"step": "2", "progress": "Built", "next_action": "Push"},
            }],
        })

        await run_agentic_heartbeat(
            _mock_conn(), pool=MagicMock(), registry=_mock_registry(),
            heartbeat_id="hb-tm-005", context=ctx,
        )

        # The user_message passed to run_agent should include checkpoint context
        call_kwargs = mock_run_agent.call_args[1]
        user_msg = call_kwargs["user_message"]
        assert "Checkpoint Resume" in user_msg
        assert "Deploy" in user_msg
        assert "Push" in user_msg


# ============================================================================
# Integration: finalize_heartbeat auto-checkpoint
# ============================================================================


class TestFinalizeAutoCheckpoint:
    async def test_auto_checkpoint_on_timeout(self, db_pool):
        """In-progress items without checkpoints get auto-checkpointed on timeout."""
        async with db_pool.acquire() as conn:
            # Create an in-progress backlog item
            item_id = await conn.fetchval(
                """
                INSERT INTO public.backlog (title, status, priority)
                VALUES ('Auto-checkpoint test', 'in_progress', 'high')
                RETURNING id
                """
            )

            try:
                result = await finalize_heartbeat(
                    conn,
                    heartbeat_id=str(uuid.uuid4()),
                    result={
                        "text": "Ran out of time.",
                        "tool_calls_made": [{"name": "shell"}],
                        "energy_spent": 20,
                        "stopped_reason": "timeout",
                        "has_backlog_tasks": True,
                    },
                )

                assert result["completed"] is True
                assert result["has_backlog_tasks"] is True

                # Verify checkpoint was set
                row = await conn.fetchrow(
                    "SELECT checkpoint FROM public.backlog WHERE id = $1", item_id
                )
                checkpoint = json.loads(row["checkpoint"])
                assert checkpoint["step"] == "interrupted"
                assert "timeout" in checkpoint["progress"]
            finally:
                await conn.execute("DELETE FROM public.backlog WHERE id = $1", item_id)

    async def test_no_auto_checkpoint_when_already_checkpointed(self, db_pool):
        """Items with existing checkpoints are not overwritten."""
        async with db_pool.acquire() as conn:
            original_cp = json.dumps({"step": "step 3", "progress": "good", "next_action": "verify"})
            item_id = await conn.fetchval(
                """
                INSERT INTO public.backlog (title, status, priority, checkpoint)
                VALUES ('Already checkpointed', 'in_progress', 'high', $1::jsonb)
                RETURNING id
                """,
                original_cp,
            )

            try:
                await finalize_heartbeat(
                    conn,
                    heartbeat_id=str(uuid.uuid4()),
                    result={
                        "text": "Timed out.",
                        "tool_calls_made": [],
                        "energy_spent": 10,
                        "stopped_reason": "timeout",
                        "has_backlog_tasks": True,
                    },
                )

                row = await conn.fetchrow(
                    "SELECT checkpoint FROM public.backlog WHERE id = $1", item_id
                )
                checkpoint = json.loads(row["checkpoint"])
                assert checkpoint["step"] == "step 3"  # unchanged
            finally:
                await conn.execute("DELETE FROM public.backlog WHERE id = $1", item_id)

    async def test_no_auto_checkpoint_on_normal_completion(self, db_pool):
        """No auto-checkpoint when heartbeat completes normally."""
        async with db_pool.acquire() as conn:
            item_id = await conn.fetchval(
                """
                INSERT INTO public.backlog (title, status, priority)
                VALUES ('Normal completion', 'in_progress', 'normal')
                RETURNING id
                """
            )

            try:
                await finalize_heartbeat(
                    conn,
                    heartbeat_id=str(uuid.uuid4()),
                    result={
                        "text": "All done.",
                        "tool_calls_made": [],
                        "energy_spent": 5,
                        "stopped_reason": "completed",
                        "has_backlog_tasks": True,
                    },
                )

                row = await conn.fetchrow(
                    "SELECT checkpoint FROM public.backlog WHERE id = $1", item_id
                )
                assert row["checkpoint"] is None  # not auto-checkpointed
            finally:
                await conn.execute("DELETE FROM public.backlog WHERE id = $1", item_id)

    async def test_no_auto_checkpoint_without_backlog_tasks(self, db_pool):
        """No auto-checkpoint when has_backlog_tasks is False."""
        async with db_pool.acquire() as conn:
            item_id = await conn.fetchval(
                """
                INSERT INTO public.backlog (title, status, priority)
                VALUES ('No task mode', 'in_progress', 'normal')
                RETURNING id
                """
            )

            try:
                await finalize_heartbeat(
                    conn,
                    heartbeat_id=str(uuid.uuid4()),
                    result={
                        "text": "Timed out.",
                        "tool_calls_made": [],
                        "energy_spent": 10,
                        "stopped_reason": "timeout",
                        "has_backlog_tasks": False,
                    },
                )

                row = await conn.fetchrow(
                    "SELECT checkpoint FROM public.backlog WHERE id = $1", item_id
                )
                assert row["checkpoint"] is None
            finally:
                await conn.execute("DELETE FROM public.backlog WHERE id = $1", item_id)

    async def test_auto_checkpoint_on_energy_exhausted(self, db_pool):
        """Auto-checkpoint also triggers on energy_exhausted."""
        async with db_pool.acquire() as conn:
            item_id = await conn.fetchval(
                """
                INSERT INTO public.backlog (title, status, priority)
                VALUES ('Energy exhausted test', 'in_progress', 'urgent')
                RETURNING id
                """
            )

            try:
                await finalize_heartbeat(
                    conn,
                    heartbeat_id=str(uuid.uuid4()),
                    result={
                        "text": "Out of energy.",
                        "tool_calls_made": [{"name": "shell"}, {"name": "recall"}],
                        "energy_spent": 40,
                        "stopped_reason": "energy_exhausted",
                        "has_backlog_tasks": True,
                    },
                )

                row = await conn.fetchrow(
                    "SELECT checkpoint FROM public.backlog WHERE id = $1", item_id
                )
                checkpoint = json.loads(row["checkpoint"])
                assert checkpoint["step"] == "interrupted"
                assert "energy_exhausted" in checkpoint["progress"]
            finally:
                await conn.execute("DELETE FROM public.backlog WHERE id = $1", item_id)


# ============================================================================
# Unit: prompt_resources task mode loader
# ============================================================================


class TestTaskModePromptLoader:
    def test_load_task_mode_prompt(self):
        from services.prompt_resources import load_heartbeat_task_mode_prompt
        prompt = load_heartbeat_task_mode_prompt()
        assert "Task Mode" in prompt
        assert "PICK" in prompt
        assert "EXECUTE" in prompt
        assert "VERIFY" in prompt
        assert "CHECKPOINT" in prompt


# ============================================================================
# Integration: always-on planning/continuation, gated permissions
# ============================================================================


class TestHeartbeatAgentLoopWiring:
    """Verify that planning and continuation are always on, permissions are gated.

    Planning and continuation are now configured inside services.agent.run_agent(),
    so we mock at that level. Permission gating is tested by inspecting kwargs
    passed from run_agentic_heartbeat → run_agent.
    """

    @patch("services.agent.run_subconscious_appraisal", new_callable=AsyncMock)
    @patch("services.agent.AgentLoop")
    @patch("services.agent.load_llm_config")
    async def test_planning_always_enabled(self, mock_load_config, mock_agent_class, mock_sub):
        """Planning is on even without backlog tasks."""
        from services.agent import SubconsciousOutput
        mock_sub.return_value = SubconsciousOutput()
        mock_load_config.return_value = {
            "provider": "openai", "model": "gpt-4o", "endpoint": None, "api_key": "t",
        }
        mock_agent = AsyncMock()
        mock_agent.run.return_value = MagicMock(
            text="Done.", tool_calls_made=[], iterations=1,
            energy_spent=0, timed_out=False, stopped_reason="completed",
        )
        mock_agent_class.return_value = mock_agent

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(return_value=None)
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        ctx = _base_context(backlog={"counts": {}, "actionable": []})

        await run_agentic_heartbeat(
            _mock_conn(), pool=mock_pool, registry=_mock_registry(),
            heartbeat_id="hb-always-plan", context=ctx,
        )

        config_arg = mock_agent_class.call_args[0][0]
        assert config_arg.enable_planning is True

    @patch("services.agent.run_subconscious_appraisal", new_callable=AsyncMock)
    @patch("services.agent.AgentLoop")
    @patch("services.agent.load_llm_config")
    async def test_continuation_always_enabled(self, mock_load_config, mock_agent_class, mock_sub):
        """Continuation nudge is on even without backlog tasks."""
        from services.agent import SubconsciousOutput
        mock_sub.return_value = SubconsciousOutput()
        mock_load_config.return_value = {
            "provider": "openai", "model": "gpt-4o", "endpoint": None, "api_key": "t",
        }
        mock_agent = AsyncMock()
        mock_agent.run.return_value = MagicMock(
            text="Done.", tool_calls_made=[], iterations=1,
            energy_spent=0, timed_out=False, stopped_reason="completed",
        )
        mock_agent_class.return_value = mock_agent

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(return_value=None)
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        ctx = _base_context(backlog={"counts": {}, "actionable": []})

        await run_agentic_heartbeat(
            _mock_conn(), pool=mock_pool, registry=_mock_registry(),
            heartbeat_id="hb-always-cont", context=ctx,
        )

        config_arg = mock_agent_class.call_args[0][0]
        assert config_arg.continuation_prompt is not None
        assert config_arg.max_continuations >= 1

    @patch("services.heartbeat_agentic.run_agent")
    async def test_backlog_grants_permissions(self, mock_run_agent):
        """Backlog tasks grant shell + file_write permissions."""
        mock_run_agent.return_value = MagicMock(
            text="Done.", tool_calls_made=[], iterations=1,
            energy_spent=0, timed_out=False, stopped_reason="completed",
        )

        ctx = _base_context(backlog={
            "counts": {"todo": 1},
            "actionable": [{"title": "Write script", "status": "todo"}],
        })

        await run_agentic_heartbeat(
            _mock_conn(), pool=MagicMock(), registry=_mock_registry(),
            heartbeat_id="hb-perms", context=ctx,
        )

        call_kwargs = mock_run_agent.call_args[1]
        overrides = call_kwargs["context_overrides"]
        assert overrides is not None
        assert overrides.allow_shell is True
        assert overrides.allow_file_write is True

    @patch("services.heartbeat_agentic.run_agent")
    async def test_no_backlog_no_permissions(self, mock_run_agent):
        """Without backlog tasks, no elevated permissions are granted."""
        mock_run_agent.return_value = MagicMock(
            text="Done.", tool_calls_made=[], iterations=1,
            energy_spent=0, timed_out=False, stopped_reason="completed",
        )

        ctx = _base_context(backlog={"counts": {}, "actionable": []})

        await run_agentic_heartbeat(
            _mock_conn(), pool=MagicMock(), registry=_mock_registry(),
            heartbeat_id="hb-no-perms", context=ctx,
        )

        call_kwargs = mock_run_agent.call_args[1]
        assert call_kwargs.get("context_overrides") is None

    @patch("services.agent.run_subconscious_appraisal", new_callable=AsyncMock)
    @patch("services.agent.AgentLoop")
    @patch("services.agent.load_llm_config")
    async def test_backlog_gets_more_continuations(self, mock_load_config, mock_agent_class, mock_sub):
        """Backlog tasks get 2 continuations, empty backlog gets 1."""
        from services.agent import SubconsciousOutput
        mock_sub.return_value = SubconsciousOutput()
        mock_load_config.return_value = {
            "provider": "openai", "model": "gpt-4o", "endpoint": None, "api_key": "t",
        }
        mock_agent = AsyncMock()
        mock_agent.run.return_value = MagicMock(
            text="Done.", tool_calls_made=[], iterations=1,
            energy_spent=0, timed_out=False, stopped_reason="completed",
        )
        mock_agent_class.return_value = mock_agent

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(return_value=None)
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        # With backlog
        ctx_tasks = _base_context(backlog={
            "counts": {"todo": 1},
            "actionable": [{"title": "Task", "status": "todo"}],
        })
        await run_agentic_heartbeat(
            _mock_conn(), pool=mock_pool, registry=_mock_registry(),
            heartbeat_id="hb-cont-tasks", context=ctx_tasks,
        )
        config_tasks = mock_agent_class.call_args[0][0]

        # Without backlog
        ctx_empty = _base_context(backlog={"counts": {}, "actionable": []})
        await run_agentic_heartbeat(
            _mock_conn(), pool=mock_pool, registry=_mock_registry(),
            heartbeat_id="hb-cont-empty", context=ctx_empty,
        )
        config_empty = mock_agent_class.call_args[0][0]

        assert config_tasks.max_continuations == 2
        assert config_empty.max_continuations == 1
