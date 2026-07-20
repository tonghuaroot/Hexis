import { NextResponse } from "next/server";

import { normalizeJsonValue } from "@/lib/db";
import { prisma } from "@/lib/prisma";

export const runtime = "nodejs";

type Row = Record<string, unknown>;
const DEFAULT_UPSTREAM = "http://127.0.0.1:43817";

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

function resolveUpstreamUrl(pathname: string): string {
  const base =
    process.env.HEXIS_API_URL ||
    process.env.HEXIS_API_BASE_URL ||
    DEFAULT_UPSTREAM;
  const normalizedBase = base.endsWith("/") ? base : `${base}/`;
  const normalizedPath = pathname.replace(/^\//, "");
  return new URL(normalizedPath, normalizedBase).toString();
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
  let bodyText = "";
  try {
    bodyText = await request.text();
  } catch (error: unknown) {
    return NextResponse.json(
      { error: errorMessage(error) || "Failed to read integration action body." },
      { status: 400 }
    );
  }

  try {
    const headers: HeadersInit = { "Content-Type": "application/json" };
    const apiKey = process.env.HEXIS_API_KEY;
    if (apiKey) headers.Authorization = `Bearer ${apiKey}`;
    const upstream = await fetch(resolveUpstreamUrl("/api/integrations/action"), {
      method: "POST",
      headers,
      body: bodyText,
    });
    const payload = await upstream.text();
    return new Response(payload, {
      status: upstream.status,
      headers: { "Content-Type": "application/json" },
    });
  } catch (error: unknown) {
    console.error("Integration setup API error:", error);
    return NextResponse.json(
      { error: `Integration upstream unreachable: ${errorMessage(error)}` },
      { status: 502 }
    );
  }
}
