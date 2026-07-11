import type { MessagePresentation } from "../../lib/message-presentation";

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function renderMarkdown(text: string) {
  if (!text) return null;

  const parts: React.ReactNode[] = [];
  const lines = text.split("\n");

  for (let index = 0; index < lines.length; index++) {
    const line = lines[index];
    if (line.startsWith("```")) {
      const codeLines: string[] = [];
      let closingIndex = index + 1;
      while (
        closingIndex < lines.length &&
        !lines[closingIndex].startsWith("```")
      ) {
        codeLines.push(lines[closingIndex]);
        closingIndex++;
      }
      parts.push(
        <pre
          key={`code-${index}`}
          className="my-2 overflow-x-auto rounded-md bg-[var(--surface-strong)] p-3 text-xs"
        >
          <code>{codeLines.join("\n")}</code>
        </pre>
      );
      index = closingIndex;
      continue;
    }

    const formatted = escapeHtml(line)
      .replace(
        /`([^`]+)`/g,
        '<code class="rounded bg-[var(--surface-strong)] px-1.5 py-0.5 text-xs">$1</code>'
      )
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/\*([^*]+)\*/g, "<em>$1</em>");

    parts.push(
      <span key={`line-${index}`}>
        <span dangerouslySetInnerHTML={{ __html: formatted }} />
        {index < lines.length - 1 && <br />}
      </span>
    );
  }

  return <>{parts}</>;
}

export function MessagePresentationView({
  presentation,
}: {
  presentation: MessagePresentation;
}) {
  return (
    <div className="space-y-3" data-presentation-tone={presentation.tone}>
      {presentation.title ? (
        <div className="font-semibold">{presentation.title}</div>
      ) : null}
      {presentation.blocks.map((block, index) => {
        if (block.type === "divider") {
          return <hr key={`divider-${index}`} className="border-[var(--outline)]" />;
        }
        if (block.type === "context") {
          return (
            <div key={`context-${index}`} className="text-xs text-[var(--ink-soft)]">
              {renderMarkdown(block.text)}
            </div>
          );
        }
        return <div key={`text-${index}`}>{renderMarkdown(block.text)}</div>;
      })}
    </div>
  );
}
