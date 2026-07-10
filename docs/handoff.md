# Hexis Handoff

Last updated: 2026-07-10 (Phase 2 extensibility hardening complete)

## Current Status

The HMX workstream (`plans/hmx.md`) is MVP-complete, Phase 2 extensibility is
hardened, and the core Phase 3 interop work is complete. Slices 0-13 and the
final HMX acceptance audit are complete:
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
audit, target-digest verification, and collateral-section verification.
Executed replacements now have explicit, one-shot reversion windows bounded by
independent heartbeat and wall-clock limits; reversion restores reference
topology, refuses newer-state overwrite, verifies the result atomically, and
purges consumed snapshot payloads while retaining tombstones. Rare operator
overrides now require an exact responsibility phrase, enumerated
reason and evidence, plus an Ed25519 signature over the complete replacement
bundle verified against a configured public trust anchor. Overrides cannot
bypass an agent refusal and retain the normal reversion window. The agent now
has skill-gated list, inspect, acknowledge, audit-history, open-reversion, and
explicit revert tools without any operator-override capability. The durable
criterion-by-criterion completion record is `docs/hmx-acceptance.md`. Hexis now
also serves its canonical agent through OpenAI-compatible model discovery and
buffered/streamed chat completions, with tested MCP listing and dispatch.
Plugin manifests and live configuration now fail closed before registration,
and agent skill updates require explicit ownership provenance. The next
implementation boundary is Phase 4 cross-session learning.

The prior hosted green baseline was `a31b0b8` (`Complete HMX Slice 13 agent
protocol tools`), run
https://github.com/QuixiAI/Hexis/actions/runs/29114104335
(all jobs succeeded). Always verify the current head's hosted result with the
command in "Useful Commands" below rather than assuming this historical
baseline applies.

Important recent commits:

- `a31b0b8` - Complete HMX Slice 13 agent protocol tools
- `43f2e70` - Complete HMX Slice 12 operator override
- `8e2d524` - Complete HMX Slice 11 bounded reversion
- `c0a2e8e` - Complete HMX Slice 10 authoritative replacement
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

### HMX Slice 11 complete (bounded protected-state reversion)

Key files:

- `db/55_hmx_reversion.sql` and migration `0014` - snapshot reference maps,
  widened request state, window discovery, and consumed-snapshot tombstones.
- `core/protected_replacement.py` - open-window inspection and atomic,
  audit-addressed reversion.
- `core/memory_exchange.py` - restore preparation that converts wire records to
  storage input without adding a false import-history hop.
- `core/tools/memory_exchange.py`, `services/heartbeat_agentic.py`, and
  `skills/installed/memory-exchange/SKILL.md` - list, inspect, and explicit
  reversion controls surfaced to the agent.
- `tests/db/test_hmx_authoritative_import.py` - replace-and-revert coverage for
  all six protected sections plus expiry, drift, rollback, and reference edges.

Important behavior:

- Reversion is never automatic. It requires the local replacement audit ID and
  a non-empty rationale, and is idempotent after a lost successful response.
- The window closes when either 7 heartbeats or the bounded wall-clock deadline
  expires. Heartbeat surfaces remaining choices without choosing for the agent.
- The current target digest must still match the executed replacement. Newer
  protected state is never overwritten; the error gives the exact authoritative
  request path for any further change.
- Snapshot content, its digest, the replacement audit's previous/new digests,
  target output, and all collateral protected digests are verified. Snapshot
  reference maps restore surviving local evidence and other reference topology.
- Restore, immutable reversion audit, request transition, and snapshot
  consumption are one transaction. Failure leaves current state and the open
  snapshot intact. Success immediately purges the sensitive payload and retains
  a `consumed_by_reversion` tombstone.

Validation: 63 scoped Slice 10-11 database/tool/heartbeat/CLI tests and 172
HMX-focused tests pass. Full validation: 2137 tests pass with the existing 421
advisory marker warnings. Formatting, compilation, focused mypy, SQL
baseline/migration equivalence, and diff hygiene also pass. The built wheel
contains the Slice 11 baseline SQL, migration, protected-replacement module,
HMX schema, and updated memory-exchange skill.

Slice 12 follows with the signature-verified, verbatim-confirmed operator
override path without weakening ordinary protected replacement policy.

### HMX Slice 12 complete (operator override and trust anchors)

Key files:

- `core/trust_anchors.py` - concrete Ed25519 operator verifier, strict key and
  signature parsing, stable public-key fingerprint, and fail-closed environment
  loading from `HEXIS_HMX_OPERATOR_ED25519_PUBLIC_KEY`.
- `core/protected_replacement.py` - canonical multi-section signing payload,
  override-field and live-state validation, refusal protection, and execution
  through the existing atomic snapshot/audit/write/digest path.
- `apps/hexis_cli.py` and `apps/cli_exchange.py` - `--force-replace` arguments,
  side-effect-free signing material in dry-run output, signature verification,
  and prominent override results.
- `tests/core/test_hmx_operator_override.py`,
  `tests/core/test_hmx_trust_anchors.py`,
  `tests/db/test_hmx_authoritative_import.py`, and
  `tests/cli/test_hmx_cli.py` - canonical payload, real Ed25519, fail-closed,
  atomic multi-section, refusal, audit, and CLI journey coverage.

Important behavior:

- One Ed25519 signature covers the complete sorted replacement bundle, including
  source, each current/imported protected digest pair, the exact responsibility
  phrase, reason code, evidence reference, rationale, and operator identity.
  State drift therefore invalidates an old signature.
- Dry-run JSON emits the exact base64 payload and SHA-256 digest without requiring
  a signature. Execution requires a verified signature and configured public
  trust anchor; unconfigured, malformed, mismatched, or stale signatures fail as
  `unverified_signature` before any import state is committed.
- `agent_paused` and `agent_terminated` are checked against live database state.
  `agent_unresponsive` requires the live agent to be running and unpaused. Every
  reason requires a `scheme:value` reference to independently recorded evidence.
- Pending and deferred requests can be overridden. Refused or
  modification-requested operations are answered decisions and cannot be
  bypassed. Multi-section ordinary/protected writes and audits remain one
  transaction.
- Override audits record `replacement_executor=operator_override`,
  `agent_acknowledgement=bypassed`, reason, evidence, verified anchor, payload
  digest, signature, and operator identity. The normal 7-heartbeat/30-day
  earlier-of reversion policy remains unchanged.

Validation: 184 HMX-focused tests and 2149 full-suite tests pass with the
existing 421 advisory marker warnings. Focused formatting, compilation, mypy,
wheel inspection, and diff hygiene pass. The wheel contains the updated trust,
replacement, memory-exchange, and CLI modules.

Slice 13 follows with the agent-facing replacement-protocol tools described in
`plans/hmx.md`; operator override remains CLI/operator-only.

### HMX Slice 13 complete (agent replacement protocol tools)

Key files:

- `core/tools/protected_replacement.py` - dedicated skill-gated protocol
  handlers for pending requests, inspection, acknowledgement, immutable local
  audit history, open reversion windows, and explicit reversion.
- `core/protected_replacement.py` - bounded `since`/`until` audit-history query
  with total/returned/truncated metadata and foreign-diagnostic exclusion.
- `core/tools/memory_exchange.py` and `core/tools/__init__.py` - backward-
  compatible factory composition and public tool-factory export.
- `skills/installed/memory-exchange/SKILL.md` and
  `services/heartbeat_agentic.py` - discoverability, complete workflow guidance,
  and an explicit statement that operator override is unavailable to the agent.
- `tests/core/test_hmx_tools.py` and
  `tests/services/test_heartbeat_agentic.py` - registry/schema boundaries and a
  full pending -> inspect -> defer -> accept -> audit -> revert -> audit journey.

Important behavior:

- `protected_replacement_list` returns open pending/deferred decisions;
  `protected_replacement_inspect` compares current and proposed state before a
  decision; `protected_replacement_review` preserves all four agent choices.
- `protected_replacement_audit_list` returns only immutable local replacement,
  verification, and reversion history. Imported foreign diagnostics never read
  as local experience. Results default to 100, accept a 1-500 limit, and report
  truncation.
- `protected_reversion_list` shows only still-open earlier-of windows, and
  `protected_replacement_revert` retains the explicit rationale, drift check,
  atomic restore/audit, and one-shot snapshot consumption rules from Slice 11.
- All six tools are chat/heartbeat-only and skill-gated. Read operations are
  marked read-only; acknowledgement and reversion are agent self-decisions and
  cannot run in parallel. No HMX agent tool exposes force, operator signature,
  operator identity, acknowledgement bypass, reason, or evidence arguments.

Validation: 207 scoped HMX/tool/heartbeat tests pass with the existing five
advisory marker warnings in the heartbeat module. Full validation: 2150 tests
pass with the existing 421 advisory marker warnings. Focused compilation,
Black, mypy, wheel inspection, and diff hygiene pass.

### HMX MVP acceptance audit complete

The final line-by-line audit is recorded in `docs/hmx-acceptance.md`. All 24
MVP-Core and 21 MVP-Protected Replacement criteria have code ownership and
executable evidence. The audit closed four discrepancies:

- non-material edits now preserve `imported_and_accepted` while material edits
  become `derived_from_import`, with the complete chain surviving re-export;
- unknown future sections remain forward-compatible but now report that they
  were not applied instead of disappearing silently;
- `hexis_instance_is_empty()` now derives event-specific diagnostics from the
  unified protected audit ledger, via migration `0015` for existing instances;
- a trust-anchor-rejected matching lineage is distinct from ordinary digest
  divergence and cannot enter the normal agent acceptance path.

Acceptance-focused coverage also pins narrative staging, active-target MVP-PR
recovery guidance, subset-scope refusal, complete consent payloads, every agent
acknowledgement choice, and all three protected audit event types.

Validation: the full repository suite passes with 2157 tests and the existing
421 advisory marker warnings. The HMX-focused suite passed before the final
full run, and the full run includes the additional acknowledgement test. Black,
compilation, wheel construction/package inspection, migration-survivor coverage,
and diff hygiene pass. The wheel includes migration `0015`; focused mypy remains
advisory with the existing `jsonschema` stub and broad-union baseline errors in
`core/memory_exchange.py`.

### Phase 3 API and MCP interop complete

Key files:

- `apps/hexis_api.py` - live `GET /v1/models`, strict
  `POST /v1/chat/completions`, buffered responses, and standard streaming
  `chat.completion.chunk` SSE terminated by `[DONE]`;
- `services/agent.py` - caller-provided temperature reaches the canonical agent
  loop while configured temperature remains the default;
- `apps/hexis_mcp_server.py` - composable live tool listing and protocol-level
  dispatch with duplicate suppression, pre-list refresh, MCP execution context,
  and `isError` results;
- `tests/web/test_openai_compat.py` and
  `tests/core/test_hexis_mcp_server.py` - official OpenAI Python client journeys
  plus MCP listing, legacy dispatch, registry dispatch, and failure coverage;
- `docs/reference/api.md` and `docs/guides/mcp-integration.md` - supported
  controls, compatibility limits, examples, and MCP behavior.

Important behavior:

- Model discovery derives from live `llm.chat` configuration without resolving
  or consuming provider credentials. Completion requests must use that model.
- OpenAI messages pass through the same memory hydration, skills, internal
  tools, gateway audit, and conversation-memory journey as `/api/chat`.
- System/developer/user/assistant text history, `temperature`, and one max-token
  control are supported. Unsupported controls, non-text parts, external tool
  histories, and usage requests fail explicitly rather than being ignored.
- Per-completion token usage is omitted honestly: one Hexis completion may span
  several provider/tool iterations and current usage records are not correlated
  tightly enough to report one exact aggregate.
- MCP discovery combines legacy cognitive-memory tools with enabled registry
  tools allowed in MCP context. Legacy names win collisions; registry execution
  still passes through central policy.

Validation: official OpenAI-client buffered and streaming journeys and MCP
contract tests pass; the focused API/agent regression set passes 98 tests with
13 existing warnings. Full validation passes 2171 tests with the existing 421
advisory marker warnings. Compilation and diff hygiene pass.

### Phase 2 extensibility hardening complete

Key files:

- `plugins/base.py` - strict manifest identifiers, names, semantic versions,
  object-root configuration schemas, and JSON Schema meta-validation;
- `plugins/loader.py` - pre-import `plugin.json` validation, runtime-manifest
  matching, live `plugin.<id>` configuration validation, and isolated plugin
  failure with actionable logging;
- `skills/base.py` and `core/tools/skills.py` - parsed structured provenance,
  a fixed agent-authored root, ownership checks before updates, legacy-footer
  migration, and symlink refusal;
- `tests/core/test_plugin_validation.py` and
  `tests/core/test_skills_marketplace.py` - invalid manifest/config preflight,
  import ordering, ownership, no-overwrite, legacy migration, and path-boundary
  contracts.

Important behavior:

- A present `plugin.json` is validated before plugin code imports and must
  exactly match the runtime `PluginManifest`. Runtime-only manifests remain
  supported and are validated before registration.
- Live configuration must be a JSON object and satisfy the manifest schema.
  Invalid configuration never silently becomes `{}` and the plugin registers
  no partial capabilities; other plugins continue loading.
- Configuration errors identify paths and constraints without logging rejected
  values, which may contain secrets.
- `author_skill` creates only under `~/.hexis/skills/agent-authored/` and writes
  `provenance.authored_by`, `managed_by`, `created_at`, and `updated_at`.
- Updates require structured Hexis ownership. The exact historical
  `author_skill` footer is accepted only as a compatibility signal and is
  upgraded to structured provenance during that approved update. Unmarked user
  files and symlinked targets are left unchanged with an actionable error.

Validation: the focused plugin/skill/tool/agent/heartbeat regression set passes
235 tests with 104 existing marker warnings. Full validation passes 2185 tests
with the existing 421 advisory marker warnings. Compilation and diff hygiene
pass; focused mypy still expands into the repository's existing advisory
baseline.

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

Status: complete.

Completed:

- Live agent uses skills as the primary capability abstraction (`f14ed93`).
- Tool descriptions are not duplicated in the prompt; schemas ride the tool API.
- Skill discovery is explicit and cheap: a compact always-present index plus
  `list_skills`/`use_skill` on-demand detail.
- Plugin-provided skill dirs load into selection, discovery, and activation.
- Plugin manifests, optional `plugin.json`, and live configuration schemas are
  validated before capability registration.
- Hexis authors skills only under `~/.hexis/skills/agent-authored/`, records
  structured ownership provenance, and proves that ownership before updates.

### Phase 3 - Interop and reach

Status: core goals complete.

Goals:

- Add an OpenAI-compatible API surface:
  - `GET /v1/models`
  - `POST /v1/chat/completions`
  - streaming chat completion chunks
- Add MCP server tests for tool listing and dispatch.
- Optional follow-up: add streamable HTTP MCP transport if a concrete client
  requires it; stdio listing and dispatch are now tested.

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

HMX, Phase 2 extensibility, and the core Phase 3 interop work are complete. Preserve
`docs/hmx-acceptance.md` evidence when changing exchange or protected-state
behavior, and preserve the official-client journeys when changing API framing.

Next highest-leverage options, in rough priority order:

1. Phase 4 "it learns": FTS cross-session search + a background
   self-improvement worker that authors skills from recent experience (the
   structured `author_skill` provenance contract now exists to build on).
2. Optional interop extension: streamable HTTP MCP transport, driven by a
   specific client requirement rather than added speculatively.
3. Deferred Phase 1 hardening: formatting/type-check cleanup, action SHA
   pinning, and dependency lockfile policy when the team chooses those costs.
