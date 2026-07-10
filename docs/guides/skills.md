<!--
title: Skills
summary: Use built-in skills and create custom workflow packages
read_when:
  - "You want to use or create skills"
  - "You want to understand the skills system"
section: guides
-->

# Skills

Skills are declarative, composable workflows that bundle tool sequences, prompts, and configuration into reusable packages.

## Quick Start

```bash
hexis skills list                    # list installed skills
hexis skills info daily-briefing     # show skill details
```

## Built-in Skills (12)

| Skill | Category | Description |
|-------|----------|-------------|
| `daily-briefing` | productivity | Morning summary of calendar, email, goals, priorities |
| `meeting-prep` | productivity | Pre-meeting research, attendee context, agenda prep |
| `email-digest` | communication | Summarize and triage unread email |
| `research` | research | Multi-source research with memory integration |
| `twitter-research` | research | Twitter/X trend and account analysis |
| `youtube-analytics` | analytics | Channel and video performance analysis |
| `crm-lookup` | productivity | Contact and deal lookup across CRM sources |
| `cost-report` | system | Usage and cost analysis across LLM providers |
| `knowledge-ingest` | knowledge | Guided knowledge ingestion with mode selection |
| `self-reflection` | system | Guided self-reflection and worldview review |
| `image-gen` | creative | Image generation with prompt refinement |
| `humanizer` | communication | Detect and rewrite AI-patterned text |

## Managing Skills

```bash
hexis skills list                    # list all skills
hexis skills info <name>             # show skill details
hexis skills install ./my-skill      # install custom skill
hexis skills uninstall <name>        # remove a skill
```

## Skill Format

Each skill is a directory with a `SKILL.md` file:

```yaml
---
name: my-skill
description: What this skill does
category: research          # research | productivity | communication | knowledge | analytics | creative | system | other
contexts: [chat, heartbeat] # where the skill can run
requires_tools: [web_search, recall]
requires_config: [TAVILY_API_KEY]
---

## Instructions

Markdown instructions the agent follows when executing this skill.
```

### Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Unique skill identifier |
| `description` | Yes | One-line description |
| `category` | Yes | Skill category |
| `contexts` | Yes | Where it can run: `chat`, `heartbeat`, or both |
| `requires_tools` | No | Tools the skill needs |
| `requires_config` | No | Config keys required |
| `provenance` | No | Ownership metadata reserved for managed agent-authored skills |

## Custom Skills

Custom skills go in `~/.hexis/skills/` or `skills/installed/` (for bundled skills).

User-authored skills under `~/.hexis/skills/` remain user-owned. The
`author_skill` agent tool writes only beneath
`~/.hexis/skills/agent-authored/` and adds structured `provenance` frontmatter.
An update is allowed only when the existing file proves it is managed by
`author_skill`; unmarked files and symlinked targets are refused without being
modified. Skills created by older Hexis versions with the exact legacy
provenance footer are upgraded to structured provenance on their next approved
update.

### Creating a Custom Skill

1. Create a directory: `~/.hexis/skills/my-skill/`
2. Add `SKILL.md` with frontmatter and instructions
3. Install: `hexis skills install ~/.hexis/skills/my-skill`

### Example

```markdown
---
name: weekly-review
description: Weekly review of goals, accomplishments, and plans
category: productivity
contexts: [chat, heartbeat]
requires_tools: [recall, manage_goals]
---

## Instructions

1. Recall all active goals and recent episodic memories from the past week.
2. Summarize accomplishments and progress toward each goal.
3. Identify blocked goals and suggest next steps.
4. Propose goals for the coming week based on patterns.
```

## Related

- [Tools Configuration](tools-configuration.md) -- ensuring required tools are enabled
- [Scheduling](scheduling.md) -- scheduling skill execution
