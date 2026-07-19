import { describe, expect, it } from "vitest";

import { hasCompleteLlmConfig, hasCompleteLlmSetup, nextStageFromInitStatus } from "./page";

describe("init model setup guards", () => {
  it("requires provider and model for a role config", () => {
    expect(hasCompleteLlmConfig(null)).toBe(false);
    expect(hasCompleteLlmConfig({ provider: "openai" })).toBe(false);
    expect(hasCompleteLlmConfig({ model: "gpt-5" })).toBe(false);
    expect(hasCompleteLlmConfig({ provider: " ", model: "gpt-5" })).toBe(false);
    expect(hasCompleteLlmConfig({ provider: "openai", model: " " })).toBe(false);
    expect(hasCompleteLlmConfig({ provider: "openai", model: "gpt-5" })).toBe(true);
  });

  it("does not trust llm_configured when one role is missing", () => {
    expect(
      hasCompleteLlmSetup({
        status: { stage: "llm", steps: { llm_configured: true } },
        llm_heartbeat: { provider: "openai", model: "gpt-5" },
        llm_subconscious: null,
      })
    ).toBe(false);
  });

  it("passes only when conscious and subconscious configs are complete", () => {
    expect(
      hasCompleteLlmSetup({
        status: { stage: "llm", steps: { llm_configured: true } },
        llm_heartbeat: { provider: "openai", model: "gpt-5" },
        llm_subconscious: { provider: "anthropic", model: "claude-sonnet-4-5-latest" },
      })
    ).toBe(true);
  });

  it("does not skip Models from ambient stored config while DB is still at llm", () => {
    expect(
      nextStageFromInitStatus("llm", {
        status: { stage: "llm", steps: { llm_configured: true } },
        llm_heartbeat: { provider: "openai-codex", model: "gpt-5.6-terra" },
        llm_subconscious: { provider: "openai-codex", model: "gpt-5.6-terra" },
      })
    ).toBe("llm");
  });

  it("preserves an explicit in-session advance after models are saved", () => {
    expect(
      nextStageFromInitStatus("choose_path", {
        status: { stage: "llm", steps: { llm_configured: true } },
        llm_heartbeat: { provider: "openai", model: "gpt-5" },
        llm_subconscious: { provider: "openai", model: "gpt-5" },
      })
    ).toBe("choose_path");
  });
});
