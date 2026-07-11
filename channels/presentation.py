"""Portable, presentation-only message blocks for every Hexis surface.

Presentation never replaces the canonical conversation text stored by the
agent.  It gives each delivery surface enough structure to render that text
without learning platform-specific payloads.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, TypeAlias


class MarkdownDialect(str, Enum):
    """Text formatting understood by a delivery surface."""

    PLAIN = "plain"
    MARKDOWN = "markdown"
    SLACK = "slack-mrkdwn"
    TELEGRAM = "telegram-markdown"


PresentationTone: TypeAlias = Literal["neutral", "info", "success", "warning", "danger"]


@dataclass(frozen=True)
class TextBlock:
    """Primary markdown-ish message text."""

    text: str


@dataclass(frozen=True)
class ContextBlock:
    """Lower-emphasis supporting text."""

    text: str


@dataclass(frozen=True)
class DividerBlock:
    """A semantic break between adjacent blocks."""


PresentationBlock: TypeAlias = TextBlock | ContextBlock | DividerBlock


@dataclass(frozen=True)
class MessagePresentation:
    """Ordered portable blocks plus optional presentation metadata."""

    blocks: tuple[PresentationBlock, ...] = ()
    title: str | None = None
    tone: PresentationTone = "neutral"

    def __post_init__(self) -> None:
        if not isinstance(self.blocks, tuple):
            object.__setattr__(self, "blocks", tuple(self.blocks))
        if self.title is not None and not self.title.strip():
            raise ValueError("presentation title must not be blank")
        if not self.title and not self.blocks:
            raise ValueError("presentation requires a title or at least one block")
        if self.tone not in {"neutral", "info", "success", "warning", "danger"}:
            raise ValueError(f"unsupported presentation tone: {self.tone!r}")
        for index, block in enumerate(self.blocks):
            if not isinstance(block, (TextBlock, ContextBlock, DividerBlock)):
                raise TypeError(f"unsupported presentation block at index {index}")
            if isinstance(block, (TextBlock, ContextBlock)) and not block.text.strip():
                raise ValueError(f"presentation block {index} text must not be blank")

    def to_dict(self) -> dict[str, Any]:
        blocks: list[dict[str, Any]] = []
        for block in self.blocks:
            if isinstance(block, TextBlock):
                blocks.append({"type": "text", "text": block.text})
            elif isinstance(block, ContextBlock):
                blocks.append({"type": "context", "text": block.text})
            else:
                blocks.append({"type": "divider"})
        result: dict[str, Any] = {"blocks": blocks, "tone": self.tone}
        if self.title:
            result["title"] = self.title
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "MessagePresentation":
        raw_blocks = value.get("blocks", [])
        if not isinstance(raw_blocks, list):
            raise ValueError("presentation.blocks must be a list")

        blocks: list[PresentationBlock] = []
        for index, raw in enumerate(raw_blocks):
            if not isinstance(raw, dict):
                raise ValueError(f"presentation.blocks[{index}] must be an object")
            block_type = raw.get("type")
            if block_type in {"text", "context"}:
                text = raw.get("text")
                if not isinstance(text, str) or not text.strip():
                    raise ValueError(
                        f"presentation.blocks[{index}].text must be non-blank text"
                    )
                block = TextBlock(text) if block_type == "text" else ContextBlock(text)
                blocks.append(block)
            elif block_type == "divider":
                blocks.append(DividerBlock())
            else:
                raise ValueError(
                    f"presentation.blocks[{index}].type is unsupported: {block_type!r}"
                )

        title = value.get("title")
        if title is not None and not isinstance(title, str):
            raise ValueError("presentation.title must be text")
        tone = value.get("tone", "neutral")
        if not isinstance(tone, str):
            raise ValueError("presentation.tone must be text")
        return cls(blocks=tuple(blocks), title=title, tone=tone)  # type: ignore[arg-type]


def normalize_message_presentation(value: Any) -> MessagePresentation:
    """Normalize a wire payload without silently dropping malformed blocks."""

    if isinstance(value, MessagePresentation):
        return value
    if not isinstance(value, dict):
        raise ValueError("presentation must be an object")
    return MessagePresentation.from_dict(value)


def presentation_from_text(text: str) -> MessagePresentation:
    """Wrap canonical text in the portable presentation contract."""

    return MessagePresentation(blocks=(TextBlock(text),))


def render_presentation(
    presentation: MessagePresentation,
    dialect: MarkdownDialect | str = MarkdownDialect.PLAIN,
) -> str:
    """Render portable blocks for a surface's declared text dialect."""

    try:
        resolved = MarkdownDialect(dialect)
    except ValueError:
        resolved = MarkdownDialect.PLAIN

    sections: list[str] = []
    if presentation.title:
        if resolved is MarkdownDialect.MARKDOWN:
            sections.append(f"**{presentation.title}**")
        elif resolved in {MarkdownDialect.SLACK, MarkdownDialect.TELEGRAM}:
            sections.append(f"*{presentation.title}*")
        else:
            sections.append(presentation.title)

    for block in presentation.blocks:
        if isinstance(block, DividerBlock):
            sections.append(
                "---" if resolved is not MarkdownDialect.PLAIN else "-" * 40
            )
        elif isinstance(block, ContextBlock) and resolved in {
            MarkdownDialect.MARKDOWN,
            MarkdownDialect.SLACK,
        }:
            sections.append("\n".join(f"> {line}" for line in block.text.splitlines()))
        else:
            sections.append(block.text)

    return "\n\n".join(sections)
