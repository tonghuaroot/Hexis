import { describe, expect, it } from "vitest";

import { isActiveIngestJob, mergeIngestJobs, normalizeIngestJob } from "./jobs";

describe("ingest job helpers", () => {
  it("normalizes exact DB job rows by deriving display title from payload", () => {
    const job = normalizeIngestJob({
      id: "job-1",
      kind: "artifact",
      status: "in_progress",
      attempts: 2,
      error: null,
      payload: { filename: "source.pdf" },
      progress: { sections_done: 3 },
      result: null,
      created_at: "2026-07-20T12:00:00.000Z",
      updated_at: "2026-07-20T12:00:03.000Z",
      completed_at: null,
    });

    expect(job?.title).toBe("source.pdf");
    expect(job?.progress).toEqual({ sections_done: 3 });
    expect(job && isActiveIngestJob(job)).toBe(true);
  });

  it("merges exact polled rows over recent-list placeholders", () => {
    const placeholder = normalizeIngestJob({
      id: "job-1",
      kind: "url",
      status: "pending",
      title: "https://example.com",
      created_at: "2026-07-20T12:00:00.000Z",
    });
    const exact = normalizeIngestJob({
      id: "job-1",
      kind: "url",
      status: "completed",
      payload: { url: "https://example.com" },
      result: { memories_created: 5 },
      created_at: "2026-07-20T12:00:00.000Z",
      completed_at: "2026-07-20T12:00:10.000Z",
    });

    expect(placeholder).not.toBeNull();
    expect(exact).not.toBeNull();
    const merged = mergeIngestJobs([placeholder!], [exact!]);

    expect(merged).toHaveLength(1);
    expect(merged[0].status).toBe("completed");
    expect(merged[0].result?.memories_created).toBe(5);
  });
});
