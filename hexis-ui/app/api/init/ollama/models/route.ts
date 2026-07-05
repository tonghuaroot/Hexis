import { Ollama } from "ollama";

export const runtime = "nodejs";

const DEFAULT_HOST = "http://127.0.0.1:11434";

// Honor the endpoint the user typed in the init form (?endpoint=...), stripping
// any path so the base host is used. Falls back to env, then localhost.
function hostFromEndpoint(endpoint: string | null): string | null {
  if (!endpoint) return null;
  const m = endpoint.trim().match(/^(https?:\/\/[^/]+)/);
  return m ? m[1] : null;
}

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const host =
    hostFromEndpoint(searchParams.get("endpoint")) ||
    process.env.OLLAMA_HOST ||
    process.env.OLLAMA_URL ||
    DEFAULT_HOST;
  try {
    const client = new Ollama({ host });
    const response = await client.list();
    const models = Array.isArray(response?.models)
      ? response.models
          .map((model: any) => (typeof model?.name === "string" ? model.name : null))
          .filter((name: string | null) => name)
      : [];
    return Response.json({ models });
  } catch (err: any) {
    return Response.json(
      { models: [], error: err?.message || "Unable to reach Ollama." },
      { status: 503 }
    );
  }
}
