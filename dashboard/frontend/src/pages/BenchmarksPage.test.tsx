/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { JobHistoryResponse } from "../api";
import { BenchmarksPage } from "./BenchmarksPage";

const apiMocks = vi.hoisted(() => ({
  getJobHistory: vi.fn(),
  compareJobs: vi.fn(),
}));

vi.mock("../api", () => apiMocks);

afterEach(() => cleanup());

const history: JobHistoryResponse = {
  jobs: [
    {
      job_id: "a".repeat(32),
      kind: "performance-benchmark",
      state: "completed",
      created_at: "2026-10-29T12:00:00Z",
      started_at: "2026-10-29T12:00:01Z",
      completed_at: "2026-10-29T12:00:10Z",
      options: { profile_id: "baseline-4k-2", concurrency: 1 },
      summary: { profile_id: "baseline-4k-2", hardware: { gpu_model: "NVIDIA RTX 3060" } },
      error_category: null,
      artifact_id: `${"a".repeat(32)}.jsonl`,
      exit_code: 0,
    },
    {
      job_id: "b".repeat(32),
      kind: "performance-benchmark",
      state: "completed",
      created_at: "2026-10-29T13:00:00Z",
      started_at: "2026-10-29T13:00:01Z",
      completed_at: "2026-10-29T13:00:10Z",
      options: { profile_id: "baseline-8k-4", concurrency: 1 },
      summary: { profile_id: "baseline-8k-4", hardware: { gpu_model: "NVIDIA RTX 4090" } },
      error_category: null,
      artifact_id: `${"b".repeat(32)}.jsonl`,
      exit_code: 0,
    },
  ],
  limit: 50,
  offset: 0,
};

describe("BenchmarksPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.getJobHistory.mockResolvedValue(history);
    apiMocks.compareJobs.mockResolvedValue({
      comparable: false,
      differences: [
        { field: "profile_id", left: "baseline-4k-2", right: "baseline-8k-4" },
        { field: "hardware.gpu_model", left: "NVIDIA RTX 3060", right: "NVIDIA RTX 4090" },
      ],
      left: { request_count: 10 },
      right: { request_count: 10 },
    });
  });

  it("shows profile and hardware differences when comparing runs", async () => {
    render(<BenchmarksPage csrfToken="csrf-value" />);
    await screen.findByText("Completed runs");
    fireEvent.change(screen.getByRole("combobox", { name: "First run" }), {
      target: { value: "a".repeat(32) },
    });
    fireEvent.change(screen.getByRole("combobox", { name: "Second run" }), {
      target: { value: "b".repeat(32) },
    });
    fireEvent.click(screen.getByRole("button", { name: "Compare runs" }));

    expect(await screen.findByText("Runs are not comparable")).toBeTruthy();
    expect(screen.getByText(/baseline-4k-2.*baseline-8k-4/i)).toBeTruthy();
    expect(screen.getByText(/NVIDIA RTX 3060.*NVIDIA RTX 4090/i)).toBeTruthy();
    await waitFor(() => expect(apiMocks.compareJobs).toHaveBeenCalledWith("a".repeat(32), "b".repeat(32)));
  });
});
