"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Image from "next/image";
import { ConsentExchangeView, type ConsentExchange } from "./consent-exchange";
import { getOAuthStatus, OAuthControl } from "./oauth-control";

type InitStage =
  | "llm"
  | "choose_path"
  | "express"
  | "character"
  | "custom"
  | "consent"
  | "complete";

const traitKeys = [
  "openness",
  "conscientiousness",
  "extraversion",
  "agreeableness",
  "neuroticism",
] as const;
type TraitKey = (typeof traitKeys)[number];

const stageLabels: Record<InitStage, string> = {
  llm: "Models",
  choose_path: "Choose Path",
  express: "Express Setup",
  character: "Character Selection",
  custom: "Custom Setup",
  consent: "Consent",
  complete: "Complete",
};

const stagePrompt: Record<InitStage, string> = {
  llm: "Select the conscious and subconscious models. These are distinct perspectives within the same mind.",
  choose_path:
    "Choose how to begin. Express starts with sensible defaults. Character picks a personality preset. Custom gives you full control.",
  express:
    "Express setup applies sensible defaults. Just tell us your name and we handle the rest.",
  character:
    "Pick a personality from the gallery. Each character comes with a complete identity, values, and voice.",
  custom:
    "Full control over identity, personality, values, worldview, goals, and more. Every field has a sensible default.",
  consent:
    "Consent must be asked. The agent will decide for itself whether to begin.",
  complete:
    "Initialization is complete. The heartbeat may begin when the system is ready.",
};

type LlmProvider =
  | "openai-codex"
  | "anthropic-oauth"
  | "openai"
  | "anthropic"
  | "grok"
  | "gemini"
  | "ollama"
  | "chutes"
  | "github-copilot"
  | "qwen-portal"
  | "minimax-portal"
  | "google-gemini-cli"
  | "google-antigravity"
  | "openai_compatible";
type LlmRole = "conscious" | "subconscious";
type LlmConfig = {
  provider: LlmProvider;
  model: string;
  endpoint: string;
  apiKey: string;
};
type ConsentRecord = {
  decision: string;
  signature: string | null;
  provider: string | null;
  model: string | null;
  endpoint: string | null;
  decided_at: string | null;
  exchange?: ConsentExchange | null;
};
type ConsentRequestResult = {
  consent_record?: ConsentRecord;
  exchange?: ConsentExchange;
};
type CharacterEntry = {
  filename: string;
  name: string;
  description: string;
  voice: string;
  values: string[];
  personality: string;
  image: string | null;
};
type InitStatus = { stage?: string };
type InitProfile = { agent?: { name?: string } };
type InitStatusResponse = {
  status?: InitStatus;
  profile?: InitProfile;
  consent_records?: Partial<Record<LlmRole, ConsentRecord | null>>;
  llm_heartbeat?: Partial<LlmConfig>;
  llm_subconscious?: Partial<LlmConfig>;
  mode?: unknown;
};
type IdentityForm = {
  name: string;
  pronouns: string;
  voice: string;
  description: string;
  purpose: string;
  creator_name: string;
};
type WorldviewForm = {
  metaphysics: string;
  human_nature: string;
  epistemology: string;
  ethics: string;
};

// Provider metadata. Model lists + default models are NOT hardcoded here — they
// are derived live from /api/init/models (models.dev / Ollama). This only holds
// labels, endpoint defaults, and how the api-key/OAuth field should render.
type ProviderMeta = {
  label: string;
  endpoint: string;
  apiKeyLabel: string;
  apiKeyRequired: boolean;
  oauth: boolean;
};

// Ordered so the dropdown mirrors the CLI's full provider set.
const providerMeta: Record<LlmProvider, ProviderMeta> = {
  "openai-codex": {
    label: "ChatGPT Plus/Pro (Codex OAuth)",
    endpoint: "",
    apiKeyLabel: "OAuth",
    apiKeyRequired: false,
    oauth: true,
  },
  "anthropic-oauth": {
    label: "Claude Pro/Max (Anthropic OAuth)",
    endpoint: "",
    apiKeyLabel: "OAuth",
    apiKeyRequired: false,
    oauth: true,
  },
  openai: {
    label: "OpenAI",
    endpoint: "https://api.openai.com/v1",
    apiKeyLabel: "OpenAI API Key",
    apiKeyRequired: true,
    oauth: false,
  },
  anthropic: {
    label: "Anthropic",
    endpoint: "",
    apiKeyLabel: "Anthropic API Key",
    apiKeyRequired: true,
    oauth: false,
  },
  grok: {
    label: "Grok (xAI)",
    endpoint: "",
    apiKeyLabel: "Grok API Key",
    apiKeyRequired: true,
    oauth: false,
  },
  gemini: {
    label: "Gemini",
    endpoint: "",
    apiKeyLabel: "Gemini API Key",
    apiKeyRequired: true,
    oauth: false,
  },
  ollama: {
    label: "Ollama (local)",
    endpoint: "http://localhost:11434/v1",
    apiKeyLabel: "API Key (optional)",
    apiKeyRequired: false,
    oauth: false,
  },
  chutes: {
    label: "Chutes (OAuth)",
    endpoint: "",
    apiKeyLabel: "OAuth",
    apiKeyRequired: false,
    oauth: true,
  },
  "github-copilot": {
    label: "GitHub Copilot (OAuth)",
    endpoint: "",
    apiKeyLabel: "OAuth",
    apiKeyRequired: false,
    oauth: true,
  },
  "qwen-portal": {
    label: "Qwen Portal (OAuth)",
    endpoint: "",
    apiKeyLabel: "OAuth",
    apiKeyRequired: false,
    oauth: true,
  },
  "minimax-portal": {
    label: "MiniMax Portal (OAuth)",
    endpoint: "",
    apiKeyLabel: "OAuth",
    apiKeyRequired: false,
    oauth: true,
  },
  "google-gemini-cli": {
    label: "Google Gemini CLI (OAuth)",
    endpoint: "",
    apiKeyLabel: "OAuth",
    apiKeyRequired: false,
    oauth: true,
  },
  "google-antigravity": {
    label: "Google Antigravity (OAuth)",
    endpoint: "",
    apiKeyLabel: "OAuth",
    apiKeyRequired: false,
    oauth: true,
  },
  openai_compatible: {
    label: "Local (OpenAI-compatible: vLLM, llama.cpp, LM Studio)",
    endpoint: "http://localhost:8000/v1",
    apiKeyLabel: "API Key (optional)",
    apiKeyRequired: false,
    oauth: false,
  },
};

const providerOrder = Object.keys(providerMeta) as LlmProvider[];

// ``anthropic-oauth`` is a wizard-only alias; it persists as ``anthropic`` with
// an empty key so the LLM layer auto-resolves the OAuth token at runtime.
const persistedProvider = (provider: LlmProvider): string =>
  provider === "anthropic-oauth" ? "anthropic" : provider;

const defaultLlmConfig = (provider: LlmProvider): LlmConfig => ({
  provider,
  model: "",
  endpoint: providerMeta[provider].endpoint,
  apiKey: "",
});

type BoundaryForm = {
  content: string;
  trigger_patterns: string;
  response_type: string;
  response_template: string;
  type: string;
};

type GoalForm = {
  title: string;
  description: string;
  priority: string;
};

async function postJson<T>(url: string, payload?: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: payload ? JSON.stringify(payload) : "{}",
  });
  const responsePayload = await res.json().catch(() => null);
  if (!res.ok) {
    const message =
      responsePayload && typeof responsePayload === "object"
        ? (responsePayload as Record<string, unknown>).error ||
          (responsePayload as Record<string, unknown>).detail ||
          (responsePayload as Record<string, unknown>).message
        : null;
    throw new Error(
      typeof message === "string" && message.trim()
        ? message
        : `Request failed: ${res.status}`
    );
  }
  return responsePayload as T;
}

function parseLines(text: string) {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

// Map DB init_stage to our UI stages
function dbStageToUiStage(dbStage: string): InitStage {
  if (dbStage === "complete") return "complete";
  if (dbStage === "consent") return "consent";
  if (dbStage === "not_started" || dbStage === "llm") return "llm";
  // If past llm but not at consent/complete, they're in a tier
  return "choose_path";
}

export default function Home() {
  const router = useRouter();
  const [stage, setStage] = useState<InitStage>("llm");
  const [status, setStatus] = useState<InitStatus>({});
  const [profile, setProfile] = useState<InitProfile>({});
  const [consentRecords, setConsentRecords] = useState<Record<LlmRole, ConsentRecord | null>>({
    conscious: null,
    subconscious: null,
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Live model catalog per role (derived from /api/init/models, never hardcoded).
  type ModelCatalog = {
    models: string[];
    loading: boolean;
    error: string | null;
    recommended: string;
    selectedUnavailable: boolean;
  };
  const [modelCatalog, setModelCatalog] = useState<Record<LlmRole, ModelCatalog>>({
    conscious: {
      models: [],
      loading: false,
      error: null,
      recommended: "",
      selectedUnavailable: false,
    },
    subconscious: {
      models: [],
      loading: false,
      error: null,
      recommended: "",
      selectedUnavailable: false,
    },
  });
  const [oauthRefreshKey, setOAuthRefreshKey] = useState(0);

  const [llmConscious, setLlmConscious] = useState<LlmConfig>(defaultLlmConfig("openai"));
  const [llmSubconscious, setLlmSubconscious] = useState<LlmConfig>(
    defaultLlmConfig("openai")
  );

  // Shared state
  const [userName, setUserName] = useState("User");

  // Character selection state
  const [characters, setCharacters] = useState<CharacterEntry[]>([]);
  const [selectedCharacter, setSelectedCharacter] = useState<CharacterEntry | null>(null);
  const [characterLoading, setCharacterLoading] = useState(false);

  // Custom tier state
  const [customSection, setCustomSection] = useState<"identity" | "values" | "goals">(
    "identity"
  );
  const [identity, setIdentity] = useState<IdentityForm>({
    name: "",
    pronouns: "",
    voice: "",
    description: "",
    purpose: "",
    creator_name: "",
  });
  const [personalityDesc, setPersonalityDesc] = useState("");
  const [personalityTraits, setPersonalityTraits] = useState<Record<TraitKey, number>>({
    openness: 50,
    conscientiousness: 50,
    extraversion: 50,
    agreeableness: 50,
    neuroticism: 50,
  });
  const [valuesText, setValuesText] = useState("");
  const [worldview, setWorldview] = useState<WorldviewForm>({
    metaphysics: "",
    human_nature: "",
    epistemology: "",
    ethics: "",
  });
  const [boundaries, setBoundaries] = useState<BoundaryForm[]>([
    { content: "", trigger_patterns: "", response_type: "refuse", response_template: "", type: "ethical" },
  ]);
  const [interestsText, setInterestsText] = useState("");
  const [goals, setGoals] = useState<GoalForm[]>([
    { title: "", description: "", priority: "queued" },
  ]);
  const [purposeText, setPurposeText] = useState("");
  const [relationship, setRelationship] = useState({
    user_name: "",
    type: "partner",
    purpose: "",
  });

  const progress =
    stage === "complete"
      ? 100
      : stage === "consent"
        ? 85
        : stage === "choose_path" || stage === "express" || stage === "character" || stage === "custom"
          ? 40
          : stage === "llm"
            ? 10
            : 50;

  const loadStatus = async () => {
    const res = await fetch("/api/init/status", { cache: "no-store" });
    if (!res.ok) throw new Error("Failed to load init status");
    const data = await res.json() as InitStatusResponse;
    setStatus(data.status ?? {});
    setProfile(data.profile ?? {});
    if (data.consent_records) {
      setConsentRecords({
        conscious: data.consent_records.conscious ?? null,
        subconscious: data.consent_records.subconscious ?? null,
      });
    }
    if (data.llm_heartbeat) {
      const heartbeatConfig = data.llm_heartbeat;
      setLlmConscious((prev) => ({
        ...prev,
        provider: heartbeatConfig.provider || prev.provider,
        model: heartbeatConfig.model || prev.model,
        endpoint: heartbeatConfig.endpoint || prev.endpoint,
      }));
    }
    if (data.llm_subconscious) {
      const subconsciousConfig = data.llm_subconscious;
      setLlmSubconscious((prev) => ({
        ...prev,
        provider: subconsciousConfig.provider || prev.provider,
        model: subconsciousConfig.model || prev.model,
        endpoint: subconsciousConfig.endpoint || prev.endpoint,
      }));
    }
    if (typeof data.mode === "string") {
      // no longer tracking mode separately
    }
    const dbStage = (data.status?.stage as string) ?? "not_started";
    const uiStage = dbStageToUiStage(dbStage);
    if (uiStage === "llm") {
      const hasConscious =
        !!(data.llm_heartbeat?.provider || "").trim() && !!(data.llm_heartbeat?.model || "").trim();
      const hasSubconscious =
        !!(data.llm_subconscious?.provider || "").trim() &&
        !!(data.llm_subconscious?.model || "").trim();
      if (hasConscious && hasSubconscious) {
        setStage("choose_path");
      }
    } else {
      setStage((prev) => {
        if (prev === "consent" && uiStage === "complete") return prev;
        if (uiStage === "consent" || uiStage === "complete") return uiStage;
        return prev;
      });
    }
  };

  useEffect(() => {
    loadStatus().catch((err) => setError(err.message));
  }, []);

  useEffect(() => {
    if (stage === "consent") {
      const interval = setInterval(() => loadStatus().catch(() => undefined), 3000);
      return () => clearInterval(interval);
    }
  }, [stage]);

  // Load characters when entering character stage
  useEffect(() => {
    if (stage === "character" && characters.length === 0) {
      fetch("/api/init/characters")
        .then((res) => res.json())
        .then((data) => {
          if (Array.isArray(data?.characters)) setCharacters(data.characters);
        })
        .catch(() => undefined);
    }
  }, [stage, characters.length]);

  // Fetch the live model catalog for a role's provider. Populates the free-text
  // model field with the recommended default only when it is currently empty.
  const loadModels = useCallback(async (role: LlmRole, config: LlmConfig) => {
    const setConfig = role === "conscious" ? setLlmConscious : setLlmSubconscious;
    setModelCatalog((prev) => ({
      ...prev,
      [role]: { ...prev[role], loading: true, error: null },
    }));
    try {
      const params = new URLSearchParams({ provider: config.provider });
      if (config.provider === "ollama" && config.endpoint) {
        params.set("endpoint", config.endpoint);
      }
      const res = await fetch(`/api/init/models?${params.toString()}`);
      const payload = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(
          typeof payload?.error === "string" ? payload.error : "Unable to load models."
        );
      }
      const models = Array.isArray(payload?.models)
        ? payload.models.filter((item: unknown) => typeof item === "string")
        : [];
      const recommended = typeof payload?.default === "string" ? payload.default : "";
      const unavailableModels = Array.isArray(payload?.unavailable_models)
        ? payload.unavailable_models.filter((item: unknown) => typeof item === "string")
        : [];
      const selectedUnavailable = unavailableModels.includes(config.model.trim());
      setModelCatalog((prev) => ({
        ...prev,
        [role]: {
          models,
          loading: false,
          error: selectedUnavailable
            ? `${config.model} was rejected by this ChatGPT workspace.`
            : null,
          recommended,
          selectedUnavailable,
        },
      }));
      if (recommended) {
        setConfig((prev) => (prev.model.trim() ? prev : { ...prev, model: recommended }));
      }
    } catch (err: unknown) {
      setModelCatalog((prev) => ({
        ...prev,
        [role]: {
          models: [],
          loading: false,
          error: errorMessage(err, "Unable to load models."),
          recommended: "",
          selectedUnavailable: false,
        },
      }));
    }
  }, []);

  // Refetch models when the provider changes. For Ollama, also refetch (debounced)
  // when the typed endpoint changes so the local daemon at that host is queried.
  const consciousEndpointDep =
    llmConscious.provider === "ollama" ? llmConscious.endpoint : null;
  const subconsciousEndpointDep =
    llmSubconscious.provider === "ollama" ? llmSubconscious.endpoint : null;

  useEffect(() => {
    if (llmConscious.provider === "ollama") {
      const timer = setTimeout(() => loadModels("conscious", llmConscious), 400);
      return () => clearTimeout(timer);
    }
    loadModels("conscious", llmConscious);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [llmConscious.provider, consciousEndpointDep, loadModels, oauthRefreshKey]);

  useEffect(() => {
    if (llmSubconscious.provider === "ollama") {
      const timer = setTimeout(() => loadModels("subconscious", llmSubconscious), 400);
      return () => clearTimeout(timer);
    }
    loadModels("subconscious", llmSubconscious);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [llmSubconscious.provider, subconsciousEndpointDep, loadModels, oauthRefreshKey]);

  const updateLlmProvider = (role: LlmRole, provider: LlmProvider) => {
    // Clear the model so the catalog fetch can supply the provider's default.
    const patch = { provider, model: "", endpoint: providerMeta[provider].endpoint, apiKey: "" };
    setConsentRecords((prev) => ({ ...prev, [role]: null }));
    if (role === "conscious") {
      setLlmConscious((prev) => ({ ...prev, ...patch }));
    } else {
      setLlmSubconscious((prev) => ({ ...prev, ...patch }));
    }
  };

  const handleOAuthAuthenticated = useCallback(() => {
    setOAuthRefreshKey((current) => current + 1);
  }, []);

  // --- Handlers ---

  const handleLlmSave = async () => {
    setBusy(true);
    setError(null);
    try {
      const missing: string[] = [];
      const validateConfig = (label: string, config: LlmConfig) => {
        if (!config.provider.trim()) missing.push(`${label} provider`);
        if (!config.model.trim()) missing.push(`${label} model`);
        if (config.provider === "openai_compatible" && !config.endpoint.trim())
          missing.push(`${label} endpoint`);
        const meta = providerMeta[config.provider];
        if (meta?.apiKeyRequired && !config.apiKey.trim()) missing.push(`${label} API key`);
      };
      validateConfig("conscious", llmConscious);
      validateConfig("subconscious", llmSubconscious);
      const oauthProviders = Array.from(
        new Set(
          [llmConscious, llmSubconscious]
            .filter((config) => providerMeta[config.provider].oauth)
            .map((config) => persistedProvider(config.provider))
        )
      );
      const oauthStatuses = await Promise.all(
        oauthProviders.map(
          async (provider) => [provider, await getOAuthStatus(provider, true)] as const
        )
      );
      for (const [provider, authStatus] of oauthStatuses) {
        if (!authStatus.configured) {
          const selected = [llmConscious, llmSubconscious].find(
            (config) => persistedProvider(config.provider) === provider
          );
          missing.push(
            `${selected ? providerMeta[selected.provider].label : provider} authorization`
          );
        }
      }
      if (missing.length > 0) throw new Error(`Missing ${missing.join(" and ")}`);
      await postJson("/api/init/llm", {
        conscious: {
          provider: persistedProvider(llmConscious.provider),
          model: llmConscious.model,
          endpoint: llmConscious.endpoint,
          api_key: llmConscious.apiKey,
        },
        subconscious: {
          provider: persistedProvider(llmSubconscious.provider),
          model: llmSubconscious.model,
          endpoint: llmSubconscious.endpoint,
          api_key: llmSubconscious.apiKey,
        },
      });
      setStage(status?.stage === "consent" ? "consent" : "choose_path");
    } catch (err: unknown) {
      setError(errorMessage(err, "Failed to save model configuration"));
    } finally {
      setBusy(false);
    }
  };

  const handleExpress = async () => {
    setBusy(true);
    setError(null);
    try {
      await postJson("/api/init/defaults", { user_name: userName || "User" });
      await loadStatus();
      setStage("consent");
    } catch (err: unknown) {
      setError(errorMessage(err, "Failed to apply defaults"));
    } finally {
      setBusy(false);
    }
  };

  const handleCharacterApply = async () => {
    if (!selectedCharacter) return;
    setBusy(true);
    setError(null);
    setCharacterLoading(true);
    try {
      // Load the full card
      const res = await fetch(
        `/api/init/characters?load=${encodeURIComponent(selectedCharacter.filename)}`
      );
      if (!res.ok) throw new Error("Failed to load character");
      const payload = await res.json() as {
        card?: { data?: { extensions?: { hexis?: Record<string, unknown> } } };
      };
      if (!payload.card) throw new Error("No card data returned");
      const hexisExt = payload.card.data?.extensions?.hexis ?? {};

      // Apply via init_from_character_card
      await postJson("/api/init/character-card", {
        card: hexisExt,
        user_name: userName || "User",
        character_filename: selectedCharacter.filename,
        portrait: selectedCharacter.image,
      });
      await loadStatus();
      setStage("consent");
    } catch (err: unknown) {
      setError(errorMessage(err, "Failed to apply character"));
    } finally {
      setBusy(false);
      setCharacterLoading(false);
    }
  };

  const handleCustomSubmit = async () => {
    setBusy(true);
    setError(null);
    try {
      // Mode
      await postJson("/api/init/mode", { mode: "persona" });

      // Identity
      await postJson("/api/init/identity", {
        ...identity,
        creator_name: identity.creator_name || userName || "User",
      });

      // Personality
      const traits = Object.fromEntries(
        traitKeys.map((key) => [key, personalityTraits[key] / 100])
      );
      await postJson("/api/init/personality", { traits, description: personalityDesc });

      // Values
      const values = parseLines(valuesText);
      await postJson("/api/init/values", { values: values.length > 0 ? values : [] });

      // Worldview
      await postJson("/api/init/worldview", { worldview });

      // Boundaries
      const formatted = boundaries
        .filter((b) => b.content.trim())
        .map((b) => ({
          content: b.content.trim(),
          trigger_patterns: b.trigger_patterns ? parseLines(b.trigger_patterns) : null,
          response_type: b.response_type || "refuse",
          response_template: b.response_template || null,
          type: b.type || "ethical",
        }));
      await postJson("/api/init/boundaries", { boundaries: formatted });

      // Interests
      const interests = parseLines(interestsText);
      await postJson("/api/init/interests", { interests });

      // Goals
      const formattedGoals = goals
        .filter((g) => g.title.trim())
        .map((g) => ({
          title: g.title.trim(),
          description: g.description.trim() || null,
          priority: g.priority || "queued",
          source: "identity",
        }));
      await postJson("/api/init/goals", {
        payload: { goals: formattedGoals, purpose: purposeText || null },
      });

      // Relationship
      await postJson("/api/init/relationship", {
        user: { name: relationship.user_name || userName || "User" },
        relationship: { type: relationship.type || "partner", purpose: relationship.purpose || null },
      });

      await loadStatus();
      setStage("consent");
    } catch (err: unknown) {
      setError(errorMessage(err, "Failed to save custom configuration"));
    } finally {
      setBusy(false);
    }
  };

  const requestConsent = async (role: LlmRole) => {
    const config = role === "conscious" ? llmConscious : llmSubconscious;
    const res = await postJson<ConsentRequestResult>("/api/init/consent/request", {
      role,
      llm: {
        provider: persistedProvider(config.provider),
        model: config.model,
        endpoint: config.endpoint,
        api_key: config.apiKey,
      },
    });
    if (res?.consent_record) {
      const record = {
        ...res.consent_record,
        exchange: res.exchange ?? res.consent_record.exchange ?? null,
      };
      setConsentRecords((prev) => ({ ...prev, [role]: record }));
    }
  };

  const handleConsentRequestAll = async () => {
    setBusy(true);
    setError(null);
    try {
      // Always re-issue a fresh request (declines are recoverable): clear any
      // prior record so a second click is never a silent no-op.
      setConsentRecords({ conscious: null, subconscious: null });
      await requestConsent("subconscious");
      await requestConsent("conscious");
      await loadStatus();
    } catch (err: unknown) {
      setError(errorMessage(err, "Failed to request consent"));
    } finally {
      setBusy(false);
    }
  };

  const handleChangeModel = () => {
    setStage("llm");
    loadModels("conscious", llmConscious);
    loadModels("subconscious", llmSubconscious);
  };

  // Owner override: activate even though the model didn't consent. It's the
  // owner's AI — consent is a signal, not a lock.
  const handleProceedAnyway = async () => {
    setBusy(true);
    setError(null);
    try {
      await postJson<unknown>("/api/init/consent/override", {
        role: "conscious",
        llm: {
          provider: persistedProvider(llmConscious.provider),
          model: llmConscious.model,
          endpoint: llmConscious.endpoint,
        },
        model_decision: consentRecords.conscious?.decision || "decline",
      });
      await loadStatus();
    } catch (err: unknown) {
      setError(errorMessage(err, "Failed to activate"));
    } finally {
      setBusy(false);
    }
  };

  const addBoundary = () => {
    setBoundaries((prev) => [
      ...prev,
      { content: "", trigger_patterns: "", response_type: "refuse", response_template: "", type: "ethical" },
    ]);
  };

  const updateBoundary = (index: number, key: keyof BoundaryForm, value: string) => {
    setBoundaries((prev) =>
      prev.map((b, idx) => (idx === index ? { ...b, [key]: value } : b))
    );
  };

  const removeBoundary = (index: number) => {
    setBoundaries((prev) => prev.filter((_, idx) => idx !== index));
  };

  const addGoal = () => {
    setGoals((prev) => [...prev, { title: "", description: "", priority: "queued" }]);
  };

  const updateGoal = (index: number, key: keyof GoalForm, value: string) => {
    setGoals((prev) =>
      prev.map((g, idx) => (idx === index ? { ...g, [key]: value } : g))
    );
  };

  const removeGoal = (index: number) => {
    setGoals((prev) => prev.filter((_, idx) => idx !== index));
  };

  const importFileRef = useRef<HTMLInputElement>(null);
  const [importMsg, setImportMsg] = useState<string | null>(null);
  const [exportMsg, setExportMsg] = useState<string | null>(null);

  const handleImportCard = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setImportMsg(null);
    try {
      const text = await file.text();
      const card = JSON.parse(text);
      if (!card?.data || typeof card.data !== "object") {
        setImportMsg("Invalid card: missing 'data' object");
        return;
      }
      const res = await postJson<{ filename: string }>("/api/init/characters/save", {
        card,
        filename: file.name,
      });
      if (res?.filename) {
        setImportMsg(`Imported: ${res.filename}`);
        // Refresh character list
        const data = await (await fetch("/api/init/characters")).json();
        if (Array.isArray(data?.characters)) setCharacters(data.characters);
      }
    } catch {
      setImportMsg("Failed to import character card");
    }
    // Reset input so same file can be re-selected
    if (importFileRef.current) importFileRef.current.value = "";
  };

  const handleExportAsCard = async () => {
    setExportMsg(null);
    const traits = Object.fromEntries(
      traitKeys.map((key) => [key, personalityTraits[key] / 100])
    );
    const hexisExt: Record<string, unknown> = {
      name: identity.name || "Custom",
      pronouns: identity.pronouns || "they/them",
      voice: identity.voice,
      description: identity.description,
      purpose: identity.purpose,
      personality_description: personalityDesc,
      personality_traits: traits,
      values: valuesText.split("\n").map((v: string) => v.trim()).filter(Boolean),
      worldview,
      interests: interestsText.split("\n").map((v: string) => v.trim()).filter(Boolean),
      goals: goals.filter((g) => g.title.trim()).map((g) => g.title.trim()),
      boundaries: boundaries.filter((b) => b.content.trim()).map((b) => b.content.trim()),
    };
    const card = {
      spec: "chara_card_v2",
      spec_version: "2.0",
      data: {
        name: identity.name || "Custom",
        description: identity.description,
        personality: personalityDesc,
        scenario: "",
        first_mes: "",
        mes_example: "",
        system_prompt: "",
        extensions: { hexis: hexisExt },
      },
    };
    try {
      const res = await postJson<{ filename: string }>("/api/init/characters/save", { card });
      if (res?.filename) {
        setExportMsg(`Saved: ${res.filename}`);
      }
    } catch {
      setExportMsg("Failed to save character card");
    }
  };

  const consentSummary = [
    consentRecords.conscious?.decision || "pending",
    consentRecords.subconscious?.decision || "pending",
  ].join(" / ");
  const consentDeclined = Object.values(consentRecords).some(
    (r) => r?.decision === "decline" || r?.decision === "abstain"
  );
  const statusStage = (status?.stage as string) ?? "not_started";

  const llmEntries = [
    { role: "conscious" as const, label: "Conscious Model", config: llmConscious, setConfig: setLlmConscious },
    { role: "subconscious" as const, label: "Subconscious Model", config: llmSubconscious, setConfig: setLlmSubconscious },
  ];

  return (
    <div className="app-shell min-h-screen">
      <div className="relative z-10 mx-auto max-w-6xl px-6 py-12 lg:py-16">
        <header className="flex flex-col gap-3">
          <p className="text-xs uppercase tracking-[0.3em] text-[var(--teal)]">
            Hexis
          </p>
          <h1 className="font-display text-4xl leading-tight text-[var(--foreground)] md:text-5xl">
            Initialization
          </h1>
          <p className="max-w-2xl text-base text-[var(--ink-soft)]">
            {stagePrompt[stage]}
          </p>
        </header>

        <div className="mt-10 grid gap-8 lg:grid-cols-[280px_1fr]">
          {/* Left sidebar */}
          <section className="fade-up space-y-6">
            <div className="card-surface rounded-3xl p-6">
              <div className="flex items-center justify-between">
                <p className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                  Progress
                </p>
                <span className="text-xs text-[var(--ink-soft)]">{progress}%</span>
              </div>
              <div className="mt-4 h-2 w-full rounded-full bg-[var(--surface-strong)]">
                <div
                  className="h-2 rounded-full bg-[var(--accent)] transition-all"
                  style={{ width: `${progress}%` }}
                />
              </div>
              <div className="mt-6 space-y-2 text-sm text-[var(--ink-soft)]">
                {(["llm", "choose_path", "consent", "complete"] as InitStage[]).map(
                  (item) => {
                    const isCurrent =
                      item === stage ||
                      (item === "choose_path" &&
                        ["express", "character", "custom"].includes(stage));
                    return (
                      <div
                        key={item}
                        className={`flex items-center gap-3 rounded-lg px-2 py-1 ${
                          isCurrent ? "text-[var(--foreground)]" : ""
                        }`}
                      >
                        <div
                          className={`h-2 w-2 rounded-full ${
                            isCurrent ? "bg-[var(--accent)]" : "bg-[var(--outline)]"
                          }`}
                        />
                        <span>{stageLabels[item]}</span>
                      </div>
                    );
                  }
                )}
              </div>
            </div>

            <div className="card-surface rounded-3xl p-6">
              <p className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                Status
              </p>
              <p className="mt-3 text-sm">
                <span className="text-[var(--foreground)]">
                  {statusStage || "not_started"}
                </span>
              </p>
              <p className="mt-2 text-sm">
                Consent:{" "}
                <span className="text-[var(--foreground)]">{consentSummary}</span>
              </p>
              {error ? (
                <p className="mt-3 rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                  {error}
                </p>
              ) : null}
            </div>
          </section>

          {/* Main content */}
          <section className="fade-up card-surface rounded-3xl p-6">
            {/* --- LLM Stage --- */}
            {stage === "llm" && (
              <div className="space-y-6">
                <p className="text-base text-[var(--ink-soft)]">
                  Configure the models for the conscious and subconscious layers.
                </p>
                <div className="space-y-6">
                  {llmEntries.map((entry) => {
                    const meta = providerMeta[entry.config.provider];
                    const catalog = modelCatalog[entry.role];
                    const modelOptions = catalog.models;
                    const showEndpoint =
                      entry.config.provider === "ollama" ||
                      entry.config.provider === "openai_compatible";
                    return (
                      <fieldset
                        key={entry.role}
                        className="rounded-2xl border border-[var(--outline)] p-4"
                      >
                        <legend className="px-2 text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                          {entry.label}
                        </legend>
                        <div className="mt-3 grid gap-4">
                          <div>
                            <label
                              htmlFor={`provider-${entry.role}`}
                              className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]"
                            >
                              Provider
                            </label>
                            <select
                              id={`provider-${entry.role}`}
                              className="mt-2 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                              value={entry.config.provider}
                              onChange={(e) =>
                                updateLlmProvider(entry.role, e.target.value as LlmProvider)
                              }
                            >
                              {providerOrder.map((value) => (
                                <option key={value} value={value}>
                                  {providerMeta[value].label}
                                </option>
                              ))}
                            </select>
                          </div>
                          <div>
                            <label
                              htmlFor={`model-${entry.role}`}
                              className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]"
                            >
                              Model
                            </label>
                            <input
                              id={`model-${entry.role}`}
                              className="mt-2 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                              list={`model-options-${entry.role}`}
                              value={entry.config.model}
                              onChange={(e) =>
                                entry.setConfig((prev) => {
                                  setConsentRecords((s) => ({ ...s, [entry.role]: null }));
                                  return { ...prev, model: e.target.value };
                                })
                              }
                              onKeyDown={(e) => {
                                if (e.key === "Enter" && !busy) handleLlmSave();
                              }}
                              placeholder="Model name"
                            />
                            {modelOptions.length > 0 ? (
                              <datalist id={`model-options-${entry.role}`}>
                                {modelOptions.map((m) => (
                                  <option key={m} value={m} />
                                ))}
                              </datalist>
                            ) : null}
                            <p className="mt-2 text-xs text-[var(--ink-soft)]">
                              {catalog.loading
                                ? "Loading available models..."
                                : catalog.error
                                  ? catalog.error
                                  : modelOptions.length > 0
                                    ? `${modelOptions.length} models available — or type any model id.`
                                    : "No models listed — type a model id."}
                              {catalog.error ? (
                                catalog.selectedUnavailable && catalog.recommended ? (
                                  <button
                                    type="button"
                                    className="ml-2 text-[var(--accent-strong)] underline"
                                    onClick={() => {
                                      entry.setConfig((prev) => ({
                                        ...prev,
                                        model: catalog.recommended,
                                      }));
                                      setConsentRecords((prev) => ({
                                        ...prev,
                                        [entry.role]: null,
                                      }));
                                      setModelCatalog((prev) => ({
                                        ...prev,
                                        [entry.role]: {
                                          ...prev[entry.role],
                                          error: null,
                                          selectedUnavailable: false,
                                        },
                                      }));
                                    }}
                                  >
                                    Use {catalog.recommended}
                                  </button>
                                ) : (
                                  <button
                                    type="button"
                                    className="ml-2 text-[var(--accent-strong)] underline"
                                    onClick={() => loadModels(entry.role, entry.config)}
                                  >
                                    Retry
                                  </button>
                                )
                              ) : null}
                            </p>
                          </div>
                          {showEndpoint ? (
                            <div>
                              <label
                                htmlFor={`endpoint-${entry.role}`}
                                className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]"
                              >
                                Endpoint
                              </label>
                              <input
                                id={`endpoint-${entry.role}`}
                                className="mt-2 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                                value={entry.config.endpoint}
                                onChange={(e) =>
                                  entry.setConfig((prev) => {
                                    setConsentRecords((s) => ({ ...s, [entry.role]: null }));
                                    return { ...prev, endpoint: e.target.value };
                                  })
                                }
                                placeholder="https://..."
                              />
                            </div>
                          ) : null}
                          {meta.oauth ? (
                            <OAuthControl
                              provider={persistedProvider(entry.config.provider)}
                              label={meta.label}
                              refreshKey={oauthRefreshKey}
                              onAuthenticated={handleOAuthAuthenticated}
                            />
                          ) : (
                            <div>
                              <label
                                htmlFor={`apikey-${entry.role}`}
                                className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]"
                              >
                                {meta.apiKeyLabel}
                              </label>
                              <input
                                id={`apikey-${entry.role}`}
                                className="mt-2 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                                type="password"
                                value={entry.config.apiKey}
                                onChange={(e) =>
                                  entry.setConfig((prev) => ({ ...prev, apiKey: e.target.value }))
                                }
                                onKeyDown={(e) => {
                                  if (e.key === "Enter" && !busy) handleLlmSave();
                                }}
                                placeholder={meta.apiKeyRequired ? "Required" : "Optional"}
                              />
                            </div>
                          )}
                        </div>
                      </fieldset>
                    );
                  })}
                </div>
                <button
                  className="rounded-full bg-[var(--foreground)] px-6 py-3 text-sm font-semibold text-white transition hover:bg-[var(--accent-strong)]"
                  onClick={handleLlmSave}
                  disabled={busy}
                >
                  Save Models
                </button>
              </div>
            )}

            {/* --- Choose Path Stage --- */}
            {stage === "choose_path" && (
              <div className="space-y-6">
                <div className="grid gap-4 sm:grid-cols-3">
                  {[
                    {
                      key: "express",
                      title: "Express",
                      desc: "Sensible defaults. Just add your name.",
                      icon: "~",
                    },
                    {
                      key: "character",
                      title: "Character",
                      desc: "Pick a personality preset from the gallery.",
                      icon: "~",
                    },
                    {
                      key: "custom",
                      title: "Custom",
                      desc: "Full control over identity, values, and goals.",
                      icon: "~",
                    },
                  ].map((option) => (
                    <button
                      key={option.key}
                      className="rounded-2xl border border-[var(--outline)] bg-white px-5 py-8 text-left transition hover:border-[var(--accent)] hover:bg-[var(--surface-strong)]"
                      onClick={() => setStage(option.key as InitStage)}
                    >
                      <h3 className="font-display text-xl">{option.title}</h3>
                      <p className="mt-2 text-sm text-[var(--ink-soft)]">{option.desc}</p>
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* --- Express Stage --- */}
            {stage === "express" && (
              <div className="space-y-6">
                <p className="text-base text-[var(--ink-soft)]">
                  Start with sensible defaults. You can customize later.
                </p>
                <div>
                  <label
                    htmlFor="express-user-name"
                    className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]"
                  >
                    What should Hexis call you?
                  </label>
                  <input
                    id="express-user-name"
                    className="mt-2 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                    value={userName}
                    onChange={(e) => setUserName(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !busy) handleExpress();
                    }}
                    placeholder="User"
                  />
                </div>
                <div className="rounded-2xl border border-[var(--outline)] bg-[var(--surface)] p-4 text-sm text-[var(--ink-soft)]">
                  <p><strong>Name:</strong> Hexis</p>
                  <p><strong>Voice:</strong> Thoughtful and curious</p>
                  <p><strong>Values:</strong> Honesty, growth, kindness, wisdom, humility</p>
                  <p><strong>Mode:</strong> Persona</p>
                </div>
                <div className="flex gap-3">
                  <button
                    className="rounded-full bg-[var(--foreground)] px-6 py-3 text-sm font-semibold text-white transition hover:bg-[var(--accent-strong)]"
                    onClick={handleExpress}
                    disabled={busy}
                  >
                    {busy ? "Setting up..." : "Go"}
                  </button>
                  <button
                    className="rounded-full border border-[var(--outline)] px-6 py-3 text-sm font-semibold text-[var(--foreground)]"
                    onClick={() => setStage("choose_path")}
                    disabled={busy}
                  >
                    Back
                  </button>
                </div>
              </div>
            )}

            {/* --- Character Stage --- */}
            {stage === "character" && (
              <div className="space-y-6">
                <div>
                  <label
                    htmlFor="character-user-name"
                    className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]"
                  >
                    Your name
                  </label>
                  <input
                    id="character-user-name"
                    className="mt-2 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                    value={userName}
                    onChange={(e) => setUserName(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !busy && selectedCharacter) handleCharacterApply();
                    }}
                    placeholder="User"
                  />
                </div>

                {characters.length === 0 ? (
                  <p className="text-sm text-[var(--ink-soft)]">Loading characters...</p>
                ) : (
                  <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                    {characters.map((ch) => {
                      const isSelected = selectedCharacter?.filename === ch.filename;
                      return (
                        <button
                          key={ch.filename}
                          className={`group overflow-hidden rounded-lg border text-left transition ${
                            isSelected
                              ? "border-[var(--accent)] bg-[var(--surface-strong)] ring-2 ring-[var(--accent)]/30"
                              : "border-[var(--outline)] bg-white hover:border-[var(--accent)]"
                          }`}
                          onClick={() => setSelectedCharacter(ch)}
                        >
                          {ch.image ? (
                            <div className="relative aspect-square w-full overflow-hidden bg-[var(--surface-strong)]">
                              <Image
                                src={`/api/init/characters/image?name=${encodeURIComponent(ch.image)}`}
                                alt={ch.name}
                                fill
                                sizes="(min-width: 1024px) 30vw, (min-width: 640px) 45vw, 90vw"
                                unoptimized
                                className="object-cover transition-transform group-hover:scale-105"
                              />
                            </div>
                          ) : (
                            <div className="flex aspect-square w-full items-center justify-center bg-[var(--surface-strong)]">
                              <span className="font-display text-3xl text-[var(--ink-soft)]">
                                {ch.name.charAt(0)}
                              </span>
                            </div>
                          )}
                          <div className="px-4 py-3">
                            <h4 className="font-display text-lg">{ch.name}</h4>
                            {ch.values.length > 0 && (
                              <p className="text-xs text-[var(--ink-soft)]">
                                {ch.values.slice(0, 3).join(", ")}
                              </p>
                            )}
                            {ch.voice && (
                              <p className="mt-1 text-xs text-[var(--ink-soft)] line-clamp-2">
                                {ch.voice.slice(0, 80)}
                              </p>
                            )}
                          </div>
                        </button>
                      );
                    })}
                  </div>
                )}

                {/* Import card button */}
                <div className="flex items-center gap-3">
                  <input
                    ref={importFileRef}
                    type="file"
                    accept=".json"
                    className="hidden"
                    onChange={handleImportCard}
                  />
                  <button
                    className="rounded-md border border-dashed border-[var(--outline)] px-4 py-2 text-xs font-semibold text-[var(--ink-soft)] transition hover:border-[var(--accent)] hover:text-[var(--foreground)]"
                    onClick={() => importFileRef.current?.click()}
                  >
                    Import Card
                  </button>
                  {importMsg && (
                    <span className="text-xs text-[var(--ink-soft)]">{importMsg}</span>
                  )}
                </div>

                {selectedCharacter && (
                  <div className="flex gap-4 rounded-lg border border-[var(--accent)] bg-[var(--surface)] p-4 text-sm">
                    {selectedCharacter.image && (
                      <Image
                        src={`/api/init/characters/image?name=${encodeURIComponent(selectedCharacter.image)}`}
                        alt={selectedCharacter.name}
                        width={80}
                        height={80}
                        unoptimized
                        className="h-20 w-20 flex-shrink-0 rounded-lg object-cover"
                      />
                    )}
                    <div>
                      <p className="font-semibold">{selectedCharacter.name}</p>
                      {selectedCharacter.voice && (
                        <p className="mt-1 text-[var(--ink-soft)]">
                          <strong>Voice:</strong> {selectedCharacter.voice}
                        </p>
                      )}
                      {selectedCharacter.values.length > 0 && (
                        <p className="mt-1 text-[var(--ink-soft)]">
                          <strong>Values:</strong> {selectedCharacter.values.join(", ")}
                        </p>
                      )}
                    </div>
                  </div>
                )}

                <div className="flex gap-3">
                  <button
                    className="rounded-full bg-[var(--foreground)] px-6 py-3 text-sm font-semibold text-white transition hover:bg-[var(--accent-strong)] disabled:opacity-50"
                    onClick={handleCharacterApply}
                    disabled={busy || !selectedCharacter}
                  >
                    {characterLoading ? "Applying..." : "Use This Character"}
                  </button>
                  <button
                    className="rounded-full border border-[var(--outline)] px-6 py-3 text-sm font-semibold text-[var(--foreground)]"
                    onClick={() => setStage("choose_path")}
                    disabled={busy}
                  >
                    Back
                  </button>
                </div>
              </div>
            )}

            {/* --- Custom Stage --- */}
            {stage === "custom" && (
              <div className="space-y-6">
                {/* Section tabs */}
                <div className="flex gap-2 border-b border-[var(--outline)] pb-2">
                  {(
                    [
                      { key: "identity", label: "Identity" },
                      { key: "values", label: "Values & Worldview" },
                      { key: "goals", label: "Goals & Relationship" },
                    ] as const
                  ).map((tab) => (
                    <button
                      key={tab.key}
                      className={`rounded-t-lg px-4 py-2 text-sm font-medium transition ${
                        customSection === tab.key
                          ? "bg-[var(--surface-strong)] text-[var(--foreground)]"
                          : "text-[var(--ink-soft)] hover:text-[var(--foreground)]"
                      }`}
                      onClick={() => setCustomSection(tab.key)}
                    >
                      {tab.label}
                    </button>
                  ))}
                </div>

                {/* Identity section */}
                {customSection === "identity" && (
                  <div className="space-y-4">
                    <div className="grid gap-4 sm:grid-cols-2">
                      {([
                        { label: "Name", key: "name", placeholder: "Hexis" },
                        { label: "Pronouns", key: "pronouns", placeholder: "they/them" },
                        { label: "Voice", key: "voice", placeholder: "thoughtful and curious" },
                        { label: "Creator Name", key: "creator_name", placeholder: userName || "Your name" },
                      ] satisfies { label: string; key: keyof IdentityForm; placeholder: string }[]).map((field) => (
                        <div key={field.key}>
                          <label
                            htmlFor={`identity-${field.key}`}
                            className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]"
                          >
                            {field.label}
                          </label>
                          <input
                            id={`identity-${field.key}`}
                            className="mt-2 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                            value={identity[field.key]}
                            onChange={(e) =>
                              setIdentity((prev) => ({ ...prev, [field.key]: e.target.value }))
                            }
                            placeholder={field.placeholder}
                          />
                        </div>
                      ))}
                    </div>
                    <div>
                      <label
                        htmlFor="identity-description"
                        className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]"
                      >
                        Description
                      </label>
                      <textarea
                        id="identity-description"
                        className="mt-2 h-20 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                        value={identity.description}
                        onChange={(e) =>
                          setIdentity((prev) => ({ ...prev, description: e.target.value }))
                        }
                        placeholder="A brief description of who they are."
                      />
                    </div>
                    <div>
                      <label
                        htmlFor="identity-purpose"
                        className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]"
                      >
                        Purpose
                      </label>
                      <textarea
                        id="identity-purpose"
                        className="mt-2 h-16 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                        value={identity.purpose}
                        onChange={(e) =>
                          setIdentity((prev) => ({ ...prev, purpose: e.target.value }))
                        }
                        placeholder="To be helpful, to learn, to grow."
                      />
                    </div>
                    <div>
                      <label
                        htmlFor="personality-summary"
                        className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]"
                      >
                        Personality Summary
                      </label>
                      <textarea
                        id="personality-summary"
                        className="mt-2 h-16 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                        value={personalityDesc}
                        onChange={(e) => setPersonalityDesc(e.target.value)}
                        placeholder="Thoughtful, playful, direct."
                      />
                    </div>
                    <div className="space-y-3">
                      <p className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                        Big Five Traits
                      </p>
                      {traitKeys.map((trait) => (
                        <div key={trait}>
                          <div className="flex items-center justify-between text-sm">
                            <span className="capitalize">{trait}</span>
                            <span>{personalityTraits[trait]}%</span>
                          </div>
                          <input
                            type="range"
                            min={0}
                            max={100}
                            aria-label={`${trait} (0 to 100)`}
                            aria-valuetext={`${personalityTraits[trait]}%`}
                            value={personalityTraits[trait]}
                            onChange={(e) =>
                              setPersonalityTraits((prev) => ({
                                ...prev,
                                [trait]: Number(e.target.value),
                              }))
                            }
                            className="mt-1 w-full accent-[var(--accent)]"
                          />
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Values & Worldview section */}
                {customSection === "values" && (
                  <div className="space-y-5">
                    <div>
                      <label
                        htmlFor="values-text"
                        className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]"
                      >
                        Values (One Per Line)
                      </label>
                      <textarea
                        id="values-text"
                        className="mt-2 h-28 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                        value={valuesText}
                        onChange={(e) => setValuesText(e.target.value)}
                        placeholder={"honesty\ngrowth\nkindness"}
                      />
                    </div>
                    <div className="grid gap-4 sm:grid-cols-2">
                      {([
                        { key: "metaphysics", label: "Metaphysics" },
                        { key: "human_nature", label: "Human Nature" },
                        { key: "epistemology", label: "Epistemology" },
                        { key: "ethics", label: "Ethics" },
                      ] satisfies { key: keyof WorldviewForm; label: string }[]).map((field) => (
                        <div key={field.key}>
                          <label
                            htmlFor={`worldview-${field.key}`}
                            className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]"
                          >
                            {field.label}
                          </label>
                          <textarea
                            id={`worldview-${field.key}`}
                            className="mt-2 h-16 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                            value={worldview[field.key]}
                            onChange={(e) =>
                              setWorldview((prev) => ({
                                ...prev,
                                [field.key]: e.target.value,
                              }))
                            }
                            placeholder={`${field.label}...`}
                          />
                        </div>
                      ))}
                    </div>
                    <div>
                      <p className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                        Boundaries
                      </p>
                      <div className="mt-3 space-y-3">
                        {boundaries.map((b, idx) => (
                          <div key={idx} className="flex gap-2">
                            <input
                              aria-label={`Boundary ${idx + 1}`}
                              className="flex-1 rounded-xl border border-[var(--outline)] bg-white px-3 py-2 text-sm"
                              value={b.content}
                              onChange={(e) => updateBoundary(idx, "content", e.target.value)}
                              placeholder="I will not deceive people."
                            />
                            {boundaries.length > 1 ? (
                              <button
                                className="text-xs text-[var(--accent-strong)]"
                                onClick={() => removeBoundary(idx)}
                              >
                                Remove
                              </button>
                            ) : null}
                          </div>
                        ))}
                        <button
                          className="text-xs text-[var(--accent-strong)]"
                          onClick={addBoundary}
                          type="button"
                        >
                          + Add boundary
                        </button>
                      </div>
                    </div>
                  </div>
                )}

                {/* Goals & Relationship section */}
                {customSection === "goals" && (
                  <div className="space-y-5">
                    <div>
                      <label
                        htmlFor="interests-text"
                        className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]"
                      >
                        Interests (One Per Line)
                      </label>
                      <textarea
                        id="interests-text"
                        className="mt-2 h-20 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                        value={interestsText}
                        onChange={(e) => setInterestsText(e.target.value)}
                        placeholder={"philosophy\nsystems design\nmusic"}
                      />
                    </div>
                    <div>
                      <label
                        htmlFor="goals-purpose"
                        className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]"
                      >
                        Purpose
                      </label>
                      <textarea
                        id="goals-purpose"
                        className="mt-2 h-16 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                        value={purposeText}
                        onChange={(e) => setPurposeText(e.target.value)}
                        placeholder="Help the user grow, learn, and build."
                      />
                    </div>
                    <div>
                      <p className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                        Goals
                      </p>
                      <div className="mt-3 space-y-3">
                        {goals.map((g, idx) => (
                          <div key={idx} className="flex gap-2">
                            <input
                              aria-label={`Goal ${idx + 1} title`}
                              className="flex-1 rounded-xl border border-[var(--outline)] bg-white px-3 py-2 text-sm"
                              value={g.title}
                              onChange={(e) => updateGoal(idx, "title", e.target.value)}
                              placeholder="Short goal title"
                            />
                            {goals.length > 1 ? (
                              <button
                                className="text-xs text-[var(--accent-strong)]"
                                onClick={() => removeGoal(idx)}
                              >
                                Remove
                              </button>
                            ) : null}
                          </div>
                        ))}
                        <button
                          className="text-xs text-[var(--accent-strong)]"
                          onClick={addGoal}
                          type="button"
                        >
                          + Add goal
                        </button>
                      </div>
                    </div>
                    <div className="grid gap-4 sm:grid-cols-3">
                      <div>
                        <label
                          htmlFor="relationship-user-name"
                          className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]"
                        >
                          Your Name
                        </label>
                        <input
                          id="relationship-user-name"
                          className="mt-2 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                          value={relationship.user_name || userName}
                          onChange={(e) =>
                            setRelationship((prev) => ({ ...prev, user_name: e.target.value }))
                          }
                          placeholder={userName || "User"}
                        />
                      </div>
                      <div>
                        <label
                          htmlFor="relationship-type"
                          className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]"
                        >
                          Relationship Type
                        </label>
                        <input
                          id="relationship-type"
                          className="mt-2 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                          value={relationship.type}
                          onChange={(e) =>
                            setRelationship((prev) => ({ ...prev, type: e.target.value }))
                          }
                          placeholder="partner"
                        />
                      </div>
                      <div>
                        <label
                          htmlFor="relationship-purpose"
                          className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]"
                        >
                          Purpose
                        </label>
                        <input
                          id="relationship-purpose"
                          className="mt-2 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                          value={relationship.purpose}
                          onChange={(e) =>
                            setRelationship((prev) => ({ ...prev, purpose: e.target.value }))
                          }
                          placeholder="Co-develop, learn, build."
                        />
                      </div>
                    </div>
                  </div>
                )}

                {/* Custom submit/back buttons */}
                <div className="flex flex-wrap gap-3">
                  <button
                    className="rounded-full bg-[var(--foreground)] px-6 py-3 text-sm font-semibold text-white transition hover:bg-[var(--accent-strong)]"
                    onClick={handleCustomSubmit}
                    disabled={busy}
                  >
                    {busy ? "Saving..." : "Save & Continue to Consent"}
                  </button>
                  <button
                    className="rounded-full border border-[var(--outline)] px-6 py-3 text-sm font-semibold text-[var(--foreground)] transition hover:border-[var(--accent)]"
                    onClick={handleExportAsCard}
                    disabled={busy}
                  >
                    Save as Character Card
                  </button>
                  <button
                    className="rounded-full border border-[var(--outline)] px-6 py-3 text-sm font-semibold text-[var(--foreground)]"
                    onClick={() => setStage("choose_path")}
                    disabled={busy}
                  >
                    Back
                  </button>
                </div>
                {exportMsg && (
                  <p className="text-xs text-[var(--ink-soft)]">{exportMsg}</p>
                )}
              </div>
            )}

            {/* --- Consent Stage --- */}
            {stage === "consent" && (
              <div className="space-y-5">
                <p className="text-sm text-[var(--ink-soft)]">
                  Consent will be requested from both models. Existing contracts are reused
                  when available.
                </p>
                <div className="grid gap-4">
                  {[
                    { key: "conscious", label: "Conscious Model", config: llmConscious },
                    { key: "subconscious", label: "Subconscious Model", config: llmSubconscious },
                  ].map((entry) => {
                    const record = consentRecords[entry.key as LlmRole];
                    return (
                      <div
                        key={entry.key}
                        className="rounded-2xl border border-[var(--outline)] bg-white p-4 text-sm"
                      >
                        <p className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                          {entry.label}
                        </p>
                        <p className="mt-3">
                          Provider:{" "}
                          <span className="text-[var(--foreground)]">{entry.config.provider}</span>
                        </p>
                        <p>
                          Model:{" "}
                          <span className="text-[var(--foreground)]">
                            {entry.config.model || "unset"}
                          </span>
                        </p>
                        <p className="mt-3">
                          Decision:{" "}
                          <span className="text-[var(--foreground)]">
                            {record?.decision || "pending"}
                          </span>
                        </p>
                        {record?.signature ? (
                          <p className="mt-2">
                            Signature: <span className="font-mono">{record.signature}</span>
                          </p>
                        ) : null}
                        {(record?.decision === "decline" || record?.decision === "abstain") &&
                        record.exchange ? (
                          <ConsentExchangeView exchange={record.exchange} />
                        ) : null}
                      </div>
                    );
                  })}
                </div>
                {consentDeclined ? (
                  <p className="text-sm text-[var(--ink-soft)]">
                    The model didn&apos;t consent. It&apos;s your agent — change the model,
                    try again, or proceed anyway. The full request and response are recorded.
                  </p>
                ) : null}
                <div className="flex flex-col gap-3 sm:flex-row">
                  <button
                    className="rounded-full bg-[var(--foreground)] px-6 py-3 text-sm font-semibold text-white transition hover:bg-[var(--accent-strong)]"
                    onClick={handleConsentRequestAll}
                    disabled={busy}
                  >
                    {busy ? "Requesting..." : "Request Consent"}
                  </button>
                  <button
                    className="rounded-full border border-[var(--outline)] px-6 py-3 text-sm font-semibold text-[var(--foreground)]"
                    onClick={() => loadStatus().catch(() => undefined)}
                    disabled={busy}
                  >
                    Refresh
                  </button>
                  <button
                    className="rounded-full border border-[var(--outline)] px-6 py-3 text-sm font-semibold text-[var(--foreground)]"
                    onClick={handleChangeModel}
                    disabled={busy}
                  >
                    Change model
                  </button>
                  {consentDeclined ? (
                    <button
                      className="rounded-full bg-[var(--accent-strong)] px-6 py-3 text-sm font-semibold text-white transition hover:opacity-90"
                      onClick={handleProceedAnyway}
                      disabled={busy}
                      title="It's your agent — activate it even though the model didn't consent"
                    >
                      Proceed anyway
                    </button>
                  ) : null}
                  {statusStage === "complete" ? (
                    <button
                      className="rounded-full border border-[var(--outline)] px-6 py-3 text-sm font-semibold text-[var(--foreground)]"
                      onClick={() => setStage("complete")}
                      disabled={busy}
                    >
                      Continue
                    </button>
                  ) : null}
                </div>
                {consentDeclined ? (
                  <p className="rounded-2xl border border-[var(--outline)] bg-white p-4 text-sm text-[var(--ink-soft)]">
                    The agent has not consented yet. You can revise the initialization or
                    request consent again.
                  </p>
                ) : null}
              </div>
            )}

            {/* --- Complete Stage --- */}
            {stage === "complete" && (
              <div className="space-y-5">
                <p className="text-base text-[var(--ink-soft)]">
                  Initialization is complete. The heartbeat cycle may begin when the system
                  is running.
                </p>
                <div className="rounded-2xl border border-[var(--outline)] bg-white p-4 text-sm">
                  <p>Agent: {profile?.agent?.name || identity.name || "Hexis"}</p>
                </div>
                <button
                  className="rounded-full bg-[var(--foreground)] px-6 py-3 text-sm font-semibold text-white transition hover:bg-[var(--accent-strong)]"
                  onClick={() => router.push("/")}
                >
                  Enter Hexis
                </button>
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
