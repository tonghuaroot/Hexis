"""Hexis ingestion — split from the former services/ingest.py (#89).
Module: pipeline.
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

from .config import (
    Appraisal,
    Config,
    DocumentInfo,
    Extraction,
    IngestionMetrics,
    IngestionMode,
    Section,
    _emit,
    _extract_title,
    _hash_text,
    _infer_source_type,
    _normalize_mode,
    _should_cancel,
    _word_count,
)
from .llm import Appraiser, IngestLLM, KnowledgeExtractor
from .readers import (
    AudioReader,
    CodeReader,
    DataReader,
    DocxReader,
    EmailReader,
    EpubReader,
    ImageReader,
    LatexReader,
    NotebookReader,
    PptxReader,
    RssReader,
    RtfReader,
    VideoReader,
    WebReader,
    XlsxReader,
    get_reader,
)
from .sectioning import Sectioner
from .store import MemoryStore

# =========================================================================
# INGESTION PIPELINE
# =========================================================================


class IngestionPipeline:
    SUPPORTED_EXTENSIONS = (
        # Documents
        {".md", ".markdown", ".txt", ".text", ".pdf"}
        | DocxReader.EXTENSIONS
        | RtfReader.EXTENSIONS
        | LatexReader.EXTENSIONS
        | EmailReader.EXTENSIONS
        | EpubReader.EXTENSIONS
        | PptxReader.EXTENSIONS
        | XlsxReader.EXTENSIONS
        | NotebookReader.EXTENSIONS
        # Code
        | set(CodeReader.LANGUAGE_MAP.keys())
        # Data
        | DataReader.DATA_EXTENSIONS
        # Media
        | ImageReader.IMAGE_EXTENSIONS
        | AudioReader.AUDIO_EXTENSIONS
        | VideoReader.VIDEO_EXTENSIONS
    )

    def __init__(self, config: Config):
        self.config = config
        self.config.mode = _normalize_mode(self.config.mode)
        self.sectioner = Sectioner(config.max_section_chars, config.chunk_overlap)
        self.llm = IngestLLM(config)
        self.appraiser = Appraiser(self.llm)
        self.extractor = KnowledgeExtractor(self.llm)
        self.store = MemoryStore(config)
        self.stats = {"files_processed": 0, "memories_created": 0, "errors": 0}

    async def ingest_file(self, file_path: Path) -> int:
        metrics = IngestionMetrics(start_time=time.time())
        if _should_cancel(self.config):
            raise RuntimeError("Ingestion cancelled")
        if not file_path.exists():
            _emit(self.config, f"File not found: {file_path}")
            return 0
        if file_path.suffix.lower() not in self.SUPPORTED_EXTENSIONS:
            _emit(self.config, f"Unsupported file type: {file_path.suffix}")
            return 0
        if self.config.verbose:
            _emit(self.config, f"\nProcessing: {file_path}")

        reader = get_reader(file_path)
        try:
            content = reader.read(file_path)
            metrics.source_size_bytes = len(content.encode("utf-8"))
        except Exception as exc:
            _emit(self.config, f"  Error reading file: {exc}")
            self.stats["errors"] += 1
            metrics.errors.append(str(exc))
            return 0

        doc = DocumentInfo(
            title=_extract_title(content, file_path),
            source_type=_infer_source_type(file_path),
            content_hash=_hash_text(content),
            word_count=_word_count(content),
            path=str(file_path),
            file_type=file_path.suffix.lower(),
        )
        return await self._ingest_content(content, doc, metrics, section_path=file_path)

    GIT_IGNORE_DIRS = {
        ".git", "node_modules", "__pycache__", ".venv", "venv",
        ".env", "dist", "build", ".tox", ".mypy_cache",
        ".pytest_cache", "vendor", ".bundle", ".next", "coverage",
    }

    async def ingest_directory(
        self,
        dir_path: Path,
        recursive: bool = True,
        exclude_dirs: set[str] | None = None,
    ) -> int:
        if _should_cancel(self.config):
            raise RuntimeError("Ingestion cancelled")
        if not dir_path.exists() or not dir_path.is_dir():
            _emit(self.config, f"Directory not found: {dir_path}")
            return 0
        pattern = "**/*" if recursive else "*"
        files = [
            f for f in dir_path.glob(pattern)
            if f.is_file()
            and f.suffix.lower() in self.SUPPORTED_EXTENSIONS
            and (
                exclude_dirs is None
                or not any(part in exclude_dirs for part in f.relative_to(dir_path).parts)
            )
        ]
        if self.config.verbose:
            _emit(self.config, f"Found {len(files)} files to process")

        # Bounded file concurrency (#90); per-file work is already parallel
        # inside, so this multiplies modestly, not explosively.
        file_slots = asyncio.Semaphore(max(1, self.config.max_parallel_files))

        async def _one(file_path: Path) -> int:
            async with file_slots:
                return await self.ingest_file(file_path)

        counts = await asyncio.gather(*[_one(f) for f in files])
        return sum(counts)

    async def ingest_url(self, url: str, title: str | None = None) -> int:
        """Ingest content from a URL."""
        metrics = IngestionMetrics(start_time=time.time())

        if self.config.verbose:
            _emit(self.config, f"\nFetching: {url}")

        try:
            content = WebReader.read(url)
            metrics.source_size_bytes = len(content.encode("utf-8"))
        except Exception:
            # Fallback: try as RSS/Atom feed
            try:
                content = RssReader.read(url)
                if content:
                    metrics.source_size_bytes = len(content.encode("utf-8"))
                    if self.config.verbose:
                        _emit(self.config, "  Fetched as RSS/Atom feed")
                else:
                    raise RuntimeError("No RSS entries found")
            except Exception as exc2:
                _emit(self.config, f"  Error fetching URL: {exc2}")
                self.stats["errors"] += 1
                metrics.errors.append(str(exc2))
                return 0

        if not title:
            title_match = re.search(r"\[Title: (.+?)\]", content)
            if title_match:
                title = title_match.group(1)
            else:
                title = url.split("/")[-1] or url

        doc = DocumentInfo(
            title=title,
            source_type="web",
            content_hash=_hash_text(content),
            word_count=_word_count(content),
            path=url,
            file_type=".html",
        )
        return await self._ingest_content(
            content, doc, metrics, section_path=Path("web_content.md")
        )

    async def ingest_text(
        self,
        content: str,
        *,
        title: str | None = None,
        source_type: str = "pasted_text",
        path: str | None = None,
        file_type: str = ".md",
    ) -> int:
        """Ingest raw text (pasted documents, job payloads) — no file needed."""
        metrics = IngestionMetrics(start_time=time.time())
        metrics.source_size_bytes = len(content.encode("utf-8"))
        if not title:
            first_line = next((ln.strip() for ln in content.splitlines() if ln.strip()), "")
            title = first_line[:80] or "Pasted text"

        doc = DocumentInfo(
            title=title,
            source_type=source_type,
            content_hash=_hash_text(content),
            word_count=_word_count(content),
            path=path or f"text:{_hash_text(content)[:12]}",
            file_type=file_type,
        )
        return await self._ingest_content(
            content, doc, metrics, section_path=Path("pasted_text.md")
        )

    async def _ingest_content(
        self,
        content: str,
        doc: DocumentInfo,
        metrics: IngestionMetrics,
        *,
        section_path: Path,
    ) -> int:
        """The single ingestion core (#89): every entry point — file, URL,
        raw text, stdin — reduces to a reader over this."""
        llm_calls_start = self.llm.call_count
        mode = self.config.mode
        metrics.word_count = doc.word_count
        metrics.mode = mode.value
        metrics.source_type = doc.source_type

        stored_doc = await self.store.store_source_document(
            title=doc.title,
            source_type=doc.source_type,
            content_hash=doc.content_hash,
            path=doc.path,
            file_type=doc.file_type,
            content=content,
            word_count=doc.word_count,
            source_attribution=self._source_payload(doc),
            metadata={"mode": mode.value},
        )
        doc.document_id = str(stored_doc.get("document_id") or "") or None

        sections = self.sectioner.split(content, section_path)
        section_hashes = [_hash_text(s.content) for s in sections]

        # Receipt gate (#85): completion is asserted by receipts, never by the
        # encounter's existence. Doc-complete row -> skip; receipted sections
        # drop out (resume); the enc: sentinel hands back the encounter.
        doc_ref = doc.content_hash
        receipts = await self.store.get_receipts(
            doc_ref, [doc_ref, f"enc:{doc_ref}"] + section_hashes
        )
        if doc_ref in receipts:
            if self.config.verbose:
                _emit(self.config, f"  Already ingested (hash={doc_ref[:8]}...). Skipping.")
            return 0
        done_sections = {h for h in section_hashes if h in receipts}
        if done_sections and self.config.verbose:
            _emit(self.config, f"  Resuming: {len(done_sections)}/{len(sections)} sections already ingested.")
        if self.config.verbose:
            _emit(self.config, f"  Mode: {mode.value} | Words: {doc.word_count} | Sections: {len(sections)}")

        # Slow/hybrid mode: delegate to RLM-based ingestion
        if mode in (IngestionMode.SLOW, IngestionMode.HYBRID):
            return await self._run_rlm_ingest(mode, doc, sections, metrics, llm_calls_start)

        # FAST mode: small docs (<=deep_max_words) get per-section appraisal;
        # larger docs get a single doc-level appraisal. All sections processed.
        base_context = await self._build_appraisal_context(doc)
        use_deep = doc.word_count <= self.config.deep_max_words

        overall_appraisal = None
        if not use_deep:
            sample = self._sample_content(content)
            overall_appraisal = await self.appraiser.appraise(content=sample, context=base_context, mode=mode)
            await self.store.set_affective_state(overall_appraisal)
            metrics.appraisal_valence = overall_appraisal.valence
            metrics.appraisal_arousal = overall_appraisal.arousal
            metrics.appraisal_emotion = overall_appraisal.primary_emotion
            metrics.appraisal_intensity = overall_appraisal.intensity

        encounter_id = receipts.get(f"enc:{doc_ref}")
        if encounter_id is None:
            encounter_id = await self._create_encounter_memory(doc, overall_appraisal, mode)
            # The enc: sentinel is a receipt for the encounter only — never a
            # doc-complete claim (#85: a crash from here on RESUMES).
            await self.store.record_receipt(
                doc_ref, f"enc:{doc_ref}",
                memory_id=encounter_id, source_path=doc.path,
            )

        created_ids: list[str] = []
        total_extractions = 0
        dedup_count = 0

        section_hash_by_id = dict(zip((id(s) for s in sections), section_hashes))
        active_sections = [
            s for s in sections
            if not self._skip_section(s.title)
            and section_hash_by_id[id(s)] not in done_sections
        ]
        if _should_cancel(self.config):
            raise RuntimeError("Ingestion cancelled")
        max_items = self.config.max_facts_per_section

        # Bounded LLM fan-out (#90): the rate-limit stampede bound — a large
        # document no longer launches every section's calls at once.
        llm_slots = asyncio.Semaphore(max(1, self.config.max_parallel_llm))

        if use_deep:
            # Small docs: each section gets its own appraisal + extraction,
            # gathered on the caller's loop (#88 — the asyncio.run island died).
            async def _appraise_and_extract(s: Section) -> tuple[Appraisal, list[Extraction]]:
                async with llm_slots:
                    sample = self._sample_content(s.content)
                    apr = await self.appraiser.appraise(content=sample, context=base_context, mode=mode)
                    exts = await self.extractor.extract(
                        section=s, doc=doc, appraisal=apr, mode=mode, max_items=max_items,
                    )
                    return apr, exts

            deep_results = await asyncio.gather(
                *[_appraise_and_extract(s) for s in active_sections]
            )
            for section, (appraisal, extractions) in zip(active_sections, deep_results):
                await self.store.set_affective_state(appraisal)
                metrics.appraisal_valence = appraisal.valence
                metrics.appraisal_arousal = appraisal.arousal
                metrics.appraisal_emotion = appraisal.primary_emotion
                metrics.appraisal_intensity = appraisal.intensity
                s_hash = section_hash_by_id[id(section)]
                if not extractions:
                    await self.store.record_receipt(doc_ref, s_hash, source_path=doc.path)
                    continue
                total_extractions += len(extractions)
                new_memories = await self._create_semantic_memories(
                    doc, encounter_id, appraisal, extractions, section_hash=s_hash)
                dedup_count += len(extractions) - len(new_memories)
                created_ids.extend(new_memories)
        else:
            # Larger docs: shared appraisal, parallel extraction only
            appraisal = overall_appraisal if overall_appraisal is not None else Appraisal()

            async def _bounded_extract(s: Section) -> list[Extraction]:
                async with llm_slots:
                    return await self.extractor.extract(
                        section=s, doc=doc, appraisal=appraisal, mode=mode, max_items=max_items,
                    )

            section_extractions = await asyncio.gather(
                *[_bounded_extract(s) for s in active_sections]
            )
            for section, extractions in zip(active_sections, section_extractions):
                s_hash = section_hash_by_id[id(section)]
                if not extractions:
                    await self.store.record_receipt(doc_ref, s_hash, source_path=doc.path)
                    continue
                total_extractions += len(extractions)
                new_memories = await self._create_semantic_memories(
                    doc, encounter_id, appraisal, extractions, section_hash=s_hash)
                dedup_count += len(extractions) - len(new_memories)
                created_ids.extend(new_memories)

        if self.config.verbose:
            _emit(self.config, f"  Created {len(created_ids)} semantic memories")

        self.stats["files_processed"] += 1
        self.stats["memories_created"] += len(created_ids) + (1 if encounter_id else 0)

        metrics.extraction_count = total_extractions
        metrics.dedup_count = dedup_count
        metrics.memory_count = len(created_ids) + (1 if encounter_id else 0)
        metrics.llm_calls = self.llm.call_count - llm_calls_start
        metrics.duration_seconds = time.time() - metrics.start_time
        await self.store.store_metrics(metrics)

        # Doc-complete: the final receipt — everything before it is resumable.
        await self.store.record_receipt(
            doc_ref, doc_ref,
            memories_created=len(created_ids), source_path=doc.path,
        )

        return len(created_ids)

    def _sample_content(self, content: str, limit: int = 2000) -> str:
        if len(content) <= limit:
            return content
        head = content[:limit]
        tail = content[-limit:]
        return f"{head}\n\n...\n\n{tail}"

    async def _build_appraisal_context(self, doc: DocumentInfo) -> dict[str, Any]:
        ctx = {
            "document": {
                "title": doc.title,
                "source_type": doc.source_type,
                "word_count": doc.word_count,
            }
        }
        try:
            ctx.update(await self.store.fetch_appraisal_context())
        except Exception:
            pass
        return ctx

    def _source_payload(self, doc: DocumentInfo, *, section_hash: str | None = None) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        payload = {
            "kind": doc.source_type,
            "ref": doc.content_hash,
            "label": doc.title,
            "observed_at": now,
            "content_hash": doc.content_hash,
            "path": doc.path,
        }
        if section_hash is not None:
            # The persist functions record the section receipt atomically
            # with persistence when this key rides the source (#85/#90).
            payload["section_hash"] = section_hash
        if doc.document_id:
            payload["source_document_id"] = doc.document_id
            payload["document_id"] = doc.document_id
        if self.config.base_trust is not None:
            payload["trust"] = float(self.config.base_trust)
        if self.config.sensitivity:
            payload["sensitivity"] = str(self.config.sensitivity)
        return payload

    async def _create_archive_encounter(self, doc: DocumentInfo) -> str | None:
        source = self._source_payload(doc)
        text = f"I have access to '{doc.title}' but haven't engaged with it yet."
        context = {
            "activity": "archived",
            "source_type": doc.source_type,
            "source_ref": doc.content_hash,
            "word_count": doc.word_count,
            "mode": "archived",
            "awaiting_processing": True,
        }
        importance = max(self.config.min_importance_floor or 0.0, 0.2)
        encounter_id = await self.store.create_encounter_memory(
            text=text,
            source=source,
            emotional_valence=0.0,
            context=context,
            importance=importance,
        )
        await self._apply_decay(encounter_id, intensity=0.0)
        return encounter_id

    async def _create_encounter_memory(self, doc: DocumentInfo, appraisal: Appraisal | None, mode: IngestionMode) -> str | None:
        source = self._source_payload(doc)
        appraisal = appraisal or Appraisal()
        summary = appraisal.summary or ""
        if not summary:
            summary = f"It felt {appraisal.primary_emotion} with intensity {appraisal.intensity:.2f}."
        text = f"I read '{doc.title}'. {summary}"
        context = {
            "activity": "reading",
            "source_type": doc.source_type,
            "source_ref": doc.content_hash,
            "word_count": doc.word_count,
            "mode": mode.value,
            "appraisal": appraisal.__dict__,
        }
        importance = max(self.config.min_importance_floor or 0.0, 0.4 + appraisal.intensity * 0.4)
        encounter_id = await self.store.create_encounter_memory(
            text=text,
            source=source,
            emotional_valence=appraisal.valence,
            context=context,
            importance=importance,
        )
        await self._apply_decay(encounter_id, intensity=appraisal.intensity)
        return encounter_id

    async def _apply_decay(self, memory_id: str, intensity: float) -> None:
        # Decay bands are DB-owned (db/66 decay_rate_for_intensity).
        await self.store.apply_ingest_decay(memory_id, intensity, self.config.permanent)

    async def _create_semantic_memories(
        self,
        doc: DocumentInfo,
        encounter_id: str | None,
        appraisal: Appraisal,
        extractions: list[Extraction],
        *,
        section_hash: str | None = None,
    ) -> list[str]:
        """Thin wrapper: the whole post-LLM persistence pass runs atomically
        in the DB (db/66 ingest_persist_extractions) — routing, corroboration
        via the audited belief-revision policy, creation, concept links,
        worldview-hint edges, provenance edges, decay, and the section
        receipt (#85)."""
        source = self._source_payload(doc, section_hash=section_hash)
        result = await self.store.persist_extractions(
            extractions,
            source,
            encounter_id=encounter_id,
            intensity=appraisal.intensity,
            min_confidence=self.config.min_confidence_threshold,
            min_importance_floor=self.config.min_importance_floor,
            base_trust=self.config.base_trust,
            permanent=self.config.permanent,
        )
        return [str(mid) for mid in (result.get("created") or [])]

    def _skip_section(self, title: str) -> bool:
        lowered = title.strip().lower()
        return any(skip in lowered for skip in self.config.skip_sections)

    async def _run_rlm_ingest(
        self,
        mode: IngestionMode,
        doc: DocumentInfo,
        sections: list[Section],
        metrics: IngestionMetrics,
        llm_calls_start: int,
    ) -> int:
        """Run slow or hybrid ingestion via the async RLM loop, on the
        caller's loop (#88 — the thread-plus-third-loop bridge died)."""
        from services.slow_ingest_rlm import run_hybrid_ingest, run_slow_ingest

        if self.config.dsn:
            dsn = self.config.dsn
        else:
            dsn = (
                f"postgresql://{self.config.db_user}:{self.config.db_password}"
                f"@{self.config.db_host}:{self.config.db_port}/{self.config.db_name}"
            )

        runner = run_slow_ingest if mode == IngestionMode.SLOW else run_hybrid_ingest
        result = await runner(
            pipeline=self,
            doc=doc,
            sections=sections,
            llm_config=self.llm._cfg,
            dsn=dsn,
        )

        count = result.get("memories_created", 0)
        self.stats["files_processed"] += 1
        self.stats["memories_created"] += count

        metrics.memory_count = count
        metrics.mode = mode.value
        metrics.llm_calls = self.llm.call_count - llm_calls_start
        metrics.duration_seconds = time.time() - metrics.start_time
        await self.store.store_metrics(metrics)

        if self.config.verbose:
            _emit(self.config, f"  RLM {mode.value} ingest: {count} memories created")
            if mode == IngestionMode.HYBRID:
                _emit(
                    self.config,
                    f"  Slow chunks: {result.get('slow_chunks', 0)} | "
                    f"Fast chunks: {result.get('fast_chunks', 0)}",
                )

        return count

    async def check_and_process_archived(self, query: str, threshold: float = 0.75) -> list[str]:
        """
        Check if any archived content matches the query and process it.

        This implements retrieval-triggered processing: when a query surfaces
        archived content that hasn't been fully processed, we upgrade it now.

        Returns list of content hashes that were processed.
        """
        if self.store.client is None:
            await self.store.connect()

        # Find archived content matching the query
        rows = await self.store._fetchval(
            """
            SELECT jsonb_agg(jsonb_build_object(
                'memory_id', memory_id,
                'content_hash', content_hash,
                'title', title,
                'similarity', similarity,
                'source_path', source_path
            ))
            FROM check_archived_for_query($1, $2, 5)
            """,
            query,
            threshold,
        )

        if not rows:
            return []

        archived = json.loads(rows) if isinstance(rows, str) else rows
        if not archived:
            return []

        processed_hashes: list[str] = []

        for item in archived:
            if not item:
                continue

            content_hash = item.get("content_hash")
            source_path = item.get("source_path")
            title = item.get("title")
            memory_id = item.get("memory_id")

            if not content_hash:
                continue

            _emit(self.config, f"Processing archived content triggered by query: {title}")

            # Attempt to re-read the source file if it exists
            if source_path and source_path != "stdin" and not source_path.startswith("http"):
                path = Path(source_path)
                if path.exists():
                    # Re-ingest the file with the current mode (not archive)
                    original_mode = self.config.mode
                    self.config.mode = IngestionMode.FAST
                    try:
                        # Mark as processed first to avoid duplicate detection
                        await self.store._fetchval(
                            "SELECT mark_archived_as_processed($1::uuid)",
                            memory_id,
                        )
                        await self.ingest_file(path)
                        processed_hashes.append(content_hash)
                    finally:
                        self.config.mode = original_mode
                    continue

            # If source file not available, just mark as processed
            await self.store._fetchval(
                "SELECT mark_archived_as_processed($1::uuid)",
                memory_id,
            )
            processed_hashes.append(content_hash)

        return processed_hashes

    def print_stats(self) -> None:
        _emit(self.config, "\n" + "=" * 50)
        _emit(self.config, "INGESTION COMPLETE")
        _emit(self.config, "=" * 50)
        _emit(self.config, f"Files processed:   {self.stats['files_processed']}")
        _emit(self.config, f"Memories created:  {self.stats['memories_created']}")
        _emit(self.config, f"Errors:            {self.stats['errors']}")
        _emit(self.config, "=" * 50)

    async def close(self) -> None:
        await self.store.close()


# =========================================================================
# ARCHIVED CONTENT PROCESSOR
# =========================================================================


class ArchivedContentProcessor:
    """
    Processor for upgrading archived content to full memories.

    This can be used:
    1. During recall - when a query surfaces relevant archived content
    2. By maintenance workers - batch processing of pending archives
    3. By CLI - manual processing of specific content
    """

    def __init__(self, config: Config):
        self.config = config
        self.pipeline = IngestionPipeline(config)

    async def process_for_query(self, query: str, threshold: float = 0.75) -> list[str]:
        """
        Check if archived content matches a query and process it.

        Returns list of content hashes that were processed.
        """
        return await self.pipeline.check_and_process_archived(query, threshold)

    async def process_by_hash(self, content_hash: str) -> bool:
        """Process a specific archived item by content hash."""
        archived = await self.pipeline.store.check_archived_for_query(
            content_hash, threshold=0.0, limit=1
        )

        if not archived:
            # Try direct lookup
            row = await self.pipeline.store._fetchval(
                """
                SELECT jsonb_build_object(
                    'memory_id', id,
                    'content_hash', source_attribution->>'content_hash',
                    'title', source_attribution->>'label',
                    'source_path', source_attribution->>'path'
                )
                FROM memories
                WHERE type = 'episodic'
                  AND source_attribution->>'content_hash' = $1
                  AND metadata->>'awaiting_processing' = 'true'
                LIMIT 1
                """,
                content_hash,
            )
            if not row:
                return False
            archived = [json.loads(row) if isinstance(row, str) else row]

        for item in archived:
            if not item:
                continue

            source_path = item.get("source_path")
            memory_id = item.get("memory_id")

            if source_path and source_path != "stdin" and not source_path.startswith("http"):
                path = Path(source_path)
                if path.exists():
                    await self.pipeline.store.mark_archived_processed(memory_id)
                    original_mode = self.config.mode
                    self.config.mode = IngestionMode.FAST
                    try:
                        await self.pipeline.ingest_file(path)
                    finally:
                        self.config.mode = original_mode
                    return True

            # Mark as processed even if file not found
            return await self.pipeline.store.mark_archived_processed(memory_id)

        return False

    async def process_batch(self, limit: int = 10) -> int:
        """Process a batch of archived items."""
        rows = await self.pipeline.store._fetchval(
            """
            SELECT ARRAY_AGG(source_attribution->>'content_hash')
            FROM memories
            WHERE type = 'episodic'
              AND metadata->>'awaiting_processing' = 'true'
            ORDER BY importance DESC, created_at ASC
            LIMIT $1
            """,
            limit,
        )

        if not rows:
            return 0

        hashes = list(rows) if rows else []
        count = 0
        for h in hashes:
            if h and await self.process_by_hash(h):
                count += 1

        return count

    async def close(self) -> None:
        await self.pipeline.close()
