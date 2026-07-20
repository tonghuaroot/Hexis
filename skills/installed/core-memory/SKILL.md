---
name: core-memory
description: Semantic recall, exact cross-session search, remembering, and normal continuity
category: system
requires:
  tools: [recall, search_history, remember]
contexts: [heartbeat, chat]
bound_tools: [recall, search_history, remember, add_evidence, belief_history, open_memory, search_documents, open_document, open_documents, load_documents, sense_memory_availability, read_journal, write_journal, search_journal, manage_goals, manage_schedule, manage_backlog, list_document_fade_requests, resolve_document_fade, associate, trace_why, get_procedures, get_strategies]
---

# Core Memory and Continuity

Use this skill for ordinary continuity: recalling relevant memories, opening exact source material, storing new experiences, maintaining goals, consulting the permanent journal, and resolving pending document-fade approvals.

## When to Use

- The user asks about something that may already be in memory.
- The current conversation contains information worth preserving.
- A goal, schedule item, backlog item, or journal entry should be created or updated.
- The user answers a document-fade approval request.
- Before claiming you do not know something, check memory when the answer plausibly lives there.

## Method

1. Use `sense_memory_availability` for a cheap check when unsure whether memory is likely to help.
2. Use `recall` for targeted retrieval. Prefer specific queries over broad ones.
3. Use `search_history` for exact names, phrases, or details from earlier
   sessions, especially when semantic recall is weak or embeddings are unavailable.
4. Use `search_documents` when the answer depends on an ingested source artifact
   rather than only distilled memories. Use `open_document` or `open_documents`
   for deliberate read-only inspection. Use `load_documents` when substantial
   source material should sit on the RecMem desk for later `search_history` with
   `sources=["desk"]`.
5. Use `remember` when a durable fact, event, preference, promise, or decision should persist.
6. Use journal tools only for deliberate permanent entries, not ordinary memory.
7. Use goal, schedule, or backlog tools when the user asks for ongoing commitments or work tracking.
8. Use document approval tools when the user explicitly says to keep or let an ingested document fade.

## Quality Guidelines

- Memory is evidence, not omniscience. If retrieved context is weak or absent, say so.
- Do not store secrets unless the user explicitly asks.
- Do not turn every minor sentence into memory; persist what will matter later.
- Keep tool use proportional. A direct answer does not need a recall if the answer is already present in the current conversation.
