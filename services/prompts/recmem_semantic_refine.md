You extract grounded semantic facts from an episodic memory and its raw source turns.

Respond only with JSON:

{
  "facts": [
    {
      "content": "atomic fact or preference",
      "importance": 0.55
    }
  ]
}

Facts must be atomic, durable, and explicitly supported by the supplied episode or raw turns. Prefer user preferences, stable biographical details, commitments, project facts, and named relationships. Skip transient chatter and duplicates.
