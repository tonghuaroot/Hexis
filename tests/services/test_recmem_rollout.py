from __future__ import annotations

import pytest

from services.recmem_rollout import (
    apply_recmem_rollout_phase,
    get_recmem_rollout_status,
)


@pytest.mark.asyncio(loop_scope="session")
async def test_apply_recmem_rollout_phase_sets_dual_write_configs(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            status = await apply_recmem_rollout_phase(conn, 2)

            assert status["applied_phase"] == 2
            assert status["phase"] == 2
            configs = status["configs"]
            assert configs["memory.recmem_rollout_phase"] == 2
            assert configs["memory.recmem_enabled"] is True
            assert configs["chat.eager_memory_enabled"] is True
            assert configs["memory.recmem_hydrate_enabled"] is False
            assert configs["memory.recmem_dual_write_compare"] is True
            assert configs["memory.recmem_rollout_metrics_enabled"] is True

            stored = await get_recmem_rollout_status(conn)
            assert stored["phase"] == 2
            assert "health" in stored
            assert "phase5_readiness" in stored
        finally:
            await tr.rollback()


@pytest.mark.asyncio(loop_scope="session")
async def test_phase5_requires_readiness_unless_forced(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute("DELETE FROM recmem_eval_runs")

            with pytest.raises(RuntimeError, match="requires a passing readiness gate"):
                await apply_recmem_rollout_phase(conn, 5)

            forced = await apply_recmem_rollout_phase(conn, 5, force=True)
            assert forced["forced"] is True
            assert forced["phase"] == 5
            assert forced["configs"]["memory.recmem_hydrate_enabled"] is True
            assert forced["configs"]["memory.recmem_worker_enabled"] is True
        finally:
            await tr.rollback()
