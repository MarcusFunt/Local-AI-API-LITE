/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DatabasesPage } from "./DatabasesPage";

const apiMocks = vi.hoisted(() => ({
  getDatabases: vi.fn(),
  saveDatabase: vi.fn(),
  checkDatabase: vi.fn(),
  deleteDatabase: vi.fn(),
  revealDatabaseCredential: vi.fn(),
}));
vi.mock("../api", () => apiMocks);

afterEach(() => cleanup());

describe("DatabasesPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.getDatabases.mockResolvedValue({
      configuration_state: { state: "Unconfigured" },
      databases: [],
    });
    apiMocks.saveDatabase.mockResolvedValue({ database_id: "a".repeat(32), name: "Analytics" });
    apiMocks.checkDatabase.mockResolvedValue({
      database_id: "a".repeat(32),
      state: "Unavailable",
      checked_at: "2026-10-29T12:00:00Z",
      latency_ms: 12,
      failure_category: "connection_error",
    });
    apiMocks.deleteDatabase.mockResolvedValue(undefined);
    apiMocks.revealDatabaseCredential.mockResolvedValue({ credential: "private-dsn-value" });
  });

  it("distinguishes unconfigured monitoring from a configured database that is down", async () => {
    const { getByRole } = render(<DatabasesPage csrfToken="csrf-value" />);
    expect(await screen.findByText("Unconfigured")).toBeTruthy();

    apiMocks.getDatabases.mockResolvedValueOnce({
      configuration_state: { state: "Configured" },
      databases: [{
        database_id: "a".repeat(32),
        name: "Analytics",
        state: "Unavailable",
        checked_at: "2026-10-29T12:00:00Z",
        latency_ms: 12,
        failure_category: "connection_error",
      }],
    });
    fireEvent.click(getByRole("button", { name: "Refresh status" }));

    expect(await screen.findByText("Analytics")).toBeTruthy();
    expect(screen.getByText("Unavailable")).toBeTruthy();
    expect(screen.getByText("connection_error")).toBeTruthy();
    expect(screen.queryByText(/not configured/i)).toBeNull();
  });

  it("reveals a credential only after an explicit one-time request", async () => {
    apiMocks.getDatabases.mockResolvedValue({
      configuration_state: { state: "Configured" },
      databases: [{
        database_id: "a".repeat(32),
        name: "Analytics",
        state: "Unknown",
        checked_at: null,
        latency_ms: null,
        failure_category: null,
      }],
    });
    render(<DatabasesPage csrfToken="csrf-value" />);
    await screen.findByText("Analytics");
    expect(screen.queryByText("private-dsn-value")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Reveal credential" }));

    expect(await screen.findByText("private-dsn-value")).toBeTruthy();
    expect(apiMocks.revealDatabaseCredential).toHaveBeenCalledWith("a".repeat(32), "csrf-value");
  });
});
