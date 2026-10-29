/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AuthClient } from "../auth";
import { SettingsPage } from "./SettingsPage";

afterEach(() => cleanup());

function makeClient(): AuthClient {
  return {
    readState: vi.fn(),
    setup: vi.fn(),
    login: vi.fn(),
    recovery: vi.fn(),
    logout: vi.fn(),
    listSessions: vi.fn().mockResolvedValue([
      { id: "current", created_at: "2026-10-29T10:00:00Z", expires_at: "2026-10-29T18:00:00Z", current: true },
      { id: "other", created_at: "2026-10-28T10:00:00Z", expires_at: "2026-10-28T18:00:00Z", current: false },
    ]),
    revokeOtherSessions: vi.fn().mockResolvedValue(1),
    changePassword: vi.fn().mockResolvedValue(undefined),
  };
}

describe("settings security controls", () => {
  it("lists safe session metadata and revokes sessions other than the current one", async () => {
    const client = makeClient();
    render(
      <SettingsPage
        authClient={client}
        csrfToken="csrf-in-memory"
        onPasswordChanged={vi.fn()}
      />,
    );

    expect(await screen.findByText("Current session")).toBeTruthy();
    expect(screen.getByText("Other session")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Revoke other sessions" }));

    await waitFor(() =>
      expect(client.revokeOtherSessions).toHaveBeenCalledWith("csrf-in-memory"),
    );
    expect((await screen.findByRole("status")).textContent).toContain("1 other session revoked");
  });

  it("changes the administrator password and returns to the login gate", async () => {
    const client = makeClient();
    const onPasswordChanged = vi.fn();
    render(
      <SettingsPage
        authClient={client}
        csrfToken="csrf-in-memory"
        onPasswordChanged={onPasswordChanged}
      />,
    );

    fireEvent.change(screen.getByLabelText("Current password"), {
      target: { value: "old-password" },
    });
    fireEvent.change(screen.getByLabelText("New password"), {
      target: { value: "new-password-long" },
    });
    fireEvent.change(screen.getByLabelText("Confirm new password"), {
      target: { value: "new-password-long" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Change password" }));

    await waitFor(() =>
      expect(client.changePassword).toHaveBeenCalledWith(
        "csrf-in-memory",
        "old-password",
        "new-password-long",
      ),
    );
    expect(onPasswordChanged).toHaveBeenCalledOnce();
  });
});
