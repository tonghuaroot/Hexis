<!--
title: Quickstart
summary: Get a running Hexis agent in 3 commands
read_when:
  - "You want the fastest path to a working agent"
  - "You just want to try Hexis"
section: start
-->

# Quickstart

Get a running agent in 3 commands.

## Prerequisites

- [Docker Desktop](https://docs.docker.com/get-docker/) -- installed **and running**
- [Ollama](https://ollama.com/download) -- installed **and running** (`ollama serve` if it isn't); it serves the ~300M-parameter local embedding model that `hexis init` pulls
- Python 3.10+
- For the default command below: a **ChatGPT Plus/Pro subscription** (browser OAuth, no API key). Without one, pick any provider from [Other Providers](#other-providers) instead.

## 3-Command Setup

```bash
pip install hexis
hexis init --character hexis --provider openai-codex --model gpt-5.2
hexis chat
```

`hexis init` opens a browser window for login, starts the containers, pulls the embedding model, configures the character, and runs consent (the agent's recorded agreement to operate) -- all in one command.

**What success looks like:** init finishes with consent recorded; `hexis chat` greets you in character; `hexis status` reports a configured agent. Tell it your name, open a *new* chat, and ask -- it remembers.

**If it breaks:** `hexis doctor` diagnoses the usual suspects (Docker daemon down, Ollama unreachable, login incomplete). Then see [Troubleshooting](../operations/troubleshooting.md).

## Other Providers

**OAuth (no API key needed):**

```bash
# GitHub Copilot (device code login)
hexis init --character jarvis --provider github-copilot --model gpt-4o

# Chutes (free inference)
hexis init --character hexis --provider chutes --model deepseek-ai/DeepSeek-V3-0324

# Google Gemini CLI
hexis init --provider google-gemini-cli --model gemini-2.5-flash --character hexis

# Qwen Portal
hexis init --provider qwen-portal --model qwen-max-latest --character hexis
```

**API-key providers:**

```bash
# OpenAI Platform (auto-detect provider from key prefix)
hexis init --character jarvis --api-key sk-...

# Anthropic
hexis init --provider anthropic --model claude-sonnet-4-20250514 --api-key sk-ant-...

# Ollama (fully local, no API key needed)
hexis init --provider ollama --model llama3.1 --character hexis

# Express defaults (no character card)
hexis init --api-key sk-ant-...
```

`hexis init` auto-detects the provider from API key prefixes. For all supported providers, see [Auth Providers](../integrations/auth/index.md).

## Verify It Worked

```bash
hexis status    # shows agent status, memory counts, energy level
hexis doctor    # checks Docker, DB, embedding service health
hexis demo      # proves recall, refusal, energy, and heartbeat, then rolls back
hexis maturity  # shows live capability levels and exact next steps
```

## Enable Autonomy (Optional)

```bash
hexis up --profile active
```

With the `active` profile, the agent wakes on its own, reviews goals, reflects, and reaches out when it has something to say. Without it, the agent only responds when you talk to it.

## What Just Happened

1. `hexis init` started a PostgreSQL container (the agent's brain), pulled an embedding model into Ollama, configured your chosen character's identity/personality/values, and ran a consent flow where the agent agreed to begin.
2. `hexis chat` opened an interactive conversation with memory enrichment -- your messages are augmented with relevant memories, and the agent forms new memories from the conversation.

## Next Steps

- [Choose a character](../guides/character-cards.md) -- 11 presets or create your own
- [Ingest knowledge](../guides/ingestion.md) -- feed documents into memory
- [Enable the heartbeat](../guides/heartbeat.md) -- let the agent think autonomously
- [Set up messaging channels](../integrations/channels/index.md) -- Discord, Telegram, Slack, and more
- [Full installation guide](installation.md) -- .env configuration, source checkout
