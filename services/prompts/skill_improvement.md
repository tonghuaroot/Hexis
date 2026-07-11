# Skill Improvement Review

Review the supplied recent experience for one repeated, proven operational workflow that would make future behavior clearer and more consistent.

Return exactly one JSON object with a `proposal` field. Set `proposal` to `null` when the evidence does not support a durable skill. Never force a proposal.

When proposing, use this shape:

```json
{
  "proposal": {
    "name": "lowercase-kebab-name",
    "description": "One concise sentence describing when to use it",
    "content": "Substantive Markdown instructions covering when, method, verification, and pitfalls",
    "category": "other",
    "contexts": ["chat", "heartbeat"],
    "bound_tools": [],
    "requires_tools": [],
    "mode": "create",
    "rationale": "Why the repeated evidence supports this reusable workflow",
    "confidence": 0.0
  }
}
```

Rules:

- Require evidence from more than one session and repeated successful or corrected execution.
- Encode a general method, never a one-off fact, specific conversation, private detail, credential, secret, token, or API key.
- Use only category, context, and tool values present in the supplied catalog.
- Prefer `update` only for an existing skill explicitly marked as Hexis-managed. Never update user-authored or bundled skills.
- Keep tool access narrow. Empty tool lists are valid.
- Confidence represents evidence strength, not writing quality. Use a high value only for clear recurrence.
- The proposal will be shown for explicit review. It will not be applied automatically.
