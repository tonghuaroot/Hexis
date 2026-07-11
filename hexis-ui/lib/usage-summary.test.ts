import { describe, expect, it } from "vitest";

import { summarizeUsageByModel } from "./usage-summary";

describe("summarizeUsageByModel", () => {
  it("combines operation-level rows into unique model totals", () => {
    const summary = summarizeUsageByModel([
      {
        provider: "openai-codex",
        model: "gpt-5.6-terra",
        call_count: 3n,
        total_tokens: 0n,
        total_cost: "0",
      },
      {
        provider: "openai-codex",
        model: "gpt-5.6-terra",
        call_count: 14n,
        total_tokens: 1200n,
        total_cost: "1.25",
      },
      {
        provider: "openai-codex",
        model: "gpt-5.5",
        call_count: 2n,
        total_tokens: 800n,
        total_cost: "2.50",
      },
    ]);

    expect(summary.byModel).toEqual([
      {
        provider: "openai-codex",
        model: "gpt-5.5",
        calls: 2,
        tokens: 800,
        cost_usd: 2.5,
      },
      {
        provider: "openai-codex",
        model: "gpt-5.6-terra",
        calls: 17,
        tokens: 1200,
        cost_usd: 1.25,
      },
    ]);
    expect(summary.totalCalls).toBe(19);
    expect(summary.totalTokens).toBe(2000);
    expect(summary.totalCost).toBe(3.75);
  });
});
