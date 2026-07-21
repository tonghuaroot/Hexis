import { describe, expect, it } from "vitest";

import { isEmbeddingUnavailable } from "./init-errors";

describe("init route errors", () => {
  it("detects DB embedding outages", () => {
    expect(
      isEmbeddingUnavailable(
        "Failed to get embeddings: Embedding service not available after 30 seconds"
      )
    ).toBe(true);
    expect(
      isEmbeddingUnavailable(
        "Failed to connect to host.docker.internal port 42666: Connection refused"
      )
    ).toBe(true);
  });

  it("does not classify unrelated failures as embedding outages", () => {
    expect(isEmbeddingUnavailable("character card is malformed")).toBe(false);
  });
});
