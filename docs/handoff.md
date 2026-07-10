# Hexis Handoff

Last updated: 2026-07-09 (HMX Slice 5 skill-first agent tools complete)

## Current Status

The active workstream is HMX (`plans/hmx.md`). Slices 0-5 are complete:
schema prerequisites, canonical hashing, schema-valid JSON/JSONL export, a
fail-closed trust-anchor boundary, target-state diagnostics, transactional
additive import with full reference remapping, the operator CLI with
side-effect-free dry-run reporting, and isolated deliberative/analysis storage
with explicit review transitions, plus skill-gated agent tools for the complete
export/import/review workflow. The next implementation boundary is Slice 6
(re-embedding accepted imports and recomputing derived memory structures).

The prior hosted green baseline was `3ba0bc6` (`Complete HMX Slice 4 isolated
review`), run https://github.com/QuixiAI/Hexis/actions/runs/29053571794 (all jobs
succeeded). Always verify the current head's hosted result with the command in
"Useful Commands" below rather than assuming this historical baseline applies.

Important recent commits:

- `3ba0bc6` - Complete HMX Slice 4 isolated review
- `c3be2f8` - Wait for DB init completion in CI
- `e0fb2cf` - Stabilize CI fake embedding lane
- `afeaacb` - Add CI DB image fallback
- `cb2c25c` - Add CI quality gate
- `f14ed93` - Make agent capabilities skill-first
- `d9540c6` - Unify consent onto the DB
- `3ae887f` - Add schema migrations + AGE-aware backup/restore

## User Direction

The user wants Hexis brought closer to parity with `.reference/hermes-agent` and
`.reference/openclaw`, especially on engineering quality, extensibility, and prompt/token efficiency.

Specific user preferences from this workstream:

- Prefer skills over tools for agent capability exposure.
- Skills must be discoverable by the model, for example through a `list_skills`-style capability.
- Hexis should be able to author its own skills.
- Prompting should be more token-efficient; avoid paying for large, duplicated tool descriptions and static identity blocks every call.
- Commit and push completed work, then verify the hosted CI result rather than stopping at local tests.

## What Was Completed

### Skills-first capability surface

Commit: `f14ed93`

Key files:

- `core/tools/skills.py`
- `services/skill_runtime.py`
- `services/agent.py`
- `core/agent_loop.py`
- `services/prompt_resources.py`
- `skills/installed/core-memory/SKILL.md`
- `skills/installed/skill-authoring/SKILL.md`

The commit made agent capabilities skill-first and added tests:

- `tests/core/test_skills_marketplace.py`
- `tests/core/test_tool_unification.py`
- `tests/core/test_agent_loop.py`
- `tests/services/test_heartbeat_agentic.py`

Resume advice: before extending this, read `core/tools/skills.py` and
`services/skill_runtime.py` first. They are now the center of gravity for
discoverability and skill execution.

### Skill surface: compact index + plugin dirs (Phase 2 core)

Commit: `Compact skill prompt to an index; load plugin skill dirs`

Key files:

- `services/skill_runtime.py` — `format_skills_prompt` (compact index),
  `load_available_skills` (plugin-dir-aware loading), `SkillSelection.available`
- `services/agent.py` — one `## Skills` section replaces `## Tool Use` +
  full-body `## Active Skills`
- `core/tools/registry.py` — `ToolRegistry.extra_skill_dirs`, populated from
  `PluginRegistry.get_skill_dirs()` in `create_full_registry`
- `skills/base.py` — `SkillSpec.to_index_line()`

What changed:

- The system prompt now carries a flat ~355-token skill section: one index
  line per skill (active + full catalog), never full SKILL.md bodies. Before,
  every active skill's whole body was injected — worst case measured ~2,540
  tokens when 4 skills auto-activated; now constant regardless of activation.
- Full skill instructions are delivered only on demand by `use_skill` (which
  also unlocks the skill's bound tools mid-turn; `AgentLoop` already handled
  that and it is covered by `test_use_skill_unlocks_bound_tools_for_next_iteration`).
- Plugin-provided skill directories now actually load: they were collected by
  `plugins/loader.py` but dropped. `create_full_registry` copies them onto
  `registry.extra_skill_dirs` and all skill loading in `skill_runtime` scans
  them. Tests: `TestPluginSkillDirs` in `tests/core/test_skills_marketplace.py`.

### CI quality gate

Commits: `cb2c25c`, `afeaacb`, `e0fb2cf`, `c3be2f8`

Key files:

- `.github/workflows/ci.yml`
- `.github/workflows/osv.yml`
- `.github/dependabot.yml`
- `ops/ci/fake_embeddings.py`
- `ops/ci/migration_survivor.py`
- `docker-compose.yml`
- `ops/docker-compose.runtime.yml`
- `docs/contributing/index.md`
- `docs/contributing/testing.md`
- `CLAUDE.md`

What exists now:

- A required aggregate `all-checks-pass` job.
- `lint` lane with import smoke and advisory formatting/type checks.
- `workflow-lint` lane with `actionlint` and advisory `zizmor`.
- Full `test` lane against a real Hexis Postgres/AGE/pgvector DB.
- `migration-survivor` lane proving an existing DB can migrate without losing seeded data.
- OSV scanning workflow.
- Dependabot scoped to GitHub Actions.

CI implementation details that matter:

- The GHCR image `ghcr.io/quixiai/hexis-brain:latest` may be inaccessible to hosted
  runners. The workflow now tries to pull it, then falls back to building
  `ops/Dockerfile.db` locally as `hexis-ci-brain`.
- CI does not use Ollama. `ops/ci/fake_embeddings.py` serves deterministic positive
  768-dimensional vectors over `/api/embed` and `/api/tags`.
- The fake vectors are intentionally non-negative because several DB tests compare
  generated embeddings against positive `array_fill(...)` vectors.
- CI waits for the Docker log marker `PostgreSQL init process complete` before
  connecting. `pg_isready` and simple `psql` probes can pass against the temporary
  init server while SQL files are still replaying, which caused the earlier
  `ConnectionResetError`.

Hosted CI failure history and fixes:

- First push failed because HTTPS auth lacked GitHub `workflow` scope. Pushes were
  then sent via SSH: `git push git@github.com:QuixiAI/Hexis.git main`.
- Initial CI failed pulling GHCR with `denied`; fixed by DB-image fallback.
- Next CI failed two recall-ranking tests because fake vectors included negative
  dimensions; fixed by positive deterministic vectors.
- Next CI failed migration-survivor with asyncpg `ConnectionResetError`; fixed by
  waiting for full Postgres init completion.

### HMX Slice 0 complete (memory export/import prerequisite)

Spec: `plans/hmx.md` (HMX v1.7). Slice 0 — the schema prerequisite for all
HMX work — is fully landed:

- Migrations `0001` (enum values: `staged`, `SUPERSEDES`/`CONTAINS`/
  `HAS_BELIEF`/`MEMBER_OF`), `0002` (AGE `SUPERSEDES` label, `agent.lineage_id`),
  and `0003` (bootstrap provenance) — all mirrored into the baseline
  (`db/00_tables.sql`, `db/05_functions_provenance_trust.sql`, `db/91_triggers.sql`).
- Init-created memories are tagged `metadata.provenance.acquisition_mode =
  "bootstrap"` + `replaceable_during_bootstrap = true` by the
  `trg_hmx_bootstrap_provenance` trigger (single seam using `reset_persona()`'s
  init predicate — no per-init-function edits). Rows with non-empty
  `change_history` read as `experienced`.
- `hmx_backfill_provenance()` classifies legacy rows; migration 0003 runs it
  once. Both the migration-survivor CI lane and
  `tests/db/test_migrations.py` now assert the backfill classified
  pre-migration data.
- Tests: `tests/db/test_hmx_slice0.py`.

Note for the next slice: `tests/db/test_migrations.py` no longer asserts the
baseline LACKS migrated values — deltas are mirrored into the baseline per
`db/migrations/README.md`, so "old deployment" is simulated only by an empty
`schema_migrations` table.

### HMX Slice 1 complete

Landed so far:

- `core/digest.py` — all three hash families with the Slice 8 eight-property
  fixture suite passing (`tests/core/test_hmx_digest.py`). Two documented
  spec resolutions (module docstring): `*_ref`/`*_refs` fields and the
  `provenance` subtree are excluded from digest input, following the
  ref/remap-independence principle over the spec's contradictory field lists.
- `core/memory_exchange.py` — intent policy (`resolve_export_sections`,
  the port/duplicate vs telepathy/analysis protected-section matrix),
  envelope construction, and `load_source_context()` which derives the
  source block from the live DB (edge types from the `graph_edge_type`
  enum, lineage from config, schema version from `schema_migrations`).
  Tests: `tests/core/test_hmx_exchange.py`.

- `db/48_functions_memory_exchange.sql` (mirrored as migration `0004`) —
  per-section export functions returning raw JSONB with local UUIDs and no
  embeddings. Relationships export from `memory_edges` (the primary flat
  substrate) plus SUPERSEDES edges derived from `memories.superseded_by`;
  narrative/identity export from AGE via Cypher; worldview/goal memories ride
  only in their dedicated sections, never doubled into `memories`.
- `export_hmx()` in `core/memory_exchange.py` — the full export pipeline:
  export-scoped refs, content hashes, provenance enrichment (defaults to
  `experienced`), `section_digests` for port/duplicate (Phase 0 fast path),
  statistics, and `iter_hmx_jsonl()` streaming. Tests:
  `tests/db/test_hmx_export.py`, incl. the invariant that two exports with
  different export_ids produce identical protected digests.

- `schemas/hmx-1.7.schema.json` — packaged Draft 2020-12 canonical schema with
  per-section record validation, forward-compatible unknown fields/sections,
  and the required discriminated union for replacement/verified/reversion
  audit records. `export_hmx()` validates every completed export and reports a
  JSON path on failure. Dependency: `jsonschema>=4.18.0`.
- `core/trust_anchors.py` — deployment-pluggable `TrustAnchorVerifier`, explicit
  verified/unverified/invalid outcomes, and an `UnconfiguredTrustAnchors`
  default that never turns signatures or matching lineage labels into proof.
- Migrations `0005` and `0006` complete the export journey for existing DBs:
  AGE narrative nodes now carry export-scopeable IDs and normalized wire fields;
  explicit `include_raw_units` / `include_config` requests now emit the promised
  sections instead of only setting scope flags. Raw units omit embeddings and
  redacted rows; config excludes credential/trust material using the exact
  patterns declared in `privacy.excluded_secret_patterns`.
- Focused validation: 69 HMX schema/digest/trust/export/migration tests pass.

### HMX Slice 2 additive import complete locally

Key files:

- `core/memory_exchange.py` — `import_hmx()` transaction/policy orchestration,
  per-record schema validation, 1.x minor-version tolerance, intent-aware
  acquisition modes, import-chain append, conflict/warning reporting, and
  fresh local reference maps.
- `db/48_functions_memory_exchange.sql` — `hexis_instance_is_empty()` plus SQL
  import functions for memories, episodes, relationships, clusters, identity,
  worldview/goals, drives, emotional triggers, narrative, and goal hierarchy
  remapping.
- Migrations `0007` and `0008` — additive import functions, provenance storage
  for drives/triggers, protected-state diagnostics, and trigger behavior.
- `core/migrations.py` — migration bookkeeping now explicitly reuses either
  `public.schema_migrations` or the legacy `ag_catalog.schema_migrations` table;
  this prevents search-path changes from replaying already-applied migrations.
- `pyproject.toml` now packages `db/migrations/*.sql`; wheel installs previously
  carried the baseline SQL but silently omitted every forward migration.
- `tests/db/test_hmx_import.py` — real DB coverage for duplicate-content reuse,
  fresh UUID remapping, episodes/clusters/edges, legacy `superseded_by`, unknown
  edge preservation, per-record validation, lineage enforcement, target-state
  transitions, and full protected export-import-export round trips.

Important behavior:

- Import preflight and mutation share an advisory-locked transaction, so the
  empty/active decision cannot go stale before protected writes.
- Port/duplicate preserves source acquisition mode and adopts source lineage on
  an empty target. Cross-agent additive records become `imported_and_accepted`.
- Protected state imports directly only for port/duplicate into an empty target.
  Telepathy/analysis or active targets fail with `bootstrap_state_violation`.
- Memories receive a zero-vector sentinel plus
  `metadata.embedding_status = pending_import`; re-embedding remains Slice 6.
- `raw_units`, `config`, `in_flight_work`, and non-empty `audit_records` report
  `unsupported_section` rather than being silently consumed; their import paths
  belong to later slices.
- Focused validation: 79 HMX schema/digest/trust/export/import/migration tests pass.

### HMX Slice 3 CLI and dry-run complete locally

Key files:

- `apps/cli_exchange.py` — JSON/JSONL input, atomic private-file output,
  database connection retry, intent confirmation, skip controls, and human/JSON
  reports.
- `apps/hexis_cli.py` — top-level `hexis export` and `hexis import` argument and
  dispatch integration.
- `core/memory_exchange.py` — `HmxDryRunResult`, `dry_run_hmx()`, and lossless
  typed JSONL reconstruction (including empty and forward-compatible sections).
- `tests/cli/test_hmx_cli.py` — real CLI export, overwrite refusal, dry-run,
  intent mismatch, and confirmed additive import coverage.

Important behavior:

- `hexis import --dry-run` validates records independently, predicts normalized
  duplicate-content reuse, reports target state and protected-section policy,
  estimates embedding work, and surfaces privacy/unsupported-section warnings
  without opening a write transaction.
- File export is atomic, mode `0600`, and never overwrites without
  `--overwrite`. JSON/JSONL written to stdout remains machine-readable because
  status output goes elsewhere.
- Mutating import requires `--confirm-intent` to exactly match the file. A
  policy-blocked dry-run exits nonzero, so automation cannot mistake a report
  for permission to import.
- Intent-derived safe defaults remain visible: deliberative, analysis-only, and
  authoritative strategies report their future implementation boundary rather
  than silently falling back to additive. Operators may explicitly choose
  additive where current policy permits it.
- Strict redaction and raw-unit export cannot be combined. Config export still
  derives its secret exclusions from the export function rather than exposing
  credential/trust material.
- Focused HMX/CLI validation: 91 tests pass. Full validation: 2051 tests pass;
  the existing 421 pytest marker warnings remain advisory.

### HMX Slice 4 isolated review storage complete

Key files:

- `db/49_hmx_import_staging.sql` and migration `0009` — deliberative batches,
  staged records, persistent per-batch reference maps, conflict grouping, and
  pending-review summaries.
- `db/50_hmx_analysis_storage.sql` — physically separate analysis batches and
  records plus copy-on-promote and provenance-preserving demotion.
- `core/memory_exchange.py` — strategy dispatch, isolated loading, acceptance,
  rejection, material modification, archived quoting, promotion, and demotion.
- `apps/cli_exchange.py` / `apps/hexis_cli.py` — intent-derived strategies and
  `hexis import-review` operator commands, so staging has no dead-end.
- `services/heartbeat_agentic.py` — pending deliberative review count enters
  heartbeat context; analysis-only records remain absent.
- `tests/db/test_hmx_staging.py` — live-DB isolation and lifecycle coverage.

Important behavior:

- Deliberative and analysis-only imports never insert into `memories`, create
  neighborhoods, update drives/emotions, or enter active recall on load.
- Analysis storage has no embedding column and never appears in heartbeat
  context. Promotion copies the source record and does not remove analysis
  history or copy an embedding.
- Accepted records become `imported_and_accepted`; materially modified records
  become `derived_from_import`. The staging import event is not duplicated in
  the provenance chain when review completes.
- Relationship acceptance requires its referenced records to have persistent
  batch mappings first. Other partial structures retain the existing additive
  import warnings.
- Protected records can be staged and inspected. Acceptance into an active
  target still fails with `bootstrap_state_violation` until the Protected
  Replacement Protocol lands; review does not bypass that boundary.
- Reject, quote, and demote decisions retain their source record and rationale.
  Quoted material is archived and excluded from ordinary active recall.
- Focused HMX/CLI validation: 102 tests pass. Full validation: 2063 tests pass;
  the existing 421 pytest marker warnings remain advisory.

### HMX Slice 5 skill-first agent tools complete

Key files:

- `core/tools/memory_exchange.py` — ten chat/heartbeat handlers for export,
  dry-run, import, pending review, all four review decisions, promotion, and
  demotion.
- `core/hmx_files.py` / `apps/cli_exchange.py` — one shared JSON/JSONL transport
  with atomic mode-`0600` writes and explicit no-clobber behavior.
- `skills/installed/memory-exchange/SKILL.md` — on-demand workflow and safety
  instructions binding all ten handlers; the schemas are absent from normal
  model context until the skill is selected or activated.
- `core/tools/registry.py` / `core/tools/__init__.py` — default-registry and
  public factory wiring.
- `pyproject.toml` — bundled skill documents are package data, fixing the prior
  source-checkout-only behavior for every installed skill.
- `tests/core/test_hmx_tools.py` — live-DB end-to-end handler journey, policy
  metadata, registry/skill selection, path confinement, private file mode, and
  package-data coverage.

Important behavior:

- Intent, protected-section, redaction, and supported-strategy schemas derive
  from the HMX policy module rather than duplicating constants in tool code.
- Every mutating or data-exporting handler requires approval, is sequential,
  and is unavailable to external MCP contexts. Dry-run and review listing are
  read-only.
- Import requires exact intent confirmation and always repeats DB-aware
  preflight immediately before mutation. A blocked forecast is returned as
  structured output with a boundary error; it never falls through to import.
- File access honors the execution context's read/write flags and workspace
  boundary. Export never overwrites or creates a parent directory implicitly.
- Deliberative and analysis workflows complete in place through accept, reject,
  modify, quote, promote, and demote tools; protected acceptance still cannot
  bypass active-state policy.
- Focused HMX/CLI/skill/tool validation: 198 tests pass. Full validation: 2067
  tests pass with the existing 421 advisory marker warnings. Wheel contents
  were inspected and include both HMX modules plus all bundled `SKILL.md` files.

Next: Slice 6 connects accepted `pending_import` memories to the maintenance
embedding queue, refreshes their vectors, and recomputes derived neighborhoods
and clusters without embedding staged or analysis-only records into active
recall.

## Current Roadmap

This is the active quality-parity roadmap derived from reviewing Hermes and OpenClaw.

### Phase 1 - Engineering rigor foundation

Status: mostly done.

Completed:

- CI gate.
- Migration-survivor job.
- Fake embedding service for CI.
- DB image fallback.
- Compose restart and healthcheck improvements.
- OSV and Dependabot setup.

Still worth considering later:

- Make formatting/type checks hard gates only after a deliberate cleanup pass.
  Current `black`, `isort`, and `mypy` checks are advisory because the existing
  repo is not formatting-clean.
- Add action SHA pinning if the team wants stronger supply-chain posture.
- Add `uv.lock` and a lockfile check if/when dependency management is standardized.

### Phase 2 - Extensibility: make plugins and skills real

Status: core loop done (see "Skill surface: compact index + plugin dirs").

Completed:

- Live agent uses skills as the primary capability abstraction (`f14ed93`).
- Tool descriptions are not duplicated in the prompt; schemas ride the tool API.
- Skill discovery is explicit and cheap: a compact always-present index plus
  `list_skills`/`use_skill` on-demand detail.
- Plugin-provided skill dirs load into selection, discovery, and activation.
- Hexis authors skills via `author_skill` with provenance footers, writing only
  to `~/.hexis/skills/agent-authored/`.

Still open in Phase 2:

- Validate plugin manifests and config schemas at load time.
- Enforce (not just convention) that Hexis may modify only agent-authored
  skills, never user-authored ones — `author_skill` writes only to its own dir
  today, which protects user files, but there is no explicit provenance check.

### Phase 3 - Interop and reach

Goals:

- Add an OpenAI-compatible API surface:
  - `GET /v1/models`
  - `POST /v1/chat/completions`
  - streaming chat completion chunks
- Add MCP server tests for tool listing and dispatch.
- Consider streamable HTTP MCP transport after stdio is tested.

Likely files:

- `apps/hexis_api.py`
- `services/chat.py`
- `apps/hexis_mcp_server.py`
- `tests/services/`
- `tests/core/`

### Phase 4 - "It learns" differentiator

Goals:

- Add free cross-session search over stored turns/memories using Postgres FTS.
- Add a background self-improvement worker that reviews recent experience and
  authors or updates skills.
- Tag self-authored skills with provenance, source memories, and confidence.

Likely files:

- `services/worker_service.py`
- `services/recmem.py`
- `services/summarization.py`
- `services/skill_runtime.py`
- `skills/installed/skill-authoring/SKILL.md`
- DB migrations under `db/migrations/`

### Phase 5 - Demo, DX, and measurement

Goals:

- Build a one-command "it's alive" demo that proves heartbeat, recall, boundary
  refusal, energy, and self-initiated behavior.
- Add a capability maturity scorecard similar to OpenClaw's QA scenarios.
- Improve channel presentation with typed portable message blocks.
- Continue docs coherence work.

## Generic Identity Adaptation Notes

If Hexis is adapted for a strongly defined identity or character, the work should
be treated as a prompt-surface consistency problem, not just a seed-data problem.

Key finding:

- Identity data can be seeded into memories/config through character cards and
  initialization profile data, but several prompt wrappers still contain static
  Hexis/AI-agent framing. A high-fidelity adaptation needs one rendered identity
  header sourced from configuration, plus task prompts that avoid restating a
  competing identity.

High-impact prompt areas:

- `services/prompts/conversation.md`
- `services/prompts/rlm_chat_system.md`
- `services/prompts/heartbeat_agentic.md`
- `services/prompts/heartbeat_system.md`
- `services/prompts/rlm_heartbeat_system.md`
- `services/prompts/rlm_slow_ingest_system.md`
- `services/prompts/subconscious.md`
- `services/prompts/personhood.md`
- DB mirror: `db/40_seed_prompt_modules.sql`

Important rule:

- Prompt files have DB mirrors in `prompt_modules`. If editing static prompts,
  update both the `.md` files and the `db/40_seed_prompt_modules.sql` rows, or
  update the DB rows at runtime via `upsert_prompt_module`.

Recommended durable fix:

- Add one rendered identity header sourced from the active character card or
  `agent.init_profile`, then reduce static prompt files to task-role
  instructions. This prevents future prompt edits from reintroducing generic
  Hexis identity leaks.

## Useful Commands

Check latest GitHub run:

```bash
gh run list --repo QuixiAI/Hexis --branch main --limit 8 \
  --json databaseId,workflowName,status,conclusion,headSha,createdAt,url \
  | jq -r '.[] | [.databaseId,.workflowName,.status,(.conclusion//""),.headSha[0:7],.createdAt,.url] | @tsv'
```

Inspect a run:

```bash
gh run view RUN_ID --repo QuixiAI/Hexis --json status,conclusion,jobs,url \
  | jq -r '.status + " " + (.conclusion//"") + " " + .url, (.jobs[] | [.name,.status,(.conclusion//""),.databaseId] | @tsv)'
```

Push workflow changes over SSH if HTTPS rejects workflow edits:

```bash
git push git@github.com:QuixiAI/Hexis.git main
```

Local validation used during the CI work:

```bash
python -m py_compile ops/ci/fake_embeddings.py ops/ci/migration_survivor.py
docker compose config --quiet
docker compose -f ops/docker-compose.runtime.yml config --quiet
ruby -e 'require "yaml"; ARGV.each { |p| YAML.load_file(p); puts "#{p}: ok" }' \
  .github/workflows/ci.yml .github/workflows/osv.yml .github/dependabot.yml
pytest tests/db -q
pytest tests/core tests/services tests/cli -q
```

Run actionlint locally:

```bash
set -e
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
bash <(curl -sSf https://raw.githubusercontent.com/rhysd/actionlint/main/scripts/download-actionlint.bash)
"$tmp/actionlint" -color /Users/eric/hexis/.github/workflows/*.yml
```

## Resume Recommendation

Do not continue debugging the old CI failures first. Continue the HMX thread at
Slice 6 from the accepted-import contract now pinned in tests. Read
`core/memory_exchange.py`, `core/tools/memory_exchange.py`,
`services/worker_service.py`, `tests/db/test_hmx_staging.py`, and the Slice 6
plan section before editing.

Next highest-leverage options, in rough priority order:

1. HMX Slice 6: queue and process re-embedding for accepted imported memories,
   then recompute neighborhoods/clusters while preserving staging isolation.
2. Phase 3 interop: OpenAI-compatible `GET /v1/models` +
   `POST /v1/chat/completions` (with streaming) on `apps/hexis_api.py`, and MCP
   server tests for tool listing/dispatch.
3. Finish Phase 2 hardening: plugin manifest/config-schema validation and an
   explicit agent-vs-user skill provenance guard.
4. Phase 4 "it learns": FTS cross-session search + a background
   self-improvement worker that authors skills from recent experience (the
   `author_skill` provenance footer already exists to build on).
