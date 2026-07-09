"""HMX (Hexis Memory Exchange) v1.7 — intent policy and envelope construction.

HMX is one wire format used by four operations with different safety rules
(plans/hmx.md): ``port`` and ``duplicate`` may carry deep self-structure
because source and target are the same agent or an intended clone; ``telepathy``
and ``analysis`` must not silently graft one instance's identity, worldview,
drives, emotional triggers, narrative, or active goals into another.

This module is the policy layer: which sections an export intent includes by
default, what explicit opt-in unlocks, and the envelope every export carries.
Section serializers and the SQL export functions build on it (Slice 1 of the
implementation plan); they are not here yet.
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
