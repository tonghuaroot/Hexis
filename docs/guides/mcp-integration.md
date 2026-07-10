<!--
title: MCP Integration
summary: Use Hexis as an MCP server or connect external MCP servers
read_when:
  - "You want to expose Hexis memory as MCP tools"
  - "You want to connect external MCP servers"
section: guides
-->

# MCP Integration

Use Hexis as an MCP (Model Context Protocol) server to expose memory operations, or connect external MCP servers to extend the agent's capabilities.

## Hexis as MCP Server

Expose the `cognitive_memory_api` surface as MCP tools over stdio:

```bash
hexis mcp
# or: python -m apps.hexis_mcp_server
```

### Available Tools

The MCP server exposes batch-style memory tools:

| Tool | Description |
|------|-------------|
| `remember_batch` | Store multiple memories |
| `connect_batch` | Create memory connections |
| `hydrate_batch` | Build context from memories |
| `batch` | Sequential tool calls |

It also exposes enabled Hexis registry tools whose policy allows MCP context.
Legacy memory names win on collisions, so each advertised tool name is unique.
Tool failures use MCP's `isError` flag and retain an actionable text message.

### Usage with LLM Runtimes

Any MCP-capable runtime can connect to Hexis. Typical flow:

1. LLM calls `hydrate` before answering a user (retrieves relevant context)
2. LLM calls `remember_batch` after a conversation (stores new knowledge)

### Configuration

```bash
hexis mcp --dsn postgresql://hexis_user:hexis_password@localhost:43815/hexis_memory
```

## Connecting External MCP Servers

Extend the agent's tool set by connecting external MCP servers:

```bash
# Add a filesystem MCP server
hexis tools add-mcp fs-server npx \
  --args "-y" "@modelcontextprotocol/server-filesystem" "/home/user/docs"

# Add a custom MCP server
hexis tools add-mcp my-tools python --args "-m" "my_mcp_server"

# Add with environment variables
hexis tools add-mcp api-server node --args "server.js" --env "API_KEY=xxx"

# Remove
hexis tools remove-mcp fs-server
```

### How It Works

- MCP servers are started automatically by the heartbeat worker
- Their tools become available to the agent alongside built-in tools
- MCP tools are automatically assigned energy costs based on their category (read/write/send)
- Configuration is stored in the `config` table under the `tools` key

### Energy Cost Assignment

MCP tools get costs assigned automatically:

| Category | Default Cost | Heartbeat Allowed |
|----------|-------------|-------------------|
| Read / search | 0.5-1.0 | Yes |
| Draft / create local | 1.0-2.0 | Yes |
| Modify / update | 2.0-3.0 | Context-dependent |
| Send private | 3.0-5.0 | No (default) |
| Send public | 6.0-8.0 | No |
| Delete / destroy | 5.0-7.0 | No |

## Related

- [Tools Configuration](tools-configuration.md) -- managing tools and API keys
- [Energy Model](../reference/energy-model.md) -- energy cost mechanics
