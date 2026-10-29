export type AuthState =
  | { kind: "setup-required" }
  | { kind: "logged-out"; reauthenticate?: boolean }
  | { kind: "authenticated"; csrfToken: string; expiresAt: string };

export type AuthenticatedState = Extract<AuthState, { kind: "authenticated" }>;

export const ADMIN_PASSWORD_PATTERN = "(?=.*[A-Z])(?=.*[a-z])(?=.*[0-9]).{8,256}";
export const ADMIN_PASSWORD_HINT =
  "Use 8–256 characters, including uppercase, lowercase, and a number. Symbols are optional.";

export function adminPasswordIssues(password: string): string[] {
  const issues: string[] = [];
  if (password.length < 8) issues.push("at least 8 characters");
  if (password.length > 256) issues.push("no more than 256 characters");
  if (!/[A-Z]/.test(password)) issues.push("at least one uppercase letter");
  if (!/[a-z]/.test(password)) issues.push("at least one lowercase letter");
  if (!/[0-9]/.test(password)) issues.push("at least one digit");
  return issues;
}

export function adminPasswordValidationTitle(password: string): string {
  const issues = adminPasswordIssues(password);
  return issues.length
    ? `Password does not meet these requirements: ${issues.join("; ")}.`
    : ADMIN_PASSWORD_HINT;
}

export interface SessionMetadata {
  id: string;
  created_at: string;
  expires_at: string;
  current: boolean;
}

export interface AuthClient {
  readState(): Promise<Exclude<AuthState, AuthenticatedState>>;
  setup(token: string, password: string): Promise<void>;
  login(password: string): Promise<AuthenticatedState>;
  recovery(token: string, newPassword: string): Promise<void>;
  logout(csrfToken: string): Promise<void>;
  listSessions(): Promise<SessionMetadata[]>;
  revokeOtherSessions(csrfToken: string): Promise<number>;
  changePassword(csrfToken: string, oldPassword: string, newPassword: string): Promise<void>;
}

export class AuthRequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "AuthRequestError";
  }
}

function errorFallback(path: string, status: number): string {
  if (path === "/auth/setup" && status === 401) {
    return "The setup token was not accepted. Make sure it is current and copy the full value from this dashboard's setup-token.txt.";
  }
  if (path === "/auth/setup" && status === 422) return ADMIN_PASSWORD_HINT;
  if (path === "/auth/login" && status === 401) {
    return "The administrator password is incorrect. Check it and try again.";
  }
  if (path === "/auth/login" && status === 429) {
    return "Too many sign-in attempts. The limit is 5 failed attempts in a rolling 15-minute window. " +
      "Wait until an earlier failed attempt is more than 15 minutes old, then try again.";
  }
  if (status === 401) return "Authentication failed";
  return `Dashboard request failed (${status})`;
}

export function createAuthClient(fetcher: typeof fetch = fetch): AuthClient {
  async function request<T>(
    path: string,
    method = "GET",
    body?: Record<string, string>,
    csrfToken?: string,
  ): Promise<T> {
    const headers: Record<string, string> = { Accept: "application/json" };
    if (body) headers["Content-Type"] = "application/json";
    if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
    const response = await fetcher(path, {
      method,
      credentials: "same-origin",
      cache: "no-store",
      headers,
      ...(body ? { body: JSON.stringify(body) } : {}),
      signal: AbortSignal.timeout(10_000),
    });
    if (!response.ok) {
      let actionableDetail: string | null = null;
      if (path === "/auth/setup" || path === "/auth/login") {
        try {
          const payload: unknown = await response.json();
          if (
            typeof payload === "object" &&
            payload !== null &&
            "detail" in payload &&
            typeof payload.detail === "string"
          ) {
            actionableDetail = payload.detail;
          }
        } catch {
          // Keep the endpoint-specific fallback when the response is not JSON.
        }
      }
      const message = actionableDetail ?? errorFallback(path, response.status);
      throw new AuthRequestError(message, response.status);
    }
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }

  return {
    async readState() {
      const state = await request<{ setup_required: boolean; authenticated: boolean }>("/auth/state");
      // A page reload cannot restore the one-time CSRF token, which is kept only in memory.
      if (state.setup_required) return { kind: "setup-required" };
      return state.authenticated
        ? { kind: "logged-out", reauthenticate: true }
        : { kind: "logged-out" };
    },
    async setup(token, password) {
      await request<void>("/auth/setup", "POST", { token: token.trim(), password });
    },
    async login(password) {
      const result = await request<{ csrf_token: string; expires_at: string }>(
        "/auth/login",
        "POST",
        { password },
      );
      if (!result.csrf_token || !result.expires_at) {
        throw new AuthRequestError("The controller returned an invalid login response", 502);
      }
      return { kind: "authenticated", csrfToken: result.csrf_token, expiresAt: result.expires_at };
    },
    async recovery(token, newPassword) {
      await request<void>("/auth/recovery", "POST", { token, new_password: newPassword });
    },
    async logout(csrfToken) {
      await request<void>("/auth/logout", "POST", undefined, csrfToken);
    },
    async listSessions() {
      const result = await request<{ sessions: SessionMetadata[] }>("/auth/sessions");
      return result.sessions;
    },
    async revokeOtherSessions(csrfToken) {
      const result = await request<{ revoked: number }>(
        "/auth/sessions/revoke-others",
        "POST",
        undefined,
        csrfToken,
      );
      return result.revoked;
    },
    async changePassword(csrfToken, oldPassword, newPassword) {
      await request<void>(
        "/auth/password",
        "POST",
        { old_password: oldPassword, new_password: newPassword },
        csrfToken,
      );
    },
  };
}
