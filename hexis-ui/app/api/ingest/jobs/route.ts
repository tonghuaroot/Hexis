export const runtime = "nodejs";

/**
 * Ingestion job listing proxy: the Ingest page polls this for live receipts —
 * what ran, what's pending, what failed and why.
 */

const DEFAULT_UPSTREAM = "http://127.0.0.1:43817";

function resolveUpstreamUrl(pathname: string, search: string): string {
  const base =
    process.env.HEXIS_API_URL ||
    process.env.HEXIS_API_BASE_URL ||
    DEFAULT_UPSTREAM;
  const normalizedBase = base.endsWith("/") ? base : `${base}/`;
  const normalizedPath = pathname.replace(/^\//, "");
  return new URL(`${normalizedPath}${search}`, normalizedBase).toString();
}

export async function GET(request: Request): Promise<Response> {
  const { search } = new URL(request.url);
  try {
    const upstream = await fetch(resolveUpstreamUrl("/api/ingest/jobs", search), {
      cache: "no-store",
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
