"""Tests for the schema-migration runner (core/migrations.py) + the inaugural HMX
Slice 0 migrations. The load-bearing test is the non-destructive proof: an EXISTING
database with real data evolves to the new schema WITHOUT a wipe."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

pytestmark = [pytest.mark.asyncio(loop_scope="session")]

_DB_ROOT = Path(__file__).resolve().parents[2] / "db"


def _admin_dsn(dbname: str) -> str:
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "43815")
    user = os.getenv("POSTGRES_USER", "hexis_user")
    pw = os.getenv("POSTGRES_PASSWORD", "hexis_password")
    return f"postgresql://{user}:{pw}@{host}:{port}/{dbname}"


async def test_migrations_recorded_and_idempotent(db_pool):
    """conftest already migrated this DB; the deltas are recorded and re-runs no-op."""
    from core.migrations import apply_pending_migrations, migration_status

    async with db_pool.acquire() as conn:
        st = await migration_status(conn)
        assert "0001_hmx_enum_values" in st["applied"]
        assert "0002_hmx_supersedes_lineage" in st["applied"]
        assert "0004_hmx_export_functions" in st["applied"]
        assert "0005_hmx_narrative_export_ids" in st["applied"]
        assert "0006_hmx_optional_export_sections" in st["applied"]
        assert "0007_hmx_additive_import" in st["applied"]
        assert "0008_hmx_protected_import" in st["applied"]
        assert st["pending"] == []
        assert await apply_pending_migrations(conn) == []  # nothing left to do
        # the deltas are live
        assert await conn.fetchval("SELECT 'staged'::memory_status::text") == "staged"
        assert (
            await conn.fetchval("SELECT 'SUPERSEDES'::graph_edge_type::text")
            == "SUPERSEDES"
        )
        assert await conn.fetchval(
            "SELECT value IS NOT NULL FROM config WHERE key='agent.lineage_id'"
        )


async def test_migrate_existing_database_preserves_data():
    """Build a DB from the BASELINE only (an 'old' deployment), give it real data,
    then run the migrator: the data survives and the new schema is present."""
    admin_db = os.getenv("POSTGRES_ADMIN_DB", "postgres")
    scratch = f"tmp_mig_{uuid4().hex}"

    admin = await asyncpg.connect(_admin_dsn(admin_db))
    try:
        await admin.execute(f'CREATE DATABASE "{scratch}"')
    finally:
        await admin.close()

    try:
        conn = await asyncpg.connect(_admin_dsn(scratch))
        try:
            # baseline only — NO migrations (simulates a pre-migration instance)
            for path in sorted(_DB_ROOT.glob("*.sql"), key=lambda p: p.name):
                await conn.execute(path.read_text(encoding="utf-8"))
            await conn.execute("LOAD 'age'")
            await conn.execute("SET search_path = ag_catalog, public")

            await conn.execute(
                "INSERT INTO memories (type, content, embedding, importance, trust_level, status) "
                "VALUES ('episodic','precious pre-migration data', "
                "        array_fill(0.1, ARRAY[embedding_dimension()])::vector, 0.5, 0.9, 'active')"
            )

            # NOTE: deltas are mirrored into the baseline (db/migrations/README.md),
            # so a current-baseline DB may already contain them. The load-bearing
            # invariant is that the runner applies every migration on a DB with an
            # empty schema_migrations table, the data survives, and re-runs no-op.
            from core.migrations import apply_pending_migrations

            applied = await apply_pending_migrations(conn)
            assert "0001_hmx_enum_values" in applied
            assert "0002_hmx_supersedes_lineage" in applied
            assert "0003_hmx_bootstrap_provenance" in applied
            assert "0004_hmx_export_functions" in applied
            assert "0005_hmx_narrative_export_ids" in applied
            assert "0006_hmx_optional_export_sections" in applied
            assert "0007_hmx_additive_import" in applied
            assert "0008_hmx_protected_import" in applied

            # AFTER: the data is intact AND the schema evolved
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM memories WHERE content='precious pre-migration data'"
                )
                == 1
            )
            # the enum value is now usable on the surviving row (proves the delta landed)
            await conn.execute(
                "UPDATE memories SET status='staged' WHERE content='precious pre-migration data'"
            )
            assert await conn.fetchval(
                "SELECT value IS NOT NULL FROM config WHERE key='agent.lineage_id'"
            )
            assert await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM ag_catalog.ag_label WHERE name='SUPERSEDES')"
            )
            # 0003's backfill classified the pre-migration row as lived experience
            assert (
                await conn.fetchval(
                    "SELECT metadata->'provenance'->>'acquisition_mode' FROM memories "
                    "WHERE content='precious pre-migration data'"
                )
                == "experienced"
            )

            # idempotent: a second run does nothing
            assert await apply_pending_migrations(conn) == []
        finally:
            await conn.close()
    finally:
        admin = await asyncpg.connect(_admin_dsn(admin_db))
        try:
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                f"WHERE datname='{scratch}' AND pid <> pg_backend_pid()"
            )
            await admin.execute(f'DROP DATABASE IF EXISTS "{scratch}"')
        finally:
            await admin.close()
