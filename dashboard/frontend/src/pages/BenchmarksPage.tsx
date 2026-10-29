import { useCallback, useEffect, useMemo, useState } from "react";

import {
  ApiRequestError,
  cancelJob,
  cleanupJobHistory,
  compareJobs,
  getJobArtifact,
  getJobHistory,
  previewJobCleanup,
  startJob,
} from "../api";
import type { JobComparison, JobId, JobRecord } from "../api";

const ACTIVE_STATES = new Set(["queued", "running"]);

function summaryText(record: JobRecord, field: string): string {
  const value = record.summary[field];
  return value === null || value === undefined ? "Unavailable" : String(value);
}

function differenceLabel(field: string): string {
  const labels: Record<string, string> = {
    profile_id: "Profile ID",
    model_revision: "Model revision",
    engine: "Engine",
    engine_image_digest: "Engine image",
    context_length: "Context length",
    max_num_seqs: "Max sequences",
    "host.platform": "Host platform",
    "host.architecture": "Host architecture",
    "hardware.gpu_model": "GPU model",
    script_version: "Job script version",
    kind: "Job type",
  };
  return labels[field] ?? field;
}

export function BenchmarksPage({ csrfToken }: { csrfToken: string }) {
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [jobId, setJobId] = useState<JobId>("server-readiness");
  const [profileId, setProfileId] = useState("baseline-4k-2");
  const [caseName, setCaseName] = useState("all");
  const [concurrency, setConcurrency] = useState(1);
  const [diagnostic, setDiagnostic] = useState(false);
  const [requests, setRequests] = useState(5);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [leftId, setLeftId] = useState("");
  const [rightId, setRightId] = useState("");
  const [comparison, setComparison] = useState<JobComparison | null>(null);
  const [cleanup, setCleanup] = useState<{ removable_jobs: number; removable_artifacts: number } | null>(null);
  const [artifact, setArtifact] = useState<Record<string, unknown>[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const result = await getJobHistory(50, 0);
      setJobs(result.jobs);
      setError(null);
      const active = result.jobs.find((item) => ACTIVE_STATES.has(item.state));
      setActiveJobId(active?.job_id ?? null);
      setLeftId((current) => current || result.jobs[0]?.job_id || "");
      setRightId((current) => current || result.jobs[1]?.job_id || "");
    } catch (cause) {
      setError(cause instanceof ApiRequestError && cause.status === 401
        ? "Your dashboard session expired. Sign in again to use jobs."
        : "Job history is unavailable.");
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    if (!activeJobId) return undefined;
    const timer = window.setInterval(() => void refresh(), 2000);
    return () => window.clearInterval(timer);
  }, [activeJobId, refresh]);

  const activeJob = useMemo(() => jobs.find((job) => job.job_id === activeJobId) ?? null, [activeJobId, jobs]);

  const runJob = async () => {
    setBusy(true);
    setError(null);
    setArtifact(null);
    const options: Record<string, string | number | boolean> = jobId === "api-smoke"
      ? { case: caseName, max_tokens: 180 }
      : jobId === "performance-benchmark"
        ? {
            profile_id: profileId,
            concurrency,
            diagnostic_concurrency: diagnostic,
            requests_per_concurrency: requests,
            warmup_per_case: 0,
          }
        : {};
    try {
      const record = await startJob(jobId, options, csrfToken);
      setActiveJobId(ACTIVE_STATES.has(record.state) ? record.job_id : null);
      await refresh();
    } catch (cause) {
      setError(cause instanceof ApiRequestError && cause.status === 409
        ? "The job was not accepted. Check project configuration, activity state, and history cleanup."
        : "The fixed dashboard job could not be started.");
    } finally {
      setBusy(false);
    }
  };

  const cancelActive = async () => {
    if (!activeJobId) return;
    setBusy(true);
    try {
      await cancelJob(activeJobId, csrfToken);
      await refresh();
    } catch {
      setError("The job cancellation request could not be completed.");
    } finally {
      setBusy(false);
    }
  };

  const compare = async () => {
    setBusy(true);
    setComparison(null);
    try {
      setComparison(await compareJobs(leftId, rightId));
    } catch {
      setError("These job results could not be compared.");
    } finally {
      setBusy(false);
    }
  };

  const viewArtifact = async (id: string) => {
    try {
      const result = await getJobArtifact(id);
      setArtifact(result.records);
    } catch {
      setError("The sanitized job result is unavailable.");
    }
  };

  const previewCleanup = async () => {
    try {
      setCleanup(await previewJobCleanup());
    } catch {
      setError("Job cleanup preview is unavailable.");
    }
  };

  const confirmCleanup = async () => {
    setBusy(true);
    try {
      await cleanupJobHistory(csrfToken);
      setCleanup(null);
      setComparison(null);
      setArtifact(null);
      setLeftId("");
      setRightId("");
      await refresh();
    } catch {
      setError("Job history cleanup could not be completed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page-content jobs-content">
      <section className="intro-panel">
        <span className="eyebrow">FIXED LOCAL JOBS</span>
        <h2>Protocol checks and benchmarks</h2>
        <p>Only the versioned readiness, synthetic API smoke, and bounded performance jobs are available. Prompts and completions are never stored in dashboard history.</p>
      </section>
      <section className="panel job-launch-panel">
        <div className="panel-heading"><div><span className="eyebrow">JOB LAUNCHER</span><h2>Start a predefined job</h2></div><span className="readonly-tag">ONE ACTIVE JOB</span></div>
        <div className="job-form">
          <label>Job type
            <select value={jobId} onChange={(event) => setJobId(event.target.value as JobId)}>
              <option value="server-readiness">Server readiness</option>
              <option value="api-smoke">Synthetic API smoke</option>
              <option value="performance-benchmark">Performance benchmark</option>
            </select>
          </label>
          {jobId === "api-smoke" && <label>Fixed workload
            <select value={caseName} onChange={(event) => setCaseName(event.target.value)}>
              <option value="all">Both synthetic cases</option>
              <option value="history">History case</option>
              <option value="medical">Fictional medical case</option>
            </select>
          </label>}
          {jobId === "performance-benchmark" && <>
            <label>Inference profile
              <select value={profileId} onChange={(event) => setProfileId(event.target.value)}>
                <option value="baseline-4k-2">Baseline 4K / 2 sequences</option>
                <option value="baseline-8k-4">Baseline 8K / 4 sequences</option>
              </select>
            </label>
            <label>Concurrency
              <select value={concurrency} onChange={(event) => setConcurrency(Number(event.target.value))}>
                <option value={1}>1 (default)</option>
                <option value={2}>2</option>
                <option value={4}>4 (diagnostic)</option>
              </select>
            </label>
            <label>Requests
              <select value={requests} onChange={(event) => setRequests(Number(event.target.value))}>
                <option value={1}>1</option><option value={5}>5</option><option value={10}>10</option><option value={25}>25 (maximum)</option>
              </select>
            </label>
            {concurrency === 4 && <label className="job-check-label"><input type="checkbox" checked={diagnostic} onChange={(event) => setDiagnostic(event.target.checked)} />Confirm diagnostic concurrency</label>}
          </>}
          <button className="primary-button" disabled={busy || Boolean(activeJobId)} onClick={() => void runJob()} type="button">Run job</button>
        </div>
        {jobId === "api-smoke" && <p className="job-disclosure">This sends only the existing fixed synthetic cases to the local inference API. Request and response bodies are not kept.</p>}
        {error && <p className="operation-error" role="alert">{error}</p>}
        {activeJob && <div className="job-active-state" role="status"><strong>{activeJob.kind.replaceAll("-", " ")}: {activeJob.state}</strong>{activeJob.state === "running" && <button className="secondary-button" disabled={busy} onClick={() => void cancelActive()} type="button">Cancel job</button>}</div>}
      </section>

      <section className="panel job-history-panel">
        <div className="panel-heading"><div><span className="eyebrow">SANITIZED HISTORY</span><h2>Completed runs</h2></div><button className="secondary-button" onClick={() => void previewCleanup()} type="button">Preview cleanup</button></div>
        {jobs.length === 0 ? <p className="empty-state">No dashboard jobs have been recorded.</p> : <div className="job-history-list">
          {jobs.map((job) => <article className="job-history-row" key={job.job_id}>
            <div><strong>{job.kind.replaceAll("-", " ")}</strong><span>{job.created_at ?? "Time unavailable"} · {job.state}</span></div>
            <div className="job-history-summary"><span>Profile: {summaryText(job, "profile_id")}</span><span>GPU: {String((job.summary.hardware as Record<string, unknown> | undefined)?.gpu_model ?? "Unavailable")}</span><span>Requests: {summaryText(job, "request_count")}</span></div>
            {job.artifact_id && <button className="text-button" onClick={() => void viewArtifact(job.job_id)} type="button">View results</button>}
          </article>)}
        </div>}
        {cleanup && <div className="job-confirm-box" role="alertdialog" aria-labelledby="cleanup-title">
          <h3 id="cleanup-title">Review history cleanup</h3>
          <p>{cleanup.removable_jobs} completed job records and {cleanup.removable_artifacts} artifacts can be removed. This cannot be undone.</p>
          <div><button className="secondary-button" onClick={() => setCleanup(null)} type="button">Keep history</button><button className="primary-button" disabled={busy || cleanup.removable_jobs + cleanup.removable_artifacts === 0} onClick={() => void confirmCleanup()} type="button">Confirm cleanup</button></div>
        </div>}
        {artifact && <div className="job-artifact-view"><div className="panel-heading"><h3>Sanitized result records</h3><button className="text-button" onClick={() => setArtifact(null)} type="button">Close</button></div><pre>{JSON.stringify(artifact, null, 2)}</pre></div>}
      </section>

      {jobs.length >= 2 && <section className="panel job-compare-panel">
        <div className="panel-heading"><div><span className="eyebrow">RUN COMPARISON</span><h2>Compare compatible results</h2></div></div>
        <div className="job-compare-controls">
          <label>First run<select aria-label="First run" value={leftId} onChange={(event) => setLeftId(event.target.value)}>{jobs.map((job) => <option key={job.job_id} value={job.job_id}>{job.kind} · {job.job_id.slice(0, 8)}</option>)}</select></label>
          <label>Second run<select aria-label="Second run" value={rightId} onChange={(event) => setRightId(event.target.value)}>{jobs.map((job) => <option key={job.job_id} value={job.job_id}>{job.kind} · {job.job_id.slice(0, 8)}</option>)}</select></label>
          <button className="secondary-button" disabled={busy || !leftId || !rightId || leftId === rightId} onClick={() => void compare()} type="button">Compare runs</button>
        </div>
        {comparison && <div className="job-comparison-result" role="status">
          <strong>{comparison.comparable ? "Runs are comparable" : "Runs are not comparable"}</strong>
          {comparison.differences.length > 0 && <ul>{comparison.differences.map((difference) => <li key={difference.field}><span>{differenceLabel(difference.field)}:</span> {String(difference.left)} → {String(difference.right)}</li>)}</ul>}
          {comparison.comparable && <p>Request count: {summaryText(comparison.left, "request_count")} / {summaryText(comparison.right, "request_count")}</p>}
        </div>}
      </section>}
    </div>
  );
}
