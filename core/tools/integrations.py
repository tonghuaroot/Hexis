"""Tools for first-class personal-data connector setup."""

from __future__ import annotations

import json
import re
from typing import Any

from .base import (
    ToolCategory,
    ToolContext,
    ToolErrorType,
    ToolExecutionContext,
    ToolHandler,
    ToolResult,
    ToolSpec,
)


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


_CHANNEL_CONNECTORS = {"slack", "telegram", "signal"}
_ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_SECRET_CHANNEL_KEYS = {"bot_token", "app_token", "access_token", "password"}


def _connector_id(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def _setup_next_step(plan: dict[str, Any]) -> str:
    manifest = plan.get("setup_manifest") if isinstance(plan, dict) else {}
    if not isinstance(manifest, dict):
        manifest = {}
    step = str(manifest.get("user_next_step") or "").strip()
    if step:
        return step
    notes = manifest.get("notes")
    if isinstance(notes, list) and notes:
        return " ".join(str(item) for item in notes if item)
    return "Follow the connector setup manifest, then verify the connection."


async def _connected_gmail_accounts(pool: Any) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT account_key, display_name, capabilities, granted_scopes, updated_at
            FROM integration_connections
            WHERE connector_id = 'gmail'
              AND status = 'connected'
            ORDER BY updated_at DESC, account_key
            """
        )
    accounts: list[dict[str, Any]] = []
    for row in rows:
        accounts.append(
            {
                "account_key": row["account_key"],
                "display_name": row["display_name"],
                "capabilities": _json(row["capabilities"]) or [],
                "granted_scopes": list(row["granted_scopes"] or []),
                "updated_at": row["updated_at"],
            }
        )
    return accounts


async def _resolve_gmail_account(pool: Any, requested: Any = None) -> str:
    account_key = str(requested or "").strip().lower()
    accounts = await _connected_gmail_accounts(pool)
    if account_key:
        if any(str(item.get("account_key") or "").lower() == account_key for item in accounts):
            return account_key
        raise ValueError(f"Gmail account is not connected: {account_key}")
    if not accounts:
        raise ValueError("Gmail is not connected. Use connect_gmail first.")
    if len(accounts) == 1:
        return str(accounts[0]["account_key"])
    choices = ", ".join(str(item["account_key"]) for item in accounts)
    raise ValueError(f"Multiple Gmail accounts are connected. Specify account_key. Connected: {choices}")


class IntegrationSetupStatusHandler(ToolHandler):
    """Inspect first-class connector setup state for any provider."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="integration_setup_status",
            description=(
                "Show connector setup state for Gmail, Slack, Telegram, Signal, Twitter/X, "
                "and other registered integrations. Does not expose secrets."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "connector_id": {
                        "type": "string",
                        "description": "Optional connector filter, such as gmail, slack, telegram, signal, or twitter_x.",
                    }
                },
            },
            category=ToolCategory.EXTERNAL,
            energy_cost=1,
            is_read_only=True,
            supports_parallel=True,
            allowed_contexts={ToolContext.CHAT, ToolContext.HEARTBEAT, ToolContext.MCP},
        )

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        if not context.registry:
            return ToolResult.error_result(
                "integration_setup_status requires an active tool registry.",
                ToolErrorType.EXECUTION_FAILED,
            )
        connector_id = _connector_id(arguments.get("connector_id")) or None
        async with context.registry.pool.acquire() as conn:
            raw = await conn.fetchval("SELECT integration_status($1)", connector_id)
            runtime_raw = await conn.fetchval(
                "SELECT list_channel_adapter_status($1)",
                connector_id if connector_id in _CHANNEL_CONNECTORS else None,
            )
        payload = _json(raw) or {}
        payload["channel_runtime"] = _json(runtime_raw) or []
        connectors = payload.get("connectors", [])
        connected = payload.get("connections", [])
        if connector_id and connectors:
            label = connectors[0].get("display_name") or connector_id
            status = connectors[0].get("status")
            count = len([item for item in connected if item.get("status") == "connected"])
            display = f"{label}: {status}; connected accounts: {count}"
            runtime = payload["channel_runtime"]
            if runtime:
                display += f"; adapter: {runtime[0].get('status')}"
        else:
            available = [item["id"] for item in connectors if item.get("status") == "available"]
            planned = [item["id"] for item in connectors if item.get("status") == "planned"]
            display = f"Available connectors: {', '.join(available) or 'none'}"
            if planned:
                display += f"; planned: {', '.join(planned)}"
        return ToolResult.success_result(payload, display_output=display)


class StartIntegrationSetupHandler(ToolHandler):
    """Start a DB-owned setup attempt for non-Gmail connectors."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="start_integration_setup",
            description=(
                "Start a first-class connector setup attempt for manual/pairing/API-key channels "
                "such as Slack, Telegram, or Signal. Gmail OAuth uses connect_gmail instead."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "connector_id": {
                        "type": "string",
                        "description": "Connector id, such as slack, telegram, signal, or twitter_x.",
                    },
                    "capabilities": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional requested capabilities. Defaults come from the DB connector manifest.",
                    },
                    "source_channel": {
                        "type": "string",
                        "description": "Optional source surface, such as cli, web, slack, telegram, or signal.",
                    },
                    "source_session_id": {
                        "type": "string",
                        "description": "Optional conversation/session identifier for resuming setup.",
                    },
                },
                "required": ["connector_id"],
            },
            category=ToolCategory.EXTERNAL,
            energy_cost=1,
            is_read_only=False,
            requires_approval=True,
            supports_parallel=False,
            allowed_contexts={ToolContext.CHAT, ToolContext.MCP},
        )

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        if not context.registry:
            return ToolResult.error_result(
                "start_integration_setup requires an active tool registry.",
                ToolErrorType.EXECUTION_FAILED,
            )
        connector_id = _connector_id(arguments.get("connector_id"))
        if connector_id == "gmail":
            return ToolResult.error_result(
                "Use connect_gmail for Gmail OAuth setup.",
                ToolErrorType.INVALID_PARAMS,
            )
        requested = arguments.get("capabilities")
        requested_json = json.dumps(requested) if requested is not None else None

        try:
            async with context.registry.pool.acquire() as conn:
                plan_raw = await conn.fetchval(
                    "SELECT prepare_connection_attempt($1, $2::jsonb)",
                    connector_id,
                    requested_json,
                )
                plan = _json(plan_raw) or {}
                next_step = _setup_next_step(plan)
                attempt_raw = await conn.fetchval(
                    """
                    SELECT start_connection_attempt(
                        $1,
                        $2::jsonb,
                        $3::text[],
                        $4::jsonb,
                        NULL,
                        $5,
                        $6,
                        $7,
                        NULL
                    )
                    """,
                    connector_id,
                    requested_json or json.dumps(plan.get("capabilities") or []),
                    [str(item) for item in plan.get("requested_scopes", [])],
                    json.dumps({"setup_kind": "manual_channel", "auth_type": plan.get("auth_type")}),
                    next_step,
                    arguments.get("source_channel"),
                    arguments.get("source_session_id") or context.session_id,
                )
        except Exception as exc:
            return ToolResult.error_result(str(exc), ToolErrorType.INVALID_PARAMS)

        payload = _json(attempt_raw) or {}
        payload["setup_plan"] = plan
        payload["next_step"] = payload.get("user_next_step") or next_step
        return ToolResult.success_result(payload, display_output=payload["next_step"])


class ConfigureChannelIntegrationHandler(ToolHandler):
    """Write non-secret channel connector config through the DB catalog."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="configure_channel_integration",
            description=(
                "Configure Slack, Telegram, or Signal channel settings using DB-owned channel config. "
                "Use env var names for tokens; do not paste token values because tool calls are audited."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "connector_id": {
                        "type": "string",
                        "description": "Channel connector id: slack, telegram, or signal.",
                    },
                    "settings": {
                        "type": "object",
                        "description": (
                            "Channel settings accepted by apply_channel_config. Token fields must be env var names, "
                            "for example {'bot_token': 'TELEGRAM_BOT_TOKEN'}."
                        ),
                    },
                },
                "required": ["connector_id", "settings"],
            },
            category=ToolCategory.EXTERNAL,
            energy_cost=1,
            is_read_only=False,
            requires_approval=True,
            supports_parallel=False,
            allowed_contexts={ToolContext.CHAT, ToolContext.MCP},
        )

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        if not context.registry:
            return ToolResult.error_result(
                "configure_channel_integration requires an active tool registry.",
                ToolErrorType.EXECUTION_FAILED,
            )
        connector_id = _connector_id(arguments.get("connector_id"))
        if connector_id not in _CHANNEL_CONNECTORS:
            return ToolResult.error_result(
                "configure_channel_integration supports slack, telegram, and signal.",
                ToolErrorType.INVALID_PARAMS,
            )
        settings = arguments.get("settings")
        if not isinstance(settings, dict) or not settings:
            return ToolResult.error_result("settings must be a non-empty object.", ToolErrorType.INVALID_PARAMS)

        for key, value in settings.items():
            if key in _SECRET_CHANNEL_KEYS:
                if not isinstance(value, str) or not _ENV_NAME_RE.fullmatch(value):
                    return ToolResult.error_result(
                        f"{key} must be an environment variable name, not a token value.",
                        ToolErrorType.INVALID_PARAMS,
                    )

        try:
            async with context.registry.pool.acquire() as conn:
                raw = await conn.fetchval(
                    "SELECT apply_channel_config($1, $2::jsonb)",
                    connector_id,
                    json.dumps(settings),
                )
        except Exception as exc:
            return ToolResult.error_result(str(exc), ToolErrorType.INVALID_PARAMS)

        payload = _json(raw) or {}
        return ToolResult.success_result(
            payload,
            display_output=f"{connector_id} channel config applied: {', '.join(payload.get('applied', []))}.",
        )


class VerifyChannelIntegrationHandler(ToolHandler):
    """Verify channel config and mark the connector connected."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="verify_channel_integration",
            description=(
                "Verify that Slack, Telegram, or Signal channel config resolves the credentials expected "
                "by the channel worker, then mark the connector connected in the DB."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "connector_id": {
                        "type": "string",
                        "description": "Channel connector id: slack, telegram, or signal.",
                    },
                    "attempt_id": {
                        "type": "string",
                        "description": "Optional setup attempt ID. Defaults to latest active attempt for the connector.",
                    },
                    "account_key": {
                        "type": "string",
                        "description": "Optional redacted account key. Defaults to channel:<connector_id>.",
                    },
                },
                "required": ["connector_id"],
            },
            category=ToolCategory.EXTERNAL,
            energy_cost=1,
            is_read_only=False,
            requires_approval=True,
            supports_parallel=False,
            allowed_contexts={ToolContext.CHAT, ToolContext.MCP},
        )

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        if not context.registry:
            return ToolResult.error_result(
                "verify_channel_integration requires an active tool registry.",
                ToolErrorType.EXECUTION_FAILED,
            )
        connector_id = _connector_id(arguments.get("connector_id"))
        if connector_id not in _CHANNEL_CONNECTORS:
            return ToolResult.error_result(
                "verify_channel_integration supports slack, telegram, and signal.",
                ToolErrorType.INVALID_PARAMS,
            )

        from services.channel_worker import _is_channel_configured, _load_channel_config

        async with context.registry.pool.acquire() as conn:
            config = await _load_channel_config(conn, connector_id)
            configured = _is_channel_configured(connector_id, config)
            if not configured:
                raw_status = await conn.fetchval("SELECT integration_status($1)", connector_id)
                status = _json(raw_status) or {}
                connectors = status.get("connectors") or []
                plan = connectors[0].get("setup_manifest", {}) if connectors else {}
                next_step = plan.get("user_next_step") or "Configure the channel token env vars, then verify again."
                return ToolResult.error_result(next_step, ToolErrorType.MISSING_CONFIG)

            attempt_id = str(arguments.get("attempt_id") or "").strip()
            if not attempt_id:
                attempt_id = await conn.fetchval(
                    """
                    SELECT id::text
                    FROM connection_attempts
                    WHERE connector_id = $1
                      AND status IN ('pending_user', 'awaiting_input', 'error')
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    connector_id,
                )
            if not attempt_id:
                started = _json(await conn.fetchval(
                    """
                    SELECT start_connection_attempt(
                        $1,
                        NULL,
                        ARRAY[]::text[],
                        '{"setup_kind": "verified_existing_channel"}'::jsonb,
                        NULL,
                        'Existing channel configuration verified.',
                        'chat',
                        $2,
                        NULL
                    )
                    """,
                    connector_id,
                    context.session_id,
                ))
                attempt_id = started["attempt_id"]

            plan = _json(await conn.fetchval(
                "SELECT prepare_connection_attempt($1, NULL)",
                connector_id,
            )) or {}
            account_key = str(arguments.get("account_key") or "").strip() or f"channel:{connector_id}"
            if connector_id == "signal":
                try:
                    from channels.signal_adapter import _resolve_token

                    phone = _resolve_token(config)
                    if phone:
                        account_key = phone
                except Exception:
                    pass

            completed = _json(await conn.fetchval(
                """
                SELECT complete_connection_attempt(
                    $1::uuid,
                    $2,
                    $3,
                    $4,
                    $5::text[],
                    $6::jsonb,
                    $7::jsonb
                )
                """,
                attempt_id,
                account_key,
                plan.get("display_name") or connector_id,
                f"config:channel.{connector_id}",
                [str(item) for item in plan.get("requested_scopes", [])],
                json.dumps(plan.get("capabilities") or []),
                json.dumps({
                    "verified_by": "verify_channel_integration",
                    "channel_config_keys": sorted(config.keys()),
                    "runtime": "hexis-channels",
                    "secret_values_stored": False,
                }),
            ))

        completed["next_step"] = "Start or restart hexis-channels if the adapter is not already running."
        return ToolResult.success_result(
            completed,
            display_output=f"{plan.get('display_name') or connector_id} verified. {completed['next_step']}",
        )


class GmailSetupStatusHandler(ToolHandler):
    """Inspect Gmail connector setup state without exposing secrets."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="gmail_setup_status",
            description=(
                "Show Gmail connector setup status, granted capabilities, pending OAuth attempts, "
                "and whether local credential files exist. Does not expose secrets."
            ),
            parameters={"type": "object", "properties": {}},
            category=ToolCategory.EMAIL,
            energy_cost=1,
            is_read_only=True,
            supports_parallel=True,
            allowed_contexts={ToolContext.CHAT, ToolContext.HEARTBEAT, ToolContext.MCP},
        )

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        if not context.registry:
            return ToolResult.error_result(
                "Gmail setup status requires an active tool registry.",
                ToolErrorType.EXECUTION_FAILED,
            )
        from core.auth.google_gmail import (
            has_saved_gmail_client_secret,
            load_default_credentials,
        )

        async with context.registry.pool.acquire() as conn:
            raw = await conn.fetchval("SELECT integration_status('gmail')")
        payload = _json(raw) or {}
        payload["client_secret_saved"] = has_saved_gmail_client_secret()
        payload["credentials_saved"] = load_default_credentials() is not None

        connected = [
            item
            for item in payload.get("connections", [])
            if isinstance(item, dict) and item.get("status") == "connected"
        ]
        pending = [
            item
            for item in payload.get("recent_attempts", [])
            if isinstance(item, dict) and item.get("status") in {"pending_user", "awaiting_input", "error"}
        ]
        display = "Gmail: "
        if connected:
            accounts = ", ".join(str(item.get("account_key")) for item in connected)
            display += f"connected ({accounts})"
        elif pending:
            display += "setup pending"
        elif payload["client_secret_saved"]:
            display += "OAuth client saved; no account connected"
        else:
            display += "not connected"

        return ToolResult.success_result(payload, display_output=display)


class ConnectGmailHandler(ToolHandler):
    """Start a Gmail OAuth connection attempt."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="connect_gmail",
            description=(
                "Start the Gmail connector OAuth setup flow. Use when the user asks to connect Gmail, "
                "authorize email reading/search/ingestion, label or spam triage, or sending/replying. "
                "Prefer client_secret_path over pasted client JSON because tool arguments are audited."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "capabilities": {
                        "type": "array",
                        "items": {
                            "type": "string",
                        },
                        "description": (
                            "Gmail grants to request. Default is read/search/ingest. "
                            "Only include label/spam_triage/send/reply when the user asked for those powers. "
                            "The database connector manifest validates names, aliases, and required scopes."
                        ),
                    },
                    "client_secret_path": {
                        "type": "string",
                        "description": "Local path to the Google OAuth Desktop client JSON file.",
                    },
                    "use_env_client_secret": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "Use GOOGLE_GMAIL_CLIENT_SECRET_PATH/JSON or GOOGLE_CLIENT_SECRET_PATH/JSON. "
                            "Only set true after the user explicitly asks to use environment-provided client credentials."
                        ),
                    },
                    "source_channel": {
                        "type": "string",
                        "description": "Optional source surface, such as cli, web, slack, telegram, or signal.",
                    },
                    "source_session_id": {
                        "type": "string",
                        "description": "Optional conversation/session identifier for resuming setup.",
                    },
                },
            },
            category=ToolCategory.EMAIL,
            energy_cost=1,
            is_read_only=False,
            requires_approval=True,
            supports_parallel=False,
            allowed_contexts={ToolContext.CHAT, ToolContext.MCP},
        )

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        if not context.registry:
            return ToolResult.error_result(
                "connect_gmail requires an active tool registry.",
                ToolErrorType.EXECUTION_FAILED,
            )
        from core.auth.google_gmail import GmailOAuthError, GmailOAuthStart, start_gmail_oauth

        try:
            started = await start_gmail_oauth(
                context.registry.pool,
                capabilities=arguments.get("capabilities"),
                client_secret_path=arguments.get("client_secret_path"),
                use_env_client_secret=bool(arguments.get("use_env_client_secret", False)),
                source_channel=arguments.get("source_channel"),
                source_session_id=arguments.get("source_session_id") or context.session_id,
            )
        except GmailOAuthError as exc:
            return ToolResult.error_result(str(exc), ToolErrorType.MISSING_CONFIG)

        if isinstance(started, dict):
            return ToolResult.success_result(
                started,
                display_output=started.get("next_step"),
            )

        assert isinstance(started, GmailOAuthStart)
        payload = started.attempt_payload
        display = (
            "Gmail authorization started.\n"
            f"Attempt: {payload['attempt_id']}\n"
            f"{payload['authorization_url']}\n\n"
            "After approving, paste the full redirected localhost URL back here."
        )
        return ToolResult.success_result(payload, display_output=display)


class CompleteGmailConnectionHandler(ToolHandler):
    """Complete a pending Gmail OAuth attempt from the pasted redirect URL."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="complete_gmail_connection",
            description=(
                "Complete Gmail OAuth setup after the user pastes the full redirected localhost URL "
                "or authorization code from Google."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "authorization_response": {
                        "type": "string",
                        "description": "Full redirected URL or raw authorization code.",
                    },
                    "attempt_id": {
                        "type": "string",
                        "description": "Optional Gmail connection attempt ID. Defaults to latest pending Gmail attempt.",
                    },
                },
                "required": ["authorization_response"],
            },
            category=ToolCategory.EMAIL,
            energy_cost=1,
            is_read_only=False,
            requires_approval=True,
            supports_parallel=False,
            allowed_contexts={ToolContext.CHAT, ToolContext.MCP},
        )

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        if not context.registry:
            return ToolResult.error_result(
                "complete_gmail_connection requires an active tool registry.",
                ToolErrorType.EXECUTION_FAILED,
            )
        from core.auth.google_gmail import GmailOAuthError, complete_gmail_oauth

        try:
            completed = await complete_gmail_oauth(
                context.registry.pool,
                authorization_response=str(arguments.get("authorization_response") or ""),
                attempt_id=arguments.get("attempt_id"),
            )
        except GmailOAuthError as exc:
            return ToolResult.error_result(str(exc), ToolErrorType.AUTH_FAILED)

        output = {
            "connector_id": "gmail",
            "status": "connected",
            "account_key": completed.account_key,
            "display_name": completed.display_name,
            "credential_ref": completed.credential_ref,
            "granted_scopes": completed.granted_scopes,
            "capabilities": completed.capabilities,
        }
        return ToolResult.success_result(
            output,
            display_output=f"Gmail connected for {completed.display_name}.",
        )


class RevokeGmailConnectionHandler(ToolHandler):
    """Disconnect Gmail locally and mark DB connection state revoked."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="revoke_gmail_connection",
            description=(
                "Disconnect Gmail from Hexis by deleting the local credential file and marking the "
                "connection revoked. The user can also remove the Google-side grant in their Google Account permissions."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "account_key": {
                        "type": "string",
                        "description": "Optional Gmail account key/email. If omitted, revokes local default Gmail credentials.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Optional user-visible revocation reason.",
                    },
                },
            },
            category=ToolCategory.EMAIL,
            energy_cost=1,
            is_read_only=False,
            requires_approval=True,
            supports_parallel=False,
            allowed_contexts={ToolContext.CHAT, ToolContext.MCP},
        )

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        if not context.registry:
            return ToolResult.error_result(
                "revoke_gmail_connection requires an active tool registry.",
                ToolErrorType.EXECUTION_FAILED,
            )
        from core.auth.google_gmail import delete_default_credentials

        delete_default_credentials()
        async with context.registry.pool.acquire() as conn:
            raw = await conn.fetchval(
                "SELECT revoke_integration_connection('gmail', $1, $2)",
                arguments.get("account_key"),
                arguments.get("reason") or "revoked by user request",
            )
        payload = _json(raw) or {}
        payload["local_credentials_deleted"] = True
        payload["remote_revocation"] = "not_attempted"
        payload["next_step"] = (
            "Local Gmail credentials are removed. To remove the Google-side OAuth grant too, "
            "open your Google Account security settings and remove Hexis/your OAuth client from third-party access."
        )
        return ToolResult.success_result(
            payload,
            display_output="Gmail disconnected locally.",
        )


class StartGmailBackfillHandler(ToolHandler):
    """Queue a DB-owned Gmail history backfill."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="start_gmail_backfill",
            description=(
                "Queue Gmail message history ingestion into raw source documents and the memory ingestion queue. "
                "Use after Gmail is connected when the user asks Hexis to learn from email, import email history, "
                "or ingest a Gmail search/label. This only reads Gmail; message sending, replying, labeling, "
                "and spam actions require separate tools and explicit authorization."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "account_key": {
                        "type": "string",
                        "description": "Connected Gmail account/email. Required only when multiple Gmail accounts are connected.",
                    },
                    "query": {
                        "type": "string",
                        "description": "Optional Gmail search query, such as newer_than:30d or from:alice@example.com.",
                    },
                    "label_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional Gmail labels to restrict the backfill, such as INBOX or SENT.",
                    },
                    "include_spam_trash": {
                        "type": "boolean",
                        "default": False,
                        "description": "Include spam and trash in Gmail list results.",
                    },
                    "max_messages": {
                        "type": "integer",
                        "default": 100,
                        "minimum": 1,
                        "maximum": 500,
                        "description": "Maximum messages to fetch in this job chunk.",
                    },
                    "page_size": {
                        "type": "integer",
                        "default": 100,
                        "minimum": 1,
                        "maximum": 100,
                        "description": "Gmail API page size for this job.",
                    },
                    "source_channel": {
                        "type": "string",
                        "description": "Optional source surface, such as cli, web, slack, telegram, or signal.",
                    },
                    "source_session_id": {
                        "type": "string",
                        "description": "Optional conversation/session identifier.",
                    },
                },
            },
            category=ToolCategory.EMAIL,
            energy_cost=2,
            is_read_only=False,
            requires_approval=True,
            supports_parallel=False,
            allowed_contexts={ToolContext.CHAT, ToolContext.MCP},
        )

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        if not context.registry:
            return ToolResult.error_result(
                "start_gmail_backfill requires an active tool registry.",
                ToolErrorType.EXECUTION_FAILED,
            )
        from core.auth.google_gmail import load_default_credentials

        if load_default_credentials() is None:
            return ToolResult.error_result(
                "Gmail credentials are not saved locally. Use connect_gmail first.",
                ToolErrorType.AUTH_FAILED,
            )
        try:
            account_key = await _resolve_gmail_account(
                context.registry.pool,
                arguments.get("account_key"),
            )
        except ValueError as exc:
            return ToolResult.error_result(str(exc), ToolErrorType.INVALID_PARAMS)

        requested_range = {
            "query": str(arguments.get("query") or "").strip() or None,
            "label_ids": arguments.get("label_ids") or [],
            "include_spam_trash": bool(arguments.get("include_spam_trash", False)),
            "max_messages": arguments.get("max_messages", 100),
            "page_size": arguments.get("page_size", 100),
        }
        metadata = {
            "source_channel": arguments.get("source_channel"),
            "source_session_id": arguments.get("source_session_id") or context.session_id,
            "queued_by_tool": "start_gmail_backfill",
        }
        async with context.registry.pool.acquire() as conn:
            raw = await conn.fetchval(
                """
                SELECT enqueue_connector_backfill_job(
                    'gmail',
                    $1,
                    'messages',
                    $2::jsonb,
                    $3::jsonb
                )
                """,
                account_key,
                json.dumps({k: v for k, v in requested_range.items() if v not in (None, [], "")}),
                json.dumps({k: v for k, v in metadata.items() if v not in (None, "")}),
            )
        payload = _json(raw) or {}
        verb = "already queued" if payload.get("existing") else "queued"
        return ToolResult.success_result(
            payload,
            display_output=(
                f"Gmail backfill {verb} for {account_key}. "
                "The maintenance worker will fetch messages and queue ingestion."
            ),
        )


class GmailBackfillStatusHandler(ToolHandler):
    """Inspect DB-owned Gmail backfill status."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="gmail_backfill_status",
            description="Show Gmail backfill jobs, cursors, and raw source-item counts.",
            parameters={
                "type": "object",
                "properties": {
                    "account_key": {
                        "type": "string",
                        "description": "Optional connected Gmail account/email to filter status.",
                    },
                },
            },
            category=ToolCategory.EMAIL,
            energy_cost=1,
            is_read_only=True,
            supports_parallel=True,
            allowed_contexts={ToolContext.CHAT, ToolContext.HEARTBEAT, ToolContext.MCP},
        )

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        if not context.registry:
            return ToolResult.error_result(
                "gmail_backfill_status requires an active tool registry.",
                ToolErrorType.EXECUTION_FAILED,
            )
        async with context.registry.pool.acquire() as conn:
            raw = await conn.fetchval(
                "SELECT get_connector_backfill_status('gmail', $1)",
                arguments.get("account_key"),
            )
        payload = _json(raw) or {}
        jobs = payload.get("jobs") if isinstance(payload.get("jobs"), list) else []
        item_counts = payload.get("item_counts") if isinstance(payload.get("item_counts"), list) else []
        active = [job for job in jobs if isinstance(job, dict) and job.get("status") in {"pending", "in_progress", "paused"}]
        total_items = sum(int(item.get("count") or 0) for item in item_counts if isinstance(item, dict))
        return ToolResult.success_result(
            payload,
            display_output=f"Gmail backfill: {len(active)} active jobs, {total_items} source items.",
        )


class ControlGmailBackfillHandler(ToolHandler):
    """Pause, resume, or cancel a queued Gmail backfill job."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="control_gmail_backfill",
            description="Pause, resume, or cancel a Gmail backfill job by job_id.",
            parameters={
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description": "Connector backfill job ID from gmail_backfill_status.",
                    },
                    "action": {
                        "type": "string",
                        "enum": ["pause", "resume", "cancel"],
                        "description": "Control action to apply.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Optional human-readable reason for pause/cancel.",
                    },
                },
                "required": ["job_id", "action"],
            },
            category=ToolCategory.EMAIL,
            energy_cost=1,
            is_read_only=False,
            requires_approval=True,
            supports_parallel=False,
            allowed_contexts={ToolContext.CHAT, ToolContext.MCP},
        )

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        if not context.registry:
            return ToolResult.error_result(
                "control_gmail_backfill requires an active tool registry.",
                ToolErrorType.EXECUTION_FAILED,
            )
        action = str(arguments.get("action") or "").strip().lower()
        job_id = str(arguments.get("job_id") or "").strip()
        if action not in {"pause", "resume", "cancel"}:
            return ToolResult.error_result("action must be pause, resume, or cancel.", ToolErrorType.INVALID_PARAMS)
        statement = {
            "pause": "SELECT pause_connector_backfill_job($1::uuid, $2)",
            "resume": "SELECT resume_connector_backfill_job($1::uuid)",
            "cancel": "SELECT cancel_connector_backfill_job($1::uuid, $2)",
        }[action]
        async with context.registry.pool.acquire() as conn:
            if action == "resume":
                raw = await conn.fetchval(statement, job_id)
            else:
                raw = await conn.fetchval(statement, job_id, arguments.get("reason"))
        payload = _json(raw) or {}
        return ToolResult.success_result(
            payload,
            display_output=f"Gmail backfill {action}: {payload.get('status', 'unknown')}.",
        )


class ConnectorActionPolicyStatusHandler(ToolHandler):
    """List DB-owned connector action policies."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="connector_action_policy_status",
            description=(
                "List connector action policies that authorize sends, replies, label/spam actions, "
                "or other external provider state changes."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "connector_id": {
                        "type": "string",
                        "description": "Optional connector filter, such as gmail, slack, telegram, or email.",
                    },
                    "account_key": {
                        "type": "string",
                        "description": "Optional provider account filter.",
                    },
                    "include_inactive": {
                        "type": "boolean",
                        "default": False,
                        "description": "Include revoked/expired policies.",
                    },
                },
            },
            category=ToolCategory.EXTERNAL,
            energy_cost=1,
            is_read_only=True,
            supports_parallel=True,
            allowed_contexts={ToolContext.CHAT, ToolContext.HEARTBEAT, ToolContext.MCP},
        )

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        if not context.registry:
            return ToolResult.error_result(
                "connector_action_policy_status requires an active tool registry.",
                ToolErrorType.EXECUTION_FAILED,
            )
        async with context.registry.pool.acquire() as conn:
            raw = await conn.fetchval(
                "SELECT list_connector_action_policies($1, $2, $3)",
                arguments.get("connector_id"),
                arguments.get("account_key"),
                bool(arguments.get("include_inactive", False)),
            )
        policies = _json(raw) or []
        return ToolResult.success_result(
            {"policies": policies, "count": len(policies)},
            display_output=f"Connector action policies: {len(policies)}.",
        )


class GrantConnectorActionPolicyHandler(ToolHandler):
    """Grant a DB-owned connector action policy."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="grant_connector_action_policy",
            description=(
                "Create a scoped connector action policy. Use only when the user explicitly authorizes "
                "a class of actions, such as allowing heartbeat to send Slack alerts to one channel or "
                "letting Gmail reply no-thank-you to a constrained class of emails."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "connector_id": {
                        "type": "string",
                        "description": "Connector/provider id, such as gmail, slack, telegram, discord, or email.",
                    },
                    "action_kind": {
                        "type": "string",
                        "description": "Action kind, such as send, reply, label, spam_triage, mark_read, or delete.",
                    },
                    "account_key": {
                        "type": "string",
                        "description": "Optional provider account/email this policy applies to.",
                    },
                    "constraints": {
                        "type": "object",
                        "description": (
                            "Policy constraints. Supported keys include allowed_targets, denied_targets, "
                            "allowed_recipients, denied_recipients, and max_per_day."
                        ),
                    },
                    "allow_autonomous": {
                        "type": "boolean",
                        "default": False,
                        "description": "Allow non-chat contexts such as heartbeat to use this policy.",
                    },
                    "requires_per_action_approval": {
                        "type": "boolean",
                        "default": True,
                        "description": "Require explicit approval for each non-chat action unless false.",
                    },
                    "contexts": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["chat", "heartbeat", "mcp"]},
                        "description": "Contexts where this policy can apply.",
                    },
                    "expires_at": {
                        "type": "string",
                        "description": "Optional ISO timestamp after which this policy expires.",
                    },
                    "rationale": {
                        "type": "string",
                        "description": "Short human-readable reason for the grant.",
                    },
                    "source_session_id": {
                        "type": "string",
                        "description": "Optional source conversation/session identifier.",
                    },
                },
                "required": ["connector_id", "action_kind"],
            },
            category=ToolCategory.EXTERNAL,
            energy_cost=1,
            is_read_only=False,
            requires_approval=True,
            supports_parallel=False,
            allowed_contexts={ToolContext.CHAT, ToolContext.MCP},
        )

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        if not context.registry:
            return ToolResult.error_result(
                "grant_connector_action_policy requires an active tool registry.",
                ToolErrorType.EXECUTION_FAILED,
            )
        constraints = arguments.get("constraints")
        if constraints is None:
            constraints = {}
        if not isinstance(constraints, dict):
            return ToolResult.error_result("constraints must be an object.", ToolErrorType.INVALID_PARAMS)
        contexts = arguments.get("contexts")
        if contexts is not None and not isinstance(contexts, list):
            return ToolResult.error_result("contexts must be an array.", ToolErrorType.INVALID_PARAMS)
        async with context.registry.pool.acquire() as conn:
            raw = await conn.fetchval(
                """
                SELECT grant_connector_action_policy(
                    $1,
                    $2,
                    $3,
                    $4::jsonb,
                    $5,
                    $6,
                    $7::text[],
                    NULLIF($8, '')::timestamptz,
                    $9,
                    $10,
                    'user'
                )
                """,
                arguments.get("connector_id"),
                arguments.get("action_kind"),
                arguments.get("account_key"),
                json.dumps(constraints),
                bool(arguments.get("allow_autonomous", False)),
                bool(arguments.get("requires_per_action_approval", True)),
                [str(item) for item in contexts] if contexts is not None else None,
                str(arguments.get("expires_at") or ""),
                arguments.get("source_session_id") or context.session_id,
                arguments.get("rationale"),
            )
        payload = _json(raw) or {}
        return ToolResult.success_result(
            payload,
            display_output=(
                f"Connector action policy granted: {payload.get('connector_id')}/"
                f"{payload.get('action_kind')} ({payload.get('policy_id')})."
            ),
        )


class RevokeConnectorActionPolicyHandler(ToolHandler):
    """Revoke a DB-owned connector action policy."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="revoke_connector_action_policy",
            description="Revoke a connector action policy by policy_id.",
            parameters={
                "type": "object",
                "properties": {
                    "policy_id": {
                        "type": "string",
                        "description": "Policy ID from connector_action_policy_status.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Optional human-readable revocation reason.",
                    },
                },
                "required": ["policy_id"],
            },
            category=ToolCategory.EXTERNAL,
            energy_cost=1,
            is_read_only=False,
            requires_approval=True,
            supports_parallel=False,
            allowed_contexts={ToolContext.CHAT, ToolContext.MCP},
        )

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        if not context.registry:
            return ToolResult.error_result(
                "revoke_connector_action_policy requires an active tool registry.",
                ToolErrorType.EXECUTION_FAILED,
            )
        async with context.registry.pool.acquire() as conn:
            raw = await conn.fetchval(
                "SELECT revoke_connector_action_policy($1::uuid, $2)",
                arguments.get("policy_id"),
                arguments.get("reason"),
            )
        payload = _json(raw) or {}
        return ToolResult.success_result(
            payload,
            display_output=f"Connector action policy revoke: {payload.get('status', 'unknown')}.",
        )


def create_integration_tools() -> list[ToolHandler]:
    return [
        IntegrationSetupStatusHandler(),
        StartIntegrationSetupHandler(),
        ConfigureChannelIntegrationHandler(),
        VerifyChannelIntegrationHandler(),
        GmailSetupStatusHandler(),
        ConnectGmailHandler(),
        CompleteGmailConnectionHandler(),
        RevokeGmailConnectionHandler(),
        StartGmailBackfillHandler(),
        GmailBackfillStatusHandler(),
        ControlGmailBackfillHandler(),
        ConnectorActionPolicyStatusHandler(),
        GrantConnectorActionPolicyHandler(),
        RevokeConnectorActionPolicyHandler(),
    ]
