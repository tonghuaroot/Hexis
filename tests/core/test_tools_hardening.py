import json
import sys
import types
from types import SimpleNamespace

import pytest

from core.tools.base import ToolExecutionContext, ToolContext, ToolErrorType
from core.tools.registry import ToolRegistry
from core.tools.config import ToolsConfig
from core.tools.policy import PolicyCheckResult
from core.tools.base import ToolHandler, ToolResult, ToolSpec, ToolCategory
from core.tools.web import WebFetchHandler, WebSummarizeHandler
class DummyHandler(ToolHandler):
    def __init__(self, name: str, cost: int, supports_parallel: bool = True):
        self._spec = ToolSpec(
            name=name,
            description="dummy",
            parameters={"type": "object", "properties": {}},
            category=ToolCategory.MEMORY,
            energy_cost=cost,
            is_read_only=True,
            supports_parallel=supports_parallel,
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    async def execute(self, arguments, context):
        return ToolResult.success_result({"ok": True})


@pytest.mark.core
def test_is_path_allowed_blocks_prefix_escape(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ctx = ToolExecutionContext(tool_context=ToolContext.CHAT, call_id="x", workspace_path=str(workspace))

    assert ctx.is_path_allowed(str(workspace / "file.txt")) is True

    sneaky = tmp_path / "workspace_evil" / "file.txt"
    sneaky.parent.mkdir()
    assert ctx.is_path_allowed(str(sneaky)) is False


@pytest.mark.core
def test_is_path_allowed_blocks_symlink_escape(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")

    link = workspace / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform")

    ctx = ToolExecutionContext(tool_context=ToolContext.CHAT, call_id="x", workspace_path=str(workspace))
    assert ctx.is_path_allowed(str(link / "secret.txt")) is False


@pytest.mark.core
def test_is_path_allowed_allows_nonexistent_within_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ctx = ToolExecutionContext(tool_context=ToolContext.CHAT, call_id="x", workspace_path=str(workspace))

    assert ctx.is_path_allowed(str(workspace / "new.txt")) is True


@pytest.mark.core
def test_web_url_private_ip_validation():
    handler = WebFetchHandler()
    errs = handler.validate({"url": "http://172.16.0.1/test"})
    assert any("internal" in e.lower() for e in errs)

    errs = handler.validate({"url": "http://127.0.0.1/test"})
    assert any("localhost" in e.lower() or "internal" in e.lower() for e in errs)

    errs = handler.validate({"url": "http://172.0.0.1/test"})
    assert not any("internal" in e.lower() for e in errs)


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.core
async def test_execute_batch_parallel_energy_budget(monkeypatch):
    registry = ToolRegistry(pool=object())
    registry.register(DummyHandler("tool_a", cost=3))
    registry.register(DummyHandler("tool_b", cost=3))

    async def _allow(*args, **kwargs):
        return PolicyCheckResult.allow()

    async def _config():
        return ToolsConfig()

    monkeypatch.setattr(registry._policy, "check_all", _allow)
    monkeypatch.setattr(registry, "get_config", _config)

    context = ToolExecutionContext(
        tool_context=ToolContext.HEARTBEAT,
        call_id="x",
        energy_available=3,
    )

    results = await registry.execute_batch(
        [("tool_a", {}), ("tool_b", {})],
        context,
        parallel=True,
    )

    assert results[0].success is True
    assert results[1].success is False
    assert results[1].error_type == ToolErrorType.INSUFFICIENT_ENERGY


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.core
async def test_web_summarize_uses_direct_llm(monkeypatch):
    """web_summarize summarizes via a single in-process LLM call (the broken
    external_calls queue+poll path is gone)."""
    dummy_trafilatura = types.SimpleNamespace(
        fetch_url=lambda url: "<html></html>",
        extract=lambda downloaded, include_tables=True: "content",
        extract_metadata=lambda downloaded: types.SimpleNamespace(title="Title"),
    )
    monkeypatch.setitem(sys.modules, "trafilatura", dummy_trafilatura)

    async def _fake_load_llm_config(pool, **kwargs):
        return {"provider": "openai", "model": "gpt-4o", "endpoint": None, "api_key": "t"}

    captured: dict = {}

    async def _fake_chat_completion(**kwargs):
        captured.update(kwargs)
        return {"content": "A concise summary.", "raw": None}

    monkeypatch.setattr("core.llm_config.load_llm_config", _fake_load_llm_config)
    monkeypatch.setattr("core.llm.chat_completion", _fake_chat_completion)

    context = ToolExecutionContext(tool_context=ToolContext.CHAT, call_id="x")
    context.registry = SimpleNamespace(pool=SimpleNamespace())  # only handed to load_llm_config

    handler = WebSummarizeHandler()
    result = await handler.execute({"url": "http://example.com"}, context)

    assert result.success is True
    assert result.output["summary"] == "A concise summary."
    assert captured["max_tokens"] == 500
    assert captured["messages"][0]["role"] == "user"
