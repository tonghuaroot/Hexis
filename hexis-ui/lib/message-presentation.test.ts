import { describe, expect, it } from "vitest";

import { normalizeMessagePresentation } from "./message-presentation";

describe("normalizeMessagePresentation", () => {
  it("preserves ordered portable blocks", () => {
    expect(
      normalizeMessagePresentation({
        title: "Deployment",
        tone: "success",
        blocks: [
          { type: "text", text: "Ready" },
          { type: "divider" },
          { type: "context", text: "Live evidence" },
        ],
      })
    ).toEqual({
      title: "Deployment",
      tone: "success",
      blocks: [
        { type: "text", text: "Ready" },
        { type: "divider" },
        { type: "context", text: "Live evidence" },
      ],
    });
  });

  it("rejects an unknown block instead of rendering partial content", () => {
    expect(
      normalizeMessagePresentation({
        blocks: [
          { type: "text", text: "Visible" },
          { type: "buttons", buttons: [] },
        ],
      })
    ).toBeUndefined();
  });

  it("uses a neutral tone when an older client receives a new tone", () => {
    expect(
      normalizeMessagePresentation({
        tone: "future-tone",
        blocks: [{ type: "text", text: "Visible" }],
      })?.tone
    ).toBe("neutral");
  });
});
