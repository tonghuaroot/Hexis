<!--
title: Memory Architecture
summary: Multi-layered memory system with vectors, graphs, and neighborhoods
read_when:
  - "You want to understand how memory works"
  - "You want to understand the retrieval system"
section: concepts
-->

# Memory Architecture

Hexis implements a multi-layered memory system modeled after cognitive science research.

## In Brief

Five memory types (working, episodic, semantic, procedural, strategic) with vector embeddings for similarity search, graph relationships for reasoning, and precomputed neighborhoods for fast retrieval.

## The Problem

Simple RAG systems store text chunks with embeddings and retrieve by similarity. This works for knowledge retrieval but fails to capture:

- **Temporal relationships** -- what happened before/after
- **Causal chains** -- what caused what
- **Contradictions** -- when new information conflicts with existing beliefs
- **Importance decay** -- not all memories are equally important over time
- **Associative recall** -- remembering one thing triggers related memories

## How Hexis Approaches It

### Memory Types

```mermaid
graph TD
    Input[New Information] --> WM[Working Memory]
    WM --> |Consolidation| LTM[Long-Term Memory]

    subgraph "Long-Term Memory"
        LTM --> EM[Episodic Memory]
        LTM --> SM[Semantic Memory]
        LTM --> PM[Procedural Memory]
        LTM --> STM[Strategic Memory]
    end

    Query[Query/Retrieval] --> |Vector Search| LTM
    Query --> |Graph Traversal| LTM

    EM ---|Relationships| SM
    SM ---|Relationships| PM
    PM ---|Relationships| STM

    LTM --> |Decay| Archive[Archive/Removal]
    WM --> |Cleanup| Archive
```

1. **Working Memory** -- temporary buffer (UNLOGGED table for fast writes). Information enters here first. Expires automatically; important items are promoted.

2. **Episodic Memory** -- events with temporal context, actions, results, and emotional valence. Forms the agent's autobiographical timeline.

3. **Semantic Memory** -- facts with confidence scores, source tracking, and contradiction management. The agent's knowledge base.

4. **Procedural Memory** -- step-by-step procedures with success rate tracking. How the agent knows how to do things.

5. **Strategic Memory** -- patterns with adaptation history. High-level strategies learned from experience.

### Memory Infrastructure

**Vector embeddings** (pgvector) provide similarity-based retrieval via HNSW indexes. The `get_embedding()` function handles generation and caching.

**Graph relationships** (Apache AGE) enable multi-hop traversal: `TEMPORAL_NEXT` for narrative sequence, `CAUSES` for causal reasoning, `CONTRADICTS` for dialectical tension, `SUPPORTS` for evidence chains.

**Automatic clustering** groups memories into thematic clusters with emotional signatures and centroid embeddings.

**Precomputed neighborhoods** store associative neighbor data for each memory, enabling spreading activation without real-time graph traversal.

**Full-text history search** uses PostgreSQL GIN indexes across raw RecMem turns
and consolidated memories. It provides a free lexical fallback for exact names
and phrases even before a turn has an embedding or while an embedding provider
is unavailable.

**Memory decay** reduces importance over time with importance-weighted persistence. Permanent memories (from important ingestion) are exempt.

### Retrieval Model

Three performance tiers:

| Path | Method | Speed | Use Case |
|------|--------|-------|----------|
| **Lexical** | `search_cross_session_history` | Fast | Exact prior-turn or memory details without embeddings |
| **Hot** | `fast_recall` + neighborhoods + temporal | Fast | Primary retrieval |
| **Warm** | Cluster/episode lookups | Medium | Thematic search |
| **Cold** | Graph traversal (Apache AGE) | Slow | Multi-hop reasoning |

`fast_recall()` combines:
1. **Vector similarity** -- cosine distance on embeddings
2. **Neighborhood expansion** -- precomputed associative neighbors
3. **Temporal context** -- memories in the same episode get a boost

### Worldview Integration

Beliefs (stored as worldview memories) filter and weight other memories. When new information contradicts existing beliefs, `CONTRADICTS` graph edges are created and the coherence drive is nudged upward to surface the tension.

## Key Design Decisions

- **Single `memories` table** -- all memory types share one table with JSONB metadata for type-specific fields. Simpler than a table-per-type approach.
- **Neighborhoods over real-time graph traversal** -- precomputed during maintenance for hot-path speed
- **Embeddings as DB implementation detail** -- application code never sees vectors
- **UNLOGGED working memory** -- fast writes since we can afford data loss (it's temporary)

## Implementation Pointers

- Tables: `db/*_tables_memory.sql`
- Functions: `db/*_functions_memory.sql`
- Neighborhoods: `db/*_functions_maintenance.sql`
- Python client: `core/cognitive_memory_api.py`

## Related

- [Memory Types](../reference/memory-types.md) -- field-level reference
- [Memory Operations](../guides/memory-operations.md) -- practical usage
- [Database Is the Brain](database-is-the-brain.md) -- why memory is in Postgres
