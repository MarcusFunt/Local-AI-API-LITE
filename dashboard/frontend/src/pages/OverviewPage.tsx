import type { DashboardStatus, RetrievalWorkerMetrics, RetrievalWorkerStatus, SpeechServiceStatus, TelemetrySample } from "../api";

interface OverviewPageProps {
  status: DashboardStatus | null;
  samples: TelemetrySample[];
}

function formatBytes(value: number | null): string {
  if (value === null || !Number.isFinite(value) || value < 0) return "Unavailable";
  if (value < 1024) return `${value} B`;
  const units = ["KiB", "MiB", "GiB", "TiB"];
  let size = value;
  let unit = -1;
  do {
    size /= 1024;
    unit += 1;
  } while (size >= 1024 && unit < units.length - 1);
  return `${size.toFixed(1)} ${units[unit]}`;
}

function formatDate(value: string | null): string {
  if (!value) return "Unavailable";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "Unavailable"
    : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "medium" }).format(date);
}

function formatUptime(value: number | null): string {
  if (value === null) return "Unavailable";
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  const seconds = value % 60;
  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m ${seconds}s`;
  return `${seconds}s`;
}

function formatSnapshotAge(value: string | null): string {
  if (!value) return "Unavailable";
  const captured = Date.parse(value);
  if (!Number.isFinite(captured)) return "Unavailable";
  const seconds = Math.max(0, Math.floor((Date.now() - captured) / 1000));
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  return `${Math.floor(seconds / 3600)}h`;
}

function formatLastInference(metrics: RetrievalWorkerMetrics | null): string {
  if (!metrics) return "Unavailable";
  if (metrics.last_successful_inference_at) return formatDate(metrics.last_successful_inference_at);
  return metrics.completed_total > 0 ? "Timestamp unavailable" : "Never";
}

function MetricCard({ label, value, detail, samples, color = "mint" }: {
  label: string;
  value: string;
  detail: string;
  samples?: Array<number | null>;
  color?: "mint" | "blue" | "amber";
}) {
  const points = (samples ?? []).filter((sample): sample is number => sample !== null && Number.isFinite(sample));
  const max = Math.max(100, ...points);
  return (
    <section className={`metric-card metric-card-${color}`} aria-label={label}>
      <div className="metric-heading"><span className="eyebrow">{label}</span><span className="metric-mark" aria-hidden="true" /></div>
      <strong className="metric-value">{value}</strong>
      <div className="metric-footer">
        <span>{detail}</span>
        {points.length > 1 && <div className="sparkline" aria-label={`${label} recent samples`} role="img">
          {(samples ?? []).slice(-24).map((sample, index) => <span
            aria-hidden="true"
            className={sample === null ? "sparkline-gap" : ""}
            key={`${index}-${sample ?? "empty"}`}
            style={{ height: sample === null ? "3px" : `${Math.max(4, (sample / max) * 24)}px` }}
          />)}
        </div>}
      </div>
    </section>
  );
}

function StateBadge({ state }: { state: string }) {
  const key = state.toLowerCase().replaceAll(" ", "-");
  return <span className={`state-badge state-${key}`}><span className="state-dot" />{state}</span>;
}

function RetrievalWorkerCard({ worker, snapshotAt, stale }: {
  worker: RetrievalWorkerStatus;
  snapshotAt: string | null;
  stale: boolean;
}) {
  const metrics = worker.metrics;
  const container = worker.container;
  const requestTotals = metrics
    ? `${metrics.accepted_total} accepted · ${metrics.completed_total} completed · ${metrics.failed_total} failed`
    : "Unavailable";
  return (
    <section className="panel retrieval-worker-card" aria-label={`${worker.alias} status`}>
      <div className="panel-heading">
        <div><span className="eyebrow">CPU RETRIEVAL WORKER</span><h3>{worker.alias}</h3></div>
        <StateBadge state={worker.state} />
      </div>
      <div className="detail-list retrieval-worker-details">
        <div><span>Model</span><strong>{worker.model_repository}</strong></div>
        <div><span>Served alias</span><strong>{worker.model_id ?? "Unavailable"}</strong></div>
        <div><span>Immutable revision</span><strong className="mono">{worker.model_revision ?? "Unavailable"}</strong></div>
        <div><span>Compose project</span><strong>{worker.project_name}</strong></div>
        <div><span>Image</span><strong className="mono">{worker.image_ref ?? "Unavailable"}</strong></div>
        <div><span>Container</span><strong>{container?.name ?? "Unavailable"}</strong></div>
        <div><span>Container state</span><strong>{container?.state ?? "Unavailable"}</strong></div>
        <div><span>Container health</span><strong>{container?.health ?? "Unavailable"}</strong></div>
        <div><span>Container CPU</span><strong>{container?.cpu_percent == null ? "Unavailable" : `${container.cpu_percent.toFixed(1)}%`}</strong></div>
        <div><span>Container RAM</span><strong>{container?.memory_used_bytes == null ? "Unavailable" : `${formatBytes(container.memory_used_bytes)} / ${formatBytes(container.memory_limit_bytes)}`}</strong></div>
        <div><span>Last successful readiness</span><strong>{formatDate(worker.last_successful_readiness_check_at)}</strong></div>
        <div><span>Last successful inference</span><strong>{formatLastInference(metrics)}</strong></div>
        <div><span>Snapshot age</span><strong>{formatSnapshotAge(snapshotAt)}{stale ? " · stale" : ""}</strong></div>
        <div><span>Request totals</span><strong>{requestTotals}{metrics ? ` · ${metrics.timed_out_total} timed out` : ""}</strong></div>
        <div><span>Queue</span><strong>{metrics ? `${metrics.active_requests} active · ${metrics.queued_requests} queued · ${metrics.max_outstanding} max` : "Unavailable"}</strong></div>
        <div><span>Worker metrics</span><strong>{worker.metrics_availability === "available" ? "Available" : "Unavailable"}</strong></div>
        {worker.last_error_category && <div><span>Last error category</span><strong>{worker.last_error_category}</strong></div>}
      </div>
    </section>
  );
}

function SpeechServiceCard({ service, snapshotAt, stale }: {
  service: SpeechServiceStatus;
  snapshotAt: string | null;
  stale: boolean;
}) {
  const metrics = service.metrics;
  const container = service.container;
  const requestTotals = metrics
    ? `${metrics.accepted_total} accepted · ${metrics.completed_total} completed · ${metrics.failed_total} failed · ${metrics.timed_out_total} timed out`
    : "Unavailable";
  return (
    <section className="panel retrieval-worker-card" aria-label={`${service.alias} status`}>
      <div className="panel-heading">
        <div><span className="eyebrow">CPU SPEECH SERVICE</span><h3>{service.alias}</h3></div>
        <StateBadge state={service.state} />
      </div>
      <div className="detail-list retrieval-worker-details">
        <div><span>Model</span><strong>{service.model_repository}</strong></div>
        <div><span>Served model</span><strong>{service.model_id ?? "Unavailable"}</strong></div>
        <div><span>Immutable revision</span><strong className="mono">{service.model_revision ?? "Unavailable"}</strong></div>
        <div><span>Expected revision</span><strong className="mono">{service.expected_model_revision}</strong></div>
        <div><span>Container</span><strong>{container?.name ?? "Unavailable"}</strong></div>
        <div><span>Container state</span><strong>{container?.state ?? "Unavailable"}</strong></div>
        <div><span>Container health</span><strong>{container?.health ?? "Unavailable"}</strong></div>
        <div><span>Container CPU</span><strong>{container?.cpu_percent == null ? "Unavailable" : `${container.cpu_percent.toFixed(1)}%`}</strong></div>
        <div><span>Container RAM</span><strong>{container?.memory_used_bytes == null ? "Unavailable" : `${formatBytes(container.memory_used_bytes)} / ${formatBytes(container.memory_limit_bytes)}`}</strong></div>
        <div><span>Last successful readiness</span><strong>{formatDate(service.last_successful_readiness_check_at)}</strong></div>
        <div><span>Last successful inference</span><strong>{formatLastInference(metrics)}</strong></div>
        <div><span>Snapshot age</span><strong>{formatSnapshotAge(snapshotAt)}{stale ? " · stale" : ""}</strong></div>
        <div><span>Request totals</span><strong>{requestTotals}</strong></div>
        <div><span>Queue</span><strong>{metrics ? `${metrics.active_requests} active · ${metrics.queued_requests} queued · ${metrics.max_outstanding} max` : "Unavailable"}</strong></div>
        {service.last_error_category && <div><span>Last error category</span><strong>{service.last_error_category}</strong></div>}
      </div>
    </section>
  );
}

export function OverviewPage({ status, samples }: OverviewPageProps) {
  if (!status) return <div className="loading-state"><span className="loading-line" />Waiting for the local controller…</div>;

  const { inference, telemetry, active_profile: profile } = status;
  const gpu = telemetry.gpu;
  const gpuMemoryPercent = gpu.memory_used_bytes !== null && gpu.memory_total_bytes
    ? Math.round((gpu.memory_used_bytes / gpu.memory_total_bytes) * 100)
    : null;

  return (
    <div className="page-content">
      <section className="hero-panel">
        <div className="hero-copy">
          <span className="eyebrow hero-kicker"><span className="live-dot" /> LIVE SERVICE STATUS</span>
          <h2>{inference.state === "Ready" ? "Inference is ready." : `Inference is ${inference.state.toLowerCase()}.`}</h2>
          <p>{inference.configuration?.model_id ?? "No model configuration is available from Docker."}</p>
        </div>
        <div className="hero-state"><span className="eyebrow">INFERENCE</span><StateBadge state={inference.state} /><span className="hero-alias">{inference.configuration?.model_alias ?? "Alias unavailable"}</span></div>
        <div className="hero-orbit hero-orbit-one" aria-hidden="true" /><div className="hero-orbit hero-orbit-two" aria-hidden="true" />
      </section>
      {inference.last_failure_reason && <div className="alert-row" role="status"><span className="alert-symbol">!</span><div><strong>Service attention</strong><span>{inference.last_failure_reason}</span></div></div>}
      <div className="section-heading"><div><span className="eyebrow">INDEPENDENT CPU SERVICES</span><h2>Retrieval workers</h2></div><span className="section-note">Readiness and identity are checked separately from narrator status</span></div>
      <div className="retrieval-worker-grid">
        {(status.model_services ?? []).map((worker) => <RetrievalWorkerCard
          key={worker.service}
          worker={worker}
          snapshotAt={worker.observed_at}
          stale={status.status_cache.stale}
        />)}
        {(status.model_services ?? []).length === 0 && <div className="empty-state panel">Retrieval worker status is unavailable.</div>}
      </div>
      <div className="section-heading"><div><span className="eyebrow">INDEPENDENT CPU SERVICES</span><h2>Speech workers</h2></div><span className="section-note">Container health, pinned voice/model identity, queue and resource snapshots</span></div>
      <div className="retrieval-worker-grid">
        {(status.speech_services ?? []).map((service) => <SpeechServiceCard
          key={service.service}
          service={service}
          snapshotAt={service.observed_at}
          stale={status.status_cache.stale}
        />)}
        {(status.speech_services ?? []).length === 0 && <div className="empty-state panel">Speech worker status is unavailable.</div>}
      </div>
      <div className="section-heading"><div><span className="eyebrow">HOST TELEMETRY</span><h2>Resource snapshot</h2></div><span className="section-note">Metrics refresh while this page is visible</span></div>
      <div className="metrics-grid">
        <MetricCard color="mint" detail="Host processor" label="CPU UTILIZATION" samples={samples.map((sample) => sample.cpuPercent)} value={telemetry.cpu_percent === null ? "Unavailable" : `${telemetry.cpu_percent.toFixed(1)}%`} />
        <MetricCard color="blue" detail="System memory" label="HOST RAM" value={telemetry.ram_used_bytes === null || telemetry.ram_total_bytes === null ? "Unavailable" : `${formatBytes(telemetry.ram_used_bytes)} / ${formatBytes(telemetry.ram_total_bytes)}`} />
        <MetricCard color="amber" detail="Device-wide · all processes" label="GPU UTILIZATION" samples={samples.map((sample) => sample.gpuPercent)} value={gpu.utilization_percent === null ? "Unavailable" : `${gpu.utilization_percent.toFixed(0)}%`} />
        <MetricCard color="blue" detail={gpuMemoryPercent === null ? "Device-wide VRAM" : `Device-wide VRAM · ${gpuMemoryPercent}% used`} label="GPU MEMORY" samples={samples.map((sample) => sample.gpuMemoryPercent)} value={gpu.memory_used_bytes === null || gpu.memory_total_bytes === null ? "Unavailable" : `${formatBytes(gpu.memory_used_bytes)} / ${formatBytes(gpu.memory_total_bytes)}`} />
      </div>
      <div className="lower-grid">
        <section className="panel service-panel">
          <div className="panel-heading"><div><span className="eyebrow">INFERENCE CONTAINER</span><h2>Service details</h2></div><StateBadge state={inference.container?.health ?? "Unavailable"} /></div>
          <div className="detail-list">
            <div><span>Container</span><strong>{inference.container?.name ?? "Not found"}</strong></div>
            <div><span>Container state</span><strong>{inference.container?.state ?? "Unavailable"}</strong></div>
            <div><span>Active profile</span><strong>{profile?.name ?? "Unrecognized / unavailable"}</strong></div>
            <div><span>Model revision</span><strong className="mono">{inference.configuration?.model_revision ?? "Unavailable"}</strong></div>
            <div><span>Uptime</span><strong>{formatUptime(inference.container?.uptime_seconds ?? null)}</strong></div>
            <div><span>Container CPU</span><strong>{inference.container?.cpu_percent == null ? "Unavailable" : `${inference.container.cpu_percent.toFixed(1)}%`}</strong></div>
            <div><span>Container RAM</span><strong>{inference.container?.memory_used_bytes == null ? "Unavailable" : `${formatBytes(inference.container.memory_used_bytes)} / ${formatBytes(inference.container.memory_limit_bytes)}`}</strong></div>
            <div><span>Most recent healthy probe</span><strong>{formatDate(inference.last_successful_health_probe_at)}</strong></div>
            <div><span>Status snapshot age</span><strong>{formatSnapshotAge(status.status_cache.observed_at)}{status.status_cache.stale ? " · stale" : ""}</strong></div>
            <div><span>Cache disk free</span><strong>{formatBytes(telemetry.cache_disk_free_bytes)}</strong></div>
          </div>
        </section>
        <section className="panel model-panel">
          <div className="panel-heading"><div><span className="eyebrow">ACTIVE MODEL</span><h2>Serving contract</h2></div><span className="readonly-tag">READ ONLY</span></div>
          <div className="model-alias-card"><span className="eyebrow">STABLE API ALIAS</span><strong>{inference.configuration?.model_alias ?? "Unavailable"}</strong><span>Trusted clients stay on the private Docker network.</span></div>
          <div className="detail-list compact-list">
            <div><span>Engine</span><strong>{inference.configuration?.engine ?? "Unavailable"}</strong></div>
            <div><span>Context</span><strong>{inference.configuration?.max_model_len ? `${inference.configuration.max_model_len.toLocaleString()} tokens` : "Unavailable"}</strong></div>
            <div><span>Sequences</span><strong>{inference.configuration?.max_num_seqs ?? "Unavailable"}</strong></div>
            <div><span>GPU memory target</span><strong>{inference.configuration?.gpu_memory_utilization == null ? "Unavailable" : `${Math.round(inference.configuration.gpu_memory_utilization * 100)}%`}</strong></div>
          </div>
        </section>
      </div>
      <p className="privacy-note"><span className="privacy-mark" aria-hidden="true">⌁</span> This dashboard reads container health and host metrics. It does not collect prompts, responses or credentials.</p>
    </div>
  );
}
