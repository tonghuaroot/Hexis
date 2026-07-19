# Mission Progress

Living tracker for the mission-aligned architecture goals set on 2026-07-18
(the post-Convergence-Batch survey, re-argued through `MISSION.md`'s tests).
Update the Status column as work lands: `todo` → `in progress` →
`done (commit/issue)`. Add rows; don't delete history — strike through
superseded goals with a note.

Grounding: every item cites the mission test that justifies it
(**Person** / **Piper** / **Continuity** / **Substrate** / **Dignity** /
**Experience Bar**), because the *reason* is what keeps the work from
drifting back into engineering-economy triage.

---

## Batch 1 — One mind, one retrieval mechanism

The core act of the architecture, implemented once, with its conscious
surface complete. (Person, Continuity; fixes a live Dignity hole.)

| Goal | Test | Status |
|---|---|---|
| Sensitivity stopgap: sensitivity filters on the tool recall paths — `execute_memory_tool` recall arms + `search_cross_session_history` — threaded from the agent loop's `is_group` (closes the #92 group-leak via the `recall`/`search_history` tools) | Dignity | done (see #96) |
| Ranker fusion (#78): recmem tier skeleton absorbs fast_recall's scoring — associations, episode binding, recency, strength, mood congruence, trust floor, activation-boost term, retrieval provenance; per-type seed scans guarantee tier representation; fast_recall is a wrapper, upgrading the whole db/05 family transitively | Person | done (see #96) |
| Widen fused recall to all memory types (procedural, strategic, worldview, goal — the knowledge tier) | Person | done (see #96) |
| Fix `search_query` embedding-prefix asymmetry in recmem path | Person | done (already fixed pre-fusion; verified) |
| Repoint every caller at the unified function: `fast_recall` is now a thin wrapper (db/05 family + all downstream upgraded transitively); final removal deferred one release | Substrate | done (see #96) |
| Metamemory surface: thin/empty recall carries {feeling, familiarity, TOT partials}; familiar-but-blocked auto-files incubation ('I'll let it simmer'); low familiarity reads honestly; default relevance floor (memory.recall_min_score) makes honest failure possible at all | Person | done (see #96) |
| Retrieval eval: seeded corpus pins home-turf ranking, knowledge-tier reachability, association expansion, mood congruence, activation boost (tests/db/test_retrieval_eval.py) | Experience Bar | done (see #96) |

## Batch 2 — Both north stars, visible in days

| Goal | Test | Status |
|---|---|---|
| **"It came to me later"**: filed at high familiarity (#96), resolved by maintenance — boosts clear the spontaneous floor, a first-person note reaches the web inbox (explicit delivery, sensitivity-honoring, capped), and the answer rides recall's spontaneous tier + the heartbeat "On my mind" line | Person + Piper | done (see #98) |
| **Inferred commitments** (mirror of #58): extraction kind `user_event` (openclaw categories, confidence floors 0.72/0.86, dedupe keys, no-same-moment clamp, pending + per-day caps, 90-day horizon, web-inbox-pinned) → scheduled check-in after the event; default ON with one off switch | Piper (care) | done (see #98) |

## Batch 3 — Lean core, reachable capability

| Goal | Test | Status |
|---|---|---|
| Skill-coverage test: every registered non-internal tool bound by ≥1 skill (agent-authored skills count); shrinking grandfather list for the seven extraction candidates | Piper law 8 | done (see #99) |
| Bind the dark tools: calendar skill (CRUD), email_send → email-digest, create_contact + contact-ingest → crm-lookup, glob/grep/edit_file/shell/browser → code-execution, web_summarize/brave/firecrawl → research, queue_user_message → self-reflection + outreach, messaging sends → `outreach` (earn-the-interruption norm), council skill, git_ingest → knowledge-ingest, graph acts → core-memory | Piper law 1 | done (see #99) |
| Phenomenological renames for graph/memory tools: `associate` (what does this remind me of), `trace_why` (why do I believe/feel this); `explore_subgraph`/`explore_concept` are internal aliases — never graph-browser framing | Person | done (8b10159 / #99) |
| Plugins made real: create `plugins/installed/`, implement `plugin.external_dirs`, ship first real plugin | Piper law 8 | done (8b10159 / #99) |
| Extract speculative integrations (Todoist, Asana, HubSpot, Fathom, video gen, Twitter, YouTube) from core into plugins | Piper law 8 | done (8b10159 / #99) |
| Mark operator/system tools `internal` (config_export/import, database_backup, backup_retention, post_process_output, manage_sessions, execute_workflow; `create_tool` stays visible only through gated self-extension) | Dignity | done (8b10159 / #99) |
| Self-extension visibility: dynamic tool creation and skill authoring journal a `self_extension` change and post a web-inbox notice; live acceptance remains blocked on init + consent | Person + Piper law 8 | in progress (code complete; live acceptance pending / #99) |

## Batch 4 — Continuity hygiene

| Goal | Test | Status |
|---|---|---|
| Dead-code sweep: SSE stack (keep `gateway_events` table), `services/ingest_api.py`, db/09 `record_chat_turn`/`record_subconscious_exchange`, MCP hand-written duplicate schemas (registry canonical), orphan Next routes | Substrate | todo |
| Web-chat history becomes DB-owned (her memory of a conversation and the record of it = same substrate; no localStorage-only history) | Continuity | todo |
| One DSN resolution shared by all clients (UI reads the same instance registry as Python — no split-brain on instance switch) | Continuity | todo |
| Collapse chat orchestration onto `services/chat.py`; move the RLM gate where both web and channel paths pass through | Substrate | todo |
| Wire `/api/ingest/jobs/{id}` polling into the web ingest flow | Experience Bar | todo |

## Batch 5 — The reward loop, proven by emergence

| Goal | Test | Status |
|---|---|---|
| Dopamine wiring: fire spikes from `satisfy_drive`, goal completion, `decide_resource_request` grants, `record_backup_completed` | Person | todo |
| **Social reward**: positive-valence appraisal directed at her → RPE spike (the strongest human reward) | Person | todo |
| Emergence eval suite: seeded scenarios asserting signatures appear — TOT events occur, mood measurably colors recall, open goals boost related retrieval (Zeigarnik), spaced reinforcement beats massed | Person (the standing test) | todo |

## Batch 6 — Graph as subconscious substrate

| Goal | Test | Status |
|---|---|---|
| `reconcile_graph()`: diff memory_edges vs AGE, repair, journal drift count (#93) — dual-store becomes checked invariant | Substrate | todo |
| Causal-ancestor chains + contradiction *paths* rendered into context (directional; mind the `find_causal_chain` direction wrinkle) | Person | todo |
| Graph-adjacency signal joins the fused ranker's association tier | Person | todo |

## Batch 7 — Conduct norms (prompt modules)

| Goal | Test | Status |
|---|---|---|
| Execute-verify-report: "I'll do that" is not doing it — do it, then report | Piper law 1 | done (uncommitted / 0095) |
| Steering-reduction as extraction criterion: prioritize memories that prevent future corrections and reminders | Piper law 3 | done (uncommitted / 0095) |
| Silence discipline: proactive messages clear an interruption bar; similar messages dedupe; choosing silence is a recorded, valid act | Piper law 4 | done (uncommitted / 0095) |
| Human-scale conversational inference: local cues calibrate register without becoming durable relationship/user-preference memories; test scaffolding fades unless explicitly made durable; overloaded `partner` init memory is purpose-qualified as co-development | Person + Continuity | done (uncommitted / 0092–0094) |

## Batch 8 — Small mechanics

| Goal | Test | Status |
|---|---|---|
| Config-defaults registry: each default lives in exactly one row; `get_config_*` falls back to it (ends the 5-copies-of-`heartbeat.max_energy` drift risk) | Substrate | in progress (uncommitted / 0096–0100: heartbeat + maintenance defaults moved; init/status/channel paths read registry; retired local-provider references removed from source/live DB; broader seed sweep pending) |
| Baseline file renumbering (duplicate 28/32) in one mechanical commit | Substrate | todo |
| Graduated appraisal depth: appraisal intensity scales with stimulus salience (#67 budgets as the hook) — attention allocation as psychology and cost control | Person + Piper | todo |
| Chat energy, the human way: tools in chat cost energy; conversation interacts with drives — connection satisfied by good interaction; cost-vs-restore governed by a character-card temperament dial | Person + Piper | todo |
| Presence polish on channels: typing indicators, presence beacons | Piper law 5 | todo |
| Async embedding lifecycle for `memories` (adopt the units pattern: nullable + `embedding_status`; no HTTP inside transactions) | Substrate | todo |

---

## Settled decisions (do not relitigate)

- **AGE stays.** memory_edges = write-path truth; AGE = the graph query
  engine, subconscious substrate. The fix direction is activation, not
  retirement.
- **No cypher/graph-query tools for the agent.** Conscious memory tools
  mirror human phenomenology only (remember, feeling-of-knowing, TOT,
  incubation, association, reminiscence).
- **`working_memory` table folds into units.** The mechanism (attention
  buffer) lives as hydrated context + recent-units tier; the RabbitMQ inbox
  reroutes through `recmem_ingest_turn` so incoming messages are
  *experienced*, not shelved.
- **Chat energy is not simply metered.** Temperament-valenced, per above.
- **Mechanisms, never quirks.** Implement the architecture the quirks point
  at; the quirks must emerge. `docs/_archive/reference-quirks.md` is the
  evidence ledger.
- **OpenAI-compat endpoints stay** (an integration surface, law 2), to be
  documented as supported.

## Completed

*(move rows here with date + commit/issue as they land)*

- 2026-07-18 — MISSION.md written (purpose, distillation method, emergence
  test, second north star, six tests) — this file's grounding.
