"""
Hexis Tools System - Backlog Management

A task/todo system that both the agent and user can CRUD.
Supports episodic memory creation when users modify the backlog,
multi-heartbeat continuation via checkpoints, and chat delegation.
"""

from __future__ import annotations

import json
import logging
from typing import Any, TYPE_CHECKING

from .base import (
    ToolCategory,
    ToolContext,
    ToolErrorType,
    ToolExecutionContext,
    ToolHandler,
    ToolResult,
    ToolSpec,
)

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

_VALID_STATUSES = {"todo", "in_progress", "done", "blocked", "cancelled"}
_VALID_PRIORITIES = {"urgent", "high", "normal", "low"}
_VALID_OWNERS = {"agent", "user", "shared"}
_VALID_CREATED_BY = {"agent", "user"}
_VALID_ACTIONS = {"create", "update", "delete", "list", "get", "set_status", "set_checkpoint"}


class ManageBacklogHandler(ToolHandler):
    """Manage a shared backlog of tasks between agent and user."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="manage_backlog",
            description=(
                "Manage a shared task backlog. Actions: "
                "'create' (new task), "
                "'update' (modify fields), "
                "'delete' (remove task), "
                "'list' (view tasks), "
                "'get' (single task detail), "
                "'set_status' (change task status), "
                "'set_checkpoint' (save progress for continuation)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": list(_VALID_ACTIONS),
                        "description": "The backlog action to perform.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Task title (required for 'create').",
                    },
                    "description": {
                        "type": "string",
                        "description": "Task description (optional for 'create'/'update').",
                    },
                    "priority": {
                        "type": "string",
                        "enum": list(_VALID_PRIORITIES),
                        "description": "Priority: urgent, high, normal, low.",
                    },
                    "owner": {
                        "type": "string",
                        "enum": list(_VALID_OWNERS),
                        "description": "Who owns the task: agent, user, shared.",
                    },
                    "status": {
                        "type": "string",
                        "enum": list(_VALID_STATUSES),
                        "description": "Task status (for 'set_status').",
                    },
                    "item_id": {
                        "type": "string",
                        "description": "Backlog item ID (required for update/delete/get/set_status/set_checkpoint).",
                    },
                    "parent_id": {
                        "type": "string",
                        "description": "Parent task ID (optional for 'create', for subtasks).",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Tags for the task.",
                    },
                    "checkpoint": {
                        "type": "object",
                        "description": "Checkpoint data for multi-heartbeat continuation.",
                    },
                    "note": {
                        "type": "string",
                        "description": "Status change reason (optional for 'set_status').",
                    },
                    "status_filter": {
                        "type": "string",
                        "description": "Filter by status (for 'list').",
                    },
                    "priority_filter": {
                        "type": "string",
                        "description": "Filter by priority (for 'list').",
                    },
                    "owner_filter": {
                        "type": "string",
                        "description": "Filter by owner (for 'list').",
                    },
                },
                "required": ["action"],
            },
            category=ToolCategory.MEMORY,
            energy_cost=1,
            is_read_only=False,
            requires_approval=False,
            allowed_contexts={ToolContext.HEARTBEAT, ToolContext.CHAT, ToolContext.MCP},
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        pool = context.registry.pool if context.registry else None
        if pool:
            try:
                async with pool.acquire() as conn:
                    raw = await conn.fetchval(
                        "SELECT execute_backlog_tool($1::jsonb, $2::jsonb)",
                        json.dumps(arguments),
                        json.dumps({"tool_context": context.tool_context.value}),
                    )
                payload = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(payload, dict) and "success" in payload:
                    if payload.get("success"):
                        return ToolResult.success_result(payload.get("output"), payload.get("display_output"))
                    return ToolResult.error_result(
                        payload.get("error") or "Backlog tool failed",
                        ToolErrorType(payload.get("error_type") or ToolErrorType.EXECUTION_FAILED.value),
                    )
            except Exception:
                logger.debug("DB backlog tool failed; falling back to compatibility path", exc_info=True)

        action = arguments.get("action", "")
        if action not in _VALID_ACTIONS:
            return ToolResult.error_result(
                f"Invalid action '{action}'. Must be one of: {', '.join(sorted(_VALID_ACTIONS))}",
                ToolErrorType.INVALID_PARAMS,
            )

        if not pool:
            return ToolResult.error_result(
                "Database pool not available",
                ToolErrorType.MISSING_CONFIG,
            )

        if action == "create":
            return await self._create(pool, arguments, context)
        if action == "update":
            return await self._update(pool, arguments, context)
        if action == "delete":
            return await self._delete(pool, arguments, context)
        if action == "list":
            return await self._list(pool, arguments)
        if action == "get":
            return await self._get(pool, arguments)
        if action == "set_status":
            return await self._set_status(pool, arguments, context)
        if action == "set_checkpoint":
            return await self._set_checkpoint(pool, arguments)

        return ToolResult.error_result(f"Unhandled action: {action}")

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    async def _create(
        self,
        pool: "asyncpg.Pool",
        args: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        title = (args.get("title") or "").strip()
        if not title:
            return ToolResult.error_result(
                "Title is required for create",
                ToolErrorType.INVALID_PARAMS,
            )

        description = args.get("description", "")
        priority = args.get("priority", "normal")
        owner = args.get("owner", "agent")
        tags = args.get("tags", [])
        parent_id = args.get("parent_id")

        if priority not in _VALID_PRIORITIES:
            priority = "normal"
        if owner not in _VALID_OWNERS:
            owner = "agent"

        # Determine created_by from context
        created_by = "user" if context.tool_context in (ToolContext.CHAT, ToolContext.MCP) else "agent"

        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM create_backlog_item($1, $2, $3, $4, $5, $6, $7)",
                    title,
                    description,
                    priority,
                    owner,
                    created_by,
                    tags or [],
                    parent_id,
                )

                # Create episodic memory when user creates a task
                if created_by == "user":
                    await self._record_user_change(
                        conn, "created", title, str(row["id"])
                    )

            return ToolResult.success_result(
                {
                    "item_id": str(row["id"]),
                    "title": row["title"],
                    "priority": row["priority"],
                    "owner": row["owner"],
                    "created_by": row["created_by"],
                },
                display_output=f"Created backlog item: {title} ({priority})",
            )
        except Exception as e:
            logger.error("Failed to create backlog item: %s", e)
            return ToolResult.error_result(f"Failed to create backlog item: {e}")

    async def _update(
        self,
        pool: "asyncpg.Pool",
        args: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        item_id = (args.get("item_id") or "").strip()
        if not item_id:
            return ToolResult.error_result(
                "item_id is required for update",
                ToolErrorType.INVALID_PARAMS,
            )

        # Build fields JSONB from args
        fields: dict[str, Any] = {}
        for key in ("title", "description", "priority", "owner", "status", "tags"):
            if key in args and args[key] is not None:
                fields[key] = args[key]

        if not fields:
            return ToolResult.error_result(
                "No fields to update. Provide at least one of: title, description, priority, owner, status, tags.",
                ToolErrorType.INVALID_PARAMS,
            )

        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM update_backlog_item($1::uuid, $2::jsonb)",
                    item_id,
                    json.dumps(fields),
                )

                if row is None or row["id"] is None:
                    return ToolResult.error_result(f"Backlog item {item_id} not found")

                # Record user modification
                is_user = context.tool_context in (ToolContext.CHAT, ToolContext.MCP)
                if is_user:
                    await self._record_user_change(
                        conn, "updated", row["title"], item_id
                    )

            return ToolResult.success_result(
                {
                    "item_id": str(row["id"]),
                    "title": row["title"],
                    "status": row["status"],
                    "priority": row["priority"],
                    "owner": row["owner"],
                },
                display_output=f"Updated backlog item {item_id[:8]}...",
            )
        except Exception as e:
            logger.error("Failed to update backlog item: %s", e)
            return ToolResult.error_result(f"Failed to update backlog item: {e}")

    async def _delete(
        self,
        pool: "asyncpg.Pool",
        args: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        item_id = (args.get("item_id") or "").strip()
        if not item_id:
            return ToolResult.error_result(
                "item_id is required for delete",
                ToolErrorType.INVALID_PARAMS,
            )

        try:
            async with pool.acquire() as conn:
                # Get title before deleting for memory creation
                existing = await conn.fetchrow(
                    "SELECT * FROM get_backlog_item($1::uuid)", item_id
                )

                deleted = await conn.fetchval(
                    "SELECT delete_backlog_item($1::uuid)", item_id
                )

                if not deleted:
                    return ToolResult.error_result(f"Backlog item {item_id} not found")

                # Record user deletion
                is_user = context.tool_context in (ToolContext.CHAT, ToolContext.MCP)
                if is_user and existing and existing["title"]:
                    await self._record_user_change(
                        conn, "deleted", existing["title"], item_id
                    )

            return ToolResult.success_result(
                {"item_id": item_id, "deleted": True},
                display_output=f"Deleted backlog item {item_id[:8]}...",
            )
        except Exception as e:
            logger.error("Failed to delete backlog item: %s", e)
            return ToolResult.error_result(f"Failed to delete backlog item: {e}")

    async def _list(
        self,
        pool: "asyncpg.Pool",
        args: dict[str, Any],
    ) -> ToolResult:
        status_filter = args.get("status_filter")
        priority_filter = args.get("priority_filter")
        owner_filter = args.get("owner_filter")

        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT * FROM list_backlog($1, $2, $3)",
                    status_filter if status_filter in _VALID_STATUSES else None,
                    priority_filter if priority_filter in _VALID_PRIORITIES else None,
                    owner_filter if owner_filter in _VALID_OWNERS else None,
                )

            items = []
            for row in rows:
                items.append({
                    "id": str(row["id"]),
                    "title": row["title"],
                    "description": row["description"],
                    "status": row["status"],
                    "priority": row["priority"],
                    "owner": row["owner"],
                    "created_by": row["created_by"],
                    "tags": row["tags"] or [],
                    "has_checkpoint": row["checkpoint"] is not None,
                    "parent_id": str(row["parent_id"]) if row["parent_id"] else None,
                })

            return ToolResult.success_result(
                {"items": items, "count": len(items)},
            )
        except Exception as e:
            logger.error("Failed to list backlog: %s", e)
            return ToolResult.error_result(f"Failed to list backlog: {e}")

    async def _get(
        self,
        pool: "asyncpg.Pool",
        args: dict[str, Any],
    ) -> ToolResult:
        item_id = (args.get("item_id") or "").strip()
        if not item_id:
            return ToolResult.error_result(
                "item_id is required for get",
                ToolErrorType.INVALID_PARAMS,
            )

        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM get_backlog_item($1::uuid)", item_id
                )

            if row is None or row["id"] is None:
                return ToolResult.error_result(f"Backlog item {item_id} not found")

            checkpoint = row["checkpoint"]
            if checkpoint and isinstance(checkpoint, str):
                checkpoint = json.loads(checkpoint)

            return ToolResult.success_result({
                "id": str(row["id"]),
                "title": row["title"],
                "description": row["description"],
                "status": row["status"],
                "priority": row["priority"],
                "owner": row["owner"],
                "created_by": row["created_by"],
                "tags": row["tags"] or [],
                "checkpoint": checkpoint,
                "parent_id": str(row["parent_id"]) if row["parent_id"] else None,
                "due_date": row["due_date"].isoformat() if row["due_date"] else None,
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
            })
        except Exception as e:
            logger.error("Failed to get backlog item: %s", e)
            return ToolResult.error_result(f"Failed to get backlog item: {e}")

    async def _set_status(
        self,
        pool: "asyncpg.Pool",
        args: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        item_id = (args.get("item_id") or "").strip()
        if not item_id:
            return ToolResult.error_result(
                "item_id is required for set_status",
                ToolErrorType.INVALID_PARAMS,
            )

        status = args.get("status", "")
        if status not in _VALID_STATUSES:
            return ToolResult.error_result(
                f"Invalid status '{status}'. Must be one of: {', '.join(sorted(_VALID_STATUSES))}",
                ToolErrorType.INVALID_PARAMS,
            )

        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM update_backlog_item($1::uuid, $2::jsonb)",
                    item_id,
                    json.dumps({"status": status}),
                )

                if row is None or row["id"] is None:
                    return ToolResult.error_result(f"Backlog item {item_id} not found")

                is_user = context.tool_context in (ToolContext.CHAT, ToolContext.MCP)
                if is_user:
                    await self._record_user_change(
                        conn,
                        f"changed status to '{status}' on",
                        row["title"],
                        item_id,
                    )

            return ToolResult.success_result(
                {"item_id": str(row["id"]), "title": row["title"], "new_status": status},
                display_output=f"Set {row['title']} to {status}",
            )
        except Exception as e:
            logger.error("Failed to set backlog status: %s", e)
            return ToolResult.error_result(f"Failed to set backlog status: {e}")

    async def _set_checkpoint(
        self,
        pool: "asyncpg.Pool",
        args: dict[str, Any],
    ) -> ToolResult:
        item_id = (args.get("item_id") or "").strip()
        if not item_id:
            return ToolResult.error_result(
                "item_id is required for set_checkpoint",
                ToolErrorType.INVALID_PARAMS,
            )

        checkpoint = args.get("checkpoint")
        if checkpoint is None:
            return ToolResult.error_result(
                "checkpoint data is required for set_checkpoint",
                ToolErrorType.INVALID_PARAMS,
            )

        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM update_backlog_item($1::uuid, $2::jsonb)",
                    item_id,
                    json.dumps({"checkpoint": checkpoint}),
                )

                if row is None or row["id"] is None:
                    return ToolResult.error_result(f"Backlog item {item_id} not found")

            return ToolResult.success_result(
                {"item_id": str(row["id"]), "title": row["title"], "checkpoint_saved": True},
                display_output=f"Saved checkpoint for {row['title']}",
            )
        except Exception as e:
            logger.error("Failed to set checkpoint: %s", e)
            return ToolResult.error_result(f"Failed to set checkpoint: {e}")

    # ------------------------------------------------------------------
    # User change memory
    # ------------------------------------------------------------------

    async def _record_user_change(
        self,
        conn: "asyncpg.Connection",
        action_verb: str,
        title: str,
        item_id: str,
    ) -> None:
        """Create an episodic memory when the user modifies the backlog."""
        content = f"User {action_verb} backlog item: {title}"
        try:
            await conn.execute(
                """
                INSERT INTO memories (type, content, embedding, importance, trust_level, metadata)
                VALUES (
                    'episodic',
                    $1,
                    array_fill(0.1, ARRAY[embedding_dimension()])::vector,
                    0.6,
                    1.0,
                    $2::jsonb
                )
                """,
                content,
                json.dumps({
                    "backlog_item_id": item_id,
                    "action": action_verb,
                    "source": "user_backlog_change",
                }),
            )
        except Exception:
            logger.debug("Failed to record user backlog change memory", exc_info=True)


def create_backlog_tools() -> list[ToolHandler]:
    """Create backlog management tools."""
    return [ManageBacklogHandler()]
