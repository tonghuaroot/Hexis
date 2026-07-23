import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { isImageAttachmentFile, uploadFileName } from "./attachment-helpers";
import ChatPage from "./page";

describe("chat attachment helpers", () => {
  it("detects pasted clipboard images by mime type", () => {
    const file = new File(["pixels"], "", { type: "image/png" });

    expect(isImageAttachmentFile(file)).toBe(true);
  });

  it("adds an image extension to unnamed clipboard images", () => {
    const file = new File(["pixels"], "", { type: "image/png" });

    expect(uploadFileName(file, "pasted-image-1")).toBe("pasted-image-1.png");
  });

  it("keeps a named upload filename unchanged", () => {
    const file = new File(["pixels"], "diagram.webp", { type: "image/webp" });

    expect(uploadFileName(file, "pasted-image-1")).toBe("diagram.webp");
  });
});

describe("ChatPage attachments", () => {
  beforeEach(() => {
    vi.stubGlobal("matchMedia", vi.fn(() => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })));
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/api/status")) {
          return Response.json({
            configured: true,
            agent_name: "Samantha",
            mood: "Ready",
            valence: 0,
          });
        }
        if (url.endsWith("/api/outbox")) {
          return Response.json({ unread: 0, messages: [], pending_requests: [] });
        }
        return Response.json({});
      }) as unknown as typeof fetch
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    sessionStorage.clear();
  });

  it("turns pasted clipboard images into sendable file attachments", async () => {
    render(<ChatPage />);

    const composer = await screen.findByLabelText("Message Samantha");
    const image = new File(["pixels"], "", { type: "image/png" });
    fireEvent.paste(composer, {
      clipboardData: {
        getData: () => "",
        files: [],
        items: [
          {
            kind: "file",
            getAsFile: () => image,
          },
        ],
      },
    });

    await waitFor(() => {
      expect(screen.getByText(/pasted-image-.*\.png/)).toBeInTheDocument();
    });
  });
});
