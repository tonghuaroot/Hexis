<!--
title: Channels Setup
summary: General pattern for connecting messaging platforms
read_when:
  - "You want to connect a messaging channel"
  - "You want to understand how channels work"
section: guides
-->

# Channels Setup

Connect your Hexis agent to messaging platforms (Discord, Telegram, Slack, Signal, WhatsApp, iMessage, Matrix).

## Quick Start

```bash
# Configure a channel
hexis channels setup discord

# Start additional live channel workers (part of active profile)
hexis up --profile active

# Check status
hexis channels status
```

## General Setup Pattern

All channels follow the same three-step pattern:

### 1. Configure Credentials

```bash
hexis channels setup <channel>
```

This interactive command stores the required credentials (bot token, phone number, etc.) in the database config.

### 2. Start the Channel Worker

The channel worker runs as part of the `active` Docker Compose profile:

```bash
docker compose --profile active up -d
```

Or start a specific channel:

```bash
hexis channels start --channel discord
```

### 3. Verify

```bash
hexis channels status          # show session counts per channel
hexis channels status --json   # JSON output
```

## Architecture

```
Platform API  <-->  Channel Adapter  <-->  RabbitMQ  <-->  Agent Loop
```

Each adapter:
- Maintains a persistent connection to the platform
- Routes incoming messages through RabbitMQ to the agent's conversation loop
- Delivers the agent's responses back to the platform
- Tracks conversation sessions per user/channel

## Supported Channels

| Channel | Required | Notes |
|---------|----------|-------|
| Discord | Bot token | Create at discord.com/developers |
| Telegram | Bot token | Create via @BotFather |
| Slack | Bot token + App token | Create a Slack app with Socket Mode |
| Signal | Phone number | Requires `signal` Docker profile |
| WhatsApp | Phone number | WhatsApp Business API |
| iMessage | macOS + AppleScript | macOS only, no Docker |
| Matrix | Access token + homeserver | Any Matrix homeserver |

See individual channel pages under [Integrations > Channels](../integrations/channels/index.md) for per-channel setup details.

## Related

- [Channels overview](../integrations/channels/index.md) -- comparison matrix and individual channel docs
- [Docker Compose](../operations/docker-compose.md) -- profiles and services
