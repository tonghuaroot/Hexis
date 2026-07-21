"""Non-Gmail connector backfill adapters.

Postgres owns jobs, cursors, retries, and source-document receipts. This
module only performs provider I/O and converts provider messages into the
connector source-item contract.
"""

from __future__ import annotations

import json
import logging
import csv
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import httpx

from channels.base import parse_allowlist
from channels.slack_adapter import _resolve_token as _resolve_slack_token
from services.channel_worker import _load_channel_config

logger = logging.getLogger(__name__)

SLACK_API_BASE = "https://slack.com/api"
CHANNEL_BACKFILL_CONNECTORS = ("slack", "telegram", "signal", "twitter_x")


class ChannelBackfillError(RuntimeError):
    """Expected, DB-recorded channel backfill failure."""


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _json_dumps(value: Any) -> str:
    return json.dumps(value, default=str)


def _coerce_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)


def _requested_range(job: dict[str, Any]) -> dict[str, Any]:
    requested = _json(job.get("requested_range")) or {}
    return requested if isinstance(requested, dict) else {}


def estimate_channel_backfill(connector_id: str, requested_range: dict[str, Any] | None = None) -> dict[str, Any]:
    requested = requested_range if isinstance(requested_range, dict) else {}
    max_messages = _coerce_int(requested.get("max_messages"), 100, minimum=1, maximum=5000)
    page_size = _coerce_int(requested.get("page_size"), 100, minimum=1, maximum=500)
    pages = max(1, (max_messages + page_size - 1) // page_size)
    export_path = str(requested.get("export_path") or requested.get("import_path") or "").strip()
    connector_id = str(connector_id or "").strip().lower()
    if connector_id in {"slack", "gmail"}:
        return {
            "connector_id": connector_id,
            "provider_status": "api_backfill_available",
            "estimated_items": max_messages,
            "page_size": page_size,
            "estimated_pages": pages,
            "cost_class": "small" if max_messages <= 100 else "medium" if max_messages <= 1000 else "large",
        }
    if connector_id in {"telegram", "signal"}:
        return {
            "connector_id": connector_id,
            "provider_status": "local_export_import" if export_path else "export_required",
            "estimated_items": max_messages,
            "page_size": page_size,
            "estimated_pages": pages,
            "cost_class": "blocked_until_export" if not export_path else (
                "local_medium" if max_messages <= 1000 else "local_large"
            ),
            "requires_export_path": not bool(export_path),
        }
    return {
        "connector_id": connector_id,
        "provider_status": "planned" if connector_id == "twitter_x" else "unknown",
        "estimated_items": 0 if connector_id == "twitter_x" else max_messages,
        "estimated_pages": 0 if connector_id == "twitter_x" else pages,
        "cost_class": "unavailable" if connector_id == "twitter_x" else "unknown",
    }


async def _load_cursor_value(pool: Any, job: dict[str, Any]) -> dict[str, Any]:
    async with pool.acquire() as conn:
        raw = await conn.fetchval(
            """
            SELECT cursor_value
            FROM connector_sync_cursors
            WHERE connection_id = $1::uuid
              AND cursor_key = $2
            """,
            str(job.get("connection_id")),
            str(job.get("cursor_key") or "messages"),
        )
    value = _json(raw) or {}
    return value if isinstance(value, dict) else {}


async def _upsert_source_item(pool: Any, job: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    async with pool.acquire() as conn:
        raw = await conn.fetchval(
            """
            SELECT upsert_connector_source_item(
                $1,
                $2,
                $3,
                $4,
                $5,
                $6,
                $7,
                $8,
                $9::text[],
                $10::jsonb,
                $11::jsonb,
                $12::jsonb,
                $13,
                TRUE
            )
            """,
            str(job.get("connector_id")),
            str(job.get("account_key")),
            source["provider_item_id"],
            source["title"],
            source["content"],
            source["item_kind"],
            source.get("provider_thread_id"),
            source.get("item_timestamp"),
            source["labels"],
            _json_dumps(source["participants"]),
            _json_dumps(source["attachments"]),
            _json_dumps(source["metadata"]),
            source.get("sensitivity", "shared"),
        )
    return _json(raw) or {}


async def _update_progress(
    pool: Any,
    job_id: str,
    progress: dict[str, Any],
    cursor_value: dict[str, Any] | None,
    high_watermark: datetime | None,
) -> dict[str, Any]:
    async with pool.acquire() as conn:
        raw = await conn.fetchval(
            """
            SELECT update_connector_backfill_progress(
                $1::uuid,
                $2::jsonb,
                $3::jsonb,
                $4
            )
            """,
            job_id,
            _json_dumps(progress),
            _json_dumps(cursor_value) if cursor_value is not None else None,
            high_watermark,
        )
    return _json(raw) or {}


async def _complete_job(
    pool: Any,
    job_id: str,
    result: dict[str, Any],
    cursor_value: dict[str, Any] | None,
    high_watermark: datetime | None,
) -> dict[str, Any]:
    async with pool.acquire() as conn:
        raw = await conn.fetchval(
            """
            SELECT complete_connector_backfill_job(
                $1::uuid,
                $2::jsonb,
                $3::jsonb,
                $4
            )
            """,
            job_id,
            _json_dumps(result),
            _json_dumps(cursor_value) if cursor_value is not None else None,
            high_watermark,
        )
    return _json(raw) or {}


async def _fail_job(pool: Any, job_id: str, error: str) -> dict[str, Any]:
    async with pool.acquire() as conn:
        raw = await conn.fetchval("SELECT fail_connector_backfill_job($1::uuid, $2)", job_id, error[:2000])
    return _json(raw) or {}


def _slack_timestamp(ts: Any) -> datetime | None:
    try:
        seconds = float(str(ts))
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


def _slack_sensitivity(channel_id: str) -> str:
    if channel_id.startswith("D"):
        return "private"
    return "shared"


def slack_message_to_source_item(message: dict[str, Any], *, channel_id: str) -> dict[str, Any]:
    """Convert Slack conversations.history JSON to the DB source-item contract."""
    ts = str(message.get("ts") or "").strip()
    if not ts:
        raise ChannelBackfillError("Slack message payload is missing ts.")

    user_id = str(message.get("user") or message.get("bot_id") or message.get("username") or "unknown")
    text = str(message.get("text") or "").strip()
    thread_ts = str(message.get("thread_ts") or "").strip() or None
    subtype = str(message.get("subtype") or "").strip() or None

    attachments: list[dict[str, Any]] = []
    for item in message.get("files") or []:
        if not isinstance(item, dict):
            continue
        attachments.append(
            {
                "filename": item.get("name") or item.get("title") or item.get("id"),
                "mime_type": item.get("mimetype") or item.get("filetype") or "",
                "size": item.get("size") or 0,
                "platform_id": item.get("id"),
                "url": item.get("url_private_download") or item.get("url_private"),
            }
        )

    content_lines = [
        f"Slack channel: {channel_id}",
        f"Slack timestamp: {ts}",
        f"Sender: {user_id}",
    ]
    if thread_ts:
        content_lines.append(f"Thread: {thread_ts}")
    if subtype:
        content_lines.append(f"Subtype: {subtype}")
    content_lines.extend(["", "Message:", text or "(No text body)"])

    return {
        "provider_item_id": f"{channel_id}:{ts}",
        "title": f"Slack message in {channel_id}",
        "content": "\n".join(content_lines),
        "item_kind": "message",
        "provider_thread_id": thread_ts,
        "item_timestamp": _slack_timestamp(ts),
        "labels": ["slack", channel_id],
        "participants": [{"role": "sender", "id": user_id}],
        "attachments": attachments,
        "sensitivity": _slack_sensitivity(channel_id),
        "metadata": {
            "slack_channel_id": channel_id,
            "slack_ts": ts,
            "slack_thread_ts": thread_ts,
            "slack_subtype": subtype,
            "slack_raw_message": message,
        },
    }


def _coerce_export_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("value") or ""))
        return "".join(parts)
    return "" if value is None else str(value)


def _stable_fragment_id(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def _parse_export_timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 10_000_000_000:
            seconds = seconds / 1000.0
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return _parse_export_timestamp(int(text))
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _iter_telegram_messages(payload: Any) -> Iterable[tuple[str, dict[str, Any]]]:
    if isinstance(payload, dict) and isinstance(payload.get("messages"), list):
        yield str(payload.get("name") or payload.get("id") or "telegram_export"), payload
        return
    chats = payload.get("chats") if isinstance(payload, dict) else None
    chat_list = chats.get("list") if isinstance(chats, dict) else None
    if isinstance(chat_list, list):
        for chat in chat_list:
            if isinstance(chat, dict) and isinstance(chat.get("messages"), list):
                yield str(chat.get("name") or chat.get("id") or "telegram_chat"), chat


def telegram_export_message_to_source_item(
    message: dict[str, Any],
    *,
    chat_label: str,
    account_key: str,
    export_path: str,
) -> dict[str, Any] | None:
    message_id = str(message.get("id") or message.get("message_id") or "").strip()
    text = _coerce_export_text(message.get("text") or message.get("text_entities") or message.get("message"))
    if not message_id and not text:
        return None
    sender = str(message.get("from") or message.get("from_id") or message.get("actor") or "unknown")
    timestamp = _parse_export_timestamp(message.get("date_unixtime") or message.get("date"))
    provider_id = f"{chat_label}:{message_id or _stable_fragment_id(text)}"
    content = "\n".join([
        f"Telegram chat: {chat_label}",
        f"Sender: {sender}",
        f"Export: {export_path}",
        "",
        "Message:",
        text or "(No text body)",
    ])
    return {
        "provider_item_id": provider_id,
        "title": f"Telegram message in {chat_label}",
        "content": content,
        "item_kind": "message",
        "provider_thread_id": chat_label,
        "item_timestamp": timestamp,
        "labels": ["telegram", chat_label],
        "participants": [{"role": "sender", "id": sender}],
        "attachments": [],
        "sensitivity": "private",
        "metadata": {
            "telegram_chat": chat_label,
            "telegram_message_id": message_id,
            "telegram_raw_message": message,
            "export_path": export_path,
            "account_key": account_key,
        },
    }


def _iter_signal_rows(path: Path, payload: Any) -> Iterable[dict[str, Any]]:
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                yield item
        return
    if isinstance(payload, dict):
        for key in ("messages", "conversations", "items"):
            values = payload.get(key)
            if isinstance(values, list):
                for item in values:
                    if isinstance(item, dict):
                        yield item
                return
    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            yield from reader


def signal_export_message_to_source_item(
    message: dict[str, Any],
    *,
    account_key: str,
    export_path: str,
) -> dict[str, Any] | None:
    message_id = str(message.get("id") or message.get("message_id") or message.get("timestamp") or "").strip()
    text = str(message.get("text") or message.get("body") or message.get("message") or "").strip()
    if not message_id and not text:
        return None
    sender = str(message.get("sender") or message.get("from") or message.get("source") or "unknown")
    conversation = str(message.get("conversation") or message.get("thread") or message.get("recipient") or "signal_export")
    timestamp = _parse_export_timestamp(
        message.get("timestamp")
        or message.get("timestamp_ms")
        or message.get("sent_at")
        or message.get("date")
    )
    provider_id = f"{conversation}:{message_id or _stable_fragment_id(text)}"
    content = "\n".join([
        f"Signal conversation: {conversation}",
        f"Sender: {sender}",
        f"Export: {export_path}",
        "",
        "Message:",
        text or "(No text body)",
    ])
    return {
        "provider_item_id": provider_id,
        "title": f"Signal message in {conversation}",
        "content": content,
        "item_kind": "message",
        "provider_thread_id": conversation,
        "item_timestamp": timestamp,
        "labels": ["signal", conversation],
        "participants": [{"role": "sender", "id": sender}],
        "attachments": [],
        "sensitivity": "private",
        "metadata": {
            "signal_conversation": conversation,
            "signal_message_id": message_id,
            "signal_raw_message": message,
            "export_path": export_path,
            "account_key": account_key,
        },
    }


async def _slack_get(token: str, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
    url = path if path.startswith("http") else f"{SLACK_API_BASE}{path}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers={"Authorization": f"Bearer {token}"}, params=params)
    if resp.status_code < 200 or resp.status_code >= 300:
        raise ChannelBackfillError(f"Slack API failed: HTTP {resp.status_code}: {resp.text}")
    payload = resp.json()
    if not isinstance(payload, dict):
        raise ChannelBackfillError("Slack API returned an invalid payload.")
    if not payload.get("ok", False):
        raise ChannelBackfillError(f"Slack API failed: {payload.get('error') or 'unknown_error'}")
    return payload


def _slack_request_options(job: dict[str, Any], cursor_value: dict[str, Any]) -> dict[str, Any]:
    requested = _requested_range(job)
    channel_id = str(requested.get("channel_id") or requested.get("channel") or "").strip()
    if not channel_id:
        raise ChannelBackfillError("Slack backfill requires requested_range.channel_id.")
    return {
        "channel_id": channel_id,
        "oldest": str(requested.get("oldest") or "").strip() or None,
        "latest": str(requested.get("latest") or "").strip() or None,
        "inclusive": bool(requested.get("inclusive", False)),
        "max_messages": _coerce_int(requested.get("max_messages"), 100, minimum=1, maximum=500),
        "page_size": _coerce_int(requested.get("page_size"), 100, minimum=1, maximum=200),
        "cursor": (
            str(requested.get("cursor") or "").strip()
            or str(cursor_value.get("cursor") or "").strip()
            or None
        ),
    }


async def _load_slack_token(pool: Any, channel_id: str) -> str:
    async with pool.acquire() as conn:
        config = await _load_channel_config(conn, "slack")
    allowed_channels = parse_allowlist(config.get("allowed_channels"))
    if allowed_channels is not None and channel_id not in allowed_channels:
        raise ChannelBackfillError(
            f"Slack channel {channel_id} is not in channel.slack.allowed_channels."
        )
    token = _resolve_slack_token(config, "bot_token", "SLACK_BOT_TOKEN")
    if not token:
        raise ChannelBackfillError(
            "Slack bot token not found. Configure channel.slack.bot_token as an env var name "
            "or set SLACK_BOT_TOKEN, then verify Slack."
        )
    return token


async def process_slack_backfill_job(pool: Any, job: dict[str, Any]) -> dict[str, Any]:
    job = _json(job) or {}
    job_id = str(job.get("id") or "")
    if not job_id:
        raise ChannelBackfillError("Claimed connector backfill job is missing id.")

    try:
        cursor_value = await _load_cursor_value(pool, job)
        options = _slack_request_options(job, cursor_value)
        token = await _load_slack_token(pool, options["channel_id"])
        channel_id = str(options["channel_id"])
        max_messages = int(options["max_messages"])
        page_size = min(int(options["page_size"]), max_messages)
        cursor = options["cursor"]

        pages = 0
        items_seen = 0
        items_stored = 0
        high_watermark: datetime | None = None
        last_message_ts: str | None = cursor_value.get("last_message_ts")

        while items_seen < max_messages:
            params: dict[str, Any] = {
                "channel": channel_id,
                "limit": min(page_size, max_messages - items_seen),
                "inclusive": "true" if options["inclusive"] else "false",
            }
            if options["oldest"]:
                params["oldest"] = options["oldest"]
            if options["latest"]:
                params["latest"] = options["latest"]
            if cursor:
                params["cursor"] = cursor

            listed = await _slack_get(token, "/conversations.history", params=params)
            pages += 1
            messages = listed.get("messages") or []
            if not isinstance(messages, list):
                messages = []

            for message in messages:
                if items_seen >= max_messages or not isinstance(message, dict):
                    break
                source = slack_message_to_source_item(message, channel_id=channel_id)
                await _upsert_source_item(pool, job, source)
                items_seen += 1
                items_stored += 1
                last_message_ts = str(message.get("ts") or last_message_ts or "")
                timestamp = source.get("item_timestamp")
                if isinstance(timestamp, datetime) and (
                    high_watermark is None or timestamp > high_watermark
                ):
                    high_watermark = timestamp

            metadata = listed.get("response_metadata")
            if isinstance(metadata, dict):
                next_cursor = str(metadata.get("next_cursor") or "").strip() or None
            else:
                next_cursor = None
            cursor = next_cursor
            next_cursor_value = {
                "cursor": cursor,
                "channel_id": channel_id,
                "oldest": options["oldest"],
                "latest": options["latest"],
                "last_message_ts": last_message_ts,
            }
            progress = await _update_progress(
                pool,
                job_id,
                {
                    "pages": pages,
                    "items_seen": items_seen,
                    "items_stored": items_stored,
                    "truncated": bool(cursor),
                },
                next_cursor_value,
                high_watermark,
            )
            if progress.get("cancel_requested"):
                return await _fail_job(pool, job_id, "cancelled by request")
            if progress.get("pause_requested"):
                return await _fail_job(pool, job_id, "paused by request")
            if not cursor:
                break

        final_cursor = {
            "cursor": cursor,
            "channel_id": channel_id,
            "oldest": options["oldest"],
            "latest": options["latest"],
            "last_message_ts": last_message_ts,
        }
        return await _complete_job(
            pool,
            job_id,
            {
                "pages": pages,
                "items_seen": items_seen,
                "items_stored": items_stored,
                "truncated": bool(cursor),
                "next_cursor": cursor,
            },
            final_cursor,
            high_watermark,
        )
    except ChannelBackfillError as exc:
        logger.warning("Slack backfill job %s failed: %s", job_id, exc)
        return await _fail_job(pool, job_id, str(exc))
    except Exception as exc:
        logger.exception("Slack backfill job %s failed unexpectedly", job_id)
        return await _fail_job(pool, job_id, str(exc))


def _local_export_path(job: dict[str, Any]) -> Path | None:
    requested = _requested_range(job)
    raw_path = str(requested.get("export_path") or requested.get("import_path") or "").strip()
    if not raw_path:
        return None
    return Path(raw_path).expanduser()


def _load_json_or_csv_payload(path: Path) -> Any:
    if path.suffix.lower() == ".csv":
        return None
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


async def process_local_export_backfill_job(pool: Any, job: dict[str, Any], *, connector_id: str) -> dict[str, Any]:
    job = _json(job) or {}
    job_id = str(job.get("id") or "")
    if not job_id:
        raise ChannelBackfillError("Claimed connector backfill job is missing id.")
    path = _local_export_path(job)
    if path is None:
        return await _fail_job(pool, job_id, unsupported_backfill_message(connector_id))
    if not path.exists() or not path.is_file():
        return await _fail_job(pool, job_id, f"{connector_id} export file not found: {path}")

    requested = _requested_range(job)
    max_messages = _coerce_int(requested.get("max_messages"), 1000, minimum=1, maximum=5000)
    account_key = str(job.get("account_key") or "")
    items_seen = 0
    items_stored = 0
    high_watermark: datetime | None = None

    try:
        payload = _load_json_or_csv_payload(path)
        sources: list[dict[str, Any]] = []
        if connector_id == "telegram":
            for chat_label, chat in _iter_telegram_messages(payload):
                messages = chat.get("messages") if isinstance(chat, dict) else []
                if not isinstance(messages, list):
                    continue
                for message in messages:
                    if not isinstance(message, dict):
                        continue
                    source = telegram_export_message_to_source_item(
                        message,
                        chat_label=chat_label,
                        account_key=account_key,
                        export_path=str(path),
                    )
                    if source:
                        sources.append(source)
        elif connector_id == "signal":
            for message in _iter_signal_rows(path, payload):
                source = signal_export_message_to_source_item(
                    message,
                    account_key=account_key,
                    export_path=str(path),
                )
                if source:
                    sources.append(source)
        else:
            return await _fail_job(pool, job_id, unsupported_backfill_message(connector_id))

        for source in sources[:max_messages]:
            await _upsert_source_item(pool, job, source)
            items_seen += 1
            items_stored += 1
            timestamp = source.get("item_timestamp")
            if isinstance(timestamp, datetime) and (
                high_watermark is None or timestamp > high_watermark
            ):
                high_watermark = timestamp
            if items_seen % 100 == 0:
                progress = await _update_progress(
                    pool,
                    job_id,
                    {
                        "items_seen": items_seen,
                        "items_stored": items_stored,
                        "export_path": str(path),
                        "estimate": estimate_channel_backfill(connector_id, requested),
                    },
                    {"export_path": str(path), "items_seen": items_seen},
                    high_watermark,
                )
                if progress.get("cancel_requested"):
                    return await _fail_job(pool, job_id, "cancelled by request")
                if progress.get("pause_requested"):
                    return await _fail_job(pool, job_id, "paused by request")

        return await _complete_job(
            pool,
            job_id,
            {
                "items_seen": items_seen,
                "items_stored": items_stored,
                "export_path": str(path),
                "truncated": len(sources) > max_messages,
                "estimate": estimate_channel_backfill(connector_id, requested),
            },
            {"export_path": str(path), "items_seen": items_seen},
            high_watermark,
        )
    except ChannelBackfillError as exc:
        logger.warning("%s export import job %s failed: %s", connector_id, job_id, exc)
        return await _fail_job(pool, job_id, str(exc))
    except Exception as exc:
        logger.exception("%s export import job %s failed unexpectedly", connector_id, job_id)
        return await _fail_job(pool, job_id, str(exc))


def unsupported_backfill_message(connector_id: str) -> str:
    if connector_id == "telegram":
        return (
            "Telegram bots cannot retroactively fetch chat history through the Bot API. "
            "Use live ingestion going forward, or start a backfill with requested_range.export_path "
            "pointing at a Telegram JSON export."
        )
    if connector_id == "signal":
        return (
            "Signal history is not exposed retroactively through signal-cli-rest-api. "
            "Use live ingestion going forward, or start a backfill with requested_range.export_path "
            "pointing at a local Signal JSON/CSV export."
        )
    if connector_id == "twitter_x":
        return "Twitter/X OAuth and historical ingestion are still planned; no provider adapter is available."
    return f"{connector_id} historical backfill is not implemented."


async def process_channel_backfill_job(pool: Any, job: dict[str, Any]) -> dict[str, Any]:
    job = _json(job) or {}
    connector_id = str(job.get("connector_id") or "").strip().lower()
    job_id = str(job.get("id") or "")
    if connector_id == "slack":
        return await process_slack_backfill_job(pool, job)
    if connector_id in {"telegram", "signal"} and _local_export_path(job) is not None:
        return await process_local_export_backfill_job(pool, job, connector_id=connector_id)
    if job_id:
        return await _fail_job(pool, job_id, unsupported_backfill_message(connector_id))
    raise ChannelBackfillError("Claimed connector backfill job is missing id.")


async def run_channel_backfill_step(
    pool: Any,
    *,
    connectors: Iterable[str] = CHANNEL_BACKFILL_CONNECTORS,
    limit: int | None = None,
) -> int:
    """Claim and process due non-Gmail connector backfill jobs."""
    handled = 0
    for connector_id in connectors:
        connector_id = str(connector_id).strip().lower()
        if connector_id == "gmail":
            continue
        async with pool.acquire() as conn:
            raw = await conn.fetchval(
                "SELECT claim_connector_backfill_jobs_for($1, $2::int)",
                connector_id,
                limit,
            )
        jobs = _json(raw) or []
        if not isinstance(jobs, list):
            jobs = []
        for job in jobs:
            await process_channel_backfill_job(pool, job)
        handled += len(jobs)
    return handled
