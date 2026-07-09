"""Skill discovery tools.

The model-facing capability layer is skills; tools are implementation details.
These handlers let the model discover available skills and activate one during a
turn. `AgentLoop` watches successful `use_skill` calls and exposes that skill's
bound tools on the next iteration.
"""

from __future__ import annotations

import json
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from services.skill_runtime import get_skill_by_name, skill_bound_tools, skill_catalog
from skills.base import SkillCategory, SkillContext
from skills.loader import load_skills_from_dir

from .base import (
    ToolCategory,
    ToolContext,
    ToolErrorType,
    ToolExecutionContext,
    ToolHandler,
    ToolResult,
    ToolSpec,
)


USER_AUTHORED_SKILLS_DIR = Path.home() / ".hexis" / "skills" / "agent-authored"
_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")


class ListSkillsHandler(ToolHandler):
    """List skills available in the current context."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="list_skills",
            description=(
                "List available skills. Use this when the task may need a capability "
                "that is not already active. Skills describe workflows and the tools "
                "they can unlock."
            ),
            parameters={"type": "object", "properties": {}},
            category=ToolCategory.EXTERNAL,
            energy_cost=0,
            is_read_only=True,
            allowed_contexts={ToolContext.CHAT, ToolContext.HEARTBEAT},
        )

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        if not context.registry:
            return ToolResult.error_result("No registry available", ToolErrorType.EXECUTION_FAILED)
        skills = skill_catalog(context.registry, context.tool_context)
        return ToolResult.success_result({"skills": skills}, f"{len(skills)} skill(s) available")


class UseSkillHandler(ToolHandler):
    """Activate a skill and return its instructions."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="use_skill",
            description=(
                "Activate a named skill for this turn. Returns the skill instructions "
                "and unlocks its bound tools for subsequent tool calls."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The skill name from list_skills, e.g. research or meeting-prep.",
                    },
                },
                "required": ["name"],
            },
            category=ToolCategory.EXTERNAL,
            energy_cost=0,
            is_read_only=True,
            allowed_contexts={ToolContext.CHAT, ToolContext.HEARTBEAT},
        )

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        if not context.registry:
            return ToolResult.error_result("No registry available", ToolErrorType.EXECUTION_FAILED)
        name = str(arguments.get("name") or "").strip()
        if not name:
            return ToolResult.error_result("Skill name is required", ToolErrorType.INVALID_PARAMS)
        skill = get_skill_by_name(context.registry, context.tool_context, name)
        if not skill:
            return ToolResult.error_result(f"Unknown or unavailable skill: {name}", ToolErrorType.INVALID_PARAMS)
        bound_tools = [
            t for t in skill_bound_tools(skill)
            if context.registry.get_spec(t) is not None
        ]
        return ToolResult.success_result(
            {
                "name": skill.name,
                "description": skill.description,
                "instructions": skill.content,
                "bound_tools": bound_tools,
            },
            f"Activated skill: {skill.name}",
        )


class AuthorSkillHandler(ToolHandler):
    """Create or update a user-scope Hexis skill document."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="author_skill",
            description=(
                "Create or update a Hexis skill. Use this when a useful workflow "
                "should become reusable future behavior. Writes only to the user "
                "skill directory and validates the resulting SKILL.md before saving."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Skill name, lowercase kebab-case, e.g. weekly-review.",
                    },
                    "description": {
                        "type": "string",
                        "description": "One concise sentence describing when to use the skill.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Markdown skill instructions, including method and quality guidance.",
                    },
                    "category": {
                        "type": "string",
                        "enum": [c.value for c in SkillCategory],
                        "description": "Skill category.",
                        "default": SkillCategory.OTHER.value,
                    },
                    "contexts": {
                        "type": "array",
                        "items": {"type": "string", "enum": [c.value for c in SkillContext]},
                        "description": "Contexts where the skill can activate.",
                        "default": [SkillContext.CHAT.value, SkillContext.HEARTBEAT.value],
                    },
                    "bound_tools": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Existing tools this skill may unlock.",
                        "default": [],
                    },
                    "requires_tools": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Existing tools required for this skill to load.",
                        "default": [],
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["create", "update"],
                        "description": "create refuses to overwrite; update requires an existing skill.",
                        "default": "create",
                    },
                    "rationale": {
                        "type": "string",
                        "description": "Why this skill is worth keeping.",
                    },
                },
                "required": ["name", "description", "content"],
            },
            category=ToolCategory.EXTERNAL,
            energy_cost=3,
            is_read_only=False,
            requires_approval=True,
            supports_parallel=False,
            allowed_contexts={ToolContext.CHAT, ToolContext.HEARTBEAT},
        )

    def validate(self, arguments: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        name = str(arguments.get("name") or "").strip()
        if not _SKILL_NAME_RE.match(name):
            errors.append("name must be lowercase kebab-case/underscore, 2-64 chars")
        if not str(arguments.get("description") or "").strip():
            errors.append("description is required")
        content = str(arguments.get("content") or "").strip()
        if len(content) < 120:
            errors.append("content must be substantive (at least 120 characters)")
        mode = str(arguments.get("mode") or "create")
        if mode not in {"create", "update"}:
            errors.append("mode must be create or update")
        for ctx in arguments.get("contexts") or [SkillContext.CHAT.value, SkillContext.HEARTBEAT.value]:
            if str(ctx) not in {c.value for c in SkillContext}:
                errors.append(f"unknown context: {ctx}")
        category = str(arguments.get("category") or SkillCategory.OTHER.value)
        if category not in {c.value for c in SkillCategory}:
            errors.append(f"unknown category: {category}")
        return errors

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        if not context.registry:
            return ToolResult.error_result("Skill authoring requires a registry reference", ToolErrorType.EXECUTION_FAILED)

        name = str(arguments["name"]).strip()
        description = str(arguments["description"]).strip()
        content = str(arguments["content"]).strip()
        category = str(arguments.get("category") or SkillCategory.OTHER.value)
        contexts = [str(v) for v in (arguments.get("contexts") or [SkillContext.CHAT.value, SkillContext.HEARTBEAT.value])]
        bound_tools = _string_list(arguments.get("bound_tools"))
        requires_tools = _string_list(arguments.get("requires_tools"))
        if not requires_tools:
            requires_tools = bound_tools[:]

        known_tools = set(context.registry.list_names())
        unknown = sorted((set(bound_tools) | set(requires_tools)) - known_tools)
        if unknown:
            return ToolResult.error_result(
                "Unknown tool(s) in skill metadata: " + ", ".join(unknown),
                ToolErrorType.INVALID_PARAMS,
            )

        mode = str(arguments.get("mode") or "create")
        skill_dir = USER_AUTHORED_SKILLS_DIR / name
        path = skill_dir / "SKILL.md"
        if mode == "create" and path.exists():
            return ToolResult.error_result(
                f"Skill '{name}' already exists; use mode='update' to replace it.",
                ToolErrorType.INVALID_PARAMS,
            )
        if mode == "update" and not path.exists():
            return ToolResult.error_result(
                f"Skill '{name}' does not exist; use mode='create' to create it.",
                ToolErrorType.FILE_NOT_FOUND,
            )

        skill_text = _render_skill_markdown(
            name=name,
            description=description,
            category=category,
            contexts=contexts,
            bound_tools=bound_tools,
            requires_tools=requires_tools,
            content=content,
            rationale=str(arguments.get("rationale") or "").strip(),
        )
        valid, detail = _validate_skill_text(name, skill_text)
        if not valid:
            return ToolResult.error_result(detail, ToolErrorType.INVALID_PARAMS)

        skill_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(skill_text, encoding="utf-8")
        return ToolResult.success_result(
            {
                "skill": name,
                "path": str(path),
                "mode": mode,
                "contexts": contexts,
                "bound_tools": bound_tools,
                "requires_tools": requires_tools,
                "discoverable_next_turn": True,
            },
            f"{mode.title()}d skill '{name}'",
        )


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]


def _render_skill_markdown(
    *,
    name: str,
    description: str,
    category: str,
    contexts: list[str],
    bound_tools: list[str],
    requires_tools: list[str],
    content: str,
    rationale: str,
) -> str:
    metadata = [
        "---",
        f"name: {json.dumps(name)}",
        f"description: {json.dumps(description)}",
        f"category: {json.dumps(category)}",
        "requires:",
        f"  tools: {json.dumps(requires_tools)}",
        f"contexts: {json.dumps(contexts)}",
        f"bound_tools: {json.dumps(bound_tools)}",
        "---",
        "",
    ]
    body = content.rstrip() + "\n"
    footer = [
        "",
        "## Provenance",
        "",
        "- Authored by Hexis via `author_skill`.",
        f"- Updated at: {datetime.now(UTC).isoformat()}",
    ]
    if rationale:
        footer.append(f"- Rationale: {rationale}")
    return "\n".join(metadata) + body + "\n".join(footer) + "\n"


def _validate_skill_text(expected_name: str, skill_text: str) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as tmp:
        skill_dir = Path(tmp) / expected_name
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(skill_text, encoding="utf-8")
        parsed = load_skills_from_dir(Path(tmp))
    if len(parsed) != 1:
        return False, "Generated skill did not parse as exactly one SKILL.md"
    skill = parsed[0]
    if skill.name != expected_name:
        return False, f"Generated skill parsed with unexpected name: {skill.name}"
    if not skill.description.strip():
        return False, "Generated skill has no description"
    if len(skill.content) < 120:
        return False, "Generated skill content is too short"
    return True, "ok"


def create_skill_tools() -> list[ToolHandler]:
    return [ListSkillsHandler(), UseSkillHandler(), AuthorSkillHandler()]
