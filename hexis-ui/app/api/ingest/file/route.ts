export const runtime = "nodejs";

/**
 * File-upload ingestion proxy. Dropped/picked files in the chat composer and
 * the Ingest page land here as multipart form data and are forwarded to the
 * Python `hexis-api` server (`POST /api/ingest/file`), which preserves the
 * original bytes as a source artifact and enqueues a durable ingestion job.
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
  let form: FormData;
  try {
    form = await request.formData();
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    return Response.json({ error: message || "Failed to read upload." }, { status: 400 });
  }

  try {
    const upstream = await fetch(resolveUpstreamUrl("/api/ingest/file"), {
      method: "POST",
      body: form,
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
