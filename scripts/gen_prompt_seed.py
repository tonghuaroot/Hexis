#!/usr/bin/env python3
"""Generate db/40_seed_prompt_modules.sql from services/prompts/*.md.

The DB owns prompt text (prompt_modules), so render_prompt()/build_llm_request()
can assemble LLM requests without Python loading markdown from disk. This baker
keeps the .md files as the authoring source and regenerates the SQL seed that is
applied at DB init. Run after editing any prompt file:

    python scripts/gen_prompt_seed.py
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "services" / "prompts"
OUT = ROOT / "db" / "40_seed_prompt_modules.sql"
TAG = "$pm$"

HEADER = """-- Seed prompt_modules from services/prompts/*.md (generated).
-- Regenerate with scripts/gen_prompt_seed.py after editing prompt files.
-- Makes render_prompt()/build_llm_request() live: the DB owns the prompt text
-- that Python previously loaded from disk via services/prompt_resources.py.
SET search_path = public, ag_catalog, "$user";
"""


def main() -> None:
    blocks = [HEADER]
    files = sorted(PROMPTS.glob("*.md"))
    for path in files:
        content = path.read_text(encoding="utf-8")
        if TAG in content:
            raise SystemExit(f"dollar-quote tag {TAG} collides with content of {path}")
        blocks.append(
            f"SELECT upsert_prompt_module(\n"
            f"    '{path.stem}',\n"
            f"    {TAG}{content}{TAG},\n"
            f"    'Seeded from services/prompts/{path.name}',\n"
            f"    'services/prompts/{path.name}'\n"
            f");"
        )
    OUT.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    print(f"Wrote {OUT.relative_to(ROOT)}: {len(files)} modules, {OUT.stat().st_size} bytes")


if __name__ == "__main__":
    main()
