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
  const eventStream = (events: string[]) =>
    new Response(
      new ReadableStream({
        start(controller) {
          const encoder = new TextEncoder();
          for (const event of events) controller.enqueue(encoder.encode(event));
          controller.close();
        },
      }),
      { headers: { "Content-Type": "text/event-stream" } }
    );

  const eventStreamThatErrorsAfterDone = () =>
    new Response(
      new ReadableStream({
        pull(controller) {
          const encoder = new TextEncoder();
          controller.enqueue(
            encoder.encode(
              'event: done\ndata: {"assistant":"","session_id":"00000000-0000-4000-8000-000000000001"}\n\n'
            )
          );
          controller.error(new Error("network error"));
        },
      }),
      { headers: { "Content-Type": "text/event-stream" } }
    );

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
        if (url.endsWith("/api/ingest/file")) {
          return Response.json({ accepted: true });
        }
        if (url.endsWith("/api/chat")) {
          return eventStream([
            'event: done\ndata: {"assistant":"","session_id":"00000000-0000-4000-8000-000000000001"}\n\n',
          ]);
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

  it("sends pasted images as live visual attachments instead of OCR-only notes", async () => {
    const chatBodies: Record<string, unknown>[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
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
        if (url.endsWith("/api/ingest/file")) {
          return Response.json({ accepted: true });
        }
        if (url.endsWith("/api/chat")) {
          chatBodies.push(JSON.parse(String(init?.body || "{}")));
          return eventStream([
            'event: done\ndata: {"assistant":"","session_id":"00000000-0000-4000-8000-000000000001"}\n\n',
          ]);
        }
        return Response.json({});
      }) as unknown as typeof fetch
    );

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

    fireEvent.click(screen.getByRole("button", { name: "Send message" }));

    await waitFor(() => {
      expect(chatBodies.length).toBe(1);
    });
    const body = chatBodies[0];
    const visualAttachments = body.visual_attachments as Record<string, unknown>[];
    expect(visualAttachments).toHaveLength(1);
    expect(visualAttachments[0].data_url).toMatch(/^data:image\/png;base64,/);
    expect(String(body.message)).toContain("visible in this turn");
    expect(String(body.message)).not.toContain("OCR");
    expect(await screen.findByAltText(/pasted-image-.*\.png/)).toBeInTheDocument();
  });

  it("does not show a network error after the chat stream already completed", async () => {
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
        if (url.endsWith("/api/chat")) {
          return eventStreamThatErrorsAfterDone();
        }
        return Response.json({});
      }) as unknown as typeof fetch
    );

    render(<ChatPage />);

    const composer = await screen.findByLabelText("Message Samantha");
    fireEvent.change(composer, { target: { value: "hello" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));

    await waitFor(() => {
      expect(screen.queryByText("Chat error")).not.toBeInTheDocument();
      expect(screen.queryByText("network error")).not.toBeInTheDocument();
    });
  });
});
