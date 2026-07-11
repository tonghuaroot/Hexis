import { NextResponse } from "next/server";

export const runtime = "nodejs";

type RouteContext = { params: Promise<{ path: string[] }> };

function upstreamUrl(path: string[], requestUrl: string): string {
  const base =
    process.env.HEXIS_API_URL ||
    process.env.HEXIS_API_BASE_URL ||
    "http://127.0.0.1:43817";
  const url = new URL(`/api/init/auth/${path.map(encodeURIComponent).join("/")}`, base);
  url.search = new URL(requestUrl).search;
  return url.toString();
}

async function proxy(request: Request, context: RouteContext): Promise<Response> {
  const { path } = await context.params;
  const headers = new Headers({ accept: "application/json" });
  const apiKey = process.env.HEXIS_API_KEY?.trim();
  if (apiKey) headers.set("authorization", `Bearer ${apiKey}`);

  let body: string | undefined;
  if (request.method !== "GET" && request.method !== "HEAD") {
    headers.set("content-type", request.headers.get("content-type") || "application/json");
    body = await request.text();
  }

  const url = upstreamUrl(path, request.url);
  try {
    const upstream = await fetch(url, {
      method: request.method,
      headers,
      body,
      cache: "no-store",
      signal: request.signal,
    });
    return new NextResponse(await upstream.text(), {
      status: upstream.status,
      headers: {
        "content-type": upstream.headers.get("content-type") || "application/json",
        "cache-control": "no-store",
      },
    });
  } catch (error: unknown) {
    const reason = error instanceof Error ? error.message : String(error);
    return NextResponse.json(
      {
        detail:
          `Unable to reach the Hexis API at ${url}. ` +
          `${reason || "Make sure the Hexis stack is running."}`,
      },
      { status: 503 }
    );
  }
}

export async function GET(request: Request, context: RouteContext) {
  return proxy(request, context);
}

export async function POST(request: Request, context: RouteContext) {
  return proxy(request, context);
}
