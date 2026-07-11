export const runtime = "nodejs";

function upstreamBaseUrl(): string {
  return (
    process.env.HEXIS_API_URL ||
    process.env.HEXIS_API_BASE_URL ||
    "http://127.0.0.1:43817"
  );
}

export async function POST(): Promise<Response> {
  try {
    const headers = new Headers({ accept: "text/event-stream" });
    const apiKey = process.env.HEXIS_API_KEY?.trim();
    if (apiKey) headers.set("authorization", `Bearer ${apiKey}`);
    const upstream = await fetch(`${upstreamBaseUrl()}/api/heartbeat/run`, {
      method: "POST",
      headers,
      cache: "no-store",
    });
    return new Response(upstream.body, {
      status: upstream.status,
      headers: {
        "content-type": "text/event-stream",
        "cache-control": "no-cache",
        "x-accel-buffering": "no",
      },
    });
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : "Unable to reach the Hexis API.";
    return Response.json({ error: message }, { status: 502 });
  }
}
