"""Operator helpers for RecMem rollout phases and readiness checks."""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

import asyncpg

from core.agent_api import db_dsn_from_env, pool_sizes_from_env
from services.recmem_eval import run_recmem_eval_set


def _coerce_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


async def get_recmem_rollout_status(
    conn: asyncpg.Connection,
    *,
    eval_run_id: str | None = None,
) -> dict[str, Any]:
    """Return config, health, metrics, and Phase 5 readiness from SQL."""
    raw = await conn.fetchval("SELECT get_recmem_rollout_status($1::uuid)", eval_run_id)
    result = _coerce_json(raw)
    return dict(result) if isinstance(result, dict) else {}


async def apply_recmem_rollout_phase(
    conn: asyncpg.Connection,
    phase: int,
    *,
    eval_run_id: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Apply a named rollout phase through the DB-owned readiness gate."""
    try:
        async with conn.transaction():
            raw = await conn.fetchval(
                "SELECT apply_recmem_rollout_phase($1::int, $2::uuid, $3::boolean)",
                int(phase),
                eval_run_id,
                bool(force),
            )
    except asyncpg.PostgresError as exc:
        message = str(exc)
        if "Unknown RecMem rollout phase" in message:
            raise ValueError(message) from exc
        if "requires a passing readiness gate" in message:
            raise RuntimeError(message) from exc
        raise
    result = _coerce_json(raw)
    return dict(result) if isinstance(result, dict) else {}


async def _with_conn(dsn: str, coro_name: str, *args: Any, **kwargs: Any) -> Any:
    min_size, max_size = pool_sizes_from_env(1, 3)
    pool = await asyncpg.create_pool(dsn, min_size=min_size, max_size=max_size)
    try:
        async with pool.acquire() as conn:
            if coro_name == "status":
                return await get_recmem_rollout_status(conn, **kwargs)
            if coro_name == "phase":
                return await apply_recmem_rollout_phase(conn, *args, **kwargs)
            if coro_name == "eval":
                return await run_recmem_eval_set(conn, *args, **kwargs)
            raise ValueError(f"Unknown RecMem rollout command: {coro_name}")
    finally:
        await pool.close()


def print_recmem_status_text(payload: dict[str, Any]) -> None:
    configs = payload.get("configs") or {}
    health = payload.get("health") or {}
    readiness = payload.get("phase5_readiness") or {}
    print(f"RecMem phase: {payload.get('phase') if payload.get('phase') is not None else 'custom'}")
    print(f"Raw ingest: {configs.get('memory.recmem_enabled')}")
    print(f"Eager memory: {configs.get('chat.eager_memory_enabled')}")
    print(f"Worker: {configs.get('memory.recmem_worker_enabled')}")
    print(f"Hydrate: {configs.get('memory.recmem_hydrate_enabled')}")
    print(f"Dual-write comparison: {configs.get('memory.recmem_dual_write_compare')}")
    print(f"Active raw units: {health.get('active_raw_units', 0)}")
    print(f"Pending embeddings: {health.get('pending_embeddings', 0)}")
    print(f"Pending routes: {health.get('pending_routes', 0)}")
    print(f"Pending tasks: {health.get('pending_tasks', 0)}")
    print(f"Unhealthy tasks/items: {readiness.get('unhealthy_count', 'unknown')}")
    print(f"Phase 5 ready: {readiness.get('ready', False)}")
    if readiness.get("recommendation"):
        print(f"Recommendation: {readiness['recommendation']}")
    elif readiness.get("reason"):
        print(f"Reason: {readiness['reason']}")


async def _amain(args: argparse.Namespace) -> int:
    dsn = args.dsn or db_dsn_from_env()
    if args.command == "status":
        payload = await _with_conn(dsn, "status", eval_run_id=args.eval_run_id)
        if args.json:
            print(json.dumps(payload, default=str, indent=2, sort_keys=True))
        else:
            print_recmem_status_text(payload)
        return 0

    if args.command == "phase":
        payload = await _with_conn(
            dsn,
            "phase",
            args.phase,
            eval_run_id=args.eval_run_id,
            force=args.force,
        )
        if args.json:
            print(json.dumps(payload, default=str, indent=2, sort_keys=True))
        else:
            print(f"Applied RecMem phase {args.phase}")
            print_recmem_status_text(payload)
        return 0

    if args.command == "eval":
        payload = await _with_conn(
            dsn,
            "eval",
            args.eval_set,
            label=args.label,
            limit=args.limit,
        )
        print(json.dumps(payload, default=str, indent=2, sort_keys=True))
        return 0

    raise ValueError(f"Unsupported command: {args.command}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage RecMem rollout phases and readiness.")
    parser.add_argument("--dsn", help="Postgres DSN; defaults to Hexis DB environment")
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="Show RecMem rollout status")
    status.add_argument("--json", action="store_true", help="Output JSON")
    status.add_argument("--eval-run-id", help="Eval run UUID to use for Phase 5 readiness")

    phase = sub.add_parser("phase", help="Apply a RecMem rollout phase")
    phase.add_argument("phase", type=int, choices=range(0, 7), help="Phase number to apply")
    phase.add_argument("--eval-run-id", help="Eval run UUID to use for Phase 5 readiness")
    phase.add_argument("--force", action="store_true", help="Bypass Phase 5 readiness gate")
    phase.add_argument("--json", action="store_true", help="Output JSON")

    eval_cmd = sub.add_parser("eval", help="Run a persisted RecMem retrieval eval set")
    eval_cmd.add_argument("eval_set", help="Eval set name or UUID")
    eval_cmd.add_argument("--label", help="Optional eval run label")
    eval_cmd.add_argument("--limit", type=int, default=10, help="Candidate limit per retrieval path")
    return parser


def main() -> int:
    return asyncio.run(_amain(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
