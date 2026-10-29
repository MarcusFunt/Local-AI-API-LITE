/** @vitest-environment jsdom */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { DashboardStatus } from "../api";
import { OverviewPage } from "./OverviewPage";

afterEach(() => cleanup());

describe("overview telemetry labels", () => {
  it("identifies GPU utilization as device-wide telemetry", () => {
    const status = {
      refreshed_at: "2026-10-29T10:00:00Z",
      controller: { state: "Ready" },
      inference: {
        state: "Stopped",
        availability: "available",
        container: null,
        configuration: null,
        last_health_probe_at: null,
        last_successful_health_probe_at: null,
        last_failure_reason: null,
      },
      active_profile: null,
      telemetry: {
        cpu_percent: null,
        ram_used_bytes: null,
        ram_total_bytes: null,
        cache_disk_free_bytes: null,
        gpu: {
          available: false,
          scope: "device-wide",
          reason: "NVML unavailable",
          utilization_percent: null,
          memory_used_bytes: null,
          memory_total_bytes: null,
          memory_free_bytes: null,
          temperature_c: null,
          power_watts: null,
        },
      },
      status_cache: {
        observed_at: "2026-10-29T10:00:00Z",
        stale: false,
        refreshing: false,
        last_poll_error: null,
        last_poll_error_at: null,
      },
    } satisfies DashboardStatus;

    render(<OverviewPage status={status} samples={[]} />);

    expect(screen.getByText("GPU UTILIZATION")).toBeTruthy();
    expect(screen.getByText("Device-wide · all processes")).toBeTruthy();
  });
});
