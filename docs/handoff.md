# Hexis Handoff

Last updated: 2026-07-18 (mission Batch 3 nearly complete — laptop switch mid-batch)

## Where we are

The mission workstream is live: **MISSION.md** (two north stars: personhood +
usefulness; six decision tests) and **MISSION_PROGRESS.md** (the committed
tracker) drive an 8-batch implementation plan. The full execution plan —
including per-batch exploration facts, risk-review fixes, and reference-project
guidance — is committed at **`docs/plans/mission-implementation-plan.md`**
(copied from local plan storage so it survives machine switches).

- **Batch 1 — one ranker (#96): DONE.** Unified `recmem_recall_context` with
  knowledge + spontaneous tiers, `fast_recall` reduced to a flattening wrapper,
  sensitivity stopgap, metamemory envelope (familiarity / tip-of-tongue /
  "I'm not sure I ever knew this"), relevance floor `memory.recall_min_score`,
  retrieval eval. Migrations 0080–0082. Commits c1a3442, f8dba7a, ab8bcf7.
- **Batch 2 — incubation + inferred care (#98): DONE.** Background searches
  resolve → activation boost + "it came back to me" web-inbox message;
  extraction kind `user_event` → scheduled check-ins with confidence floors,
  dedupe, caps, horizon clamps. Migrations 0083–0084. Commits e3f0785, f7ea2f3.
- **Batch 3 — lean core, reachable capability (#99): IN PROGRESS**, most of it
  in the working tree at this handoff (committed as the WIP commit alongside
  this file). Details below.
- **Batches 4–8: not started.** See the plan + MISSION_PROGRESS.md.

## Batch 3 state (issue #99)

Done and committed (487b0a4 + the WIP commit accompanying this handoff):

1. **Coverage gate**: `ToolSpec.internal` flag; `tests/core/test_tool_coverage.py`
   fails the build for any registered non-internal tool bound by no skill.
   `GRANDFATHERED_UNBOUND` is now the empty set. Agent-authored skills
   (`~/.hexis/skills/agent-authored/`) count as bindings.
2. **Bind wave**: calendar, outreach, council skills created; email-digest,
   crm-lookup, code-execution, research, self-reflection, core-memory,
   knowledge-ingest, skill-authoring extended. skill-authoring teaches the
   two-step growth loop (create_tool → bind in a skill).
3. **Phenomenological renames**: `explore_subgraph` → `associate` (old name is
   an internal alias), `explore_concept` internal, new `trace_why` wrapping
   `find_causal_chain`. DB dispatch names unchanged.
4. **Plugins made real**: all seven speculative integrations extracted to
   `plugins/installed/{todoist,asana,hubspot,fathom,video_gen,twitter,youtube}/`
   (tools.py + plugin.json manifest with a **tool-ownership contract** +
   `__init__.py` Plugin class + bundled skill). Loader reads
   `plugin.external_dirs` config; ownership mismatch skips loudly; new
   `include_bundled=False` kwarg on `discover_plugins`/`load_plugins` isolates
   synthetic-plugin tests from the now-populated bundled dir. Registry is down
   to 106 core tools; live check shows `plugins: 7 | tools: 14`. Integration
   tests moved to `tests/plugins/` (174 pass with coverage/validation suites).
5. **Self-extension visibility (partial)**: migration **0085** (applied to the
   dev DB) + baseline `db/32_tables_runtime.sql` widen the `change_journal.kind`
   CHECK to include `'self_extension'`. **The Python wiring is NOT done yet** —
   see next steps.

### Batch 3 — remaining work (do in order)

1. **Self-extension journaling + notice**: in `core/tools/dynamic.py`
   (CreateToolHandler) and `core/tools/skills.py` (author_skill path), after a
   successful create/update: call `record_change('self_extension', <summary>,
   ...)` (`db/71_functions_change_journal.sql`) and post a web-inbox notice via
   `queue_outbox_message(<first-person text>, 'self_extension' intent, ...,
   '{"mode":"web_inbox"}'::jsonb)` — note queue_outbox_message now takes a 4th
   `p_delivery` param (migration 0083). Advisory (try/except-log), never blocks
   the authoring itself. Add a db or core test for the journal row.
2. **CONTRIBUTING.md**: add pi's omissions-with-redirects section and the
   adapted hermes footprint ladder (extend existing → skill → gated tool →
   plugin → MCP → new core tool last) — "core is the mind; capability lives at
   the edges"; every "not in core" names its sanctioned path.
3. **Full suite green** (`pytest tests -q`; expect ~2455 passing), rebuild the
   stack (`hexis upgrade`), then the **live self-extension acceptance**: ask dev
   Samantha to build a small tool via `create_tool` (needs config
   `tools.allow_dynamic=true`), bind it via `author_skill`, use it in the same
   conversation; verify the change-journal row + web-inbox notice; verify it
   survives a worker restart.
4. **MISSION_PROGRESS.md**: flip the Batch 3 rows (coverage gate, bind wave,
   renames, plugins, self-extension) to done with SHAs.
5. Close **#99** with the SHAs; then proceed to Batch 4 (issue-per-batch
   pattern — file the Batch 4 issue at start).

## Gotchas worth carrying (hard-won this batch)

- **Defaulted-param overloads**: adding a defaulted param via CREATE OR REPLACE
  creates a second ambiguous signature — always `DROP FUNCTION` the old
  signature first in the migration.
- **Migration edits during dev**: regenerate a migration wholesale from the
  baselines rather than string-patching it repeatedly; force re-apply an
  unshipped migration with `DELETE FROM schema_migrations WHERE version LIKE
  'NNNN%'` then `hexis migrate`.
- **Test fixtures for recall**: seed embeddings must embed the
  `ensure_embedding_prefix(text,'search_query')` form (the CI stub axis depends
  on the exact string); 'once' schedules require a `run_at` key; fixture DBs
  are unconfigured fresh baselines (seed configs in-test).
- **Plugin tests**: use `include_bundled=False` for synthetic tmp-dir plugins.
- **Next migration number: 0086.**

## Environment notes for the new laptop

- Dev DB state is local Docker (migrations 0080–0085 applied here; a fresh
  clone just needs `docker compose up -d && hexis migrate`). Dev Samantha is
  disposable — Eric plans a wipe now that Batch 1's unified ranker landed, so
  the fresh brain refills under one ranker.
- **`.reference/` is gitignored and does NOT transfer** — it holds the
  sovereign-oss/pro specs and the pi/hermes-agent/openclaw reference checkouts.
  Copy it manually if needed (the plan already folds in the reference guidance,
  so Batches 4–8 don't strictly require it).
- HMX workstream status (previous handoff content) is durably recorded in
  `docs/hmx-acceptance.md` and `plans/hmx.md`.
