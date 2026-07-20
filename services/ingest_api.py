from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any, AsyncIterator
from uuid import uuid4

from core.agent_api import db_dsn_from_env
from services.ingest import Config, IngestionPipeline

# Cancel events stay threading.Events: is_set() is loop-agnostic and the
# public contract (create/cancel from any thread) is unchanged.
_INGESTION_CANCEL: dict[str, threading.Event] = {}
_CANCEL_LOCK = threading.Lock()


def create_ingestion_session() -> str:
    session_id = str(uuid4())
    with _CANCEL_LOCK:
        _INGESTION_CANCEL[session_id] = threading.Event()
    return session_id


def cancel_ingestion(session_id: str) -> None:
    with _CANCEL_LOCK:
        event = _INGESTION_CANCEL.get(session_id)
    if event:
        event.set()


async def stream_ingestion(
    *,
    session_id: str,
    path: str,
    recursive: bool,
    llm_config: dict[str, Any],
    mode: str | None = None,
    min_importance: float | None = None,
    permanent: bool = False,
    base_trust: float | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Stream ingestion log lines as {"type": "log", "text": ...} events.

    Pure-async internals (#88): the pipeline runs as a task on this loop and
    logs flow through an asyncio.Queue — the daemon thread and its queue.Queue
    died with the sync pipeline. The event shape and cancel contract are
    unchanged.
    """
    dsn = db_dsn_from_env()
    with _CANCEL_LOCK:
        cancel_event = _INGESTION_CANCEL.get(session_id) or threading.Event()
        _INGESTION_CANCEL[session_id] = cancel_event
    log_queue: asyncio.Queue[str | None] = asyncio.Queue()

    def log(message: str) -> None:
        # The pipeline calls this from coroutine context on this same loop.
        log_queue.put_nowait(message)

    async def run() -> None:
        config = Config(
            dsn=dsn,
            llm_config=llm_config,
            mode=mode or "fast",
            min_importance_floor=min_importance,
            permanent=permanent,
            base_trust=base_trust,
            acquisition="user",
            verbose=True,
            log=log,
            cancel_check=cancel_event.is_set,
        )
        pipeline = IngestionPipeline(config)
        try:
            target = Path(path)
            if target.is_dir():
                await pipeline.ingest_directory(target, recursive=recursive)
            else:
                await pipeline.ingest_file(target)
            pipeline.print_stats()
        except Exception as exc:
            log(f"Error: {exc}")
        finally:
            # The end-of-stream sentinel must reach the consumer even when
            # close() itself fails — a missing sentinel hangs the stream.
            try:
                await pipeline.close()
            except Exception as exc:
                log(f"Error closing pipeline: {exc}")
            finally:
                log_queue.put_nowait(None)

    task = asyncio.create_task(run())
    try:
        while True:
            line = await log_queue.get()
            if line is None:
                break
            yield {"type": "log", "text": line}
        await task
    finally:
        if not task.done():
            task.cancel()
        with _CANCEL_LOCK:
            _INGESTION_CANCEL.pop(session_id, None)
