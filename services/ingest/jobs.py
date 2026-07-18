"""Durable ingestion-job consumer (#87).

The maintenance worker calls run_ingestion_jobs_step() each tick; the queue
table (db/73) is the state — claim with SKIP LOCKED + stale reclaim, progress
heartbeats that extend the claim and surface cancellation, exponential-backoff
failure, and completion with the memory count. The pipeline itself resumes
any partial document via receipts (#85), so a reclaimed job never redoes
finished sections.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_PROGRESS_INTERVAL_SECONDS = 15.0


async def _process_job(pool, job: dict[str, Any], *, config_override=None) -> None:
    from core.tools.ingest import _build_ingest_config

    from .config import IngestionMode
    from .pipeline import IngestionPipeline

    job_id = job["id"]
    payload = job.get("payload") or {}
    if isinstance(payload, str):
        payload = json.loads(payload)
    mode_value = str(payload.get("mode") or "fast")

    cancel_flag = {"set": False}
    if config_override is not None:
        config = config_override
    else:
        config = await _build_ingest_config(pool, mode=IngestionMode(mode_value))
    config.verbose = False
    config.cancel_check = lambda: cancel_flag["set"]
    pipeline = IngestionPipeline(config)

    async def _ingest() -> int:
        if job["kind"] == "url":
            return await pipeline.ingest_url(
                str(payload.get("url") or ""), title=payload.get("title")
            )
        return await pipeline.ingest_text(
            job.get("content") or "",
            title=payload.get("title"),
            source_type=str(payload.get("source_type") or "pasted_text"),
        )

    try:
        task = asyncio.ensure_future(_ingest())
        while True:
            done, _pending = await asyncio.wait({task}, timeout=_PROGRESS_INTERVAL_SECONDS)
            if done:
                break
            # Heartbeat: extends the stale-claim window and carries back the
            # cancel request in one round trip.
            async with pool.acquire() as conn:
                cancel_requested = await conn.fetchval(
                    "SELECT update_ingestion_job_progress($1::uuid, '{}'::jsonb)", job_id
                )
            if cancel_requested:
                cancel_flag["set"] = True

        count = task.result()
        async with pool.acquire() as conn:
            await conn.fetchval(
                "SELECT complete_ingestion_job($1::uuid, $2::jsonb)",
                job_id,
                json.dumps({"memories_created": count}),
            )
        logger.info("ingestion job %s completed: %s memories", job_id, count)
    except Exception as exc:
        async with pool.acquire() as conn:
            outcome_raw = await conn.fetchval(
                "SELECT fail_ingestion_job($1::uuid, $2)", job_id, str(exc)[:2000]
            )
        outcome = json.loads(outcome_raw) if isinstance(outcome_raw, str) else outcome_raw
        logger.error(
            "ingestion job %s failed (%s): %s", job_id, (outcome or {}).get("status"), exc
        )
    finally:
        await pipeline.close()


async def run_ingestion_jobs_step(pool, *, config_override=None) -> int:
    """Claim and process due ingestion jobs; returns how many were handled."""
    async with pool.acquire() as conn:
        raw = await conn.fetchval("SELECT claim_ingestion_jobs()")
    jobs = json.loads(raw) if isinstance(raw, str) else (raw or [])
    for job in jobs:
        await _process_job(pool, job, config_override=config_override)
    return len(jobs)
