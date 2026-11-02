/** @vitest-environment jsdom */
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import type { ExamplePublicStatus } from "./api";
import type { AuthClient } from "./auth";

const apiMocks = vi.hoisted(() => ({
  getStatus: vi.fn(), getProfiles: vi.fn(), getExamples: vi.fn(), sendChatTurn: vi.fn(),
}));
vi.mock("./api", async (importOriginal) => ({
  ...await importOriginal<typeof import("./api")>(), ...apiMocks,
}));

const readyStatus: ExamplePublicStatus = {
  id: "chat-voice", name: "Chat + Voice", state: "ready",
  chat_dependency: { state: "ready", active_profile: "ddu-narrator", phase: null, observed_at: null },
  voice_dependency: { state: "ready", active_profile: null, phase: null, observed_at: null },
  missing_dependencies: [], started_at: null, last_error_category: null,
  idle_expires_at: null, activity_available: true,
};

function authClient(): AuthClient {
  return {
    readState: vi.fn().mockResolvedValue({ kind: "logged-out" }),
    login: vi.fn().mockResolvedValue({ kind: "authenticated", csrfToken: "csrf", expiresAt: "2099-01-01" }),
    setup: vi.fn(), recovery: vi.fn(), logout: vi.fn(), listSessions: vi.fn(),
    revokeOtherSessions: vi.fn(), changePassword: vi.fn(),
  };
}

async function signIn() {
  fireEvent.change(await screen.findByLabelText("Administrator password"), { target: { value: "Password123" } });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
  await screen.findByRole("textbox", { name: "Message" });
}

beforeEach(() => {
  vi.resetAllMocks();
  apiMocks.getStatus.mockImplementation(() => new Promise(() => {}));
  apiMocks.getProfiles.mockResolvedValue({ profiles: [], speech_profiles: [] });
  apiMocks.getExamples.mockResolvedValue([readyStatus]);
  apiMocks.sendChatTurn.mockResolvedValue({ text: "A remembered reply", elapsed_ms: 12, usage: {} });
});
afterEach(() => {
  cleanup();
  window.history.replaceState(null, "", "/");
});

describe("authenticated example navigation", () => {
  it("protects a direct workspace visit and preserves its URL through pending authentication and sign-in", async () => {
    window.history.replaceState(null, "", "/examples/chat-voice");
    const client = authClient();
    let resolveAuth!: (state: { kind: "logged-out" }) => void;
    vi.mocked(client.readState).mockImplementation(() => new Promise((resolve) => { resolveAuth = resolve; }));
    render(<App authClient={client} />);
    expect(screen.queryByRole("textbox", { name: "Message" })).toBeNull();
    expect(window.location.pathname).toBe("/examples/chat-voice");
    await act(async () => resolveAuth({ kind: "logged-out" }));
    await screen.findByRole("heading", { name: "Sign in" });
    expect(screen.queryByRole("textbox", { name: "Message" })).toBeNull();
    await signIn();
    expect(window.location.pathname).toBe("/examples/chat-voice");
    expect(screen.getByRole("link", { name: "Return to Examples" }).getAttribute("href")).toBe("/examples");
    expect(screen.queryByText("Try real tasks with your local model")).toBeNull();
    expect(document.querySelector(".app-shell-workspace")).toBeTruthy();
  });

  it("restores catalog/workspace with Back and Forward while preserving messages and drafts", async () => {
    window.history.replaceState(null, "", "/examples");
    render(<App authClient={authClient()} />);
    await signIn();
    fireEvent.click(screen.getByRole("link", { name: "Open Chat + Voice" }));
    expect(window.location.pathname).toBe("/examples/chat-voice");
    fireEvent.change(screen.getByRole("textbox", { name: "Message" }), { target: { value: "Remember me" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await screen.findByText("A remembered reply");
    fireEvent.change(screen.getByRole("textbox", { name: "Message" }), { target: { value: "Unsent draft" } });
    await act(async () => window.history.back());
    await screen.findByText("Try real tasks with your local model");
    expect(window.location.pathname).toBe("/examples");
    await act(async () => window.history.forward());
    await screen.findByRole("link", { name: "Return to Examples" });
    expect(window.location.pathname).toBe("/examples/chat-voice");
    expect(screen.getByText("Remember me")).toBeTruthy();
    expect(screen.getByText("A remembered reply")).toBeTruthy();
    expect((screen.getByRole("textbox", { name: "Message" }) as HTMLTextAreaElement).value).toBe("Unsent draft");
    fireEvent.click(screen.getByRole("link", { name: "Return to Examples" }));
    expect(window.location.pathname).toBe("/examples");
    fireEvent.click(screen.getByRole("button", { name: /Overview/ }));
    expect(window.location.pathname).toBe("/");
    await act(async () => window.history.back());
    await waitFor(() => expect(window.location.pathname).toBe("/examples"));
    expect(await screen.findByText("Try real tasks with your local model")).toBeTruthy();
  });

  it("loads a stopped workspace with lifecycle controls and no automatic start", async () => {
    window.history.replaceState(null, "", "/examples/chat-voice");
    apiMocks.getExamples.mockResolvedValue([{ ...readyStatus, state: "stopped" }]);
    render(<App authClient={authClient()} />);
    fireEvent.change(await screen.findByLabelText("Administrator password"), { target: { value: "Password123" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Stopped")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Start example" }).hasAttribute("disabled")).toBe(false);
    expect(screen.getByRole("link", { name: "Return to Examples" })).toBeTruthy();
    expect(screen.queryByRole("textbox", { name: "Message" })).toBeNull();
  });
});
