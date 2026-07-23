"use client";

import { Maximize2, MousePointer2, Network, RefreshCw } from "lucide-react";
import { useMemo } from "react";
import { Badge } from "../components/ui/badge";
import { Spinner } from "../components/ui/spinner";

export type MemoryGraphNode = {
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
  status?: string | null;
  source?: string | null;
  metadata: unknown;
  x: number;
  y: number;
  semantic_neighbors: string[];
};

export type MemoryGraphEdge = {
  id: string;
  source: string;
  target: string;
  rel_type: string;
  weight: number;
  kind: string | null;
  source_label: string | null;
  properties: unknown;
  created_at: string | null;
  updated_at: string | null;
};

export type MemoryGraphData = {
  nodes: MemoryGraphNode[];
  edges: MemoryGraphEdge[];
  projection: {
    method: string;
    source: string;
    dimensions: number;
    limit: number;
    neighbor_count: number;
  };
};

type MemoryGraphProps = {
  data: MemoryGraphData | null;
  loading: boolean;
  error: string | null;
  selectedId: string | null;
  focusId: string | null;
  onSelect: (node: MemoryGraphNode) => void;
  onFocus: (id: string) => void;
  onResetFocus: () => void;
  onRefresh: () => void;
};

const TYPE_COLORS: Record<string, string> = {
  episodic: "#d65d3b",
  semantic: "#176c63",
  procedural: "#6b7280",
  strategic: "#2563eb",
  worldview: "#a84026",
  goal: "#7c3aed",
};

export function MemoryGraph({
  data,
  loading,
  error,
  selectedId,
  focusId,
  onSelect,
  onFocus,
  onResetFocus,
  onRefresh,
}: MemoryGraphProps) {
  const nodes = useMemo(() => data?.nodes ?? [], [data]);
  const edges = useMemo(() => data?.edges ?? [], [data]);

  const nodeById = useMemo(() => new Map(nodes.map((node) => [node.id, node])), [nodes]);
  const edgeDegree = useMemo(() => {
    const counts = new Map<string, number>();
    for (const edge of edges) {
      counts.set(edge.source, (counts.get(edge.source) || 0) + 1);
      counts.set(edge.target, (counts.get(edge.target) || 0) + 1);
    }
    return counts;
  }, [edges]);

  const focusSet = useMemo(() => {
    if (!focusId) return null;
    const node = nodeById.get(focusId);
    if (!node) return null;
    return new Set([node.id, ...node.semantic_neighbors]);
  }, [focusId, nodeById]);

  const visibleNodes = focusSet
    ? nodes.filter((node) => focusSet.has(node.id))
    : nodes;
  const visibleNodeIds = useMemo(
    () => new Set(visibleNodes.map((node) => node.id)),
    [visibleNodes]
  );
  const visibleEdges = edges.filter(
    (edge) => visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target)
  );
  const viewBox = fitViewBox(visibleNodes);
  const labelNodes = focusSet || visibleNodes.length <= 70;
  const typeCounts = groupByType(nodes);

  return (
    <section className="min-w-0">
      <div className="flex flex-col gap-3 border-b border-[var(--outline)] px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <Network size={17} className="text-[var(--teal)]" />
            <h2 className="text-sm font-semibold">Memory Map</h2>
            {data ? <Badge variant="muted">{data.projection.method.toUpperCase()}</Badge> : null}
          </div>
          <p className="mt-1 text-xs text-[var(--ink-soft)]">
            {nodes.length.toLocaleString()} embedded memories · {edges.length.toLocaleString()} edges
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            title="Reset focus"
            aria-label="Reset focus"
            onClick={onResetFocus}
            disabled={!focusId}
            className="flex h-9 w-9 items-center justify-center rounded-md border border-[var(--outline)] disabled:opacity-30"
          >
            <Maximize2 size={16} />
          </button>
          <button
            type="button"
            title="Refresh map"
            aria-label="Refresh map"
            onClick={onRefresh}
            className="flex h-9 w-9 items-center justify-center rounded-md border border-[var(--outline)] hover:bg-[var(--surface-strong)]"
          >
            <RefreshCw size={16} />
          </button>
        </div>
      </div>

      <div className="border-b border-[var(--outline)] px-4 py-2">
        <div className="flex flex-wrap gap-2">
          {typeCounts.map(([type, count]) => (
            <Badge key={type} variant="muted" className="capitalize">
              {type} {count}
            </Badge>
          ))}
        </div>
      </div>

      <div className="relative min-h-[560px] overflow-hidden bg-[#fbfcfb]">
        {loading ? (
          <div className="absolute inset-0 flex items-center justify-center">
            <Spinner label="Loading memory map..." />
          </div>
        ) : error ? (
          <div className="flex min-h-[560px] flex-col items-center justify-center px-6 text-center">
            <p className="text-sm text-red-700">{error}</p>
            <button
              type="button"
              onClick={onRefresh}
              className="mt-3 rounded-md border border-red-200 px-3 py-2 text-sm text-red-700"
            >
              Retry
            </button>
          </div>
        ) : visibleNodes.length === 0 ? (
          <div className="flex min-h-[560px] flex-col items-center justify-center px-6 text-center">
            <MousePointer2 size={24} className="text-[var(--ink-soft)]" />
            <p className="mt-3 text-sm text-[var(--ink-soft)]">No embedded memories to map.</p>
          </div>
        ) : (
          <svg
            role="img"
            aria-label="Projected memory graph"
            viewBox={viewBox}
            preserveAspectRatio="xMidYMid meet"
            className="h-[560px] w-full"
          >
            <rect x="-2000" y="-2000" width="4000" height="4000" fill="#fbfcfb" />
            <g>
              {visibleEdges.map((edge) => {
                const source = nodeById.get(edge.source);
                const target = nodeById.get(edge.target);
                if (!source || !target) return null;
                return (
                  <line
                    key={edge.id}
                    x1={source.x}
                    y1={source.y}
                    x2={target.x}
                    y2={target.y}
                    stroke="#9aa6a0"
                    strokeWidth={edgeWidth(edge.weight)}
                    strokeOpacity={focusSet ? 0.58 : 0.34}
                    vectorEffect="non-scaling-stroke"
                  >
                    <title>{edge.rel_type}</title>
                  </line>
                );
              })}
            </g>
            <g>
              {visibleNodes.map((node) => {
                const selected = selectedId === node.id;
                const radius = nodeRadius(node, edgeDegree.get(node.id) || 0);
                return (
                  <g
                    key={node.id}
                    role="button"
                    tabIndex={0}
                    aria-label={`Select ${node.type} memory`}
                    onClick={() => onSelect(node)}
                    onDoubleClick={() => onFocus(node.id)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        onSelect(node);
                      }
                    }}
                    className="cursor-pointer outline-none"
                  >
                    <circle
                      cx={node.x}
                      cy={node.y}
                      r={selected ? radius + 5 : radius + 2}
                      fill={selected ? "#18211e" : "#ffffff"}
                      fillOpacity={selected ? 0.13 : 0.9}
                      stroke={selected ? "#18211e" : "transparent"}
                      strokeWidth={selected ? 1.6 : 0}
                      vectorEffect="non-scaling-stroke"
                    />
                    <circle
                      cx={node.x}
                      cy={node.y}
                      r={radius}
                      fill={TYPE_COLORS[node.type] || "#5d6863"}
                      stroke="#ffffff"
                      strokeWidth={1.5}
                      vectorEffect="non-scaling-stroke"
                    >
                      <title>{node.content}</title>
                    </circle>
                    {labelNodes ? (
                      <text
                        x={node.x + radius + 6}
                        y={node.y + 4}
                        className="select-none fill-[var(--foreground)] text-[11px]"
                      >
                        {shortLabel(node.content)}
                      </text>
                    ) : null}
                  </g>
                );
              })}
            </g>
          </svg>
        )}
      </div>
    </section>
  );
}

function fitViewBox(nodes: MemoryGraphNode[]): string {
  if (nodes.length === 0) return "-520 -340 1040 680";
  const minX = Math.min(...nodes.map((node) => node.x));
  const maxX = Math.max(...nodes.map((node) => node.x));
  const minY = Math.min(...nodes.map((node) => node.y));
  const maxY = Math.max(...nodes.map((node) => node.y));
  const padding = 90;
  const width = Math.max(maxX - minX + padding * 2, 320);
  const height = Math.max(maxY - minY + padding * 2, 260);
  const centerX = (minX + maxX) / 2;
  const centerY = (minY + maxY) / 2;
  return `${centerX - width / 2} ${centerY - height / 2} ${width} ${height}`;
}

function groupByType(nodes: MemoryGraphNode[]): Array<[string, number]> {
  const counts = new Map<string, number>();
  for (const node of nodes) counts.set(node.type, (counts.get(node.type) || 0) + 1);
  return Array.from(counts.entries()).sort((left, right) => right[1] - left[1]);
}

function nodeRadius(node: MemoryGraphNode, degree: number): number {
  const importance = node.importance ?? 0.5;
  return 5 + importance * 6 + Math.min(degree, 12) * 0.35;
}

function edgeWidth(weight: number): number {
  return Math.max(1, Math.min(4, 1 + weight * 2));
}

function shortLabel(content: string): string {
  const normalized = content.replace(/\s+/g, " ").trim();
  return normalized.length > 32 ? `${normalized.slice(0, 31)}...` : normalized;
}
