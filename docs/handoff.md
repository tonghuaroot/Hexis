# Hexis Handoff

Last updated: 2026-07-10 (HMX Slice 10 authoritative replacement complete)

## Current Status

The active workstream is HMX (`plans/hmx.md`). Slices 0-10 are complete:
schema prerequisites, canonical hashing, schema-valid JSON/JSONL export, a
fail-closed trust-anchor boundary, target-state diagnostics, transactional
additive import with full reference remapping, the operator CLI with
side-effect-free dry-run reporting, and isolated deliberative/analysis storage
with explicit review transitions, plus skill-gated agent tools for the complete
export/import/review workflow. Accepted imports now enter a bounded maintenance
re-embedding pipeline, refresh derived memory structures, and can carry eligible
raw RecMem units during port/duplicate. Pending and interrupted consolidation
work now travels as portable intent and resumes through the existing workers
without carrying runtime claim state. Protected and audit digests have an
explicit canonical byte contract and fixed cross-implementation vectors,
including ordered narrative chronology. Durable replacement consent,
acknowledgement, audit, snapshot, timeout, and Phase 0 verified-no-op machinery
is visible through heartbeat. Authoritative imports now require explicit
whole-section choices, import ordinary state transactionally, and execute
accepted protected replacements atomically with pre-write snapshots, immutable
audit, target-digest verification, and collateral-section verification. The
next implementation boundary is Slice 11 (bounded reversion execution and
snapshot purge lifecycle).

The prior hosted green baseline was `73f41b4` (`Complete HMX Slice 9 protected
replacement core`), run https://github.com/QuixiAI/Hexis/actions/runs/29099400191
(all jobs succeeded). Always verify the current head's hosted result with the
command in "Useful Commands" below rather than assuming this historical
baseline applies.

Important recent commits:

- `73f41b4` - Complete HMX Slice 9 protected replacement core
- `3cb8670` - Complete HMX Slice 8 canonical digests
- `7c3d0af` - Stabilize HNSW planner assertion
- `2787a60` - Complete HMX Slice 7 in-flight work
- `99f544d` - Complete HMX Slice 6 re-embedding
- `902c30f` - Complete HMX Slice 5 agent tools
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

- `core/digest.py` — all three hash families with initial inline property tests
  (`tests/core/test_hmx_digest.py`). Two documented
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
  `metadata.embedding_status = pending_import`; Slice 6 now processes that
  state after admission.
- At this slice boundary, `raw_units`, `config`, `in_flight_work`, and non-empty
  `audit_records` reported `unsupported_section` rather than being silently
  consumed. Slice 6 added the port/duplicate raw-unit path; the others remain
  later work.
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

### HMX Slice 6 re-embedding pipeline complete

Key files:

- `db/51_hmx_reembedding.sql` and migration `0010` - imported-memory queue,
  bounded claim/retry state, database-owned embedding, neighborhood/cluster
  refresh, and raw-unit RecMem ingestion.
- `services/hmx_reembedding.py` / `services/worker_service.py` - one claimed
  batch per maintenance tick with savepoint-isolated failures and actionable
  retry diagnostics.
- `core/memory_exchange.py` / `schemas/hmx-1.7.schema.json` - newly admitted
  memories are queued, port/duplicate raw units are validated and imported, and
  dry-run embedding estimates include eligible raw units.
- `tests/db/test_hmx_reembedding.py` /
  `tests/services/test_hmx_reembedding.py` - live-DB success, bounded failure,
  isolation, derivative refresh, raw-unit idempotency/routing, and worker tests.

Important behavior:

- Only active memories with an HMX provenance import chain can enter the queue.
  Staged, analysis-only, archived, and ordinary local memories are excluded.
- Claims use `FOR UPDATE SKIP LOCKED`, recover interrupted claims after a
  configured timeout, and stop after a configured maximum attempt count with
  the failure cause retained in memory metadata.
- Successful work replaces the sentinel from `get_embedding(content)`, records
  the live configured embedding model, recomputes the imported memories'
  neighborhoods, marks affected peers stale for normal maintenance, and
  refreshes linked cluster centroids and assignments.
- Promotion still copies no analysis embedding. Acceptance passes through the
  additive admission path and is freshly queued in the main index.
- Port/duplicate raw units enter `subconscious_units` through
  `recmem_ingest_turn` under `import:{export_id}:...`, retain source diagnostics,
  preserve derived-memory links, deduplicate through the normal RecMem key, and
  continue through the existing embed/route workers. Other intents do not place
  raw text into active RecMem.
- Focused cross-feature validation: 128 tests pass. Full validation: 2073 tests
  pass with the existing 421 advisory marker warnings. The wheel contains the
  service module plus both baseline and forward-migration SQL.

### HMX Slice 7 in-flight work complete

Key files:

- `db/52_hmx_in_flight_work.sql` and migration `0011` - portable task export,
  import reference ledger, task remapping, safe raw-unit route restoration, and
  failed-work diagnostics.
- `core/memory_exchange.py` / `schemas/hmx-1.7.schema.json` - independent task
  validation, dry-run drop prediction, safe import orchestration, JSONL
  transport, and structured work summaries.
- `apps/cli_exchange.py` / `apps/hexis_cli.py` /
  `core/tools/memory_exchange.py` - explicit `retry_failed_work` choice and
  export/import warnings that include the exact recovery action.
- `tests/db/test_hmx_in_flight_work.py` - live-DB coverage for portable export,
  remapping, idempotency, worker claims, isolation, missing inputs, diagnostics,
  and explicit retries across both task families.

Important behavior:

- Port/duplicate exports carry pending, in-progress, and failed RecMem and
  reconsolidation task intent. Local IDs are export-scoped; claim timestamps,
  completion state, results, progress counters, and worker locks never travel.
- In-progress tasks become fresh local pending work with remapped source units,
  target memories, and beliefs. The existing RecMem and reconsolidation workers
  claim them through their normal queues; no parallel HMX worker exists.
- Imported source-unit route states are restored before commit, preventing the
  normal raw router from creating duplicate consolidation work.
- Tasks missing any required imported input are dropped with both preflight and
  mutation warnings. Port/duplicate export warns when consolidation work is
  present without raw units and names `--include-raw` as the corrective action.
- Failed tasks remain non-runnable diagnostics by default. Retry requires the
  explicit CLI `--retry-failed-work` or tool `retry_failed_work=true`; the import
  ledger retains the source failure after retry and makes re-import idempotent.
- Focused cross-feature validation: 120 tests pass. Full validation: 2079 tests
  pass with the existing 421 advisory marker warnings. The wheel contains the
  baseline and migration SQL plus the updated schema and memory-exchange skill.

### HMX Slice 8 canonical digests complete

Key files:

- `core/digest.py` - canonical protected-section and audit-record byte
  serialization, fixed six-decimal float handling, non-finite rejection, and
  public pre-hash byte helpers for compatibility diagnosis.
- `tests/fixtures/digest/` - fixed protected and audit SHA-256 vectors plus
  named equality/divergence relations covering every Slice 8 acceptance gate.
- `tests/core/test_hmx_digest_fixtures.py` - vector runner and an explicit guard
  that the required compatibility properties remain represented.
- `plans/hmx.md` - reconciled canonical rules for narrative order,
  reference/provenance exclusions, record sorting, and serialization bytes.

Important behavior:

- `life_chapters` is an ordered authored chronology: reversing only the chapter
  array changes the protected digest. Turning points, narrative threads, and
  value conflicts remain set-like and sort by canonical record hash.
- Worldview, emotional-trigger, identity-facet, goal, and drive ordering remains
  semantic and independent of export IDs or remapped local UUIDs.
- The v1 byte contract uses compact recursively key-sorted JSON, ASCII string
  escaping, UTF-8 encoding, six-decimal floats, normalized negative zero, and
  loud rejection of NaN/Infinity.
- Fixed fixtures cover key order, ref remapping, every transport exclusion,
  unknown fields, floating-point noise, set order, ordered chapters, and true
  worldview/drive/identity changes. Audit fixtures cover transport-local dedupe
  and semantic divergence.
- Focused HMX validation: 143 tests pass. Full validation: 2104 tests pass with
  the existing 421 advisory marker warnings.

### HMX Slice 9 protected replacement core complete

Key files:

- `db/53_hmx_protected_replacement.sql` and migration `0012` - dedicated HMX
  consent, pending replacement attempts, immutable portable audit history, and
  bounded rollback snapshots.
- `core/protected_replacement.py` - trust-aware Phase 0 evaluation, pending
  acknowledgement state machine, audit import/forecast/dedupe, and snapshot API.
- `services/heartbeat_agentic.py`, `services/agent.py`, and
  `core/tools/memory_exchange.py` - actionable heartbeat visibility and the
  skill-gated `protected_replacement_review` acknowledgement tool.
- `tests/db/test_hmx_protected_replacement.py` - live-DB protocol coverage.

Important behavior:

- A content-identical section is a verified no-op only when lineage labels
  match and configured trust verifies them (or the caller explicitly opts into
  local label trust). The required immutable audit write fails closed; no
  consent, snapshot, pending record, or protected-state write occurs on Phase 0.
- Invalid declared digests and missing protocol capability fail before protocol
  state is created. Unverified operator signatures are discarded and surfaced.
- Every non-fast-path request creates dedicated immutable consent and waits for
  agent acknowledgement. Accept, refuse, request-modification, and defer are
  supported; refusal cannot be bypassed by retry. Timeout occurs only after both
  24 wall-clock hours and 10 heartbeats, and a timed-out request may be
  resubmitted as a new durable attempt.
- Protected audit records now export and import transactionally. Stable
  `audit_id` plus `audit_record_digest_v1` provides idempotent dedupe and loud
  divergence conflicts; non-port history remains a non-exported foreign
  diagnostic.
- Snapshot storage closes on the earlier heartbeat or wall-clock bound and
  retains a historical tombstone after payload purge.
- Accepting a pending request now enters Slice 10 execution in the same outer
  transaction. Any snapshot, audit, SQL import, target verification, or scope
  verification failure rolls the acknowledgement back to pending. Reversion
  execution remains Slice 11.

Validation at the Slice 9 boundary: 12 protocol/migration tests and 151
HMX-focused tests passed. Full validation was 2115 tests with the existing 421
advisory marker warnings.

### HMX Slice 10 complete (authoritative whole-section replacement)

Key files:

- `db/54_hmx_authoritative_import.sql` and migration `0013` - DB-owned clearing
  and import for all six protected sections, reference remapping, native current
  chapter restoration, and import-trigger isolation.
- `core/memory_exchange.py` and `core/protected_replacement.py` - authoritative
  preflight, ordinary import, durable request creation, inspection, and atomic
  accepted execution.
- `apps/hexis_cli.py` and `apps/cli_exchange.py` - derived strategy choices,
  repeatable `--replace`, rationale/trust controls, and complete human/JSON
  outcome reporting.
- `core/tools/memory_exchange.py`, `services/heartbeat_agentic.py`, and
  `skills/installed/memory-exchange/SKILL.md` - inspect-before-review agent flow.
- `tests/db/test_hmx_authoritative_import.py` - live execution coverage for all
  protected sections, stale-state rejection, runtime chapter integrity, and
  transactional rollback.

Important behavior:

- `authoritative` is limited to `port`/`duplicate`, requires at least one
  explicit protected section and rationale, and never treats unselected
  protected state as importable ambient state.
- Ordinary records import in the request transaction. Divergent protected
  sections remain untouched until explicit agent acceptance; trusted,
  content-identical sections take the audited Phase 0 no-op.
- Acceptance snapshots and audits before the DB-owned whole-section write, then
  verifies the target digest and every non-target protected digest before
  committing. Any failure rolls back acknowledgement, snapshot, audit, and
  state mutation together.
- The read-only inspector shows current/imported content and detects local state
  changes while a request is pending. Accept refuses stale requests and gives
  the next action instead of overwriting newer state.
- Current life chapter identity is explicitly a narrative-derived projection;
  its digest fixture prevents a narrative change from masquerading as an
  independent identity mutation while runtime links remain intact.

Validation: 21 authoritative/protocol/migration tests, 77 scoped
implementation tests, and 166 HMX-focused tests pass. Full validation: 2130
tests pass with the existing 421 advisory marker warnings. Formatting,
compilation, focused mypy, SQL baseline/migration equivalence, and diff hygiene
also pass. The built wheel contains the authoritative baseline SQL, migration,
protected-replacement module, HMX schema, and updated memory-exchange skill.

Next: Slice 11 implements explicit, bounded reversion from the durable snapshot
and audit records now created by accepted replacements.

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

Continue the HMX thread at Slice 11 from the durable execution state now in
place. Read `core/protected_replacement.py`,
`db/53_hmx_protected_replacement.sql`, `db/54_hmx_authoritative_import.sql`,
`tests/db/test_hmx_authoritative_import.py`, and the Slice 11 reversion
requirements in `plans/hmx.md` before editing.

Next highest-leverage options, in rough priority order:

1. HMX Slice 11: implement explicit reversion within the earlier-of heartbeat
   and wall-clock window, restore snapshot state atomically, write the immutable
   reversion audit, and preserve the tombstone after purge.
2. Phase 3 interop: OpenAI-compatible `GET /v1/models` +
   `POST /v1/chat/completions` (with streaming) on `apps/hexis_api.py`, and MCP
   server tests for tool listing/dispatch.
3. Finish Phase 2 hardening: plugin manifest/config-schema validation and an
   explicit agent-vs-user skill provenance guard.
4. Phase 4 "it learns": FTS cross-session search + a background
   self-improvement worker that authors skills from recent experience (the
   `author_skill` provenance footer already exists to build on).
