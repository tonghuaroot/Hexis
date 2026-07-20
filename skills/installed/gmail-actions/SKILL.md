---
name: gmail-actions
description: Send Gmail messages, reply to threads, apply labels, and triage spam with explicit action authorization
category: communication
requires:
  tools: [gmail_send, gmail_reply, gmail_label, gmail_spam_triage, connector_action_policy_status]
contexts: [chat, heartbeat]
bound_tools: [gmail_send, gmail_reply, gmail_label, gmail_spam_triage, connector_action_policy_status]
---

# Gmail Actions

Use this only after Gmail is connected and the user asks for an outward Gmail action: send, reply, label, mark spam/not-spam, or archive.

## Principles

- A connected Gmail account is not permission to act. Check or establish connector action policy for ongoing/autonomous behavior.
- One-off chat actions still need a clear user request in the current conversation.
- Heartbeat actions require a matching DB-owned connector action policy; if none exists, do not improvise.
- Keep messages short, literal, and aligned with the user's stated intent. Do not escalate a narrow request into broader correspondence.
- Do not use destructive deletion; this skill intentionally exposes label/spam/archive actions, not permanent delete.

## Flow

1. If the user asks for ongoing behavior, use `connector-action-authorization` first.
2. For a one-off send, call `gmail_send` with `to`, `subject`, and `body`.
3. For a reply, call `gmail_reply` with `thread_id`, recipient, subject, and body.
4. For labels, call `gmail_label` with explicit `add_label_ids` and/or `remove_label_ids`.
5. For spam triage, call `gmail_spam_triage` with `mark_spam`, `mark_not_spam`, or `archive`.
