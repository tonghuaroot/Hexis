import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { normalizeJsonValue } from "@/lib/db";

type DbRow = Record<string, unknown>;

/**
 * The web inbox: this dashboard's view of the async messaging abstraction.
 * The agent's outbox (RabbitMQ hexis.outbox) is her always-available way to
 * reach the user; the channel worker tees every user-bound message into
 * web_inbox (db/76), and this route serves that feed plus any resource
 * requests awaiting an operator decision.
 */
export async function GET() {
  try {
    const [inboxRows, requestRows] = await Promise.all([
      prisma.$queryRawUnsafe<DbRow[]>("SELECT get_web_inbox(30) AS feed"),
      prisma.$queryRawUnsafe<DbRow[]>(
        "SELECT list_resource_requests('pending', 20) AS requests"
      ),
    ]);
    const feed = normalizeJsonValue(inboxRows[0]?.feed) || {
      unread: 0,
      messages: [],
    };
    const pendingRequests = normalizeJsonValue(requestRows[0]?.requests) || [];
    return NextResponse.json({
      unread: Number(feed.unread || 0),
      messages: feed.messages || [],
      pending_requests: pendingRequests,
    });
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : "Failed to load inbox";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}

/** Mark inbox messages read: body { ids: string[] }. */
export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const ids = Array.isArray(body?.ids)
      ? body.ids.filter((id: unknown) => typeof id === "string" && id.length > 0)
      : [];
    if (ids.length === 0) {
      return NextResponse.json({ marked: 0 });
    }
    const rows = await prisma.$queryRawUnsafe<DbRow[]>(
      "SELECT mark_web_inbox_read(ARRAY(SELECT jsonb_array_elements_text($1::jsonb))::uuid[]) AS marked",
      JSON.stringify(ids)
    );
    return NextResponse.json({ marked: Number(rows[0]?.marked || 0) });
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : "Failed to mark read";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
