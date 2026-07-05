import { NextResponse } from "next/server";

export const runtime = "nodejs";

function upstreamBaseUrl(): string {
  return (
    process.env.HEXIS_API_URL ||
    process.env.HEXIS_API_BASE_URL ||
    "http://127.0.0.1:43817"
  );
}

// Owner override: activate the agent even though the model didn't consent.
// Consent is a signal, not a lock — it's the owner's AI.
export async function POST(request: Request) {
  const body = await request.json().catch(() => ({}));

  try {
    const res = await fetch(`${upstreamBaseUrl()}/api/init/consent/override`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body ?? {}),
      cache: "no-store",
    });

    const text = await res.text();
    return new NextResponse(text, {
      status: res.status,
      headers: {
        "content-type": res.headers.get("content-type") || "application/json",
      },
    });
  } catch (err: unknown) {
    console.error("Consent override proxy failed:", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Consent override failed" },
      { status: 500 }
    );
  }
}
