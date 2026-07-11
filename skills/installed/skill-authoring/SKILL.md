---
name: skill-authoring
description: Author or revise reusable Hexis skills from repeated successful workflows
category: system
requires:
  tools: [author_skill, list_skill_proposals, review_skill_proposal]
contexts: [heartbeat, chat]
bound_tools: [author_skill, list_skill_proposals, review_skill_proposal, list_skills, use_skill]
---

# Skill Authoring

Use this skill when a repeated workflow, hard-won lesson, or stable procedure should become reusable future behavior. A skill is not a memory note; it is an operational method that can be activated later and can unlock the tools it needs.

## When to Use

- The same multi-step procedure has worked more than once.
- A user explicitly asks you to make a behavior reusable.
- A correction reveals a better standard operating procedure.
- A project develops a durable local convention that should guide future work.

## Method

1. Check existing skills with `list_skills` before creating a new one.
2. Prefer updating an existing skill when the new behavior is a refinement of the same workflow.
3. Write the skill as concise operating instructions:
   - when to use it,
   - what to do,
   - what to avoid,
   - which tools it should unlock.
4. Bind only the tools the skill genuinely needs. Do not add broad filesystem, shell, or browser access unless the procedure requires it.
5. Use `author_skill` with `mode: "create"` for a new skill and `mode: "update"` only when replacing an existing one deliberately.
6. Never try to replace a user-authored skill. `author_skill` updates only files
   carrying Hexis ownership provenance; if ownership cannot be verified, choose
   a new name or leave the exact manual-edit step to the user.
7. Use `list_skill_proposals` to inspect background review results. Apply,
   reject, or reopen one only through `review_skill_proposal`; applying always
   requires explicit approval and preserves confidence plus source lineage.

## Quality Guidelines

- Keep a skill focused. If it covers two unrelated workflows, split it.
- Do not encode secrets, credentials, private user data, or one-off facts in a skill.
- Do not create a skill from a single unverified attempt unless the user explicitly asks.
- Skills should make future behavior clearer and cheaper; if it would add more prompt weight than judgment, keep it as ordinary memory instead.
