<!--
title: Ingestion
summary: Feed documents and knowledge into your agent's memory
read_when:
  - "You want to load documents into memory"
  - "You want to understand ingestion modes"
section: guides
-->

# Ingestion

Feed documents, files, URLs, and other content into your agent's memory.

## Quick Start

```bash
# Ingest a file
hexis ingest --file ./notes.md

# Ingest a directory (recursive)
hexis ingest --input ./documents

# Ingest a URL
hexis ingest --url https://example.com/article
```

## How It Works

Ingestion is tiered and emotionally aware:

1. Creates an **encounter memory** (the agent "encountered" this content)
2. **Appraises** the content (importance, relevance, emotional valence)
3. **Extracts** semantic knowledge based on the chosen mode

Content is deduplicated at two levels. Whole documents are receipt-tracked by
content hash — re-ingesting an identical document is skipped. Individual
extracted facts route through the memory dedup policy: a fact that near-matches
an existing memory **corroborates** it (the document is merged as a source, the
belief's confidence rises through the audited revision policy, and a `SUPPORTS`
edge links the reading encounter to the belief) instead of being re-stored.

## Standard Modes

| Mode | Behavior | Best for |
|------|----------|----------|
| `auto` | Chooses based on content size | Default |
| `deep` | Per-section appraisal + extraction | Short, high-value documents |
| `standard` | Single appraisal + chunked extraction | General use |
| `shallow` | Summary-only extraction | Broad corpora |
| `archive` | Store encounter only (process later) | Large files, batch processing |

```bash
hexis ingest --input ./docs --mode auto       # auto-select
hexis ingest --file paper.pdf --mode deep     # thorough extraction
hexis ingest --input ./corpus --mode shallow  # light pass
```

## Conscious Ingestion Modes (RLM)

These modes use the RLM (Recursive Language Model) loop for conscious reading against the agent's existing knowledge:

| Mode | Energy | Behavior |
|------|--------|----------|
| `fast` | 2 | Quick chunking + fact extraction + basic graph linking |
| `slow` | 5 | Mini-RLM loop per chunk: searches related memories, compares against worldview, forms emotional reactions, decides to accept/contest/question each piece |
| `hybrid` | 3 | Fast first pass, then slow-processes only high-signal chunks (importance > 0.7, worldview-contradicting, or goal-related) |

```bash
# Conscious slow reading of an important document
hexis ingest --file philosophy.md --mode slow

# Hybrid: fast scan, deep-read only what matters
hexis ingest --input ./research/ --mode hybrid
```

Contested content is stored with a `contested` flag and `CONTESTED_BECAUSE` graph edges linking to the beliefs that caused rejection.

## Input Sources

```bash
# Single file
hexis ingest --file ./notes.md

# Directory (recursive by default, --no-recursive to disable)
hexis ingest --input ./docs

# URL
hexis ingest --url https://example.com/article

# Stdin
cat ./notes.md | hexis ingest --stdin --stdin-type markdown --stdin-title "notes"
```

## Useful Flags

| Flag | Description |
|------|-------------|
| `--mode <mode>` | Choose ingestion mode |
| `--min-importance 0.6` | Floor importance for extracted memories |
| `--permanent` | No decay (memories persist indefinitely) |
| `--base-trust 0.7` | Override source trust level |
| `--no-recursive` | Don't recurse into subdirectories |
| `--quiet`, `-q` | Suppress verbose output |

## Processing Archived Content

If `auto` mode archives large content for later processing:

```bash
# Check what's pending
hexis ingest status --pending

# Process archived items
hexis ingest process --all-archived --limit 10

# Process a specific item
hexis ingest process --content-hash <hash>
```

## Tips

- Start with `--mode auto`. Only force `deep` for known small/important content.
- Use `--mode shallow` for broad corpora to avoid memory bloat.
- Use `--permanent` sparingly -- it disables decay for created memories.
- Use `--base-trust` for low-trust sources (e.g., random web pages).
- The agent can also trigger ingestion autonomously during heartbeats via `fast_ingest`, `slow_ingest`, and `hybrid_ingest` tools.

## Troubleshooting

**"I ingested a big folder and nothing happened"** -- `auto` mode may have chosen `archive` for large files. Run `hexis ingest status --pending` to check, then `hexis ingest process --all-archived`.

**Duplicate content skipped** -- Ingestion receipts are content-hash based. If you need to re-ingest, either change the content or process the archived items. Note that near-duplicate *facts* are not lost — they corroborate the matched memory (source merged, confidence revised, audited in `belief_revision_audit`).

## Related

- [Memory Operations](memory-operations.md) -- recall and search memories
- [Memory Types](../reference/memory-types.md) -- memory type reference
- [CLI reference](../reference/cli.md) -- full `hexis ingest` flags
