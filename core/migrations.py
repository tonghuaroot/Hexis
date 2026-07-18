"""Database migrations for Hexis.

The schema BASELINE lives in ``db/*.sql`` (applied by Postgres on a fresh volume).
Forward schema changes for EXISTING databases are deltas in
``db/migrations/NNNN_slug.sql``, applied here idempotently and recorded in a
``schema_migrations`` table. The runner:

* is **advisory-locked**, so concurrent workers / CLI / API serialize and never
  double-apply;
* runs **everywhere** (fresh + existing + tests) — "current = baseline + migrations";
* **no-ops** when the migrations directory is absent (so containers that don't
  ship it don't error).

Directive: a migration whose leading comment block contains
``-- migrate:no-transaction`` is executed statement-by-statement in autocommit,
which is required for ``ALTER TYPE ... ADD VALUE`` (it cannot run inside a
transaction block). Such migrations must be simple (``;``-separated statements,
no ``$$`` blocks). Every other migration runs atomically inside one transaction.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path

import asyncpg

logger = logging.getLogger("migrations")

# db/migrations lives next to db/*.sql (this file is core/migrations.py).
MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "db" / "migrations"

# Fixed key so every runner (worker, API, CLI) contends for the same lock.
_ADVISORY_LOCK_KEY = 0x48584D31  # "HXM1"

_NO_TX_DIRECTIVE = re.compile(r"^\s*--\s*migrate:\s*no-transaction\b", re.IGNORECASE)


async def _ensure_table(conn: asyncpg.Connection) -> str:
    """Return the qualified bookkeeping table, preserving legacy installs.

    Older runners put ``ag_catalog`` first on ``search_path``, so some live
    databases created the table there. Never create a second empty table and
    replay migrations just because a later migration changed search_path.
    """

    if await conn.fetchval(
        "SELECT to_regclass('public.schema_migrations') IS NOT NULL"
    ):
        return "public.schema_migrations"
    if await conn.fetchval(
        "SELECT to_regclass('ag_catalog.schema_migrations') IS NOT NULL"
    ):
        # Self-heal (#77 homecoming): the ledger belongs in public. Only the
        # runner can move its own table — a migration moving it mid-run pulls
        # the floor out from under the INSERT that records that migration.
        await conn.execute(
            "ALTER TABLE ag_catalog.schema_migrations SET SCHEMA public"
        )
        logger.info("moved schema_migrations from ag_catalog to public (#77)")
        return "public.schema_migrations"

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS public.schema_migrations (
            version      TEXT PRIMARY KEY,
            applied_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            checksum     TEXT,
            execution_ms INTEGER
        )
        """)
    return "public.schema_migrations"


def _list_migration_files(migrations_dir: Path) -> list[Path]:
    if not migrations_dir.exists():
        return []
    return sorted(
        (p for p in migrations_dir.glob("*.sql") if p.is_file()), key=lambda p: p.name
    )


def _migration_summary(sql: str) -> str:
    """First header-comment line of a migration — its own one-line story."""
    for line in sql.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            text = stripped.lstrip("-").strip()
            if text:
                return text[:200]
        elif stripped and not stripped.upper().startswith("SET "):
            break
    return "schema migration"


def _is_no_transaction(sql: str) -> bool:
    """True if the leading comment block declares the no-transaction directive."""
    for line in sql.splitlines()[:8]:
        if _NO_TX_DIRECTIVE.match(line):
            return True
        if line.strip() and not line.strip().startswith("--"):
            break  # reached real SQL before any directive
    return False


def _split_statements(sql: str) -> list[str]:
    """Split a (simple, ``$$``-free) migration into individual statements, dropping
    full-line comments. Only used for no-transaction migrations."""
    body = "\n".join(ln for ln in sql.splitlines() if not ln.strip().startswith("--"))
    return [s.strip() for s in body.split(";") if s.strip()]


async def _prepare_conn(conn: asyncpg.Connection) -> None:
    try:
        await conn.execute("LOAD 'age'")
    except Exception:
        pass  # age not loadable in some contexts; non-AGE migrations don't need it
    await conn.execute('SET search_path = public, ag_catalog, "$user"')


async def migrations_table_name(conn: asyncpg.Connection) -> str:
    """Return the qualified migration table used by this database."""

    return await _ensure_table(conn)


async def apply_pending_migrations(
    conn: asyncpg.Connection, *, migrations_dir: Path | None = None
) -> list[str]:
    """Apply every migration not yet in ``schema_migrations``, in filename order.

    Returns the list of versions applied this call ([] if none / dir absent).
    Advisory-locked and idempotent — safe to call from many places on startup.
    """
    migrations_dir = migrations_dir or MIGRATIONS_DIR
    files = _list_migration_files(migrations_dir)
    if not files:
        return []

    await conn.execute("SELECT pg_advisory_lock($1)", _ADVISORY_LOCK_KEY)
    applied: list[str] = []
    try:
        table = await _ensure_table(conn)
        done = {r["version"] for r in await conn.fetch(f"SELECT version FROM {table}")}
        for path in files:
            version = path.stem  # e.g. "0001_hmx_enum_values"
            if version in done:
                continue
            sql = path.read_text(encoding="utf-8")
            checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
            start = time.monotonic()
            await _prepare_conn(conn)
            if _is_no_transaction(sql):
                # each statement commits on its own; re-runs are safe (idempotent SQL)
                for stmt in _split_statements(sql):
                    await conn.execute(stmt)
                await conn.execute(
                    f"INSERT INTO {table} (version, checksum, execution_ms) VALUES ($1, $2, $3)",
                    version,
                    checksum,
                    int((time.monotonic() - start) * 1000),
                )
            else:
                async with conn.transaction():
                    await conn.execute(sql)
                    await conn.execute(
                        f"INSERT INTO {table} (version, checksum, execution_ms) VALUES ($1, $2, $3)",
                        version,
                        checksum,
                        int((time.monotonic() - start) * 1000),
                    )
            logger.info(
                "applied migration %s (%dms)",
                version,
                int((time.monotonic() - start) * 1000),
            )
            # Change legibility (#93): each applied migration lands in the
            # agent-readable journal, summarized by its own header comment.
            # Advisory — the journal function first exists partway through
            # the migration series, and its absence must never fail an apply.
            try:
                await conn.execute(
                    "SELECT record_change('migration', $1, $2::jsonb)",
                    f"{version}: {_migration_summary(sql)}",
                    json.dumps({"version": version, "checksum": checksum}),
                )
            except Exception:
                logger.debug("change_journal unavailable for %s", version, exc_info=True)
            applied.append(version)

        # Postcondition (#77): the schema this runner just wrote must be the
        # schema connections resolve. Stale ag_catalog twins shadowed public
        # for months once — detect and evict them loudly on every run.
        # Inline catalog SQL (no dependency on any migration having run).
        strays = await conn.fetch(
            """
            SELECT p.proname,
                   pg_get_function_identity_arguments(p.oid) AS args,
                   p.prokind
            FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = 'ag_catalog'
              AND EXISTS (
                  SELECT 1 FROM pg_proc p2
                  JOIN pg_namespace n2 ON n2.oid = p2.pronamespace
                  WHERE n2.nspname = 'public' AND p2.proname = p.proname
              )
            """
        )
        for stray in strays:
            kind = "PROCEDURE" if stray["prokind"] == "p" else "FUNCTION"
            await conn.execute(
                f'DROP {kind} IF EXISTS ag_catalog."{stray["proname"]}"({stray["args"]})'
            )
            logger.error(
                "schema guard: evicted stray ag_catalog.%s(%s) — it shadowed the "
                "migrated public version for every runtime connection (#77)",
                stray["proname"],
                stray["args"],
            )
    finally:
        await conn.execute("SELECT pg_advisory_unlock($1)", _ADVISORY_LOCK_KEY)
    return applied


async def migration_status(
    conn: asyncpg.Connection, *, migrations_dir: Path | None = None
) -> dict:
    """{'applied': [...], 'pending': [...]} in filename order."""
    migrations_dir = migrations_dir or MIGRATIONS_DIR
    table = await _ensure_table(conn)
    done = {r["version"] for r in await conn.fetch(f"SELECT version FROM {table}")}
    versions = [p.stem for p in _list_migration_files(migrations_dir)]
    return {
        "applied": [v for v in versions if v in done],
        "pending": [v for v in versions if v not in done],
    }
