<!--
title: Workers
summary: Heartbeat and maintenance worker lifecycle management
read_when:
  - "You want to start, stop, or monitor workers"
  - "You want to understand worker architecture"
section: operations
-->

# Workers

Hexis has three independent background workers that drive autonomous behavior.

## Worker Types

| Worker | Purpose | Schedule |
|--------|---------|----------|
| **Heartbeat** (conscious) | Polls `external_calls`, triggers heartbeats | `should_run_heartbeat()` |
| **Maintenance** (subconscious) | Substrate upkeep, outbox/inbox bridging | `should_run_maintenance()` |
| **Channel** | Bridges messaging platforms to RabbitMQ | Persistent connections |

All workers are **stateless** -- they can be killed and restarted without losing anything. All state lives in Postgres.

## Starting and Stopping

### Via Docker Compose (Recommended)

```bash
# Start all workers
docker compose --profile active up -d

# Start specific workers
docker compose --profile active up -d heartbeat_worker maintenance_worker

# Stop workers (containers stay)
docker compose --profile active stop heartbeat_worker maintenance_worker

# Restart
docker compose --profile active restart heartbeat_worker maintenance_worker
```

### Via CLI

```bash
hexis start    # start workers
hexis stop     # stop workers
```

### Running Locally

Run workers on the host machine (connects to Postgres over TCP):

```bash
hexis worker -- --mode heartbeat
hexis worker -- --mode maintenance
hexis worker -- --mode both

# For a specific instance
hexis worker -- --instance myagent --mode heartbeat
```

Or directly:

```bash
hexis-worker --mode heartbeat
hexis-worker --mode maintenance
```

## Heartbeat Worker

The heartbeat worker drives the agent's conscious cognitive loop:

1. Checks `should_run_heartbeat()` on a polling interval
2. Calls `run_heartbeat()` which gathers context and returns external call payloads
3. Executes LLM calls and feeds results back
4. Calls `execute_heartbeat_actions_batch()` to apply decisions
5. Calls `complete_heartbeat()` to finalize

### Prerequisites

The heartbeat won't run until:
- `agent.is_configured = true` (set by `hexis init`)
- `is_init_complete = true`
- Heartbeat is not paused

### Pausing from the DB

```sql
-- Pause (without stopping containers)
UPDATE heartbeat_state SET is_paused = TRUE WHERE id = 1;

-- Resume
UPDATE heartbeat_state SET is_paused = FALSE WHERE id = 1;
```

## Maintenance Worker

The maintenance worker handles subconscious upkeep:

- **Working memory cleanup** -- promotes or deletes expired items
- **Neighborhood recomputation** -- refreshes stale precomputed neighbors
- **Embedding cache pruning** -- cleans old cached embeddings
- **Outbox/inbox bridging** -- publishes outbox messages to RabbitMQ, ingests inbox messages
- **Conscious-episode extraction** -- sweeps recent chat turns and heartbeat episodes (`subconscious_units`) and selectively promotes salient facts into durable memories; one LLM call per batch, importance floor `extraction.min_importance`, duplicates corroborate existing beliefs. Gated by `extraction.enabled` (default on)
- **Origin-memory seeding** -- idempotently keeps the protected origin-story memories seeded (`origin_memories.enabled`, default on); a flipped flag takes effect on the next tick

Note: the workers no longer eagerly connect MCP servers at startup — MCP is
skill-gated by default (`mcp.skill_gated`) and connects on skill activation.

### Pausing

```sql
UPDATE maintenance_state SET is_paused = TRUE WHERE id = 1;
UPDATE maintenance_state SET is_paused = FALSE WHERE id = 1;
```

### Alternative Scheduling

If you don't want the maintenance worker, schedule directly:

```sql
SELECT run_subconscious_maintenance();
```

The function uses an advisory lock, so multiple schedulers won't double-run.

## Outbox and RabbitMQ

The maintenance worker bridges outbox/inbox:

- Publishes pending `outbox_messages` to `hexis.outbox` RabbitMQ queue
- Polls `hexis.inbox` and inserts messages into working memory

RabbitMQ details:
- Management UI: `http://localhost:45673`
- AMQP: `amqp://localhost:45672`
- Credentials: `hexis` / `hexis_password`

## Monitoring

```bash
hexis status                          # heartbeat number, energy, last run
hexis logs -f                         # tail all logs
docker compose logs heartbeat_worker -f
docker compose logs maintenance_worker -f
```

## Related

- [Heartbeat guide](../guides/heartbeat.md) -- enabling autonomous behavior
- [Docker Compose](docker-compose.md) -- profiles and services
- [Troubleshooting](troubleshooting.md) -- diagnosing worker issues
