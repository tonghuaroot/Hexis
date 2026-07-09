"""End-to-end command coverage for HMX Slice 3."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from core.digest import content_hash_v1
from core.memory_exchange import build_envelope, resolve_export_sections

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.cli]

_ROOT = Path(__file__).resolve().parents[2]


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "apps.hexis_cli", *args],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        cwd=_ROOT,
    )


async def test_export_jsonl_and_database_aware_dry_run(db_pool, tmp_path):
    output = tmp_path / "memory.hmx.jsonl"
    async with db_pool.acquire() as conn:
        memory_id = await conn.fetchval(
            "INSERT INTO memories (type, content, embedding) "
            "VALUES ('semantic', $1, array_fill(0.1, ARRAY[embedding_dimension()])::vector) "
            "RETURNING id",
            f"HMX CLI round trip {uuid.uuid4().hex}",
        )
    try:
        exported = _run(
            "export",
            "--intent",
            "telepathy",
            "--format",
            "jsonl",
            "--output",
            str(output),
            "--wait-seconds",
            "60",
        )
        assert exported.returncode == 0, exported.stderr
        assert (
            output.read_text(encoding="utf-8")
            .splitlines()[0]
            .startswith('{"record_type": "envelope"')
        )
        assert stat.S_IMODE(output.stat().st_mode) == 0o600

        dry_run = _run(
            "import",
            str(output),
            "--strategy",
            "additive",
            "--dry-run",
            "--json",
            "--wait-seconds",
            "60",
        )
        assert dry_run.returncode == 0, dry_run.stderr
        report = json.loads(dry_run.stdout)
        assert report["intent"] == "telepathy"
        assert report["strategy"] == "additive"
        assert report["can_import"] is True
        assert report["counts"]["duplicate_memories"] >= 1
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM memories WHERE id = $1", memory_id)


async def test_export_refuses_silent_overwrite(db_pool, tmp_path):
    output = tmp_path / "memory.hmx.json"
    first = _run("export", "--intent", "telepathy", "--output", str(output))
    assert first.returncode == 0, first.stderr
    original = output.read_bytes()

    second = _run("export", "--intent", "telepathy", "--output", str(output))
    assert second.returncode != 0
    assert "--overwrite" in second.stderr
    assert output.read_bytes() == original


async def test_export_rejects_inverted_time_range_before_connecting():
    result = _run(
        "export",
        "--intent",
        "telepathy",
        "--since",
        "2026-07-10",
        "--until",
        "2026-07-09",
    )
    assert result.returncode != 0
    assert "--since must be earlier" in result.stderr


async def test_import_requires_exact_intent_confirmation(db_pool, tmp_path):
    output = tmp_path / "memory.hmx.json"
    exported = _run("export", "--intent", "telepathy", "--output", str(output))
    assert exported.returncode == 0, exported.stderr

    missing = _run("import", str(output), "--strategy", "additive")
    assert missing.returncode != 0
    assert "--confirm-intent telepathy" in missing.stderr

    mismatch = _run(
        "import",
        str(output),
        "--strategy",
        "additive",
        "--confirm-intent",
        "analysis",
    )
    assert mismatch.returncode != 0
    assert "intent confirmation mismatch" in mismatch.stderr


async def test_confirmed_additive_import_completes_in_place(db_pool, tmp_path):
    content = f"HMX CLI accepted import {uuid.uuid4().hex}"
    document = build_envelope(
        intent="telepathy",
        plan=resolve_export_sections("telepathy"),
        instance_id="cli-test-source",
        schema_version="0008_hmx_protected_import",
        embedding_model="embeddinggemma:300m",
        embedding_dimension=768,
        lineage_id=str(uuid.uuid4()),
        relationship_edge_types=["SUPPORTS"],
    )
    document["sections"] = {
        "memories": [
            {
                "ref": f"{document['export_id']}:{uuid.uuid4()}",
                "type": "semantic",
                "status": "active",
                "content": content,
                "content_hash_v1": content_hash_v1(content),
                "importance": 0.6,
                "trust_level": 0.7,
                "metadata": {},
                "provenance": {
                    "acquisition_mode": "experienced",
                    "origin_instance": "cli-test-source",
                    "origin_id": str(uuid.uuid4()),
                    "import_chain": [],
                    "modification_chain": [],
                },
            }
        ]
    }
    source = tmp_path / "accepted.hmx.json"
    source.write_text(json.dumps(document), encoding="utf-8")

    imported = _run(
        "import",
        str(source),
        "--strategy",
        "additive",
        "--confirm-intent",
        "telepathy",
        "--json",
        "--wait-seconds",
        "60",
    )
    assert imported.returncode == 0, imported.stderr
    report = json.loads(imported.stdout)
    assert report["inserted"]["memories"] == 1

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, metadata FROM memories WHERE content = $1", content
        )
        assert row is not None
        try:
            metadata = row["metadata"]
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            assert metadata["embedding_status"] == "pending_import"
        finally:
            await conn.execute("DELETE FROM memories WHERE id = $1", row["id"])
