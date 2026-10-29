import { useCallback, useEffect, useMemo, useState } from "react";

import { getProfiles, getStatus } from "./api";
import type { DashboardStatus, Profile, TelemetrySample } from "./api";
import { startDashboardPolling } from "./polling";

type PageName = "Overview" | "Models";

const SAMPLE_LIMIT = 120;
const VISIBLE_POLL_MS = 5000;
const HIDDEN_POLL_MS = 30000;

function nextPollDelay(): number {
  return document.visibilityState === "hidden" ? HIDDEN_POLL_MS : VISIBLE_POLL_MS;
}

function makeSample(status: DashboardStatus): TelemetrySample {
  const gpu = status.telemetry.gpu;
  const gpuMemoryPercent =
    gpu.memory_used_bytes !== null && gpu.memory_total_bytes
      ? (gpu.memory_used_bytes / gpu.memory_total_bytes) * 100
      : null;
  return {
    capturedAt: status.refreshed_at,
    cpuPercent: status.telemetry.cpu_percent,
    gpuPercent: gpu.utilization_percent,
    gpuMemoryPercent,
  };
}

function useDashboard() {
  const [status, setStatus] = useState<DashboardStatus | null>(null);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [samples, setSamples] = useState<TelemetrySample[]>([]);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const nextStatus = await getStatus();
    setStatus(nextStatus);
    setSamples((previous) => [...previous, makeSample(nextStatus)].slice(-SAMPLE_LIMIT));
    setError(null);
    return nextStatus;
  }, []);

  useEffect(() => {
    let disposed = false;
    const stopPolling = startDashboardPolling({
      refresh,
      onError: (cause) =>
        setError(cause instanceof Error ? cause.message : "Dashboard status unavailable"),
      getVisibilityState: () => document.visibilityState,
      subscribeVisibility: (listener) => {
        document.addEventListener("visibilitychange", listener);
        return () => document.removeEventListener("visibilitychange", listener);
      },
      schedule: (callback, delay) => window.setTimeout(callback, delay),
      cancel: (timerId) => window.clearTimeout(timerId),
      nextDelay: nextPollDelay,
      hiddenDelay: HIDDEN_POLL_MS,
    });
    void getProfiles()
      .then((result) => {
        if (!disposed) setProfiles(result.profiles);
      })
      .catch(() => {
        // Status remains useful if only the read-only profile registry is unavailable.
      });
    return () => {
      disposed = true;
      stopPolling();
    };
  }, [refresh]);

  return { status, profiles, samples, error };
}

function formatBytes(value: number | null, decimals = 1): string {
  if (value === null || !Number.isFinite(value) || value < 0) return "Unavailable";
  if (value < 1024) return `${value} B`;
  const units = ["KiB", "MiB", "GiB", "TiB"];
  let size = value;
  let unit = -1;
  do {
    size /= 1024;
    unit += 1;
  } while (size >= 1024 && unit < units.length - 1);
  return `${size.toFixed(decimals)} ${units[unit]}`;
}

function formatDate(value: string | null | undefined): string {
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

function MetricCard({
  label,
  value,
  detail,
  samples,
  color = "mint",
}: {
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
      <div className="metric-heading">
        <span className="eyebrow">{label}</span>
        <span className="metric-mark" aria-hidden="true" />
      </div>
      <strong className="metric-value">{value}</strong>
      <div className="metric-footer">
        <span>{detail}</span>
        {points.length > 1 && (
          <div className="sparkline" aria-label={`${label} recent samples`} role="img">
            {(samples ?? []).slice(-24).map((sample, index) => (
              <span
                aria-hidden="true"
                className={sample === null ? "sparkline-gap" : ""}
                key={`${index}-${sample ?? "empty"}`}
                style={{ height: sample === null ? "3px" : `${Math.max(4, (sample / max) * 24)}px` }}
              />
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

function StateBadge({ state }: { state: string }) {
  const key = state.toLowerCase().replaceAll(" ", "-");
  return <span className={`state-badge state-${key}`}><span className="state-dot" />{state}</span>;
}

function Sidebar({ page, onNavigate }: { page: PageName; onNavigate: (page: PageName) => void }) {
  const pages: PageName[] = ["Overview", "Models"];
  return (
    <aside className="sidebar">
      <div className="brand-lockup">
        <div className="brand-symbol" aria-hidden="true"><span /><span /><span /></div>
        <div>
          <div className="brand-name">LOCAL AI</div>
          <div className="brand-subtitle">API · LITE</div>
        </div>
      </div>
      <div className="nav-label eyebrow">CONTROL ROOM</div>
      <nav aria-label="Dashboard pages" className="side-nav">
        {pages.map((item, index) => (
          <button
            aria-current={page === item ? "page" : undefined}
            className={`nav-button ${page === item ? "nav-button-active" : ""}`}
            key={item}
            onClick={() => onNavigate(item)}
            type="button"
          >
            <span className="nav-number">0{index + 1}</span>
            <span>{item}</span>
            {page === item && <span className="nav-indicator" aria-hidden="true" />}
          </button>
        ))}
      </nav>
      <div className="sidebar-spacer" />
      <div className="sidebar-note">
        <span className="live-dot" />
        <div>
          <span className="sidebar-note-title">READ ONLY</span>
          <span className="sidebar-note-copy">Monitoring view · Phase 1</span>
        </div>
      </div>
      <div className="sidebar-version">LOCAL CONTROL PLANE <span>V0.1</span></div>
    </aside>
  );
}

function Topbar({ page, status }: { page: PageName; status: DashboardStatus | null }) {
  return (
    <header className="topbar">
      <div>
        <div className="breadcrumb eyebrow">LOCAL-AI-API-LITE <span>/</span> {page.toUpperCase()}</div>
        <h1>{page === "Overview" ? "System overview" : "Model configuration"}</h1>
      </div>
      <div className="topbar-status">
        <div className="refresh-label eyebrow">LAST REFRESH</div>
        <div className="refresh-value">{formatDate(status?.refreshed_at)}</div>
      </div>
    </header>
  );
}

function Overview({ status, samples }: { status: DashboardStatus | null; samples: TelemetrySample[] }) {
  if (!status) {
    return <div className="loading-state"><span className="loading-line" />Waiting for the local controller…</div>;
  }

  const { inference, telemetry, active_profile: profile } = status;
  const gpu = telemetry.gpu;
  const ramPercent =
    telemetry.ram_used_bytes !== null && telemetry.ram_total_bytes
      ? Math.round((telemetry.ram_used_bytes / telemetry.ram_total_bytes) * 100)
      : null;
  const gpuMemoryPercent =
    gpu.memory_used_bytes !== null && gpu.memory_total_bytes
      ? Math.round((gpu.memory_used_bytes / gpu.memory_total_bytes) * 100)
      : null;

  return (
    <div className="page-content">
      <section className="hero-panel">
        <div className="hero-copy">
          <span className="eyebrow hero-kicker"><span className="live-dot" /> LIVE SERVICE STATUS</span>
          <h2>{inference.state === "Ready" ? "Inference is ready." : `Inference is ${inference.state.toLowerCase()}.`}</h2>
          <p>
            {inference.configuration?.model_id ?? "No model configuration is available from Docker."}
          </p>
        </div>
        <div className="hero-state">
          <span className="eyebrow">INFERENCE</span>
          <StateBadge state={inference.state} />
          <span className="hero-alias">{inference.configuration?.model_alias ?? "Alias unavailable"}</span>
        </div>
        <div className="hero-orbit hero-orbit-one" aria-hidden="true" />
        <div className="hero-orbit hero-orbit-two" aria-hidden="true" />
      </section>

      {inference.last_failure_reason && (
        <div className="alert-row" role="status">
          <span className="alert-symbol">!</span>
          <div><strong>Service attention</strong><span>{inference.last_failure_reason}</span></div>
        </div>
      )}

      <div className="section-heading">
        <div><span className="eyebrow">HOST TELEMETRY</span><h2>Resource snapshot</h2></div>
        <span className="section-note">Metrics refresh while this page is visible</span>
      </div>
      <div className="metrics-grid">
        <MetricCard
          color="mint"
          detail="Host processor"
          label="CPU UTILIZATION"
          samples={samples.map((sample) => sample.cpuPercent)}
          value={telemetry.cpu_percent === null ? "Unavailable" : `${telemetry.cpu_percent.toFixed(1)}%`}
        />
        <MetricCard
          color="blue"
          detail="System memory"
          label="HOST RAM"
          value={
            telemetry.ram_used_bytes === null || telemetry.ram_total_bytes === null
              ? "Unavailable"
              : `${formatBytes(telemetry.ram_used_bytes)} / ${formatBytes(telemetry.ram_total_bytes)}`
          }
        />
        <MetricCard
          color="amber"
          detail="Device-wide · all processes"
          label="GPU UTILIZATION"
          samples={samples.map((sample) => sample.gpuPercent)}
          value={gpu.utilization_percent === null ? "Unavailable" : `${gpu.utilization_percent.toFixed(0)}%`}
        />
        <MetricCard
          color="blue"
          detail={gpuMemoryPercent === null ? "Device-wide VRAM" : `Device-wide VRAM · ${gpuMemoryPercent}% used`}
          label="GPU MEMORY"
          samples={samples.map((sample) => sample.gpuMemoryPercent)}
          value={
            gpu.memory_used_bytes === null || gpu.memory_total_bytes === null
              ? "Unavailable"
              : `${formatBytes(gpu.memory_used_bytes)} / ${formatBytes(gpu.memory_total_bytes)}`
          }
        />
      </div>

      <div className="lower-grid">
        <section className="panel service-panel">
          <div className="panel-heading"><div><span className="eyebrow">INFERENCE CONTAINER</span><h2>Service details</h2></div><StateBadge state={inference.container?.health ?? "Unavailable"} /></div>
          <div className="detail-list">
            <div><span>Container</span><strong>{inference.container?.name ?? "Not found"}</strong></div>
            <div><span>Active profile</span><strong>{profile?.name ?? "Unrecognized / unavailable"}</strong></div>
            <div><span>Uptime</span><strong>{formatUptime(inference.container?.uptime_seconds ?? null)}</strong></div>
            <div><span>Container CPU</span><strong>{inference.container?.cpu_percent === null || inference.container?.cpu_percent === undefined ? "Unavailable" : `${inference.container.cpu_percent.toFixed(1)}%`}</strong></div>
            <div><span>Container RAM</span><strong>
              {inference.container?.memory_used_bytes == null
                ? "Unavailable"
                : `${formatBytes(inference.container.memory_used_bytes)} / ${formatBytes(inference.container.memory_limit_bytes)}`}
            </strong></div>
            <div><span>Most recent healthy probe</span><strong>{formatDate(inference.last_successful_health_probe_at)}</strong></div>
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

function Models({ status, profiles }: { status: DashboardStatus | null; profiles: Profile[] }) {
  const activeProfileId = status?.active_profile?.id;
  const configuration = status?.inference.configuration;
  return (
    <div className="page-content models-content">
      <section className="intro-panel">
        <span className="eyebrow">PINNED CONFIGURATIONS</span>
        <h2>Validated on this host</h2>
        <p>These profiles are displayed for reference. Profile changes are not enabled in this read-only phase.</p>
      </section>

      <section className="observed-config panel">
        <div className="panel-heading"><div><span className="eyebrow">OBSERVED AT RUNTIME</span><h2>Current model configuration</h2></div><StateBadge state={status?.inference.state ?? "Unknown"} /></div>
        {configuration ? (
          <div className="observed-grid">
            <div className="observed-model"><span className="eyebrow">MODEL IDENTIFIER</span><strong>{configuration.model_id}</strong></div>
            <div><span className="eyebrow">REVISION</span><strong className="mono">{configuration.model_revision ?? "Unavailable"}</strong></div>
            <div><span className="eyebrow">SERVED ALIAS</span><strong>{configuration.model_alias ?? "Unavailable"}</strong></div>
            <div><span className="eyebrow">REASONING PARSER</span><strong>{configuration.reasoning_parser ?? "Unavailable"}</strong></div>
            <div><span className="eyebrow">PROFILE MATCH</span><strong>{status?.active_profile?.name ?? "No validated match"}</strong></div>
            <div className="observed-image"><span className="eyebrow">PINNED ENGINE IMAGE</span><strong className="mono">{configuration.image_ref ?? "Unavailable"}</strong></div>
          </div>
        ) : (
          <p className="empty-state">No inference container configuration is available. The controller can still report host telemetry while inference is stopped.</p>
        )}
      </section>

      <div className="section-heading profile-heading">
        <div><span className="eyebrow">PROFILE REGISTRY</span><h2>Host-tested options</h2></div>
        <span className="section-note">Stable API alias: ddu-narrator</span>
      </div>
      <div className="profile-grid">
        {profiles.map((profile, index) => (
          <ProfileCard key={profile.id} profile={profile} active={activeProfileId === profile.id} index={index} />
        ))}
        {profiles.length === 0 && <div className="empty-state panel">The versioned profile registry is unavailable.</div>}
      </div>
      <div className="profile-boundary"><span className="boundary-icon">i</span><span>Only the original pinned model and host-tested settings appear here. Applying profiles is intentionally unavailable in Phase 1.</span></div>
    </div>
  );
}

function ProfileCard({ profile, active, index }: { profile: Profile; active: boolean; index: number }) {
  return (
    <article className={`profile-card ${active ? "profile-card-active" : ""}`}>
      <div className="profile-card-top">
        <div className={`profile-index profile-index-${index}`}>0{index + 1}</div>
        <div className="profile-tags"><span className="validated-tag"><span />VALIDATED</span>{active && <span className="active-tag">ACTIVE</span>}</div>
      </div>
      <h3>{profile.name}</h3>
      <p>{profile.description}</p>
      <div className="profile-model">{profile.model_id}</div>
      <div className="profile-specs">
        <div><span>CONTEXT</span><strong>{profile.max_model_len.toLocaleString()}</strong></div>
        <div><span>MAX SEQUENCES</span><strong>{profile.max_num_seqs}</strong></div>
        <div><span>GPU MEMORY TARGET</span><strong>{Math.round(profile.gpu_memory_utilization * 100)}%</strong></div>
      </div>
      <div className="profile-revision"><span>REASONING PARSER</span><code>{profile.reasoning_parser}</code></div>
      <div className="profile-revision"><span>REVISION</span><code>{profile.model_revision.slice(0, 12)}…</code></div>
      <div className="profile-image"><span>PINNED ENGINE IMAGE</span><code>{profile.image_ref.split("@")[1]?.slice(0, 20) ?? profile.image_ref}</code></div>
    </article>
  );
}

function App() {
  const [page, setPage] = useState<PageName>("Overview");
  const { status, profiles, samples, error } = useDashboard();
  const statusText = useMemo(() => status?.inference.state ?? "Unknown", [status]);

  return (
    <div className="app-shell">
      <Sidebar page={page} onNavigate={setPage} />
      <main className="main-area">
        <Topbar page={page} status={status} />
        {error && <div className="connection-alert" role="alert"><span className="alert-symbol">!</span><div><strong>Controller connection issue</strong><span>{error}. The last successful snapshot remains on screen.</span></div></div>}
        <div className="page-state-line"><span className="eyebrow">INFERENCE STATE</span><StateBadge state={statusText} /><span className="controller-indicator"><span />CONTROLLER {status?.controller.state.toUpperCase() ?? "CONNECTING"}</span></div>
        {page === "Overview" ? <Overview status={status} samples={samples} /> : <Models status={status} profiles={profiles} />}
        <footer className="app-footer"><span>LOCAL AI API · LITE</span><span>CPU-ONLY CONTROL PLANE <i /> PRIVATE NETWORK INFERENCE</span></footer>
      </main>
    </div>
  );
}

export default App;
