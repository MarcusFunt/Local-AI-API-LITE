from __future__ import annotations

from pathlib import Path

from conftest import AsgiTestClient, load_dashboard_module


class SettingsStub:
    def __init__(self, root: Path | None) -> None:
        self.root = root

    def load_compose_root(self) -> Path | None:
        return self.root


class ControlStateStub:
    def __init__(self) -> None:
        self.state = {
            "desired_state": "stopped",
            "selected_profile_id": None,
            "active_profile_id": None,
            "last_known_good_profile_id": None,
        }

    def get_control_state(self):
        return self.state

    def latest_operation(self):
        return None


class CoordinatorStub:
    def __init__(self) -> None:
        self.control_state = ControlStateStub()
        self.calls: list[tuple[str, str | None]] = []

    def start(self, profile_id: str):
        self.calls.append(("start", profile_id))
        return {"operation_id": "op-start", "phase": "succeeded", "outcome_category": "success"}

    def stop(self):
        self.calls.append(("stop", None))
        return {"operation_id": "op-stop", "phase": "succeeded", "outcome_category": "success"}

    def restart(self):
        self.calls.append(("restart", None))
        return {"operation_id": "op-restart", "phase": "succeeded", "outcome_category": "success"}

    def apply_profile(self, profile_id: str):
        self.calls.append(("apply_profile", profile_id))
        return {"operation_id": "op-profile", "phase": "configured", "outcome_category": "configured_while_stopped"}


def make_client(tmp_path: Path, *, configured: bool = True):
    api = load_dashboard_module("api")
    settings = SettingsStub(tmp_path if configured else None)
    coordinator = CoordinatorStub()
    app = api.create_app(
        metadata_path=tmp_path / "dashboard.sqlite3",
        frontend_dist=tmp_path / "frontend",
        settings_store=settings,
        operation_coordinator=coordinator,
    )
    client = AsgiTestClient(app)
    client.setup_admin()
    return client, coordinator


def test_mutation_requires_session_and_csrf(tmp_path):
    api = load_dashboard_module("api")
    settings = SettingsStub(tmp_path)
    coordinator = CoordinatorStub()
    app = api.create_app(
        metadata_path=tmp_path / "dashboard.sqlite3",
        frontend_dist=tmp_path / "frontend",
        settings_store=settings,
        operation_coordinator=coordinator,
    )
    anonymous = AsgiTestClient(app)

    assert anonymous.post(
        "/api/control/v1/operations/start",
        json_body={"profile_id": "baseline-4k-2"},
    ).status_code == 401

    client = AsgiTestClient(app)
    client.setup_admin()
    assert client.post(
        "/api/control/v1/operations/start",
        json_body={"profile_id": "baseline-4k-2"},
    ).status_code == 403

    response = client.post(
        "/api/control/v1/operations/start",
        json_body={"profile_id": "baseline-4k-2"},
        headers={"X-CSRF-Token": client.csrf_token or ""},
    )
    assert response.status_code == 200
    assert coordinator.calls == [("start", "baseline-4k-2")]


def test_operation_rejects_unconfigured_compose_root(tmp_path):
    client, coordinator = make_client(tmp_path, configured=False)

    response = client.post(
        "/api/control/v1/operations/start",
        json_body={"profile_id": "baseline-4k-2"},
        headers={"X-CSRF-Token": client.csrf_token or ""},
    )

    assert response.status_code == 409
    assert coordinator.calls == []


def test_operation_body_accepts_only_a_fixed_profile_id(tmp_path):
    client, coordinator = make_client(tmp_path)

    unknown = client.post(
        "/api/control/v1/operations/start",
        json_body={"profile_id": "experimental"},
        headers={"X-CSRF-Token": client.csrf_token or ""},
    )
    arbitrary = client.post(
        "/api/control/v1/operations/apply-profile",
        json_body={"profile_id": "baseline-8k-4", "command": "docker compose down"},
        headers={"X-CSRF-Token": client.csrf_token or ""},
    )

    assert unknown.status_code == 422
    assert arbitrary.status_code == 422
    assert coordinator.calls == []


def test_operation_state_is_session_protected_and_keeps_desired_stop(tmp_path):
    client, _coordinator = make_client(tmp_path)

    response = client.get("/api/control/v1/operations/state")

    assert response.status_code == 200
    assert response.json()["control_state"]["desired_state"] == "stopped"
    assert response.json()["configured"] is True
