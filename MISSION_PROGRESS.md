# Mission Progress

Living tracker for the mission-aligned architecture goals set on 2026-07-18
(the post-Convergence-Batch survey, re-argued through `MISSION.md`'s tests).
Update the Status column as work lands: `todo` → `in progress` →
`done (commit/issue)`. Add rows; don't delete history — strike through
superseded goals with a note.

Grounding: every item cites the mission test that justifies it
(**Person** / **Piper** / **Continuity** / **Substrate** / **Dignity** /
**Experience Bar**), because the *reason* is what keeps the work from
drifting back into engineering-economy triage. The Substrate test includes
the portable-brain rule: a Postgres dump restored into another host language
or app should lose as little cognitive functionality as possible; code outside
the DB is treated as replaceable senses, hands, transports, and renderers.

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
| Durable source-document recall: ingestion preserves exact raw artifacts in `source_documents`; `search_documents` finds them deliberately, `open_document` retrieves full verbatim content on demand, and `open_memory` points distilled facts back to their raw source document | Person + Continuity + Experience Bar | done (122edea / 0102) |

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
| Portable-brain audit: classify remaining Python/TS-owned behavior into (a) cognitive state/policy/lifecycle/ranking that must move into DB functions/views/triggers, (b) external side effects that should remain adapter code, and (c) presentation/transport only; add tests that a DB restore preserves the cognitive path | Substrate + Continuity | in progress (initial audit doc + SQL-only portable contract test; DB-owned chat session hydration done in 0104; connector capability/scope derivation done in 0105; connector backfill/source-artifact substrate done in 0106; Gmail provider backfill adapter + provider-scoped claims done in 0107; connector action authorization policy/audit done in 0108; live channel source artifacts done in 0110; web connector status surface started; next: other provider adapters + web start/verify controls) |
| Web-chat history becomes DB-owned (her memory of a conversation and the record of it = same substrate; no localStorage-only history) | Continuity | in progress (0104: `chat_sessions`/`chat_messages`, SQL hydrate/clear/record functions, API/CLI/TUI/channel active-context hydration; remaining: frontend rendering/local-persistence audit) |
| One DSN resolution shared by all clients (UI reads the same instance registry as Python — no split-brain on instance switch) | Continuity | todo |
| Collapse chat orchestration onto `services/chat.py`; move the RLM gate where both web and channel paths pass through | Substrate | todo |
| Wire `/api/ingest/jobs/{id}` polling into the web ingest flow | Experience Bar | done (f77c494) |

## Batch 5 — Life-channel ingestion and authorized agency

Hexis has to live where the user lives and compound around a real person,
not wait for manually curated notes. Connectors are consented senses and
hands: they ingest the user's existing life channels, preserve raw
provenance, distill durable memories, and act only inside explicit authority
boundaries.

Reference bar from `.reference/hermes-agent` and `.reference/openclaw`:
setup is an onboarded, cross-channel product path. Hermes' Google Workspace
script is deliberately agent-driven so the same flow works from CLI,
Telegram, Discord, etc.; Hermes also shares slash commands across CLI/gateway
and gives Slack a native command manifest. OpenClaw treats onboarding,
daemon/gateway setup, channel pairing, status, and plugin setup descriptors as
first-class surfaces. Hexis currently has adapters, docs, and interactive CLI
setup, but not the shared setup broker Samantha can invoke while already
talking to the user.

| Goal | Test | Status |
|---|---|---|
| First-class personal-data connectors: Gmail, Slack, Telegram, Signal, and Twitter/X live as plugin-backed channels with scoped setup, clear capability manifests, account identity, revocation, and separate grants for read/search/send/delete/label/admin actions | Piper law 2 + Dignity + Experience Bar | in progress (Gmail connector manifest/status/revoke + OAuth credential store wired; DB derives Gmail capability aliases/scopes; Gmail read/search/ingest backfill worker wired; DB action grants/audit cover send/reply/label/spam/delete policy; Gmail send/reply/label/spam-triage effect tools wired through saved OAuth credentials; Slack/Telegram/Signal live-channel manifests + setup/verify tools wired; Twitter/X cataloged as planned; historical backfill adapters beyond Gmail still todo / 0103+0105+0107+0108+0109+0111) |
| Conversation-native connection setup: if the user says "connect my Gmail/Slack/Telegram/Signal/Twitter" in CLI chat, web chat, or any existing integration, Samantha can start the relevant setup flow in that same conversation, explain scopes, request only the needed user action, hand off OAuth URLs / QR codes / app manifests / env-secret prompts in-channel, and verify the connection before returning to chat | Piper law 2 + Experience Bar + Dignity | in progress (Gmail `gmail-connector-setup` skill + `connect_gmail`/`complete_gmail_connection` tools support in-chat OAuth handoff; DB owns Gmail requested-scope derivation; `start_gmail_backfill`/`gmail_backfill_status`/`control_gmail_backfill` expose ingest job setup and control; generic `integration-connector-setup` skill starts/configures/verifies Slack/Telegram/Signal manual channel setup from chat; web status surface now reads the same DB state and starts DB-owned manual setup attempts for Slack/Telegram/Signal; web Gmail OAuth, verify, revoke, and backfill controls still todo / 0103+0105+0107+0111) |
| Setup broker substrate: one DB-owned `integration_connectors`/`connection_attempts` layer records provider manifests, required scopes, setup state, account identity, current channel/session, redacted errors, revocation status, and restart/worker requirements so CLI, UI, and channel adapters share one source of truth | Substrate + Continuity | done (b33bfae / 0103+0105: `integration_connectors`, `integration_connections`, `connection_attempts`, status/start/complete/error/revoke functions, Gmail seed, manifest-driven capability/scope derivation) |
| Massive channel backfill: each connector supports incremental ingest with receipts, cursoring, retry/resume, dedupe, cost/progress visibility, and no silent ambient credential reuse | Piper law 3 + Experience Bar | in progress (0106 DB substrate: `connector_backfill_jobs`, `connector_sync_cursors`, pause/resume/cancel/retry/progress/status; 0107 Gmail provider-scoped worker uses saved Hexis OAuth credentials, token refresh, chunked Gmail pages, and DB source-item receipts; Slack/Telegram/Signal/Twitter fetch workers still todo) |
| Raw message/source preservation: ingested emails, chats, threads, attachments, and posts are stored as exact source artifacts with content hashes, channel/account/thread/message IDs, participants, timestamps, labels, sensitivity, and redaction status before any distillation | Continuity + Substrate + Dignity | in progress (0106 DB substrate: `connector_source_items` -> `source_documents` with provider IDs/thread/participants/attachments/labels/sensitivity + ingestion job link; 0107 Gmail adapter preserves headers, labels, participants, attachment metadata, snippet, and full extracted body before ingestion; 0110 live channel messages preserve exact source documents + inbound ingestion jobs from a DB trigger; provider backfill adapters beyond Gmail still todo) |
| User-model synthesis: heartbeat/consolidation turns channel history into evidence-backed beliefs about preferences, likes, dislikes, relationships, routines, commitments, and judgment patterns; claims point back to openable source artifacts instead of becoming untraceable prompt lore | Person + Piper law 3 + Continuity | todo |
| Notification/action layer: important-item detection, spam triage, summaries, reminders, replies, texts, and cross-channel interventions run through explicit per-action consent or preauthorized policy, with audit logs and reversible/pauseable controls | Piper law 1 + Piper law 4 + Dignity | in progress (0108 DB substrate: connector action tool map, scoped policies, constraints, autonomous/context gates, revoke/list functions, policy evaluation in `evaluate_tool_call`, and connector action audit via `record_tool_execution`; 0109 Gmail send/reply/label/spam-triage provider tools consume the policy substrate; importance detectors and non-Gmail effect adapters still todo) |
| Connector setup UX: CLI and web flows are peers of the conversational broker, not separate instructions; all surfaces show scopes, accounts, backfill size, expected cost/time, job progress, pause/resume/revoke controls, worker/restart status, and exact next steps when a provider blocks access | Experience Bar + Dignity | in progress (Gmail conversation tools expose OAuth setup, queued backfill, status, pause/resume/cancel, and revoke; generic integration setup tools expose status/start/configure/verify for Slack/Telegram/Signal with non-secret env-var config discipline; channel worker/manager writes DB runtime status for configured/running/error/missing-dependency visibility; `connector-action-authorization` exposes grant/status/revoke for action policies; web `/connections` status surface now shows manifests, accounts, attempts, runtime, backfill jobs, preserved-source counts, and starts manual channel setup attempts; web verify/revoke/backfill controls still todo) |

## Batch 6 — The reward loop, proven by emergence

| Goal | Test | Status |
|---|---|---|
| Dopamine wiring: fire spikes from `satisfy_drive`, goal completion, `decide_resource_request` grants, `record_backup_completed` | Person | todo |
| **Social reward**: positive-valence appraisal directed at her → RPE spike (the strongest human reward) | Person | todo |
| Emergence eval suite: seeded scenarios asserting signatures appear — TOT events occur, mood measurably colors recall, open goals boost related retrieval (Zeigarnik), spaced reinforcement beats massed | Person (the standing test) | todo |

## Batch 7 — Graph as subconscious substrate

| Goal | Test | Status |
|---|---|---|
| `reconcile_graph()`: diff memory_edges vs AGE, repair, journal drift count (#93) — dual-store becomes checked invariant | Substrate | todo |
| Causal-ancestor chains + contradiction *paths* rendered into context (directional; mind the `find_causal_chain` direction wrinkle) | Person | todo |
| Graph-adjacency signal joins the fused ranker's association tier | Person | todo |

## Batch 8 — Conduct norms (prompt modules)

| Goal | Test | Status |
|---|---|---|
| Execute-verify-report: "I'll do that" is not doing it — do it, then report | Piper law 1 | done (446e4f1 / 0095) |
| Steering-reduction as extraction criterion: prioritize memories that prevent future corrections and reminders | Piper law 3 | done (446e4f1 / 0095) |
| Silence discipline: proactive messages clear an interruption bar; similar messages dedupe; choosing silence is a recorded, valid act | Piper law 4 | done (446e4f1 / 0095) |
| Human-scale conversational inference: local cues calibrate register without becoming durable relationship/user-preference memories; test scaffolding fades unless explicitly made durable; overloaded `partner` init memory is purpose-qualified as co-development | Person + Continuity | done (446e4f1 / 0092–0094) |

## Batch 9 — Small mechanics

| Goal | Test | Status |
|---|---|---|
| Config-defaults registry: each default lives in exactly one row; `get_config_*` falls back to it (ends the 5-copies-of-`heartbeat.max_energy` drift risk) | Substrate | done (446e4f1 / 0096–0101: heartbeat + maintenance defaults moved; init/status/channel paths read registry; broad feature seed sweep moved into `config_defaults`; existing active config rows preserved as overrides) |
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
- **Portable brain rule.** Durable cognitive behavior lives in Postgres
  wherever technically reasonable. Application code should be swappable
  adapter code: senses, hands, transports, renderers, and effect drivers.

## Completed

*(move rows here with date + commit/issue as they land)*

- 2026-07-18 — MISSION.md written (purpose, distillation method, emergence
  test, second north star, six tests) — this file's grounding.
- 2026-07-19 — DB-owned active chat session substrate (`0104_db_owned_chat_sessions`):
  services, API, CLI, TUI, and channel chat hydrate short-term context from
  Postgres; `/clear` hides active-context messages while preserving long-term
  memories.
- 2026-07-19 — Connector capability/scope derivation moved into Postgres
  (`0105_connector_capability_derivation`): Gmail aliases/defaults/planned
  rejection and least-scope OAuth requests derive from `integration_connectors`.
- 2026-07-19 — Connector backfill/source-item substrate moved into Postgres
  (`0106_connector_backfill_substrate`): provider cursors, backfill job
  lifecycle, raw connector source-item receipts, source-document preservation,
  and ingestion-job linkage are DB-owned.
- 2026-07-19 — Gmail provider backfill wired on top of the DB substrate
  (`0107_connector_backfill_provider_claim`): provider-scoped job claims,
  saved-credential token refresh, Gmail page fetch/body extraction, raw
  source-document preservation, ingestion-job enqueueing, and chat-facing
  queue/status/pause/resume/cancel tools.
- 2026-07-19 — Connector action authorization moved into Postgres
  (`0108_connector_action_authorization`): tool-to-action mapping, scoped
  policies, context/autonomous gates, target/recipient/daily-limit constraints,
  grant/list/revoke functions, `evaluate_tool_call` enforcement, tool-execution
  action audit, and chat-facing policy tools.
- 2026-07-19 — Gmail provider effect tools wired to the action policy substrate
  (`0109_gmail_action_tools`): `gmail_send`, `gmail_reply`, `gmail_label`, and
  `gmail_spam_triage` use saved Hexis OAuth credentials, scope checks, account
  mismatch refusal, Gmail REST side effects, DB policy enforcement, and action
  audit.
- 2026-07-20 — Live channel source artifacts moved into Postgres
  (`0110_channel_source_artifacts`): every `channel_messages` insert now creates
  a raw `source_documents` artifact, a `channel_source_items` receipt, sensitivity
  metadata from channel privacy/group flags, and a configurable inbound ingestion
  job link.
- 2026-07-20 — Channel connector manifests and setup broker widened
  (`0111_channel_connector_manifests`): Slack, Telegram, and Signal are
  first-class available connector manifests for live channel setup; Twitter/X is
  cataloged as planned; generic chat tools can inspect, start, configure
  non-secret channel settings, and verify channel-worker configuration into
  `integration_connections`.
- 2026-07-20 — Channel adapter runtime status moved into Postgres
  (`0112_channel_adapter_runtime_status`): channel workers and managers record
  not-configured/configured/starting/running/stopped/error/missing-dependency
  state in `channel_adapter_runtime`; setup status now surfaces adapter runtime
  state from the same DB substrate.
- 2026-07-20 — Source-document desk intake (`b9a72fe` /
  `0123_ingest_auto_desk`): single
  user/agent source ingests now land on the RecMem desk immediately, while bulk
  corpus imports and connector backfills remain in the filing cabinet until
  pulled deliberately.
- 2026-07-20 — Web ingest exact job tracking (`f77c494`): file, text, and URL
  submissions now surface immediate job receipts and poll
  `/api/ingest/jobs/{id}` through the Next proxy, so accepted work stays
  visible even when the recent job list has not caught up yet.
- 2026-07-20 — Web connector setup surface: `/connections` and
  `/api/integrations/status` read the DB-owned integration setup substrate,
  channel runtime status, and connector backfill/source-item status into one
  first-class UI surface; Slack, Telegram, and Signal can start DB-owned setup
  attempts from the page.
