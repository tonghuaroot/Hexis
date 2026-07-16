<!--
title: Memory Operations
summary: Practical usage of remember, recall, and hydrate
read_when:
  - "You want to search the agent's memories"
  - "You want to understand how memory works in practice"
section: guides
-->

# Memory Operations

Practical guide to storing, retrieving, and using the agent's memories.

## Quick Start

```bash
# Search memories from the CLI
hexis recall "user preferences"

# Search with type filter
hexis recall "how to deploy" --type procedural

# JSON output
hexis recall "project goals" --type goal --json
```

## Core Operations

### Remember (Store)

In chat, the agent automatically forms memories from conversations. You can also store memories programmatically:

```python
from core.cognitive_memory_api import CognitiveMemory

async with CognitiveMemory.connect(DSN) as mem:
    await mem.remember("User prefers dark mode and concise responses")
```

Or directly via SQL:

```sql
SELECT create_semantic_memory('User prefers dark mode', 0.9);
```

The agent-facing `remember` tool also accepts optional provenance — a
`sources` array (`{kind, ref, label, author, trust}`) and a `confidence`
score. Semantic memories record every source and derive their trust from
them, which is what makes a belief revisable later.

### Add Evidence (Revise a Belief)

When new information bears on a belief that already exists, don't create a
duplicate — attach evidence. The `add_evidence` tool takes a `memory_id`
(from recall), a stance (`supports` or `contradicts`), and a source, then
revises the belief's confidence through the audited revision policy and
returns the prior and posterior values ("confidence 0.50 → 0.66"). Duplicate
sources are merged without moving confidence, and every change is recorded in
`belief_revision_audit`:

```sql
SELECT * FROM belief_revision_audit WHERE memory_id = '<id>' ORDER BY created_at;
```

### Recall (Retrieve)

Recall uses vector similarity search augmented with precomputed neighborhoods and temporal context:

```bash
hexis recall "UI preferences" --limit 5
```

```python
async with CognitiveMemory.connect(DSN) as mem:
    memories = await mem.recall("UI preferences", limit=5)
```

```sql
SELECT * FROM fast_recall('UI preferences', 5);
```

Recall results include each memory's `trust` and `confidence`, so the agent
can weigh what it believes. The agent-facing tool also accepts `min_score` (a
relevance floor — drop weak matches instead of padding to the count), and its
default/maximum counts are config-driven budgets (`memory.recall_default_limit`,
`memory.recall_max_limit`).

### Search History (Exact Cross-Session Retrieval)

Use full-text history search for exact names, phrases, operators, or details from
prior conversations. It searches active raw turns and consolidated memories in
Postgres without calling an embedding provider:

```python
async with CognitiveMemory.connect(DSN) as mem:
    results = await mem.search_history(
        '"project lantern" deployment',
        sources=["turn", "memory"],
        limit=20,
    )
```

```sql
SELECT *
FROM search_cross_session_history('"project lantern" deployment', 20);
```

The agent-facing `search_history` tool excludes raw turns from its current UUID
session by default because the live conversation is already in context. Set
`exclude_current_session` to false when the current stored turn is relevant.
Inactive memories, expired memories, and redacted or archived raw turns are
never returned.

### Hydrate (Context Building)

Hydrate gathers a rich context package for LLM prompts -- memories, goals, identity, worldview:

```python
async with CognitiveMemory.connect(DSN) as mem:
    ctx = await mem.hydrate("How should I respond?", include_goals=True)
```

## Memory Types

| Type | Purpose | Example |
|------|---------|---------|
| **Working** | Temporary buffer with expiry | Recent conversation context |
| **Episodic** | Events with temporal context | "User asked about deployment at 3pm" |
| **Semantic** | Facts with confidence scores | "Python 3.10+ is required" |
| **Procedural** | Step-by-step procedures | "How to deploy: 1. Build... 2. Push..." |
| **Strategic** | Patterns and strategies | "User responds well to concise answers" |
| **Worldview** | Beliefs and identity | "I value honesty over comfort" |
| **Goal** | Active objectives | "Help user learn Python" |

See [Memory Types reference](../reference/memory-types.md) for full field details.

## CLI Recall Options

```bash
hexis recall <query>                    # basic search
hexis recall <query> --limit 20         # more results
hexis recall <query> --type semantic    # filter by type
hexis recall <query> --json             # JSON output
```

## How Retrieval Works

The `fast_recall()` function combines three retrieval strategies:

1. **Vector similarity** -- cosine distance on embeddings (pgvector HNSW index)
2. **Neighborhood expansion** -- precomputed associative neighbors for spreading activation
3. **Temporal context** -- memories in the same episode get a temporal boost

Results are scored, deduplicated, and ranked.

`search_cross_session_history()` is the complementary lexical path. PostgreSQL
web-search syntax supports quoted phrases, `OR`, and minus-prefixed exclusions;
the partial GIN index on active raw turns keeps this path independent of vector
generation and RecMem embedding lag.

## Working with Memories in SQL

```sql
-- Search active memories
SELECT * FROM fast_recall('what the user likes', 10);

-- Free lexical search across prior turns and consolidated memories
SELECT * FROM search_cross_session_history('"release checklist"', 20);

-- Count memories by type
SELECT type, count(*) FROM memories WHERE status = 'active' GROUP BY type;

-- View memory health
SELECT * FROM memory_health;

-- Search working memory
SELECT * FROM search_working_memory('current task');
```

## Related

- [Ingestion](ingestion.md) -- feeding content into memory
- [Memory Types](../reference/memory-types.md) -- detailed type reference
- [Memory Architecture](../concepts/memory-architecture.md) -- how memory works architecturally
- [Database API](../reference/database-api.md) -- SQL function reference
