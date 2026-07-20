"""Hexis ingestion — split from the former services/ingest.py (#89).
Module: config.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional
from uuid import UUID


# =========================================================================
# CONFIGURATION
# =========================================================================


class IngestionMode(str, Enum):
    """Ingestion mode taxonomy:

    FAST   -- chunk, appraise, extract facts (default)
    SLOW   -- RLM conscious reading per chunk
    HYBRID -- fast triage then selective RLM on high-signal chunks
    """

    FAST = "fast"
    SLOW = "slow"
    HYBRID = "hybrid"


@dataclass
class Config:
    """Pipeline configuration."""

    # LLM Settings -- a fully-resolved llm_config dict (from resolve_llm_config / load_llm_config)
    llm_config: dict[str, Any] | None = None

    # Database Settings (unified -- pass a DSN string)
    dsn: str | None = None
    # Legacy DB fields (used when dsn is None -- standalone CLI)
    db_host: str = "localhost"
    db_port: int = 43815
    db_name: str = "hexis_memory"
    db_user: str = "postgres"
    db_password: str = "password"

    # Mode
    mode: IngestionMode = IngestionMode.FAST

    # Internal threshold: docs <= this word count get per-section appraisal
    deep_max_words: int = 2000

    # Chunking
    max_section_chars: int = 2000
    chunk_overlap: int = 200

    # Extraction
    max_facts_per_section: int = 20
    min_confidence_threshold: float = 0.6
    skip_sections: list[str] = field(
        default_factory=lambda: ["references", "bibliography", "acknowledgments", "appendix"]
    )

    # Persistence overrides
    min_importance_floor: float | None = None
    permanent: bool = False

    # Source trust override
    base_trust: float | None = None

    # Sensitivity marking (#92): 'private' keeps this content out of group
    # channels and HMX exports; the agent still sees it in 1:1.
    sensitivity: str | None = None

    # Concurrency (#90): bounded LLM fan-out and directory parallelism.
    max_parallel_llm: int = 4
    max_parallel_files: int = 2

    # Retry: one re-ask when a completion parses to empty JSON. Transient
    # HTTP/network retry lives at the provider layer (core/llm.py).
    llm_json_retries: int = 1

    # Processing
    verbose: bool = True
    log: Optional[Callable[[str], None]] = None
    cancel_check: Optional[Callable[[], bool]] = None


# =========================================================================
# DATA STRUCTURES
# =========================================================================


@dataclass
class Section:
    title: str
    content: str
    index: int


@dataclass
class DocumentInfo:
    title: str
    source_type: str
    content_hash: str
    word_count: int
    path: str
    file_type: str
    document_id: str | None = None


@dataclass
class Appraisal:
    valence: float = 0.0
    arousal: float = 0.3
    primary_emotion: str = "neutral"
    intensity: float = 0.0
    goal_relevance: list[dict[str, Any]] = field(default_factory=list)
    worldview_tension: float = 0.0
    curiosity: float = 0.0
    summary: str = ""

    def to_state_payload(self, source: str = "ingest") -> dict[str, Any]:
        return {
            "valence": self.valence,
            "arousal": self.arousal,
            "primary_emotion": self.primary_emotion,
            "intensity": self.intensity,
            "source": source,
        }


@dataclass
class Extraction:
    content: str
    category: str
    confidence: float
    importance: float
    why: str | None = None
    connections: list[str] = field(default_factory=list)
    supports: str | None = None
    contradicts: str | None = None
    concepts: list[str] = field(default_factory=list)


@dataclass
class IngestionMetrics:
    """Metrics collected during ingestion for observability."""

    source_type: str = ""
    source_size_bytes: int = 0
    word_count: int = 0
    mode: str = ""
    appraisal_valence: float = 0.0
    appraisal_arousal: float = 0.0
    appraisal_emotion: str = "neutral"
    appraisal_intensity: float = 0.0
    extraction_count: int = 0
    dedup_count: int = 0
    memory_count: int = 0
    llm_calls: int = 0
    duration_seconds: float = 0.0
    errors: list[str] = field(default_factory=list)
    start_time: float = field(default_factory=lambda: 0.0)


# =========================================================================
# HELPERS
# =========================================================================


def _emit(config: Config, message: str) -> None:
    if config.log:
        config.log(message)
    else:
        print(message)


def _should_cancel(config: Config) -> bool:
    if config.cancel_check:
        try:
            return bool(config.cancel_check())
        except Exception:
            return False
    return False


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text))


def _normalize_mode(mode: IngestionMode | str | None) -> IngestionMode:
    if isinstance(mode, IngestionMode):
        return mode
    raw = str(mode or "fast").strip().lower()
    # Legacy modes all collapse to FAST
    if raw in ("auto", "standard", "deep", "shallow", "archive"):
        return IngestionMode.FAST
    for item in IngestionMode:
        if raw == item.value:
            return item
    return IngestionMode.FAST


def _infer_source_type(file_path: Path) -> str:
    suffix = file_path.suffix.lower()
    if suffix in {".pdf", ".md", ".markdown", ".txt", ".text", ".rtf", ".docx", ".tex", ".bib", ".epub"}:
        return "document"
    if suffix == ".pptx":
        return "presentation"
    if suffix in {".xlsx", ".xls"}:
        return "spreadsheet"
    if suffix in {".eml", ".mbox"}:
        return "email"
    if suffix == ".ipynb":
        return "code"
    if suffix in CodeReader.LANGUAGE_MAP:
        return "code"
    if suffix in {".json", ".yaml", ".yml", ".csv", ".xml"}:
        return "data"
    if suffix in ImageReader.IMAGE_EXTENSIONS:
        return "image"
    if suffix in AudioReader.AUDIO_EXTENSIONS:
        return "audio"
    if suffix in VideoReader.VIDEO_EXTENSIONS:
        return "video"
    return "document"


def _extract_title(content: str, file_path: Path) -> str:
    # Try markdown header
    header_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    if header_match:
        return header_match.group(1).strip()
    # Try first non-empty line
    for line in content.splitlines():
        if line.strip():
            return line.strip()[:120]
    return file_path.stem


# Config keys the DB owns (#91): tuning ingestion means set_config, never a
# rebuild. Explicit Config(...) arguments and CLI flags still override.
INGEST_CONFIG_KEYS = {
    "deep_max_words": "ingest.deep_max_words",
    "max_section_chars": "ingest.max_section_chars",
    "chunk_overlap": "ingest.chunk_overlap",
    "max_facts_per_section": "ingest.max_facts_per_section",
    "min_confidence_threshold": "ingest.min_confidence_threshold",
    "max_parallel_llm": "ingest.max_parallel_llm",
    "max_parallel_files": "ingest.max_parallel_files",
    "llm_json_retries": "ingest.llm_json_retries",
}


async def load_ingest_settings(conn) -> dict[str, Any]:
    """Read the ingest.* policy keys from config; absent keys fall back to
    the dataclass defaults (which mirror the seeded values)."""
    import json as _json

    settings: dict[str, Any] = {}
    for field_name, key in INGEST_CONFIG_KEYS.items():
        raw = await conn.fetchval("SELECT get_config($1)", key)
        if raw is None:
            continue
        value = _json.loads(raw) if isinstance(raw, str) else raw
        if value is not None:
            settings[field_name] = value
    return settings
