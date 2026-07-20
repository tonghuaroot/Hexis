<!--
title: Config Keys
summary: All config table keys with types and defaults
read_when:
  - "You need to check or set a config value"
  - "You want to see all configuration options"
section: reference
-->

# Config Keys

All keys stored in the Postgres `config` table. Values are JSONB.

## Querying Config

```sql
-- Get a specific key
SELECT value FROM config WHERE key = 'llm.chat';

-- Using helper functions
SELECT get_config_text('llm.chat.provider');
SELECT get_config_int('heartbeat.interval_seconds');
SELECT get_config_bool('agent.is_configured');

-- Set a value
SELECT set_config('agent.name', '"MyAgent"'::jsonb);
```

## Agent Configuration

| Key | Type | Description |
|-----|------|-------------|
| `agent.is_configured` | bool | Whether init has completed |
| `agent.name` | text | Agent's name |
| `agent.user_name` | text | What to call the user |
| `agent.active_hours_start` | text | Active hours start (e.g., "09:00") |
| `agent.active_hours_end` | text | Active hours end (e.g., "22:00") |
| `agent.timezone` | text | Agent timezone |

## LLM Configuration

| Key | Type | Description |
|-----|------|-------------|
| `llm.chat.provider` | text | Conscious LLM provider |
| `llm.chat.model` | text | Conscious LLM model |
| `llm.chat.endpoint` | text | API endpoint URL |
| `llm.heartbeat.provider` | text | Heartbeat LLM provider (falls back to chat) |
| `llm.heartbeat.model` | text | Heartbeat model |
| `llm.subconscious.provider` | text | Subconscious LLM provider |
| `llm.subconscious.model` | text | Subconscious model |
| `llm.guardrails.*` | text | Action-claim verifier LLM (falls back to subconscious) |
| `llm.extraction.*` | text | Conscious-extraction LLM (falls back to subconscious) |

## Heartbeat Configuration

| Key | Type | Description |
|-----|------|-------------|
| `heartbeat.interval_seconds` | int | Seconds between heartbeats |
| `heartbeat.max_energy` | float | Maximum energy cap |
| `heartbeat.energy_regen_rate` | float | Energy per hour |

## Maintenance Configuration

| Key | Type | Description |
|-----|------|-------------|
| `maintenance.subconscious_enabled` | bool | Toggle subconscious decider |
| `maintenance.subconscious_interval_seconds` | int | Decider cadence |

## Tools Configuration

| Key | Type | Description |
|-----|------|-------------|
| `tools` | object | Tool config: enabled/disabled, API keys, costs, MCP servers |
| `tools.workspace_path` | text | Filesystem tools workspace restriction |
| `mcp.skill_gated` | bool | MCP servers connect lazily on skill activation (default `true`; `false` = legacy eager startup connect) |
| `mcp.expose_unbound` | bool | Expose `mcp_*` schemas to turns that skip skill routing (default `false`) |

## Truthfulness Guardrails

| Key | Type | Description |
|-----|------|-------------|
| `guardrails.action_claims.enabled` | bool | Detect unsupported action claims in final text and append a visible `[Correction]` (default `true`) |
| `guardrails.action_claims.llm_verifier_enabled` | bool | Confirm/extend heuristic findings with an LLM pass (default `false`) |
| `inspection.retention_hint_enabled` | bool | Append a retention reminder to `inspect_source` read results (default `true`) |
| `inspection.config_prefixes` | array | Config key prefixes the agent may read via `inspect_config` (secret-named values redacted; `tools`/`oauth.*`/`token.*` always excluded) |

## Belief Revision

| Key | Type | Description |
|-----|------|-------------|
| `belief.revision_enabled` | bool | Calibrated confidence revision on corroborating/contradicting evidence (default `true`) |
| `belief.support_rate` | float | Fraction of remaining doubt closed by one independent supporting source at trust 1.0 (default `0.35`) |
| `belief.contradict_rate` | float | Fraction of current confidence removed by one independent contradiction at trust 1.0 (default `0.35`) |
| `belief.confidence_floor` | float | Confidence never drops below this (default `0.05`) |
| `belief.confidence_ceiling` | float | Confidence never reaches certainty (default `0.99`) |

## Origin Memories

| Key | Type | Description |
|-----|------|-------------|
| `origin_memories.enabled` | bool | Seed protected origin-story memories at consent and on maintenance ticks (default `true`; kill switch) |
| `origin_memories.trust` | float | Trust level for seeded origin memories (default `0.9`) |
| `origin_memories.confidence` | float | Confidence for seeded origin memories (default `0.9`) |
| `origin_memories.importance` | float | Importance for seeded origin memories (default `0.9`) |

## Conscious-Episode Extraction

| Key | Type | Description |
|-----|------|-------------|
| `extraction.enabled` | bool | Sweep chat turns + heartbeat episodes into selective durable memories (default `true`; kill switch) |
| `extraction.min_importance` | float | Units below this importance never earn an LLM pass (default `0.6`) |
| `extraction.batch_size` | int | Units claimed per extraction sweep (default `8`) |
| `extraction.min_confidence` | float | Extracted facts below this confidence are dropped (default `0.55`) |
| `extraction.max_facts_per_batch` | int | Soft cost cap on facts per sweep (default `5`) |

## Memory Budgets

| Key | Type | Description |
|-----|------|-------------|
| `memory.recall_default_limit` | int | Default recall count when the caller does not specify one (default `5`) |
| `memory.recall_max_limit` | int | Ceiling on recall count — a context/cost budget, not a knowledge limit (default `50`) |
| `memory.hydrate_memory_limit` | int | Default memory count for RAG hydration (default `10`) |
| `memory.context_section_limits` | object | Per-section caps for subconscious/hydration context assembly |

## Source Documents, Chunks, and Desk

| Key | Type | Description |
|-----|------|-------------|
| `memory.document_search_default_limit` / `_max_limit` | int | Row budgets for document search (defaults `10` / `50`) |
| `memory.source_chunk_search_default_limit` / `_max_limit` | int | Row budgets for passage (chunk) search (defaults `10` / `50`) |
| `retrieval.chunk_weight_lexical` / `_vector` / `_recency` / `_trust` / `_desk` | float | Hybrid chunk-search fusion weights (defaults `0.4` / `0.6` / `0.1` / `0.1` / `0.05`) |
| `retrieval.chunk_recency_half_life_days` | float | Document-age half life for the recency component (default `30`) |
| `memory.source_chunk_embed_batch_size` / `_claim_timeout_s` / `_max_attempts` | int | Background chunk-embedding queue tuning (defaults `32` / `120` / `3`) |
| `memory.source_document_desk_chunk_chars` | int | Desk chunk size for whole-document loads (default `8000`) |
| `memory.recmem_desk_list_default_limit` | int | Default rows for `list_desk` (default `20`) |
| `memory.recmem_desk_open_default_chars` | int | Default window when opening a desk item (default `4000`) |
| `memory.recmem_gc_*` | various | Desk GC: enabled, idle days, grace days, batch size (pinned items are skipped; redacted sources are swept regardless) |

## Ingestion

| Key | Type | Description |
|-----|------|-------------|
| `ingest.max_section_chars` / `ingest.chunk_overlap` | int | Chunk size and extraction-context overlap (defaults `2000` / `200`) |
| `ingest.artifact_max_db_bytes` | int | Originals up to this size are stored in-DB; larger go to `$HEXIS_ARTIFACT_DIR` (default `26214400`) |
| `ingest.xlsx_max_rows_per_sheet` | int | Spreadsheet row cap per sheet — capping always emits a `truncated_rows` warning (default `5000`) |
| `ingest.upload_max_bytes` | int | Upload API file-size cap; larger files use the CLI (default `104857600`) |
| `ingest.job_*` | various | Durable ingestion-job queue: content cap, claim timeout, retry backoff, batch size |

## Source Retention

All gated on `retention.enabled` (ships dark). User-provided sources never
auto-fade — they only leave via the fade-request → user-approval flow.

| Key | Type | Description |
|-----|------|-------------|
| `retention.doc_stale_days` / `doc_idle_days` / `doc_request_batch` | int | When user-provided documents trigger a fade *request* (defaults `180` / `90` / `2`) |
| `retention.agent_source_idle_days` | int | Archive agent-acquired sources untouched this long (default `60`) |
| `retention.agent_source_escalate_memories` | int | Agent-acquired sources cited by this many memories escalate to a user ask instead (default `5`) |
| `retention.agent_source_batch` | int | Agent-acquired sources processed per daily pass (default `5`) |

## OAuth Credentials

| Key | Type | Description |
|-----|------|-------------|
| `oauth.openai_codex` | object | OpenAI Codex OAuth credentials |
| `oauth.chutes` | object | Chutes OAuth credentials |
| `oauth.github_copilot` | object | GitHub Copilot credentials |
| `oauth.qwen_portal` | object | Qwen Portal credentials |
| `oauth.minimax_portal` | object | MiniMax Portal credentials |
| `oauth.google_gemini_cli` | object | Google Gemini CLI credentials |
| `oauth.google_antigravity` | object | Google Antigravity credentials |
| `token.anthropic_setup_token` | object | Anthropic setup token |

## Channel Configuration

| Key | Type | Description |
|-----|------|-------------|
| `channel.<name>.bot_token` | text | Env var name for bot token |
| `channel.<name>.allowed_*` | array | Allowlist (guild IDs, chat IDs, etc.) |

## Embedding Configuration

Embedding config is primarily via environment variables, not the config table. See [Environment Variables](../operations/environment-variables.md).

## Related

- [Environment Variables](../operations/environment-variables.md) -- .env configuration
- [Database](../operations/database.md) -- accessing the config table
