/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { RetrievalOperationStateSnapshot } from "../api";
import { RetrievalOperationsPanel } from "./RetrievalOperationsPanel";

const apiMocks = vi.hoisted(() => ({
  getRetrievalOperationState: vi.fn(),
  operateRetrievalService: vi.fn(),
}));

vi.mock("../api", () => ({ ...apiMocks, ApiRequestError: class ApiRequestError extends Error { constructor(readonly status: number) { super(); } } }));

afterEach(() => cleanup());

function snapshot(configured = true): RetrievalOperationStateSnapshot {
  return {
    configured,
    services: {
      embed: { desired_state: "stopped", latest_operation: null },
      rerank: { desired_state: "running", latest_operation: null },
    },
    latest_operation: null,
    activity: null,
  };
}

describe("RetrievalOperationsPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.getRetrievalOperationState.mockResolvedValue(snapshot());
    apiMocks.operateRetrievalService.mockResolvedValue({
      operation_id: "op-1",
      service: "embed",
      action: "start",
      phase: "succeeded",
      outcome_category: "success",
    });
  });

  it("requires confirmation and sends a fixed service action with the CSRF token", async () => {
    render(<RetrievalOperationsPanel csrfToken="csrf-value" workers={[]} />);
    await screen.findAllByText("Desired state");

    fireEvent.click(within(screen.getByRole("article", { name: "ddu-embed controls" })).getByRole("button", { name: "Start" }));
    expect(screen.getByRole("alertdialog")).toBeTruthy();
    expect(apiMocks.operateRetrievalService).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Confirm action" }));

    await waitFor(() => expect(apiMocks.operateRetrievalService).toHaveBeenCalledWith("embed", "start", "csrf-value"));
    expect(screen.getByText("embed start: success")).toBeTruthy();
  });

  it("lets the operator cancel without sending the operation", async () => {
    render(<RetrievalOperationsPanel csrfToken="csrf-value" workers={[]} />);
    await screen.findAllByText("Desired state");
    fireEvent.click(within(screen.getByRole("article", { name: "ddu-embed controls" })).getByRole("button", { name: "Stop" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(apiMocks.operateRetrievalService).not.toHaveBeenCalled();
  });

  it("keeps controls disabled until the retrieval root is configured", async () => {
    apiMocks.getRetrievalOperationState.mockResolvedValue(snapshot(false));
    render(<RetrievalOperationsPanel csrfToken="csrf-value" workers={[]} />);

    const startButtons = await screen.findAllByRole("button", { name: "Start" });
    expect(startButtons.every((button) => (button as HTMLButtonElement).disabled)).toBe(true);
    expect(screen.getByRole("status").textContent).toContain("Configure the retrieval Compose root");
  });
});
