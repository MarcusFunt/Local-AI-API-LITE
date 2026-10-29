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

export type JobId = "server-readiness" | "api-smoke" | "performance-benchmark";
export type JobState = "queued" | "running" | "completed" | "failed" | "cancelled" | "rejected";

export interface JobRecord {
  job_id: string;
  kind: JobId;
  state: JobState;
  created_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  options: Record<string, string | number | boolean>;
  summary: Record<string, unknown>;
  error_category: string | null;
  artifact_id: string | null;
  exit_code: number | null;
}

export interface JobHistoryResponse {
  jobs: JobRecord[];
  limit: number;
  offset: number;
}

export interface JobDifference {
  field: string;
  left: string | number;
  right: string | number;
}

export interface JobComparison {
  comparable: boolean;
  differences: JobDifference[];
  left: JobRecord;
  right: JobRecord;
}

export interface JobCleanupPreview {
  removable_jobs: number;
  removable_artifacts: number;
}

export interface LogsResponse {
  availability: "available" | "unavailable";
  lines: string[];
  error_category: string | null;
}

export type DatabaseConfigurationState = "Unconfigured" | "Configured" | "Unavailable";
export type DatabaseProbeState = "Unknown" | "Available" | "Unavailable";

export interface DatabaseStatus {
  database_id: string;
  name: string;
  state: DatabaseProbeState;
  checked_at: string | null;
  latency_ms: number | null;
  failure_category: string | null;
}

export interface DatabaseListResponse {
  configuration_state: { state: DatabaseConfigurationState };
  databases: DatabaseStatus[];
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

export function getLogs(tail = 100): Promise<LogsResponse> {
  const boundedTail = Math.max(1, Math.min(200, Math.trunc(tail)));
  return getJson<LogsResponse>(`/api/control/v1/logs?tail=${boundedTail}`);
}

export function getDatabases(): Promise<DatabaseListResponse> {
  return getJson<DatabaseListResponse>("/api/control/v1/databases");
}

async function databaseMutation<T>(
  path: string,
  method: "POST" | "PUT" | "DELETE",
  csrfToken: string,
  body?: Record<string, string>,
): Promise<T> {
  const response = await fetch(path, {
    method,
    headers: {
      Accept: "application/json",
      "X-CSRF-Token": csrfToken,
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    credentials: "same-origin",
    cache: "no-store",
    ...(body ? { body: JSON.stringify(body) } : {}),
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) throw new ApiRequestError(response.status);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function saveDatabase(
  name: string,
  csrfToken: string,
  options: { databaseId?: string; dsn?: string } = {},
): Promise<{ database_id: string; name: string; state?: string }> {
  const path = options.databaseId
    ? `/api/control/v1/databases/${encodeURIComponent(options.databaseId)}`
    : "/api/control/v1/databases";
  const body = { name, ...(options.dsn ? { dsn: options.dsn } : {}) };
  return databaseMutation(path, options.databaseId ? "PUT" : "POST", csrfToken, body);
}

export function checkDatabase(databaseId: string, csrfToken: string): Promise<DatabaseStatus> {
  return databaseMutation(
    `/api/control/v1/databases/${encodeURIComponent(databaseId)}/check`,
    "POST",
    csrfToken,
  );
}

export function deleteDatabase(databaseId: string, csrfToken: string): Promise<void> {
  return databaseMutation(
    `/api/control/v1/databases/${encodeURIComponent(databaseId)}`,
    "DELETE",
    csrfToken,
  );
}

export function revealDatabaseCredential(
  databaseId: string,
  csrfToken: string,
): Promise<{ credential: string }> {
  return databaseMutation(
    `/api/control/v1/databases/${encodeURIComponent(databaseId)}/reveal`,
    "POST",
    csrfToken,
  );
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

export async function startJob(
  jobId: JobId,
  options: Record<string, string | number | boolean>,
  csrfToken: string,
): Promise<JobRecord> {
  const response = await fetch("/api/control/v1/jobs/start", {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-CSRF-Token": csrfToken,
    },
    credentials: "same-origin",
    cache: "no-store",
    body: JSON.stringify({ job_id: jobId, options }),
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) throw new ApiRequestError(response.status);
  return (await response.json()) as JobRecord;
}

export async function cancelJob(jobId: string, csrfToken: string): Promise<{ cancel_requested: boolean; job: JobRecord }> {
  const response = await fetch(`/api/control/v1/jobs/${encodeURIComponent(jobId)}/cancel`, {
    method: "POST",
    headers: { Accept: "application/json", "X-CSRF-Token": csrfToken },
    credentials: "same-origin",
    cache: "no-store",
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) throw new ApiRequestError(response.status);
  return (await response.json()) as { cancel_requested: boolean; job: JobRecord };
}

export function getJobStatus(jobId: string): Promise<JobRecord> {
  return getJson<JobRecord>(`/api/control/v1/jobs/${encodeURIComponent(jobId)}`);
}

export function getJobHistory(limit = 50, offset = 0): Promise<JobHistoryResponse> {
  const query = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  return getJson<JobHistoryResponse>(`/api/control/v1/jobs/history?${query.toString()}`);
}

export function compareJobs(leftId: string, rightId: string): Promise<JobComparison> {
  const query = new URLSearchParams({ left_id: leftId, right_id: rightId });
  return getJson<JobComparison>(`/api/control/v1/jobs/compare?${query.toString()}`);
}

export function previewJobCleanup(): Promise<JobCleanupPreview> {
  return getJson<JobCleanupPreview>("/api/control/v1/jobs/cleanup/preview");
}

export async function cleanupJobHistory(csrfToken: string): Promise<{ deleted_jobs: number; deleted_artifacts: number }> {
  const response = await fetch("/api/control/v1/jobs/cleanup", {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-CSRF-Token": csrfToken,
    },
    credentials: "same-origin",
    cache: "no-store",
    body: JSON.stringify({ confirm: true }),
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) throw new ApiRequestError(response.status);
  return (await response.json()) as { deleted_jobs: number; deleted_artifacts: number };
}

export function getJobArtifact(jobId: string): Promise<{ job_id: string; records: Record<string, unknown>[] }> {
  return getJson<{ job_id: string; records: Record<string, unknown>[] }>(
    `/api/control/v1/jobs/${encodeURIComponent(jobId)}/artifact`,
  );
}
