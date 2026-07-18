"""Hexis ingestion — split from the former services/ingest.py (#89).
Module: sectioning.
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

from .config import Section

# =========================================================================
# SECTIONING
# =========================================================================


class Sectioner:
    def __init__(self, max_chars: int = 2000, overlap: int = 200):
        self.max_chars = max_chars
        self.overlap = overlap

    def split(self, content: str, file_path: Path) -> list[Section]:
        suffix = file_path.suffix.lower()
        if suffix in [".md", ".markdown"]:
            return self._split_markdown(content)
        if suffix == ".pptx":
            return self._split_on_delimiter(content, r"\[Slide \d+\]")
        if suffix in {".xlsx", ".xls"}:
            return self._split_on_delimiter(content, r"\[Sheet: [^\]]+\]")
        if suffix == ".ipynb":
            return self._split_notebook(content)
        if suffix in {".eml", ".mbox"}:
            return self._split_on_delimiter(content, r"--- Message \d+ ---")
        return self._split_text(content)

    def _split_markdown(self, content: str) -> list[Section]:
        header_pattern = r"^(#{1,6}\s+.+)$"
        parts = re.split(header_pattern, content, flags=re.MULTILINE)
        sections: list[Section] = []
        current_title = "Introduction"
        current_content = ""
        for part in parts:
            if re.match(header_pattern, part or ""):
                if current_content.strip():
                    sections.append(Section(title=current_title, content=current_content.strip(), index=len(sections)))
                current_title = part.strip().lstrip("# ").strip() or current_title
                current_content = ""
            else:
                current_content += part
        if current_content.strip():
            sections.append(Section(title=current_title, content=current_content.strip(), index=len(sections)))
        if not sections:
            return [Section(title="Document", content=content, index=0)]
        return sections

    def _split_text(self, content: str) -> list[Section]:
        if len(content) <= self.max_chars:
            return [Section(title="Section 1", content=content, index=0)]
        chunks: list[str] = []
        paragraphs = content.split("\n\n")
        current = ""
        for para in paragraphs:
            if len(current) + len(para) + 2 <= self.max_chars:
                current += para + "\n\n"
                continue
            if current:
                chunks.append(current.strip())
            if len(para) > self.max_chars:
                sentences = re.split(r"(?<=[.!?])\s+", para)
                current = ""
                for sentence in sentences:
                    if len(current) + len(sentence) <= self.max_chars:
                        current += sentence + " "
                    else:
                        if current:
                            chunks.append(current.strip())
                        current = sentence + " "
            else:
                current = para + "\n\n"
        if current.strip():
            chunks.append(current.strip())
        if self.overlap > 0 and len(chunks) > 1:
            overlapped: list[str] = []
            for i, chunk in enumerate(chunks):
                if i > 0:
                    prev_overlap = chunks[i - 1][-self.overlap :]
                    chunk = f"...{prev_overlap}\n\n{chunk}"
                overlapped.append(chunk)
            chunks = overlapped
        return [Section(title=f"Section {i + 1}", content=chunk, index=i) for i, chunk in enumerate(chunks)]

    def _split_on_delimiter(self, content: str, pattern: str) -> list[Section]:
        """Split content on a regex delimiter pattern, keeping the delimiter with its section."""
        parts = re.split(f"({pattern})", content)
        sections: list[Section] = []
        current_title = "Header"
        current_content = ""
        for part in parts:
            if re.match(pattern, part):
                if current_content.strip():
                    sections.append(Section(title=current_title, content=current_content.strip(), index=len(sections)))
                current_title = part.strip().strip("[]").strip("-").strip()
                current_content = ""
            else:
                current_content += part
        if current_content.strip():
            sections.append(Section(title=current_title, content=current_content.strip(), index=len(sections)))
        if not sections:
            return [Section(title="Document", content=content, index=0)]
        return sections

    def _split_notebook(self, content: str) -> list[Section]:
        """Split notebook content on cell boundaries (triple-backtick blocks and text)."""
        parts = re.split(r"(```\w*\n.*?```)", content, flags=re.DOTALL)
        sections: list[Section] = []
        for part in parts:
            text = part.strip()
            if not text:
                continue
            if text.startswith("```"):
                title = f"Code Cell {len(sections) + 1}"
            else:
                title = f"Cell {len(sections) + 1}"
            sections.append(Section(title=title, content=text, index=len(sections)))
        if not sections:
            return [Section(title="Notebook", content=content, index=0)]
        return sections
