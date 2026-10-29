/** @vitest-environment jsdom */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LogsPage } from "./LogsPage";

const apiMocks = vi.hoisted(() => ({ getLogs: vi.fn() }));
vi.mock("../api", () => apiMocks);

afterEach(() => cleanup());

describe("LogsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.getLogs.mockResolvedValue({
      availability: "available",
      lines: ["server ready", "[redacted content-bearing log output]"],
      error_category: null,
    });
  });

  it("displays only the bounded, sanitized log response", async () => {
    render(<LogsPage />);

    const output = await screen.findByLabelText("Sanitized service logs");
    expect(output.textContent).toContain("server ready");
    expect(output.textContent).toContain("[redacted content-bearing log output]");
    expect(output.textContent).not.toMatch(/secret|prompt body/i);
    expect(apiMocks.getLogs).toHaveBeenCalledWith(100);
  });
});
