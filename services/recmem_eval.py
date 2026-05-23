"""Deterministic retrieval eval harness for RecMem rollout gates."""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

import asyncpg

from core.agent_api import db_dsn_from_env, pool_sizes_from_env


def _coerce_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


async def run_recmem_eval_set(
    conn: asyncpg.Connection,
    eval_set: str,
    *,
    label: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Run a retrieval-only eval set through the DB-owned eval harness."""
    raw = await conn.fetchval(
        "SELECT run_recmem_eval_set($1::text, $2::text, $3::int)",
        eval_set,
        label,
        int(limit),
    )
    result = _coerce_json(raw)
    return dict(result) if isinstance(result, dict) else {}


async def _amain(args: argparse.Namespace) -> int:
    dsn = args.dsn or db_dsn_from_env()
    min_size, max_size = pool_sizes_from_env(1, 3)
    pool = await asyncpg.create_pool(dsn, min_size=min_size, max_size=max_size)
    try:
        async with pool.acquire() as conn:
            summary = await run_recmem_eval_set(
                conn,
                args.eval_set,
                label=args.label,
                limit=args.limit,
            )
        print(json.dumps(summary, default=str, indent=2))
        return 0
    finally:
        await pool.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a persisted RecMem retrieval eval set.")
    parser.add_argument("eval_set", help="Eval set name or UUID")
    parser.add_argument("--label", help="Optional eval run label")
    parser.add_argument("--limit", type=int, default=10, help="Candidate limit per retrieval path")
    parser.add_argument("--dsn", help="Postgres DSN; defaults to Hexis DB environment")
    return parser


def main() -> int:
    return asyncio.run(_amain(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
