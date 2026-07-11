"use client";

import { Activity, BrainCircuit, ChevronRight, Cpu, MessageCircle, Shield, Wrench } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Badge } from "../components/ui/badge";
import { PageHeader } from "../components/ui/page-header";
import { Spinner } from "../components/ui/spinner";

type SettingsData = {
  groups: Record<string, Record<string, unknown>>;
  llm: Record<string, unknown>;
  heartbeat: Record<string, unknown>;
  agent: Record<string, unknown>;
  tools: Record<string, unknown>;
};

const TABS = ["models", "autonomy", "tools", "advanced"] as const;
type Tab = (typeof TABS)[number];

const MODEL_ROLES = [
  { key: "llm.chat", label: "Conversation", icon: MessageCircle },
  { key: "llm.heartbeat", label: "Heartbeat", icon: Activity },
  { key: "llm.subconscious", label: "Subconscious", icon: BrainCircuit },
  { key: "llm.recmem", label: "Memory maintenance", icon: Cpu },
  { key: "llm.summarization", label: "Summarization", icon: Cpu },
  { key: "llm.skill_improvement", label: "Skill improvement", icon: Wrench },
];

export default function SettingsPage() {
  const [data, setData] = useState<SettingsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("models");

  const fetchSettings = useCallback(async () => {
    try {
      const response = await fetch("/api/settings", { cache: "no-store" });
      if (!response.ok) throw new Error(`Failed to load settings (${response.status})`);
      setData(await response.json());
      setError(null);
    } catch (requestError: unknown) {
      setError(requestError instanceof Error ? requestError.message : "Failed to load settings.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchSettings();
  }, [fetchSettings]);

  if (loading) return <div className="flex min-h-screen items-center justify-center"><Spinner label="Loading settings..." /></div>;
  if (!data) {
    return <div className="flex min-h-screen items-center justify-center px-6"><div className="max-w-md rounded-lg border border-red-200 bg-white p-5"><p className="text-sm text-red-700">{error || "Unable to load settings."}</p><button onClick={() => { setLoading(true); fetchSettings(); }} className="mt-4 rounded-md bg-[var(--foreground)] px-4 py-2 text-sm font-semibold text-white">Retry</button></div></div>;
  }

  const toolsConfig = asRecord(data.tools.tools);
  const contexts = asRecord(toolsConfig.context_overrides);
  const allowedActions = arrayOfStrings(data.heartbeat["heartbeat.allowed_actions"]);

  return (
    <div className="app-shell">
      <div className="mx-auto max-w-6xl px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
        <div className="flex items-center justify-between gap-4 border-b border-[var(--outline)] pb-5">
          <PageHeader title="Settings" subtitle="Runtime configuration" />
          <Link href="/init" className="flex items-center rounded-lg border border-[var(--outline)] bg-white px-3 py-2 text-sm font-semibold hover:bg-[var(--surface-strong)]">Reconfigure <ChevronRight size={15} className="ml-1" /></Link>
        </div>

        <div className="mt-5 flex gap-1 overflow-x-auto border-b border-[var(--outline)]" role="tablist">
          {TABS.map((value) => (
            <button key={value} type="button" role="tab" aria-selected={tab === value} onClick={() => setTab(value)} className={`flex-none border-b-2 px-4 py-3 text-sm font-medium capitalize ${tab === value ? "border-[var(--teal)] text-[var(--foreground)]" : "border-transparent text-[var(--ink-soft)] hover:text-[var(--foreground)]"}`}>{value}</button>
          ))}
        </div>

        <div className="mt-6">
          {tab === "models" ? (
            <section>
              <div className="grid gap-3 md:grid-cols-2">
                {MODEL_ROLES.map(({ key, label, icon: Icon }) => {
                  const config = asRecord(data.llm[key]);
                  const inherited = Object.keys(config).length === 0;
                  return (
                    <div key={key} className="rounded-lg border border-[var(--outline)] bg-white p-4">
                      <div className="flex items-start gap-3">
                        <div className="flex h-9 w-9 items-center justify-center rounded-md bg-[var(--surface-strong)] text-[var(--teal)]"><Icon size={17} /></div>
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center justify-between gap-3"><h2 className="text-sm font-semibold">{label}</h2>{inherited ? <Badge variant="muted">inherited</Badge> : null}</div>
                          <p className="mt-2 truncate text-sm">{asString(config.model, "Default model")}</p>
                          <p className="mt-0.5 truncate text-xs text-[var(--ink-soft)]">{asString(config.provider, "Uses fallback configuration")}</p>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </section>
          ) : null}

          {tab === "autonomy" ? (
            <section className="space-y-5">
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <SettingMetric label="Interval" value={`${numberValue(data.heartbeat["heartbeat.heartbeat_interval_minutes"], 60)} min`} />
                <SettingMetric label="Maximum energy" value={String(numberValue(data.heartbeat["heartbeat.max_energy"], 20))} />
                <SettingMetric label="Energy regeneration" value={String(numberValue(data.heartbeat["heartbeat.base_regeneration"], 10))} />
                <SettingMetric label="Contact cooldown" value={`${numberValue(data.heartbeat["heartbeat.user_contact_cooldown_hours"], 0)} hr`} />
              </div>
              <div className="rounded-lg border border-[var(--outline)] bg-white">
                <div className="flex items-center justify-between border-b border-[var(--outline)] px-5 py-4"><h2 className="text-sm font-semibold">Allowed heartbeat actions</h2><Badge variant="teal">{allowedActions.length}</Badge></div>
                <div className="flex flex-wrap gap-2 p-5">{allowedActions.map((action) => <Badge key={action} variant="muted">{humanize(action)}</Badge>)}</div>
              </div>
              <div className="rounded-lg border border-[var(--outline)] bg-white p-5">
                <div className="flex items-center justify-between"><span className="flex items-center gap-2 text-sm font-semibold"><BrainCircuit size={17} /> Recursive reasoning</span><Badge variant={data.heartbeat["heartbeat.use_rlm"] === true ? "success" : "muted"}>{data.heartbeat["heartbeat.use_rlm"] === true ? "Enabled" : "Disabled"}</Badge></div>
              </div>
            </section>
          ) : null}

          {tab === "tools" ? (
            <section className="space-y-5">
              <div className="grid gap-4 lg:grid-cols-2">
                <PermissionPanel name="Conversation" value={asRecord(contexts.chat)} />
                <PermissionPanel name="Heartbeat" value={asRecord(contexts.heartbeat)} />
              </div>
              <div className="grid gap-4 md:grid-cols-3">
                <SettingMetric label="Globally disabled" value={String(arrayOfStrings(toolsConfig.disabled).length)} />
                <SettingMetric label="Disabled categories" value={String(arrayOfStrings(toolsConfig.disabled_categories).length)} />
                <SettingMetric label="MCP servers" value={String(Array.isArray(toolsConfig.mcp_servers) ? toolsConfig.mcp_servers.length : 0)} />
              </div>
              {arrayOfStrings(toolsConfig.disabled).length ? <div className="rounded-lg border border-[var(--outline)] bg-white p-5"><h2 className="text-sm font-semibold">Disabled tools</h2><div className="mt-3 flex flex-wrap gap-2">{arrayOfStrings(toolsConfig.disabled).map((tool) => <Badge key={tool} variant="warning">{humanize(tool)}</Badge>)}</div></div> : null}
            </section>
          ) : null}

          {tab === "advanced" ? (
            <section className="space-y-3">
              {Object.entries(data.groups).sort(([a], [b]) => a.localeCompare(b)).map(([group, entries]) => (
                <details key={group} className="rounded-lg border border-[var(--outline)] bg-white">
                  <summary className="cursor-pointer px-5 py-4 text-sm font-semibold capitalize">{group} <span className="ml-2 font-normal text-[var(--ink-soft)]">{Object.keys(entries).length}</span></summary>
                  <div className="border-t border-[var(--outline)]">
                    {Object.entries(entries).map(([key, value]) => (
                      <div key={key} className="grid gap-1 border-b border-[var(--outline)] px-5 py-3 text-xs last:border-0 md:grid-cols-[260px_minmax(0,1fr)]"><span className="font-medium">{key}</span><pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words text-[var(--ink-soft)]">{formatValue(value)}</pre></div>
                    ))}
                  </div>
                </details>
              ))}
            </section>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function SettingMetric({ label, value }: { label: string; value: string }) {
  return <div className="rounded-lg border border-[var(--outline)] bg-white p-4"><p className="text-xs text-[var(--ink-soft)]">{label}</p><p className="mt-1 text-xl font-semibold">{value}</p></div>;
}

function PermissionPanel({ name, value }: { name: string; value: Record<string, unknown> }) {
  const disabled = arrayOfStrings(value.disabled);
  return (
    <div className="rounded-lg border border-[var(--outline)] bg-white p-5">
      <div className="flex items-center justify-between"><span className="flex items-center gap-2 text-sm font-semibold"><Shield size={17} /> {name}</span><Badge variant={value.allow_all === true ? "success" : "muted"}>{value.allow_all === true ? "Broad access" : "Restricted"}</Badge></div>
      <dl className="mt-4 space-y-3 text-sm"><SettingRow label="Shell" enabled={value.allow_shell === true} /><SettingRow label="File writing" enabled={value.allow_file_write === true} />{typeof value.max_energy_per_tool === "number" ? <div className="flex justify-between"><dt className="text-[var(--ink-soft)]">Energy per tool</dt><dd>{value.max_energy_per_tool}</dd></div> : null}</dl>
      {disabled.length ? <div className="mt-4 flex flex-wrap gap-2">{disabled.map((tool) => <Badge key={tool} variant="warning">{humanize(tool)}</Badge>)}</div> : null}
    </div>
  );
}

function SettingRow({ label, enabled }: { label: string; enabled: boolean }) {
  return <div className="flex justify-between"><dt className="text-[var(--ink-soft)]">{label}</dt><dd className={enabled ? "text-emerald-700" : "text-[var(--ink-soft)]"}>{enabled ? "Allowed" : "Blocked"}</dd></div>;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function arrayOfStrings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function asString(value: unknown, fallback: string): string {
  return typeof value === "string" && value ? value : fallback;
}

function numberValue(value: unknown, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function humanize(value: string): string {
  return value.replace(/_/g, " ");
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "null";
  return typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
}
