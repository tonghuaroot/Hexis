"""Command-line I/O and reporting for Hexis Memory Exchange (HMX)."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
import tempfile
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg

from core.memory_exchange import (
    HmxAnalysisResult,
    HmxDryRunResult,
    HmxImportResult,
    HmxPolicyError,
    HmxSchemaError,
    HmxStagingResult,
    accept_staged_import,
    default_import_strategy,
    demote_staged_to_analysis,
    dry_run_hmx,
    export_hmx,
    import_hmx,
    iter_hmx_jsonl,
    parse_hmx_jsonl,
    pending_hmx_reviews,
    promote_analysis_to_staged,
    quote_staged_import,
    reject_staged_import,
    modify_staged_import,
)


def _csv(value: str | None) -> list[str] | None:
    if value is None:
        return None
    values = [part.strip() for part in value.split(",") if part.strip()]
    return values or []


def _section_csv(value: str | None) -> list[str] | None:
    values = _csv(value)
    return [item.replace("-", "_") for item in values] if values is not None else None


async def _connect(dsn: str, wait_seconds: int) -> asyncpg.Connection:
    deadline = time.monotonic() + max(wait_seconds, 1)
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return await asyncpg.connect(dsn, ssl=False, command_timeout=60.0)
        except Exception as exc:  # pragma: no cover - exact driver errors vary
            last_error = exc
            await asyncio.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
    raise TimeoutError(
        f"failed to connect to Postgres after {wait_seconds}s: {last_error}"
    )


def _timestamp(value: str | None, flag: str) -> datetime | None:
    if value is None:
        return None
    candidate = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise HmxPolicyError(
            f"{flag} must be an ISO 8601 date or timestamp; got {value!r}"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _load_document(path_value: str) -> dict[str, Any]:
    if path_value == "-":
        text = sys.stdin.read()
        label = "stdin"
    else:
        path = Path(path_value).expanduser()
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise HmxSchemaError(f"HMX file not found: {path}") from exc
        except OSError as exc:
            raise HmxSchemaError(f"could not read HMX file {path}: {exc}") from exc
        label = str(path)

    if not text.strip():
        raise HmxSchemaError(f"HMX input from {label} is empty")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return parse_hmx_jsonl(text.splitlines())
    if not isinstance(parsed, dict):
        raise HmxSchemaError(f"HMX input from {label} must be a JSON object")
    if parsed.get("record_type") == "envelope":
        return parse_hmx_jsonl(text.splitlines())
    return parsed


def _serialized_export(document: dict[str, Any], output_format: str) -> str:
    if output_format == "jsonl":
        return "\n".join(iter_hmx_jsonl(document)) + "\n"
    return json.dumps(document, indent=2, sort_keys=True, default=str) + "\n"


def _write_private_file(path_value: str, content: str, *, overwrite: bool) -> Path:
    path = Path(path_value).expanduser()
    parent = path.parent
    if not parent.exists():
        raise HmxPolicyError(
            f"output directory does not exist: {parent}. Create it explicitly and retry."
        )
    if path.exists() and not overwrite:
        raise HmxPolicyError(
            f"output file already exists: {path}. Choose another path or pass --overwrite."
        )

    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if overwrite:
            os.replace(temporary, path)
        else:
            try:
                os.link(temporary, path)
            except FileExistsError as exc:
                raise HmxPolicyError(
                    f"output file already exists: {path}. Choose another path or pass --overwrite."
                ) from exc
            os.unlink(temporary)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return path


def _apply_skips(document: dict[str, Any], skipped: list[str]) -> dict[str, Any]:
    prepared = copy.deepcopy(document)
    sections = prepared.get("sections")
    if not isinstance(sections, dict):
        return prepared
    for section in skipped:
        sections.pop(section, None)
    return prepared


def _print_dry_run(
    result: HmxDryRunResult, *, as_json: bool, skipped: list[str]
) -> None:
    payload = asdict(result)
    payload["skipped_sections"] = skipped
    if as_json:
        sys.stdout.write(
            json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n"
        )
        return

    from apps.cli_theme import console, make_table

    console.print(
        f"[heading]HMX import dry run[/heading]  [muted]{result.export_id}[/muted]"
    )
    console.print(
        f"Intent [accent]{result.intent}[/accent]  Strategy [accent]{result.strategy}[/accent]  "
        f"Target [teal]{result.target_state.get('state', 'unknown')}[/teal]"
    )
    table = make_table("Section", ("Would import", {"justify": "right"}))
    hidden = {"total_records", "invalid_records", "duplicate_memories"}
    for section, count in result.counts.items():
        if section not in hidden and count:
            table.add_row(section, str(count))
    console.print(table)
    console.print(
        f"New embedding work: [accent]{result.estimated_embedding_items}[/accent]  "
        f"Duplicates: [warn]{len(result.duplicate_refs)}[/warn]  "
        f"Invalid records: [warn]{result.counts.get('invalid_records', 0)}[/warn]"
    )
    if skipped:
        console.print(f"Skipped by operator: [muted]{', '.join(skipped)}[/muted]")
    for conflict in result.conflicts:
        label = "conflict" if conflict.get("code") == "duplicate_content" else "blocked"
        style = "warn" if label == "conflict" else "fail"
        console.print(
            f"[{style}]{label}:[/{style}] {conflict.get('code')} "
            f"{json.dumps(conflict, sort_keys=True)}"
        )
    for warning in result.warnings:
        console.print(
            f"[warn]warning:[/warn] {warning.get('code')} {warning.get('error', '')}"
        )
    if result.can_import:
        console.print("[ok]This import is permitted by the current HMX policy.[/ok]")
    else:
        console.print(
            "[fail]This import cannot run with the selected intent and strategy.[/fail]"
        )


def _print_import_result(
    result: HmxImportResult | HmxStagingResult | HmxAnalysisResult,
    *,
    as_json: bool,
    skipped: list[str],
) -> None:
    payload = asdict(result)
    payload["skipped_sections"] = skipped
    if as_json:
        sys.stdout.write(
            json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n"
        )
        return

    from apps.cli_theme import console, make_table

    if isinstance(result, HmxStagingResult):
        console.print(
            f"[ok]HMX import staged for review.[/ok] [muted]{result.export_id}[/muted]"
        )
        counts = result.staged
        footer = f"Pending review records: [accent]{len(result.staging_ids)}[/accent]"
    elif isinstance(result, HmxAnalysisResult):
        console.print(
            f"[ok]HMX import loaded into isolated analysis storage.[/ok] "
            f"[muted]{result.export_id}[/muted]"
        )
        counts = result.loaded
        footer = f"Analysis records: [accent]{len(result.analysis_ids)}[/accent]"
    else:
        console.print(
            f"[ok]HMX import complete.[/ok] [muted]{result.export_id}[/muted]"
        )
        counts = result.inserted
        footer = (
            f"Duplicates reused: [warn]{len(result.duplicate_refs)}[/warn]  "
            f"Warnings: [warn]{len(result.warnings)}[/warn]"
        )
    table = make_table("Section", ("Imported", {"justify": "right"}))
    for section, count in counts.items():
        table.add_row(section, str(count))
    console.print(table)
    console.print(footer)
    if skipped:
        console.print(f"Skipped by operator: [muted]{', '.join(skipped)}[/muted]")
    for warning in result.warnings:
        console.print(
            f"[warn]warning:[/warn] {warning.get('code')} {warning.get('error', '')}"
        )


async def run_export(dsn: str, args: Any) -> int:
    if args.include_raw:
        from apps.cli_theme import err_console

        err_console.print(
            "[warn]Raw units are sensitive source material; protect the exported file.[/warn]"
        )
    if args.redaction == "strict" and args.include_raw:
        raise HmxPolicyError("--redaction strict cannot be combined with --include-raw")
    since = _timestamp(args.since, "--since")
    until = _timestamp(args.until, "--until")
    if since and until and since > until:
        raise HmxPolicyError("--since must be earlier than or equal to --until")
    conn = await _connect(dsn, args.wait_seconds)
    try:
        document = await export_hmx(
            conn,
            intent=args.intent,
            include_protected=_section_csv(args.include_protected),
            include_raw_units=args.include_raw,
            include_config=args.include_config,
            include_in_flight_work=args.include_in_flight_work,
            include_audit_records=args.include_audit_records,
            types=_csv(args.types),
            since=since,
            until=until,
            redaction_policy=args.redaction,
        )
    finally:
        await conn.close()

    content = _serialized_export(document, args.format)
    if args.output in (None, "-"):
        sys.stdout.write(content)
    else:
        written = _write_private_file(args.output, content, overwrite=args.overwrite)
        from apps.cli_theme import console

        console.print(
            f"[ok]Exported HMX {document['hmx_version']} to {written}[/ok] "
            f"[muted]({document['statistics']['estimated_uncompressed_bytes']} bytes estimated)[/muted]"
        )
    return 0


async def run_import(dsn: str, args: Any) -> int:
    document = _load_document(args.path)
    intent = str(document.get("export_intent") or "")
    if args.confirm_intent and args.confirm_intent != intent:
        raise HmxPolicyError(
            f"intent confirmation mismatch: file declares {intent!r}, "
            f"but --confirm-intent was {args.confirm_intent!r}"
        )
    if not args.dry_run and not args.confirm_intent:
        raise HmxPolicyError(
            f"import requires --confirm-intent {intent}; run with --dry-run first to inspect it"
        )

    skipped = [
        section
        for section, enabled in (
            ("identity", args.skip_identity),
            ("worldview", args.skip_worldview),
            ("narrative", args.skip_narrative),
        )
        if enabled
    ]
    document = _apply_skips(document, skipped)
    strategy = (args.strategy or default_import_strategy(intent)).replace("-", "_")

    conn = await _connect(dsn, args.wait_seconds)
    try:
        forecast = await dry_run_hmx(conn, document, strategy=strategy)
        if args.dry_run:
            _print_dry_run(forecast, as_json=args.json, skipped=skipped)
            return 0 if forecast.can_import else 2
        if not forecast.can_import:
            _print_dry_run(forecast, as_json=args.json, skipped=skipped)
            raise HmxPolicyError(
                "import blocked by preflight; resolve the reported policy conflict or strategy before retrying"
            )
        result = await import_hmx(conn, document, strategy=strategy)
    finally:
        await conn.close()

    _print_import_result(result, as_json=args.json, skipped=skipped)
    return 0


async def run_review(dsn: str, args: Any) -> int:
    conn = await _connect(dsn, args.wait_seconds)
    try:
        if args.review_command in (None, "list"):
            result: Any = await pending_hmx_reviews(conn)
        elif args.review_command == "accept":
            result = asdict(
                await accept_staged_import(
                    conn, args.staging_id, rationale=args.rationale
                )
            )
        elif args.review_command == "reject":
            result = asdict(
                await reject_staged_import(
                    conn, args.staging_id, rationale=args.rationale
                )
            )
        elif args.review_command == "modify":
            try:
                changes = json.loads(args.changes)
            except json.JSONDecodeError as exc:
                raise HmxPolicyError(
                    f"--changes must be a JSON object: {exc.msg}"
                ) from exc
            if not isinstance(changes, dict):
                raise HmxPolicyError("--changes must be a JSON object")
            result = asdict(
                await modify_staged_import(
                    conn,
                    args.staging_id,
                    changes,
                    modification_kind=args.modification_kind,
                    rationale=args.rationale,
                )
            )
        elif args.review_command == "quote":
            result = asdict(
                await quote_staged_import(
                    conn, args.staging_id, rationale=args.rationale
                )
            )
        elif args.review_command == "promote":
            staging_id = await promote_analysis_to_staged(
                conn, args.analysis_id, rationale=args.rationale
            )
            result = {"decision": "promoted", "staging_id": staging_id}
        elif args.review_command == "demote":
            analysis_id = await demote_staged_to_analysis(
                conn, args.staging_id, rationale=args.rationale
            )
            result = {"decision": "demoted", "analysis_id": analysis_id}
        else:  # pragma: no cover - argparse constrains this
            raise HmxPolicyError(
                f"unknown import review command: {args.review_command}"
            )
    finally:
        await conn.close()

    if args.json:
        sys.stdout.write(
            json.dumps(result, indent=2, sort_keys=True, default=str) + "\n"
        )
    else:
        from apps.cli_theme import console

        if args.review_command in (None, "list"):
            console.print(
                f"[heading]Pending HMX review[/heading]  [accent]{result['total']}[/accent] records"
            )
            for item in result["records"]:
                console.print(
                    f"  [key]{item['id']}[/key]  {item['section']}  "
                    f"[muted]{item.get('source_ref') or ''}[/muted]"
                )
        else:
            console.print(
                f"[ok]HMX review decision complete:[/ok] {result['decision']}"
            )
            for key in ("staging_id", "analysis_id", "local_ref"):
                if result.get(key):
                    console.print(f"  [key]{key}:[/key] {result[key]}")
    return 0
