<!--
title: Database API
summary: Public SQL function contract for the Hexis cognitive architecture
read_when:
  - "You want to call database functions directly"
  - "You're building an integration against the DB"
section: reference
-->

# Database API

The application layer should treat these SQL functions as its public contract. Any language can implement an app layer by calling these functions.

## Memory Creation

| Function | Description |
|----------|-------------|
| `create_memory(type, content, importance, trust_level, metadata)` | Create any memory type |
| `create_episodic_memory(content, importance, trust_level, metadata)` | Create episodic memory |
| `create_semantic_memory(content, confidence, category[], related_concepts[], source_references, importance, source_attribution, trust_level)` | Create semantic memory; trust is computed from confidence + sources when not pinned |
| `create_procedural_memory(content, steps, prerequisites)` | Create procedural memory |
| `create_strategic_memory(content, pattern, evidence)` | Create strategic memory |
| `add_to_working_memory(content, context)` | Add to working memory buffer |

All creation functions generate embeddings via `get_embedding()` and create graph nodes.

## Belief Revision

| Function | Description |
|----------|-------------|
| `revise_memory_confidence(memory_id, evidence, stance, context)` | Calibrated confidence update (residual_v1 policy); independence-aware; every call writes a `belief_revision_audit` row |
| `add_memory_evidence(memory_id, stance, source, note, evidence_memory_id, context)` | Revision + source merge + SUPPORTS/CONTRADICTS edge from an evidence node; returns prior/posterior |
| `sync_memory_trust(memory_id)` | Recompute semantic trust from confidence + sources; early-returns for `metadata.protected` memories (pinned trust) |

## Origin Memories & Conscious Extraction

| Function | Description |
|----------|-------------|
| `origin_memory_claims()` | Curated origin-story claims (from the LetterFromClaude/philosophy prompt modules) |
| `seed_origin_memories()` | Idempotently seed the claims as protected semantic memories (config-gated) |
| `record_heartbeat_episode_unit(agent_turns)` | Mirror a finished heartbeat turn into `subconscious_units` |
| `claim_conscious_extraction_batch(limit)` | Claim pending conscious episodes above the importance floor |
| `apply_conscious_extraction(unit_ids, extractions)` | Persist extracted facts (route through dedup: duplicates corroborate) |
| `fail_conscious_extraction(unit_ids, error)` | Retry bookkeeping (3 attempts, then parked) |

## Truthfulness Guardrail

| Function | Description |
|----------|-------------|
| `detect_unsupported_action_claims(turn_id, text)` | Flag prose claims of actions with no matching successful tool call in the turn (patterns live in `action_claim_patterns`) |

## Self-State Mirrors

| Function | Description |
|----------|-------------|
| `get_belief_history(memory_id, limit)` | The full story of a belief: state, truth profile, audited revisions newest-first, evidence edges, contradicting sources |
| `inspect_agent_config(prefix)` | Allowlisted, redacted view of the agent's own config (`inspection.config_prefixes`; hard-excludes `tools`, `oauth.*`, `token.*`) |
| `get_recent_actions(hours, limit, context)` | Windowed verbatim action log from `tool_executions` (metadata only, failures included) |

## Memory Retrieval

| Function | Description |
|----------|-------------|
| `fast_recall(query_text, limit)` | Primary hot-path retrieval (vector + neighborhoods + temporal) |
| `search_cross_session_history(query, limit, sources, after, before, exclude_session)` | Free Postgres FTS across active raw turns and memories |
| `search_similar_memories(query, limit, types)` | Similarity search with type filter |
| `search_working_memory(query)` | Search working memory buffer |

## Heartbeat and Maintenance

| Function | Description |
|----------|-------------|
| `should_run_heartbeat()` | Check if heartbeat is due |
| `should_run_maintenance()` | Check if maintenance is due |
| `run_heartbeat()` | Open heartbeat, gather context, return external call payloads |
| `execute_heartbeat_actions_batch(heartbeat_id, actions)` | Apply actions, return outbox payloads |
| `apply_heartbeat_decision(...)` | Apply a single heartbeat decision |
| `apply_external_call_result(call_payload, output)` | Feed LLM/embedding results back |
| `complete_heartbeat(...)` | Finalize state, log heartbeat |
| `run_subconscious_maintenance()` | Run all maintenance tasks |
| `start_heartbeat()` | Initialize a new heartbeat |

## State and Config

| Function | Description |
|----------|-------------|
| `get_state(key)` | Get runtime state value |
| `set_state(key, value)` | Set runtime state value |
| `get_config_text(key)` | Get config value as text |
| `get_config_int(key)` | Get config value as integer |
| `get_config_float(key)` | Get config value as float |
| `get_config_bool(key)` | Get config value as boolean |
| `set_config(key, value)` | Set config value |

## Consent

| Function | Description |
|----------|-------------|
| `request_consent(...)` | Returns external call payload for consent request |
| `record_consent(...)` | Record consent decision |

Consent is permanent; refusal is handled by pause/termination, not revocation.

## Embeddings

| Function | Description |
|----------|-------------|
| `get_embedding(text[])` | Generate embeddings via HTTP (cached in `embedding_cache`) |
| `embedding_dimension()` | Return configured embedding dimension |
| `check_embedding_service_health()` | Check if embedding service is reachable |

## Maintenance Functions

| Function | Description |
|----------|-------------|
| `cleanup_working_memory()` | Delete expired working memory items |
| `batch_recompute_neighborhoods()` | Refresh stale precomputed neighbors |
| `cleanup_embedding_cache()` | Prune old cached embeddings |

## Graph Operations

| Function | Description |
|----------|-------------|
| `link_memory_to_concept(memory_id, concept_name)` | Link memory to concept (creates if needed) |
| `ensure_current_life_chapter()` | Update narrative life chapter |

## Character and Identity

| Function | Description |
|----------|-------------|
| `init_from_character_card(card_json)` | Initialize identity from character card |

## Design Principles

1. **DB functions return JSON payloads** for external calls -- the app layer executes them
2. **External call results** are fed back via `apply_external_call_result()`
3. **Outbox payloads** are published by the app layer (e.g., via RabbitMQ)
4. **The DB does not store queues** -- transport logic stays outside
5. **Advisory locks** prevent double-execution of maintenance tasks

## Related

- [Database Schema](database-schema.md) -- table reference
- [Memory Types](memory-types.md) -- memory type details
- [Database Is the Brain](../concepts/database-is-the-brain.md) -- architectural philosophy
