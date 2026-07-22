<!--
title: Installation
summary: Install Hexis via pip or from source, configure environment
read_when:
  - "You want to install Hexis"
  - "You need to set up your .env file"
  - "You want to run from source"
section: start
-->

# Installation

## Install via pip (Recommended)

```bash
pip install hexis
```

This installs the `hexis` CLI and all dependencies. The CLI manages Docker containers, the database, and agent configuration.

## Install from Source

For development or contributing:

```bash
git clone https://github.com/QuixiAI/Hexis.git && cd Hexis
pip install -e .
cp .env.local .env   # edit with your settings
```

If build isolation fails in a restricted environment:

```bash
pip install -e . --no-build-isolation
```

## Environment Configuration

Create a `.env` file (automatically created by `hexis init` for pip installs):

```bash
POSTGRES_DB=hexis_memory
POSTGRES_USER=hexis_user
POSTGRES_PASSWORD=hexis_password
POSTGRES_HOST=localhost
POSTGRES_PORT=43815
HEXIS_BIND_ADDRESS=127.0.0.1    # Set to 0.0.0.0 to expose services
```

If port `43815` is already in use, set `POSTGRES_PORT` to any free port.

For LLM API keys (if using API-key providers):

```bash
OPENAI_API_KEY=sk-...           # OpenAI Platform
ANTHROPIC_API_KEY=sk-ant-...    # Anthropic
```

See [Environment Variables](../operations/environment-variables.md) for the complete reference.

## Start the Stack

```bash
hexis up         # starts PostgreSQL, RabbitMQ, heartbeat worker, and maintenance worker
hexis doctor     # verify everything is healthy
```

The CLI auto-detects whether you're running from source or a pip install and uses the appropriate Docker Compose file.

## Verify It Worked

```bash
hexis status     # should show database connected, agent not yet configured
hexis doctor     # checks Docker, DB, and embedding service health
```

## Next Steps

- [First Agent](first-agent.md) -- configure your agent's identity and personality
