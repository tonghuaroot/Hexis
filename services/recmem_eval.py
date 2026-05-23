"""Deterministic retrieval eval harness for RecMem rollout gates."""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any
from uuid import UUID

import asyncpg

from core.agent_api import db_dsn_from_env, pool_sizes_from_env


def _coerce_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def _uuid_list(value: Any) -> list[UUID]:
    raw = _coerce_json(value)
    if not isinstance(raw, list):
        return []
    out: list[UUID] = []
    for item in raw:
        try:
            out.append(UUID(str(item)))
        except Exception:
            continue
    return out


def _expected_memory_ids(item: asyncpg.Record) -> list[UUID]:
    metadata = _coerce_json(item["metadata"]) if item["metadata"] is not None else {}
    fixture = _coerce_json(item["session_fixture"]) if item["session_fixture"] is not None else {}
    if isinstance(metadata, dict):
        ids = _uuid_list(metadata.get("expected_memory_ids"))
        if ids:
            return ids
    if isinstance(fixture, dict):
        ids = _uuid_list(fixture.get("expected_memory_ids"))
        if ids:
            return ids
    return []


def _hit_rate(candidates: list[UUID], expected: list[UUID]) -> float | None:
    if not expected:
        return None
    candidate_set = set(candidates)
    return len(candidate_set.intersection(expected)) / len(expected)


def _verdict(baseline_score: float | None, recmem_score: float | None) -> str:
    if recmem_score is None:
        return "unjudged"
    if baseline_score is None:
        return "pass" if recmem_score > 0 else "miss"
    if recmem_score >= baseline_score:
        return "pass"
    return "regression"


async def _resolve_eval_set(conn: asyncpg.Connection, name_or_id: str) -> asyncpg.Record:
    try:
        eval_set_id = UUID(str(name_or_id))
        row = await conn.fetchrow("SELECT * FROM recmem_eval_sets WHERE id = $1", eval_set_id)
    except Exception:
        row = await conn.fetchrow("SELECT * FROM recmem_eval_sets WHERE name = $1", name_or_id)
    if not row:
        raise ValueError(f"RecMem eval set not found: {name_or_id}")
    return row


async def run_recmem_eval_set(
    conn: asyncpg.Connection,
    eval_set: str,
    *,
    label: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Run a retrieval-only eval set and persist results.

    If an item contains ``metadata.expected_memory_ids`` or
    ``session_fixture.expected_memory_ids``, the harness scores baseline and
    RecMem retrieval by expected-memory hit rate. Items without expectations are
    persisted as ``unjudged`` but still record both retrieval sets.
    """
    eval_row = await _resolve_eval_set(conn, eval_set)
    run_id = await conn.fetchval(
        """
        INSERT INTO recmem_eval_runs (
            eval_set_id, label, baseline_config, recmem_config, metadata
        )
        VALUES (
            $1,
            $2,
            jsonb_build_object('retrieval', 'fast_recall', 'limit', $3::int),
            jsonb_build_object('retrieval', 'recmem_recall_context', 'limit', $3::int),
            '{}'::jsonb
        )
        RETURNING id
        """,
        eval_row["id"],
        label,
        int(limit),
    )

    try:
        items = await conn.fetch(
            """
            SELECT *
            FROM recmem_eval_items
            WHERE eval_set_id = $1
            ORDER BY created_at, id
            """,
            eval_row["id"],
        )
        for item in items:
            query = item["query_text"]
            baseline_rows = await conn.fetch(
                "SELECT memory_id FROM fast_recall($1::text, $2::int)",
                query,
                int(limit),
            )
            recmem_rows = await conn.fetch(
                """
                SELECT item_id
                FROM recmem_recall_context($1::text, $2::int, $3::int, $4::int)
                WHERE tier IN ('episodic','semantic')
                LIMIT $5::int
                """,
                query,
                int(limit),
                max(1, min(int(limit), 5)),
                max(1, int(limit)),
                int(limit),
            )

            baseline_ids = [row["memory_id"] for row in baseline_rows]
            recmem_ids = [row["item_id"] for row in recmem_rows]
            expected_ids = _expected_memory_ids(item)
            baseline_score = _hit_rate(baseline_ids, expected_ids)
            recmem_score = _hit_rate(recmem_ids, expected_ids)
            verdict = _verdict(baseline_score, recmem_score)

            await conn.execute(
                """
                INSERT INTO recmem_eval_results (
                    run_id,
                    item_id,
                    category,
                    baseline_memory_ids,
                    recmem_memory_ids,
                    judge_score,
                    verdict,
                    metadata
                )
                VALUES ($1, $2, $3, $4::uuid[], $5::uuid[], $6::float, $7, $8::jsonb)
                """,
                run_id,
                item["id"],
                item["category"],
                baseline_ids,
                recmem_ids,
                recmem_score,
                verdict,
                json.dumps({
                    "expected_memory_ids": [str(v) for v in expected_ids],
                    "baseline_hit_rate": baseline_score,
                    "recmem_hit_rate": recmem_score,
                }),
            )

        await conn.execute(
            """
            UPDATE recmem_eval_runs
            SET status = 'completed',
                completed_at = CURRENT_TIMESTAMP
            WHERE id = $1
            """,
            run_id,
        )
    except Exception as exc:
        await conn.execute(
            """
            UPDATE recmem_eval_runs
            SET status = 'failed',
                completed_at = CURRENT_TIMESTAMP,
                metadata = metadata || jsonb_build_object('error', $2::text)
            WHERE id = $1
            """,
            run_id,
            str(exc),
        )
        raise

    summary = await conn.fetchval("SELECT get_recmem_eval_run_summary($1)", run_id)
    result = _coerce_json(summary)
    return dict(result) if isinstance(result, dict) else {"run_id": str(run_id)}


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
