// Live per-provider model catalog for the init wizard.
//
// Mirrors apps/tui/model_catalog.py: prefer a live, auto-updating source over a
// hand-maintained list, treat it as advisory, and always allow a free-typed id.
//   * cloud providers -> the models.dev catalog (one no-auth JSON), cached ~24h
//     in-memory and sorted newest-first
//   * ollama -> the local daemon's /api/tags at the user-provided endpoint
//   * on any failure -> a short curated fallback; the model field stays free-text

export const runtime = "nodejs";

const MODELS_DEV_URL = "https://models.dev/api.json";
const CACHE_TTL_MS = 24 * 3600 * 1000;
const TIMEOUT_MS = 12000;

// Hexis provider id -> models.dev slug. Ollama is special-cased (local fetch).
const PROVIDER_SLUG: Record<string, string> = {
  openai: "openai",
  "openai-codex": "openai",
  anthropic: "anthropic",
  "anthropic-oauth": "anthropic",
  grok: "xai",
  gemini: "google",
  chutes: "chutes",
  "github-copilot": "github-copilot",
  "qwen-portal": "alibaba",
  "minimax-portal": "minimax",
  "google-gemini-cli": "google",
  "google-antigravity": "google",
};

// Non-chat model ids to hide from the dropdown (still typeable as free text).
const NON_CHAT_RE =
  /embed|tts|whisper|moderation|rerank|image|audio|video|dall.?e|imagen|veo|sora|guard|ocr|speech|transcrib/i;

// Variant/specialty suffixes that shouldn't be the *default* pick (still listed).
const DEFAULT_SKIP = [
  "-pro",
  "-mini",
  "-nano",
  "-lite",
  "preview",
  "-exp",
  "experimental",
  "thinking",
  "chat-latest",
  "non-reasoning",
  "multi-agent",
  "imagine",
  "-build",
  "deep-research",
  "realtime",
  "-audio",
  "-tts",
  "-image",
  "-high",
  "-low",
  "-search",
  "computer-use",
];

// Only where "newest non-variant flagship" isn't the right recommendation.
// Self-healing: ignored if not present in the live catalog.
const PREFERRED_DEFAULT: Record<string, string> = {
  "openai-codex": "gpt-5.2-codex",
};

// Minimal offline fallback if models.dev is unreachable.
const FALLBACK: Record<string, string[]> = {
  openai: ["gpt-5.2", "gpt-4o", "gpt-4o-mini"],
  "openai-codex": ["gpt-5.2", "gpt-5.2-codex"],
  anthropic: ["claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5"],
  grok: ["grok-4.3", "grok-3"],
  gemini: ["gemini-3-pro-preview", "gemini-2.5-flash"],
  chutes: ["deepseek-ai/DeepSeek-V3-0324"],
  "github-copilot": ["gpt-4o"],
};

let memCache: { data: any; ts: number } | null = null;

async function fetchWithTimeout(url: string): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    return await fetch(url, { signal: controller.signal, cache: "no-store" });
  } finally {
    clearTimeout(timer);
  }
}

async function modelsDev(): Promise<any> {
  if (memCache && Date.now() - memCache.ts < CACHE_TTL_MS) {
    return memCache.data;
  }
  const resp = await fetchWithTimeout(MODELS_DEV_URL);
  if (!resp.ok) throw new Error(`models.dev responded ${resp.status}`);
  const data = await resp.json();
  memCache = { data, ts: Date.now() };
  return data;
}

function sortKey(m: any): string {
  return (m?.last_updated as string) || (m?.release_date as string) || "";
}

// Extract text/chat model ids from a models.dev provider block, newest first.
function chatModels(block: any): string[] {
  const rows: [string, string][] = [];
  const models = block?.models || {};
  for (const key of Object.keys(models)) {
    const m = models[key];
    const mid = typeof m?.id === "string" ? m.id : "";
    if (!mid || NON_CHAT_RE.test(mid)) continue;
    const modal = m?.modalities;
    const outs = modal && typeof modal === "object" ? modal.output : null;
    if (Array.isArray(outs) && !outs.includes("text")) continue;
    rows.push([mid, sortKey(m)]);
  }
  // Newest first (stable sort preserves catalog order within equal keys).
  rows.sort((a, b) => (a[1] < b[1] ? 1 : a[1] > b[1] ? -1 : 0));
  const seen = new Set<string>();
  const ids: string[] = [];
  for (const [mid] of rows) {
    if (!seen.has(mid)) {
      seen.add(mid);
      ids.push(mid);
    }
  }
  return ids;
}

// Pick a sensible default from the live catalog (newest-first list).
function recommendedDefault(provider: string, models: string[]): string {
  if (models.length === 0) return "";
  const pref = PREFERRED_DEFAULT[provider];
  if (pref && models.includes(pref)) return pref;
  for (const mid of models) {
    const lower = mid.toLowerCase();
    if (!DEFAULT_SKIP.some((s) => lower.includes(s))) return mid;
  }
  return models[0];
}

async function ollamaModels(endpoint: string | null): Promise<string[]> {
  let host = "http://localhost:11434";
  if (endpoint) {
    const m = endpoint.trim().match(/^(https?:\/\/[^/]+)/);
    if (m) host = m[1];
  }
  const url = host.replace(/\/+$/, "") + "/api/tags";
  const resp = await fetchWithTimeout(url);
  if (!resp.ok) throw new Error(`Ollama responded ${resp.status}`);
  const data = await resp.json();
  const list = Array.isArray(data?.models) ? data.models : [];
  return list
    .map((m: any) => m?.name)
    .filter((n: any): n is string => typeof n === "string");
}

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const provider = (searchParams.get("provider") || "").trim();
  const endpoint = searchParams.get("endpoint");
  if (!provider) {
    return Response.json({ models: [], default: "", error: "Missing provider" }, { status: 400 });
  }

  if (provider === "ollama") {
    try {
      const models = await ollamaModels(endpoint);
      return Response.json({ models, default: models[0] || "" });
    } catch (err: any) {
      return Response.json(
        { models: [], default: "", error: err?.message || "Unable to reach Ollama." },
        { status: 503 }
      );
    }
  }

  const slug = PROVIDER_SLUG[provider];
  if (!slug) {
    const models = FALLBACK[provider] || [];
    return Response.json({ models, default: recommendedDefault(provider, models) });
  }

  try {
    const data = await modelsDev();
    const block = data?.[slug];
    let models = block ? chatModels(block) : [];
    if (models.length === 0) models = FALLBACK[provider] || [];
    return Response.json({ models, default: recommendedDefault(provider, models) });
  } catch (err: any) {
    // Degrade gracefully: fall back to a curated list, keep the field free-text.
    const models = FALLBACK[provider] || [];
    return Response.json({
      models,
      default: recommendedDefault(provider, models),
      error: err?.message || "Unable to load live model catalog.",
    });
  }
}
