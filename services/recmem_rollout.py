"""Operator helpers for RecMem rollout phases and readiness checks."""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any
from uuid import UUID

import asyncpg

from core.agent_api import db_dsn_from_env, pool_sizes_from_env
from services.recmem_eval import run_recmem_eval_set


ROLL_OUT_CONFIG_KEYS = (
    "memory.recmem_rollout_phase",
    "memory.recmem_enabled",
    "chat.eager_memory_enabled",
    "chat.recmem_salience_direct_promote",
    "chat.inline_subconscious_enabled",
    "memory.recmem_hydrate_enabled",
    "memory.recmem_dual_write_compare",
    "memory.recmem_rollout_metrics_enabled",
    "memory.recmem_worker_enabled",
)

PHASE_CONFIGS: dict[int, dict[str, Any]] = {
    0: {
        "memory.recmem_rollout_phase": 0,
        "memory.recmem_enabled": False,
        "chat.eager_memory_enabled": True,
        "chat.inline_subconscious_enabled": True,
        "memory.recmem_hydrate_enabled": False,
        "memory.recmem_dual_write_compare": False,
        "memory.recmem_rollout_metrics_enabled": False,
        "memory.recmem_worker_enabled": False,
    },
    1: {
        "memory.recmem_rollout_phase": 1,
        "memory.recmem_enabled": False,
        "chat.eager_memory_enabled": True,
        "chat.inline_subconscious_enabled": True,
        "memory.recmem_hydrate_enabled": False,
        "memory.recmem_dual_write_compare": False,
        "memory.recmem_rollout_metrics_enabled": True,
        "memory.recmem_worker_enabled": False,
    },
    2: {
        "memory.recmem_rollout_phase": 2,
        "memory.recmem_enabled": True,
        "chat.eager_memory_enabled": True,
        "chat.inline_subconscious_enabled": True,
        "memory.recmem_hydrate_enabled": False,
        "memory.recmem_dual_write_compare": True,
        "memory.recmem_rollout_metrics_enabled": True,
        "memory.recmem_worker_enabled": False,
    },
    3: {
        "memory.recmem_rollout_phase": 3,
        "memory.recmem_enabled": True,
        "chat.eager_memory_enabled": False,
        "chat.inline_subconscious_enabled": True,
        "memory.recmem_hydrate_enabled": False,
        "memory.recmem_dual_write_compare": False,
        "memory.recmem_rollout_metrics_enabled": True,
        "memory.recmem_worker_enabled": False,
    },
    4: {
        "memory.recmem_rollout_phase": 4,
        "memory.recmem_enabled": True,
        "chat.eager_memory_enabled": False,
        "chat.inline_subconscious_enabled": True,
        "memory.recmem_hydrate_enabled": False,
        "memory.recmem_dual_write_compare": False,
        "memory.recmem_rollout_metrics_enabled": True,
        "memory.recmem_worker_enabled": True,
    },
    5: {
        "memory.recmem_rollout_phase": 5,
        "memory.recmem_enabled": True,
        "chat.eager_memory_enabled": False,
        "chat.inline_subconscious_enabled": True,
        "memory.recmem_hydrate_enabled": True,
        "memory.recmem_dual_write_compare": False,
        "memory.recmem_rollout_metrics_enabled": True,
        "memory.recmem_worker_enabled": True,
    },
    6: {
        "memory.recmem_rollout_phase": 6,
        "memory.recmem_enabled": True,
        "chat.eager_memory_enabled": False,
        "chat.inline_subconscious_enabled": True,
        "memory.recmem_hydrate_enabled": True,
        "memory.recmem_dual_write_compare": False,
        "memory.recmem_rollout_metrics_enabled": True,
        "memory.recmem_worker_enabled": True,
    },
}


def _coerce_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def _uuid_or_none(value: str | UUID | None) -> UUID | None:
    if value is None:
        return None
    return value if isinstance(value, UUID) else UUID(str(value))


def infer_recmem_rollout_phase(configs: dict[str, Any]) -> int | None:
    """Infer the closest named rollout phase from active config toggles."""
    comparable = {key: configs.get(key) for key in ROLL_OUT_CONFIG_KEYS if key in configs}
    for phase in sorted(PHASE_CONFIGS.keys(), reverse=True):
        expected = PHASE_CONFIGS[phase]
        if all(comparable.get(key) == value for key, value in expected.items()):
            return phase
    return None


async def _fetch_config(conn: asyncpg.Connection, key: str) -> Any:
    return _coerce_json(await conn.fetchval("SELECT get_config($1)", key))


async def _fetch_configs(conn: asyncpg.Connection) -> dict[str, Any]:
    return {key: await _fetch_config(conn, key) for key in ROLL_OUT_CONFIG_KEYS}


async def _fetch_health(conn: asyncpg.Connection) -> dict[str, Any]:
    row = await conn.fetchrow("SELECT * FROM recmem_rollout_health")
    if not row:
        return {}
    return {key: _coerce_json(row[key]) for key in row.keys()}


async def _fetch_latest_eval_run_id(conn: asyncpg.Connection) -> UUID | None:
    return await conn.fetchval(
        """
        SELECT id
        FROM recmem_eval_runs
        WHERE status = 'completed'
        ORDER BY completed_at DESC NULLS LAST, started_at DESC
        LIMIT 1
        """
    )


async def get_recmem_rollout_status(
    conn: asyncpg.Connection,
    *,
    eval_run_id: str | UUID | None = None,
) -> dict[str, Any]:
    """Return config, health, metrics, and Phase 5 readiness in one payload."""
    selected_eval_run_id = _uuid_or_none(eval_run_id) or await _fetch_latest_eval_run_id(conn)
    configs = await _fetch_configs(conn)
    health = await _fetch_health(conn)
    metrics = _coerce_json(
        await conn.fetchval(
            "SELECT get_recmem_rollout_metrics(CURRENT_TIMESTAMP - INTERVAL '7 days')"
        )
    )
    readiness = _coerce_json(
        await conn.fetchval("SELECT get_recmem_phase5_readiness($1::uuid)", selected_eval_run_id)
    )
    return {
        "phase": infer_recmem_rollout_phase(configs),
        "configs": configs,
        "health": health,
        "metrics_7d": metrics,
        "phase5_readiness": readiness,
    }


async def apply_recmem_rollout_phase(
    conn: asyncpg.Connection,
    phase: int,
    *,
    eval_run_id: str | UUID | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Apply a named rollout phase.

    Phase 5 and 6 enable RecMem retrieval, so they require the SQL readiness
    gate unless ``force`` is passed by an operator.
    """
    if phase not in PHASE_CONFIGS:
        raise ValueError(f"Unknown RecMem rollout phase: {phase}")

    selected_eval_run_id = _uuid_or_none(eval_run_id) or await _fetch_latest_eval_run_id(conn)
    readiness = None
    if phase >= 5 and not force:
        readiness = _coerce_json(
            await conn.fetchval("SELECT get_recmem_phase5_readiness($1::uuid)", selected_eval_run_id)
        )
        if not isinstance(readiness, dict) or readiness.get("ready") is not True:
            reason = readiness.get("reason") if isinstance(readiness, dict) else "readiness_unavailable"
            raise RuntimeError(f"Phase {phase} requires a passing readiness gate: {reason}")

    for key, value in PHASE_CONFIGS[phase].items():
        await conn.execute("SELECT set_config($1, $2::jsonb)", key, json.dumps(value))

    status = await get_recmem_rollout_status(conn, eval_run_id=selected_eval_run_id)
    status["applied_phase"] = phase
    if readiness is not None:
        status["preflight_readiness"] = readiness
    if force and phase >= 5:
        status["forced"] = True
    return status


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
    phase.add_argument("phase", type=int, choices=sorted(PHASE_CONFIGS), help="Phase number to apply")
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
