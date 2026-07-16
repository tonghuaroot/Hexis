"""
Hexis Tools System - Personal CRM (Contacts)

Allows the agent to search, view, update, and merge contacts through
the standard tool_use interface. Wraps the database functions in
db/30_tables_contacts.sql.
"""

from __future__ import annotations

import json
import logging
from typing import Any, TYPE_CHECKING

from .base import (
    ToolCategory,
    ToolErrorType,
    ToolExecutionContext,
    ToolHandler,
    ToolResult,
    ToolSpec,
)

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)


async def _try_db_contact_tool(tool_name: str, arguments: dict[str, Any], context: ToolExecutionContext) -> ToolResult | None:
    pool = context.registry.pool if context.registry else None
    if not pool:
        return None
    try:
        async with pool.acquire() as conn:
            raw = await conn.fetchval(
                "SELECT execute_contact_tool($1::text, $2::jsonb)",
                tool_name,
                json.dumps(arguments),
            )
        payload = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(payload, dict) and "success" in payload:
            if payload.get("success"):
                return ToolResult.success_result(payload.get("output"), payload.get("display_output"))
            try:
                error_type = ToolErrorType(payload.get("error_type") or ToolErrorType.EXECUTION_FAILED.value)
            except ValueError:
                error_type = ToolErrorType.EXECUTION_FAILED
            return ToolResult.error_result(payload.get("error") or "Contact tool failed", error_type)
    except Exception:
        logger.debug("DB contact tool failed; falling back to compatibility path", exc_info=True)
    return None


def _contact_to_dict(row: Any) -> dict[str, Any]:
    """Convert a contacts row to a JSON-friendly dict."""
    return {
        "id": row["id"],
        "name": row["name"],
        "email": row.get("email"),
        "company": row.get("company"),
        "role": row.get("role"),
        "phone": row.get("phone"),
        "notes": row.get("notes"),
        "tags": list(row.get("tags") or []),
        "source": row.get("source"),
        "first_seen": str(row["first_seen"]) if row.get("first_seen") else None,
        "last_touch": str(row["last_touch"]) if row.get("last_touch") else None,
    }


class SearchContactsHandler(ToolHandler):
    """Search contacts by text query or list recent contacts."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="search_contacts",
            description=(
                "Search contacts in the CRM by name, email, company, or notes. "
                "If no query is provided, returns recently touched contacts."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (name, email, company, or keyword). Leave empty to list recent contacts.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results to return (default 20).",
                        "default": 20,
                    },
                },
                "required": [],
            },
            category=ToolCategory.MEMORY,
            energy_cost=1,
            is_read_only=True,
        )

    async def execute(
        self, arguments: dict[str, Any], context: ToolExecutionContext
    ) -> ToolResult:
        db_result = await _try_db_contact_tool("search_contacts", arguments, context)
        if db_result is not None:
            return db_result
        pool: asyncpg.Pool = context.registry.pool
        query = arguments.get("query", "").strip()
        limit = min(arguments.get("limit", 20), 100)

        try:
            if query:
                rows = await pool.fetch(
                    "SELECT * FROM search_contacts($1, $2)", query, limit,
                )
            else:
                rows = await pool.fetch(
                    "SELECT * FROM recent_contacts($1)", limit,
                )

            contacts = [_contact_to_dict(r) for r in rows]
            return ToolResult.success_result(
                {"count": len(contacts), "contacts": contacts},
                display_output=f"Found {len(contacts)} contact(s)",
            )
        except Exception as exc:
            logger.error("search_contacts failed: %s", exc)
            return ToolResult.error_result(str(exc))


class GetContactHandler(ToolHandler):
    """Get a specific contact by ID or email."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="get_contact",
            description="Get a specific contact by ID or email address.",
            parameters={
                "type": "object",
                "properties": {
                    "id": {
                        "type": "integer",
                        "description": "Contact ID.",
                    },
                    "email": {
                        "type": "string",
                        "description": "Contact email address.",
                    },
                },
                "required": [],
            },
            category=ToolCategory.MEMORY,
            energy_cost=0,
            is_read_only=True,
        )

    async def execute(
        self, arguments: dict[str, Any], context: ToolExecutionContext
    ) -> ToolResult:
        db_result = await _try_db_contact_tool("get_contact", arguments, context)
        if db_result is not None:
            return db_result
        pool: asyncpg.Pool = context.registry.pool
        contact_id = arguments.get("id")
        email = arguments.get("email", "").strip()

        try:
            if contact_id:
                row = await pool.fetchrow(
                    "SELECT * FROM contacts WHERE id = $1", int(contact_id),
                )
            elif email:
                row = await pool.fetchrow(
                    "SELECT * FROM get_contact_by_email($1)", email,
                )
            else:
                return ToolResult.error_result("Provide either 'id' or 'email'.")

            if row is None:
                return ToolResult.success_result({"found": False})

            contact = _contact_to_dict(row)
            return ToolResult.success_result({"found": True, "contact": contact})
        except Exception as exc:
            logger.error("get_contact failed: %s", exc)
            return ToolResult.error_result(str(exc))


class CreateContactHandler(ToolHandler):
    """Create a new contact."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="create_contact",
            description="Create a new contact in the CRM.",
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Contact's full name."},
                    "email": {"type": "string", "description": "Email address."},
                    "company": {"type": "string", "description": "Company or organization."},
                    "role": {"type": "string", "description": "Role or title (e.g. CEO, engineer)."},
                    "phone": {"type": "string", "description": "Phone number."},
                    "notes": {"type": "string", "description": "Free-form notes about this person."},
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Tags for categorization.",
                    },
                    "source": {"type": "string", "description": "Where this contact came from (email, calendar, manual)."},
                },
                "required": ["name"],
            },
            category=ToolCategory.MEMORY,
            energy_cost=1,
            is_read_only=False,
        )

    async def execute(
        self, arguments: dict[str, Any], context: ToolExecutionContext
    ) -> ToolResult:
        db_result = await _try_db_contact_tool("create_contact", arguments, context)
        if db_result is not None:
            return db_result
        pool: asyncpg.Pool = context.registry.pool
        name = arguments.get("name", "").strip()
        if not name:
            return ToolResult.error_result("Name is required.")

        try:
            contact_id = await pool.fetchval(
                "SELECT create_contact($1,$2,$3,$4,$5,$6,$7,$8)",
                name,
                arguments.get("email"),
                arguments.get("company"),
                arguments.get("role"),
                arguments.get("phone"),
                arguments.get("notes"),
                arguments.get("tags", []),
                arguments.get("source", "manual"),
            )
            return ToolResult.success_result(
                {"id": contact_id, "name": name},
                display_output=f"Created contact #{contact_id}: {name}",
            )
        except Exception as exc:
            logger.error("create_contact failed: %s", exc)
            return ToolResult.error_result(str(exc))


class UpdateContactHandler(ToolHandler):
    """Update an existing contact's fields."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="update_contact",
            description="Update an existing contact's fields. Only provided fields are changed.",
            parameters={
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "Contact ID to update."},
                    "name": {"type": "string", "description": "Updated name."},
                    "email": {"type": "string", "description": "Updated email."},
                    "company": {"type": "string", "description": "Updated company."},
                    "role": {"type": "string", "description": "Updated role."},
                    "phone": {"type": "string", "description": "Updated phone."},
                    "notes": {"type": "string", "description": "Updated or appended notes."},
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Replacement tag list.",
                    },
                },
                "required": ["id"],
            },
            category=ToolCategory.MEMORY,
            energy_cost=1,
            is_read_only=False,
        )

    async def execute(
        self, arguments: dict[str, Any], context: ToolExecutionContext
    ) -> ToolResult:
        db_result = await _try_db_contact_tool("update_contact", arguments, context)
        if db_result is not None:
            return db_result
        pool: asyncpg.Pool = context.registry.pool
        contact_id = arguments.get("id")
        if not contact_id:
            return ToolResult.error_result("Contact ID is required.")

        try:
            updated = await pool.fetchval(
                "SELECT update_contact($1,$2,$3,$4,$5,$6,$7,$8)",
                int(contact_id),
                arguments.get("name"),
                arguments.get("email"),
                arguments.get("company"),
                arguments.get("role"),
                arguments.get("phone"),
                arguments.get("notes"),
                arguments.get("tags"),
            )
            if not updated:
                return ToolResult.error_result(f"Contact #{contact_id} not found.")

            # Touch the contact
            await pool.execute("SELECT touch_contact($1)", int(contact_id))
            return ToolResult.success_result(
                {"id": contact_id, "updated": True},
                display_output=f"Updated contact #{contact_id}",
            )
        except Exception as exc:
            logger.error("update_contact failed: %s", exc)
            return ToolResult.error_result(str(exc))


class MergeContactsHandler(ToolHandler):
    """Merge two duplicate contacts into one."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="merge_contacts",
            description=(
                "Merge two duplicate contacts. Keeps the first contact "
                "and merges data from the second into it, then deletes the second."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "keep_id": {
                        "type": "integer",
                        "description": "ID of the contact to keep.",
                    },
                    "remove_id": {
                        "type": "integer",
                        "description": "ID of the duplicate contact to merge and delete.",
                    },
                },
                "required": ["keep_id", "remove_id"],
            },
            category=ToolCategory.MEMORY,
            energy_cost=2,
            is_read_only=False,
        )

    async def execute(
        self, arguments: dict[str, Any], context: ToolExecutionContext
    ) -> ToolResult:
        db_result = await _try_db_contact_tool("merge_contacts", arguments, context)
        if db_result is not None:
            return db_result
        pool: asyncpg.Pool = context.registry.pool
        keep_id = arguments.get("keep_id")
        remove_id = arguments.get("remove_id")

        if not keep_id or not remove_id:
            return ToolResult.error_result("Both keep_id and remove_id are required.")
        if keep_id == remove_id:
            return ToolResult.error_result("Cannot merge a contact with itself.")

        try:
            merged = await pool.fetchval(
                "SELECT merge_contacts($1, $2)",
                int(keep_id), int(remove_id),
            )
            if not merged:
                return ToolResult.error_result(f"Contact #{remove_id} not found.")

            return ToolResult.success_result(
                {"keep_id": keep_id, "removed_id": remove_id, "merged": True},
                display_output=f"Merged contact #{remove_id} into #{keep_id}",
            )
        except Exception as exc:
            logger.error("merge_contacts failed: %s", exc)
            return ToolResult.error_result(str(exc))


class IngestContactsFromEmailHandler(ToolHandler):
    """A.2: Extract contacts from recent emails and upsert into CRM."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="ingest_contacts_email",
            description=(
                "Scan recent emails and extract sender/recipient contacts. "
                "Creates new contacts or updates last_touch for existing ones. "
                "Use as a cron job to keep the CRM updated from email activity."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "max_emails": {
                        "type": "integer",
                        "default": 50,
                        "description": "Maximum emails to scan",
                    },
                    "since_days": {
                        "type": "integer",
                        "default": 7,
                        "description": "Only scan emails from the last N days",
                    },
                },
            },
            category=ToolCategory.MEMORY,
            energy_cost=3,
            is_read_only=False,
        )

    async def execute(
        self, arguments: dict[str, Any], context: ToolExecutionContext
    ) -> ToolResult:
        pool: asyncpg.Pool = context.registry.pool
        max_emails = arguments.get("max_emails", 50)

        # This tool orchestrates: it reads emails from the DB (if ingested)
        # or directly from email metadata, and upserts contacts.
        try:
            async with pool.acquire() as conn:
                # Look for recent episodic memories from email ingestion
                rows = await conn.fetch("""
                    SELECT source_attribution, created_at
                    FROM memories
                    WHERE type = 'episodic'
                      AND source_attribution->>'kind' = 'email'
                      AND created_at > now() - ($1 || ' days')::interval
                    ORDER BY created_at DESC
                    LIMIT $2
                """, str(arguments.get("since_days", 7)), max_emails)

                created = 0
                updated = 0
                for row in rows:
                    sa = row["source_attribution"]
                    if isinstance(sa, str):
                        sa = json.loads(sa)
                    if not isinstance(sa, dict):
                        continue
                    sender = sa.get("sender") or sa.get("from")
                    if not sender:
                        continue
                    # Try to extract name and email
                    import re
                    match = re.match(r"(.+?)\s*<(.+?)>", sender)
                    if match:
                        name, email = match.group(1).strip(), match.group(2).strip()
                    elif "@" in sender:
                        email = sender.strip()
                        name = email.split("@")[0]
                    else:
                        continue

                    # Check if contact exists
                    existing = await conn.fetchval(
                        "SELECT id FROM contacts WHERE email = $1", email
                    )
                    if existing:
                        await conn.execute(
                            "SELECT touch_contact($1)",
                            existing,
                        )
                        updated += 1
                    else:
                        await conn.fetchval(
                            "SELECT create_contact($1,$2,$3,$4,$5,$6,$7,$8)",
                            name, email, None, None, None, None, [], "email",
                        )
                        created += 1

            return ToolResult.success_result(
                {"created": created, "updated": updated, "emails_scanned": len(rows)},
                display_output=f"Contacts from email: {created} new, {updated} updated",
            )
        except Exception as exc:
            logger.error("ingest_contacts_email failed: %s", exc)
            return ToolResult.error_result(str(exc))


class IngestContactsFromCalendarHandler(ToolHandler):
    """A.3: Extract contacts from calendar event attendees into CRM."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="ingest_contacts_calendar",
            description=(
                "Extract attendees from recent calendar events and upsert into CRM. "
                "Creates new contacts or updates last_touch for existing ones."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "days_back": {
                        "type": "integer",
                        "default": 7,
                        "description": "Scan events from the last N days",
                    },
                },
            },
            category=ToolCategory.MEMORY,
            energy_cost=3,
            is_read_only=False,
        )

    async def execute(
        self, arguments: dict[str, Any], context: ToolExecutionContext
    ) -> ToolResult:
        pool: asyncpg.Pool = context.registry.pool

        try:
            async with pool.acquire() as conn:
                # Look for episodic memories from calendar events
                rows = await conn.fetch("""
                    SELECT content, source_attribution, metadata, created_at
                    FROM memories
                    WHERE type = 'episodic'
                      AND source_attribution->>'kind' = 'calendar'
                      AND created_at > now() - ($1 || ' days')::interval
                    ORDER BY created_at DESC
                    LIMIT 100
                """, str(arguments.get("days_back", 7)))

                created = 0
                updated = 0
                for row in rows:
                    sa = row["source_attribution"]
                    if isinstance(sa, str):
                        sa = json.loads(sa)
                    if not isinstance(sa, dict):
                        continue
                    attendees = sa.get("attendees") or []
                    if isinstance(attendees, str):
                        attendees = json.loads(attendees)
                    for att in attendees:
                        email = att.get("email", "") if isinstance(att, dict) else str(att)
                        if not email or "@" not in email:
                            continue
                        name = att.get("displayName", "") if isinstance(att, dict) else email.split("@")[0]
                        if not name:
                            name = email.split("@")[0]

                        existing = await conn.fetchval(
                            "SELECT id FROM contacts WHERE email = $1", email
                        )
                        if existing:
                            await conn.execute(
                                "SELECT touch_contact($1)",
                                existing,
                            )
                            updated += 1
                        else:
                            await conn.fetchval(
                                "SELECT create_contact($1,$2,$3,$4,$5,$6,$7,$8)",
                                name, email, None, None, None, None, [], "calendar",
                            )
                            created += 1

            return ToolResult.success_result(
                {"created": created, "updated": updated, "events_scanned": len(rows)},
                display_output=f"Contacts from calendar: {created} new, {updated} updated",
            )
        except Exception as exc:
            logger.error("ingest_contacts_calendar failed: %s", exc)
            return ToolResult.error_result(str(exc))


def create_contact_tools() -> list[ToolHandler]:
    """Create all CRM contact tool handlers."""
    return [
        SearchContactsHandler(),
        GetContactHandler(),
        CreateContactHandler(),
        UpdateContactHandler(),
        MergeContactsHandler(),
        IngestContactsFromEmailHandler(),
        IngestContactsFromCalendarHandler(),
    ]
