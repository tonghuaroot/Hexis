export const runtime = "nodejs";

/**
 * URL ingestion proxy: forwards to the Python `hexis-api` server
 * (`POST /api/ingest/url`), which enqueues a durable fetch+ingest job.
 */

const DEFAULT_UPSTREAM = "http://127.0.0.1:43817";

function resolveUpstreamUrl(pathname: string): string {
  const base =
    process.env.HEXIS_API_URL ||
    process.env.HEXIS_API_BASE_URL ||
    DEFAULT_UPSTREAM;
  const normalizedBase = base.endsWith("/") ? base : `${base}/`;
  const normalizedPath = pathname.replace(/^\//, "");
  return new URL(normalizedPath, normalizedBase).toString();
}

export async function POST(request: Request): Promise<Response> {
  let bodyText = "";
  try {
    bodyText = await request.text();
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    return Response.json({ error: message || "Failed to read request body." }, { status: 400 });
  }

  try {
    const upstream = await fetch(resolveUpstreamUrl("/api/ingest/url"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: bodyText,
    });
    const payload = await upstream.text();
    return new Response(payload, {
      status: upstream.status,
      headers: { "Content-Type": "application/json" },
    });
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    return Response.json(
      { error: `Ingest upstream unreachable: ${message}` },
      { status: 502 }
    );
  }
}
