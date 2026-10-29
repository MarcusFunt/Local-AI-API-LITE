/** @vitest-environment jsdom */
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { OperationStateSnapshot, Profile } from "../api";
import { OperationPanel } from "./OperationPanel";

const apiMocks = vi.hoisted(() => ({
  getOperationState: vi.fn(),
  startInference: vi.fn(),
  stopInference: vi.fn(),
  restartInference: vi.fn(),
  applyProfile: vi.fn(),
}));

vi.mock("../api", () => apiMocks);

afterEach(() => cleanup());

const profiles: Profile[] = [
  {
    id: "baseline-4k-2",
    name: "Baseline 4K / 2 sequences",
    model_id: "RedHatAI/Qwen3.5-4B-quantized.w4a16",
    model_revision: "7a613872f394578b0b52b683ff4ac47516b4bcaf",
    model_alias: "ddu-narrator",
    reasoning_parser: "qwen3",
    engine: "vLLM 0.32.0",
    image_ref: "vllm/vllm-openai:v0.32.0@sha256:test",
    quantization: "W4A16",
    max_model_len: 4096,
    max_num_seqs: 2,
    gpu_memory_utilization: 0.75,
    validation_state: "validated",
    description: "Baseline profile",
  },
  {
    id: "baseline-8k-4",
    name: "Baseline 8K / 4 sequences",
    model_id: "RedHatAI/Qwen3.5-4B-quantized.w4a16",
    model_revision: "7a613872f394578b0b52b683ff4ac47516b4bcaf",
    model_alias: "ddu-narrator",
    reasoning_parser: "qwen3",
    engine: "vLLM 0.32.0",
    image_ref: "vllm/vllm-openai:v0.32.0@sha256:test",
    quantization: "W4A16",
    max_model_len: 8192,
    max_num_seqs: 4,
    gpu_memory_utilization: 0.75,
    validation_state: "validated",
    description: "Larger context profile",
  },
];

function snapshot(desiredState: "running" | "stopped" = "stopped"): OperationStateSnapshot {
  return {
    configured: true,
    control_state: {
      desired_state: desiredState,
      selected_profile_id: null,
      active_profile_id: null,
      last_known_good_profile_id: null,
      updated_at: 1000,
    },
    latest_operation: null,
  };
}

describe("OperationPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.getOperationState.mockResolvedValue(snapshot());
    apiMocks.startInference.mockResolvedValue({ operation_id: "start", phase: "succeeded" });
    apiMocks.stopInference.mockResolvedValue({ operation_id: "stop", phase: "succeeded" });
    apiMocks.restartInference.mockResolvedValue({ operation_id: "restart", phase: "succeeded" });
    apiMocks.applyProfile.mockResolvedValue({ operation_id: "profile", phase: "configured" });
  });

  it("sends only the selected profile ID when applying a profile", async () => {
    render(<OperationPanel profiles={profiles} csrfToken="csrf-value" />);
    await screen.findByText("Desired state: Stopped");
    fireEvent.change(screen.getByRole("combobox", { name: "Inference profile" }), {
      target: { value: "baseline-8k-4" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Apply profile" }));
    expect(apiMocks.applyProfile).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Confirm action" }));

    await waitFor(() => expect(apiMocks.applyProfile).toHaveBeenCalledWith("baseline-8k-4", "csrf-value"));
    expect(apiMocks.applyProfile.mock.calls[0]).toHaveLength(2);
  });

  it("requires explicit confirmation before a disruptive action", async () => {
    render(<OperationPanel profiles={profiles} csrfToken="csrf-value" />);
    await screen.findByText("Desired state: Stopped");

    fireEvent.click(screen.getByRole("button", { name: "Start inference" }));

    expect(screen.getByRole("alertdialog")).toBeTruthy();
    expect(apiMocks.startInference).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(apiMocks.startInference).not.toHaveBeenCalled();
  });

  it("shows Loading until the action resolves and reports candidate and rollback outcomes", async () => {
    let resolveStart!: (value: object) => void;
    apiMocks.startInference.mockImplementation(() => new Promise((resolve) => { resolveStart = resolve; }));
    const loadingSnapshot = snapshot();
    loadingSnapshot.latest_operation = {
      operation_id: "start",
      kind: "start",
      phase: "loading",
      started_at: "2026-10-29T12:00:00Z",
      updated_at: "2026-10-29T12:00:01Z",
      completed_at: null,
      requested_profile: "baseline-4k-2",
      requested_state: "running",
      outcome_category: null,
      recovery_state: {},
    };
    const terminalSnapshot = snapshot();
    terminalSnapshot.latest_operation = {
      operation_id: "start",
      kind: "start",
      phase: "rolled_back",
      started_at: "2026-10-29T12:00:00Z",
      updated_at: "2026-10-29T12:00:02Z",
      completed_at: "2026-10-29T12:00:02Z",
      requested_profile: "baseline-4k-2",
      requested_state: "running",
      outcome_category: "candidate_failed_rolled_back",
      recovery_state: {
        candidate: { outcome: "failed", category: "contract_failed" },
        rollback: { outcome: "succeeded", profile_id: "baseline-4k-2" },
      },
    };
    apiMocks.getOperationState
      .mockResolvedValueOnce(snapshot())
      .mockResolvedValueOnce(loadingSnapshot)
      .mockResolvedValue(terminalSnapshot);
    render(<OperationPanel profiles={profiles} csrfToken="csrf-value" />);
    await screen.findByText("Desired state: Stopped");
    fireEvent.click(screen.getByRole("button", { name: "Start inference" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm action" }));
    expect(await screen.findByText(/Loading model and waiting for health checks/i, {}, { timeout: 3000 })).toBeTruthy();

    await act(async () => resolveStart({
      operation_id: "start",
      phase: "rolled_back",
      outcome_category: "candidate_failed_rolled_back",
      recovery_state: {
        candidate: { outcome: "failed", category: "contract_failed" },
        rollback: { outcome: "succeeded", profile_id: "baseline-4k-2" },
      },
    }));

    expect(await screen.findByText(/Candidate failed.*contract failed/i)).toBeTruthy();
    expect(screen.getByText(/Rollback succeeded/i)).toBeTruthy();
  });

  it("keeps the deliberate stopped state after remount without starting inference", async () => {
    apiMocks.getOperationState.mockResolvedValue(snapshot("stopped"));
    const first = render(<OperationPanel profiles={profiles} csrfToken="csrf-value" />);
    await screen.findByText("Desired state: Stopped");
    first.unmount();

    render(<OperationPanel profiles={profiles} csrfToken="csrf-value" />);

    expect(await screen.findByText("Desired state: Stopped")).toBeTruthy();
    expect(apiMocks.startInference).not.toHaveBeenCalled();
  });
});
