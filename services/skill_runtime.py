"""Skill-first runtime helpers.

Skills are the model-facing capability layer. Tools are implementation details:
the LLM sees only skill discovery tools plus tools bound by selected/activated
skills. This keeps prompts smaller and makes capability use intentional.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.tools.base import ToolContext
from skills.base import SkillContext, SkillSpec
from skills.loader import load_skills

if False:  # pragma: no cover - typing only
    from core.tools.registry import ToolRegistry


DISCOVERY_TOOL_NAMES = {"list_skills", "use_skill"}
DEFAULT_SKILL_NAMES = {"core-memory"}
HEARTBEAT_DEFAULT_SKILL_NAMES = {"core-memory", "self-reflection"}
AUTO_ACTIVATE_SCORE_THRESHOLD = 5
STOPWORDS = {
    "about", "after", "again", "also", "before", "could", "did", "does",
    "for", "from", "give", "have", "help", "into", "last", "more", "next",
    "please", "show", "that", "the", "then", "this", "time", "want", "what",
    "when", "where", "with", "would", "you",
}


@dataclass(frozen=True)
class SkillSelection:
    skills: list[SkillSpec]
    allowed_tool_names: set[str]
    # Full catalog for this context — used for the compact skill index in the
    # system prompt, so the model can discover skills without a list_skills call.
    available: list[SkillSpec] = field(default_factory=list)


def tool_context_to_skill_context(context: ToolContext) -> SkillContext:
    if context == ToolContext.HEARTBEAT:
        return SkillContext.HEARTBEAT
    if context == ToolContext.MCP:
        return SkillContext.MCP
    return SkillContext.CHAT


def _tokens(text: str) -> set[str]:
    return {
        t for t in re.findall(r"[a-z0-9_]+", text.lower())
        if len(t) >= 3 and t not in STOPWORDS
    }


def _score_skill(skill: SkillSpec, query_tokens: set[str]) -> int:
    if not query_tokens:
        return 0
    haystack = " ".join([skill.name, skill.description, skill.content[:1500]]).lower()
    score = 0
    name_tokens = _tokens(skill.name.replace("-", " "))
    desc_tokens = _tokens(skill.description)
    for tok in query_tokens:
        if tok in name_tokens:
            score += 5
        elif tok in desc_tokens:
            score += 3
        elif tok in haystack:
            score += 1
    return score


def _passes_specialized_gate(skill: SkillSpec, query_tokens: set[str]) -> bool:
    """Avoid auto-activating narrow integrations from generic overlap. They stay
    discoverable through `list_skills`/`use_skill`."""
    gates = {
        "twitter-research": {"twitter", "tweet", "tweets", "x", "social", "sentiment"},
        "youtube-analytics": {"youtube", "video", "channel", "subscriber", "subscribers"},
        "image-gen": {"image", "picture", "draw", "illustration", "generate", "visual"},
        "cost-report": {"cost", "costs", "spend", "spent", "usage", "tokens", "budget", "bill"},
        "humanizer": {"humanize", "natural", "voice", "rewrite", "prose", "ai"},
        "skill-authoring": {"author", "write", "create", "update", "revise", "skill", "skills", "procedure"},
    }
    required = gates.get(skill.name)
    return True if required is None else bool(query_tokens & required)


def skill_bound_tools(skill: SkillSpec) -> list[str]:
    """Tools a skill may use. `bound_tools` is preferred; `requires.tools` is a
    fallback for older skill files."""
    return list(dict.fromkeys([*(skill.bound_tools or []), *skill.requires_tools]))


def _plugin_skill_dirs(registry: "ToolRegistry") -> list[Path]:
    """Skill directories contributed by plugins, carried on the tool registry."""
    dirs = getattr(registry, "extra_skill_dirs", None) or []
    return [Path(d) for d in dirs]


def load_available_skills(registry: "ToolRegistry", context: ToolContext) -> list[SkillSpec]:
    """All loadable skills for a context, including plugin-provided ones."""
    return load_skills(
        tool_context_to_skill_context(context),
        available_tools=set(registry.list_names()),
        available_config=None,  # tool handlers validate credentials at execution time
        extra_dirs=_plugin_skill_dirs(registry),
    )


async def select_skills(
    registry: "ToolRegistry",
    tool_context: ToolContext,
    *,
    query: str = "",
    max_skills: int = 4,
) -> SkillSelection:
    """Select active skills for this turn and derive the exposed tool set."""
    available_tools = set(registry.list_names())
    skills = load_available_skills(registry, tool_context)

    default_names = HEARTBEAT_DEFAULT_SKILL_NAMES if tool_context == ToolContext.HEARTBEAT else DEFAULT_SKILL_NAMES
    selected: list[SkillSpec] = [s for s in skills if s.name in default_names]

    selected_names = {s.name for s in selected}
    q_tokens = _tokens(query)
    scored = [
        (_score_skill(s, q_tokens), s)
        for s in skills
        if s.name not in selected_names and _passes_specialized_gate(s, q_tokens)
    ]
    for score, skill in sorted(scored, key=lambda item: (-item[0], item[1].name)):
        if score < AUTO_ACTIVATE_SCORE_THRESHOLD:
            continue
        selected.append(skill)
        selected_names.add(skill.name)
        if len(selected) >= max_skills:
            break

    allowed = set(DISCOVERY_TOOL_NAMES)
    for skill in selected:
        allowed.update(t for t in skill_bound_tools(skill) if t in available_tools)

    return SkillSelection(skills=selected, allowed_tool_names=allowed, available=skills)


def format_skills_prompt(
    active: list[SkillSpec],
    available: list[SkillSpec] | None = None,
) -> str:
    """Compact skill section for the system prompt.

    One index line per skill — never full skill bodies. Full instructions are
    fetched on demand via `use_skill`, and tool schemas ride the structured
    tool-calling API, so this block stays flat regardless of skill size.
    """
    lines = [
        "## Skills",
        "Use skills first: capabilities are packaged as skills, and a skill's "
        "tools are exposed through the tool API only while that skill is active. "
        "If the task needs a capability that is not active, call `use_skill` with "
        "the skill's name — it returns the skill's full instructions and unlocks "
        "its tools for this turn. `list_skills` shows the catalog with bound tools.",
    ]
    if active:
        lines.append("Active now:\n" + "\n".join(s.to_index_line() for s in active))
    active_names = {s.name for s in active}
    inactive = [s for s in (available or []) if s.name not in active_names]
    if inactive:
        lines.append(
            "Available (activate with `use_skill`):\n"
            + "\n".join(s.to_index_line() for s in inactive)
        )
    return "\n\n".join(lines)


def skill_catalog(registry: "ToolRegistry", context: ToolContext) -> list[dict[str, Any]]:
    available_tools = set(registry.list_names())
    skills = load_available_skills(registry, context)
    return [
        {
            "name": s.name,
            "description": s.description,
            "category": s.category.value,
            "bound_tools": [t for t in skill_bound_tools(s) if t in available_tools],
        }
        for s in skills
    ]


def get_skill_by_name(registry: "ToolRegistry", context: ToolContext, name: str) -> SkillSpec | None:
    wanted = name.strip().lower()
    skills = load_available_skills(registry, context)
    for skill in skills:
        if skill.name.lower() == wanted:
            return skill
    return None
