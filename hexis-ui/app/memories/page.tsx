"use client";

import { Brain, ChevronLeft, ChevronRight, Search, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Badge, MemoryTypeBadge } from "../components/ui/badge";
import { PageHeader } from "../components/ui/page-header";
import { Spinner } from "../components/ui/spinner";

type Memory = {
  id: string;
  type: string;
  content: string;
  importance: number | null;
  trust_level: number | null;
  strength: number | null;
  emotional_valence: number | null;
  score: number | null;
  access_count: number | null;
  created_at: string | null;
  last_accessed: string | null;
  status?: string;
  source?: string;
  metadata: unknown;
};

type HealthEntry = {
  type: string;
  count: number;
  avg_importance: number | null;
};

const SORT_OPTIONS = [
  { value: "recent", label: "Most recent" },
  { value: "importance", label: "Highest importance" },
  { value: "oldest", label: "Oldest" },
];

export default function MemoriesPage() {
  const [memories, setMemories] = useState<Memory[]>([]);
  const [health, setHealth] = useState<HealthEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [sort, setSort] = useState("recent");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Memory | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailData, setDetailData] = useState<Memory | null>(null);
  const limit = 20;

  const fetchMemories = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ sort, limit: String(limit), offset: String(offset) });
      if (query) params.set("q", query);
      if (typeFilter) params.set("type", typeFilter);
      const response = await fetch(`/api/memories?${params}`, { cache: "no-store" });
      if (!response.ok) throw new Error(`Failed to load memories (${response.status})`);
      const data = await response.json();
      setMemories(data.memories || []);
      setHealth(data.health || []);
      setTotal(data.total || 0);
      setError(null);
    } catch (requestError: unknown) {
      setError(requestError instanceof Error ? requestError.message : "Failed to load memories.");
    } finally {
      setLoading(false);
    }
  }, [query, typeFilter, sort, offset]);

  useEffect(() => {
    fetchMemories();
  }, [fetchMemories]);

  const runSearch = () => {
    setOffset(0);
    setQuery(searchInput.trim());
  };

  const openDetail = async (memory: Memory) => {
    setSelected(memory);
    setDetailData(null);
    setDetailLoading(true);
    try {
      const response = await fetch(`/api/memories/${memory.id}`, { cache: "no-store" });
      if (response.ok) setDetailData(await response.json());
    } finally {
      setDetailLoading(false);
    }
  };

  const detail = detailData || selected;

  return (
    <div className="app-shell">
      <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
        <PageHeader title="Memory" subtitle={`${total.toLocaleString()} active memories`} />

        <div className="mt-6 rounded-lg border border-[var(--outline)] bg-white">
          <div className="flex flex-col gap-3 border-b border-[var(--outline)] p-3 sm:flex-row">
            <div className="flex min-w-0 flex-1 items-center rounded-md border border-[var(--outline)] px-3 focus-within:border-[var(--teal)] focus-within:ring-2 focus-within:ring-[var(--teal)]/10">
              <Search size={17} className="flex-none text-[var(--ink-soft)]" />
              <input
                type="search"
                value={searchInput}
                onChange={(event) => setSearchInput(event.target.value)}
                onKeyDown={(event) => { if (event.key === "Enter") runSearch(); }}
                placeholder="Search memories"
                className="min-w-0 flex-1 border-0 bg-transparent px-3 py-2.5 text-sm outline-none"
              />
              {searchInput ? <button type="button" title="Clear search" aria-label="Clear search" onClick={() => { setSearchInput(""); setQuery(""); setOffset(0); }}><X size={15} /></button> : null}
            </div>
            <button type="button" onClick={runSearch} className="rounded-md bg-[var(--foreground)] px-4 py-2.5 text-sm font-semibold text-white hover:bg-[var(--teal)]">Search</button>
            <select value={sort} onChange={(event) => { setSort(event.target.value); setOffset(0); }} className="rounded-md border border-[var(--outline)] bg-white px-3 py-2.5 text-sm">
              {SORT_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
          </div>

          <div className="flex gap-1 overflow-x-auto border-b border-[var(--outline)] p-2">
            <button type="button" onClick={() => { setTypeFilter(""); setOffset(0); }} className={`flex-none rounded-md px-3 py-2 text-xs font-medium ${!typeFilter ? "bg-[var(--foreground)] text-white" : "text-[var(--ink-soft)] hover:bg-[var(--surface-strong)]"}`}>All <span className="ml-1 opacity-70">{total}</span></button>
            {health.map((entry) => (
              <button key={entry.type} type="button" onClick={() => { setTypeFilter(typeFilter === entry.type ? "" : entry.type); setOffset(0); }} className={`flex-none rounded-md px-3 py-2 text-xs font-medium capitalize ${typeFilter === entry.type ? "bg-[var(--foreground)] text-white" : "text-[var(--ink-soft)] hover:bg-[var(--surface-strong)]"}`}>{entry.type} <span className="ml-1 opacity-70">{entry.count}</span></button>
            ))}
          </div>

          <div className="grid lg:grid-cols-[minmax(0,1fr)_380px]">
            <section className="min-w-0 lg:max-h-[calc(100vh-250px)] lg:overflow-y-auto">
              {loading ? <div className="flex justify-center py-16"><Spinner label="Loading memories..." /></div> : error ? (
                <div className="p-6"><p className="text-sm text-red-700">{error}</p><button onClick={fetchMemories} className="mt-3 rounded-md border border-red-200 px-3 py-2 text-sm text-red-700">Retry</button></div>
              ) : memories.length === 0 ? (
                <div className="flex flex-col items-center py-16 text-center"><Brain size={28} className="text-[var(--ink-soft)]" /><p className="mt-3 text-sm text-[var(--ink-soft)]">No matching memories.</p></div>
              ) : memories.map((memory) => (
                <button
                  key={memory.id}
                  type="button"
                  onClick={() => openDetail(memory)}
                  className={`block w-full border-b border-[var(--outline)] px-4 py-4 text-left transition last:border-0 sm:px-5 ${selected?.id === memory.id ? "bg-[#edf4f1]" : "hover:bg-[#f8faf8]"}`}
                >
                  <div className="flex items-start gap-3">
                    <MemoryTypeBadge type={memory.type} />
                    <div className="min-w-0 flex-1">
                      <p className="line-clamp-3 text-sm leading-6">{memory.content}</p>
                      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-[var(--ink-soft)]">
                        <Strength value={memory.strength} />
                        {memory.emotional_valence != null ? <Valence value={memory.emotional_valence} /> : null}
                        {memory.created_at ? <span>{new Date(memory.created_at).toLocaleDateString()}</span> : null}
                        {memory.score != null ? <span>match {(memory.score * 100).toFixed(0)}%</span> : null}
                      </div>
                    </div>
                  </div>
                </button>
              ))}
              {!loading && memories.length > 0 ? (
                <div className="flex items-center justify-between px-4 py-3">
                  <button type="button" title="Previous page" aria-label="Previous page" onClick={() => setOffset(Math.max(0, offset - limit))} disabled={offset === 0} className="flex h-9 w-9 items-center justify-center rounded-md border border-[var(--outline)] disabled:opacity-30"><ChevronLeft size={17} /></button>
                  <span className="text-xs text-[var(--ink-soft)]">{offset + 1}-{offset + memories.length} of {total}</span>
                  <button type="button" title="Next page" aria-label="Next page" onClick={() => setOffset(offset + limit)} disabled={offset + memories.length >= total} className="flex h-9 w-9 items-center justify-center rounded-md border border-[var(--outline)] disabled:opacity-30"><ChevronRight size={17} /></button>
                </div>
              ) : null}
            </section>

            <aside className={`${selected ? "fixed inset-0 z-40 overflow-y-auto bg-white p-5 lg:static lg:z-auto" : "hidden lg:block"} border-l border-[var(--outline)] lg:max-h-[calc(100vh-250px)] lg:overflow-y-auto`}>
              {detailLoading ? <Spinner label="Loading memory..." /> : detail ? (
                <div>
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex flex-wrap gap-2"><MemoryTypeBadge type={detail.type} />{detail.status && detail.status !== "active" ? <Badge variant="warning">{detail.status}</Badge> : null}</div>
                    <button type="button" title="Close details" aria-label="Close details" onClick={() => { setSelected(null); setDetailData(null); }} className="flex h-8 w-8 items-center justify-center rounded-md hover:bg-[var(--surface-strong)]"><X size={17} /></button>
                  </div>
                  <p className="mt-5 whitespace-pre-wrap text-sm leading-6">{detail.content}</p>
                  <div className="mt-6 grid grid-cols-2 gap-4 border-y border-[var(--outline)] py-4">
                    <DetailMetric label="Strength" value={percent(detail.strength)} />
                    <DetailMetric label="Importance" value={percent(detail.importance)} />
                    <DetailMetric label="Trust" value={percent(detail.trust_level)} />
                    <DetailMetric label="Valence" value={detail.emotional_valence != null ? signed(detail.emotional_valence) : "--"} />
                  </div>
                  <dl className="mt-5 space-y-3 text-xs">
                    {detail.access_count != null ? <Row label="Accessed" value={`${detail.access_count} times`} /> : null}
                    {detail.source ? <Row label="Source" value={detail.source} /> : null}
                    {detail.created_at ? <Row label="Created" value={new Date(detail.created_at).toLocaleString()} /> : null}
                    {detail.last_accessed ? <Row label="Last accessed" value={new Date(detail.last_accessed).toLocaleString()} /> : null}
                  </dl>
                  {detail.metadata && Object.keys(asRecord(detail.metadata)).length ? (
                    <details className="mt-6"><summary className="cursor-pointer text-xs font-medium text-[var(--ink-soft)]">Metadata</summary><pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-md bg-[var(--surface-strong)] p-3 text-xs">{JSON.stringify(detail.metadata, null, 2)}</pre></details>
                  ) : null}
                </div>
              ) : <div className="flex h-60 items-center justify-center px-8 text-center text-sm text-[var(--ink-soft)]">Select a memory to inspect its strength and emotional context.</div>}
            </aside>
          </div>
        </div>
      </div>
    </div>
  );
}

function Strength({ value }: { value: number | null }) {
  const width = Math.max(0, Math.min(100, (value || 0) * 100));
  return <span className="flex items-center gap-2"><span>strength</span><span className="h-1.5 w-16 rounded-full bg-[var(--surface-strong)]"><span className="block h-1.5 rounded-full bg-[var(--teal)]" style={{ width: `${width}%` }} /></span><span>{width.toFixed(0)}%</span></span>;
}

function Valence({ value }: { value: number }) {
  return <span className={value >= 0 ? "text-emerald-700" : "text-rose-700"}>valence {signed(value)}</span>;
}

function DetailMetric({ label, value }: { label: string; value: string }) {
  return <div><dt className="text-xs text-[var(--ink-soft)]">{label}</dt><dd className="mt-1 text-sm font-semibold">{value}</dd></div>;
}

function Row({ label, value }: { label: string; value: string }) {
  return <div className="flex justify-between gap-4"><dt className="text-[var(--ink-soft)]">{label}</dt><dd className="text-right">{value}</dd></div>;
}

function percent(value: number | null): string {
  return value == null ? "--" : `${Math.max(0, value * 100).toFixed(0)}%`;
}

function signed(value: number): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}`;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}
