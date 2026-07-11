export type MessagePresentationTone =
  | "neutral"
  | "info"
  | "success"
  | "warning"
  | "danger";

export type MessagePresentationBlock =
  | { type: "text"; text: string }
  | { type: "context"; text: string }
  | { type: "divider" };

export type MessagePresentation = {
  title?: string;
  tone: MessagePresentationTone;
  blocks: MessagePresentationBlock[];
};

const tones = new Set<MessagePresentationTone>([
  "neutral",
  "info",
  "success",
  "warning",
  "danger",
]);

export function normalizeMessagePresentation(
  value: unknown
): MessagePresentation | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  const record = value as Record<string, unknown>;
  if (!Array.isArray(record.blocks)) return undefined;

  const blocks: MessagePresentationBlock[] = [];
  for (const valueBlock of record.blocks) {
    if (!valueBlock || typeof valueBlock !== "object" || Array.isArray(valueBlock)) {
      return undefined;
    }
    const block = valueBlock as Record<string, unknown>;
    if (block.type === "divider") {
      blocks.push({ type: "divider" });
      continue;
    }
    if (
      (block.type === "text" || block.type === "context") &&
      typeof block.text === "string" &&
      block.text.trim()
    ) {
      blocks.push({ type: block.type, text: block.text });
      continue;
    }
    return undefined;
  }

  const title = typeof record.title === "string" && record.title.trim()
    ? record.title
    : undefined;
  if (!title && blocks.length === 0) return undefined;
  const tone = typeof record.tone === "string" && tones.has(record.tone as MessagePresentationTone)
    ? (record.tone as MessagePresentationTone)
    : "neutral";
  return { title, tone, blocks };
}
