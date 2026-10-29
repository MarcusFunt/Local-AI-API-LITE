import { useCallback, useEffect, useState, type FormEvent } from "react";

import { ADMIN_PASSWORD_HINT, ADMIN_PASSWORD_PATTERN, type AuthClient, type SessionMetadata } from "../auth";

interface SettingsPageProps {
  authClient: AuthClient;
  csrfToken: string;
  onPasswordChanged: () => void;
}

function formatSessionDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "Unavailable"
    : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
}

export function SettingsPage({ authClient, csrfToken, onPasswordChanged }: SettingsPageProps) {
  const [sessions, setSessions] = useState<SessionMetadata[]>([]);
  const [loadingSessions, setLoadingSessions] = useState(true);
  const [busy, setBusy] = useState(false);
  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadSessions = useCallback(async () => {
    setLoadingSessions(true);
    try {
      setSessions(await authClient.listSessions());
      setError(null);
    } catch {
      setError("Active sessions could not be loaded.");
    } finally {
      setLoadingSessions(false);
    }
  }, [authClient]);

  useEffect(() => {
    void loadSessions();
  }, [loadSessions]);

  const revokeOthers = async () => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const revoked = await authClient.revokeOtherSessions(csrfToken);
      setNotice(`${revoked} other ${revoked === 1 ? "session" : "sessions"} revoked`);
      await loadSessions();
    } catch {
      setError("Other sessions could not be revoked.");
    } finally {
      setBusy(false);
    }
  };

  const changePassword = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    setNotice(null);
    if (newPassword !== confirmation) {
      setError("The new passwords do not match.");
      return;
    }
    setBusy(true);
    try {
      await authClient.changePassword(csrfToken, oldPassword, newPassword);
      setNotice("Password changed. Sign in again with your new password.");
      setOldPassword("");
      setNewPassword("");
      setConfirmation("");
      onPasswordChanged();
    } catch {
      setError("Password could not be changed. Check the current password and try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page-content settings-content">
      <section className="intro-panel">
        <span className="eyebrow">LOCAL ACCOUNT</span>
        <h2>Security settings</h2>
        <p>Manage this administrator password and the browser sessions that can access the local dashboard.</p>
      </section>
      {(error || notice) && <p className={error ? "settings-message settings-error" : "settings-message"} role={error ? "alert" : "status"}>{error ?? notice}</p>}
      <section className="panel settings-panel">
        <div className="panel-heading"><div><span className="eyebrow">BROWSER ACCESS</span><h2>Active sessions</h2></div><span className="readonly-tag">SERVER MANAGED</span></div>
        {loadingSessions ? <p className="empty-state">Loading active sessions…</p> : sessions.length === 0 ? <p className="empty-state">No active sessions were returned.</p> : (
          <div className="session-list">
            {sessions.map((session) => <article className="session-row" key={session.id}>
              <div><strong>{session.current ? "Current session" : "Other session"}</strong><span>Created {formatSessionDate(session.created_at)} · Expires {formatSessionDate(session.expires_at)}</span></div>
              {session.current && <span className="active-tag">THIS BROWSER</span>}
            </article>)}
          </div>
        )}
        <button className="secondary-button" disabled={busy || loadingSessions || !sessions.some((session) => !session.current)} onClick={() => void revokeOthers()} type="button">
          {busy ? "Please wait…" : "Revoke other sessions"}
        </button>
      </section>
      <section className="panel settings-panel">
        <div className="panel-heading"><div><span className="eyebrow">ADMINISTRATOR CREDENTIAL</span><h2>Change password</h2></div></div>
        <form className="settings-form" onSubmit={(event) => void changePassword(event)}>
          <label>Current password<input autoComplete="current-password" minLength={8} maxLength={256} required type="password" value={oldPassword} onChange={(event) => setOldPassword(event.target.value)} /></label>
          <label>New password<input autoComplete="new-password" minLength={8} maxLength={256} pattern={ADMIN_PASSWORD_PATTERN} title={ADMIN_PASSWORD_HINT} required type="password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} /></label>
          <label>Confirm new password<input autoComplete="new-password" minLength={8} maxLength={256} pattern={ADMIN_PASSWORD_PATTERN} title={ADMIN_PASSWORD_HINT} required type="password" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} /></label>
          <p className="password-requirements">{ADMIN_PASSWORD_HINT}</p>
          <button className="primary-button" disabled={busy} type="submit">{busy ? "Please wait…" : "Change password"}</button>
        </form>
      </section>
    </div>
  );
}
