from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from typing import Any
import asyncpg
from dotenv import load_dotenv

from core.agent_api import db_dsn_from_env
from core.gateway import EventSource, Gateway, GatewayConsumer, GatewayEvent
from core.rabbitmq_bridge import RabbitMQBridge
from core.state import (
    is_agent_terminated,
    mark_subconscious_decider_run,
    recompute_cron_next_runs,
    run_heartbeat,
    run_maintenance_if_due,
    run_scheduled_tasks,
    should_run_subconscious_decider,
)
from services.external_calls import ExternalCallProcessor
from services.heartbeat_agentic import finalize_heartbeat, run_agentic_heartbeat
from services.heartbeat_runner import execute_heartbeat_decision
from services.hmx_reembedding import run_hmx_reembed_step
from services.recmem import (
    run_recmem_consolidation_step,
    run_recmem_embed_step,
    run_recmem_route_step,
    run_recmem_sweep_step,
)
from services.extraction import run_conscious_extraction_step
from services.summarization import run_memory_summarization_step
from services.skill_improvement import run_skill_improvement_review_step
from services.reconsolidation import run_reconsolidation_step
from services.subconscious import run_subconscious_decider


load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("heartbeat_worker")

POLL_INTERVAL = float(os.getenv("WORKER_POLL_INTERVAL", 1.0))
MAX_RETRIES = int(os.getenv("WORKER_MAX_RETRIES", 3))


# ---------------------------------------------------------------------------
# HeartbeatWorker — Timer that checks if heartbeat is due and submits events
# ---------------------------------------------------------------------------


class HeartbeatWorker:
    """Timer that checks if heartbeat is due and submits gateway events.

    This worker does NOT execute heartbeats — it only decides WHEN to fire
    and submits the heartbeat payload as a gateway event. The GatewayConsumer
    dequeues and executes the actual heartbeat logic.
    """

    def __init__(self, instance: str | None = None):
        self.instance = instance or os.getenv("HEXIS_INSTANCE")
        self.pool: asyncpg.Pool | None = None
        self.running = False

    async def connect(self) -> None:
        self.pool = await asyncpg.create_pool(
            dsn=db_dsn_from_env(self.instance), min_size=1, max_size=5,
        )
        logger.info("HeartbeatWorker connected to database")

    async def disconnect(self) -> None:
        if self.pool:
            await self.pool.close()
            logger.info("HeartbeatWorker disconnected")

    async def _submit_heartbeat_if_due(self) -> None:
        if not self.pool:
            return
        async with self.pool.acquire() as conn:
            payload = await run_heartbeat(conn)
            if not payload:
                return
            heartbeat_id = payload.get("heartbeat_id")
            if heartbeat_id:
                logger.info(f"Heartbeat due: {heartbeat_id} — submitting to gateway")

        # Submit the full payload as a gateway event for the consumer
        try:
            gw = Gateway(self.pool)
            await gw.submit(
                EventSource.HEARTBEAT,
                f"heartbeat:{heartbeat_id or 'unknown'}",
                payload,
            )
        except Exception:
            logger.error("Failed to submit heartbeat event", exc_info=True)

    async def run(self) -> None:
        self.running = True
        logger.info("HeartbeatWorker (timer) starting...")
        await self.connect()

        try:
            while self.running:
                try:
                    if await self._is_agent_terminated():
                        logger.info("Agent is terminated; heartbeat timer exiting.")
                        break
                    if not await self._is_agent_ready():
                        await asyncio.sleep(POLL_INTERVAL)
                        continue
                    if not await self._is_active_hour():
                        logger.debug("Outside active hours; skipping heartbeat.")
                        await asyncio.sleep(POLL_INTERVAL * 10)
                        continue
                    await self._submit_heartbeat_if_due()
                except Exception as exc:
                    logger.error(f"Heartbeat timer error: {exc}")
                await asyncio.sleep(POLL_INTERVAL)
        finally:
            await self.disconnect()

    def stop(self) -> None:
        self.running = False
        logger.info("HeartbeatWorker (timer) stopping...")

    async def _is_agent_terminated(self) -> bool:
        if not self.pool:
            return False
        try:
            async with self.pool.acquire() as conn:
                return await is_agent_terminated(conn)
        except Exception:
            return False

    async def _is_agent_ready(self) -> bool:
        if not self.pool:
            return False
        try:
            async with self.pool.acquire() as conn:
                return bool(await conn.fetchval("SELECT is_agent_configured() AND is_init_complete()"))
        except Exception:
            return False

    async def _is_active_hour(self) -> bool:
        """Check if the current time is within configured active hours (DB-owned)."""
        if not self.pool:
            return True
        try:
            async with self.pool.acquire() as conn:
                result = await conn.fetchval("SELECT is_within_active_hours()")
            return bool(result) if result is not None else True
        except Exception:
            return True


# ---------------------------------------------------------------------------
# Heartbeat event handler — registered with GatewayConsumer
# ---------------------------------------------------------------------------


def _extract_heartbeat_context(payload: dict) -> dict:
    """Extract heartbeat context from the run_heartbeat() payload."""
    external_calls = payload.get("external_calls")
    if not isinstance(external_calls, list):
        return payload

    for call in external_calls:
        if not isinstance(call, dict):
            continue
        call_type = str(call.get("call_type") or "")
        if call_type == "think":
            call_input = call.get("input") or {}
            if isinstance(call_input, dict):
                context = call_input.get("context")
                if isinstance(context, dict):
                    return context
                return call_input

    return payload


async def _is_agentic_heartbeat_enabled(conn) -> bool:
    """Check if the agentic heartbeat loop is enabled via config."""
    try:
        val = await conn.fetchval("SELECT get_config('heartbeat.use_agentic_loop')")
        if val is None:
            return False
        if isinstance(val, str):
            return val.strip().lower() in ("true", "1", "yes", "on")
        return bool(val)
    except Exception:
        return False


def create_heartbeat_handler(
    *,
    pool: asyncpg.Pool,
    bridge: RabbitMQBridge | None,
    tool_registry,
    call_processor: ExternalCallProcessor,
    stop_callback: Any = None,
):
    """Factory that returns a heartbeat event handler for the GatewayConsumer.

    The handler receives a GatewayEvent whose payload is the full run_heartbeat()
    output and executes the heartbeat (agentic or legacy path).
    """

    async def _publish_outbox(messages: list[dict]) -> None:
        if not messages or not bridge:
            return
        await bridge.publish_outbox_payloads(messages)

    async def handle_heartbeat(event: GatewayEvent) -> dict[str, Any] | None:
        payload = event.payload
        heartbeat_id = payload.get("heartbeat_id")
        if heartbeat_id:
            logger.info(f"Consumer executing heartbeat: {heartbeat_id}")

        # Publish outbox messages from initialization
        outbox_messages = payload.get("outbox_messages")
        if isinstance(outbox_messages, list):
            await _publish_outbox(outbox_messages)

        async with pool.acquire() as conn:
            # Agentic heartbeat path
            if await _is_agentic_heartbeat_enabled(conn) and tool_registry:
                if not heartbeat_id:
                    logger.warning("Agentic heartbeat: no heartbeat_id in payload")
                    return {"path": "agentic", "note": "no heartbeat_id"}

                context = _extract_heartbeat_context(payload)

                try:
                    result = await run_agentic_heartbeat(
                        conn,
                        pool=pool,
                        registry=tool_registry,
                        heartbeat_id=str(heartbeat_id),
                        context=context,
                    )
                    logger.info(
                        "Agentic heartbeat %s completed: %d tools, %d energy, reason=%s",
                        str(heartbeat_id)[:8],
                        len(result.get("tool_calls_made", [])),
                        result.get("energy_spent", 0),
                        result.get("stopped_reason", "?"),
                    )
                except Exception as exc:
                    logger.error(f"Agentic heartbeat failed: {exc}")
                    result = {
                        "text": f"Heartbeat failed: {exc}",
                        "tool_calls_made": [],
                        "energy_spent": 0,
                        "stopped_reason": "error",
                    }

                try:
                    fin = await finalize_heartbeat(
                        conn,
                        heartbeat_id=str(heartbeat_id),
                        result=result,
                    )
                    outbox_messages = fin.get("outbox_messages")
                    if isinstance(outbox_messages, list):
                        await _publish_outbox(outbox_messages)
                except Exception as exc:
                    logger.error(f"Heartbeat finalization failed: {exc}")

                return {"path": "agentic", "stopped_reason": result.get("stopped_reason")}

            # Legacy heartbeat path
            external_calls = payload.get("external_calls")
            if not isinstance(external_calls, list):
                return {"path": "legacy", "note": "no external_calls"}

            for call in external_calls:
                if not isinstance(call, dict):
                    continue
                call_type = str(call.get("call_type") or "")
                call_input = call.get("input") or {}
                if not isinstance(call_input, dict):
                    call_input = {}
                try:
                    result = await call_processor.process_call_payload(conn, call_type, call_input)
                    applied = await call_processor.apply_result(conn, call, result)
                except Exception as exc:
                    logger.error(f"Error processing external call: {exc}")
                    continue

                if isinstance(applied, dict):
                    outbox_messages = applied.get("outbox_messages")
                    if isinstance(outbox_messages, list):
                        await _publish_outbox(outbox_messages)

                if (
                    isinstance(result, dict)
                    and result.get("kind") == "heartbeat_decision"
                    and "decision" in result
                    and heartbeat_id
                ):
                    exec_result = await execute_heartbeat_decision(
                        conn,
                        heartbeat_id=str(heartbeat_id),
                        decision=result["decision"],
                        call_processor=call_processor,
                        pre_executed_actions=result.get("rlm_repl_actions"),
                    )
                    if isinstance(exec_result, dict):
                        outbox_messages = exec_result.get("outbox_messages")
                        if isinstance(outbox_messages, list):
                            await _publish_outbox(outbox_messages)
                        if exec_result.get("terminated") is True:
                            logger.info("Termination executed; stopping workers.")
                            if stop_callback:
                                stop_callback()
                    return {"path": "legacy"}

            return {"path": "legacy"}

    return handle_heartbeat


# ---------------------------------------------------------------------------
# MaintenanceWorker — runs directly (atomic check+execute tasks)
# ---------------------------------------------------------------------------


class MaintenanceWorker:
    """Subconscious maintenance loop: consolidates/prunes substrate on its own trigger."""

    def __init__(self, instance: str | None = None):
        self.instance = instance or os.getenv("HEXIS_INSTANCE")
        self.pool: asyncpg.Pool | None = None
        self.running = False
        self.bridge: RabbitMQBridge | None = None
        self.tool_registry = None

    async def connect(self) -> None:
        self.pool = await asyncpg.create_pool(dsn=db_dsn_from_env(self.instance), min_size=1, max_size=5)
        logger.info("Connected to database")
        self.bridge = RabbitMQBridge(self.pool)
        await self.bridge.ensure_ready()

    async def disconnect(self) -> None:
        if self.pool:
            await self.pool.close()
            logger.info("Disconnected from database")

    async def _publish_outbox(self, messages: list[dict]) -> None:
        if not messages:
            return
        if self.bridge:
            await self.bridge.publish_outbox_payloads(messages)

    async def _run_outbox_delivery(self) -> None:
        """Drain the DB-native outbox (tool-queued user messages) to RabbitMQ.

        publish_outbox_payloads publishes in order and returns the count that
        succeeded (stopping at the first failure), so mark that prefix published
        and requeue the rest for the next tick.
        """
        if not self.pool or not self.bridge:
            return
        async with self.pool.acquire() as conn:
            raw = await conn.fetchval("SELECT claim_pending_outbox($1::int)", 50)
            claimed = json.loads(raw) if isinstance(raw, str) else (raw or [])
            if not claimed:
                return
            ids = [c["id"] for c in claimed]
            envelopes = [c["envelope"] for c in claimed]
            published = await self.bridge.publish_outbox_payloads(envelopes)
            if published > 0:
                await conn.fetchval("SELECT mark_outbox_published($1::uuid[])", ids[:published])
            if published < len(ids):
                await conn.fetchval("SELECT requeue_outbox($1::uuid[])", ids[published:])

    async def _run_scheduled_tasks(self) -> None:
        if not self.pool:
            return
        async with self.pool.acquire() as conn:
            payload = await run_scheduled_tasks(conn)
            if not isinstance(payload, dict):
                return
            outbox_messages = payload.get("outbox_messages")
            if isinstance(outbox_messages, list):
                await self._publish_outbox(outbox_messages)
            executed = payload.get("ran") or payload.get("executed_count") or payload.get("executed")
            ran_tasks = payload.get("ran_tasks") or []
            if executed:
                try:
                    await Gateway(self.pool).record(
                        EventSource.CRON,
                        "cron::scheduled",
                        {"executed": executed, "tasks": ran_tasks},
                    )
                except Exception:
                    logger.debug("Gateway record failed (non-fatal)", exc_info=True)
            # Recompute next_run_at for cron-expression tasks (cron_next_fire in the DB)
            cron_task_ids = payload.get("cron_task_ids") or []
            if cron_task_ids:
                try:
                    await recompute_cron_next_runs(conn, cron_task_ids)
                except Exception:
                    logger.warning("Cron recompute failed (non-fatal)", exc_info=True)

    async def _run_maintenance_if_due(self) -> None:
        if not self.pool:
            return
        async with self.pool.acquire() as conn:
            stats = await run_maintenance_if_due(conn, {})
            if stats is None:
                return
            if not stats.get("skipped"):
                logger.info(f"Subconscious maintenance: {stats}")
                try:
                    await Gateway(self.pool).record(
                        EventSource.MAINTENANCE,
                        "maintenance::consolidation",
                        {"stats": stats},
                    )
                except Exception:
                    logger.debug("Gateway record failed (non-fatal)", exc_info=True)

    async def _run_hmx_reembedding(self) -> None:
        if not self.pool:
            return
        async with self.pool.acquire() as conn:
            result = await run_hmx_reembed_step(conn)
            if not result.get("skipped"):
                logger.info("HMX re-embedding step: %s", result)

    async def _run_subconscious_if_due(self) -> None:
        if not self.pool:
            return
        async with self.pool.acquire() as conn:
            should_run = await should_run_subconscious_decider(conn)
            if not should_run:
                return
            result = await run_subconscious_decider(conn)
            await mark_subconscious_decider_run(conn)
            logger.info(f"Subconscious decider: {result}")

    async def _run_reconsolidation_if_pending(self) -> None:
        if not self.pool:
            return
        async with self.pool.acquire() as conn:
            has_pending = await conn.fetchval("SELECT has_pending_reconsolidation()")
            if not has_pending:
                return
            result = await run_reconsolidation_step(conn)
            if not result.get("skipped"):
                logger.info(f"Reconsolidation step: {result}")

    async def _run_ingestion_jobs(self) -> None:
        """Durable ingestion jobs (#87): the queue table is the state — no
        state-doc gate, matching outbox delivery."""
        try:
            from services.ingest.jobs import run_ingestion_jobs_step

            await run_ingestion_jobs_step(self.pool)
        except Exception:
            logger.exception("ingestion job step failed")

    async def _run_recmem_if_enabled(self) -> None:
        if not self.pool:
            return
        async with self.pool.acquire() as conn:
            embed_result = await run_recmem_embed_step(conn)
            if not embed_result.get("skipped"):
                logger.info("RecMem embed step: %s", embed_result)
            route_result = await run_recmem_route_step(conn)
            if not route_result.get("skipped"):
                logger.info("RecMem route step: %s", route_result)
            should_sweep = bool(await conn.fetchval("SELECT should_run_recmem_sweep()"))
            if should_sweep:
                sweep_result = await run_recmem_sweep_step(conn)
                await conn.fetchval("SELECT mark_recmem_sweep_run($1::jsonb)", json.dumps(sweep_result))
                if sweep_result.get("processed", 0):
                    logger.info("RecMem sweep step: %s", sweep_result)

            # Scene consolidation (#73): sessions gone quiet become one
            # episode_create task covering the whole conversation.
            if bool(await conn.fetchval("SELECT should_run_scene_consolidation()")):
                scene_result = await conn.fetchval("SELECT enqueue_scene_consolidations()")
                scene_doc = json.loads(scene_result) if isinstance(scene_result, str) else (scene_result or {})
                await conn.fetchval(
                    "SELECT mark_scene_consolidation_run($1::jsonb)", json.dumps(scene_doc)
                )
                if scene_doc.get("enqueued"):
                    logger.info("Scene consolidation: %s", scene_doc)

            task_batch_size = int(await conn.fetchval("SELECT COALESCE(get_config_int('memory.recmem_task_batch_size'), 3)") or 3)
            for _ in range(max(task_batch_size, 1)):
                result = await run_recmem_consolidation_step(conn)
                if result.get("skipped"):
                    break

    async def _run_memory_rest_if_enabled(self) -> None:
        """Drain the memory-consolidation summarization queue (LLM compaction +
        distill-upward). Consolidation/pruning themselves run in the DB maintenance
        pass; this only does the LLM step. No-op unless retention.enabled."""
        if not self.pool:
            return
        async with self.pool.acquire() as conn:
            if not bool(await conn.fetchval("SELECT COALESCE(get_config_bool('retention.enabled'), false)")):
                return
            result = await run_memory_summarization_step(conn)
            if not result.get("skipped"):
                logger.info("Memory summarization: %s", result)

    async def _run_extraction_if_enabled(self) -> None:
        """Sweep conscious episodes (chat turns + heartbeat episodes) into
        selective durable memories (#37). No-op unless extraction.enabled."""
        if not self.pool:
            return
        async with self.pool.acquire() as conn:
            result = await run_conscious_extraction_step(conn)
            if not result.get("skipped"):
                logger.info("Conscious extraction: %s", result)

    async def _run_origin_seed_if_enabled(self) -> None:
        """Keep origin memories seeded (#40). Idempotent and config-gated in
        the DB, so flipping origin_memories.enabled takes effect on the next
        tick — no manual SQL, no re-consent. Advisory: a failure (e.g. the
        embedding service being down) waits for the next tick, loudly."""
        if not self.pool:
            return
        try:
            async with self.pool.acquire() as conn:
                raw = await conn.fetchval("SELECT seed_origin_memories()")
            result = json.loads(raw) if isinstance(raw, str) else (raw or {})
            if result.get("seeded"):
                logger.info("Origin memories seeded: %s", result)
        except Exception as exc:
            logger.warning("Origin memory seeding failed (will retry next tick): %s", exc)

    async def _run_skill_improvement_if_due(self) -> None:
        if not self.pool:
            return
        async with self.pool.acquire() as conn:
            result = await run_skill_improvement_review_step(conn, registry=self.tool_registry)
            if not result.get("skipped"):
                logger.info("Skill-improvement review: %s", result)

    async def run(self) -> None:
        self.running = True
        logger.info("Maintenance worker starting...")
        await self.connect()
        try:
            while self.running:
                try:
                    if await self._is_agent_terminated():
                        logger.info("Agent is terminated; maintenance worker exiting.")
                        break
                    if not await self._is_agent_ready():
                        await asyncio.sleep(POLL_INTERVAL)
                        continue
                    if self.bridge:
                        await self.bridge.poll_inbox_messages()
                    await self._run_outbox_delivery()
                    await self._run_scheduled_tasks()
                    await self._run_hmx_reembedding()
                    await self._run_maintenance_if_due()
                    await self._run_subconscious_if_due()
                    await self._run_reconsolidation_if_pending()
                    await self._run_recmem_if_enabled()
                    await self._run_memory_rest_if_enabled()
                    await self._run_extraction_if_enabled()
                    await self._run_origin_seed_if_enabled()
                    await self._run_skill_improvement_if_due()
                    await self._run_ingestion_jobs()
                except Exception as exc:
                    logger.error(f"Maintenance loop error: {exc}")
                await asyncio.sleep(POLL_INTERVAL)
        finally:
            await self.disconnect()

    def stop(self) -> None:
        self.running = False
        logger.info("Maintenance worker stopping...")

    async def _is_agent_terminated(self) -> bool:
        if not self.pool:
            return False
        try:
            async with self.pool.acquire() as conn:
                return await is_agent_terminated(conn)
        except Exception:
            return False

    async def _is_agent_ready(self) -> bool:
        if not self.pool:
            return False
        try:
            async with self.pool.acquire() as conn:
                return bool(await conn.fetchval("SELECT is_agent_configured() AND is_init_complete()"))
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Webhook event handler — registered with GatewayConsumer
# ---------------------------------------------------------------------------


def create_webhook_handler(*, pool: asyncpg.Pool):
    """Factory that returns a webhook event handler for the GatewayConsumer.

    Webhook events are recorded as episodic memories so the agent
    is aware that an external system sent a notification.
    """

    async def handle_webhook(event: GatewayEvent) -> dict[str, Any] | None:
        payload = event.payload
        source_name = event.session_key.removeprefix("webhook:")
        logger.info("Processing webhook event: %s (id=%d)", source_name, event.id)

        # Record the webhook as an episodic memory
        try:
            async with pool.acquire() as conn:
                summary = json.dumps(payload)[:500] if payload else "{}"
                await conn.fetchval(
                    """
                    SELECT create_episodic_memory(
                        p_content := $1,
                        p_importance := 0.4,
                        p_emotional_valence := 0.0,
                        p_context := $2::jsonb,
                        p_source_attribution := $3::jsonb,
                        p_trust_level := 0.7
                    )
                    """,
                    f"Received webhook from {source_name}: {summary}",
                    json.dumps({"type": "webhook", "source": source_name}),
                    json.dumps({
                        "kind": "webhook",
                        "ref": str(event.correlation_id),
                        "label": f"webhook:{source_name}",
                        "trust": 0.7,
                    }),
                )
        except Exception as exc:
            logger.warning("Failed to record webhook memory: %s", exc)

        return {"source": source_name, "recorded": True}

    return handle_webhook


# ---------------------------------------------------------------------------
# Main entrypoint — wires timer + consumer + maintenance
# ---------------------------------------------------------------------------


async def _amain(mode: str, instance: str | None = None) -> None:
    hb_timer = HeartbeatWorker(instance)
    maint_worker = MaintenanceWorker(instance)

    # Shared resources for the heartbeat consumer
    dsn = db_dsn_from_env(instance)
    # Bring the schema up to date before doing anything (advisory-locked, idempotent,
    # no-op if already current). Never wipes data.
    try:
        from core.agent_api import apply_migrations
        applied = await apply_migrations(dsn)
        if applied:
            logger.info("applied %d schema migration(s) on startup: %s", len(applied), applied)
    except Exception as exc:
        logger.warning("startup migration check failed (continuing): %s", exc)
    consumer_pool = await asyncpg.create_pool(dsn, min_size=2, max_size=10)
    from core.usage import set_usage_pool
    set_usage_pool(consumer_pool)
    try:
        from core.agent_api import record_build_change
        async with consumer_pool.acquire() as conn:
            await record_build_change(conn, "worker")
    except Exception:
        logger.debug("build-change journaling failed", exc_info=True)
    bridge = RabbitMQBridge(consumer_pool)
    await bridge.ensure_ready()

    # Initialize tool registry for heartbeat execution
    tool_registry = None
    mcp_manager = None
    call_processor = ExternalCallProcessor(max_retries=MAX_RETRIES)
    try:
        from core.tools import create_default_registry, create_mcp_manager

        tool_registry = create_default_registry(consumer_pool)
        maint_worker.tool_registry = tool_registry
        call_processor.set_tool_registry(tool_registry)
        logger.info("Tool registry initialized for consumer")

        # Skill-gated MCP (#41, default): servers connect lazily when a skill
        # binding them is activated, so nothing eager-loads here. The legacy
        # eager mode remains behind mcp.skill_gated=false.
        async with consumer_pool.acquire() as conn:
            skill_gated = bool(await conn.fetchval(
                "SELECT COALESCE(get_config_bool('mcp.skill_gated'), TRUE)"
            ))
        if skill_gated:
            logger.info("MCP is skill-gated; servers connect on skill activation")
        else:
            mcp_manager = await create_mcp_manager(tool_registry)
            mcp_count = len(mcp_manager.list_servers())
            if mcp_count > 0:
                logger.info(f"Loaded {mcp_count} MCP server(s)")
    except Exception as e:
        logger.warning(f"Failed to initialize tool registry: {e}")

    # Build the gateway consumer
    consumer = GatewayConsumer(consumer_pool, poll_interval=POLL_INTERVAL)

    def _stop_all():
        hb_timer.stop()
        maint_worker.stop()
        consumer.stop()

    heartbeat_handler = create_heartbeat_handler(
        pool=consumer_pool,
        bridge=bridge,
        tool_registry=tool_registry,
        call_processor=call_processor,
        stop_callback=_stop_all,
    )
    consumer.register(EventSource.HEARTBEAT, heartbeat_handler)
    consumer.register(EventSource.WEBHOOK, create_webhook_handler(pool=consumer_pool))

    import signal

    def shutdown(signum, frame):
        _stop_all()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    mode = (mode or "both").strip().lower()
    instance_info = f" (instance: {instance})" if instance else ""
    logger.info(f"Starting worker in {mode} mode{instance_info}")

    try:
        if mode == "heartbeat":
            await asyncio.gather(hb_timer.run(), consumer.run())
        elif mode == "maintenance":
            await maint_worker.run()
        elif mode == "both":
            await asyncio.gather(hb_timer.run(), consumer.run(), maint_worker.run())
        else:
            raise ValueError("mode must be one of: heartbeat, maintenance, both")
    finally:
        # Cleanup consumer resources
        if mcp_manager:
            try:
                await mcp_manager.shutdown()
            except Exception:
                pass
        try:
            from core.tools.mcp_runtime import MCPRuntime
            await MCPRuntime.instance().shutdown()
        except Exception:
            pass
        await consumer_pool.close()


def main() -> int:
    p = argparse.ArgumentParser(prog="hexis-worker", description="Run Hexis background workers.")
    p.add_argument(
        "--mode",
        choices=["heartbeat", "maintenance", "both"],
        default=os.getenv("HEXIS_WORKER_MODE", "both"),
        help="Which worker to run.",
    )
    p.add_argument(
        "--instance", "-i",
        default=os.getenv("HEXIS_INSTANCE"),
        help="Target a specific instance (overrides HEXIS_INSTANCE env var).",
    )
    args = p.parse_args()
    asyncio.run(_amain(args.mode, args.instance))
    return 0


__all__ = [
    "HeartbeatWorker",
    "MaintenanceWorker",
    "GatewayConsumer",
    "create_heartbeat_handler",
    "create_webhook_handler",
    "main",
    "MAX_RETRIES",
]
