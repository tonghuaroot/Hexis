---
name: gmail-connector-setup
description: Connect Gmail through explicit OAuth setup, inspect status, complete authorization, queue read-only backfill, control jobs, and revoke local access
category: communication
requires:
  tools: [gmail_setup_status, connect_gmail, complete_gmail_connection, start_gmail_backfill, gmail_backfill_status, control_gmail_backfill, revoke_gmail_connection]
contexts: [chat]
bound_tools: [gmail_setup_status, connect_gmail, complete_gmail_connection, start_gmail_backfill, gmail_backfill_status, control_gmail_backfill, revoke_gmail_connection]
---

# Gmail Connector Setup

Use this when the user asks to connect Gmail, inspect Gmail setup, authorize email ingestion, filter spam, label messages, send replies, or disconnect Gmail.

## Principles

- Treat connection setup as a first-class conversation flow. Do not tell the user to leave chat and figure it out alone.
- Explain requested capabilities before starting OAuth. Default to read/search/ingest only.
- Add `label` or `spam_triage` only when the user explicitly wants labeling or spam filtering.
- Add `send` or `reply` only when the user explicitly authorizes sending or replying on their behalf.
- Queue `start_gmail_backfill` only after Gmail is connected and the user has asked Hexis to ingest/learn from email history.
- Treat backfill as read-only provider access but local memory ingestion: it still requires explicit user approval.
- For ongoing send/reply/label/spam-triage behavior, use `connector-action-authorization` after connection setup so the grant is scoped and DB-audited.
- Prefer `client_secret_path` over pasted OAuth client JSON because tool calls are audited.
- Never ask for Google account passwords.

## Flow

1. Call `gmail_setup_status`.
2. If Gmail is not connected, call `connect_gmail` with the least capabilities that match the user's request.
3. If the tool says a client secret is needed, ask for the local path to the Google OAuth Desktop client JSON. If the path starts with `/`, ask the user to send it in a sentence.
4. Send the returned `authorization_url` exactly. Tell the user the localhost page may fail after approval and that this is expected.
5. When the user pastes the redirected URL or code, call `complete_gmail_connection`.
6. Report the connected account and granted capabilities.
7. If the user asked to ingest email history, call `start_gmail_backfill` with the smallest useful `query`, `label_ids`, and `max_messages` for the request.
8. Use `gmail_backfill_status` for progress, and `control_gmail_backfill` only when the user asks to pause, resume, or cancel a job.

Use `revoke_gmail_connection` only after the user asks to disconnect Gmail.
