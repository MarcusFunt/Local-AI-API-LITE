export type InferenceState =
  | "Ready"
  | "Stopped"
  | "Starting"
  | "Switching"
  | "Degraded"
  | "Failed"
  | "Unknown";

export interface GpuTelemetry {
  available: boolean;
  scope: "device-wide";
  reason: string | null;
  utilization_percent: number | null;
  memory_used_bytes: number | null;
  memory_total_bytes: number | null;
  memory_free_bytes: number | null;
  temperature_c: number | null;
  power_watts: number | null;
}

export interface Telemetry {
  cpu_percent: number | null;
  ram_used_bytes: number | null;
  ram_total_bytes: number | null;
  cache_disk_free_bytes: number | null;
  gpu: GpuTelemetry;
}

export interface Profile {
  id: string;
  name: string;
  model_id: string;
  model_revision: string;
  model_alias: string;
  reasoning_parser: string;
  engine: string;
  image_ref: string;
  quantization: string;
  max_model_len: number;
  max_num_seqs: number;
  gpu_memory_utilization: number;
  validation_state: string;
  description: string;
}

export type DesiredInferenceState = "running" | "stopped";

export interface OperationRecord {
  operation_id: string;
  kind: string;
  phase: string;
  started_at: string;
  updated_at: string;
  completed_at: string | null;
  requested_profile: string | null;
  requested_state: DesiredInferenceState | null;
  outcome_category: string | null;
  recovery_state: Record<string, unknown>;
}

export interface ControlState {
  desired_state: DesiredInferenceState;
  selected_profile_id: string | null;
  active_profile_id: string | null;
  last_known_good_profile_id: string | null;
  updated_at: number;
}

export interface OperationStateSnapshot {
  configured: boolean;
  control_state: ControlState;
  latest_operation: OperationRecord | null;
}

export interface ModelConfiguration {
  model_id: string;
  model_revision: string | null;
  model_alias: string | null;
  reasoning_parser: string | null;
  engine: string;
  image_ref: string | null;
  quantization: string;
  max_model_len: number | null;
  max_num_seqs: number | null;
  gpu_memory_utilization: number | null;
}

export interface ContainerStatus {
  name: string;
  state: string;
  health: string;
  uptime_seconds: number | null;
  restart_count: number;
  cpu_percent: number | null;
  memory_used_bytes: number | null;
  memory_limit_bytes: number | null;
  host_published_ports: string[];
}

export interface DashboardStatus {
  refreshed_at: string;
  controller: { state: string };
  inference: {
    state: InferenceState;
    availability: "available" | "unavailable";
    container: ContainerStatus | null;
    configuration: ModelConfiguration | null;
    last_health_probe_at: string | null;
    last_successful_health_probe_at: string | null;
    last_failure_reason: string | null;
  };
  active_profile: Profile | null;
  telemetry: Telemetry;
  status_cache: {
    observed_at: string | null;
    stale: boolean;
    refreshing: boolean;
    last_poll_error: string | null;
    last_poll_error_at: string | null;
  };
}

export interface TelemetrySample {
  capturedAt: string;
  cpuPercent: number | null;
  gpuPercent: number | null;
  gpuMemoryPercent: number | null;
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path, {
    headers: { Accept: "application/json" },
    credentials: "same-origin",
    cache: "no-store",
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) {
    throw new ApiRequestError(response.status);
  }
  return (await response.json()) as T;
}

export class ApiRequestError extends Error {
  constructor(readonly status: number) {
    super(`Dashboard API returned ${status}`);
    this.name = "ApiRequestError";
  }
}

export function getStatus(): Promise<DashboardStatus> {
  return getJson<DashboardStatus>("/api/control/v1/status");
}

export function getProfiles(): Promise<{ profiles: Profile[] }> {
  return getJson<{ profiles: Profile[] }>("/api/control/v1/profiles");
}

async function postOperation(
  path: string,
  csrfToken: string,
  body?: { profile_id: string },
): Promise<OperationRecord> {
  const response = await fetch(path, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "X-CSRF-Token": csrfToken,
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    credentials: "same-origin",
    cache: "no-store",
    ...(body ? { body: JSON.stringify(body) } : {}),
    // Profile activation can legitimately spend several minutes loading model weights.
    signal: AbortSignal.timeout(11 * 60 * 1000),
  });
  if (!response.ok) throw new ApiRequestError(response.status);
  return (await response.json()) as OperationRecord;
}

export function getOperationState(): Promise<OperationStateSnapshot> {
  return getJson<OperationStateSnapshot>("/api/control/v1/operations/state");
}

export function startInference(profileId: string, csrfToken: string): Promise<OperationRecord> {
  return postOperation("/api/control/v1/operations/start", csrfToken, { profile_id: profileId });
}

export function stopInference(csrfToken: string): Promise<OperationRecord> {
  return postOperation("/api/control/v1/operations/stop", csrfToken);
}

export function restartInference(csrfToken: string): Promise<OperationRecord> {
  return postOperation("/api/control/v1/operations/restart", csrfToken);
}

export function applyProfile(profileId: string, csrfToken: string): Promise<OperationRecord> {
  return postOperation("/api/control/v1/operations/apply-profile", csrfToken, { profile_id: profileId });
}
