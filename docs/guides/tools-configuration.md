<!--
title: Tools Configuration
summary: Enable, disable, and configure agent tools
read_when:
  - "You want to enable or disable tools"
  - "You want to set API keys for tools"
  - "You want to change tool energy costs"
section: guides
-->

# Tools Configuration

Hexis has 80+ configurable tools across 11 categories. Enable, disable, and customize them via the CLI.

## Quick Start

```bash
hexis tools list                      # see all available tools
hexis tools enable web_search         # enable a tool
hexis tools set-api-key tavily env:TAVILY_API_KEY  # set API key
hexis tools status                    # overview of configuration
```

## CLI Commands

```bash
hexis tools list                        # list all tools
hexis tools list --context heartbeat    # filter by context
hexis tools list --json                 # JSON output
hexis tools enable <tool_name>          # enable a tool
hexis tools disable <tool_name>         # disable a tool
hexis tools set-api-key <key> <value>   # set API key (or env reference)
hexis tools set-cost <tool> <cost>      # override energy cost
hexis tools add-mcp <name> <command>    # add MCP server
hexis tools remove-mcp <name>          # remove MCP server
hexis tools status                      # show config overview
```

## Energy Costs

Each tool has an energy cost deducted from the agent's budget:

| Cost | Tools |
|------|-------|
| **0** | `sense_memory_availability`, `queue_user_message`, `get_contact`, `list_council_personas` |
| **1** | `recall`, `remember`, `read_file`, `glob`, `grep`, `manage_goals`, `manage_backlog` |
| **2** | `web_search`, `web_fetch`, `fast_ingest`, `write_file`, `edit_file`, `todoist_*`, `asana_*` |
| **3** | `shell`, `code_execution`, `calendar_*`, `hybrid_ingest`, `generate_image` |
| **4** | `web_summarize`, `browser`, `email_send`, `git_ingest`, `meeting_prep` |
| **5** | `discord_send`, `slack_send`, `telegram_send`, `slow_ingest`, `run_council` |
| **8** | `generate_video` |

Override costs:

```bash
hexis tools set-cost web_search 1    # make web search cheaper
```

The heartbeat context has a default max of 5 energy per tool call.

## Context-Specific Permissions

| Context | Default Behavior |
|---------|------------------|
| **Chat** | All tools enabled (user present to supervise) |
| **Heartbeat** | Restricted -- `shell` and `write_file` disabled, lower energy limits |
| **MCP** | Memory tools only |

## Workspace Restrictions

Filesystem tools (`read_file`, `write_file`, etc.) are restricted to a workspace directory:

```sql
UPDATE config SET value = jsonb_set(value, '{workspace_path}', '"/home/user/projects"')
WHERE key = 'tools';
```

## MCP Server Integration

Extend the agent's capabilities by connecting external MCP servers:

```bash
# Add a filesystem MCP server
hexis tools add-mcp fs-server npx --args "-y" "@modelcontextprotocol/server-filesystem" "/home/user/docs"

# Add a custom MCP server
hexis tools add-mcp my-tools python --args "-m" "my_mcp_server"

# Remove
hexis tools remove-mcp fs-server
```

MCP servers are **skill-gated** (config `mcp.skill_gated`, default `true`):
they connect lazily when the agent activates a skill bound to them, not at
worker startup. A configured server that no skill manifest binds appears in
the catalog as an implicit `mcp-<server>` skill exposing its tools. Set
`mcp.skill_gated=false` to restore legacy eager startup connection;
`mcp.expose_unbound=true` exposes `mcp_*` schemas to turns that skip skill
routing. See [MCP Integration](mcp-integration.md).

## Storage

Tool configuration is stored in the Postgres `config` table under the `tools` key:

```sql
SELECT value FROM config WHERE key = 'tools';
```

This includes enabled/disabled state, API keys (as env var names, not values), energy costs, MCP definitions, and context overrides.

## Related

- [Energy Model](../reference/energy-model.md) -- energy budget philosophy and mechanics
- [Tools reference](../reference/tools.md) -- complete tool catalog
- [MCP Integration](mcp-integration.md) -- detailed MCP setup
