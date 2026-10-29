import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiRequestError, getProfiles, getStatus } from "./api";
import type { DashboardStatus, Profile, TelemetrySample } from "./api";
import { createAuthClient } from "./auth";
import type { AuthClient, AuthState, AuthenticatedState } from "./auth";
import { AuthPage } from "./pages/AuthPage";
import { BenchmarksPage } from "./pages/BenchmarksPage";
import { DatabasesPage } from "./pages/DatabasesPage";
import { LogsPage } from "./pages/LogsPage";
import { ModelsPage } from "./pages/ModelsPage";
import { OverviewPage } from "./pages/OverviewPage";
import { SettingsPage } from "./pages/SettingsPage";
import { startDashboardPolling } from "./polling";

type PageName = "Overview" | "Models" | "Benchmarks" | "Logs" | "Databases" | "Settings";

const SAMPLE_LIMIT = 120;
const VISIBLE_POLL_MS = 5000;
const HIDDEN_POLL_MS = 30000;
const defaultAuthClient = createAuthClient();

function nextPollDelay(): number {
  return document.visibilityState === "hidden" ? HIDDEN_POLL_MS : VISIBLE_POLL_MS;
}

function makeSample(status: DashboardStatus): TelemetrySample {
  const gpu = status.telemetry.gpu;
  const gpuMemoryPercent = gpu.memory_used_bytes !== null && gpu.memory_total_bytes
    ? (gpu.memory_used_bytes / gpu.memory_total_bytes) * 100
    : null;
  return {
    capturedAt: status.status_cache.observed_at ?? status.refreshed_at,
    cpuPercent: status.telemetry.cpu_percent,
    gpuPercent: gpu.utilization_percent,
    gpuMemoryPercent,
  };
}

function useDashboard(onSessionExpired: () => void) {
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
    const reportError = (cause: unknown) => {
      if (cause instanceof ApiRequestError && cause.status === 401) onSessionExpired();
      setError(cause instanceof Error ? cause.message : "Dashboard status unavailable");
    };
    const stopPolling = startDashboardPolling({
      refresh,
      onError: reportError,
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
      .catch((cause: unknown) => {
        if (cause instanceof ApiRequestError && cause.status === 401) onSessionExpired();
        if (!disposed) setError(cause instanceof Error ? cause.message : "Dashboard profile registry unavailable");
      });
    return () => {
      disposed = true;
      stopPolling();
    };
  }, [onSessionExpired, refresh]);

  return { status, profiles, samples, error };
}

function formatDate(value: string | null | undefined): string {
  if (!value) return "Unavailable";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "Unavailable"
    : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "medium" }).format(date);
}

function StateBadge({ state }: { state: string }) {
  const key = state.toLowerCase().replaceAll(" ", "-");
  return <span className={`state-badge state-${key}`}><span className="state-dot" />{state}</span>;
}

function Sidebar({ page, onNavigate, onLogout }: {
  page: PageName;
  onNavigate: (page: PageName) => void;
  onLogout: () => void;
}) {
  const pages: PageName[] = ["Overview", "Models", "Benchmarks", "Logs", "Databases", "Settings"];
  return (
    <aside className="sidebar">
      <div className="brand-lockup">
        <div className="brand-symbol" aria-hidden="true"><span /><span /><span /></div>
        <div><div className="brand-name">LOCAL AI</div><div className="brand-subtitle">API · LITE</div></div>
      </div>
      <div className="nav-label eyebrow">CONTROL ROOM</div>
      <nav aria-label="Dashboard pages" className="side-nav">
        {pages.map((item, index) => <button
          aria-current={page === item ? "page" : undefined}
          className={`nav-button ${page === item ? "nav-button-active" : ""}`}
          key={item}
          onClick={() => onNavigate(item)}
          type="button"
        ><span className="nav-number">0{index + 1}</span><span>{item}</span>{page === item && <span className="nav-indicator" aria-hidden="true" />}</button>)}
      </nav>
      <div className="sidebar-spacer" />
      <div className="sidebar-note"><span className="live-dot" /><div><span className="sidebar-note-title">LOCAL SESSION</span><span className="sidebar-note-copy">Administrator access</span></div></div>
      <button className="sidebar-logout" onClick={onLogout} type="button">Sign out</button>
      <div className="sidebar-version">LOCAL CONTROL PLANE <span>V0.1</span></div>
    </aside>
  );
}

function Topbar({ page, status }: { page: PageName; status: DashboardStatus | null }) {
  const heading = page === "Overview" ? "System overview" : page === "Models" ? "Model configuration" : page === "Benchmarks" ? "Benchmarks & jobs" : page === "Logs" ? "Service logs" : page === "Databases" ? "Database monitoring" : "Security settings";
  const freshness = status?.status_cache;
  const freshnessLabel = freshness?.stale
    ? freshness.refreshing ? "STALE · REFRESHING" : "STALE SNAPSHOT"
    : freshness?.refreshing ? "REFRESHING SNAPSHOT" : "CURRENT SNAPSHOT";
  return (
    <header className="topbar">
      <div><div className="breadcrumb eyebrow">LOCAL-AI-API-LITE <span>/</span> {page.toUpperCase()}</div><h1>{heading}</h1></div>
      <div className="topbar-status">
        <div className="refresh-label eyebrow">LAST STATUS OBSERVATION</div>
        <div className="refresh-value">{formatDate(freshness?.observed_at)}</div>
        <div className="refresh-state" role="status">{freshnessLabel}</div>
      </div>
    </header>
  );
}

function AuthenticatedDashboard({
  authClient,
  authState,
  onLogout,
  onPasswordChanged,
  onSessionExpired,
}: {
  authClient: AuthClient;
  authState: AuthenticatedState;
  onLogout: () => void;
  onPasswordChanged: () => void;
  onSessionExpired: () => void;
}) {
  const [page, setPage] = useState<PageName>("Overview");
  const { status, profiles, samples, error } = useDashboard(onSessionExpired);
  const statusText = useMemo(() => status?.inference.state ?? "Unknown", [status]);

  return (
    <div className="app-shell">
      <Sidebar page={page} onNavigate={setPage} onLogout={onLogout} />
      <main className="main-area">
        <Topbar page={page} status={status} />
        {error && <div className="connection-alert" role="alert"><span className="alert-symbol">!</span><div><strong>Controller connection issue</strong><span>{error}. The last successful snapshot remains on screen.</span></div></div>}
        {page !== "Settings" && <div className="page-state-line"><span className="eyebrow">INFERENCE STATE</span><StateBadge state={statusText} /><span className="controller-indicator"><span />CONTROLLER {status?.controller.state.toUpperCase() ?? "CONNECTING"}</span></div>}
        {page === "Overview" && <OverviewPage status={status} samples={samples} />}
        {page === "Models" && <ModelsPage status={status} profiles={profiles} csrfToken={authState.csrfToken} />}
        {page === "Benchmarks" && <BenchmarksPage csrfToken={authState.csrfToken} />}
        {page === "Logs" && <LogsPage />}
        {page === "Databases" && <DatabasesPage csrfToken={authState.csrfToken} />}
        {page === "Settings" && <SettingsPage
          authClient={authClient}
          csrfToken={authState.csrfToken}
          onPasswordChanged={onPasswordChanged}
        />}
        <footer className="app-footer"><span>LOCAL AI API · LITE</span><span>CPU-ONLY CONTROL PLANE <i /> PRIVATE NETWORK INFERENCE</span></footer>
      </main>
    </div>
  );
}

export function App({ authClient = defaultAuthClient }: { authClient?: AuthClient } = {}) {
  const [authState, setAuthState] = useState<AuthState | null>(null);
  const [authError, setAuthError] = useState<string | null>(null);
  const [authNotice, setAuthNotice] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void authClient.readState().then((state) => {
      if (!cancelled) {
        setAuthState(state);
        if (state.kind === "logged-out" && state.reauthenticate) {
          setAuthNotice("Sign in again to restore this browser session. Security tokens are kept in memory only.");
        }
      }
    }).catch(() => {
      if (!cancelled) {
        setAuthState({ kind: "logged-out" });
        setAuthError("The local controller could not be reached. Try again when it is running.");
      }
    });
    return () => { cancelled = true; };
  }, [authClient]);

  const handleSetup = async (token: string, password: string) => {
    setAuthError(null);
    try {
      await authClient.setup(token, password);
      setAuthState({ kind: "logged-out" });
      setAuthNotice("Administrator password set. Sign in to continue.");
    } catch (cause) {
      setAuthError(cause instanceof Error ? cause.message : "Administrator setup failed.");
    }
  };

  const handleLogin = async (password: string) => {
    setAuthError(null);
    setAuthNotice(null);
    try {
      setAuthState(await authClient.login(password));
    } catch (cause) {
      setAuthError(cause instanceof Error ? cause.message : "Sign in failed.");
    }
  };

  const handleRecovery = async (token: string, newPassword: string) => {
    setAuthError(null);
    try {
      await authClient.recovery(token, newPassword);
      setAuthState({ kind: "logged-out" });
      setAuthNotice("Password reset. Sign in with the new password.");
    } catch (cause) {
      setAuthError(cause instanceof Error ? cause.message : "Password recovery failed.");
    }
  };

  const handleLogout = useCallback(async () => {
    if (authState?.kind === "authenticated") {
      try {
        await authClient.logout(authState.csrfToken);
      } catch (cause) {
        setAuthError(cause instanceof Error ? cause.message : "Sign out request failed.");
      }
    }
    setAuthState({ kind: "logged-out" });
    setAuthNotice("Signed out.");
  }, [authClient, authState]);

  const expireSession = useCallback(() => {
    setAuthState({ kind: "logged-out" });
    setAuthNotice("Your session expired. Sign in again to continue.");
  }, []);

  if (authState === null) {
    return <main className="auth-shell"><div className="loading-state"><span className="loading-line" />Connecting to the local controller…</div></main>;
  }
  if (authState.kind !== "authenticated") {
    return <AuthPage
      mode={authState.kind}
      onSetup={handleSetup}
      onLogin={handleLogin}
      onRecovery={handleRecovery}
      error={authError}
      notice={authNotice}
    />;
  }
  return <AuthenticatedDashboard
    authClient={authClient}
    authState={authState}
    onLogout={() => void handleLogout()}
    onPasswordChanged={() => {
      setAuthState({ kind: "logged-out" });
      setAuthNotice("Password changed. Sign in again.");
    }}
    onSessionExpired={expireSession}
  />;
}

export default App;
