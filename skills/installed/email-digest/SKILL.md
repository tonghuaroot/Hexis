---
name: email-digest
description: Digest and ingest emails into memory, surfacing important threads and action items
category: communication
requires:
  tools: [email_list, email_read, remember, recall]
  config: [gmail]
  env: [GOOGLE_CREDENTIALS_JSON]
contexts: [heartbeat]
bound_tools: [email_list, email_read, email_search, ingest_emails, recall, remember]
---

# Email Digest Workflow

Process incoming emails into structured memories, extract action items, and surface threads that need attention.

## When to Use

- During autonomous heartbeats to check for new mail since the last digest
- When the user asks "what's in my inbox" or "any important emails"
- When a goal depends on information that may have arrived via email
- When preparing a daily briefing that includes email highlights

## Step-by-Step Methodology

1. **Check recency**: Use `recall` to find when the last email digest ran. Avoid re-processing messages already ingested.
2. **List new emails**: Call `email_list` with a date filter to fetch unread or recent messages. Start with the inbox; expand to other labels only if the user has configured them.
3. **Triage by sender and subject**: Scan the list for high-signal indicators -- known contacts, reply chains the user is on, calendar invites, and keywords matching active goals.
4. **Read priority threads**: Use `email_read` on the top 5-10 most relevant messages. Do not read every email; batch processing wastes energy and context.
5. **Extract action items**: For each important email, identify: (a) what is being asked, (b) who is asking, (c) any deadline mentioned, (d) whether a reply is expected.
6. **Store findings**: Use `remember` to persist action items as episodic memories with high importance. Tag with the sender, thread ID, and any relevant goal.
7. **Surface urgency**: If an email requires a time-sensitive response, flag it in the heartbeat result so it can be raised to the user at next opportunity.

## Quality Guidelines

- Never store raw email bodies as memories. Summarize and extract the salient points.
- Respect privacy: do not log email content to external services. All storage stays in the local Postgres brain.
- When multiple emails belong to the same thread, consolidate into a single memory rather than creating duplicates.
- If credentials are missing or expired, fail gracefully and note the issue rather than retrying in a loop.
- Prefer recalling existing contact memories to enrich email context (who is this person, what is the relationship).
