import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MessagePresentationView } from "./message-presentation";

describe("MessagePresentationView", () => {
  it("renders typed text, context, divider, and tone", () => {
    const { container } = render(
      <MessagePresentationView
        presentation={{
          title: "Deployment",
          tone: "success",
          blocks: [
            { type: "text", text: "**Ready** for review." },
            { type: "divider" },
            { type: "context", text: "Derived from live evidence." },
          ],
        }}
      />
    );

    expect(screen.getByText("Deployment")).toBeInTheDocument();
    expect(screen.getByText("Ready").tagName).toBe("STRONG");
    expect(screen.getByText("Derived from live evidence.")).toBeInTheDocument();
    expect(container.querySelector("hr")).toBeInTheDocument();
    expect(container.firstChild).toHaveAttribute("data-presentation-tone", "success");
  });

  it("escapes model-provided HTML before applying inline formatting", () => {
    const { container } = render(
      <MessagePresentationView
        presentation={{
          tone: "neutral",
          blocks: [{ type: "text", text: '<img src=x onerror="alert(1)">' }],
        }}
      />
    );

    expect(container.querySelector("img")).not.toBeInTheDocument();
    expect(screen.getByText('<img src=x onerror="alert(1)">')).toBeInTheDocument();
  });
});
