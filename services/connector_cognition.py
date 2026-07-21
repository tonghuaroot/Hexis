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

from core.llm_config import load_llm_config
from core.llm_json import chat_json

logger = logging.getLogger(__name__)

DETECTOR_VERSION = "connector_cognition_hybrid_v2"
RULES_DETECTOR_VERSION = "connector_cognition_rules_v2"

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
_RELATIONSHIP_PATTERNS = (
    re.compile(r"\b(?:my|our)\s+(?:friend|partner|spouse|wife|husband|boss|manager|coworker|colleague|client)\s+([^.!?\n]{2,120})", re.I),
    re.compile(r"\b([A-Z][a-z][^.!?\n]{0,60})\s+is\s+(?:my|our)\s+(friend|partner|spouse|wife|husband|boss|manager|coworker|colleague|client)\b", re.I),
)
_COMMITMENT_PATTERNS = (
    re.compile(r"\bI\s+(?:promised|committed|agreed|need|have)\s+to\s+([^.!?\n]{2,140})", re.I),
    re.compile(r"\b(?:please remind me|remind me|I should)\s+to\s+([^.!?\n]{2,140})", re.I),
)
_JUDGMENT_PATTERNS = (
    re.compile(r"\bI\s+(?:decide|judge|evaluate|prioritize)\s+([^.!?\n]{2,140})", re.I),
    re.compile(r"\b(?:what matters to me is|the important thing is)\s+([^.!?\n]{2,140})", re.I),
)
_EPHEMERAL_USER_MODEL_PATTERNS = (
    re.compile(r"\bthis is (?:just )?(?:a )?test\b", re.I),
    re.compile(r"\bjust testing\b", re.I),
    re.compile(r"\bignore this\b", re.I),
    re.compile(r"\bpretend (?:that )?I\b", re.I),
    re.compile(r"\bsample (?:message|data|conversation)\b", re.I),
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
    "crash detected",
    "car crash",
    "emergency sos",
    "password reset",
    "security alert",
    "bank",
    "tax",
    "court",
    "visa",
    "flight",
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


def _bounded_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(0.0, min(1.0, parsed))


def _dedupe_claims(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for claim in claims:
        key = str(claim.get("claim_key") or "").strip().lower()
        text = str(claim.get("claim") or "").strip()
        category = str(claim.get("category") or "preference").strip().lower()
        if not key and text:
            key = _claim_key(category, text)
        if not key or not text:
            continue
        normalized = dict(claim)
        normalized["claim_key"] = key
        normalized["claim"] = text[:600]
        normalized["category"] = category
        normalized["confidence"] = _bounded_float(normalized.get("confidence"), 0.5)
        normalized["importance"] = _bounded_float(normalized.get("importance"), 0.5)
        if normalized.get("supersedes_claim_key"):
            normalized["supersedes_claim_key"] = str(normalized["supersedes_claim_key"]).strip().lower()
        contradictions = normalized.get("contradicts_claim_keys")
        if not isinstance(contradictions, list):
            contradictions = []
        normalized["contradicts_claim_keys"] = [
            str(item).strip().lower() for item in contradictions if str(item).strip()
        ][:5]
        metadata = normalized.get("metadata")
        normalized["metadata"] = metadata if isinstance(metadata, dict) else {}
        deduped.setdefault(key, normalized)
    return list(deduped.values())[:12]


def _message_body(content: str) -> str:
    marker = "\nMessage:"
    if marker in content:
        return content.split(marker, 1)[1]
    marker = "\nBody:"
    if marker in content:
        return content.split(marker, 1)[1]
    return content


def _looks_ephemeral_user_model_text(text: str) -> bool:
    lowered = text.lower()
    if len(lowered.strip()) < 12:
        return True
    return any(pattern.search(text) for pattern in _EPHEMERAL_USER_MODEL_PATTERNS)


def extract_user_model_claims(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract conservative user-model claims from one connector source item.

    This is intentionally narrow. The DB stores evidence/provenance; richer LLM
    synthesis can replace this detector without changing storage.
    """
    text = _message_body(str(item.get("content") or ""))
    if _looks_ephemeral_user_model_text(text):
        return []
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

    for pattern in _RELATIONSHIP_PATTERNS:
        for match in pattern.finditer(text):
            fragment = _clean_fragment(" ".join(part for part in match.groups() if part))
            if not fragment:
                continue
            claims.append(
                {
                    "claim_key": _claim_key("relationship", fragment),
                    "category": "relationship",
                    "claim": f"User described a relationship context: {fragment}.",
                    "confidence": 0.54,
                    "importance": 0.58,
                }
            )

    for pattern in _COMMITMENT_PATTERNS:
        for match in pattern.finditer(text):
            fragment = _clean_fragment(match.group(1))
            if not fragment:
                continue
            claims.append(
                {
                    "claim_key": _claim_key("commitment", fragment),
                    "category": "commitment",
                    "claim": f"User has a commitment or intended action: {fragment}.",
                    "confidence": 0.57,
                    "importance": 0.62,
                }
            )

    for pattern in _JUDGMENT_PATTERNS:
        for match in pattern.finditer(text):
            fragment = _clean_fragment(match.group(1))
            if not fragment:
                continue
            claims.append(
                {
                    "claim_key": _claim_key("judgment_pattern", fragment),
                    "category": "judgment_pattern",
                    "claim": f"User expressed a judgment pattern or decision heuristic: {fragment}.",
                    "confidence": 0.55,
                    "importance": 0.6,
                }
            )

    return _dedupe_claims(claims)


async def extract_user_model_claims_llm(conn: Any, item: dict[str, Any]) -> list[dict[str, Any]]:
    text = _message_body(str(item.get("content") or ""))[:6000]
    rows = await conn.fetch(
        """
        SELECT claim_key, category, claim, confidence, importance
        FROM user_model_claims
        WHERE status = 'active'
        ORDER BY updated_at DESC
        LIMIT 80
        """
    )
    existing = [
        {
            "claim_key": row["claim_key"],
            "category": row["category"],
            "claim": row["claim"],
            "confidence": float(row["confidence"] or 0),
            "importance": float(row["importance"] or 0),
        }
        for row in rows
    ]
    llm_config = await load_llm_config(conn, "llm.connector_cognition", fallback_key="llm.subconscious")
    system = (
        "Extract durable user-model claims from communication history. "
        "Return JSON only. Claims must be evidence-backed, not generic summary. "
        "Allowed categories: preference, relationship, commitment, routine, "
        "judgment_pattern, identity. Include contradictions or supersession only "
        "when the new evidence directly conflicts with an existing claim. "
        "Do not create claims for one-off test instructions, jokes, or ephemeral chat filler."
    )
    payload = {
        "source_item": {
            "connector_id": item.get("connector_id"),
            "account_key": item.get("account_key"),
            "provider_item_id": item.get("provider_item_id"),
            "title": item.get("title"),
            "timestamp": str(item.get("item_timestamp") or ""),
            "content": text,
        },
        "existing_claims": existing,
        "output_schema": {
            "claims": [
                {
                    "claim_key": "stable lowercase key, e.g. routine:morning_planning",
                    "category": "preference|relationship|commitment|routine|judgment_pattern|identity",
                    "claim": "one sentence phrased as a belief about the user",
                    "confidence": 0.0,
                    "importance": 0.0,
                    "supersedes_claim_key": "optional existing claim_key",
                    "contradicts_claim_keys": ["optional existing claim_key"],
                    "metadata": {"reason": "brief rationale"},
                }
            ]
        },
    }
    doc, _raw = await chat_json(
        llm_config=llm_config,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        max_tokens=1500,
        temperature=0.1,
        response_format={"type": "json_object"},
        fallback={"claims": []},
    )
    raw_claims = doc.get("claims") if isinstance(doc, dict) else []
    if not isinstance(raw_claims, list):
        return []
    return _dedupe_claims([claim for claim in raw_claims if isinstance(claim, dict)])


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
        actions.append({"kind": "prepare_summary", "urgency": "urgent"})
    elif important_hits:
        score = 0.86
        reasons.append(f"important terms: {', '.join(important_hits[:5])}")
        actions.append({"kind": "notify_user", "urgency": "important"})

    if "?" in text and any(term in lowered for term in ("can you", "could you", "please", "need you")):
        score = max(score, 0.72)
        reasons.append("direct request/question")
        actions.append({"kind": "draft_reply", "requires_authorization": True})

    if any(term in lowered for term in ("unsubscribe", "spam", "phishing", "suspicious")):
        score = max(score, 0.68)
        reasons.append("possible spam/security triage")
        actions.append({"kind": "classify_or_filter", "requires_authorization": True})

    if any(term in lowered for term in ("schedule", "calendar", "appointment", "meeting")):
        actions.append({"kind": "calendar_review", "requires_authorization": True})

    label = "urgent" if score >= 0.95 else "important" if score >= 0.85 else "normal"
    deduped_actions: list[dict[str, Any]] = []
    seen_actions: set[str] = set()
    for action in actions:
        key = str(action.get("kind") or "")
        if key and key not in seen_actions:
            deduped_actions.append(action)
            seen_actions.add(key)
    return {
        "score": score,
        "label": label,
        "reasons": reasons,
        "recommended_actions": deduped_actions,
    }


async def estimate_connector_item_importance_llm(conn: Any, item: dict[str, Any]) -> dict[str, Any]:
    baseline = estimate_connector_item_importance(item)
    text = str(item.get("content") or "")[:6000]
    llm_config = await load_llm_config(conn, "llm.connector_importance", fallback_key="llm.subconscious")
    system = (
        "Score a connector item for user-visible importance and route it to suggested actions. "
        "Return JSON only with score 0..1, label low|normal|important|urgent, reasons array, "
        "and recommended_actions array. High-stakes safety, finance, legal, health, security, "
        "deadline, relationship, or explicit user requests should score higher. "
        "Actions are suggestions only; sending/responding/modifying external state requires authorization."
    )
    doc, _raw = await chat_json(
        llm_config=llm_config,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps({"item": item, "rules_baseline": baseline}, default=str)},
        ],
        max_tokens=900,
        temperature=0.1,
        response_format={"type": "json_object"},
        fallback=baseline,
    )
    if not isinstance(doc, dict):
        return baseline
    try:
        score = max(float(baseline.get("score", 0.0)), max(0.0, min(1.0, float(doc.get("score") or 0.0))))
    except (TypeError, ValueError):
        score = float(baseline.get("score") or 0.0)
    label = str(doc.get("label") or baseline.get("label") or "normal").lower()
    if label not in {"low", "normal", "important", "urgent"}:
        label = "urgent" if score >= 0.95 else "important" if score >= 0.85 else "normal"
    if score >= 0.95:
        label = "urgent"
    elif score >= 0.85 and label not in {"urgent", "important"}:
        label = "important"
    reasons = baseline.get("reasons") if isinstance(baseline.get("reasons"), list) else []
    if isinstance(doc.get("reasons"), list):
        reasons = [*reasons, *[str(reason) for reason in doc["reasons"]]]
    actions = baseline.get("recommended_actions") if isinstance(baseline.get("recommended_actions"), list) else []
    if isinstance(doc.get("recommended_actions"), list):
        actions = [*actions, *[action for action in doc["recommended_actions"] if isinstance(action, dict)]]
    return {
        "score": score,
        "label": label,
        "reasons": list(dict.fromkeys(str(reason) for reason in reasons if str(reason).strip()))[:8],
        "recommended_actions": actions[:8],
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
    llm_used = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        source_item_id = str(item.get("source_item_id") or "")
        try:
            mode = str(await conn.fetchval(
                "SELECT COALESCE(get_config_text('connector.user_model_synthesis_mode'), 'hybrid')"
            ) or "hybrid").lower()
            llm_enabled = bool(await conn.fetchval(
                "SELECT COALESCE(get_config_bool('connector.user_model_llm_enabled'), TRUE)"
            ))
            rules_claims = extract_user_model_claims(item)
            claims = rules_claims
            if mode in {"llm", "hybrid"} and llm_enabled:
                try:
                    llm_claims = await extract_user_model_claims_llm(conn, item)
                    if llm_claims:
                        llm_used += 1
                        claims = _dedupe_claims([*llm_claims, *([] if mode == "llm" else rules_claims)])
                except Exception as exc:
                    logger.warning("connector user-model LLM synthesis fell back to rules: %s", exc)
                    if mode == "llm":
                        claims = []
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
        "llm_used": llm_used,
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
            llm_enabled = bool(await conn.fetchval(
                "SELECT COALESCE(get_config_bool('connector.importance_llm_enabled'), TRUE)"
            ))
            if llm_enabled:
                try:
                    estimate = await estimate_connector_item_importance_llm(conn, item)
                except Exception as exc:
                    logger.warning("connector importance LLM detector fell back to rules: %s", exc)
                    estimate = estimate_connector_item_importance(item)
            else:
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
