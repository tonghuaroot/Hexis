import { NextResponse } from "next/server";

import { normalizeJsonValue } from "@/lib/db";
import { prisma } from "@/lib/prisma";

export const runtime = "nodejs";

type Row = Record<string, unknown>;
const CHANNEL_CONNECTORS = new Set(["slack", "telegram", "signal"]);

function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Failed to fetch integration status";
}

function connectorId(value: unknown): string {
  return String(value || "").trim().toLowerCase().replace(/-/g, "_");
}

function setupNextStep(plan: Record<string, unknown>): string {
  const manifest = asRecord(plan.setup_manifest);
  if (typeof manifest.user_next_step === "string" && manifest.user_next_step.trim()) {
    return manifest.user_next_step.trim();
  }
  if (Array.isArray(manifest.notes) && manifest.notes.length > 0) {
    return manifest.notes.filter((item) => typeof item === "string").join(" ");
  }
  return "Follow the connector setup manifest, then verify the connection.";
}

export async function GET(): Promise<Response> {
  try {
    const rows = await prisma.$queryRawUnsafe<Row[]>(`
      SELECT
        integration_status(NULL) AS integration,
        list_channel_adapter_status(NULL) AS channel_runtime,
        get_connector_backfill_status(NULL, NULL) AS backfill
    `);
    const row = rows[0] ?? {};
    const integration = asRecord(normalizeJsonValue(row.integration));
    const backfill = asRecord(normalizeJsonValue(row.backfill));

    return NextResponse.json({
      connectors: asArray(integration.connectors),
      connections: asArray(integration.connections),
      recent_attempts: asArray(integration.recent_attempts),
      channel_runtime: asArray(normalizeJsonValue(row.channel_runtime)),
      backfill: {
        jobs: asArray(backfill.jobs),
        cursors: asArray(backfill.cursors),
        item_counts: asArray(backfill.item_counts),
      },
      generated_at: new Date().toISOString(),
    });
  } catch (error: unknown) {
    console.error("Integration status API error:", error);
    return NextResponse.json({ error: errorMessage(error) }, { status: 500 });
  }
}

export async function POST(request: Request): Promise<Response> {
  try {
    const body = asRecord(await request.json());
    const action = String(body.action || "").trim();
    if (action !== "start_setup") {
      return NextResponse.json({ error: `unknown action: ${action}` }, { status: 422 });
    }

    const id = connectorId(body.connector_id);
    if (!CHANNEL_CONNECTORS.has(id)) {
      return NextResponse.json(
        {
          error:
            id === "gmail"
              ? "Gmail OAuth setup uses the Gmail connector OAuth flow; this endpoint only starts manual channel setup."
              : "Web setup start currently supports Slack, Telegram, and Signal.",
        },
        { status: 422 }
      );
    }

    const requestedJson = Array.isArray(body.capabilities)
      ? JSON.stringify(body.capabilities)
      : null;
    const planRows = await prisma.$queryRawUnsafe<Row[]>(
      "SELECT prepare_connection_attempt($1, $2::jsonb) AS plan",
      id,
      requestedJson
    );
    const plan = asRecord(normalizeJsonValue(planRows[0]?.plan));
    const nextStep = setupNextStep(plan);
    const scopesJson = JSON.stringify(plan.requested_scopes || []);
    const attemptRows = await prisma.$queryRawUnsafe<Row[]>(
      `
      SELECT start_connection_attempt(
        $1,
        $2::jsonb,
        ARRAY(SELECT jsonb_array_elements_text($3::jsonb)),
        $4::jsonb,
        NULL,
        $5,
        'web',
        $6,
        NULL
      ) AS attempt
      `,
      id,
      requestedJson || JSON.stringify(plan.capabilities || []),
      scopesJson,
      JSON.stringify({ setup_kind: "manual_channel", auth_type: plan.auth_type }),
      nextStep,
      String(body.source_session_id || "web-connections")
    );
    const attempt = asRecord(normalizeJsonValue(attemptRows[0]?.attempt));

    return NextResponse.json({
      ...attempt,
      setup_plan: plan,
      next_step: attempt.user_next_step || nextStep,
    });
  } catch (error: unknown) {
    console.error("Integration setup API error:", error);
    return NextResponse.json({ error: errorMessage(error) }, { status: 500 });
  }
}
