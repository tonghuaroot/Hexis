"""Connector cognition workers.

Connector source items preserve exact history. This module performs the
stateless pass that turns those source items into DB-owned user-model claims
and importance records.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

DETECTOR_VERSION = "connector_cognition_rules_v1"

_PREFERENCE_PATTERNS = (
    re.compile(r"\bI\s+(?:really\s+)?(?:prefer|like|love|enjoy)\s+([^.!?\n]{2,120})", re.I),
    re.compile(r"\bI\s+(?:really\s+)?(?:hate|dislike|can't stand|cannot stand)\s+([^.!?\n]{2,120})", re.I),
)
_ROUTINE_PATTERNS = (
    re.compile(r"\bI\s+(?:usually|always|often|normally)\s+([^.!?\n]{2,120})", re.I),
    re.compile(r"\bmy\s+(?:routine|schedule)\s+is\s+([^.!?\n]{2,120})", re.I),
)
_IDENTITY_PATTERNS = (
    re.compile(r"\bI\s+(?:am|work as|work at|live in|live near)\s+([^.!?\n]{2,120})", re.I),
    re.compile(r"\bmy\s+(?:name|job|role|company|city)\s+is\s+([^.!?\n]{2,120})", re.I),
)

_URGENT_TERMS = {
    "crash",
    "accident",
    "emergency",
    "hospital",
    "911",
    "evacuate",
    "fire",
    "fraud",
    "breach",
}
_IMPORTANT_TERMS = {
    "urgent",
    "asap",
    "deadline",
    "due today",
    "interview",
    "offer",
    "contract",
    "invoice",
    "payment failed",
    "overdue",
    "lawyer",
    "legal",
    "doctor",
    "appointment",
    "meeting",
}


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _json_dumps(value: Any) -> str:
    return json.dumps(value, default=str)


def _clean_fragment(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value).strip(" \t\r\n\"'`.,;:")
    return cleaned[:160]


def _claim_key(category: str, fragment: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", fragment.lower()).strip("_")
    return f"{category}:{slug[:120]}"


def _message_body(content: str) -> str:
    marker = "\nMessage:"
    if marker in content:
        return content.split(marker, 1)[1]
    marker = "\nBody:"
    if marker in content:
        return content.split(marker, 1)[1]
    return content


def extract_user_model_claims(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract conservative user-model claims from one connector source item.

    This is intentionally narrow. The DB stores evidence/provenance; richer LLM
    synthesis can replace this detector without changing storage.
    """
    text = _message_body(str(item.get("content") or ""))
    claims: list[dict[str, Any]] = []

    for pattern in _PREFERENCE_PATTERNS:
        for match in pattern.finditer(text):
            fragment = _clean_fragment(match.group(1))
            if not fragment:
                continue
            verb = match.group(0).split()[1].lower()
            negative = verb in {"hate", "dislike"} or "stand" in match.group(0).lower()
            category = "preference"
            claim = f"User {'dislikes' if negative else 'prefers'} {fragment}."
            claims.append(
                {
                    "claim_key": _claim_key(category, f"{'dislikes' if negative else 'prefers'} {fragment}"),
                    "category": category,
                    "claim": claim,
                    "confidence": 0.62,
                    "importance": 0.55,
                }
            )

    for pattern in _ROUTINE_PATTERNS:
        for match in pattern.finditer(text):
            fragment = _clean_fragment(match.group(1))
            if not fragment:
                continue
            claims.append(
                {
                    "claim_key": _claim_key("routine", fragment),
                    "category": "routine",
                    "claim": f"User has a routine or recurring pattern: {fragment}.",
                    "confidence": 0.58,
                    "importance": 0.5,
                }
            )

    for pattern in _IDENTITY_PATTERNS:
        for match in pattern.finditer(text):
            fragment = _clean_fragment(match.group(1))
            if not fragment or fragment.lower() in {"fine", "okay", "good", "here"}:
                continue
            claims.append(
                {
                    "claim_key": _claim_key("identity", fragment),
                    "category": "identity",
                    "claim": f"User stated an identity/context fact: {fragment}.",
                    "confidence": 0.56,
                    "importance": 0.52,
                }
            )

    deduped: dict[str, dict[str, Any]] = {}
    for claim in claims:
        deduped.setdefault(str(claim["claim_key"]), claim)
    return list(deduped.values())[:8]


def estimate_connector_item_importance(item: dict[str, Any]) -> dict[str, Any]:
    text = str(item.get("content") or "")
    lowered = text.lower()
    reasons: list[str] = []
    actions: list[dict[str, Any]] = []
    score = 0.25

    urgent_hits = sorted(term for term in _URGENT_TERMS if term in lowered)
    important_hits = sorted(term for term in _IMPORTANT_TERMS if term in lowered)
    if urgent_hits:
        score = 0.96
        reasons.append(f"urgent terms: {', '.join(urgent_hits[:5])}")
        actions.append({"kind": "notify_user", "urgency": "urgent"})
    elif important_hits:
        score = 0.86
        reasons.append(f"important terms: {', '.join(important_hits[:5])}")
        actions.append({"kind": "notify_user", "urgency": "important"})

    if "?" in text and any(term in lowered for term in ("can you", "could you", "please", "need you")):
        score = max(score, 0.72)
        reasons.append("direct request/question")

    label = "urgent" if score >= 0.95 else "important" if score >= 0.85 else "normal"
    return {
        "score": score,
        "label": label,
        "reasons": reasons,
        "recommended_actions": actions,
    }


async def run_user_model_synthesis_step(conn: Any, *, limit: int | None = None) -> dict[str, Any]:
    if not bool(await conn.fetchval("SELECT COALESCE(get_config_bool('connector.user_model_synthesis_enabled'), TRUE)")):
        return {"skipped": True, "reason": "disabled"}
    raw = await conn.fetchval("SELECT claim_user_model_source_items($1::int)", limit)
    items = _json(raw) or []
    if not isinstance(items, list) or not items:
        return {"skipped": True, "reason": "no_connector_sources"}

    completed = 0
    failed = 0
    claims_created = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        source_item_id = str(item.get("source_item_id") or "")
        try:
            claims = extract_user_model_claims(item)
            result = _json(await conn.fetchval(
                "SELECT record_user_model_synthesis($1::uuid, $2::jsonb, $3)",
                source_item_id,
                _json_dumps(claims),
                DETECTOR_VERSION,
            )) or {}
            claims_created += int(result.get("claim_count") or 0)
            completed += 1
        except Exception as exc:
            failed += 1
            await conn.fetchval(
                "SELECT fail_user_model_source_item($1::uuid, $2)",
                source_item_id,
                str(exc),
            )

    return {
        "claimed": len(items),
        "completed": completed,
        "failed": failed,
        "claims": claims_created,
    }


async def run_connector_importance_step(conn: Any, *, limit: int | None = None) -> dict[str, Any]:
    if not bool(await conn.fetchval("SELECT COALESCE(get_config_bool('connector.importance_detection_enabled'), TRUE)")):
        return {"skipped": True, "reason": "disabled"}
    raw = await conn.fetchval("SELECT claim_connector_importance_items($1::int)", limit)
    items = _json(raw) or []
    if not isinstance(items, list) or not items:
        return {"skipped": True, "reason": "no_connector_sources"}

    completed = 0
    failed = 0
    notified = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        source_item_id = str(item.get("source_item_id") or "")
        try:
            estimate = estimate_connector_item_importance(item)
            result = _json(await conn.fetchval(
                """
                SELECT record_connector_item_importance(
                    $1::uuid,
                    $2::float,
                    $3,
                    $4::jsonb,
                    $5::jsonb,
                    $6,
                    TRUE
                )
                """,
                source_item_id,
                float(estimate["score"]),
                estimate["label"],
                _json_dumps(estimate["reasons"]),
                _json_dumps(estimate["recommended_actions"]),
                DETECTOR_VERSION,
            )) or {}
            if result.get("notification_queued"):
                notified += 1
            completed += 1
        except Exception as exc:
            failed += 1
            await conn.fetchval(
                "SELECT fail_connector_item_importance($1::uuid, $2)",
                source_item_id,
                str(exc),
            )

    return {
        "claimed": len(items),
        "completed": completed,
        "failed": failed,
        "notified": notified,
    }
