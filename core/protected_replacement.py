"""HMX Protected Section Replacement Protocol core machinery.

Slice 9 implements durable protocol state and the Phase 0 verified no-op path.
Destructive authoritative replacement is intentionally absent until Slice 10.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Mapping

from core.digest import audit_record_digest_v1, protected_section_digest_v1
from core.memory_exchange import (
    PROTECTED_SECTIONS,
    HmxPolicyError,
    HmxSchemaError,
    export_hmx,
    load_source_context,
    validate_hmx_document,
)
from core.trust_anchors import (
    TrustAnchorVerifier,
    TrustStatus,
    TrustVerification,
    UnconfiguredTrustAnchors,
)

PROTECTED_REPLACEMENT_CAPABILITY = "protected_replacement_protocol_v1"
FAST_PATH_CAPABILITY = "fast_path_verification"
ACKNOWLEDGEMENT_DECISIONS = (
    "accept",
    "refuse",
    "request_modification",
    "defer",
)

_AUDIT_GROUPS = {
    "protected_replacement_audit": "protected_section_replacement",
    "protected_section_verified_audit": "protected_section_verified",
    "protected_replacement_reversion_audit": "protected_section_reverted",
}


class ProtectedReplacementError(HmxPolicyError):
    """A stable protocol error with a machine-readable HMX conflict code."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class ProtectedReplacementResult:
    disposition: str
    section: str
    local_digest_v1: str
    imported_digest_v1: str
    lineage_verification: dict[str, Any]
    agent_acknowledgement_required: bool
    audit_ids: tuple[str, ...] = ()
    replacement_id: str | None = None
    consent_id: str | None = None
    reused: bool = False
    conflicts: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class AuditImportResult:
    inserted: int = 0
    duplicates: int = 0
    conflicts: tuple[dict[str, Any], ...] = ()
    warnings: tuple[dict[str, Any], ...] = ()


def _coerce_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _iso_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _audit_source(envelope: Mapping[str, Any]) -> dict[str, Any]:
    source = envelope.get("source") or {}
    return {
        "export_id": str(envelope.get("export_id") or ""),
        "origin_instance": str(source.get("instance_id") or ""),
        "hexis_lineage_id": str(source.get("hexis_lineage_id") or ""),
        "export_intent": str(envelope.get("export_intent") or ""),
    }


def _trust_payload(
    verification: TrustVerification, *, locally_trusted_label: bool
) -> dict[str, Any]:
    return {
        "status": verification.status.value,
        "reason": verification.reason,
        "anchor_id": verification.anchor_id,
        "metadata": dict(verification.metadata),
        "locally_trusted_label": locally_trusted_label,
    }


def _required_capabilities(envelope: Mapping[str, Any]) -> set[str]:
    capabilities = envelope.get("capabilities") or {}
    features = capabilities.get("optional_features") or []
    return {str(feature) for feature in features}


def _validate_request(
    envelope: dict[str, Any], section: str, scope_mode: str
) -> tuple[Any, str]:
    if section not in PROTECTED_SECTIONS:
        raise ProtectedReplacementError(
            "invalid_replacement_section",
            f"section must be one of {', '.join(sorted(PROTECTED_SECTIONS))}",
        )
    if scope_mode != "whole_section":
        raise ProtectedReplacementError(
            "unsupported_replacement_scope",
            "MVP protected replacement supports whole_section only; use "
            "whole_section or deliberative import",
        )
    if envelope.get("export_intent") not in ("port", "duplicate"):
        raise ProtectedReplacementError(
            "invalid_replacement_intent",
            "protected replacement requires export_intent port or duplicate",
        )
    features = _required_capabilities(envelope)
    if PROTECTED_REPLACEMENT_CAPABILITY not in features:
        raise ProtectedReplacementError(
            "protected_replacement_capability_missing",
            "the HMX exchange does not advertise protected_replacement_protocol_v1",
        )
    sections = envelope.get("sections") or {}
    if section not in sections:
        raise ProtectedReplacementError(
            "protected_section_missing", f"the exchange does not contain {section}"
        )
    declared = str((envelope.get("section_digests") or {}).get(section) or "")
    if not declared:
        raise ProtectedReplacementError(
            "protected_section_digest_missing",
            f"the exchange does not declare a protected digest for {section}",
        )
    actual = protected_section_digest_v1(section, sections[section])
    if actual != declared:
        raise ProtectedReplacementError(
            "protected_section_digest_invalid",
            f"declared digest for {section} does not match the imported content",
        )
    return sections[section], declared


async def store_audit_record(
    conn,
    record: dict[str, Any],
    *,
    is_foreign_diagnostic: bool = False,
    imported_at: datetime | None = None,
) -> dict[str, Any]:
    """Insert or compare one immutable audit record by stable ``audit_id``."""

    digest = audit_record_digest_v1(record)
    result = await conn.fetchval(
        "SELECT hmx_store_audit_record($1::jsonb, $2::text, $3::boolean, $4::timestamptz)",
        json.dumps(record),
        digest,
        is_foreign_diagnostic,
        imported_at,
    )
    parsed = _coerce_json(result)
    if not isinstance(parsed, dict):
        raise RuntimeError("hmx_store_audit_record returned a non-object result")
    return parsed


async def _write_verified_audit(
    conn,
    *,
    envelope: dict[str, Any],
    section: str,
    local_digest: str,
    imported_digest: str,
    trust_payload: dict[str, Any],
) -> str:
    audit_id = str(uuid.uuid4())
    record = {
        "audit_id": audit_id,
        "event_type": "protected_section_verified",
        "event_time": _iso_now(),
        "sections_verified": [section],
        "source": _audit_source(envelope),
        "local_digest_v1": local_digest,
        "imported_digest_v1": imported_digest,
        "lineage_verification": trust_payload,
    }
    result = await store_audit_record(conn, record)
    if result.get("status") != "inserted":
        raise RuntimeError(f"verified audit insert returned {result.get('status')}")
    return audit_id


def _request_key(
    *,
    envelope: Mapping[str, Any],
    section: str,
    imported_digest: str,
    local_digest: str,
    rationale: str,
) -> str:
    payload = json.dumps(
        {
            "export_id": envelope.get("export_id"),
            "section": section,
            "imported_digest_v1": imported_digest,
            "local_digest_v1": local_digest,
            "rationale": rationale.strip(),
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _attempt_request_key(request_fingerprint: str, attempt: int) -> str:
    if attempt == 1:
        return request_fingerprint
    return hashlib.sha256(
        f"{request_fingerprint}:{attempt}".encode("ascii")
    ).hexdigest()


def _operator_signature_payload(
    *,
    envelope: Mapping[str, Any],
    section: str,
    imported_digest: str,
    local_digest: str,
    rationale: str,
) -> bytes:
    return json.dumps(
        {
            "source": _audit_source(envelope),
            "replacement_scope": {
                "section": section,
                "mode": "whole_section",
                "selector": None,
            },
            "imported_digest_v1": imported_digest,
            "local_digest_v1": local_digest,
            "rationale": rationale,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


async def _enqueue_replacement(
    conn,
    *,
    envelope: dict[str, Any],
    section: str,
    imported_section: Any,
    imported_digest: str,
    local_digest: str,
    rationale: str,
    trust_payload: dict[str, Any],
    operator_signature: str | None,
    operator_identity: str | None,
) -> tuple[str, str, bool]:
    request_fingerprint = _request_key(
        envelope=envelope,
        section=section,
        imported_digest=imported_digest,
        local_digest=local_digest,
        rationale=rationale,
    )
    scope = {"section": section, "mode": "whole_section", "selector": None}
    source = _audit_source(envelope)

    async with conn.transaction():
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtext('hmx_protected_replacement'))"
        )
        existing = await conn.fetchrow(
            "SELECT replacement_id, consent_id, status, request_attempt "
            "FROM hmx_pending_replacements WHERE request_fingerprint=$1 "
            "ORDER BY request_attempt DESC LIMIT 1",
            request_fingerprint,
        )
        if existing and existing["status"] != "timed_out":
            return str(existing["replacement_id"]), str(existing["consent_id"]), True

        request_attempt = int(existing["request_attempt"]) + 1 if existing else 1
        request_key = _attempt_request_key(request_fingerprint, request_attempt)

        heartbeat_count = int(
            await conn.fetchval(
                "SELECT COALESCE(heartbeat_count, 0) FROM heartbeat_state WHERE id=1"
            )
            or 0
        )
        consent_id = await conn.fetchval(
            "INSERT INTO hmx_consent "
            "(sections, source, replacement_scope, rationale, operator_signature, "
            "operator_identity, agent_acknowledgement_required, trust_verification) "
            "VALUES ($1::text[], $2::jsonb, $3::jsonb, $4, $5, $6, TRUE, $7::jsonb) "
            "RETURNING consent_id",
            [section],
            json.dumps(source),
            json.dumps(scope),
            rationale.strip(),
            operator_signature,
            operator_identity,
            json.dumps(trust_payload),
        )
        replacement_id = await conn.fetchval(
            "INSERT INTO hmx_pending_replacements "
            "(request_key, request_fingerprint, request_attempt, consent_id, "
            "export_id, section, source, imported_section, "
            "imported_digest_v1, local_digest_v1, replacement_scope, rationale, "
            "created_heartbeat_count, timeout_heartbeat_count) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb, $9, $10, "
            "$11::jsonb, $12, $13, $14) RETURNING replacement_id",
            request_key,
            request_fingerprint,
            request_attempt,
            consent_id,
            str(envelope["export_id"]),
            section,
            json.dumps(source),
            json.dumps(imported_section),
            imported_digest,
            local_digest,
            json.dumps(scope),
            rationale.strip(),
            heartbeat_count,
            heartbeat_count + 10,
        )
    return str(replacement_id), str(consent_id), False


async def evaluate_protected_replacement(
    conn,
    envelope: dict[str, Any],
    *,
    section: str,
    rationale: str,
    verifier: TrustAnchorVerifier | None = None,
    allow_locally_trusted_lineage: bool = False,
    scope_mode: str = "whole_section",
    operator_signature: str | None = None,
    operator_identity: str | None = None,
) -> ProtectedReplacementResult:
    """Run Phase 0 or create an acknowledgement-gated pending replacement.

    This function never mutates protected state. A successful Phase 0 result
    means only that the requested replacement was content-identical and its
    immutable verification audit was durably written.
    """

    if not isinstance(envelope, dict):
        raise HmxSchemaError("HMX input must be a JSON object")
    validate_hmx_document(envelope)
    imported_section, imported_digest = _validate_request(envelope, section, scope_mode)
    rationale = rationale.strip()
    if not rationale:
        raise ProtectedReplacementError(
            "replacement_rationale_missing", "protected replacement requires rationale"
        )

    local_envelope = await export_hmx(
        conn,
        intent="port",
        include_in_flight_work=False,
        include_audit_records=False,
    )
    local_digest = str(local_envelope["section_digests"][section])
    local_context = await load_source_context(conn)
    source = envelope.get("source") or {}
    source_lineage = str(source.get("hexis_lineage_id") or "")
    local_lineage = str(local_context.get("lineage_id") or "")
    labels_equal = bool(source_lineage) and source_lineage == local_lineage

    active_verifier = verifier or UnconfiguredTrustAnchors()
    verification = active_verifier.verify_lineage(
        source=source,
        local_instance_id=str(local_context.get("instance_id") or ""),
        local_lineage_id=local_lineage,
    )
    locally_trusted = (
        labels_equal
        and allow_locally_trusted_lineage
        and verification.status is TrustStatus.UNVERIFIED
    )
    lineage_matches = labels_equal and (verification.verified or locally_trusted)
    trust_payload = _trust_payload(verification, locally_trusted_label=locally_trusted)

    if local_digest == imported_digest and lineage_matches:
        try:
            async with conn.transaction():
                audit_id = await _write_verified_audit(
                    conn,
                    envelope=envelope,
                    section=section,
                    local_digest=local_digest,
                    imported_digest=imported_digest,
                    trust_payload=trust_payload,
                )
        except Exception as exc:
            raise ProtectedReplacementError(
                "verified_audit_write_failure",
                "content-identical state was not reported as verified because the "
                f"required audit write failed: {exc}",
            ) from exc
        return ProtectedReplacementResult(
            disposition="verified_noop",
            section=section,
            local_digest_v1=local_digest,
            imported_digest_v1=imported_digest,
            lineage_verification=trust_payload,
            agent_acknowledgement_required=False,
            audit_ids=(audit_id,),
        )

    conflicts: list[dict[str, Any]] = []
    if local_digest != imported_digest:
        conflicts.append(
            {
                "code": "protected_section_digest_mismatch",
                "section": section,
                "local_digest_v1": local_digest,
                "imported_digest_v1": imported_digest,
            }
        )
    if not lineage_matches:
        code = (
            "lineage_integrity_failure"
            if labels_equal and verification.status is TrustStatus.INVALID
            else "unverified_lineage" if labels_equal else "lineage_mismatch"
        )
        conflicts.append(
            {
                "code": code,
                "section": section,
                "source_lineage": source_lineage,
                "local_lineage": local_lineage,
                "verification": trust_payload,
            }
        )

    effective_operator_signature = operator_signature
    if operator_signature:
        signature_verification = active_verifier.verify_operator_signature(
            signature=operator_signature,
            payload=_operator_signature_payload(
                envelope=envelope,
                section=section,
                imported_digest=imported_digest,
                local_digest=local_digest,
                rationale=rationale,
            ),
            operator_identity=operator_identity,
        )
        trust_payload = dict(trust_payload)
        trust_payload["operator_signature_verification"] = _trust_payload(
            signature_verification, locally_trusted_label=False
        )
        if not signature_verification.verified:
            effective_operator_signature = None
            conflicts.append(
                {
                    "code": "unverified_signature",
                    "section": section,
                    "verification": trust_payload["operator_signature_verification"],
                }
            )

    replacement_id, consent_id, reused = await _enqueue_replacement(
        conn,
        envelope=envelope,
        section=section,
        imported_section=imported_section,
        imported_digest=imported_digest,
        local_digest=local_digest,
        rationale=rationale,
        trust_payload=trust_payload,
        operator_signature=effective_operator_signature,
        operator_identity=operator_identity,
    )
    return ProtectedReplacementResult(
        disposition="pending_acknowledgement",
        section=section,
        local_digest_v1=local_digest,
        imported_digest_v1=imported_digest,
        lineage_verification=trust_payload,
        agent_acknowledgement_required=True,
        replacement_id=replacement_id,
        consent_id=consent_id,
        reused=reused,
        conflicts=tuple(conflicts),
    )


async def pending_protected_replacements(conn) -> dict[str, Any]:
    result = _coerce_json(await conn.fetchval("SELECT hmx_pending_replacements()"))
    if not isinstance(result, dict):
        raise RuntimeError("hmx_pending_replacements returned a non-object result")
    return result


async def acknowledge_protected_replacement(
    conn,
    replacement_id: str,
    *,
    decision: str,
    rationale: str | None = None,
    proposed_changes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if decision not in ACKNOWLEDGEMENT_DECISIONS:
        raise ProtectedReplacementError(
            "invalid_acknowledgement",
            f"decision must be one of {', '.join(ACKNOWLEDGEMENT_DECISIONS)}",
        )
    async with conn.transaction():
        result = await conn.fetchval(
            "SELECT hmx_acknowledge_protected_replacement($1::uuid, $2, $3, $4::jsonb)",
            replacement_id,
            decision,
            rationale,
            json.dumps(proposed_changes) if proposed_changes is not None else None,
        )
    parsed = _coerce_json(result)
    if not isinstance(parsed, dict):
        raise RuntimeError(
            "hmx_acknowledge_protected_replacement returned a non-object result"
        )
    return parsed


async def create_protected_snapshot(
    conn,
    sections: list[str],
    *,
    heartbeat_window: int = 7,
    wall_clock_expires_at: datetime | None = None,
) -> str:
    selected = sorted(set(sections))
    invalid = sorted(set(selected) - PROTECTED_SECTIONS)
    if not selected or invalid:
        raise ProtectedReplacementError(
            "invalid_snapshot_sections",
            "snapshot sections must be a non-empty protected-section list"
            + (f"; invalid: {', '.join(invalid)}" if invalid else ""),
        )
    local = await export_hmx(
        conn,
        intent="port",
        include_in_flight_work=False,
        include_audit_records=False,
    )
    state = {section: local["sections"][section] for section in selected}
    digests = {section: local["section_digests"][section] for section in selected}
    if wall_clock_expires_at is None:
        snapshot_id = await conn.fetchval(
            "SELECT hmx_create_protected_snapshot($1::text[], $2::jsonb, "
            "$3::jsonb, $4::integer)",
            selected,
            json.dumps(state),
            json.dumps(digests),
            heartbeat_window,
        )
    else:
        snapshot_id = await conn.fetchval(
            "SELECT hmx_create_protected_snapshot($1::text[], $2::jsonb, "
            "$3::jsonb, $4::integer, $5::timestamptz)",
            selected,
            json.dumps(state),
            json.dumps(digests),
            heartbeat_window,
            wall_clock_expires_at,
        )
    return str(snapshot_id)


async def import_audit_records(
    conn,
    envelope: dict[str, Any],
) -> AuditImportResult:
    """Preserve protected audit history with digest-based conflict detection."""

    records, warnings = _validated_audit_records(envelope)
    intent = str(envelope.get("export_intent") or "")
    foreign = intent not in ("port", "duplicate")
    inserted = 0
    duplicates = 0
    conflicts: list[dict[str, Any]] = []
    imported_at = datetime.now(UTC)

    for record in records:
        result = await store_audit_record(
            conn,
            record,
            is_foreign_diagnostic=foreign,
            imported_at=imported_at,
        )
        if result.get("status") == "inserted":
            inserted += 1
        elif result.get("status") == "duplicate":
            duplicates += 1
        else:
            conflicts.append(dict(result))

    if foreign and (inserted or duplicates):
        warnings.append(
            {
                "code": "foreign_audit_diagnostic",
                "section": "audit_records",
                "count": inserted + duplicates,
                "error": "non-port audit history was retained as a foreign diagnostic and will not be re-exported as local history",
            }
        )
    return AuditImportResult(
        inserted=inserted,
        duplicates=duplicates,
        conflicts=tuple(conflicts),
        warnings=tuple(warnings),
    )


def _validated_audit_records(
    envelope: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate protected audit records independently so one bad record is isolated."""

    audit_section = (envelope.get("sections") or {}).get("audit_records") or {}
    if not isinstance(audit_section, dict):
        return [], [
            {
                "code": "schema_validation_error",
                "section": "audit_records",
                "error": "audit_records must be an object",
            }
        ]
    valid: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for group, expected_type in _AUDIT_GROUPS.items():
        records = audit_section.get(group, [])
        if not isinstance(records, list):
            warnings.append(
                {
                    "code": "schema_validation_error",
                    "section": "audit_records",
                    "audit_group": group,
                    "error": "audit group must be an array",
                }
            )
            continue
        for index, record in enumerate(records):
            if (
                not isinstance(record, dict)
                or record.get("event_type") != expected_type
            ):
                warnings.append(
                    {
                        "code": "schema_validation_error",
                        "section": "audit_records",
                        "audit_group": group,
                        "index": index,
                        "error": f"record must have event_type {expected_type}",
                    }
                )
                continue
            probe = dict(envelope)
            probe["sections"] = {"audit_records": {group: [record]}}
            try:
                validate_hmx_document(probe)
            except HmxSchemaError as exc:
                warnings.append(
                    {
                        "code": "schema_validation_error",
                        "section": "audit_records",
                        "audit_group": group,
                        "index": index,
                        "error": str(exc),
                    }
                )
            else:
                valid.append(dict(record))

    unknown_groups = sorted(
        set(audit_section) - set(_AUDIT_GROUPS) - {"transformation_history"}
    )
    for group in unknown_groups:
        warnings.append(
            {
                "code": "unsupported_audit_event",
                "section": "audit_records",
                "audit_group": group,
                "error": "unknown audit group was skipped for operator review",
            }
        )
    return valid, warnings


async def forecast_audit_records(conn, envelope: dict[str, Any]) -> AuditImportResult:
    """Side-effect-free audit dedupe forecast for HMX dry-run."""

    records, warnings = _validated_audit_records(envelope)
    if not records:
        return AuditImportResult(warnings=tuple(warnings))
    ids = [str(record["audit_id"]) for record in records]
    rows = await conn.fetch(
        "SELECT audit_id, record_digest_v1 FROM protected_replacement_audit "
        "WHERE audit_id = ANY($1::text[])",
        ids,
    )
    existing = {str(row["audit_id"]): str(row["record_digest_v1"]) for row in rows}
    inserted = 0
    duplicates = 0
    conflicts: list[dict[str, Any]] = []
    for record in records:
        audit_id = str(record["audit_id"])
        digest = audit_record_digest_v1(record)
        if audit_id not in existing:
            inserted += 1
        elif existing[audit_id] == digest:
            duplicates += 1
        else:
            conflicts.append(
                {
                    "status": "conflict",
                    "code": "audit_integrity_conflict",
                    "audit_id": audit_id,
                    "existing_digest_v1": existing[audit_id],
                    "imported_digest_v1": digest,
                }
            )
    return AuditImportResult(
        inserted=inserted,
        duplicates=duplicates,
        conflicts=tuple(conflicts),
        warnings=tuple(warnings),
    )
