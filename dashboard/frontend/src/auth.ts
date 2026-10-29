export type AuthState =
  | { kind: "setup-required" }
  | { kind: "logged-out"; reauthenticate?: boolean }
  | { kind: "authenticated"; csrfToken: string; expiresAt: string };

export type AuthenticatedState = Extract<AuthState, { kind: "authenticated" }>;

export const ADMIN_PASSWORD_PATTERN = "(?=.*[A-Z])(?=.*[a-z])(?=.*[0-9]).{8,256}";
export const ADMIN_PASSWORD_HINT =
  "Use 8–256 characters, including uppercase, lowercase, and a number. Symbols are optional.";

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
      let setupDetail: string | null = null;
      if (path === "/auth/setup") {
        try {
          const payload: unknown = await response.json();
          if (
            typeof payload === "object" &&
            payload !== null &&
            "detail" in payload &&
            typeof payload.detail === "string"
          ) {
            setupDetail = payload.detail;
          }
        } catch {
          // Keep the setup-specific fallback when the response is not JSON.
        }
      }
      const message = setupDetail ?? (
        path === "/auth/setup" && response.status === 401
          ? "The setup token was not accepted. Check that it is current and unused."
          : path === "/auth/setup" && response.status === 422
            ? ADMIN_PASSWORD_HINT
            : response.status === 401
              ? "Authentication failed"
              : `Dashboard request failed (${response.status})`
      );
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
      await request<void>("/auth/setup", "POST", { token, password });
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
