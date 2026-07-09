"""HMX (Hexis Memory Exchange) v1.7 — intent policy and envelope construction.

HMX is one wire format used by four operations with different safety rules
(plans/hmx.md): ``port`` and ``duplicate`` may carry deep self-structure
because source and target are the same agent or an intended clone; ``telepathy``
and ``analysis`` must not silently graft one instance's identity, worldview,
drives, emotional triggers, narrative, or active goals into another.

This module is the policy layer (which sections an export intent includes by
default, what explicit opt-in unlocks), the envelope every export carries, and
the export pipeline: section data comes from the SQL functions in
db/48_functions_memory_exchange.sql with local UUIDs, and this layer applies
export-scoped refs, content hashes, provenance enrichment, protected-section
digests, statistics, and JSON/JSONL serialization.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

HMX_VERSION = "1.7"

EXPORT_INTENTS = ("port", "duplicate", "telepathy", "analysis")

# Sections that affect self-constitution, motivational state, value structure,
# or long-range narrative identity. Excluded by default for telepathy/analysis;
# replacement in an active target requires the Protected Section Replacement
# Protocol regardless of intent.
PROTECTED_SECTIONS = frozenset({
    "identity",
    "worldview",
    "drives",
    "emotional_triggers",
    "narrative",
    "goals",
})

# Every section HMX can carry, in canonical envelope order.
ALL_SECTIONS = (
    "memories",
    "episodes",
    "relationships",
    "narrative",
    "identity",
    "worldview",
    "goals",
    "drives",
    "emotional_triggers",
    "clusters",
    "in_flight_work",
    "audit_records",
)

# Optional sections gated by explicit flags rather than intent.
FLAG_GATED_SECTIONS = frozenset({"raw_units", "config"})

DEFAULT_IMPORT_STRATEGY = {
    "port": "authoritative",
    "duplicate": "authoritative",
    "telepathy": "deliberative",
    "analysis": "analysis_only",
}

HASH_ALGORITHMS = ("content_hash_v1", "protected_section_digest_v1", "audit_record_digest_v1")

OPTIONAL_FEATURES = (
    "raw_units",
    "config",
    "jsonl_streaming",
)


class HmxPolicyError(ValueError):
    """An export/import request violates HMX intent policy."""


def validate_intent(intent: str) -> str:
    if intent not in EXPORT_INTENTS:
        raise HmxPolicyError(
            f"export_intent must be one of {', '.join(EXPORT_INTENTS)}; got {intent!r}"
        )
    return intent


def new_export_id() -> str:
    return f"exp_{secrets.token_hex(8)}"


@dataclass(frozen=True)
class SectionPlan:
    """Resolved section policy for one export."""

    intent: str
    sections: tuple[str, ...]
    include_raw_units: bool = False
    include_config: bool = False
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def protected(self) -> tuple[str, ...]:
        return tuple(s for s in self.sections if s in PROTECTED_SECTIONS)


def resolve_export_sections(
    intent: str,
    *,
    include_protected: list[str] | None = None,
    include_raw_units: bool = False,
    include_config: bool = False,
    include_in_flight_work: bool | None = None,
    include_audit_records: bool | None = None,
    sections: list[str] | None = None,
) -> SectionPlan:
    """Apply intent-specific default section policy (spec: "Export Intents").

    - port/duplicate: all portable sections, including protected structure,
      in-flight work, and audit records.
    - telepathy: memories, episodes, relationships, clusters. Protected
      sections require explicit ``include_protected`` opt-in; in-flight work
      and audit records are excluded (audit opt-in is foreign diagnostic).
    - analysis: caller-selected read-only sections (default: the telepathy
      base set); protected/in-flight/audit only if explicitly requested.
    """
    validate_intent(intent)
    include_protected = list(include_protected or [])
    warnings: list[str] = []

    unknown = sorted(set(include_protected) - PROTECTED_SECTIONS)
    if unknown:
        raise HmxPolicyError(
            "include_protected accepts only protected sections "
            f"({', '.join(sorted(PROTECTED_SECTIONS))}); got: {', '.join(unknown)}"
        )

    if intent in ("port", "duplicate"):
        selected = set(ALL_SECTIONS)
        if include_in_flight_work is False:
            selected.discard("in_flight_work")
        if include_audit_records is False:
            # Audit history is how an instance accounts for past rewrites of
            # its protected state; dropping it from a port is legal but loud.
            selected.discard("audit_records")
            warnings.append(
                "audit_records excluded from a port/duplicate export: the "
                "instance's protected-section history will not travel with it"
            )
    else:
        selected = {"memories", "episodes", "relationships", "clusters"}
        if sections is not None:
            requested = set(sections)
            unknown_sections = sorted(requested - set(ALL_SECTIONS))
            if unknown_sections:
                raise HmxPolicyError(f"unknown sections: {', '.join(unknown_sections)}")
            protected_requested = sorted((requested & PROTECTED_SECTIONS) - set(include_protected))
            if protected_requested:
                raise HmxPolicyError(
                    f"{intent} exports exclude protected sections by default; "
                    f"pass include_protected for: {', '.join(protected_requested)}"
                )
            selected = requested - PROTECTED_SECTIONS
        for section in include_protected:
            selected.add(section)
            warnings.append(
                f"protected section '{section}' included in a {intent} export by "
                "explicit opt-in; importers must route it through deliberative "
                "review or analysis-only handling"
            )
        if include_in_flight_work is True:
            if intent == "analysis":
                selected.add("in_flight_work")
            else:
                warnings.append("in_flight_work is excluded for telepathy exports")
        if include_audit_records is True:
            selected.add("audit_records")
            if intent == "telepathy":
                warnings.append(
                    "audit_records in a telepathy export are foreign diagnostics, "
                    "not local history"
                )

    ordered = tuple(s for s in ALL_SECTIONS if s in selected)
    return SectionPlan(
        intent=intent,
        sections=ordered,
        include_raw_units=include_raw_units,
        include_config=include_config,
        warnings=tuple(warnings),
    )


def default_import_strategy(intent: str) -> str:
    validate_intent(intent)
    return DEFAULT_IMPORT_STRATEGY[intent]


def build_capabilities(relationship_edge_types: list[str]) -> dict[str, Any]:
    """Capabilities block. Edge types come from the live ``graph_edge_type``
    enum (derive from truth), not a hardcoded list."""
    return {
        "formats": ["json", "jsonl"],
        "sections": list(ALL_SECTIONS),
        "hash_algorithms": list(HASH_ALGORITHMS),
        "relationship_edge_types": sorted(relationship_edge_types),
        "optional_features": list(OPTIONAL_FEATURES),
    }


def build_envelope(
    *,
    intent: str,
    plan: SectionPlan,
    instance_id: str,
    schema_version: str,
    embedding_model: str,
    embedding_dimension: int,
    lineage_id: str,
    relationship_edge_types: list[str],
    redaction_policy: str = "none",
    consent_scope: str = "unspecified",
    types: list[str] | None = None,
    time_range: tuple[str | None, str | None] = (None, None),
    export_filter: str | None = None,
    export_id: str | None = None,
    exported_at: datetime | None = None,
) -> dict[str, Any]:
    """Construct the HMX envelope (spec: "Format Specification"). Sections and
    statistics start empty; the exporter fills them as it serializes."""
    validate_intent(intent)
    if redaction_policy not in ("none", "basic", "strict", "custom"):
        raise HmxPolicyError(f"unknown redaction_policy: {redaction_policy!r}")

    stamp = (exported_at or datetime.now(UTC)).isoformat().replace("+00:00", "Z")
    return {
        "hmx_version": HMX_VERSION,
        "export_id": export_id or new_export_id(),
        "export_intent": intent,
        "exported_at": stamp,
        "source": {
            "instance_id": instance_id,
            "schema_version": schema_version,
            "embedding_model": embedding_model,
            "embedding_dimension": embedding_dimension,
            "hexis_lineage_id": lineage_id,
        },
        "capabilities": build_capabilities(relationship_edge_types),
        "privacy": {
            "redaction_policy": redaction_policy,
            "contains_sensitive_content": True,
            "consent_scope": consent_scope,
            "excluded_secret_patterns": ["key", "secret", "token", "password"],
        },
        "export_scope": {
            "types": types or [],
            "time_range": list(time_range),
            "include_protected": list(plan.protected),
            "include_raw_units": plan.include_raw_units,
            "include_config": plan.include_config,
            "include_in_flight_work": "in_flight_work" in plan.sections,
            "include_audit_records": "audit_records" in plan.sections,
            "filter": export_filter,
        },
        "sections": {},
        "statistics": {
            "total_memories": 0,
            "total_relationships": 0,
            "total_episodes": 0,
            "total_raw_units": 0,
            "total_narrative_nodes": 0,
            "total_in_flight_tasks": 0,
            "total_audit_records": 0,
            "total_conflicts_predicted": 0,
            "estimated_embedding_items": 0,
            "estimated_embedding_tokens": 0,
            "estimated_embedding_cost_units": None,
            "estimated_uncompressed_bytes": 0,
        },
    }


# ---------------------------------------------------------------------------
# Export pipeline
# ---------------------------------------------------------------------------

_SECTION_FN = {
    "memories": "hmx_export_memories($1::text[], $2::timestamptz, $3::timestamptz)",
    "episodes": "hmx_export_episodes()",
    "relationships": "hmx_export_relationships()",
    "narrative": "hmx_export_narrative()",
    "identity": "hmx_export_identity()",
    "worldview": "hmx_export_worldview()",
    "goals": "hmx_export_goals()",
    "drives": "hmx_export_drives()",
    "emotional_triggers": "hmx_export_emotional_triggers()",
    "clusters": "hmx_export_clusters()",
    "in_flight_work": "hmx_export_in_flight_work()",
    "audit_records": "hmx_export_audit_records()",
}

# record_type values for JSONL streaming, per section.
_JSONL_RECORD_TYPE = {
    "memories": "memory",
    "episodes": "episode",
    "relationships": "relationship",
    "worldview": "worldview",
    "goals": "goal",
    "drives": "drive",
    "emotional_triggers": "emotional_trigger",
    "clusters": "cluster",
    "identity": "identity",
}


def _ref(export_id: str, local_id: Any) -> str:
    return f"{export_id}:{local_id}"


def _refs(export_id: str, local_ids: Any) -> list[str]:
    return [_ref(export_id, i) for i in (local_ids or [])]


def _enrich_provenance(record: dict[str, Any], *, instance_id: str, local_id: Any) -> None:
    """Move provenance out of metadata to the wire position and fill defaults
    (spec field-default table: acquisition_mode=experienced on export)."""
    metadata = record.get("metadata") or {}
    provenance = metadata.pop("provenance", None) or {}
    provenance.setdefault("acquisition_mode", "experienced")
    provenance.setdefault("origin_instance", instance_id)
    provenance.setdefault("origin_id", str(local_id))
    provenance.setdefault("import_chain", [])
    provenance.setdefault("modification_chain", [])
    record["metadata"] = metadata
    record["provenance"] = provenance


def _postprocess_section(
    section: str,
    data: Any,
    *,
    export_id: str,
    instance_id: str,
) -> Any:
    """Local UUIDs -> export-scoped refs; content hashes; wire field names."""
    from core.digest import content_hash_v1

    if section in ("memories", "worldview"):
        for record in data:
            local_id = record.pop("id")
            record["ref"] = _ref(export_id, local_id)
            record["content_hash_v1"] = content_hash_v1(record.get("content") or "")
            record.pop("superseded_by", None)  # normalized into SUPERSEDES edges
            _enrich_provenance(record, instance_id=instance_id, local_id=local_id)
            if section == "worldview":
                record["supporting_refs"] = _refs(export_id, record.pop("supporting_ids", []))
                record["contesting_refs"] = _refs(export_id, record.pop("contesting_ids", []))
    elif section == "episodes":
        for record in data:
            record["ref"] = _ref(export_id, record.pop("id"))
            record["memory_refs"] = _refs(export_id, record.pop("memory_ids", []))
    elif section == "relationships":
        for record in data:
            props = record.get("properties") or {}
            props["source_type"] = record.pop("source_type", None)
            props["target_type"] = record.pop("target_type", None)
            record["properties"] = props
            record["source_ref"] = _ref(export_id, record.pop("source_id"))
            record["target_ref"] = _ref(export_id, record.pop("target_id"))
    elif section == "goals":
        for record in data:
            local_id = record.pop("id")
            record["ref"] = _ref(export_id, local_id)
            parent = record.pop("parent_goal_id", None)
            record["parent_ref"] = _ref(export_id, parent) if parent else None
            _enrich_provenance(record, instance_id=instance_id, local_id=local_id)
    elif section == "emotional_triggers":
        for record in data:
            record.pop("id", None)
            record["content_hash_v1"] = content_hash_v1(record.get("trigger_pattern") or "")
            record["source_memory_refs"] = _refs(export_id, record.pop("source_memory_ids", []))
    elif section == "clusters":
        for record in data:
            record["ref"] = _ref(export_id, record.pop("id"))
            record["member_refs"] = _refs(export_id, record.pop("member_ids", []))
    elif section == "in_flight_work":
        for task in data.get("consolidation_tasks", []):
            task["ref"] = _ref(export_id, task.pop("id"))
            task["input_refs"] = _refs(export_id, task.pop("input_ids", []))
            target = task.pop("target_memory_id", None)
            task["output_refs"] = [_ref(export_id, target)] if target else []
        for task in data.get("reconsolidation_tasks", []):
            task["ref"] = _ref(export_id, task.pop("id"))
            task["memory_refs"] = _refs(export_id, task.pop("memory_ids", []))
    return data


def _fill_statistics(envelope: dict[str, Any]) -> None:
    import json as _json

    sections = envelope["sections"]
    stats = envelope["statistics"]
    stats["total_memories"] = len(sections.get("memories", []))
    stats["total_relationships"] = len(sections.get("relationships", []))
    stats["total_episodes"] = len(sections.get("episodes", []))
    narrative = sections.get("narrative") or {}
    stats["total_narrative_nodes"] = sum(len(v) for v in narrative.values())
    in_flight = sections.get("in_flight_work") or {}
    stats["total_in_flight_tasks"] = sum(len(v) for v in in_flight.values())
    audit = sections.get("audit_records") or {}
    stats["total_audit_records"] = sum(len(v) for v in audit.values())

    embeddable: list[str] = []
    embeddable += [m.get("content") or "" for m in sections.get("memories", [])]
    embeddable += [w.get("content") or "" for w in sections.get("worldview", [])]
    embeddable += [g.get("title") or "" for g in sections.get("goals", [])]
    embeddable += [t.get("trigger_pattern") or "" for t in sections.get("emotional_triggers", [])]
    embeddable += [e.get("summary") or "" for e in sections.get("episodes", []) if e.get("summary")]
    stats["estimated_embedding_items"] = len(embeddable)
    stats["estimated_embedding_tokens"] = sum(len(t) for t in embeddable) // 4
    stats["estimated_uncompressed_bytes"] = len(_json.dumps(envelope, default=str))


async def export_hmx(
    conn,
    *,
    intent: str,
    include_protected: list[str] | None = None,
    include_raw_units: bool = False,
    include_config: bool = False,
    include_in_flight_work: bool | None = None,
    include_audit_records: bool | None = None,
    sections: list[str] | None = None,
    types: list[str] | None = None,
    since: Any = None,
    until: Any = None,
    redaction_policy: str = "none",
    consent_scope: str = "unspecified",
) -> dict[str, Any]:
    """Produce a complete HMX envelope from the live database.

    Embeddings never leave the database; refs are export-scoped; protected
    sections ride only where intent policy allows and, for port/duplicate,
    carry ``protected_section_digest_v1`` under ``section_digests`` for the
    replacement protocol's Phase 0 fast path.
    """
    import json as _json

    from core.digest import protected_section_digest_v1

    plan = resolve_export_sections(
        intent,
        include_protected=include_protected,
        include_raw_units=include_raw_units,
        include_config=include_config,
        include_in_flight_work=include_in_flight_work,
        include_audit_records=include_audit_records,
        sections=sections,
    )
    ctx = await load_source_context(conn)
    envelope = build_envelope(
        intent=intent,
        plan=plan,
        redaction_policy=redaction_policy,
        consent_scope=consent_scope,
        types=types,
        **ctx,
    )
    export_id = envelope["export_id"]

    for section in plan.sections:
        if section == "memories":
            raw = await conn.fetchval(
                f"SELECT {_SECTION_FN['memories']}", types, since, until
            )
        else:
            raw = await conn.fetchval(f"SELECT {_SECTION_FN[section]}")
        data = _json.loads(raw) if isinstance(raw, str) else raw
        envelope["sections"][section] = _postprocess_section(
            section, data, export_id=export_id, instance_id=ctx["instance_id"]
        )

    if intent in ("port", "duplicate"):
        digests = {
            section: protected_section_digest_v1(section, envelope["sections"][section])
            for section in plan.protected
            if section in envelope["sections"]
        }
        envelope["section_digests"] = digests

    if plan.warnings:
        envelope["export_warnings"] = list(plan.warnings)

    _fill_statistics(envelope)
    return envelope


def iter_hmx_jsonl(envelope: dict[str, Any]):
    """Yield JSONL lines for an envelope (spec: "Streaming Format"): header,
    typed records, footer with statistics."""
    import json as _json

    header = {k: v for k, v in envelope.items() if k not in ("sections", "statistics")}
    yield _json.dumps({"record_type": "envelope", "data": header}, default=str)
    for section, data in envelope["sections"].items():
        record_type = _JSONL_RECORD_TYPE.get(section)
        if record_type:
            for record in data:
                yield _json.dumps({"record_type": record_type, "data": record}, default=str)
        elif section == "narrative":
            yield _json.dumps({"record_type": "narrative", "data": data}, default=str)
        elif section == "in_flight_work":
            for group in ("consolidation_tasks", "reconsolidation_tasks"):
                for task in data.get(group, []):
                    yield _json.dumps(
                        {"record_type": "in_flight_task", "data": dict(task, task_group=group)},
                        default=str,
                    )
        elif section == "audit_records":
            yield _json.dumps({"record_type": "audit_record", "data": data}, default=str)
    yield _json.dumps({"record_type": "footer", "statistics": envelope["statistics"]}, default=str)


async def load_source_context(conn) -> dict[str, Any]:
    """Read the envelope's source-block facts from the live database."""
    import json as _json

    async def _config(key: str) -> Any:
        raw = await conn.fetchval("SELECT value FROM config WHERE key = $1", key)
        if raw is None:
            return None
        return _json.loads(raw) if isinstance(raw, str) else raw

    edge_types = [
        r["enumlabel"]
        for r in await conn.fetch(
            "SELECT enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
            "WHERE t.typname = 'graph_edge_type' ORDER BY enumlabel"
        )
    ]
    schema_version = await conn.fetchval(
        "SELECT COALESCE(max(version), 'baseline') FROM schema_migrations"
    )
    return {
        "instance_id": str(await _config("agent.instance_id") or await _config("agent.name") or "hexis"),
        "schema_version": str(schema_version),
        "embedding_model": str(await _config("embedding.model_id") or "unknown"),
        "embedding_dimension": int(await _config("embedding.dimension") or 768),
        "lineage_id": str(await _config("agent.lineage_id") or ""),
        "relationship_edge_types": edge_types,
    }
