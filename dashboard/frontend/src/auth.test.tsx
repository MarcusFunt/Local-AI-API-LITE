/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import { createAuthClient } from "./auth";
import { AuthPage } from "./pages/AuthPage";

afterEach(() => cleanup());

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("dashboard authentication", () => {
  it("accepts an eight-character setup password without a symbol", () => {
    render(
      <AuthPage
        mode="setup-required"
        onSetup={vi.fn()}
        onLogin={vi.fn()}
        onRecovery={vi.fn()}
        error={null}
        notice={null}
      />,
    );
    const password = screen.getByLabelText("New administrator password") as HTMLInputElement;

    fireEvent.change(password, { target: { value: "Abcd1234" } });

    expect(password.checkValidity()).toBe(true);
    expect(screen.getByText(/Symbols are optional/)).toBeTruthy();
  });

  it.each([
    ["uppercase letter", "lowercasepassword1"],
    ["lowercase letter", "UPPERCASEPASSWORD1"],
    ["number", "PasswordWithoutDigits"],
  ])("requires a %s for setup passwords", (_requirement, value) => {
    render(
      <AuthPage
        mode="setup-required"
        onSetup={vi.fn()}
        onLogin={vi.fn()}
        onRecovery={vi.fn()}
        error={null}
        notice={null}
      />,
    );
    const password = screen.getByLabelText("New administrator password") as HTMLInputElement;

    fireEvent.change(password, { target: { value } });

    expect(password.checkValidity()).toBe(false);
  });

  it("allows an eight-character password in the sign-in field", () => {
    render(
      <AuthPage
        mode="logged-out"
        onSetup={vi.fn()}
        onLogin={vi.fn()}
        onRecovery={vi.fn()}
        error={null}
        notice={null}
      />,
    );
    const password = screen.getByLabelText("Administrator password") as HTMLInputElement;

    fireEvent.change(password, { target: { value: "Abcd1234" } });

    expect(password.checkValidity()).toBe(true);
  });

  it("returns setup-required state from the same-origin auth endpoint", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({ setup_required: true, authenticated: false }),
    );
    const client = createAuthClient(fetcher);

    await expect(client.readState()).resolves.toEqual({ kind: "setup-required" });
    expect(fetcher).toHaveBeenCalledWith(
      "/auth/state",
      expect.objectContaining({ credentials: "same-origin" }),
    );
  });

  it("requires a fresh login after reload because the CSRF token is memory-only", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({ setup_required: false, authenticated: true }),
    );

    await expect(createAuthClient(fetcher).readState()).resolves.toEqual({
      kind: "logged-out",
      reauthenticate: true,
    });
  });

  it("submits the one-time setup token without persisting it", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 204 }));
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    const client = createAuthClient(fetcher);

    await client.setup("one-time-setup-token", "a-long-setup-password");

    const request = fetcher.mock.calls[0][1];
    expect(JSON.parse(String(request?.body))).toEqual({
      token: "one-time-setup-token",
      password: "a-long-setup-password",
    });
    expect(request?.credentials).toBe("same-origin");
    expect(setItem).not.toHaveBeenCalled();
    setItem.mockRestore();
  });

  it("submits a password and keeps login CSRF data in memory", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({ csrf_token: "csrf-memory-only", expires_at: "2026-10-29T20:00:00Z" }),
    );
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    const client = createAuthClient(fetcher);

    await expect(client.login("administrator-password")).resolves.toEqual({
      kind: "authenticated",
      csrfToken: "csrf-memory-only",
      expiresAt: "2026-10-29T20:00:00Z",
    });
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual({
      password: "administrator-password",
    });
    expect(setItem).not.toHaveBeenCalled();
    setItem.mockRestore();
  });

  it("logs out with the CSRF header and does not persist session data", async () => {
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ csrf_token: "csrf-memory-only", expires_at: "later" }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    const client = createAuthClient(fetcher);
    const login = await client.login("password");

    await client.logout(login.csrfToken);

    expect(fetcher.mock.calls[1][0]).toBe("/auth/logout");
    expect(fetcher.mock.calls[1][1]).toMatchObject({
      method: "POST",
      credentials: "same-origin",
      headers: expect.objectContaining({ "X-CSRF-Token": "csrf-memory-only" }),
    });
  });

  it("does not fetch protected data before the administrator logs in", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({ setup_required: false, authenticated: true }),
    );
    const client = createAuthClient(fetcher);
    const globalFetch = vi.spyOn(globalThis, "fetch");

    render(<App authClient={client} />);

    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "System overview" })).toBeNull();
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(globalFetch).not.toHaveBeenCalled();
    globalFetch.mockRestore();
  });

  it("loads protected pages after login and returns to the login gate after logout", async () => {
    const authFetch = vi.fn<typeof fetch>(async (input) => {
      if (input === "/auth/state") return jsonResponse({ setup_required: false, authenticated: false });
      if (input === "/auth/login") return jsonResponse({ csrf_token: "csrf-session", expires_at: "later" });
      if (input === "/auth/logout") return new Response(null, { status: 204 });
      throw new Error(`unexpected auth request: ${String(input)}`);
    });
    const dataFetch = vi.fn<typeof fetch>(async (input) => {
      if (input === "/api/control/v1/profiles") return jsonResponse({ profiles: [] });
      if (input === "/api/control/v1/status") return jsonResponse({
        refreshed_at: "2026-10-29T10:00:00Z",
        controller: { state: "Ready" },
        inference: {
          state: "Stopped",
          availability: "available",
          container: null,
          configuration: null,
          last_health_probe_at: null,
          last_successful_health_probe_at: null,
          last_failure_reason: null,
        },
        active_profile: null,
        telemetry: {
          cpu_percent: null,
          ram_used_bytes: null,
          ram_total_bytes: null,
          cache_disk_free_bytes: null,
          gpu: {
            available: false,
            scope: "device-wide",
            reason: "NVML unavailable",
            utilization_percent: null,
            memory_used_bytes: null,
            memory_total_bytes: null,
            memory_free_bytes: null,
            temperature_c: null,
            power_watts: null,
          },
        },
        status_cache: {
          observed_at: "2026-10-29T10:00:00Z",
          stale: false,
          refreshing: false,
          last_poll_error: null,
          last_poll_error_at: null,
        },
      });
      throw new Error(`unexpected dashboard request: ${String(input)}`);
    });
    vi.stubGlobal("fetch", dataFetch);
    const client = createAuthClient(authFetch);
    try {
      render(<App authClient={client} />);
      expect(await screen.findByRole("heading", { name: "Sign in" })).toBeTruthy();
      fireEvent.change(screen.getByLabelText("Administrator password"), {
        target: { value: "administrator-password" },
      });
      fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

      expect(await screen.findByRole("heading", { name: "System overview" })).toBeTruthy();
      await waitFor(() => expect(dataFetch).toHaveBeenCalledWith(
        "/api/control/v1/status",
        expect.objectContaining({ credentials: "same-origin" }),
      ));
      fireEvent.click(screen.getByRole("button", { name: "Sign out" }));

      expect(await screen.findByRole("heading", { name: "Sign in" })).toBeTruthy();
      expect(authFetch.mock.calls.some(([path]) => path === "/auth/logout")).toBe(true);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("submits the login password from the login view", async () => {
    const onLogin = vi.fn().mockResolvedValue(undefined);
    render(
      <AuthPage
        mode="logged-out"
        onSetup={vi.fn()}
        onLogin={onLogin}
        onRecovery={vi.fn()}
        error={null}
        notice={null}
      />,
    );

    fireEvent.change(screen.getByLabelText("Administrator password"), {
      target: { value: "administrator-password" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(onLogin).toHaveBeenCalledWith("administrator-password"));
  });

  it("uses session revocation and password-change endpoints with the in-memory CSRF token", async () => {
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ revoked: 2 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    const client = createAuthClient(fetcher);

    await expect(client.revokeOtherSessions("csrf-in-memory")).resolves.toBe(2);
    await client.changePassword("csrf-in-memory", "old-password", "new-password-long");

    expect(fetcher.mock.calls[0][0]).toBe("/auth/sessions/revoke-others");
    expect(fetcher.mock.calls[0][1]).toMatchObject({
      headers: expect.objectContaining({ "X-CSRF-Token": "csrf-in-memory" }),
    });
    expect(fetcher.mock.calls[1][0]).toBe("/auth/password");
    expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body))).toEqual({
      old_password: "old-password",
      new_password: "new-password-long",
    });
  });
});
