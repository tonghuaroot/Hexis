"""Portable channel presentation contract and delivery tests."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from channels.base import ChannelAdapter, ChannelCapabilities
from channels.manager import ChannelManager
from channels.outbox import ChannelOutboxConsumer, _resolve_payload_message
from channels.presentation import (
    ContextBlock,
    DividerBlock,
    MarkdownDialect,
    MessagePresentation,
    TextBlock,
    normalize_message_presentation,
    render_presentation,
)


def _presentation() -> MessagePresentation:
    return MessagePresentation(
        title="Deployment",
        tone="success",
        blocks=(
            TextBlock("**Ready** for review."),
            DividerBlock(),
            ContextBlock("Derived from the live check."),
        ),
    )


def test_presentation_wire_round_trip() -> None:
    presentation = _presentation()

    assert normalize_message_presentation(presentation.to_dict()) == presentation


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ({"blocks": "wrong"}, "presentation.blocks must be a list"),
        (
            {"blocks": [{"type": "buttons", "buttons": []}]},
            "presentation.blocks[0].type is unsupported",
        ),
        (
            {"blocks": [{"type": "text", "text": ""}]},
            "presentation.blocks[0].text must be non-blank text",
        ),
    ],
)
def test_malformed_presentation_fails_with_path(value, message: str) -> None:
    with pytest.raises(
        ValueError, match=message.replace("[", r"\[").replace("]", r"\]")
    ):
        normalize_message_presentation(value)


def test_renderers_preserve_order_and_degrade_context() -> None:
    presentation = _presentation()

    assert render_presentation(presentation) == (
        "Deployment\n\n**Ready** for review.\n\n"
        "----------------------------------------\n\nDerived from the live check."
    )
    assert render_presentation(presentation, MarkdownDialect.MARKDOWN) == (
        "**Deployment**\n\n**Ready** for review.\n\n---\n\n"
        "> Derived from the live check."
    )
    assert render_presentation(presentation, MarkdownDialect.SLACK).startswith(
        "*Deployment*\n\n"
    )
    assert render_presentation(presentation, MarkdownDialect.TELEGRAM) == (
        "*Deployment*\n\n**Ready** for review.\n\n---\n\n"
        "Derived from the live check."
    )


class _PresentationAdapter(ChannelAdapter):
    def __init__(self) -> None:
        self.sent: list[dict] = []

    @property
    def channel_type(self) -> str:
        return "presentation-test"

    @property
    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(max_message_length=30)

    async def start(self, on_message) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def send(
        self,
        channel_id,
        text,
        *,
        reply_to=None,
        thread_id=None,
    ):
        self.sent.append(
            {
                "channel_id": channel_id,
                "text": text,
                "reply_to": reply_to,
                "thread_id": thread_id,
            }
        )
        return f"message-{len(self.sent)}"

    async def send_typing(self, channel_id) -> None:
        return None


async def test_adapter_chunks_presentation_without_losing_reply_context() -> None:
    adapter = _PresentationAdapter()
    presentation = MessagePresentation(
        blocks=(TextBlock("First paragraph.\n\nSecond paragraph that is longer."),)
    )

    message_id = await adapter.send_presentation(
        "channel-1", presentation, reply_to="source-1", thread_id="thread-1"
    )

    assert message_id == "message-1"
    assert "".join(item["text"] for item in adapter.sent).replace(" ", "") == (
        "Firstparagraph.Secondparagraphthatislonger."
    )
    assert adapter.sent[0]["reply_to"] == "source-1"
    assert all(item["thread_id"] == "thread-1" for item in adapter.sent)
    assert all(item["reply_to"] is None for item in adapter.sent[1:])


async def test_manager_dispatches_portable_presentation() -> None:
    adapter = _PresentationAdapter()
    manager = ChannelManager(pool=MagicMock())
    manager.register(adapter)

    message_id = await manager.send("presentation-test", "channel-1", _presentation())

    assert message_id == "message-1"
    assert adapter.sent[0]["text"].startswith("Deployment")


async def test_outbox_routes_presentation_and_logs_plain_mirror() -> None:
    manager = MagicMock()
    manager.send = AsyncMock(return_value="sent-1")
    consumer = ChannelOutboxConsumer(manager, MagicMock())
    consumer._log_delivery = AsyncMock()
    body = {
        "kind": "channel_message",
        "id": "outbox-1",
        "payload": {
            "presentation": _presentation().to_dict(),
            "delivery_mode": "direct",
            "target_channel": "discord",
            "target_id": "channel-1",
        },
    }

    await consumer._process_message(body)

    outbound = manager.send.await_args.args[2]
    assert isinstance(outbound, MessagePresentation)
    assert outbound == _presentation()
    assert consumer._log_delivery.await_args.args[4].startswith("Deployment")


def test_outbox_text_payload_remains_backward_compatible() -> None:
    message, mirror = _resolve_payload_message({"content": "Legacy text"})

    assert message == "Legacy text"
    assert mirror == "Legacy text"


def test_live_adapter_dialects_match_native_send_paths() -> None:
    from channels.discord_adapter import DiscordAdapter
    from channels.matrix_adapter import MatrixAdapter
    from channels.slack_adapter import SlackAdapter
    from channels.telegram_adapter import TelegramAdapter

    assert DiscordAdapter().capabilities.markdown_dialect is MarkdownDialect.MARKDOWN
    assert TelegramAdapter().capabilities.markdown_dialect is MarkdownDialect.TELEGRAM
    assert SlackAdapter().capabilities.markdown_dialect is MarkdownDialect.SLACK
    assert MatrixAdapter().capabilities.markdown_dialect is MarkdownDialect.PLAIN
