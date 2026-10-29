import { useCallback, useEffect, useState, type FormEvent } from "react";

import { checkDatabase, deleteDatabase, getDatabases, revealDatabaseCredential, saveDatabase } from "../api";
import type { DatabaseListResponse, DatabaseStatus } from "../api";

function configurationMessage(state: string): string {
  if (state === "Unconfigured") return "Database monitoring is not configured.";
  if (state === "Unavailable") return "The local vault or database monitor is unavailable.";
  return "Credentials stay encrypted in the local dashboard vault. The probe runs SELECT 1 in a read-only transaction.";
}

export function DatabasesPage({ csrfToken }: { csrfToken: string }) {
  const [data, setData] = useState<DatabaseListResponse | null>(null);
  const [name, setName] = useState("");
  const [dsn, setDsn] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [revealed, setRevealed] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await getDatabases());
    } catch {
      setError("Database monitor status is unavailable.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const clearForm = () => {
    setName("");
    setDsn("");
    setEditingId(null);
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      const result = await saveDatabase(name.trim(), csrfToken, {
        ...(editingId ? { databaseId: editingId } : {}),
        ...(dsn ? { dsn } : {}),
      });
      clearForm();
      setRevealed({});
      setNotice(editingId ? "Database monitor settings updated." : "Database monitor added.");
      const next = await getDatabases();
      setData(next);
      if (result.database_id && next.configuration_state.state === "Unconfigured") {
        setData({
          configuration_state: { state: "Configured" },
          databases: [{
            database_id: result.database_id,
            name: result.name,
            state: "Unknown",
            checked_at: null,
            latency_ms: null,
            failure_category: null,
          }],
        });
      }
    } catch {
      setError("Could not save database monitor settings. Check the name and PostgreSQL connection string.");
    } finally {
      setSaving(false);
    }
  };

  const startEdit = (database: DatabaseStatus) => {
    setEditingId(database.database_id);
    setName(database.name);
    setDsn("");
    setNotice("The credential stays hidden. Leave the connection string blank to keep it, or enter a replacement.");
    setError(null);
    document.getElementById("database-name")?.focus();
  };

  const runCheck = async (database: DatabaseStatus) => {
    setLoading(true);
    setError(null);
    setRevealed((current) => ({ ...current, [database.database_id]: "" }));
    try {
      const result = await checkDatabase(database.database_id, csrfToken);
      setData((current) => current ? {
        ...current,
        databases: current.databases.map((item) => item.database_id === database.database_id
          ? { ...item, ...result }
          : item),
      } : current);
    } catch {
      setError("The PostgreSQL probe could not run.");
    } finally {
      setLoading(false);
    }
  };

  const remove = async (database: DatabaseStatus) => {
    if (!window.confirm(`Remove the ${database.name} database monitor and its stored credential?`)) return;
    setError(null);
    try {
      await deleteDatabase(database.database_id, csrfToken);
      setRevealed((current) => {
        const next = { ...current };
        delete next[database.database_id];
        return next;
      });
      await refresh();
      setNotice("Database monitor removed.");
    } catch {
      setError("Database monitor could not be removed.");
    }
  };

  const reveal = async (databaseId: string) => {
    setError(null);
    setRevealed((current) => ({ ...current, [databaseId]: "" }));
    try {
      const result = await revealDatabaseCredential(databaseId, csrfToken);
      setRevealed((current) => ({ ...current, [databaseId]: result.credential }));
    } catch {
      setError("Credential reveal is unavailable. Try again after signing in.");
    }
  };

  const state = data?.configuration_state.state ?? "Unavailable";

  return (
    <section className="page-content databases-page" aria-label="Database monitoring">
      <div className="section-heading">
        <div><div className="eyebrow">OPTIONAL · READ ONLY</div><h2>PostgreSQL monitoring</h2></div>
        <span className={`state-badge state-${state.toLowerCase()}`}><span className="state-dot" />{state}</span>
      </div>
      <p className="database-disclosure">{configurationMessage(state)} Database probes run separately from inference status and never provision accounts or schemas.</p>
      {error && <div className="connection-alert" role="alert">{error}</div>}
      {notice && <div className="database-notice" role="status">{notice}</div>}

      <div className="database-layout">
        <form className="database-form panel" onSubmit={(event) => void submit(event)}>
          <div className="panel-heading"><div><div className="eyebrow">LOCAL SETTINGS</div><h3>{editingId ? "Edit monitor" : "Add a database"}</h3></div></div>
          <label htmlFor="database-name">Database name</label>
          <input id="database-name" autoComplete="off" maxLength={80} onChange={(event) => setName(event.target.value)} required value={name} />
          <label htmlFor="database-dsn">PostgreSQL connection string {editingId && <span>(optional replacement)</span>}</label>
          <input
            id="database-dsn"
            type="password"
            autoComplete="new-password"
            maxLength={4096}
            onChange={(event) => setDsn(event.target.value)}
            placeholder={editingId ? "Leave blank to keep the saved credential" : "postgresql://user:password@host:5432/database"}
            required={!editingId}
            value={dsn}
          />
          <div className="database-form-actions">
            <button className="primary-button" disabled={saving} type="submit">{saving ? "Saving…" : editingId ? "Save changes" : "Add monitor"}</button>
            {editingId && <button className="secondary-button" onClick={clearForm} type="button">Cancel edit</button>}
          </div>
          <div className="database-form-note">Connection strings are write only and encrypted with the current Windows user's DPAPI protected vault key.</div>
        </form>

        <div className="database-list panel">
          <div className="panel-heading"><div><div className="eyebrow">PROBE STATUS</div><h3>Configured databases</h3></div><button className="secondary-button" disabled={loading} onClick={() => void refresh()} type="button">Refresh status</button></div>
          {state === "Unconfigured" && <p className="empty-state">No database monitors are configured.</p>}
          {state === "Unavailable" && <p className="empty-state">The database monitor is unavailable.</p>}
          {data?.databases.map((database) => (
            <article className="database-row" key={database.database_id}>
              <div className="database-row-heading">
                <div><h4>{database.name}</h4><span className={`state-badge state-${database.state.toLowerCase()}`}><span className="state-dot" />{database.state}</span></div>
                <div className="database-meta">{database.latency_ms === null ? "Latency unavailable" : `${database.latency_ms} ms`}{database.failure_category && <span>{database.failure_category}</span>}</div>
              </div>
              <div className="database-row-meta">Last check: {database.checked_at ?? "Never"}</div>
              <div className="database-row-actions">
                <button className="secondary-button" disabled={loading} onClick={() => void runCheck(database)} type="button">Check now</button>
                <button className="secondary-button" onClick={() => revealed[database.database_id] ? setRevealed((current) => ({ ...current, [database.database_id]: "" })) : void reveal(database.database_id)} type="button">
                  {revealed[database.database_id] ? "Hide credential" : "Reveal credential"}
                </button>
                <button className="secondary-button" onClick={() => startEdit(database)} type="button">Edit settings</button>
                <button className="secondary-button danger-button" onClick={() => void remove(database)} type="button">Remove</button>
              </div>
              {revealed[database.database_id] && <pre className="database-credential" aria-label="Revealed database credential">{revealed[database.database_id]}</pre>}
            </article>
          ))}
          {!data && loading && <p className="empty-state">Loading database monitor status…</p>}
        </div>
      </div>
    </section>
  );
}
