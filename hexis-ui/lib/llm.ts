import { prisma } from "@/lib/prisma";
import { normalizeJsonValue } from "@/lib/db";
import Anthropic from "@anthropic-ai/sdk";
import OpenAI from "openai";
import { GoogleGenAI } from "@google/genai";

export type LlmProviderName =
  | "openai"
  | "anthropic"
  | "grok"
  | "gemini"
  | "openai_compatible";

export type ResolvedLlmConfig = {
  provider: LlmProviderName;
  model: string;
  endpoint: string | null;
  apiKey: string | null;
};

/**
 * Read the conscious LLM config from DB + env and return a resolved config.
 */
export async function getConsciousLlmConfig(): Promise<ResolvedLlmConfig> {
  const rows = await prisma.$queryRaw<{ llm: unknown }[]>`
    SELECT get_config('llm.heartbeat') as llm
  `;
  const config = normalizeJsonValue(rows[0]?.llm) as any;
  if (!config?.provider || !config?.model) {
    throw new Error("LLM not configured. Complete the Models step first.");
  }
  const provider = (config.provider as string).toLowerCase() as LlmProviderName;
  const model = config.model as string;
  const endpoint =
    typeof config.endpoint === "string" && config.endpoint.trim()
      ? config.endpoint.trim()
      : null;
  const envKey = (process.env.HEXIS_LLM_CONSCIOUS_API_KEY ?? "").trim() || null;
  return { provider, model, endpoint, apiKey: envKey };
}

/**
 * Call an LLM with a system + user prompt pair and return text.
 * Uses the simplest completion path for each provider (no tool_use).
 */
export async function callLlm(params: {
  config: ResolvedLlmConfig;
  system: string;
  user: string;
  temperature?: number;
  maxTokens?: number;
  jsonMode?: boolean;
}): Promise<string> {
  const { config, system, user, temperature = 0.3, maxTokens = 4000 } = params;

  if (config.provider === "anthropic") {
    return callAnthropic({ ...config, system, user, temperature, maxTokens });
  }
  if (config.provider === "gemini") {
    return callGemini({ ...config, system, user, temperature, maxTokens });
  }
  // openai, grok, and openai_compatible use OpenAI-compatible API
  return callOpenAICompatible({
    ...config,
    system,
    user,
    temperature,
    maxTokens,
    jsonMode: params.jsonMode,
  });
}

async function callOpenAICompatible(params: {
  provider: LlmProviderName;
  model: string;
  endpoint: string | null;
  apiKey: string | null;
  system: string;
  user: string;
  temperature: number;
  maxTokens: number;
  jsonMode?: boolean;
}): Promise<string> {
  const baseURL =
    params.provider === "grok"
      ? "https://api.x.ai/v1"
      : params.endpoint || undefined;
  const client = new OpenAI({
    apiKey: params.apiKey || "local-key",
    baseURL,
  });
  const completion = await client.chat.completions.create({
    model: params.model,
    messages: [
      { role: "system", content: params.system },
      { role: "user", content: params.user },
    ],
    temperature: params.temperature,
    max_tokens: params.maxTokens,
    ...(params.jsonMode ? { response_format: { type: "json_object" } } : {}),
  });
  return completion.choices[0]?.message?.content ?? "";
}

async function callAnthropic(params: {
  apiKey: string | null;
  model: string;
  system: string;
  user: string;
  temperature: number;
  maxTokens: number;
}): Promise<string> {
  if (!params.apiKey) {
    throw new Error("Missing Anthropic API key");
  }
  const client = new Anthropic({ apiKey: params.apiKey });
  const message = await client.messages.create({
    model: params.model,
    max_tokens: params.maxTokens,
    temperature: params.temperature,
    system: params.system,
    messages: [{ role: "user", content: params.user }],
  });
  const textBlock = message.content.find((block: any) => block.type === "text");
  return (textBlock as any)?.text ?? "";
}

async function callGemini(params: {
  apiKey: string | null;
  model: string;
  system: string;
  user: string;
  temperature: number;
  maxTokens: number;
}): Promise<string> {
  if (!params.apiKey) {
    throw new Error("Missing Gemini API key");
  }
  const client = new GoogleGenAI({ apiKey: params.apiKey });
  const response = await client.models.generateContent({
    model: params.model,
    contents: params.user,
    config: {
      systemInstruction: params.system,
      temperature: params.temperature,
      maxOutputTokens: params.maxTokens,
    },
  });
  return response.text ?? "";
}

/**
 * Extract a JSON object from text that may contain markdown fences or extra prose.
 */
export function extractJson(text: string): Record<string, unknown> {
  if (!text) return {};
  // Try to find a JSON block in markdown fences first
  const fenceMatch = text.match(/```(?:json)?\s*\n?([\s\S]*?)\n?```/);
  const candidate = fenceMatch ? fenceMatch[1].trim() : text;
  const start = candidate.indexOf("{");
  const end = candidate.lastIndexOf("}");
  if (start < 0 || end <= start) return {};
  try {
    const doc = JSON.parse(candidate.slice(start, end + 1));
    return typeof doc === "object" && doc !== null ? doc : {};
  } catch {
    return {};
  }
}
