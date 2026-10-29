import { useCallback, useEffect, useMemo, useState } from "react";

import {
  ApiRequestError,
  applyProfile,
  getOperationState,
  restartInference,
  startInference,
  stopInference,
} from "../api";
import type { OperationRecord, OperationStateSnapshot, Profile } from "../api";

type Action =
  | { kind: "start"; profileId: string }
  | { kind: "stop" }
  | { kind: "restart" }
  | { kind: "apply_profile"; profileId: string };

const ACTIVE_PHASES = new Set([
  "queued",
  "accepted",
  "starting",
  "loading",
  "contract_check",
  "stopping",
  "rolling_back",
  "starting_rollback",
  "rollback_contract_check",
  "configuring",
]);

function objectValue(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function actionLabel(action: Action, profilesById: Map<string, Profile>): string {
  if (action.kind === "start") return `Start ${profilesById.get(action.profileId)?.name ?? "inference"}`;
  if (action.kind === "apply_profile") return `Apply ${profilesById.get(action.profileId)?.name ?? "profile"}`;
  return action.kind === "stop" ? "Stop inference" : "Restart inference";
}

function operationStatus(record: OperationRecord | null, requestAction: Action | null): string {
  if (record?.phase === "loading") return "Loading model and waiting for health checks…";
  if (record?.phase === "contract_check" || record?.phase === "rollback_contract_check") {
    return "Checking the inference API contract…";
  }
  if (record?.phase === "stopping") return "Stopping inference…";
  if (record?.phase === "rolling_back" || record?.phase === "starting_rollback") {
    return "Restoring the last known-good profile…";
  }
  if (requestAction?.kind === "start") return "Starting inference…";
  if (requestAction?.kind === "stop") return "Stopping inference…";
  if (requestAction?.kind === "restart") return "Restarting inference…";
  if (requestAction?.kind === "apply_profile") return "Applying profile…";
  return "Inference operation in progress…";
}

function profileName(profileId: unknown, profilesById: Map<string, Profile>): string {
  return typeof profileId === "string" ? profilesById.get(profileId)?.name ?? profileId : "Unavailable";
}

export function OperationPanel({ profiles, csrfToken }: { profiles: Profile[]; csrfToken: string }) {
  const [snapshot, setSnapshot] = useState<OperationStateSnapshot | null>(null);
  const [selectedProfileId, setSelectedProfileId] = useState(profiles[0]?.id ?? "");
  const [selectionTouched, setSelectionTouched] = useState(false);
  const [confirmation, setConfirmation] = useState<Action | null>(null);
  const [runningAction, setRunningAction] = useState<Action | null>(null);
  const [localOperation, setLocalOperation] = useState<OperationRecord | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const profilesById = useMemo(() => new Map(profiles.map((profile) => [profile.id, profile])), [profiles]);

  const refresh = useCallback(async () => {
    try {
      const next = await getOperationState();
      setSnapshot(next);
      setError(null);
      if (next.latest_operation) setLocalOperation(next.latest_operation);
      if (!selectionTouched && next.control_state.selected_profile_id) {
        setSelectedProfileId(next.control_state.selected_profile_id);
      } else if (!selectionTouched && !selectedProfileId && profiles.length > 0) {
        setSelectedProfileId(profiles[0].id);
      }
    } catch (cause) {
      if (cause instanceof ApiRequestError && cause.status === 401) {
        setError("Your dashboard session expired. Sign in again to use inference controls.");
      } else {
        setError("Inference operation state is unavailable.");
      }
    }
  }, [profiles, selectedProfileId, selectionTouched]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const operation = snapshot?.latest_operation ?? localOperation;
  const operationInProgress = Boolean(operation && ACTIVE_PHASES.has(operation.phase));
  useEffect(() => {
    if (!runningAction && !operationInProgress) return undefined;
    const timer = window.setInterval(() => void refresh(), 1000);
    return () => window.clearInterval(timer);
  }, [operationInProgress, refresh, runningAction]);

  const confirmAction = async () => {
    const action = confirmation;
    if (!action) return;
    setConfirmation(null);
    setRunningAction(action);
    setError(null);
    setNotice(null);
    try {
      let result: OperationRecord;
      if (action.kind === "start") result = await startInference(action.profileId, csrfToken);
      else if (action.kind === "stop") result = await stopInference(csrfToken);
      else if (action.kind === "restart") result = await restartInference(csrfToken);
      else result = await applyProfile(action.profileId, csrfToken);
      setLocalOperation(result);
      setNotice(result.outcome_category ?? result.phase);
      await refresh();
    } catch (cause) {
      if (cause instanceof ApiRequestError && cause.status === 401) {
        setError("Your dashboard session expired. Sign in again to use inference controls.");
      } else if (cause instanceof ApiRequestError && cause.status === 409) {
        setError("The operation was not accepted. Check the configured project and current operation state.");
      } else {
        setError("The inference operation did not complete. Review its latest recorded outcome.");
      }
      await refresh();
    } finally {
      setRunningAction(null);
    }
  };

  const configured = snapshot?.configured === true;
  const busy = Boolean(runningAction || operationInProgress);
  const desired = snapshot?.control_state.desired_state;
  const recovery = objectValue(operation?.recovery_state);
  const candidate = objectValue(recovery?.candidate);
  const rollback = objectValue(recovery?.rollback);

  return (
    <section className="panel operation-panel" aria-labelledby="operation-panel-title">
      <div className="panel-heading">
        <div><span className="eyebrow">INFERENCE LIFECYCLE</span><h2 id="operation-panel-title">Runtime controls</h2></div>
        <span className="readonly-tag">FIXED OPERATIONS</span>
      </div>
      <p className="operation-desired" aria-live="polite">
        Desired state: {desired ? desired[0].toUpperCase() + desired.slice(1) : "Loading"}
      </p>
      {!configured && <p className="operation-unconfigured" role="status">
        Controls are unavailable until an operator explicitly configures a stable local Compose project.
      </p>}
      {error && <p className="operation-error" role="alert">{error}</p>}
      {notice && <p className="operation-notice" role="status">Latest outcome: {notice.replaceAll("_", " ")}</p>}
      {(busy || operationInProgress) && <p className="operation-loading" role="status">
        {operationStatus(operation, runningAction)}
      </p>}
      {candidate?.outcome === "failed" && <p className="operation-error" role="alert">
        Candidate failed: {typeof candidate.category === "string" ? candidate.category.replaceAll("_", " ") : "validation failed"}.
      </p>}
      {rollback?.outcome === "succeeded" && <p className="operation-notice" role="status">
        Rollback succeeded: {profileName(rollback.profile_id, profilesById)} is serving.
      </p>}
      {rollback?.outcome === "failed" && <p className="operation-error" role="alert">
        Rollback failed. Inference may require operator recovery.
      </p>}
      <div className="operation-controls">
        <label className="operation-profile">Inference profile
          <select
            aria-label="Inference profile"
            disabled={busy || !configured || profiles.length === 0}
            value={selectedProfileId}
            onChange={(event) => {
              setSelectionTouched(true);
              setSelectedProfileId(event.target.value);
            }}
          >
            {profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}</option>)}
          </select>
        </label>
        <button
          className="primary-button"
          disabled={busy || !configured || !selectedProfileId}
          onClick={() => setConfirmation({ kind: "start", profileId: selectedProfileId })}
          type="button"
        >Start inference</button>
        <button
          className="secondary-button"
          disabled={busy || !configured || !selectedProfileId}
          onClick={() => setConfirmation({ kind: "apply_profile", profileId: selectedProfileId })}
          type="button"
        >Apply profile</button>
        <button
          className="secondary-button"
          disabled={busy || !configured}
          onClick={() => setConfirmation({ kind: "stop" })}
          type="button"
        >Stop inference</button>
        <button
          className="secondary-button"
          disabled={busy || !configured}
          onClick={() => setConfirmation({ kind: "restart" })}
          type="button"
        >Restart inference</button>
      </div>
      {confirmation && <div className="operation-confirmation" role="alertdialog" aria-modal="true" aria-labelledby="operation-confirm-title">
        <h3 id="operation-confirm-title">Confirm {actionLabel(confirmation, profilesById)}</h3>
        <p>This changes the local inference service. The model cache and other volumes are preserved.</p>
        <div className="operation-confirm-actions">
          <button className="secondary-button" onClick={() => setConfirmation(null)} type="button">Cancel</button>
          <button className="primary-button" onClick={() => void confirmAction()} type="button">Confirm action</button>
        </div>
      </div>}
    </section>
  );
}
