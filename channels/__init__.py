"""
Hexis Channel System

Multi-channel messaging adapters that let users talk to the agent
from Discord, Telegram, and other platforms.
"""

from .base import (
    ChannelAdapter,
    ChannelCapabilities,
    ChannelMessage,
)
from .conversation import process_channel_message
from .manager import ChannelManager
from .media import Attachment
from .presentation import (
    ContextBlock,
    DividerBlock,
    MarkdownDialect,
    MessagePresentation,
    TextBlock,
    normalize_message_presentation,
    presentation_from_text,
    render_presentation,
)

__all__ = [
    "Attachment",
    "ChannelAdapter",
    "ChannelCapabilities",
    "ChannelMessage",
    "ChannelManager",
    "ContextBlock",
    "DividerBlock",
    "MarkdownDialect",
    "MessagePresentation",
    "TextBlock",
    "normalize_message_presentation",
    "presentation_from_text",
    "process_channel_message",
    "render_presentation",
]
