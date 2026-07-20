"use client";

import {
  CheckCircle2,
  Clock3,
  ExternalLink,
  Mail,
  MessageCircle,
  Plug,
  RadioTower,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import { PageHeader } from "../components/ui/page-header";
import { Spinner } from "../components/ui/spinner";
import {
  BackfillJob,
  ConnectorSummary,
  IntegrationStatusData,
  asRecord,
  capabilityKeys,
  stateLabel,
  stringArray,
  summarizeConnectors,
} from "./status";

export default function ConnectionsPage() {
  const [data, setData] = useState<IntegrationStatusData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [actionBusy, setActionBusy] = useState<string | null>(null);

  const fetchStatus = useCallback(async () => {
    try {
      const response = await fetch("/api/integrations/status", { cache: "no-store" });
      if (!response.ok) throw new Error(`Failed to load connections (${response.status})`);
      setData((await response.json()) as IntegrationStatusData);
      setError(null);
    } catch (requestError: unknown) {
      setError(
        requestError instanceof Error ? requestError.message : "Failed to load connections."
      );
    } finally {
      setLoading(false);
    }
  }, []);

  const startSetup = useCallback(
    async (connectorId: string) => {
      if (actionBusy) return;
      setActionBusy(connectorId);
      setNotice(null);
      setError(null);
      try {
        const response = await fetch("/api/integrations/status", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            action: "start_setup",
            connector_id: connectorId,
            source_session_id: "web-connections",
          }),
        });
        const payload = await response.json();
        if (!response.ok) {
          throw new Error(payload?.error || `Setup failed (${response.status})`);
        }
        setNotice(payload?.next_step || "Setup started.");
        await fetchStatus();
      } catch (requestError: unknown) {
        setError(requestError instanceof Error ? requestError.message : "Setup failed.");
      } finally {
        setActionBusy(null);
      }
    },
    [actionBusy, fetchStatus]
  );

  useEffect(() => {
    fetchStatus();
    const timer = window.setInterval(fetchStatus, 15000);
    return () => window.clearInterval(timer);
  }, [fetchStatus]);

  const summaries = useMemo(() => (data ? summarizeConnectors(data) : []), [data]);
  const connectedCount = summaries.filter((item) => item.activeConnections.length > 0).length;
  const pendingCount = summaries.filter((item) => item.activeAttempts.length > 0).length;
  const runningCount = summaries.filter((item) => item.runtime?.running).length;
  const sourceItemCount = summaries.reduce((total, item) => total + item.sourceItemCount, 0);

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Spinner label="Loading connections..." />
      </div>
    );
  }

  return (
    <div className="space-y-6 p-6">
      <div className="border-b border-[var(--outline)] pb-5">
        <PageHeader
          title="Connections"
          subtitle="Connector setup, channel runtime, and backfill status"
        />
      </div>

      {error ? (
        <div className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </div>
      ) : null}
      {notice ? (
        <div className="rounded-md border border-[var(--teal)]/40 bg-[var(--teal)]/5 px-3 py-2 text-sm text-[var(--foreground)]">
          {notice}
        </div>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Metric label="Connected" value={String(connectedCount)} icon={CheckCircle2} />
        <Metric label="Pending setup" value={String(pendingCount)} icon={Clock3} />
        <Metric label="Running channels" value={String(runningCount)} icon={RadioTower} />
        <Metric label="Source items" value={String(sourceItemCount)} icon={Mail} />
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        {summaries.map((summary) => (
          <ConnectorCard
            key={summary.connector.id}
            summary={summary}
            busy={actionBusy === summary.connector.id}
            onStartSetup={startSetup}
          />
        ))}
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.25fr,0.75fr]">
        <BackfillPanel jobs={data?.backfill.jobs || []} />
        <SourceItemsPanel summaries={summaries} />
      </div>
    </div>
  );
}

function ConnectorCard({
  summary,
  busy,
  onStartSetup,
}: {
  summary: ConnectorSummary;
  busy: boolean;
  onStartSetup: (connectorId: string) => void;
}) {
  const { connector, runtime } = summary;
  const setupManifest = asRecord(connector.setup_manifest);
  const defaultCapabilities = stringArray(setupManifest.default_capabilities);
  const nextStep = asString(setupManifest.user_next_step);
  const capabilities = capabilityKeys(connector.capability_manifest);
  const canStartSetup =
    ["slack", "telegram", "signal"].includes(connector.id) &&
    summary.activeConnections.length === 0 &&
    summary.activeAttempts.length === 0 &&
    connector.status === "available";

  return (
    <Card className="space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <ConnectorIcon id={connector.id} />
            <h2 className="truncate text-sm font-semibold">{connector.display_name}</h2>
          </div>
          <p className="mt-1 text-xs text-[var(--ink-soft)]">
            {connector.category} · {connector.auth_type}
          </p>
        </div>
        <Badge variant={stateVariant(summary.state)} className="capitalize">
          {stateLabel(summary.state)}
        </Badge>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {(defaultCapabilities.length ? defaultCapabilities : capabilities).map((capability) => (
          <Badge key={capability} variant="muted">
            {humanize(capability)}
          </Badge>
        ))}
      </div>

      <div className="space-y-2 text-sm">
        {summary.activeConnections.length ? (
          summary.activeConnections.map((connection) => (
            <div
              key={connection.id}
              className="rounded-md border border-[var(--outline)] px-3 py-2"
            >
              <div className="flex items-center justify-between gap-3">
                <span className="min-w-0 truncate font-medium">
                  {connection.display_name || connection.account_key}
                </span>
                <Badge variant="success">{connection.status}</Badge>
              </div>
              <p className="mt-1 truncate text-xs text-[var(--ink-soft)]">
                {connection.capabilities.map(humanize).join(", ") || "Connected"}
              </p>
            </div>
          ))
        ) : (
          <p className="text-sm text-[var(--ink-soft)]">No account connected.</p>
        )}

        {summary.activeAttempts.map((attempt) => (
          <div
            key={attempt.attempt_id}
            className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2"
          >
            <div className="flex items-center justify-between gap-3">
              <span className="min-w-0 truncate font-medium">
                {attempt.requested_capabilities.map(humanize).join(", ") || "Setup"}
              </span>
              <Badge variant="warning">{attempt.status}</Badge>
            </div>
            {attempt.user_next_step ? (
              <p className="mt-1 text-xs text-amber-800">{attempt.user_next_step}</p>
            ) : null}
            {attempt.authorization_url ? (
              <a
                href={attempt.authorization_url}
                target="_blank"
                rel="noreferrer"
                className="mt-2 inline-flex items-center gap-1 text-xs font-semibold text-[var(--teal)] underline"
              >
                Open authorization <ExternalLink size={12} />
              </a>
            ) : null}
          </div>
        ))}
      </div>

      {runtime ? (
        <div className="rounded-md border border-[var(--outline)] px-3 py-2 text-xs">
          <div className="flex items-center justify-between gap-3">
            <span className="text-[var(--ink-soft)]">Runtime</span>
            <Badge variant={runtime.running ? "success" : runtime.status === "error" ? "error" : "muted"}>
              {runtime.status}
            </Badge>
          </div>
          {runtime.last_error ? (
            <p className="mt-1 text-red-700">{runtime.last_error}</p>
          ) : (
            <p className="mt-1 text-[var(--ink-soft)]">
              {runtime.configured ? "Configured" : "Not configured"}
              {runtime.updated_at ? ` · ${formatDate(runtime.updated_at)}` : ""}
            </p>
          )}
        </div>
      ) : null}

      {summary.activeConnections.length === 0 && summary.activeAttempts.length === 0 && nextStep ? (
        <p className="text-xs text-[var(--ink-soft)]">{nextStep}</p>
      ) : null}

      {connector.docs_url ? (
        <div className="flex flex-wrap items-center gap-3">
          {canStartSetup ? (
            <Button
              type="button"
              variant="secondary"
              disabled={busy}
              onClick={() => onStartSetup(connector.id)}
              className="px-3 py-1.5 text-xs"
            >
              {busy ? "Starting..." : "Start setup"}
            </Button>
          ) : null}
          <a
            href={connector.docs_url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-xs font-semibold text-[var(--teal)] underline"
          >
            Provider docs <ExternalLink size={12} />
          </a>
        </div>
      ) : canStartSetup ? (
        <Button
          type="button"
          variant="secondary"
          disabled={busy}
          onClick={() => onStartSetup(connector.id)}
          className="px-3 py-1.5 text-xs"
        >
          {busy ? "Starting..." : "Start setup"}
        </Button>
      ) : null}
    </Card>
  );
}

function BackfillPanel({ jobs }: { jobs: BackfillJob[] }) {
  return (
    <Card className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">Backfill jobs</h2>
        <Badge variant="muted">{jobs.length}</Badge>
      </div>
      {jobs.length === 0 ? (
        <p className="text-sm text-[var(--ink-soft)]">No recent backfill jobs.</p>
      ) : (
        <div className="space-y-2">
          {jobs.slice(0, 8).map((job) => (
            <div
              key={job.job_id}
              className="grid gap-2 rounded-md border border-[var(--outline)] px-3 py-2 text-sm md:grid-cols-[1fr_auto]"
            >
              <div className="min-w-0">
                <p className="truncate font-medium">
                  {job.connector_id} · {job.account_key}
                </p>
                <p className="truncate text-xs text-[var(--ink-soft)]">
                  {job.cursor_key}
                  {job.updated_at ? ` · ${formatDate(job.updated_at)}` : ""}
                </p>
                {job.error ? <p className="mt-1 text-xs text-red-700">{job.error}</p> : null}
              </div>
              <div className="flex items-center gap-2 md:justify-end">
                {job.pause_requested ? <Badge variant="warning">pause requested</Badge> : null}
                {job.cancel_requested ? <Badge variant="error">cancel requested</Badge> : null}
                <Badge variant={jobVariant(job.status)}>{job.status}</Badge>
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

function SourceItemsPanel({ summaries }: { summaries: ConnectorSummary[] }) {
  const rows = summaries.filter((summary) => summary.sourceItemCount > 0);
  return (
    <Card className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">Preserved sources</h2>
        <Badge variant="muted">{rows.length}</Badge>
      </div>
      {rows.length === 0 ? (
        <p className="text-sm text-[var(--ink-soft)]">No connector source items yet.</p>
      ) : (
        <div className="space-y-2">
          {rows.map((summary) => (
            <div
              key={summary.connector.id}
              className="flex items-center justify-between gap-3 rounded-md border border-[var(--outline)] px-3 py-2 text-sm"
            >
              <span className="min-w-0 truncate">{summary.connector.display_name}</span>
              <span className="font-semibold">{summary.sourceItemCount}</span>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

function Metric({
  label,
  value,
  icon: Icon,
}: {
  label: string;
  value: string;
  icon: typeof CheckCircle2;
}) {
  return (
    <Card className="flex items-center justify-between gap-3">
      <div>
        <p className="text-xs text-[var(--ink-soft)]">{label}</p>
        <p className="mt-1 text-2xl font-semibold">{value}</p>
      </div>
      <Icon size={20} className="text-[var(--teal)]" />
    </Card>
  );
}

function ConnectorIcon({ id }: { id: string }) {
  if (id === "gmail") return <Mail size={16} className="text-[var(--teal)]" />;
  if (["slack", "telegram", "signal"].includes(id)) {
    return <MessageCircle size={16} className="text-[var(--teal)]" />;
  }
  return <Plug size={16} className="text-[var(--teal)]" />;
}

function stateVariant(state: ConnectorSummary["state"]) {
  if (state === "connected" || state === "running") return "success";
  if (state === "pending" || state === "planned") return "warning";
  if (state === "error") return "error";
  return "muted";
}

function jobVariant(status: string) {
  if (status === "completed") return "success";
  if (status === "failed" || status === "cancelled") return "error";
  if (status === "paused") return "warning";
  return "muted";
}

function asString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function humanize(value: string): string {
  return value.replace(/_/g, " ");
}

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}
