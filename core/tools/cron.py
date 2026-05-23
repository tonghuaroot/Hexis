"""
Hexis Tools System - Scheduled Task Management (Cron)

Allows the agent to create, list, update, and cancel scheduled tasks
through the standard tool_use interface. Wraps the database functions
in db/19_functions_scheduling.sql.
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

_VALID_ACTIONS = {"create", "list", "update", "cancel", "stats"}
_VALID_SCHEDULE_KINDS = {"once", "interval", "daily", "weekly", "cron"}
_VALID_ACTION_KINDS = {"queue_user_message", "create_goal"}
_VALID_DELIVERY_MODES = {"outbox", "channel", "webhook", "silent"}


def _is_cron_expression(s: str) -> bool:
    """Check if a string looks like a standard cron expression (5 or 6 fields)."""
    import re
    parts = s.strip().split()
    if len(parts) not in (5, 6):
        return False
    # Each field should match cron syntax: digits, *, /, -, comma
    cron_field = re.compile(r'^[\d\*\/\-\,\?LW#]+$')
    return all(cron_field.match(p) for p in parts)


def _cron_next_run(cron_expr: str, timezone: str = "UTC") -> str | None:
    """Compute the next run time for a cron expression. Returns ISO8601 string or None."""
    try:
        from croniter import croniter
        from datetime import datetime, timezone as tz
        import pytz
    except ImportError:
        from datetime import datetime, timedelta, timezone as tz
        return (datetime.now(tz.utc) + timedelta(minutes=1)).isoformat()
    try:
        local_tz = pytz.timezone(timezone)
    except Exception:
        local_tz = pytz.UTC
    now = datetime.now(tz.utc).astimezone(local_tz)
    cron = croniter(cron_expr, now)
    next_dt = cron.get_next(datetime)
    if next_dt.tzinfo is None:
        next_dt = local_tz.localize(next_dt)
    return next_dt.astimezone(tz.utc).isoformat()


def _parse_shorthand_schedule(
    schedule_str: str,
    *,
    timezone: str = "UTC",
) -> tuple[str, dict[str, Any], dict[str, Any]] | None:
    """Parse human-friendly schedule shorthands into (schedule_kind, schedule, action_payload).

    Supported formats:
        "once:+2h"          -> once, run_at = now + 2 hours
        "once:+30m"         -> once, run_at = now + 30 minutes
        "daily:07:00"       -> daily, time = 07:00
        "weekly:monday:09:00" -> weekly, weekday = 1, time = 09:00
        "every:5m"          -> interval, every_minutes = 5
        "every:2h"          -> interval, every_hours = 2
        "0 9 * * *"         -> cron expression (standard 5-field format)

    Returns None if the string doesn't match any shorthand.
    """
    if not schedule_str:
        return None

    # Check for standard cron expression first
    if _is_cron_expression(schedule_str):
        next_run = _cron_next_run(schedule_str, timezone)
        return ("cron", {"cron": schedule_str, "_next_run": next_run or ""}, {})

    if ":" not in schedule_str:
        return None

    parts = schedule_str.strip().split(":")

    kind = parts[0].lower()

    if kind == "once" and len(parts) >= 2:
        offset = parts[1].strip()
        if offset.startswith("+"):
            offset = offset[1:]
        # Parse duration like "2h", "30m", "1d"
        unit = offset[-1].lower()
        try:
            value = int(offset[:-1])
        except (ValueError, IndexError):
            return None
        if unit == "h":
            interval_expr = f"INTERVAL '{value} hours'"
        elif unit == "m":
            interval_expr = f"INTERVAL '{value} minutes'"
        elif unit == "d":
            interval_expr = f"INTERVAL '{value} days'"
        else:
            return None
        # Use a sentinel that the caller will resolve with SQL
        return ("once", {"_offset": f"{value}{unit}"}, {})

    if kind == "daily" and len(parts) >= 3:
        time_str = f"{parts[1]}:{parts[2]}"
        return ("daily", {"time": time_str}, {})

    if kind == "weekly" and len(parts) >= 4:
        weekday = parts[1]
        time_str = f"{parts[2]}:{parts[3]}"
        return ("weekly", {"weekday": weekday, "time": time_str}, {})

    if kind == "every" and len(parts) >= 2:
        interval = parts[1].strip()
        unit = interval[-1].lower()
        try:
            value = int(interval[:-1])
        except (ValueError, IndexError):
            return None
        if unit == "h":
            return ("interval", {"every_hours": value}, {})
        elif unit == "m":
            return ("interval", {"every_minutes": value}, {})
        elif unit == "s":
            return ("interval", {"every_seconds": value}, {})
        return None

    return None


class ManageScheduleHandler(ToolHandler):
    """Manage scheduled tasks: create, list, update, or cancel recurring/one-shot tasks."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="manage_schedule",
            description=(
                "Manage your scheduled tasks. Actions: "
                "'create' (new task), "
                "'list' (view scheduled tasks), "
                "'update' (modify a task), "
                "'cancel' (disable/delete a task), "
                "'stats' (execution statistics). "
                "Schedule kinds: 'once' (one-shot), 'interval' (recurring), 'daily', 'weekly', "
                "'cron' (standard cron expression like '0 9 * * *'). "
                "Shorthand: 'once:+2h', 'daily:07:00', 'weekly:monday:09:00', 'every:5m', "
                "or standard cron: '*/15 * * * *', '0 9 * * 1-5'. "
                "Action kinds: 'queue_user_message' (send a message prompt to yourself), "
                "'create_goal' (create a goal when the task fires). "
                "Delivery modes: 'outbox' (default), 'channel' (specific channel+topic), "
                "'webhook' (HTTP POST), 'silent' (log only, no notification)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": list(_VALID_ACTIONS),
                        "description": "The scheduling action to perform.",
                    },
                    "name": {
                        "type": "string",
                        "description": "Name for the scheduled task (required for 'create').",
                    },
                    "description": {
                        "type": "string",
                        "description": "Description of what the task does (optional).",
                    },
                    "schedule_kind": {
                        "type": "string",
                        "enum": list(_VALID_SCHEDULE_KINDS),
                        "description": "Schedule type: 'once', 'interval', 'daily', 'weekly'. Can also use shorthand in 'schedule' field.",
                    },
                    "schedule": {
                        "type": "string",
                        "description": (
                            "Schedule specification. Either a shorthand like 'daily:07:00', "
                            "'once:+2h', 'every:5m', 'weekly:monday:09:00' — or a JSON object "
                            "matching the schedule_kind (e.g. {\"time\": \"07:00\"} for daily)."
                        ),
                    },
                    "timezone": {
                        "type": "string",
                        "description": "Timezone for the schedule (default: UTC). E.g. 'America/New_York'.",
                    },
                    "action_kind": {
                        "type": "string",
                        "enum": list(_VALID_ACTION_KINDS),
                        "description": "What to do when the task fires. Default: 'queue_user_message'.",
                    },
                    "message": {
                        "type": "string",
                        "description": "Message/prompt for 'queue_user_message' action_kind (required for create with queue_user_message).",
                    },
                    "goal_title": {
                        "type": "string",
                        "description": "Goal title for 'create_goal' action_kind.",
                    },
                    "task_id": {
                        "type": "string",
                        "description": "Task ID (required for 'update' and 'cancel').",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["active", "paused", "disabled"],
                        "description": "New status (for 'update').",
                    },
                    "max_runs": {
                        "type": "integer",
                        "description": "Maximum number of times the task should run. 1 for one-shot.",
                    },
                    "delivery_mode": {
                        "type": "string",
                        "enum": list(_VALID_DELIVERY_MODES),
                        "description": (
                            "Where to deliver results: 'outbox' (default, normal outbox routing), "
                            "'channel' (specific channel+topic), 'webhook' (HTTP POST to URL), "
                            "'silent' (log only, no notification)."
                        ),
                    },
                    "delivery_channel": {
                        "type": "string",
                        "description": "Channel type for 'channel' delivery (e.g. 'telegram', 'discord').",
                    },
                    "delivery_topic": {
                        "type": "string",
                        "description": "Topic/thread ID for 'channel' delivery.",
                    },
                    "delivery_target_id": {
                        "type": "string",
                        "description": "Target chat/user/channel ID for 'channel' delivery mode.",
                    },
                    "delivery_webhook_url": {
                        "type": "string",
                        "description": "Webhook URL for 'webhook' delivery mode.",
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
        action = arguments.get("action", "")
        if action not in _VALID_ACTIONS:
            return ToolResult.error_result(
                f"Invalid action '{action}'. Must be one of: {', '.join(sorted(_VALID_ACTIONS))}",
                ToolErrorType.INVALID_PARAMS,
            )

        pool = context.registry.pool if context.registry else None
        if not pool:
            return ToolResult.error_result(
                "Database pool not available",
                ToolErrorType.MISSING_CONFIG,
            )

        try:
            async with pool.acquire() as conn:
                raw = await conn.fetchval(
                    "SELECT manage_schedule_tool($1::jsonb)",
                    json.dumps(arguments),
                )
            payload = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(payload, dict) and "success" in payload:
                if payload.get("success"):
                    return ToolResult.success_result(
                        payload.get("output"),
                        display_output=payload.get("display_output"),
                    )
                error_type = payload.get("error_type") or ToolErrorType.EXECUTION_FAILED.value
                try:
                    typed_error = ToolErrorType(error_type)
                except ValueError:
                    typed_error = ToolErrorType.EXECUTION_FAILED
                return ToolResult.error_result(payload.get("error") or "Schedule action failed", typed_error)
        except Exception:
            logger.debug("DB schedule tool failed; falling back to compatibility path", exc_info=True)

        if action == "create":
            return await self._create(pool, arguments)
        if action == "list":
            return await self._list(pool, arguments)
        if action == "update":
            return await self._update(pool, arguments)
        if action == "cancel":
            return await self._cancel(pool, arguments)
        if action == "stats":
            return await self._stats(pool, arguments)

        return ToolResult.error_result(f"Unhandled action: {action}")

    async def _create(self, pool: "asyncpg.Pool", args: dict[str, Any]) -> ToolResult:
        name = (args.get("name") or "").strip()
        if not name:
            return ToolResult.error_result("Name is required for create", ToolErrorType.INVALID_PARAMS)

        description = args.get("description")
        timezone = args.get("timezone", "UTC")
        max_runs = args.get("max_runs")

        # Resolve schedule
        schedule_str = (args.get("schedule") or "").strip()
        schedule_kind = args.get("schedule_kind")
        schedule_json: dict[str, Any] = {}

        # Try shorthand first
        if schedule_str:
            parsed = _parse_shorthand_schedule(schedule_str, timezone=timezone)
            if parsed:
                schedule_kind = parsed[0]
                schedule_json = parsed[1]
            else:
                # Try parsing as JSON
                try:
                    schedule_json = json.loads(schedule_str)
                except (json.JSONDecodeError, TypeError):
                    # Treat as a time for daily schedule
                    if ":" in schedule_str and len(schedule_str) <= 5:
                        schedule_kind = schedule_kind or "daily"
                        schedule_json = {"time": schedule_str}
                    else:
                        return ToolResult.error_result(
                            f"Could not parse schedule: '{schedule_str}'. "
                            "Use shorthand (e.g. 'daily:07:00') or JSON.",
                            ToolErrorType.INVALID_PARAMS,
                        )

        if not schedule_kind:
            return ToolResult.error_result(
                "schedule_kind is required (or use shorthand in schedule field)",
                ToolErrorType.INVALID_PARAMS,
            )

        # Resolve action kind and payload
        action_kind = args.get("action_kind", "queue_user_message")
        action_payload: dict[str, Any] = {}

        if action_kind == "queue_user_message":
            message = (args.get("message") or "").strip()
            if not message:
                return ToolResult.error_result(
                    "message is required for queue_user_message action_kind",
                    ToolErrorType.INVALID_PARAMS,
                )
            action_payload = {"message": message}
        elif action_kind == "create_goal":
            title = (args.get("goal_title") or args.get("name") or "").strip()
            if not title:
                return ToolResult.error_result(
                    "goal_title is required for create_goal action_kind",
                    ToolErrorType.INVALID_PARAMS,
                )
            action_payload = {"title": title, "description": description}

        # Handle once:+offset shorthand — resolve to absolute time
        if schedule_kind == "once" and "_offset" in schedule_json:
            offset = schedule_json.pop("_offset")
            # Validate offset format strictly (e.g. "2h", "30m", "1d")
            import re
            if not re.fullmatch(r"\d+[hmd]", offset):
                return ToolResult.error_result(
                    f"Invalid offset format: '{offset}'. Use e.g. '2h', '30m', '1d'.",
                    ToolErrorType.INVALID_PARAMS,
                )
            # Use parameterized SQL to compute the absolute time
            try:
                async with pool.acquire() as conn:
                    run_at = await conn.fetchval(
                        "SELECT (CURRENT_TIMESTAMP + $1::interval)::timestamptz",
                        offset.replace("h", " hours").replace("m", " minutes").replace("d", " days"),
                    )
                    schedule_json["run_at"] = run_at.isoformat()
            except Exception as e:
                return ToolResult.error_result(f"Failed to compute schedule offset: {e}")

        # For cron expressions, ensure _next_run is populated
        if schedule_kind == "cron":
            cron_expr = schedule_json.get("cron", "")
            if not cron_expr:
                return ToolResult.error_result(
                    "Cron expression is required for cron schedule_kind",
                    ToolErrorType.INVALID_PARAMS,
                )
            try:
                from croniter import croniter
                croniter(cron_expr)  # Validate expression
            except ImportError:
                if not _is_cron_expression(cron_expr):
                    return ToolResult.error_result(
                        f"Invalid cron expression '{cron_expr}'",
                        ToolErrorType.INVALID_PARAMS,
                    )
            except (ValueError, KeyError) as e:
                return ToolResult.error_result(
                    f"Invalid cron expression '{cron_expr}': {e}",
                    ToolErrorType.INVALID_PARAMS,
                )
            next_run = _cron_next_run(cron_expr, timezone)
            if next_run:
                schedule_json["_next_run"] = next_run

        # For one-shot tasks, default max_runs to 1
        if schedule_kind == "once" and max_runs is None:
            max_runs = 1

        # Build delivery JSONB
        delivery = self._build_delivery(args)
        if delivery.get("mode") == "channel" and not delivery.get("target_id"):
            return ToolResult.error_result(
                "delivery_target_id is required when delivery_mode is 'channel'",
                ToolErrorType.INVALID_PARAMS,
            )
        if delivery.get("mode") == "webhook" and not delivery.get("url"):
            return ToolResult.error_result(
                "delivery_webhook_url is required when delivery_mode is 'webhook'",
                ToolErrorType.INVALID_PARAMS,
            )

        try:
            async with pool.acquire() as conn:
                task_id = await conn.fetchval(
                    """SELECT create_scheduled_task(
                        $1, $2, $3::jsonb, $4, $5::jsonb,
                        $6, $7, 'active', $8, 'agent', $9::jsonb
                    )""",
                    name,
                    schedule_kind,
                    json.dumps(schedule_json),
                    action_kind,
                    json.dumps(action_payload),
                    timezone,
                    description,
                    max_runs,
                    json.dumps(delivery),
                )
            return ToolResult.success_result(
                {
                    "task_id": str(task_id),
                    "name": name,
                    "schedule_kind": schedule_kind,
                    "action_kind": action_kind,
                    "delivery": delivery,
                },
                display_output=f"Created scheduled task: {name} ({schedule_kind})",
            )
        except Exception as e:
            logger.error("Failed to create scheduled task: %s", e)
            return ToolResult.error_result(f"Failed to create scheduled task: {e}")

    async def _list(self, pool: "asyncpg.Pool", args: dict[str, Any]) -> ToolResult:
        status_filter = args.get("status")

        try:
            async with pool.acquire() as conn:
                if status_filter:
                    rows = await conn.fetch(
                        "SELECT * FROM list_scheduled_tasks($1)",
                        status_filter,
                    )
                else:
                    rows = await conn.fetch(
                        "SELECT * FROM list_scheduled_tasks()"
                    )

            tasks = []
            for row in rows:
                task_info = {
                    "id": str(row["id"]),
                    "name": row["name"],
                    "description": row.get("description"),
                    "schedule_kind": row["schedule_kind"],
                    "status": row["status"],
                    "next_run_at": str(row["next_run_at"]) if row.get("next_run_at") else None,
                    "last_run_at": str(row["last_run_at"]) if row.get("last_run_at") else None,
                    "run_count": row.get("run_count", 0),
                    "action_kind": row.get("action_kind"),
                    "last_error": row.get("last_error"),
                }
                if row.get("delivery"):
                    delivery = row["delivery"]
                    if isinstance(delivery, str):
                        try:
                            delivery = json.loads(delivery)
                        except Exception:
                            pass
                    if isinstance(delivery, dict) and delivery.get("mode") != "outbox":
                        task_info["delivery"] = delivery
                tasks.append(task_info)
            return ToolResult.success_result(
                {"tasks": tasks, "count": len(tasks)},
                display_output=f"Found {len(tasks)} scheduled task(s)",
            )
        except Exception as e:
            logger.error("Failed to list scheduled tasks: %s", e)
            return ToolResult.error_result(f"Failed to list scheduled tasks: {e}")

    async def _update(self, pool: "asyncpg.Pool", args: dict[str, Any]) -> ToolResult:
        task_id = (args.get("task_id") or "").strip()
        if not task_id:
            return ToolResult.error_result("task_id is required for update", ToolErrorType.INVALID_PARAMS)

        # Build update parameters
        name = args.get("name")
        description = args.get("description")
        status = args.get("status")
        schedule_kind = args.get("schedule_kind")
        schedule_str = args.get("schedule")
        timezone = args.get("timezone")
        action_kind = args.get("action_kind")
        max_runs = args.get("max_runs")

        # Parse schedule if provided
        schedule_json = None
        if schedule_str:
            effective_timezone = timezone
            if not effective_timezone:
                try:
                    async with pool.acquire() as conn:
                        effective_timezone = await conn.fetchval(
                            "SELECT timezone FROM scheduled_tasks WHERE id = $1::uuid",
                            task_id,
                        ) or "UTC"
                except Exception:
                    effective_timezone = "UTC"

            parsed = _parse_shorthand_schedule(schedule_str, timezone=effective_timezone)
            if parsed:
                schedule_kind = schedule_kind or parsed[0]
                parsed_schedule = parsed[1]
                if schedule_kind == "cron":
                    cron_expr = parsed_schedule.get("cron", "")
                    if not cron_expr:
                        return ToolResult.error_result(
                            "Cron expression is required for cron schedule_kind",
                            ToolErrorType.INVALID_PARAMS,
                        )
                    try:
                        from croniter import croniter
                        croniter(cron_expr)
                    except ImportError:
                        if not _is_cron_expression(cron_expr):
                            return ToolResult.error_result(
                                f"Invalid cron expression '{cron_expr}'",
                                ToolErrorType.INVALID_PARAMS,
                            )
                    except (ValueError, KeyError) as e:
                        return ToolResult.error_result(
                            f"Invalid cron expression '{cron_expr}': {e}",
                            ToolErrorType.INVALID_PARAMS,
                        )
                    next_run = _cron_next_run(cron_expr, effective_timezone or "UTC")
                    if next_run:
                        parsed_schedule["_next_run"] = next_run
                schedule_json = json.dumps(parsed_schedule)
            else:
                try:
                    schedule_obj = json.loads(schedule_str)
                    if not isinstance(schedule_obj, dict):
                        return ToolResult.error_result(
                            "Schedule JSON must be an object.",
                            ToolErrorType.INVALID_PARAMS,
                        )
                    effective_kind = schedule_kind or ""
                    if effective_kind == "cron":
                        cron_expr = schedule_obj.get("cron", "")
                        if not cron_expr:
                            return ToolResult.error_result(
                                "Cron expression is required for cron schedule_kind",
                                ToolErrorType.INVALID_PARAMS,
                            )
                        try:
                            from croniter import croniter
                            croniter(cron_expr)
                        except ImportError:
                            if not _is_cron_expression(cron_expr):
                                return ToolResult.error_result(
                                    f"Invalid cron expression '{cron_expr}'",
                                    ToolErrorType.INVALID_PARAMS,
                                )
                        except (ValueError, KeyError) as e:
                            return ToolResult.error_result(
                                f"Invalid cron expression '{cron_expr}': {e}",
                                ToolErrorType.INVALID_PARAMS,
                            )
                        tz_for_cron = effective_timezone or "UTC"
                        next_run = _cron_next_run(cron_expr, tz_for_cron)
                        if next_run:
                            schedule_obj["_next_run"] = next_run
                    schedule_json = json.dumps(schedule_obj)
                except (json.JSONDecodeError, TypeError):
                    return ToolResult.error_result(f"Could not parse schedule: '{schedule_str}'")

        # Build action payload if message provided
        action_payload = None
        message = args.get("message")
        goal_title = args.get("goal_title")
        if message:
            action_payload = json.dumps({"message": message})
        elif goal_title:
            action_payload = json.dumps({"title": goal_title})

        # Build delivery if any delivery params provided
        delivery_json = None
        if any(args.get(k) for k in ("delivery_mode", "delivery_channel", "delivery_topic", "delivery_target_id", "delivery_webhook_url")):
            delivery = self._build_delivery(args)
            if delivery.get("mode") == "channel" and not delivery.get("target_id"):
                return ToolResult.error_result(
                    "delivery_target_id is required when delivery_mode is 'channel'",
                    ToolErrorType.INVALID_PARAMS,
                )
            if delivery.get("mode") == "webhook" and not delivery.get("url"):
                return ToolResult.error_result(
                    "delivery_webhook_url is required when delivery_mode is 'webhook'",
                    ToolErrorType.INVALID_PARAMS,
                )
            delivery_json = json.dumps(delivery)

        try:
            async with pool.acquire() as conn:
                result = await conn.fetchval(
                    """SELECT update_scheduled_task(
                        $1::uuid, $2, $3, $4, $5::jsonb,
                        $6, $7, $8::jsonb, $9, $10, $11::jsonb
                    )""",
                    task_id,
                    name,
                    description,
                    schedule_kind,
                    schedule_json,
                    timezone,
                    action_kind,
                    action_payload,
                    status,
                    max_runs,
                    delivery_json,
                )
            return ToolResult.success_result(
                {"task_id": task_id, "updated": True},
                display_output=f"Updated scheduled task {task_id[:8]}...",
            )
        except Exception as e:
            logger.error("Failed to update scheduled task: %s", e)
            return ToolResult.error_result(f"Failed to update scheduled task: {e}")

    async def _cancel(self, pool: "asyncpg.Pool", args: dict[str, Any]) -> ToolResult:
        task_id = (args.get("task_id") or "").strip()
        if not task_id:
            # Try to find by name
            name = (args.get("name") or "").strip()
            if name:
                try:
                    async with pool.acquire() as conn:
                        task_id = await conn.fetchval(
                            "SELECT id FROM scheduled_tasks WHERE name = $1 AND status = 'active' LIMIT 1",
                            name,
                        )
                        if not task_id:
                            return ToolResult.error_result(f"No active task found with name '{name}'")
                        task_id = str(task_id)
                except Exception as e:
                    return ToolResult.error_result(f"Failed to look up task: {e}")
            else:
                return ToolResult.error_result(
                    "task_id or name is required for cancel",
                    ToolErrorType.INVALID_PARAMS,
                )

        reason = args.get("description") or "Cancelled by agent"

        try:
            async with pool.acquire() as conn:
                ok = await conn.fetchval(
                    "SELECT delete_scheduled_task($1::uuid, FALSE, $2)",
                    task_id,
                    reason,
                )
            if ok:
                return ToolResult.success_result(
                    {"task_id": task_id, "cancelled": True},
                    display_output=f"Cancelled scheduled task {task_id[:8]}...",
                )
            return ToolResult.error_result(f"Task {task_id} not found")
        except Exception as e:
            logger.error("Failed to cancel scheduled task: %s", e)
            return ToolResult.error_result(f"Failed to cancel scheduled task: {e}")


    @staticmethod
    def _build_delivery(args: dict[str, Any]) -> dict[str, Any]:
        """Build a delivery JSONB from tool arguments."""
        mode = args.get("delivery_mode", "outbox")
        delivery: dict[str, Any] = {"mode": mode}
        if mode == "channel":
            if args.get("delivery_channel"):
                delivery["channel"] = args["delivery_channel"]
            if args.get("delivery_target_id"):
                delivery["target_id"] = args["delivery_target_id"]
            if args.get("delivery_topic"):
                delivery["topic"] = args["delivery_topic"]
        elif mode == "webhook":
            if args.get("delivery_webhook_url"):
                delivery["url"] = args["delivery_webhook_url"]
        return delivery

    async def _stats(self, pool: "asyncpg.Pool", args: dict[str, Any]) -> ToolResult:
        """Return execution statistics for scheduled tasks (D.3)."""
        task_id = (args.get("task_id") or "").strip()
        try:
            async with pool.acquire() as conn:
                if task_id:
                    # Stats for a specific task
                    row = await conn.fetchrow(
                        """SELECT name, schedule_kind, status, run_count, max_runs,
                                  last_run_at, last_error, next_run_at, created_at
                           FROM scheduled_tasks WHERE id = $1::uuid""",
                        task_id,
                    )
                    if not row:
                        return ToolResult.error_result(f"Task {task_id} not found")
                    return ToolResult.success_result({
                        "task_id": task_id,
                        "name": row["name"],
                        "schedule_kind": row["schedule_kind"],
                        "status": row["status"],
                        "run_count": row["run_count"],
                        "max_runs": row["max_runs"],
                        "last_run_at": str(row["last_run_at"]) if row["last_run_at"] else None,
                        "next_run_at": str(row["next_run_at"]) if row["next_run_at"] else None,
                        "last_error": row["last_error"],
                        "created_at": str(row["created_at"]),
                    })
                else:
                    # Aggregate stats
                    stats = await conn.fetchrow("""
                        SELECT
                            COUNT(*) FILTER (WHERE status = 'active') AS active_count,
                            COUNT(*) FILTER (WHERE status = 'paused') AS paused_count,
                            COUNT(*) FILTER (WHERE status = 'disabled') AS disabled_count,
                            SUM(run_count) AS total_runs,
                            COUNT(*) FILTER (WHERE last_error IS NOT NULL AND status = 'active') AS tasks_with_errors,
                            MAX(last_run_at) AS last_execution,
                            MIN(next_run_at) FILTER (WHERE status = 'active') AS next_execution
                        FROM scheduled_tasks
                    """)
                    # Recent gateway events for cron
                    recent = await conn.fetch("""
                        SELECT created_at, payload
                        FROM gateway_events
                        WHERE source = 'cron'
                        ORDER BY created_at DESC
                        LIMIT 10
                    """)
                    recent_runs = []
                    for r in recent:
                        payload = r["payload"]
                        if isinstance(payload, str):
                            try:
                                payload = json.loads(payload)
                            except Exception:
                                pass
                        recent_runs.append({
                            "at": str(r["created_at"]),
                            "tasks_executed": payload.get("executed") if isinstance(payload, dict) else None,
                        })
                    return ToolResult.success_result({
                        "active_tasks": stats["active_count"],
                        "paused_tasks": stats["paused_count"],
                        "disabled_tasks": stats["disabled_count"],
                        "total_executions": stats["total_runs"],
                        "tasks_with_errors": stats["tasks_with_errors"],
                        "last_execution": str(stats["last_execution"]) if stats["last_execution"] else None,
                        "next_execution": str(stats["next_execution"]) if stats["next_execution"] else None,
                        "recent_runs": recent_runs,
                    })
        except Exception as e:
            logger.error("Failed to get cron stats: %s", e)
            return ToolResult.error_result(f"Failed to get cron stats: {e}")


def create_cron_tools() -> list[ToolHandler]:
    """Create scheduled task management tools."""
    return [ManageScheduleHandler()]
