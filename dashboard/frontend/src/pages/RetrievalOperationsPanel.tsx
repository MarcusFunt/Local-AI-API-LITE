import { useCallback, useEffect, useState } from "react";

import {
  ApiRequestError,
  getRetrievalOperationState,
  operateRetrievalService,
} from "../api";
import type {
  RetrievalLifecycleAction,
  RetrievalOperationRecord,
  RetrievalOperationStateSnapshot,
  RetrievalServiceName,
  RetrievalWorkerStatus,
} from "../api";

type Confirmation = { service: RetrievalServiceName; action: RetrievalLifecycleAction } | null;

const SERVICES: Array<{ id: RetrievalServiceName; alias: string; model: string }> = [
  { id: "embed", alias: "ddu-embed", model: "Qwen/Qwen3-Embedding-0.6B" },
  { id: "rerank", alias: "ddu-rerank", model: "Alibaba-NLP/gte-multilingual-reranker-base" },
];

function humanize(value: string | null | undefined): string {
  return value ? value.replaceAll("_", " ") : "Unavailable";
}

function RetrievalServiceCard({
  service,
  observed,
  desiredState,
  busy,
  onConfirm,
}: {
  service: (typeof SERVICES)[number];
  observed: RetrievalWorkerStatus | undefined;
  desiredState: "running" | "stopped" | undefined;
  busy: boolean;
  onConfirm: (action: RetrievalLifecycleAction) => void;
}) {
  const actions: RetrievalLifecycleAction[] = ["start", "stop", "restart"];
  return (
    <article className="panel retrieval-control-card" aria-label={`${service.alias} controls`}>
      <div className="panel-heading">
        <div><span className="eyebrow">CPU RETRIEVAL WORKER</span><h3>{service.alias}</h3></div>
        <span className={`state-badge state-${(observed?.state ?? "Unknown/Unavailable").toLowerCase().replaceAll("/", "-").replaceAll(" ", "-")}`}>
          <span className="state-dot" />{observed?.state ?? "Unknown/Unavailable"}
        </span>
      </div>
      <div className="retrieval-control-details">
        <div><span>Model</span><strong>{service.model}</strong></div>
        <div><span>Desired state</span><strong>{desiredState ? humanize(desiredState) : "Loading"}</strong></div>
        <div><span>Immutable revision</span><strong className="mono">{observed?.expected_model_revision ?? "Pinned in service contract"}</strong></div>
        <div><span>Observed revision</span><strong className="mono">{observed?.model_revision ?? "Unavailable"}</strong></div>
        <div><span>Last error category</span><strong>{humanize(observed?.last_error_category)}</strong></div>
      </div>
      <div className="retrieval-control-actions">
        {actions.map((action) => <button
          className={action === "start" ? "primary-button" : "secondary-button"}
          disabled={busy}
          key={action}
          onClick={() => onConfirm(action)}
          type="button"
        >{action[0].toUpperCase() + action.slice(1)}</button>)}
      </div>
    </article>
  );
}

function operationLabel(action: RetrievalLifecycleAction, service: RetrievalServiceName): string {
  const alias = service === "embed" ? "ddu-embed" : "ddu-rerank";
  return `${action[0].toUpperCase() + action.slice(1)} ${alias}`;
}

export function RetrievalOperationsPanel({
  csrfToken,
  workers,
}: {
  csrfToken: string;
  workers: RetrievalWorkerStatus[];
}) {
  const [snapshot, setSnapshot] = useState<RetrievalOperationStateSnapshot | null>(null);
  const [confirmation, setConfirmation] = useState<Confirmation>(null);
  const [running, setRunning] = useState<Confirmation>(null);
  const [record, setRecord] = useState<RetrievalOperationRecord | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setSnapshot(await getRetrievalOperationState());
      setError(null);
    } catch (cause) {
      if (cause instanceof ApiRequestError && cause.status === 401) {
        setError("Your dashboard session expired. Sign in again to manage retrieval workers.");
      } else {
        setError("Retrieval worker control state is unavailable.");
      }
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    if (!running) return undefined;
    const timer = window.setInterval(() => void refresh(), 2000);
    return () => window.clearInterval(timer);
  }, [refresh, running]);

  const perform = async () => {
    if (!confirmation) return;
    const action = confirmation;
    setConfirmation(null);
    setRunning(action);
    setError(null);
    setRecord(null);
    try {
      const result = await operateRetrievalService(action.service, action.action, csrfToken);
      setRecord(result);
      await refresh();
    } catch (cause) {
      if (cause instanceof ApiRequestError && cause.status === 409) {
        setError("The retrieval operation was not accepted. Check the Compose contract and current activity.");
      } else if (cause instanceof ApiRequestError && cause.status === 401) {
        setError("Your dashboard session expired. Sign in again to manage retrieval workers.");
      } else {
        setError("The retrieval operation did not complete. Review its recorded outcome and worker status.");
      }
      await refresh();
    } finally {
      setRunning(null);
    }
  };

  const busy = Boolean(running || snapshot?.activity);
  const confirmedLabel = confirmation ? operationLabel(confirmation.action, confirmation.service) : "";

  return (
    <section className="retrieval-operations-section" aria-labelledby="retrieval-operations-title">
      <div className="section-heading">
        <div><span className="eyebrow">INDEPENDENT CPU SERVICES</span><h2 id="retrieval-operations-title">Retrieval worker controls</h2></div>
        <span className="section-note">Fixed Compose project · authenticated and confirmed operations</span>
      </div>
      {!snapshot?.configured && <p className="operation-unconfigured" role="status">
        Configure the retrieval Compose root with <code>python -m app.settings configure-retrieval-compose-root --path …</code> to enable lifecycle controls.
      </p>}
      {error && <p className="operation-error" role="alert">{error}</p>}
      {record && <p className="operation-notice" role="status">
        {record.service} {record.action}: {humanize(record.outcome_category ?? record.phase)}
      </p>}
      {running && <p className="operation-loading" role="status">{operationLabel(running.action, running.service)} in progress…</p>}
      <div className="retrieval-control-grid">
        {SERVICES.map((service) => <RetrievalServiceCard
          busy={busy || !snapshot?.configured}
          desiredState={snapshot?.services[service.id]?.desired_state}
          key={service.id}
          observed={workers.find((worker) => worker.service === service.id)}
          onConfirm={(action) => setConfirmation({ service: service.id, action })}
          service={service}
        />)}
      </div>
      {confirmation && <div className="operation-confirmation" role="alertdialog" aria-modal="true" aria-labelledby="retrieval-confirm-title">
        <h3 id="retrieval-confirm-title">Confirm {confirmedLabel}</h3>
        <p>This operation affects only the selected retrieval worker. Its model cache is preserved.</p>
        <div className="operation-confirm-actions">
          <button className="secondary-button" onClick={() => setConfirmation(null)} type="button">Cancel</button>
          <button className="primary-button" onClick={() => void perform()} type="button">Confirm action</button>
        </div>
      </div>}
    </section>
  );
}
