"""Opt-in background review that creates durable skill proposals.

This service never writes skill files. The approved proposal tool owns that
separate transition so background work cannot silently change future behavior.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

from core.llm_config import load_llm_config
from core.llm_json import chat_json, extract_json_object
from services.prompt_resources import load_skill_improvement_prompt
from services.skill_runtime import load_available_skills
from skills.base import SkillCategory, SkillContext
from skills.loader import discover_skill_dirs, load_skills_from_dir

logger = logging.getLogger("skill_improvement")

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
_SECRET_VALUE_RE = re.compile(
    r"(?i)(?:api[_ -]?key|password|secret|access[_ -]?token|refresh[_ -]?token)"
    r"\s*[:=]\s*[`'\"]?[A-Za-z0-9_./+\-=]{8,}|"
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\bsk-[A-Za-z0-9_-]{16,}"
)


def _coerce_json(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _catalog(registry: Any | None) -> tuple[dict[str, dict[str, Any]], set[str]]:
    if registry is not None:
        tools = set(registry.list_names())
        skills = load_available_skills(registry, registry_context())
    else:
        tools = set()
        skills = []
        seen: set[str] = set()
        for directory in discover_skill_dirs():
            for skill in load_skills_from_dir(directory):
                if skill.name not in seen:
                    skills.append(skill)
                    seen.add(skill.name)
    return {
        skill.name: {
            "name": skill.name,
            "description": skill.description,
            "managed_by": skill.provenance.get("managed_by"),
            "authored_by": skill.provenance.get("authored_by"),
        }
        for skill in skills
    }, tools


def registry_context():
    # Local import avoids making the service layer initialize the tool package.
    from core.tools.base import ToolContext

    return ToolContext.CHAT


def _string_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"proposal {field} must be an array of strings")
    return list(dict.fromkeys(item.strip() for item in value if item.strip()))


def _normalize_proposal(
    doc: dict[str, Any],
    *,
    existing_skills: dict[str, dict[str, Any]],
    available_tools: set[str],
    min_confidence: float,
) -> dict[str, Any] | None:
    if "proposal" not in doc:
        raise ValueError("review response is missing the proposal field")
    raw = doc["proposal"]
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("review proposal must be an object or null")

    name = str(raw.get("name") or "").strip()
    description = str(raw.get("description") or "").strip()
    content = str(raw.get("content") or "").strip()
    rationale = str(raw.get("rationale") or "").strip()
    mode = str(raw.get("mode") or "create").strip()
    category = str(raw.get("category") or SkillCategory.OTHER.value).strip()
    try:
        confidence = float(raw.get("confidence"))
    except (TypeError, ValueError) as exc:
        raise ValueError("proposal confidence must be numeric") from exc

    if not _NAME_RE.fullmatch(name):
        raise ValueError("proposal name must be lowercase kebab-case/underscore, 2-64 chars")
    if not description:
        raise ValueError("proposal description is required")
    if len(content) < 120:
        raise ValueError("proposal content must be substantive (at least 120 characters)")
    if not rationale:
        raise ValueError("proposal rationale is required")
    if category not in {item.value for item in SkillCategory}:
        raise ValueError(f"unknown proposal category: {category}")
    if mode not in {"create", "update"}:
        raise ValueError("proposal mode must be create or update")
    if not 0 <= confidence <= 1:
        raise ValueError("proposal confidence must be between 0 and 1")
    if confidence < min_confidence:
        return None

    contexts = _string_list(raw.get("contexts") or ["chat", "heartbeat"], "contexts")
    unknown_contexts = sorted(set(contexts) - {item.value for item in SkillContext})
    if unknown_contexts:
        raise ValueError("unknown proposal context(s): " + ", ".join(unknown_contexts))
    bound_tools = _string_list(raw.get("bound_tools"), "bound_tools")
    requires_tools = _string_list(raw.get("requires_tools"), "requires_tools") or bound_tools[:]
    unknown_tools = sorted((set(bound_tools) | set(requires_tools)) - available_tools)
    if unknown_tools:
        raise ValueError("unknown proposal tool(s): " + ", ".join(unknown_tools))

    existing = existing_skills.get(name)
    if mode == "create" and existing is not None:
        raise ValueError(f"proposal cannot create existing skill: {name}")
    if mode == "update" and (
        existing is None
        or existing.get("authored_by") != "hexis"
        or existing.get("managed_by") != "author_skill"
    ):
        raise ValueError(f"proposal cannot update skill without Hexis ownership: {name}")
    if _SECRET_VALUE_RE.search("\n".join((description, content, rationale))):
        raise ValueError("proposal appears to contain credential or secret material")

    return {
        "name": name,
        "description": description,
        "content": content,
        "category": category,
        "contexts": contexts,
        "bound_tools": bound_tools,
        "requires_tools": requires_tools,
        "mode": mode,
        "rationale": rationale,
        "confidence": confidence,
    }


async def run_skill_improvement_review_step(conn, *, registry: Any | None = None) -> dict[str, Any]:
    """Run one due review and persist at most one proposal."""
    claimed = bool(await conn.fetchval("SELECT claim_skill_improvement_review()"))
    if not claimed:
        return {"skipped": True, "reason": "disabled_not_due_or_claimed"}

    result: dict[str, Any]
    try:
        evidence = _coerce_json(await conn.fetchval("SELECT load_skill_improvement_evidence()"))
        if not evidence.get("eligible"):
            result = {
                "status": "no_evidence",
                "reason": evidence.get("reason") or "insufficient_evidence",
                "unit_count": int(evidence.get("unit_count") or 0),
                "session_count": int(evidence.get("session_count") or 0),
            }
        else:
            existing, available_tools = _catalog(registry)
            if not available_tools:
                rows = await conn.fetch("SELECT name FROM tool_definitions ORDER BY name")
                available_tools = {str(row["name"]) for row in rows}
            min_confidence = float(
                await conn.fetchval(
                    "SELECT COALESCE(get_config_float('skills.self_improvement.min_confidence'), 0.8)"
                )
                or 0.8
            )
            llm_config = await load_llm_config(
                conn, "llm.skill_improvement", fallback_key="llm.subconscious"
            )
            review_context = {
                "constraints": {
                    "minimum_confidence": min_confidence,
                    "available_categories": [item.value for item in SkillCategory],
                    "available_contexts": [item.value for item in SkillContext],
                    "available_tools": sorted(available_tools),
                },
                "existing_skills": sorted(existing.values(), key=lambda item: item["name"]),
                "evidence": evidence,
            }
            doc, raw = await chat_json(
                llm_config=llm_config,
                messages=[
                    {"role": "system", "content": load_skill_improvement_prompt().strip()},
                    {"role": "user", "content": json.dumps(review_context, default=str)[:30000]},
                ],
                max_tokens=2400,
                temperature=0.1,
                response_format={"type": "json_object"},
                fallback={},
            )
            parsed_raw = extract_json_object(raw)
            if not parsed_raw or "proposal" not in parsed_raw:
                raise ValueError("skill-improvement model returned invalid JSON")
            proposal = _normalize_proposal(
                doc,
                existing_skills=existing,
                available_tools=available_tools,
                min_confidence=min_confidence,
            )
            if proposal is None:
                result = {"status": "no_proposal", "reason": "insufficient_recurrence_or_confidence"}
            else:
                source_ids = sorted(str(value) for value in evidence.get("source_unit_ids") or [])
                digest = hashlib.sha256("\n".join(source_ids).encode("utf-8")).hexdigest()
                created = _coerce_json(
                    await conn.fetchval(
                        "SELECT create_skill_improvement_proposal($1::jsonb, $2::jsonb, $3::text)",
                        json.dumps(proposal),
                        json.dumps(evidence),
                        digest,
                    )
                )
                result = {"status": "proposed", **created, "skill": proposal["name"]}
    except Exception as exc:
        logger.error("skill-improvement review failed: %s", exc)
        result = {"status": "error", "error": str(exc)}

    await conn.fetchval("SELECT mark_skill_improvement_review($1::jsonb)", json.dumps(result))
    return result
