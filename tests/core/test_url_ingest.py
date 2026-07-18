"""Tests for URLIngestHandler and multi-extractor pipeline (url_ingest tool)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.tools.base import ToolCategory, ToolContext, ToolErrorType, ToolExecutionContext
from core.tools.ingest import (
    URLIngestHandler,
    _detect_url_source_type,
    create_ingest_tools,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_context():
    registry = MagicMock()
    registry.pool = MagicMock()
    return ToolExecutionContext(
        tool_context=ToolContext.CHAT,
        call_id="test-call",
        registry=registry,
    )


# ---------------------------------------------------------------------------
# Spec tests
# ---------------------------------------------------------------------------

class TestURLIngestSpec:
    def test_spec_name(self):
        spec = URLIngestHandler().spec
        assert spec.name == "url_ingest"

    def test_spec_category(self):
        spec = URLIngestHandler().spec
        assert spec.category == ToolCategory.INGEST

    def test_spec_energy(self):
        spec = URLIngestHandler().spec
        assert spec.energy_cost == 3

    def test_spec_not_read_only(self):
        spec = URLIngestHandler().spec
        assert spec.is_read_only is False

    def test_spec_required_params(self):
        spec = URLIngestHandler().spec
        assert "url" in spec.parameters["required"]

    def test_spec_has_mode(self):
        spec = URLIngestHandler().spec
        props = spec.parameters["properties"]
        assert "mode" in props
        assert props["mode"]["enum"] == ["fast", "slow", "hybrid"]

    def test_spec_has_title(self):
        spec = URLIngestHandler().spec
        assert "title" in spec.parameters["properties"]

    def test_spec_description_mentions_source_types(self):
        spec = URLIngestHandler().spec
        desc = spec.description.lower()
        assert "pdf" in desc
        assert "youtube" in desc
        assert "rss" in desc


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------

class TestURLIngestValidation:
    def test_valid_url(self):
        handler = URLIngestHandler()
        errors = handler.validate({"url": "https://example.com/article"})
        assert errors == []

    def test_empty_url(self):
        handler = URLIngestHandler()
        errors = handler.validate({"url": ""})
        assert any("empty" in e.lower() for e in errors)

    def test_missing_url(self):
        handler = URLIngestHandler()
        errors = handler.validate({})
        assert any("empty" in e.lower() for e in errors)

    def test_no_scheme(self):
        handler = URLIngestHandler()
        errors = handler.validate({"url": "example.com"})
        assert any("http" in e.lower() for e in errors)

    def test_localhost_blocked(self):
        handler = URLIngestHandler()
        errors = handler.validate({"url": "http://localhost:8080/secret"})
        assert len(errors) > 0


# ---------------------------------------------------------------------------
# URL source type detection tests
# ---------------------------------------------------------------------------

class TestDetectURLSourceType:
    def test_youtube_watch_url(self):
        assert _detect_url_source_type("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "youtube"

    def test_youtube_short_url(self):
        assert _detect_url_source_type("https://youtu.be/dQw4w9WgXcQ") == "youtube"

    def test_youtube_with_params(self):
        assert _detect_url_source_type("https://youtube.com/watch?v=abc123def45&t=120") == "youtube"

    def test_pdf_url(self):
        assert _detect_url_source_type("https://example.com/paper.pdf") == "pdf"

    def test_pdf_url_case_insensitive(self):
        assert _detect_url_source_type("https://example.com/paper.PDF") == "pdf"

    def test_rss_url(self):
        assert _detect_url_source_type("https://example.com/feed.rss") == "rss"

    def test_atom_url(self):
        assert _detect_url_source_type("https://example.com/feed.atom") == "rss"

    def test_xml_url(self):
        assert _detect_url_source_type("https://example.com/rss.xml") == "rss"

    def test_regular_web_url(self):
        assert _detect_url_source_type("https://example.com/blog/article") == "web"

    def test_web_url_with_query(self):
        assert _detect_url_source_type("https://docs.python.org/3/library/asyncio.html") == "web"


# ---------------------------------------------------------------------------
# YouTube transcript reader tests
# ---------------------------------------------------------------------------

class TestYouTubeTranscriptReader:
    def test_extract_video_id_watch(self):
        from services.ingest import YouTubeTranscriptReader
        assert YouTubeTranscriptReader.extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_extract_video_id_short(self):
        from services.ingest import YouTubeTranscriptReader
        assert YouTubeTranscriptReader.extract_video_id("https://youtu.be/abc123def45") == "abc123def45"

    def test_extract_video_id_none(self):
        from services.ingest import YouTubeTranscriptReader
        assert YouTubeTranscriptReader.extract_video_id("https://example.com") is None

    def test_can_handle_youtube(self):
        from services.ingest import YouTubeTranscriptReader
        assert YouTubeTranscriptReader.can_handle("https://www.youtube.com/watch?v=xyz12345678") is True

    def test_cannot_handle_other(self):
        from services.ingest import YouTubeTranscriptReader
        assert YouTubeTranscriptReader.can_handle("https://example.com/article") is False


# ---------------------------------------------------------------------------
# Execution tests (mocked)
# ---------------------------------------------------------------------------

class TestURLIngestExecution:
    @pytest.mark.asyncio
    async def test_fetch_failure(self):
        handler = URLIngestHandler()
        ctx = _make_context()

        with patch("core.tools.ingest.asyncio") as mock_asyncio, \
             patch("core.tools.ingest._detect_url_source_type", return_value="web"):
            mock_loop = MagicMock()
            mock_asyncio.get_running_loop.return_value = mock_loop
            mock_loop.run_in_executor = AsyncMock(
                side_effect=RuntimeError("Connection refused")
            )

            result = await handler.execute(
                {"url": "https://example.com/article"}, ctx
            )

        assert not result.success
        assert result.error_type == ToolErrorType.NETWORK_ERROR

    @pytest.mark.asyncio
    async def test_empty_content(self):
        handler = URLIngestHandler()
        ctx = _make_context()

        with patch("core.tools.ingest.asyncio") as mock_asyncio, \
             patch("core.tools.ingest._detect_url_source_type", return_value="web"):
            mock_loop = MagicMock()
            mock_asyncio.get_running_loop.return_value = mock_loop
            mock_loop.run_in_executor = AsyncMock(return_value=("", "web"))

            result = await handler.execute(
                {"url": "https://example.com/empty"}, ctx
            )

        assert not result.success
        assert "No extractable content" in result.error

    @pytest.mark.asyncio
    async def test_successful_ingest(self):
        handler = URLIngestHandler()
        ctx = _make_context()

        content = "[Source: https://example.com/article]\n\nUseful content."

        call_count = 0

        async def mock_run_in_executor(executor, fn, *args):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (content, "web")
            return 5

        with patch("core.tools.ingest.asyncio") as mock_asyncio, \
             patch("core.tools.ingest._build_ingest_config") as mock_config, \
             patch("core.tools.ingest._detect_url_source_type", return_value="web"), \
             patch("services.ingest.IngestionPipeline") as mock_pipeline_cls:
            mock_loop = MagicMock()
            mock_asyncio.get_running_loop.return_value = mock_loop
            mock_loop.run_in_executor = mock_run_in_executor
            mock_config.return_value = MagicMock()
            pipeline = MagicMock()
            pipeline.ingest_text = AsyncMock(return_value=5)
            pipeline.close = AsyncMock()
            mock_pipeline_cls.return_value = pipeline

            result = await handler.execute(
                {"url": "https://example.com/article", "mode": "fast"}, ctx
            )

        assert result.success
        assert result.output["memories_created"] == 5
        assert result.output["url"] == "https://example.com/article"
        assert result.output["mode"] == "fast"
        assert result.output["source_type"] == "web"

    @pytest.mark.asyncio
    async def test_youtube_source_type_in_output(self):
        handler = URLIngestHandler()
        ctx = _make_context()

        transcript = "[Source: YouTube]\n\nHello world transcript."

        call_count = 0

        async def mock_run_in_executor(executor, fn, *args):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (transcript, "youtube")
            return 3

        with patch("core.tools.ingest.asyncio") as mock_asyncio, \
             patch("core.tools.ingest._build_ingest_config") as mock_config, \
             patch("core.tools.ingest._detect_url_source_type", return_value="youtube"), \
             patch("services.ingest.IngestionPipeline") as mock_pipeline_cls:
            mock_loop = MagicMock()
            mock_asyncio.get_running_loop.return_value = mock_loop
            mock_loop.run_in_executor = mock_run_in_executor
            mock_config.return_value = MagicMock()
            pipeline = MagicMock()
            pipeline.ingest_text = AsyncMock(return_value=3)
            pipeline.close = AsyncMock()
            mock_pipeline_cls.return_value = pipeline

            result = await handler.execute(
                {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}, ctx
            )

        assert result.success
        assert result.output["source_type"] == "youtube"


# ---------------------------------------------------------------------------
# Factory tests
# ---------------------------------------------------------------------------

class TestFactory:
    def test_url_ingest_in_factory(self):
        tools = create_ingest_tools()
        names = {t.spec.name for t in tools}
        assert "url_ingest" in names

    def test_factory_count(self):
        tools = create_ingest_tools()
        assert len(tools) == 5  # fast, slow, hybrid, git, url
