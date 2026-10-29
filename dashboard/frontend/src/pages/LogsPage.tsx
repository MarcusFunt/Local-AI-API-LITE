import { useCallback, useEffect, useState } from "react";

import { getLogs } from "../api";
import type { LogsResponse } from "../api";

export function LogsPage() {
  const [logs, setLogs] = useState<LogsResponse | null>(null);
  const [tail, setTail] = useState(100);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setLogs(await getLogs(tail));
    } catch {
      setError("Logs are currently unavailable. Try again in a moment.");
    } finally {
      setLoading(false);
    }
  }, [tail]);

  useEffect(() => { void refresh(); }, [refresh]);

  const exportLogs = () => {
    if (!logs || logs.availability !== "available") return;
    const content = `${logs.lines.join("\n")}\n`;
    const url = URL.createObjectURL(new Blob([content], { type: "text/plain;charset=utf-8" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "local-ai-api-lite-logs.txt";
    anchor.click();
    URL.revokeObjectURL(url);
  };

  return (
    <section className="page-content logs-page" aria-label="Container logs">
      <div className="section-heading">
        <div><div className="eyebrow">READ ONLY · REDACTED</div><h2>Inference service logs</h2></div>
        <div className="logs-actions">
          <label className="logs-tail-label" htmlFor="logs-tail">Recent lines</label>
          <select id="logs-tail" value={tail} onChange={(event) => setTail(Number(event.target.value))}>
            <option value={50}>50</option><option value={100}>100</option><option value={200}>200</option>
          </select>
          <button className="secondary-button" disabled={loading} onClick={() => void refresh()} type="button">
            {loading ? "Refreshing…" : "Refresh"}
          </button>
          <button className="secondary-button" disabled={!logs || logs.availability !== "available"} onClick={exportLogs} type="button">
            Export sanitized logs
          </button>
        </div>
      </div>
      <p className="logs-disclosure">Only a bounded tail from the fixed inference service is shown. Credentials, filesystem paths, and content-bearing log output are filtered before they reach this page or an export.</p>
      {error && <div className="connection-alert" role="alert">{error}</div>}
      {logs?.availability === "unavailable" && <div className="empty-state" role="status">Logs unavailable · {logs.error_category ?? "query_failed"}</div>}
      {logs?.availability === "available" && logs.lines.length === 0 && <div className="empty-state">No inference service logs are available.</div>}
      <pre className="logs-output" aria-label="Sanitized service logs">{logs?.lines.join("\n") ?? (loading ? "Loading logs…" : "")}</pre>
    </section>
  );
}
