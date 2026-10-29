import { useState, type FormEvent } from "react";

type AuthMode = "setup-required" | "logged-out";

interface AuthPageProps {
  mode: AuthMode;
  onSetup: (token: string, password: string) => Promise<void>;
  onLogin: (password: string) => Promise<void>;
  onRecovery: (token: string, newPassword: string) => Promise<void>;
  error: string | null;
  notice: string | null;
}

export function AuthPage({ mode, onSetup, onLogin, onRecovery, error, notice }: AuthPageProps) {
  const [token, setToken] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [recoveryMode, setRecoveryMode] = useState(false);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setFormError(null);
    if (mode !== "setup-required" && !recoveryMode) {
      setBusy(true);
      try {
        await onLogin(password);
        setPassword("");
      } finally {
        setBusy(false);
      }
      return;
    }
    if (password !== confirmation) {
      setFormError("The passwords do not match.");
      return;
    }
    setBusy(true);
    try {
      if (mode === "setup-required") await onSetup(token, password);
      else await onRecovery(token, password);
      setToken("");
      setPassword("");
      setConfirmation("");
      setRecoveryMode(false);
    } finally {
      setBusy(false);
    }
  };

  const showPasswordForm = mode === "setup-required" || recoveryMode;
  const title = mode === "setup-required" ? "Set up administrator access" : recoveryMode ? "Recover administrator access" : "Sign in";
  const buttonLabel = mode === "setup-required" ? "Set administrator password" : recoveryMode ? "Set new password" : "Sign in";

  return (
    <main className="auth-shell">
      <section className="auth-card" aria-labelledby="auth-title">
        <div className="brand-lockup auth-brand">
          <div className="brand-symbol" aria-hidden="true"><span /><span /><span /></div>
          <div><div className="brand-name">LOCAL AI</div><div className="brand-subtitle">API · LITE</div></div>
        </div>
        <span className="eyebrow">LOCAL CONTROL PLANE</span>
        <h1 id="auth-title">{title}</h1>
        <p className="auth-copy">
          {mode === "setup-required"
            ? "Use the one-time token printed by the local installer, then choose a permanent password."
            : recoveryMode
              ? "Enter the one-time recovery token generated locally and choose a new password."
              : "Sign in to view local service status and dashboard settings."}
        </p>
        {notice && <p className="auth-notice" role="status">{notice}</p>}
        {(formError || error) && <p className="auth-error" role="alert">{formError ?? error}</p>}
        <form className="auth-form" onSubmit={(event) => void submit(event)}>
          {showPasswordForm && (
            <label>
              {mode === "setup-required" ? "One-time setup token" : "One-time recovery token"}
              <input
                autoComplete="off"
                required
                value={token}
                onChange={(event) => setToken(event.target.value)}
                type="password"
              />
            </label>
          )}
          <label>
            {mode === "setup-required" || recoveryMode ? "New administrator password" : "Administrator password"}
            <input
              autoComplete={showPasswordForm ? "new-password" : "current-password"}
              required
              minLength={12}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              type="password"
            />
          </label>
          {showPasswordForm && (
            <label>
              Confirm new password
              <input
                autoComplete="new-password"
                required
                minLength={12}
                value={confirmation}
                onChange={(event) => setConfirmation(event.target.value)}
                type="password"
              />
            </label>
          )}
          <button className="primary-button" disabled={busy} type="submit">
            {busy ? "Please wait…" : buttonLabel}
          </button>
        </form>
        {mode === "logged-out" && (
          <button className="text-button" type="button" onClick={() => {
            setRecoveryMode((previous) => !previous);
            setPassword("");
            setConfirmation("");
            setToken("");
            setFormError(null);
          }}>
            {recoveryMode ? "Return to sign in" : "Use a local recovery token"}
          </button>
        )}
        <p className="auth-footnote">Session cookies stay local to this browser. Passwords and tokens are not saved here.</p>
      </section>
    </main>
  );
}
