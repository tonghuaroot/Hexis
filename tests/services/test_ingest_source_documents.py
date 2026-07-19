from __future__ import annotations

import os

import pytest

from services.ingest import Config, IngestionPipeline, _hash_text
from tests.utils import _db_dsn, get_test_identifier

pytestmark = [pytest.mark.asyncio(loop_scope="session")]


async def test_ingest_text_stores_raw_source_before_receipt_skip(db_pool):
    marker = get_test_identifier("ingestraw")
    content = f"# Backfill Raw {marker}\n\nThe archived raw source survives even when extraction is already receipted."
    content_hash = _hash_text(content)
    source_path = f"text:{content_hash[:12]}"

    async with db_pool.acquire() as conn:
        await conn.execute(
            "SELECT record_ingestion_receipt($1, $1, NULL, 0, $2)",
            content_hash,
            source_path,
        )

    config = Config(
        dsn=_db_dsn(os.environ.get("POSTGRES_DB")),
        llm_config={"provider": "openai", "model": "stub", "api_key": "stub"},
        verbose=False,
    )
    pipeline = IngestionPipeline(config)
    try:
        count = await pipeline.ingest_text(content, title=f"Backfill Raw {marker}")
    finally:
        await pipeline.close()

    try:
        assert count == 0
        async with db_pool.acquire() as conn:
            stored = await conn.fetchrow(
                "SELECT title, content, path FROM source_documents WHERE content_hash = $1",
                content_hash,
            )
        assert stored is not None
        assert stored["title"] == f"Backfill Raw {marker}"
        assert stored["content"] == content
        assert stored["path"] == source_path
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM source_documents WHERE content_hash = $1", content_hash)
            await conn.execute("DELETE FROM ingestion_receipts WHERE doc_ref = $1", content_hash)
