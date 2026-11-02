/** @vitest-environment jsdom */
import { cleanup, render, screen, within } from "@testing-library/react";
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
      model_services: [],
      speech_services: [],
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

  it("shows retrieval workers independently with identity, queue and snapshot state", () => {
    const status = {
      refreshed_at: "2026-10-29T10:00:00Z",
      controller: { state: "Ready" },
      inference: {
        state: "Ready",
        availability: "available",
        container: {
          name: "local-ai-api-lite-vllm-1",
          state: "Running",
          health: "Healthy",
          uptime_seconds: 3600,
          restart_count: 0,
          cpu_percent: 0,
          memory_used_bytes: 0,
          memory_limit_bytes: 32 * 1024 ** 3,
          host_published_ports: [],
        },
        configuration: {
          model_id: "RedHatAI/Qwen3.5-4B-quantized.w4a16",
          model_revision: "7a613872f394578b0b52b683ff4ac47516b4bcaf",
          model_alias: "ddu-narrator",
          reasoning_parser: "qwen3",
          engine: "vLLM 0.32.0",
          image_ref: "vllm/vllm-openai:v0.32.0",
          quantization: "W4A16",
          max_model_len: 8192,
          max_num_seqs: 4,
          gpu_memory_utilization: 0.75,
        },
        last_health_probe_at: null,
        last_successful_health_probe_at: "2026-10-29T10:00:00Z",
        last_failure_reason: null,
      },
      active_profile: null,
      model_services: [
        {
          service: "embed",
          alias: "ddu-embed",
          project_name: "local-ai-api-lite-retrieval",
          state: "Ready",
          availability: "available",
          container: null,
          image_ref: "local-ai-api-lite-retrieval:0.1.0",
          image_id: "sha256:embed-image",
          model_id: "ddu-embed",
          model_revision: "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
          expected_model_id: "ddu-embed",
          expected_model_revision: "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
          model_repository: "Qwen/Qwen3-Embedding-0.6B",
          last_successful_readiness_check_at: "2026-10-29T10:00:00Z",
          last_error_category: null,
          metrics: {
            accepted_total: 8,
            completed_total: 7,
            failed_total: 1,
            timed_out_total: 0,
            rejected_total: 0,
            active_requests: 1,
            queued_requests: 0,
            outstanding_requests: 1,
            max_outstanding: 2,
            saturated: false,
            stuck_inference: false,
            last_successful_inference_at: "2026-10-29T09:59:00Z",
          },
          metrics_availability: "available",
          observed_at: "2026-10-29T10:00:00Z",
        },
        {
          service: "rerank",
          alias: "ddu-rerank",
          project_name: "local-ai-api-lite-retrieval",
          state: "Starting/Loading",
          availability: "available",
          container: null,
          image_ref: "local-ai-api-lite-retrieval:0.1.0",
          image_id: "sha256:rerank-image",
          model_id: null,
          model_revision: null,
          expected_model_id: "ddu-rerank",
          expected_model_revision: "8215cf04918ba6f7b6a62bb44238ce2953d8831c",
          model_repository: "Alibaba-NLP/gte-multilingual-reranker-base",
          last_successful_readiness_check_at: null,
          last_error_category: "model_loading",
          metrics: null,
          metrics_availability: "unavailable",
          observed_at: "2026-10-29T10:00:00Z",
        },
      ],
      speech_services: [
        {
          service: "stt",
          alias: "ddu-stt",
          project_name: "local-ai-api-lite-speech",
          state: "Ready",
          availability: "available",
          container: {
            name: "local-ai-api-lite-speech-stt-1",
            state: "Running",
            health: "Healthy",
            uptime_seconds: 60,
            restart_count: 0,
            cpu_percent: 0,
            memory_used_bytes: 0,
            memory_limit_bytes: 4 * 1024 ** 3,
            host_published_ports: [],
          },
          image_ref: "local-ai-api-lite-speech:0.1.0",
          image_id: "sha256:speech-image",
          model_id: "Systran/faster-whisper-small",
          model_revision: "536b0662742c02347bc0e980a01041f333bce120",
          expected_model_id: "Systran/faster-whisper-small",
          expected_model_revision: "536b0662742c02347bc0e980a01041f333bce120",
          model_repository: "Systran/faster-whisper-small",
          last_successful_readiness_check_at: "2026-10-29T10:00:00Z",
          last_error_category: null,
          metrics: {
            accepted_total: 0,
            completed_total: 0,
            failed_total: 0,
            timed_out_total: 0,
            rejected_total: 0,
            active_requests: 0,
            queued_requests: 0,
            outstanding_requests: 0,
            max_outstanding: 2,
            saturated: false,
            stuck_inference: false,
            last_successful_inference_at: null,
          },
          metrics_availability: "available",
          observed_at: "2026-10-29T10:00:00Z",
        },
        {
          service: "tts",
          alias: "ddu-tts",
          project_name: "local-ai-api-lite-speech",
          state: "Unknown/Unavailable",
          availability: "unavailable",
          container: null,
          image_ref: null,
          image_id: null,
          model_id: null,
          model_revision: null,
          expected_model_id: "rhasspy/piper-voices",
          expected_model_revision: "320d5f7f7751a17ef6512d5c23863056c6a11c0f",
          model_repository: "rhasspy/piper-voices · da_DK-talesyntese-medium",
          last_successful_readiness_check_at: null,
          last_error_category: "command_timeout",
          metrics: null,
          metrics_availability: "unavailable",
          observed_at: null,
        },
      ],
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
        stale: true,
        refreshing: false,
        last_poll_error: null,
        last_poll_error_at: null,
      },
    } satisfies DashboardStatus;

    render(<OverviewPage status={status} samples={[]} />);

    expect(screen.getByRole("region", { name: "ddu-embed status" })).toBeTruthy();
    expect(screen.getByRole("region", { name: "ddu-rerank status" })).toBeTruthy();
    const stt = screen.getByRole("region", { name: "ddu-stt status" });
    const tts = screen.getByRole("region", { name: "ddu-tts status" });
    expect(within(stt).getByText("Running")).toBeTruthy();
    expect(within(stt).getByText("0.0%")).toBeTruthy();
    expect(within(stt).getByText("0 B / 4.0 GiB")).toBeTruthy();
    expect(within(stt).getByText("Never")).toBeTruthy();
    expect(within(tts).getByText("Unknown/Unavailable")).toBeTruthy();
    expect(within(tts).getByText("command_timeout")).toBeTruthy();
    expect(within(tts).getAllByText("Unavailable").length).toBeGreaterThan(1);
    expect(screen.getByText("97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3")).toBeTruthy();
    expect(screen.getByText("Starting/Loading")).toBeTruthy();
    expect(screen.getByText("8 accepted · 7 completed · 1 failed · 0 timed out")).toBeTruthy();
    expect(screen.getAllByText("Snapshot age").length).toBe(4);
    expect(screen.getAllByText(/stale$/).length).toBeGreaterThan(0);
    expect(screen.getByText("7a613872f394578b0b52b683ff4ac47516b4bcaf")).toBeTruthy();
    expect(screen.getByText("Status snapshot age")).toBeTruthy();

    cleanup();
    status.speech_services[0].metrics!.completed_total = 1;
    status.speech_services[0].metrics!.accepted_total = 1;
    status.speech_services[1].observed_at = null;
    render(<OverviewPage status={status} samples={[]} />);

    expect(within(screen.getByRole("region", { name: "ddu-stt status" })).getByText("Timestamp unavailable")).toBeTruthy();
    expect(within(screen.getByRole("region", { name: "ddu-tts status" })).getByText("Unavailable · stale")).toBeTruthy();
  });
});
