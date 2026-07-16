<!--
title: Heartbeat System
summary: OODA loop, energy budgets, and autonomous action
read_when:
  - "You want to understand autonomous behavior"
  - "You want to understand the heartbeat architecture"
section: concepts
-->

# Heartbeat System

The heartbeat is the agent's conscious cognitive loop -- the mechanism that gives it autonomous behavior.

## In Brief

A periodic OODA (Observe-Orient-Decide-Act) loop with energy budgets. The agent wakes up, reviews its situation, decides what to do within its energy budget, acts, records the experience, and sleeps.

## The Problem

Without a heartbeat, an AI agent is purely reactive -- it only does things when asked. This prevents:

- Pursuing goals across sessions
- Reflecting on past experiences
- Reaching out proactively
- Maintaining cognitive health (memory consolidation, contradiction resolution)

## How Hexis Approaches It

### The Loop

1. **Initialize** -- Regenerate energy (+10/hour, max 20)
2. **Observe** -- Check environment, pending events, user presence, scheduled tasks
3. **Orient** -- Review active goals, gather context (memories, clusters, identity, worldview, emotional state)
4. **Decide** -- LLM call with full context and action budget
5. **Act** -- Execute chosen actions within energy budget
6. **Record** -- Store heartbeat as episodic memory; the finished turn also mirrors into the conscious-episode substrate (`subconscious_units`), where the maintenance worker's extraction sweep can selectively promote salient facts to durable memory
7. **Wait** -- Sleep until next heartbeat

### Energy as Constraint

Energy makes action intentional. The agent can't do everything every heartbeat -- it must choose what matters.

| Cost | Actions |
|------|---------|
| **0** | Observe, sense memory |
| **1** | Recall, remember, manage goals |
| **2** | Web search, reflect |
| **3** | Code execution, create calendar event |
| **5** | Send messages, run council |

The budget forces trade-offs: "Do I research this or reach out to the user? Do I consolidate memories or pursue a goal?" These trade-offs are what make choices meaningful.

### Context Restrictions

Heartbeat context is more restricted than chat because no user is present to supervise:

- `shell` and `write_file` are disabled by default
- Max energy per tool call is capped (default: 5)
- Social actions have higher costs

### Worker Architecture

The heartbeat worker is stateless:

1. Polls `should_run_heartbeat()` periodically
2. Database function `run_heartbeat()` gathers all context
3. Worker executes LLM call with the context
4. Worker feeds results to `execute_heartbeat_actions_batch()`
5. `complete_heartbeat()` finalizes

If the worker crashes, no state is lost. The next poll picks up where things left off.

## Key Design Decisions

- **Database drives scheduling** -- `should_run_heartbeat()` decides timing, not the worker
- **Energy is not compute cost** -- it's situational consequence (see [Energy Model](../reference/energy-model.md))
- **Actions are batch-executed** -- all heartbeat actions in one DB call for atomicity
- **Heartbeat as memory** -- every heartbeat becomes an episodic memory, enabling self-reflection

## Implementation Pointers

- DB functions: `db/*_functions_heartbeat.sql`
- Worker: `services/worker_service.py` (`HeartbeatWorker`)
- Agent loop: `core/agent_loop.py`
- State: `heartbeat_state` view over `state` table

## Related

- [Heartbeat guide](../guides/heartbeat.md) -- enabling and managing the heartbeat
- [Energy Model](../reference/energy-model.md) -- cost mechanics and philosophy
- [Workers](../operations/workers.md) -- worker lifecycle
