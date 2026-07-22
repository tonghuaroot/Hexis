import json

import pytest

from core.tools import ToolResult
from services.connector_setup import detect_connector_setup_intent, run_connector_setup_intent


class _NoDbPool:
    def acquire(self):  # pragma: no cover - should not be touched by these cases
        raise AssertionError("database should not be queried")


class _FakeConn:
    async def fetchval(self, _sql):
        return json.dumps(
            {
                "recent_attempts": [
                    {
                        "connector_id": "gmail",
                        "status": "pending_user",
                    }
                ]
            }
        )


class _Acquire:
    async def __aenter__(self):
        return _FakeConn()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _PendingPool:
    def acquire(self):
        return _Acquire()


class _FakeRegistry:
    def __init__(self):
        self.calls = []
        self.pool = _NoDbPool()

    async def execute(self, tool_name, arguments, context):
        self.calls.append((tool_name, arguments, context.session_id))
        return ToolResult.success_result(
            {
                "status": "needs_client_secret",
                "ui": {
                    "kind": "connector_setup",
                    "connector_id": "gmail",
                    "display_name": "Gmail",
                    "status": "needs_client_secret",
                },
            },
            display_output="Add a Google OAuth client JSON path.",
        )


@pytest.mark.asyncio
async def test_detects_natural_email_connection_request():
    intent = await detect_connector_setup_intent(
        _NoDbPool(),
        "Can you connect to my email?",
        session_id="setup-natural",
    )

    assert intent is not None
    assert intent.connector_id == "gmail"
    assert intent.action == "choose_scope"
    assert "capabilities" not in intent.arguments


@pytest.mark.asyncio
async def test_scope_then_memory_answers_route_without_llm():
    session_id = "setup-staged"
    first = await detect_connector_setup_intent(_NoDbPool(), "connect my email", session_id=session_id)
    assert first is not None

    registry = _FakeRegistry()
    opened = await run_connector_setup_intent(
        _NoDbPool(),
        registry,  # type: ignore[arg-type]
        first,
        session_id=session_id,
        source_channel="cli",
    )
    assert opened.action == "choose_scope"
    assert opened.ui is not None
    assert opened.ui["status"] == "needs_capability_choice"

    second = await detect_connector_setup_intent(_NoDbPool(), "just read them", session_id=session_id)
    assert second is not None
    assert second.action == "choose_memory"
    assert second.arguments["base_capabilities"] == ["read", "search"]

    prompted = await run_connector_setup_intent(
        _NoDbPool(),
        registry,  # type: ignore[arg-type]
        second,
        session_id=session_id,
        source_channel="cli",
    )
    assert prompted.ui is not None
    assert prompted.ui["status"] == "needs_memory_choice"
    assert prompted.ui["memory_config_key"] == "integrations.gmail.memory_policy"

    third = await detect_connector_setup_intent(_NoDbPool(), "forget what they say", session_id=session_id)
    assert third is not None
    assert third.action == "start"
    assert third.arguments["capabilities"] == ["read", "search"]
    assert third.arguments["memory_policy"] == "forget"


@pytest.mark.asyncio
async def test_direct_email_setup_can_include_powers_and_memory_policy():
    intent = await detect_connector_setup_intent(
        _NoDbPool(),
        "connect Gmail so you can send replies and remember what you read",
        session_id="setup-direct",
    )

    assert intent is not None
    assert intent.action == "start"
    assert intent.arguments["capabilities"] == ["read", "search", "send", "reply"]
    assert intent.arguments["memory_policy"] == "remember"


@pytest.mark.asyncio
async def test_delete_power_is_provider_capability_but_memory_is_config():
    intent = await detect_connector_setup_intent(
        _NoDbPool(),
        "connect my email so you can delete spam but forget what you read",
        session_id="setup-delete",
    )

    assert intent is not None
    assert intent.action == "start"
    assert intent.arguments["capabilities"] == [
        "read",
        "search",
        "send",
        "reply",
        "label",
        "spam_triage",
        "delete",
    ]
    assert "ingest" not in intent.arguments["capabilities"]
    assert intent.arguments["memory_policy"] == "forget"


@pytest.mark.asyncio
async def test_detects_google_oauth_client_secret_path():
    intent = await detect_connector_setup_intent(
        _NoDbPool(),
        "The Google OAuth client JSON is /Users/eric/Downloads/client_secret.json",
        session_id="setup-path-first",
    )

    assert intent is not None
    assert intent.action == "choose_scope"
    assert intent.arguments["client_secret_path"] == "/Users/eric/Downloads/client_secret.json"


@pytest.mark.asyncio
async def test_detects_pending_gmail_oauth_redirect_as_completion():
    intent = await detect_connector_setup_intent(
        _PendingPool(),
        "http://localhost:1/?state=abc&code=4/0abc",
    )

    assert intent is not None
    assert intent.action == "complete"
    assert intent.arguments["authorization_response"].startswith("http://localhost")


@pytest.mark.asyncio
async def test_run_connector_setup_returns_assistant_text_and_ui():
    intent = await detect_connector_setup_intent(_NoDbPool(), "connect gmail", session_id="session-1")
    assert intent is not None
    registry = _FakeRegistry()

    result = await run_connector_setup_intent(
        _NoDbPool(),
        registry,  # type: ignore[arg-type]
        intent,
        session_id="session-1",
        source_channel="cli",
    )

    assert result.assistant_message.startswith("Do you want me")
    assert result.ui is not None
    assert result.ui["kind"] == "connector_setup"
    assert result.ui["status"] == "needs_capability_choice"
    assert registry.calls == []


@pytest.mark.asyncio
async def test_run_connector_setup_start_passes_policy_separate_from_capabilities():
    intent = await detect_connector_setup_intent(
        _NoDbPool(),
        "connect gmail so you can reply and forget what you read",
        session_id="session-2",
    )
    assert intent is not None
    registry = _FakeRegistry()

    result = await run_connector_setup_intent(
        _NoDbPool(),
        registry,  # type: ignore[arg-type]
        intent,
        session_id="session-2",
        source_channel="cli",
    )

    assert result.ui is not None
    assert registry.calls == [
        (
            "connect_gmail",
            {
                "capabilities": ["read", "search", "send", "reply"],
                "memory_policy": "forget",
                "source_channel": "cli",
                "source_session_id": "session-2",
            },
            "session-2",
        )
    ]
