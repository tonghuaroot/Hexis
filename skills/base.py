"""
Hexis Skills System - Base Types

SkillSpec: the parsed representation of a skill markdown file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SkillContext(str, Enum):
    """Contexts in which a skill can be active."""

    HEARTBEAT = "heartbeat"
    CHAT = "chat"
    MCP = "mcp"


class SkillCategory(str, Enum):
    """Skill categories for organization and discovery."""

    RESEARCH = "research"
    PRODUCTIVITY = "productivity"
    COMMUNICATION = "communication"
    KNOWLEDGE = "knowledge"
    ANALYTICS = "analytics"
    CREATIVE = "creative"
    SYSTEM = "system"
    OTHER = "other"


@dataclass
class InstallMethod:
    """A method for installing a skill's binary dependency."""

    kind: str  # brew, apt, pip, npm, etc.
    package: str  # package name (formula for brew, package for apt/pip)
    bins: list[str] = field(default_factory=list)  # binaries provided

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "InstallMethod":
        return cls(
            kind=str(d.get("kind", "")),
            package=str(d.get("package", d.get("formula", ""))),
            bins=list(d.get("bins", [])),
        )


@dataclass
class SkillSpec:
    """Parsed representation of a skill document."""

    name: str
    description: str
    content: str  # Markdown body (after frontmatter)
    requires_tools: list[str] = field(default_factory=list)
    requires_config: list[str] = field(default_factory=list)
    requires_bins: list[str] = field(default_factory=list)  # J.1: binary deps
    requires_env: list[str] = field(default_factory=list)  # J.1: env var deps
    install_methods: list[InstallMethod] = field(default_factory=list)  # J.1
    category: SkillCategory = SkillCategory.OTHER  # J.1
    os_support: list[str] = field(default_factory=lambda: ["darwin", "linux"])  # J.1
    bound_tools: list[str] = field(default_factory=list)  # J.4: tool names to bind
    contexts: list[SkillContext] = field(
        default_factory=lambda: [SkillContext.HEARTBEAT, SkillContext.CHAT]
    )
    source: str = ""  # File path or plugin ID that provided this skill
    enabled: bool = True  # Can be disabled via config

    def requirements_met(
        self,
        available_tools: set[str],
        available_config: set[str] | None = None,
    ) -> bool:
        """Check if all requirements are satisfied."""
        for tool in self.requires_tools:
            if tool not in available_tools:
                return False
        if available_config is not None:
            for key in self.requires_config:
                if key not in available_config:
                    return False
        return True

    def check_bins_available(self) -> list[str]:
        """Return list of missing binary dependencies."""
        import shutil

        missing = []
        for b in self.requires_bins:
            if not shutil.which(b):
                missing.append(b)
        return missing

    def check_env_available(self) -> list[str]:
        """Return list of missing environment variables."""
        import os

        missing = []
        for var in self.requires_env:
            if not os.environ.get(var):
                missing.append(var)
        return missing

    def check_os_support(self) -> bool:
        """Check if the current OS is supported."""
        import sys

        return sys.platform in self.os_support

    def full_requirements_met(
        self,
        available_tools: set[str],
        available_config: set[str] | None = None,
    ) -> tuple[bool, list[str]]:
        """
        Full requirements check including bins, env vars, and OS.

        Returns (met, reasons) where reasons lists what's missing.
        """
        reasons: list[str] = []

        if not self.enabled:
            reasons.append("skill disabled")

        if not self.check_os_support():
            import sys
            reasons.append(f"OS {sys.platform} not in {self.os_support}")

        for tool in self.requires_tools:
            if tool not in available_tools:
                reasons.append(f"missing tool: {tool}")

        if available_config is not None:
            for key in self.requires_config:
                if key not in available_config:
                    reasons.append(f"missing config: {key}")

        missing_bins = self.check_bins_available()
        for b in missing_bins:
            reasons.append(f"missing binary: {b}")

        missing_env = self.check_env_available()
        for v in missing_env:
            reasons.append(f"missing env var: {v}")

        return (len(reasons) == 0, reasons)

    def to_prompt_block(self) -> str:
        """Format this skill's full instructions (returned by `use_skill`)."""
        return f"<skill name=\"{self.name}\">\n{self.content}\n</skill>"

    def to_index_line(self) -> str:
        """One-line entry for the compact skill index in the system prompt."""
        desc = " ".join(self.description.split())
        return f"- {self.name}: {desc}" if desc else f"- {self.name}"

    @classmethod
    def from_frontmatter(cls, metadata: dict[str, Any], content: str, source: str = "") -> "SkillSpec":
        """Create a SkillSpec from parsed YAML frontmatter and markdown body."""
        # Parse contexts
        raw_contexts = metadata.get("contexts", ["heartbeat", "chat"])
        if isinstance(raw_contexts, str):
            raw_contexts = [raw_contexts]
        contexts = []
        for ctx in raw_contexts:
            try:
                contexts.append(SkillContext(ctx))
            except ValueError:
                pass
        if not contexts:
            contexts = [SkillContext.HEARTBEAT, SkillContext.CHAT]

        # Parse requires
        requires = metadata.get("requires", {})
        if not isinstance(requires, dict):
            requires = {}

        # Parse category
        raw_category = metadata.get("category", "other")
        try:
            category = SkillCategory(raw_category)
        except ValueError:
            category = SkillCategory.OTHER

        # Parse install methods
        raw_install = metadata.get("install", [])
        if not isinstance(raw_install, list):
            raw_install = []
        install_methods = [InstallMethod.from_dict(m) for m in raw_install if isinstance(m, dict)]

        # Parse os_support
        raw_os = metadata.get("os_support", ["darwin", "linux"])
        if isinstance(raw_os, str):
            raw_os = [raw_os]

        # Parse bound_tools (J.4)
        raw_bound = metadata.get("bound_tools", [])
        if isinstance(raw_bound, str):
            raw_bound = [raw_bound]

        return cls(
            name=str(metadata.get("name", "")),
            description=str(metadata.get("description", "")),
            content=content.strip(),
            requires_tools=list(requires.get("tools", [])),
            requires_config=list(requires.get("config", [])),
            requires_bins=list(requires.get("bins", [])),
            requires_env=list(requires.get("env", [])),
            install_methods=install_methods,
            category=category,
            os_support=list(raw_os),
            bound_tools=list(raw_bound),
            contexts=contexts,
            source=source,
        )
