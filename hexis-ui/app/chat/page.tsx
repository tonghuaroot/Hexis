"use client";

import {
  Activity,
  BrainCircuit,
  Database,
  Eye,
  EyeOff,
  FileText,
  Lock,
  LockOpen,
  Send,
  Settings2,
  Trash2,
  Wrench,
  X,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import Image from "next/image";
import { Card } from "../components/ui/card";
import { Badge } from "../components/ui/badge";
import { Spinner } from "../components/ui/spinner";
import { normalizeMessagePresentation } from "../../lib/message-presentation";
import type { MessagePresentation } from "../../lib/message-presentation";
import { MessagePresentationView } from "./message-presentation";

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  presentation?: MessagePresentation;
};

// A large paste captured as an attachment instead of composer text; on send
// it is ingested as a document (POST /api/ingest) rather than inlined.
type PastedAttachment = {
  id: string;
  title: string;
  content: string;
  wordCount: number;
  // "private" keeps the ingested memories out of group-channel recall and
  // default HMX export (#92); toggled per-chip before sending.
  sensitivity: "private" | null;
};

// Pastes longer than this become attachments (matching the Claude/ChatGPT
// composer convention) so huge texts go through document ingestion instead
// of flooding the conversation turn.
const PASTE_ATTACH_THRESHOLD = 2000;

// The turn's system prompt carries the attachment text up to this cap so the
// agent can discuss the document immediately; ingestion holds the full text.
const ATTACHMENT_PROMPT_CHARS = 16000;

function attachmentTitle(content: string): string {
  const firstLine = content.split("\n").map((line) => line.trim()).find(Boolean) || "";
  if (!firstLine) return "Pasted text";
  if (firstLine.length <= 80) return firstLine;
  const words = firstLine.split(/\s+/).slice(0, 8).join(" ");
  return `${words}…`;
}

function attachmentAddendum(attachment: PastedAttachment): string {
  const truncated = attachment.content.length > ATTACHMENT_PROMPT_CHARS;
  const body = attachment.content.slice(0, ATTACHMENT_PROMPT_CHARS);
  return [
    `----- ATTACHED DOCUMENT: ${attachment.title} -----`,
    "The user attached this document to their message. It is also being ingested into your durable memory (recall or open_memory can retrieve it later).",
    "",
    body,
    truncated
      ? "\n[Document truncated here for the live turn — the full text is in memory via ingestion.]"
      : "",
  ].join("\n");
}

type LogEvent = {
  id: string;
  category: "phase" | "subconscious" | "model" | "tool" | "memory" | "error";
  title: string;
  detail: string;
  raw?: unknown;
  ts: number;
};

type AgentStatus = {
  configured?: boolean;
  agent_name?: string;
  portrait_url?: string | null;
  mood?: string;
  valence?: number | null;
};

type SsePayload = Record<string, unknown>;

const promptAddendaOptions = [
  { id: "philosophy", label: "Philosophy Grounding" },
  { id: "letter", label: "Letter From Claude" },
];

const SESSION_KEY = "hexis-chat-messages";
const SESSION_ID_KEY = "hexis-chat-session-id";
const MAX_ACTIVITY_EVENTS = 60;
const ACTIVITY_TTL_MS = 30 * 60 * 1000;

function loadSessionId(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return sessionStorage.getItem(SESSION_ID_KEY);
  } catch {
    return null;
  }
}

function saveSessionId(id: string) {
  if (typeof window === "undefined") return;
  try {
    sessionStorage.setItem(SESSION_ID_KEY, id);
  } catch {
    // ignore quota errors
  }
}

function loadSession(): ChatMessage[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = sessionStorage.getItem(SESSION_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function saveSession(messages: ChatMessage[]) {
  if (typeof window === "undefined") return;
  try {
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(messages));
  } catch {
    // ignore quota errors
  }
}

export default function ChatPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [events, setEvents] = useState<LogEvent[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [attachments, setAttachments] = useState<PastedAttachment[]>([]);
  const [ready, setReady] = useState<boolean | null>(null);
  const [agentStatus, setAgentStatus] = useState<AgentStatus>({});
  const [promptAddenda, setPromptAddenda] = useState<string[]>([]);
  const [currentPhase, setCurrentPhase] = useState<string | null>(null);
  const [showSearchConfig, setShowSearchConfig] = useState(false);
  const [searchConfigValue, setSearchConfigValue] = useState("");
  const [searchConfigSaving, setSearchConfigSaving] = useState(false);
  const [searchConfigError, setSearchConfigError] = useState<string | null>(null);
  const [searchConfigNotice, setSearchConfigNotice] = useState<string | null>(null);
  const [historyIndex, setHistoryIndex] = useState<number | null>(null);
  const [historyDraft, setHistoryDraft] = useState("");
  const [showInspector, setShowInspector] = useState(false);
  const [activityFilters, setActivityFilters] = useState<Set<LogEvent["category"]>>(
    new Set(["subconscious", "model", "tool", "memory", "error"]),
  );
  const scrollRef = useRef<HTMLDivElement>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const historyPayload = useMemo(
    () =>
      messages
        .filter((msg) => msg.content.trim())
        .map((msg) => ({ role: msg.role, content: msg.content })),
    [messages]
  );

  // Load session on mount
  useEffect(() => {
    const saved = loadSession();
    if (saved.length > 0) setMessages(saved);
  }, []);

  // Save session on message change
  useEffect(() => {
    if (messages.length > 0) saveSession(messages);
  }, [messages]);

  useEffect(() => {
    const desktop = window.matchMedia("(min-width: 1024px)");
    const sync = () => setShowInspector(desktop.matches);
    const frame = requestAnimationFrame(sync);
    desktop.addEventListener("change", sync);
    return () => {
      cancelAnimationFrame(frame);
      desktop.removeEventListener("change", sync);
    };
  }, []);

  useEffect(() => {
    const load = async () => {
      const res = await fetch("/api/status", { cache: "no-store" });
      if (!res.ok) {
        setReady(false);
        return;
      }
      const data = await res.json();
      setAgentStatus(data);
      setReady(data?.configured === true);
    };
    load().catch(() => setReady(false));
  }, []);

  useEffect(() => {
    if (!scrollRef.current) return;
    scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages]);

  useEffect(() => {
    if (!logRef.current) return;
    logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [events]);

  useEffect(() => {
    const timer = setInterval(() => {
      const cutoff = Date.now() - ACTIVITY_TTL_MS;
      setEvents((current) => current.filter((event) => event.ts >= cutoff));
    }, 60000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    const latestAssistant = [...messages]
      .reverse()
      .find((msg) => msg.role === "assistant" && msg.content);
    if (latestAssistant && isSearchToolMisconfigured(latestAssistant.content)) {
      setShowSearchConfig(true);
    }
  }, [messages]);

  const appendLog = (event: LogEvent) => {
    setEvents((prev) => [...prev.slice(-(MAX_ACTIVITY_EVENTS - 1)), event]);
  };

  const updateAssistantMessage = (assistantId: string, text: string) => {
    setMessages((prev) =>
      prev.map((msg) =>
        msg.id === assistantId ? { ...msg, content: msg.content + text } : msg
      )
    );
  };

  const setAssistantPresentation = (assistantId: string, value: unknown) => {
    const presentation = normalizeMessagePresentation(value);
    if (!presentation) return;
    setMessages((prev) =>
      prev.map((msg) =>
        msg.id === assistantId ? { ...msg, presentation } : msg
      )
    );
  };

  const handleConfigureSearchTool = async () => {
    const value = searchConfigValue.trim();
    if (!value) {
      setSearchConfigError("Enter a Tavily key or env reference (for example: env:TAVILY_API_KEY).");
      return;
    }

    setSearchConfigSaving(true);
    setSearchConfigError(null);
    setSearchConfigNotice(null);
    try {
      const payload = value.startsWith("env:")
        ? { key_ref: value, enable: true }
        : { api_key: value, enable: true };
      const res = await fetch("/api/settings/tools/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data?.error || `Failed with status ${res.status}`);
      }
      appendLog({
        id: crypto.randomUUID(),
        category: "tool",
        title: "Search Tool",
        detail: "Configured web_search. Retry your question to run live search.",
        ts: Date.now(),
      });
      setShowSearchConfig(false);
      setSearchConfigValue("");
      setSearchConfigNotice("Search tool configured. Retry your question.");
    } catch (err: unknown) {
      setSearchConfigError(
        err instanceof Error ? err.message : "Failed to configure search tool."
      );
    } finally {
      setSearchConfigSaving(false);
    }
  };

  const handlePaste = (event: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const pasted = event.clipboardData?.getData("text") ?? "";
    if (pasted.length <= PASTE_ATTACH_THRESHOLD) return;
    event.preventDefault();
    setAttachments((prev) => [
      ...prev,
      {
        id: crypto.randomUUID(),
        title: attachmentTitle(pasted),
        content: pasted,
        wordCount: pasted.split(/\s+/).filter(Boolean).length,
        sensitivity: null,
      },
    ]);
  };

  const removeAttachment = (id: string) => {
    setAttachments((prev) => prev.filter((attachment) => attachment.id !== id));
  };

  const toggleAttachmentPrivacy = (id: string) => {
    setAttachments((prev) =>
      prev.map((attachment) =>
        attachment.id === id
          ? { ...attachment, sensitivity: attachment.sensitivity === "private" ? null : "private" }
          : attachment
      )
    );
  };

  const handleSend = async () => {
    if ((!input.trim() && attachments.length === 0) || sending) return;

    // Attachments ingest as documents (durable) AND ride the turn's prompt
    // addenda (immediate sight); the visible message carries only a note.
    const toIngest = attachments;
    setAttachments([]);
    const attachmentAddenda = toIngest.map(attachmentAddendum);
    const ingestNotes: string[] = [];
    for (const attachment of toIngest) {
      try {
        const res = await fetch("/api/ingest", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            content: attachment.content,
            title: attachment.title,
            mode: "fast",
            sensitivity: attachment.sensitivity ?? undefined,
          }),
        });
        if (res.ok) {
          ingestNotes.push(
            `[Attached document "${attachment.title}" (${attachment.wordCount} words) — being ingested into memory${
              attachment.sensitivity === "private" ? " as private (kept out of group conversations and exports)" : ""
            }]`
          );
        } else {
          const detail = await res.text();
          ingestNotes.push(
            `[Attached document "${attachment.title}" could not be ingested: ${res.status}]`
          );
          appendLog({
            id: crypto.randomUUID(),
            category: "error",
            title: "Ingest error",
            detail: `Attachment "${attachment.title}" failed (${res.status}): ${detail.slice(0, 200)}`,
            ts: Date.now(),
          });
        }
      } catch (err) {
        ingestNotes.push(
          `[Attached document "${attachment.title}" could not be ingested: network error]`
        );
        appendLog({
          id: crypto.randomUUID(),
          category: "error",
          title: "Ingest error",
          detail: `Attachment "${attachment.title}": ${err instanceof Error ? err.message : String(err)}`,
          ts: Date.now(),
        });
      }
    }

    const messageText = [input.trim(), ...ingestNotes].filter(Boolean).join("\n\n");
    const userMessage: ChatMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content: messageText,
    };
    const assistantMessage: ChatMessage = {
      id: crypto.randomUUID(),
      role: "assistant",
      content: "",
    };
    setMessages((prev) => [...prev, userMessage, assistantMessage]);
    setInput("");
    setHistoryIndex(null);
    setHistoryDraft("");
    setSending(true);
    setCurrentPhase(null);
    setSearchConfigNotice(null);

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: userMessage.content,
          history: historyPayload,
          prompt_addenda: [...promptAddenda, ...attachmentAddenda],
          session_id: loadSessionId(),
        }),
      });
      if (!res.ok || !res.body) {
        appendLog({
          id: crypto.randomUUID(),
          category: "error",
          title: "Chat error",
          detail: `Failed to reach chat endpoint (${res.status}).`,
          ts: Date.now(),
        });
        setSending(false);
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split("\n\n");
        buffer = parts.pop() || "";
        for (const part of parts) {
          const lines = part.split("\n");
          let eventType = "message";
          let data = "";
          for (const line of lines) {
            if (line.startsWith("event:")) {
              eventType = line.replace("event:", "").trim();
            }
            if (line.startsWith("data:")) {
              data += line.replace("data:", "").trim();
            }
          }
          if (!data) continue;
          let payload: SsePayload = {};
          try {
            const parsed = JSON.parse(data);
            payload =
              parsed && typeof parsed === "object" && !Array.isArray(parsed)
                ? (parsed as SsePayload)
                : { raw: data };
          } catch {
            payload = { raw: data };
          }

          if (eventType === "token") {
            const phase = asString(payload.phase);
            const text = asString(payload.text);
            setCurrentPhase(phase);
            if (phase === "conscious_final" && text) {
              updateAssistantMessage(assistantMessage.id, text);
              if (isSearchToolMisconfigured(text)) {
                setShowSearchConfig(true);
              }
            }
          }

          if (eventType === "phase_start") {
            const phase = asString(payload.phase, "phase");
            setCurrentPhase(phase);
            appendLog({
              id: crypto.randomUUID(),
              category: "phase",
              title: streamLabel(phase),
              detail: "started",
              ts: Date.now(),
            });
          }

          if (eventType === "phase_end" && asString(payload.phase) === "subconscious") {
            const output = asRecord(payload.output);
            appendLog({
              id: crypto.randomUUID(),
              category: "subconscious",
              title: "Subconscious appraisal",
              detail: summarizeSubconscious(output),
              raw: output,
              ts: Date.now(),
            });
          }

          if (eventType === "trace") {
            const request = asString(payload.kind) === "llm_request";
            appendLog({
              // Request/response traces share payload.id for correlation, so
              // the log entry mints its own key; the pair id stays in raw.
              id: crypto.randomUUID(),
              category: "model",
              title: request ? "Model request" : "Model response",
              detail: `${asString(payload.provider, "provider")}/${asString(payload.model, "model")} · iteration ${String(payload.iteration ?? "-")}`,
              raw: payload,
              ts: Date.now(),
            });
          }

          if (eventType === "log") {
            const detail = asString(payload.detail);
            const logKind = asString(payload.kind).toLowerCase();
            const title = asString(payload.title) || logKind || "Activity";
            appendLog({
              id: crypto.randomUUID(),
              category: logKind.includes("memory") || title.toLowerCase().includes("memory") ? "memory" : "tool",
              title,
              detail,
              raw: payload,
              ts: Date.now(),
            });
            if (isSearchToolMisconfigured(detail)) {
              setShowSearchConfig(true);
            }
          }

          if (eventType === "error") {
            const detail = asString(payload.message, "Unknown error");
            appendLog({
              id: crypto.randomUUID(),
              category: "error",
              title: "Error",
              detail,
              ts: Date.now(),
            });
            if (isSearchToolMisconfigured(String(detail))) {
              setShowSearchConfig(true);
            }
          }
          if (eventType === "done") {
            setAssistantPresentation(assistantMessage.id, payload.presentation);
            if (typeof payload.session_id === "string" && payload.session_id) {
              saveSessionId(payload.session_id);
            }
          }
        }
      }
    } catch (err: unknown) {
      appendLog({
        id: crypto.randomUUID(),
        category: "error",
        title: "Chat error",
        detail: err instanceof Error ? err.message : "Unknown error",
        ts: Date.now(),
      });
    } finally {
      setSending(false);
      setCurrentPhase(null);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    const userHistory = messages
      .filter((msg) => msg.role === "user" && msg.content.trim())
      .map((msg) => msg.content);

    if (e.key === "ArrowUp" && userHistory.length > 0) {
      e.preventDefault();
      let nextIndex = historyIndex;
      if (nextIndex === null) {
        setHistoryDraft(input);
        nextIndex = userHistory.length - 1;
      } else {
        nextIndex = Math.max(0, nextIndex - 1);
      }
      setHistoryIndex(nextIndex);
      setInput(userHistory[nextIndex] ?? "");
      requestAnimationFrame(() => {
        const el = textareaRef.current;
        if (el) {
          const pos = el.value.length;
          el.setSelectionRange(pos, pos);
        }
      });
      return;
    }

    if (e.key === "ArrowDown" && historyIndex !== null) {
      e.preventDefault();
      if (historyIndex < userHistory.length - 1) {
        const nextIndex = historyIndex + 1;
        setHistoryIndex(nextIndex);
        setInput(userHistory[nextIndex] ?? "");
      } else {
        setHistoryIndex(null);
        setInput(historyDraft);
      }
      requestAnimationFrame(() => {
        const el = textareaRef.current;
        if (el) {
          const pos = el.value.length;
          el.setSelectionRange(pos, pos);
        }
      });
      return;
    }

    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const filteredEvents = events.filter((event) => activityFilters.has(event.category));
  const toggleActivityFilter = (category: LogEvent["category"]) => {
    setActivityFilters((current) => {
      const next = new Set(current);
      if (next.has(category)) next.delete(category);
      else next.add(category);
      return next;
    });
  };

  if (ready === false) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Card className="max-w-md text-center">
          <h1 className="font-display text-2xl">Initialization Required</h1>
          <p className="mt-3 text-sm text-[var(--ink-soft)]">
            Complete the initialization ritual before entering the main chat.
          </p>
          <a
            className="mt-6 inline-flex rounded-full bg-[var(--foreground)] px-6 py-3 text-sm font-semibold text-white"
            href="/init"
          >
            Go to Initialization
          </a>
        </Card>
      </div>
    );
  }

  if (ready === null) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Spinner label="Loading status..." />
      </div>
    );
  }

  return (
    <div className="app-shell h-[calc(100vh-3.5rem)] overflow-hidden lg:h-screen">
      <div className="mx-auto flex h-full max-w-[1600px]">
        <section className="flex min-w-0 flex-1 flex-col bg-white">
          <header className="flex h-16 items-center justify-between gap-4 border-b border-[var(--outline)] px-4 sm:px-6">
            <div className="flex min-w-0 items-center gap-3">
              {agentStatus.portrait_url ? (
                <Image src={agentStatus.portrait_url} alt="" width={40} height={40} unoptimized className="h-10 w-10 rounded-md object-cover" />
              ) : (
                <div className="flex h-10 w-10 items-center justify-center rounded-md bg-[var(--foreground)] font-display text-white">
                  {(agentStatus.agent_name || "H").slice(0, 1)}
                </div>
              )}
              <div className="min-w-0">
                <h1 className="truncate text-sm font-semibold">{agentStatus.agent_name || "Hexis"}</h1>
                <p className="truncate text-xs text-[var(--ink-soft)]">
                  {sending ? phaseDescription(currentPhase || "") : agentStatus.mood || "Ready"}
                  {agentStatus.valence != null ? ` · valence ${agentStatus.valence >= 0 ? "+" : ""}${agentStatus.valence.toFixed(2)}` : ""}
                </p>
              </div>
            </div>
            <div className="flex items-center gap-1">
              <details className="relative">
                <summary className="flex h-9 cursor-pointer list-none items-center gap-2 rounded-md px-3 text-xs font-medium text-[var(--ink-soft)] hover:bg-[var(--surface-strong)]">
                  <Settings2 size={16} /> Options
                </summary>
                <div className="absolute right-0 top-11 z-30 w-64 rounded-lg border border-[var(--outline)] bg-white p-4 shadow-lg">
                  <p className="text-xs font-semibold uppercase text-[var(--ink-soft)]">Prompt modules</p>
                  <div className="mt-3 space-y-3">
                    {promptAddendaOptions.map((option) => (
                      <label key={option.id} className="flex items-center gap-3 text-sm">
                        <input
                          type="checkbox"
                          className="h-4 w-4 accent-[var(--teal)]"
                          checked={promptAddenda.includes(option.id)}
                          onChange={() => setPromptAddenda((current) => current.includes(option.id) ? current.filter((item) => item !== option.id) : [...current, option.id])}
                        />
                        {option.label}
                      </label>
                    ))}
                  </div>
                </div>
              </details>
              <button
                type="button"
                aria-label={showInspector ? "Hide activity" : "Show activity"}
                title={showInspector ? "Hide activity" : "Show activity"}
                onClick={() => setShowInspector((value) => !value)}
                className={`flex h-9 w-9 items-center justify-center rounded-md ${showInspector ? "bg-[var(--surface-strong)] text-[var(--foreground)]" : "text-[var(--ink-soft)] hover:bg-[var(--surface-strong)]"}`}
              >
                {showInspector ? <EyeOff size={17} /> : <Eye size={17} />}
              </button>
            </div>
          </header>

          {showSearchConfig ? (
            <div className="border-b border-amber-200 bg-amber-50 px-4 py-3 sm:px-6">
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                <span className="text-sm font-medium text-amber-900">Web search needs a Tavily key</span>
                <input value={searchConfigValue} onChange={(event) => setSearchConfigValue(event.target.value)} placeholder="tvly-... or env:TAVILY_API_KEY" className="min-w-0 flex-1 rounded-md border border-amber-200 bg-white px-3 py-2 text-sm" />
                <button onClick={handleConfigureSearchTool} disabled={searchConfigSaving} className="rounded-md bg-[var(--foreground)] px-3 py-2 text-xs font-semibold text-white disabled:opacity-50">{searchConfigSaving ? "Saving" : "Enable"}</button>
                <button onClick={() => setShowSearchConfig(false)} className="px-2 py-2 text-xs text-amber-800">Dismiss</button>
              </div>
              {searchConfigError ? <p className="mt-1 text-xs text-red-700">{searchConfigError}</p> : null}
            </div>
          ) : null}
          {searchConfigNotice ? <div className="border-b border-emerald-200 bg-emerald-50 px-6 py-2 text-xs text-emerald-700">{searchConfigNotice}</div> : null}

          <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-6 sm:px-8">
            <div className="mx-auto max-w-3xl space-y-6">
              {messages.length === 0 ? (
                <div className="flex min-h-80 flex-col items-center justify-center text-center">
                  {agentStatus.portrait_url ? <Image src={agentStatus.portrait_url} alt="" width={80} height={80} unoptimized className="h-20 w-20 rounded-lg object-cover" /> : <BrainCircuit size={38} className="text-[var(--teal)]" />}
                  <h2 className="mt-4 font-display text-2xl">Conversation with {agentStatus.agent_name || "Hexis"}</h2>
                  <p className="mt-2 text-sm text-[var(--ink-soft)]">What is on your mind?</p>
                </div>
              ) : null}
              {messages.map((message) => (
                <div key={message.id} className={`flex gap-3 ${message.role === "user" ? "justify-end" : "justify-start"}`}>
                  {message.role === "assistant" ? (
                    agentStatus.portrait_url ? <Image src={agentStatus.portrait_url} alt="" width={32} height={32} unoptimized className="mt-1 h-8 w-8 flex-none rounded-md object-cover" /> : <div className="mt-1 flex h-8 w-8 flex-none items-center justify-center rounded-md bg-[var(--surface-strong)] text-xs font-semibold">H</div>
                  ) : null}
                  <div className={`max-w-[85%] text-sm leading-6 ${message.role === "user" ? "rounded-lg bg-[var(--foreground)] px-4 py-3 text-white" : "min-w-0 flex-1 py-1 text-[var(--foreground)]"}`}>
                    {message.role === "assistant" ? (
                      message.presentation ? <MessagePresentationView presentation={message.presentation} /> : message.content ? <MessagePresentationView presentation={{ tone: "neutral", blocks: [{ type: "text", text: message.content }] }} /> : <Spinner label="Thinking..." />
                    ) : <p className="whitespace-pre-wrap">{message.content}</p>}
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="border-t border-[var(--outline)] bg-white px-4 py-3 sm:px-6">
            {attachments.length > 0 ? (
              <div className="mx-auto mb-2 flex max-w-3xl flex-wrap gap-2">
                {attachments.map((attachment) => (
                  <span key={attachment.id} className="flex items-center gap-2 rounded-md border border-[var(--outline)] bg-[#f5f7f5] px-2 py-1 text-xs">
                    <FileText size={13} className="flex-none text-[var(--teal)]" />
                    <span className="max-w-56 truncate font-medium">{attachment.title}</span>
                    <span className="text-[var(--ink-soft)]">{attachment.wordCount.toLocaleString()} words</span>
                    <button
                      type="button"
                      aria-label={
                        attachment.sensitivity === "private"
                          ? `Make attachment ${attachment.title} shareable`
                          : `Mark attachment ${attachment.title} private`
                      }
                      title={
                        attachment.sensitivity === "private"
                          ? "Private: kept out of group conversations and exports. Click to make shareable."
                          : "Shareable. Click to keep out of group conversations and exports."
                      }
                      onClick={() => toggleAttachmentPrivacy(attachment.id)}
                      className={`flex flex-none items-center gap-1 rounded p-0.5 ${
                        attachment.sensitivity === "private"
                          ? "text-[var(--teal)]"
                          : "text-[var(--ink-soft)] hover:bg-[var(--outline)] hover:text-[var(--foreground)]"
                      }`}
                    >
                      {attachment.sensitivity === "private" ? <Lock size={12} /> : <LockOpen size={12} />}
                      {attachment.sensitivity === "private" ? <span className="font-medium">Private</span> : null}
                    </button>
                    <button
                      type="button"
                      aria-label={`Remove attachment ${attachment.title}`}
                      title="Remove"
                      onClick={() => removeAttachment(attachment.id)}
                      className="flex-none rounded p-0.5 text-[var(--ink-soft)] hover:bg-[var(--outline)] hover:text-[var(--foreground)]"
                    >
                      <X size={12} />
                    </button>
                  </span>
                ))}
              </div>
            ) : null}
            <div className="mx-auto flex max-w-3xl items-end gap-2 rounded-lg border border-[var(--outline)] bg-white p-2 focus-within:border-[var(--teal)] focus-within:ring-2 focus-within:ring-[var(--teal)]/10">
              <textarea
                ref={textareaRef}
                aria-label={`Message ${agentStatus.agent_name || "Hexis"}`}
                className="max-h-36 min-h-10 flex-1 resize-none border-0 bg-transparent px-2 py-2 text-sm outline-none"
                placeholder={`Message ${agentStatus.agent_name || "Hexis"}`}
                value={input}
                onChange={(event) => { if (historyIndex !== null) setHistoryIndex(null); setInput(event.target.value); }}
                onKeyDown={handleKeyDown}
                onPaste={handlePaste}
                rows={1}
              />
              <button type="button" aria-label="Send message" title="Send" onClick={handleSend} disabled={sending || (!input.trim() && attachments.length === 0)} className="flex h-10 w-10 flex-none items-center justify-center rounded-md bg-[var(--foreground)] text-white hover:bg-[var(--teal)] disabled:opacity-35">
                <Send size={17} />
              </button>
            </div>
          </div>
        </section>

        {showInspector ? (
          <aside className="fixed inset-y-14 right-0 z-20 flex w-full flex-col border-l border-[var(--outline)] bg-[#f8faf8] sm:w-[390px] lg:static lg:inset-auto lg:w-[380px]">
            <div className="flex h-16 items-center justify-between border-b border-[var(--outline)] px-4">
              <div><h2 className="text-sm font-semibold">Activity</h2><p className="text-xs text-[var(--ink-soft)]">{filteredEvents.length} events</p></div>
              <div className="flex items-center gap-1">
                <button type="button" title="Clear activity" aria-label="Clear activity" onClick={() => setEvents([])} className="flex h-8 w-8 items-center justify-center rounded-md text-[var(--ink-soft)] hover:bg-[var(--surface-strong)]"><Trash2 size={16} /></button>
                <button type="button" title="Close activity" aria-label="Close activity" onClick={() => setShowInspector(false)} className="flex h-8 w-8 items-center justify-center rounded-md text-[var(--ink-soft)] hover:bg-[var(--surface-strong)] lg:hidden"><X size={17} /></button>
              </div>
            </div>
            <div className="flex flex-wrap gap-1 border-b border-[var(--outline)] p-3">
              <FilterButton icon={BrainCircuit} label="Subconscious" active={activityFilters.has("subconscious")} onClick={() => toggleActivityFilter("subconscious")} />
              <FilterButton icon={Database} label="Memory" active={activityFilters.has("memory")} onClick={() => toggleActivityFilter("memory")} />
              <FilterButton icon={Wrench} label="Tools" active={activityFilters.has("tool")} onClick={() => toggleActivityFilter("tool")} />
              <FilterButton icon={Activity} label="Models" active={activityFilters.has("model")} onClick={() => toggleActivityFilter("model")} />
            </div>
            <div ref={logRef} className="flex-1 overflow-y-auto">
              {filteredEvents.length === 0 ? <p className="p-5 text-sm text-[var(--ink-soft)]">No matching activity.</p> : filteredEvents.map((event) => (
                <details key={event.id} className={`border-b border-[var(--outline)] px-4 py-3 ${event.category === "error" ? "bg-red-50" : "bg-white"}`}>
                  <summary className="cursor-pointer list-none">
                    <div className="flex items-center justify-between gap-3"><span className="text-xs font-semibold">{event.title}</span><Badge variant={event.category === "subconscious" ? "accent" : event.category === "error" ? "error" : "muted"}>{event.category}</Badge></div>
                    <p className="mt-1 line-clamp-2 text-xs leading-5 text-[var(--ink-soft)]">{event.detail || "No summary"}</p>
                  </summary>
                  {event.raw !== undefined ? <pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-md bg-[#eef2ef] p-3 text-xs leading-5">{JSON.stringify(event.raw, null, 2)}</pre> : null}
                </details>
              ))}
            </div>
          </aside>
        ) : null}
      </div>
    </div>
  );
}

function FilterButton({ icon: Icon, label, active, onClick }: { icon: LucideIcon; label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`flex items-center gap-1.5 rounded-md px-2 py-1.5 text-xs font-medium ${active ? "bg-[var(--foreground)] text-white" : "bg-white text-[var(--ink-soft)] hover:bg-[var(--surface-strong)]"}`}
    >
      <Icon size={13} />
      {label}
    </button>
  );
}

function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function summarizeSubconscious(output: Record<string, unknown>): string {
  const signals = asRecord(output.signals);
  const emotion = asRecord(signals.emotional_state);
  const parts: string[] = [];
  const primary = asString(emotion.primary_emotion);
  if (primary) {
    const valence = typeof emotion.valence === "number" ? emotion.valence : null;
    parts.push(`${primary}${valence !== null ? ` · valence ${valence >= 0 ? "+" : ""}${valence.toFixed(2)}` : ""}`);
  }
  const reaction = asString(signals.subconscious_response);
  if (reaction) parts.push(reaction);
  const memories = Array.isArray(signals.salient_memories) ? signals.salient_memories.length : 0;
  if (memories) parts.push(`${memories} salient ${memories === 1 ? "memory" : "memories"}`);
  return parts.join(" · ") || `${asString(output.provider, "provider")}/${asString(output.model, "model")}`;
}

function streamLabel(phase: string) {
  switch (phase) {
    case "subconscious":
      return "Subconscious";
    case "conscious_plan":
      return "Conscious Plan";
    case "conscious_final":
      return "Conscious Response";
    default:
      return phase || "Stream";
  }
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function phaseDescription(phase: string) {
  switch (phase) {
    case "subconscious":
      return "Running subconscious processes...";
    case "conscious_plan":
      return "Planning response...";
    case "conscious_final":
      return "Generating response...";
    default:
      return "Thinking...";
  }
}

function isSearchToolMisconfigured(text: string): boolean {
  const normalized = (text || "").toLowerCase();
  if (!normalized) return false;
  return (
    normalized.includes("web search api key not configured") ||
    (normalized.includes("web search") && normalized.includes("not configured")) ||
    normalized.includes("tavily_api_key")
  );
}
