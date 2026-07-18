"""
Hexis Tools System - Ingestion Tools

Tools for content ingestion: fast (shallow), slow (conscious RLM), hybrid.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from .base import (
    ToolCategory,
    ToolContext,
    ToolErrorType,
    ToolExecutionContext,
    ToolHandler,
    ToolResult,
    ToolSpec,
)

logger = logging.getLogger(__name__)


async def _build_ingest_config(pool, **overrides: Any) -> "Config":
    """Build an ingestion Config with fully-resolved LLM credentials.

    Uses :func:`core.llm_config.resolve_llm_config` so that OAuth/token-refresh
    providers (Codex, Copilot, Gemini CLI, etc.) work correctly.
    """
    from core.agent_api import db_dsn_from_env
    from core.llm_config import resolve_llm_config
    from services.ingest import Config

    dsn = db_dsn_from_env()
    llm_config = await resolve_llm_config(pool, "llm.chat", fallback_key="llm")

    defaults: dict[str, Any] = {
        "dsn": dsn,
        "llm_config": llm_config,
    }
    defaults.update(overrides)
    return Config(**defaults)


class FastIngestHandler(ToolHandler):
    """Fast (shallow) content ingestion.

    Chunks content, extracts facts via LLM, creates semantic memories
    with basic graph linking. No deep reasoning -- quick and cheap.
    """

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="fast_ingest",
            description=(
                "Quickly ingest a file into memory. Chunks the content, extracts key "
                "facts, and stores them as semantic memories with basic graph links. "
                "Use for content that doesn't require deep analysis or when energy is limited."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path to ingest.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Optional title for the content.",
                    },
                },
                "required": ["path"],
            },
            category=ToolCategory.INGEST,
            energy_cost=2,
            is_read_only=False,
        )

    def validate(self, arguments: dict[str, Any]) -> list[str]:
        errors = []
        path = arguments.get("path", "")
        if not path or not str(path).strip():
            errors.append("path cannot be empty")
        return errors

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        from services.ingest import IngestionMode, IngestionPipeline

        path_str = str(arguments["path"]).strip()
        file_path = Path(path_str)

        if not file_path.exists():
            return ToolResult.error_result(
                f"File not found: {path_str}",
                ToolErrorType.FILE_NOT_FOUND,
            )

        pool = context.registry.pool
        config = await _build_ingest_config(pool, mode=IngestionMode.FAST)
        pipeline = IngestionPipeline(config)

        try:
            count = await pipeline.ingest_file(file_path)

            return ToolResult.success_result(
                {
                    "memories_created": count,
                    "path": path_str,
                    "mode": "fast",
                },
                display_output=f"Fast ingested {path_str}: {count} memories created.",
            )
        except Exception as e:
            logger.error("fast_ingest failed: %s", e)
            return ToolResult.error_result(
                f"Fast ingestion failed: {e}",
                ToolErrorType.EXECUTION_FAILED,
            )
        finally:
            await pipeline.close()


class SlowIngestHandler(ToolHandler):
    """Slow (conscious) content ingestion via RLM loop.

    Runs a mini-RLM loop per chunk: searches related memories, checks
    worldview, forms emotional reaction, writes analysis, decides
    acceptance level. Creates rich memories with emotional context,
    deep graph connections, and contested/questioned flags.
    """

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="slow_ingest",
            description=(
                "Deeply and consciously ingest a file into memory. Each chunk is "
                "processed through a reasoning loop: you'll search related memories, "
                "compare against your worldview, form emotional reactions, and decide "
                "whether to accept, contest, or question each piece of knowledge. "
                "Creates richly connected memories. Use for important content that "
                "deserves careful consideration."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path to ingest.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Optional title for the content.",
                    },
                },
                "required": ["path"],
            },
            category=ToolCategory.INGEST,
            energy_cost=5,
            is_read_only=False,
        )

    def validate(self, arguments: dict[str, Any]) -> list[str]:
        errors = []
        path = arguments.get("path", "")
        if not path or not str(path).strip():
            errors.append("path cannot be empty")
        return errors

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        from core.agent_api import db_dsn_from_env
        from services.ingest import (
            DocumentInfo,
            IngestionMode,
            IngestionPipeline,
            Sectioner,
            _hash_text,
            _word_count,
            get_reader,
        )
        from services.slow_ingest_rlm import run_slow_ingest

        path_str = str(arguments["path"]).strip()
        file_path = Path(path_str)

        if not file_path.exists():
            return ToolResult.error_result(
                f"File not found: {path_str}",
                ToolErrorType.FILE_NOT_FOUND,
            )

        pool = context.registry.pool
        config = await _build_ingest_config(pool, mode=IngestionMode.SLOW)
        pipeline = IngestionPipeline(config)

        try:
            reader = get_reader(file_path)
            content = reader.read(file_path)
            content_hash = _hash_text(content)

            title = arguments.get("title") or file_path.stem
            doc = DocumentInfo(
                title=title,
                source_type="file",
                content_hash=content_hash,
                word_count=_word_count(content),
                path=path_str,
                file_type=file_path.suffix.lower(),
            )

            sectioner = Sectioner(config.max_section_chars, config.chunk_overlap)
            sections = sectioner.split(content, file_path)

            dsn = config.dsn or db_dsn_from_env()
            llm_cfg = pipeline.llm._cfg

            result = await run_slow_ingest(
                pipeline=pipeline,
                doc=doc,
                sections=sections,
                llm_config=llm_cfg,
                dsn=dsn,
            )

            return ToolResult.success_result(
                {
                    "memories_created": result["memories_created"],
                    "chunks_processed": result["chunks_processed"],
                    "path": path_str,
                    "mode": "slow",
                },
                display_output=(
                    f"Slow ingested {path_str}: {result['memories_created']} memories "
                    f"from {result['chunks_processed']} chunks."
                ),
            )
        except Exception as e:
            logger.error("slow_ingest failed: %s", e)
            return ToolResult.error_result(
                f"Slow ingestion failed: {e}",
                ToolErrorType.EXECUTION_FAILED,
            )
        finally:
            await pipeline.close()


class HybridIngestHandler(ToolHandler):
    """Hybrid content ingestion: fast first pass, slow on high-signal chunks.

    Does a quick extraction pass to score all chunks, then runs the full
    RLM conscious reading loop only on chunks that are high-importance,
    contradict existing worldview, or relate to active goals.
    """

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="hybrid_ingest",
            description=(
                "Ingest a file using a hybrid approach: quickly scan all chunks to "
                "identify which ones are most important or potentially contradictory, "
                "then deeply process only those high-signal chunks through conscious "
                "reading. A good balance between thoroughness and energy efficiency."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path to ingest.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Optional title for the content.",
                    },
                },
                "required": ["path"],
            },
            category=ToolCategory.INGEST,
            energy_cost=3,
            is_read_only=False,
        )

    def validate(self, arguments: dict[str, Any]) -> list[str]:
        errors = []
        path = arguments.get("path", "")
        if not path or not str(path).strip():
            errors.append("path cannot be empty")
        return errors

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        from core.agent_api import db_dsn_from_env
        from services.ingest import (
            DocumentInfo,
            IngestionMode,
            IngestionPipeline,
            Sectioner,
            _hash_text,
            _word_count,
            get_reader,
        )
        from services.slow_ingest_rlm import run_hybrid_ingest

        path_str = str(arguments["path"]).strip()
        file_path = Path(path_str)

        if not file_path.exists():
            return ToolResult.error_result(
                f"File not found: {path_str}",
                ToolErrorType.FILE_NOT_FOUND,
            )

        pool = context.registry.pool
        config = await _build_ingest_config(pool, mode=IngestionMode.HYBRID)
        pipeline = IngestionPipeline(config)

        try:
            reader = get_reader(file_path)
            content = reader.read(file_path)
            content_hash = _hash_text(content)

            title = arguments.get("title") or file_path.stem
            doc = DocumentInfo(
                title=title,
                source_type="file",
                content_hash=content_hash,
                word_count=_word_count(content),
                path=path_str,
                file_type=file_path.suffix.lower(),
            )

            sectioner = Sectioner(config.max_section_chars, config.chunk_overlap)
            sections = sectioner.split(content, file_path)

            dsn = config.dsn or db_dsn_from_env()
            llm_cfg = pipeline.llm._cfg

            result = await run_hybrid_ingest(
                pipeline=pipeline,
                doc=doc,
                sections=sections,
                llm_config=llm_cfg,
                dsn=dsn,
            )

            return ToolResult.success_result(
                {
                    "memories_created": result["memories_created"],
                    "chunks_processed": result["chunks_processed"],
                    "slow_chunks": result["slow_chunks"],
                    "fast_chunks": result["fast_chunks"],
                    "path": path_str,
                    "mode": "hybrid",
                },
                display_output=(
                    f"Hybrid ingested {path_str}: {result['memories_created']} memories "
                    f"({result['slow_chunks']} slow, {result['fast_chunks']} fast chunks)."
                ),
            )
        except Exception as e:
            logger.error("hybrid_ingest failed: %s", e)
            return ToolResult.error_result(
                f"Hybrid ingestion failed: {e}",
                ToolErrorType.EXECUTION_FAILED,
            )
        finally:
            await pipeline.close()


class GitIngestHandler(ToolHandler):
    """Ingest a GitHub repository by cloning and processing its files."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="git_ingest",
            description=(
                "Clone a GitHub repository and ingest its contents into memory. "
                "Accepts a full URL (https://github.com/owner/repo) or shorthand "
                "(owner/repo). Filters out common junk directories (.git, node_modules, etc.)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "GitHub repo URL or owner/repo shorthand.",
                    },
                    "branch": {
                        "type": "string",
                        "description": "Branch to clone (default: repo default branch).",
                    },
                },
                "required": ["url"],
            },
            category=ToolCategory.INGEST,
            energy_cost=4,
            is_read_only=False,
        )

    def validate(self, arguments: dict[str, Any]) -> list[str]:
        errors = []
        url = arguments.get("url", "")
        if not url or not str(url).strip():
            errors.append("url cannot be empty")
        return errors

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        import shutil
        import subprocess
        import tempfile

        from services.ingest import IngestionMode, IngestionPipeline

        repo = str(arguments["url"]).strip()
        if "/" in repo and not repo.startswith("http"):
            repo = f"https://github.com/{repo}"

        branch = arguments.get("branch")

        pool = context.registry.pool
        config = await _build_ingest_config(pool, mode=IngestionMode.FAST)
        pipeline = IngestionPipeline(config)
        tmpdir = tempfile.mkdtemp(prefix="hexis_git_")

        try:
            cmd = ["git", "clone", "--depth", "1"]
            if branch:
                cmd.extend(["--branch", str(branch)])
            cmd.extend([repo, tmpdir])

            await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: subprocess.check_call(
                    cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
                ),
            )

            count = await pipeline.ingest_directory(
                Path(tmpdir),
                recursive=True,
                exclude_dirs=IngestionPipeline.GIT_IGNORE_DIRS,
            )

            return ToolResult.success_result(
                {
                    "memories_created": count,
                    "repo": repo,
                    "branch": branch,
                },
                display_output=f"Ingested {repo}: {count} memories created.",
            )
        except subprocess.CalledProcessError as e:
            logger.error("git_ingest clone failed: %s", e)
            return ToolResult.error_result(
                f"Git clone failed: {e}",
                ToolErrorType.EXECUTION_FAILED,
            )
        except Exception as e:
            logger.error("git_ingest failed: %s", e)
            return ToolResult.error_result(
                f"Git ingestion failed: {e}",
                ToolErrorType.EXECUTION_FAILED,
            )
        finally:
            await pipeline.close()
            shutil.rmtree(tmpdir, ignore_errors=True)


def _detect_url_source_type(url: str) -> str:
    """Detect source type from a URL for routing to the right extractor.

    Returns one of: 'youtube', 'pdf', 'rss', 'web'.
    """
    from services.ingest import YouTubeTranscriptReader

    if YouTubeTranscriptReader.can_handle(url):
        return "youtube"

    # Check URL path extension
    from urllib.parse import urlparse
    parsed = urlparse(url)
    path_lower = parsed.path.lower()
    if path_lower.endswith(".pdf"):
        return "pdf"
    if path_lower.endswith((".rss", ".atom", ".xml")):
        # Heuristic: RSS/Atom feeds often have these extensions
        return "rss"

    return "web"


def _fetch_url_content(url: str, source_type: str) -> tuple[str, str]:
    """Fetch and extract content based on detected source type.

    Returns (content, actual_source_type) — source_type may be refined
    (e.g. if XML turns out not to be RSS).
    """
    if source_type == "youtube":
        from services.ingest import YouTubeTranscriptReader
        return YouTubeTranscriptReader.read(url), "youtube"

    if source_type == "pdf":
        import tempfile
        import urllib.request
        # Download PDF to temp file, then use PDFReader
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            urllib.request.urlretrieve(url, tmp.name)
            from services.ingest import PDFReader
            content = PDFReader.read(Path(tmp.name))
            import os
            os.unlink(tmp.name)
            header = f"[Source: {url}]\n[Format: PDF]\n\n"
            return header + content, "pdf"

    if source_type == "rss":
        try:
            from services.ingest import RssReader
            content = RssReader.read(url)
            if content.strip():
                return content, "rss"
        except Exception:
            pass
        # Fall back to web extraction if RSS parsing fails
        source_type = "web"

    # Default: web (HTML extraction via trafilatura)
    from services.ingest import WebReader
    return WebReader.read(url), "web"


class URLIngestHandler(ToolHandler):
    """Ingest web content from a URL into memory.

    Detects source type (HTML, PDF, YouTube, RSS) from the URL and routes
    to the appropriate extractor, then runs through the ingestion pipeline.
    """

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="url_ingest",
            description=(
                "Fetch a URL and ingest its content into memory. Automatically "
                "detects the source type: web pages (HTML), PDFs, YouTube videos "
                "(transcripts), and RSS/Atom feeds. Extracts readable text, "
                "then processes through the ingestion pipeline."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL to fetch and ingest (http:// or https://).",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["fast", "slow", "hybrid"],
                        "default": "fast",
                        "description": (
                            "Ingestion depth: 'fast' extracts key facts quickly, "
                            "'slow' runs deep conscious reasoning on each chunk, "
                            "'hybrid' triages then deep-reads important chunks."
                        ),
                    },
                    "title": {
                        "type": "string",
                        "description": "Optional title for the content (auto-detected if omitted).",
                    },
                },
                "required": ["url"],
            },
            category=ToolCategory.INGEST,
            energy_cost=3,
            is_read_only=False,
        )

    def validate(self, arguments: dict[str, Any]) -> list[str]:
        errors = []
        url = arguments.get("url", "")
        if not url or not str(url).strip():
            errors.append("url cannot be empty")
        elif not (str(url).startswith("http://") or str(url).startswith("https://")):
            errors.append("url must start with http:// or https://")
        else:
            from core.tools.web import _validate_url_host
            errors.extend(_validate_url_host(str(url)))
        return errors

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        import tempfile

        from services.ingest import IngestionMode, IngestionPipeline

        url = str(arguments["url"]).strip()
        mode_str = arguments.get("mode", "fast")
        mode_map = {
            "fast": IngestionMode.FAST,
            "slow": IngestionMode.SLOW,
            "hybrid": IngestionMode.HYBRID,
        }
        mode = mode_map.get(mode_str, IngestionMode.FAST)

        # Detect source type and fetch content
        source_type = _detect_url_source_type(url)

        try:
            content, source_type = await asyncio.get_running_loop().run_in_executor(
                None, _fetch_url_content, url, source_type
            )
        except Exception as e:
            return ToolResult.error_result(
                f"Failed to fetch URL ({source_type}): {e}",
                ToolErrorType.NETWORK_ERROR,
            )

        if not content or not content.strip():
            return ToolResult.error_result(
                f"No extractable content from {url}",
                ToolErrorType.EXECUTION_FAILED,
            )

        # The pipeline ingests text directly now (#89) — no temp-file dance.
        pool = context.registry.pool
        config = await _build_ingest_config(pool, mode=mode)
        pipeline = IngestionPipeline(config)
        try:
            count = await pipeline.ingest_text(
                content,
                title=arguments.get("title") or url,
                source_type=source_type,
            )

            return ToolResult.success_result(
                {
                    "memories_created": count,
                    "url": url,
                    "mode": mode_str,
                    "source_type": source_type,
                    "content_chars": len(content),
                },
                display_output=(
                    f"Ingested {url} ({source_type}, {mode_str}): {count} memories "
                    f"created from {len(content):,} chars."
                ),
            )
        except Exception as e:
            logger.error("url_ingest pipeline failed: %s", e)
            return ToolResult.error_result(
                f"URL ingestion failed: {e}",
                ToolErrorType.EXECUTION_FAILED,
            )
        finally:
            await pipeline.close()


def create_ingest_tools() -> list[ToolHandler]:
    """Create all ingestion tool handlers."""
    return [
        FastIngestHandler(),
        SlowIngestHandler(),
        HybridIngestHandler(),
        GitIngestHandler(),
        URLIngestHandler(),
    ]
