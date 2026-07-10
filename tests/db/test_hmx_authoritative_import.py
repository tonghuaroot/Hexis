"""HMX Slice 10 authoritative protected-section replacement journeys."""

from __future__ import annotations

import copy
import json
import uuid

import pytest

from core.digest import content_hash_v1, protected_section_digest_v1
from core.memory_exchange import dry_run_hmx, export_hmx, import_hmx
from core.protected_replacement import (
    acknowledge_protected_replacement,
    inspect_protected_replacement,
)
from core.trust_anchors import TrustVerification

pytestmark = [pytest.mark.asyncio(loop_scope="session")]


class VerifiedTrustAnchors:
    def verify_operator_signature(self, **kwargs):
        return TrustVerification.accepted(anchor_id="slice-10-operator")

    def verify_source_identity(self, **kwargs):
        return TrustVerification.accepted(anchor_id="slice-10-source")

    def verify_lineage(self, **kwargs):
        return TrustVerification.accepted(anchor_id="slice-10-lineage")


async def _prepare(conn) -> None:
    await conn.execute("LOAD 'age'")
    await conn.execute('SET search_path = ag_catalog, public, "$user"')
    token = uuid.uuid4().hex
    await conn.execute(
        "INSERT INTO memories "
        "(type, content, embedding, importance, trust_level, status, metadata) "
        "VALUES "
        "('worldview', $1, array_fill(0.1, ARRAY[embedding_dimension()])::vector, "
        "0.9, 0.95, 'active', $2::jsonb), "
        "('goal', $3, array_fill(0.1, ARRAY[embedding_dimension()])::vector, "
        "0.8, 0.8, 'active', $4::jsonb)",
        f"Authoritative worldview {token}",
        json.dumps(
            {
                "category": "value",
                "confidence": 0.95,
                "stability": 0.9,
                "provenance": {"acquisition_mode": "experienced"},
            }
        ),
        f"Authoritative goal {token}",
        json.dumps(
            {
                "title": f"Authoritative goal {token}",
                "description": f"Goal description {token}",
                "priority": "queued",
                "source": "curiosity",
                "due_at": None,
                "progress": [],
                "blocked_by": [],
                "provenance": {"acquisition_mode": "experienced"},
            }
        ),
    )
    await conn.execute(
        "INSERT INTO emotional_triggers "
        "(trigger_pattern, trigger_embedding, valence_delta, arousal_delta, "
        "dominance_delta, typical_emotion, confidence, origin, metadata) "
        "VALUES ($1, array_fill(0.0, ARRAY[embedding_dimension()])::vector, "
        "0.2, 0.1, 0.1, 'curiosity', 0.9, 'learned', $2::jsonb)",
        f"authoritative trigger {token}",
        json.dumps({"provenance": {"acquisition_mode": "experienced"}}),
    )
    if not await conn.fetchval("SELECT EXISTS (SELECT 1 FROM drives)"):
        await conn.execute(
            "INSERT INTO drives (name, description, current_level, baseline) "
            "VALUES ('curiosity', 'Learn', 0.5, 0.5)"
        )
    await conn.execute(
        "SELECT set_config('agent.init_profile', $1::jsonb)",
        json.dumps({"agent": {"description": f"Identity {token}"}}),
    )
    await conn.execute("SELECT ensure_self_node()")
    await conn.execute(
        "SELECT upsert_self_concept_edge('identity', $1, 0.85, NULL)",
        f"concept {token}",
    )
    await conn.execute("SELECT ensure_current_life_chapter($1)", f"Chapter {token}")


def _mutate_section(envelope: dict, section: str) -> None:
    token = uuid.uuid4().hex
    value = envelope["sections"][section]
    if section == "worldview":
        record = value[0]
        record["content"] = f"Replaced worldview {token}"
        record["content_hash_v1"] = content_hash_v1(record["content"])
    elif section == "goals":
        record = value[0]
        record["title"] = f"Replaced goal {token}"
        record["description"] = f"Replaced goal description {token}"
        record["metadata"]["title"] = record["title"]
        record["metadata"]["description"] = record["description"]
    elif section == "drives":
        record = value[0]
        record["current_level"] = min(
            0.99, max(0.01, float(record.get("current_level", 0.5)) + 0.137)
        )
    elif section == "emotional_triggers":
        record = value[0]
        record["trigger_pattern"] = f"replaced trigger {token}"
        record["content_hash_v1"] = content_hash_v1(record["trigger_pattern"])
    elif section == "identity":
        record = value[0]
        record["content"] = f"Replaced identity {token}"
        record["profile"]["description"] = record["content"]
    else:
        for group in (
            "life_chapters",
            "turning_points",
            "narrative_threads",
            "value_conflicts",
        ):
            if value.get(group):
                record = value[group][0]
                field = {
                    "life_chapters": "title",
                    "turning_points": "title",
                    "narrative_threads": "name",
                    "value_conflicts": "summary",
                }[group]
                record[field] = f"Replaced narrative {token}"
                if group == "life_chapters":
                    record["name"] = record[field]
                break
        else:  # pragma: no cover - _prepare always creates a chapter
            raise AssertionError("narrative export was unexpectedly empty")
    envelope["section_digests"][section] = protected_section_digest_v1(
        section, envelope["sections"][section]
    )


@pytest.mark.parametrize(
    "section",
    [
        "identity",
        "worldview",
        "goals",
        "drives",
        "emotional_triggers",
        "narrative",
    ],
)
async def test_authoritative_acceptance_replaces_and_verifies_each_section(
    db_pool, section
):
    async with db_pool.acquire() as conn:
        transaction = conn.transaction()
        await transaction.start()
        try:
            await _prepare(conn)
            envelope = await export_hmx(conn, intent="port")
            _mutate_section(envelope, section)
            imported_digest = envelope["section_digests"][section]
            before_export = await export_hmx(conn, intent="port")
            before_digests = before_export["section_digests"]
            before_digest = before_digests[section]
            assert imported_digest != before_digest

            requested = await import_hmx(
                conn,
                envelope,
                strategy="authoritative",
                replace_sections=[section],
                replacement_rationale=f"Exercise authoritative {section}",
                verifier=VerifiedTrustAnchors(),
            )
            operation = requested.protected_operations[0]
            assert operation["disposition"] == "pending_acknowledgement"
            assert operation["replacement_id"]
            assert (await export_hmx(conn, intent="port"))["section_digests"][
                section
            ] == before_digest
            inspected = await inspect_protected_replacement(
                conn, operation["replacement_id"]
            )
            assert inspected["imported_digest_v1"] == imported_digest
            assert inspected["current_local_digest_v1"] == before_digest
            assert inspected["local_state_changed_since_request"] is False
            assert inspected["imported_section"] == envelope["sections"][section]

            executed = await acknowledge_protected_replacement(
                conn,
                operation["replacement_id"],
                decision="accept",
                executor="agent_tool",
            )
            assert executed["status"] == "executed"
            assert executed["snapshot_id"]
            assert executed["audit_id"]
            after = await export_hmx(conn, intent="port")
            assert after["section_digests"][section] == imported_digest
            for untouched_section, untouched_digest in before_digests.items():
                if untouched_section != section:
                    assert (
                        after["section_digests"][untouched_section] == untouched_digest
                    )
            if section == "narrative":
                current_chapter = next(
                    record
                    for record in envelope["sections"]["narrative"]["life_chapters"]
                    if record.get("key") == "current"
                )
                assert (
                    await conn.fetchval(
                        "SELECT get_narrative_context()->'current_chapter'->>'name'"
                    )
                    == current_chapter["name"]
                )
            if section == "identity":
                assert await conn.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM "
                    "jsonb_array_elements(get_self_model_context(200)) facet "
                    "WHERE facet->>'kind' = 'life_chapter_current')"
                )

            audit = await conn.fetchval(
                "SELECT record FROM protected_replacement_audit WHERE audit_id=$1",
                executed["audit_id"],
            )
            audit = json.loads(audit) if isinstance(audit, str) else audit
            assert audit["previous_state_digest_v1"] == before_digest
            assert audit["new_state_digest_v1"] == imported_digest
            assert audit["previous_state_snapshot_ref"] == executed["snapshot_id"]
            assert audit["agent_acknowledgement"] == "accepted"
        finally:
            await transaction.rollback()


async def test_failed_authoritative_write_rolls_back_acceptance_audit_and_snapshot(
    db_pool,
):
    async with db_pool.acquire() as conn:
        transaction = conn.transaction()
        await transaction.start()
        try:
            await _prepare(conn)
            envelope = await export_hmx(conn, intent="port")
            _mutate_section(envelope, "drives")
            before = (await export_hmx(conn, intent="port"))["section_digests"][
                "drives"
            ]
            requested = await import_hmx(
                conn,
                envelope,
                strategy="authoritative",
                replace_sections=["drives"],
                replacement_rationale="Force atomic execution rollback",
                verifier=VerifiedTrustAnchors(),
            )
            replacement_id = requested.protected_operations[0]["replacement_id"]
            audit_count = await conn.fetchval(
                "SELECT count(*) FROM protected_replacement_audit"
            )
            snapshot_count = await conn.fetchval(
                "SELECT count(*) FROM protected_replacement_snapshots"
            )
            await conn.execute(
                "CREATE FUNCTION pg_temp.reject_drive_delete() RETURNS trigger AS $$ "
                "BEGIN RAISE EXCEPTION 'forced drive delete failure'; END; $$ LANGUAGE plpgsql"
            )
            await conn.execute(
                "CREATE TRIGGER reject_drive_delete BEFORE DELETE ON drives "
                "FOR EACH STATEMENT EXECUTE FUNCTION pg_temp.reject_drive_delete()"
            )

            with pytest.raises(Exception, match="forced drive delete failure"):
                await acknowledge_protected_replacement(
                    conn,
                    replacement_id,
                    decision="accept",
                    executor="agent_tool",
                )

            assert (
                await conn.fetchval(
                    "SELECT status FROM hmx_pending_replacements "
                    "WHERE replacement_id=$1::uuid",
                    replacement_id,
                )
                == "pending"
            )
            assert (
                await conn.fetchval("SELECT count(*) FROM protected_replacement_audit")
                == audit_count
            )
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM protected_replacement_snapshots"
                )
                == snapshot_count
            )
            assert (await export_hmx(conn, intent="port"))["section_digests"][
                "drives"
            ] == before
        finally:
            await transaction.rollback()


async def test_acceptance_refuses_to_overwrite_state_that_changed_while_pending(
    db_pool,
):
    async with db_pool.acquire() as conn:
        transaction = conn.transaction()
        await transaction.start()
        try:
            await _prepare(conn)
            envelope = await export_hmx(conn, intent="port")
            _mutate_section(envelope, "drives")
            requested = await import_hmx(
                conn,
                envelope,
                strategy="authoritative",
                replace_sections=["drives"],
                replacement_rationale="Detect local change before acceptance",
                verifier=VerifiedTrustAnchors(),
            )
            replacement_id = requested.protected_operations[0]["replacement_id"]
            drive_name = envelope["sections"]["drives"][0]["name"]
            await conn.execute(
                "UPDATE drives SET current_level=GREATEST(0.01, current_level-0.071) "
                "WHERE name=$1",
                drive_name,
            )
            inspected = await inspect_protected_replacement(conn, replacement_id)
            assert inspected["local_state_changed_since_request"] is True

            with pytest.raises(
                Exception, match="protected_state_changed_while_pending"
            ):
                await acknowledge_protected_replacement(
                    conn,
                    replacement_id,
                    decision="accept",
                    executor="agent_tool",
                )
            assert (
                await conn.fetchval(
                    "SELECT status FROM hmx_pending_replacements "
                    "WHERE replacement_id=$1::uuid",
                    replacement_id,
                )
                == "pending"
            )
        finally:
            await transaction.rollback()


async def test_authoritative_dry_run_requires_explicit_sections_and_valid_intent(
    db_pool,
):
    async with db_pool.acquire() as conn:
        transaction = conn.transaction()
        await transaction.start()
        try:
            await _prepare(conn)
            port = await export_hmx(conn, intent="port")
            missing = await dry_run_hmx(conn, port, strategy="authoritative")
            assert not missing.can_import
            assert any(
                item["code"] == "replacement_sections_missing"
                for item in missing.conflicts
            )

            valid = await dry_run_hmx(
                conn,
                port,
                strategy="authoritative",
                replace_sections=["worldview"],
                allow_locally_trusted_lineage=True,
            )
            assert valid.can_import
            assert valid.protected_policy["operations"][0]["disposition"] == (
                "verified_noop_candidate"
            )

            telepathy = copy.deepcopy(port)
            telepathy["export_intent"] = "telepathy"
            invalid = await dry_run_hmx(
                conn,
                telepathy,
                strategy="authoritative",
                replace_sections=["worldview"],
            )
            assert not invalid.can_import
            assert any(
                item["code"] == "invalid_authoritative_intent"
                for item in invalid.conflicts
            )
        finally:
            await transaction.rollback()
