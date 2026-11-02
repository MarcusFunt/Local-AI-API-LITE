from __future__ import annotations

from pathlib import Path

from conftest import AsgiTestClient, load_dashboard_module


class SettingsStub:
    def load_compose_root(self):
        return Path("C:/configured/narrator")


class RetrievalCoordinatorStub:
    def __init__(self, configured=True):
        self.is_configured = configured
        self.calls = []

    def configured(self):
        return self.is_configured

    def state(self):
        return {"configured": self.is_configured, "services": {}}

    def start(self, service, *, actor_session_id):
        self.calls.append(("start", service, actor_session_id))
        return {"operation_id": "retrieval-op", "service": service, "phase": "succeeded"}

    def stop(self, service, *, actor_session_id):
        self.calls.append(("stop", service, actor_session_id))
        return {"operation_id": "retrieval-op", "service": service, "phase": "succeeded"}

    def restart(self, service, *, actor_session_id):
        self.calls.append(("restart", service, actor_session_id))
        return {"operation_id": "retrieval-op", "service": service, "phase": "succeeded"}


def make_client(tmp_path: Path, *, configured=True):
    api = load_dashboard_module("api")
    coordinator = RetrievalCoordinatorStub(configured)
    app = api.create_app(
        metadata_path=tmp_path / "metadata.sqlite3",
        frontend_dist=tmp_path / "frontend",
        settings_store=SettingsStub(),
        operation_coordinator=object(),
        retrieval_operation_coordinator=coordinator,
    )
    client = AsgiTestClient(app)
    return client, coordinator


def test_retrieval_lifecycle_requires_authentication_csrf_and_explicit_confirmation(tmp_path):
    client, coordinator = make_client(tmp_path)

    assert client.post("/api/control/v1/retrieval-operations/embed/start", json_body={"confirm": True}).status_code == 401
    client.setup_admin()
    assert client.post("/api/control/v1/retrieval-operations/embed/start", json_body={"confirm": True}).status_code == 403
    missing_confirmation = client.post(
        "/api/control/v1/retrieval-operations/embed/start",
        headers={"X-CSRF-Token": client.csrf_token or ""},
    )
    invalid_confirmation = client.post(
        "/api/control/v1/retrieval-operations/embed/start",
        json_body={"confirm": False},
        headers={"X-CSRF-Token": client.csrf_token or ""},
    )

    assert missing_confirmation.status_code == 422
    assert invalid_confirmation.status_code == 422
    assert coordinator.calls == []


def test_confirmed_route_accepts_only_fixed_service_and_records_actor_session(tmp_path):
    client, coordinator = make_client(tmp_path)
    client.setup_admin()
    headers = {"X-CSRF-Token": client.csrf_token or ""}

    response = client.post(
        "/api/control/v1/retrieval-operations/rerank/restart",
        json_body={"confirm": True},
        headers=headers,
    )
    arbitrary = client.post(
        "/api/control/v1/retrieval-operations/vllm/start",
        json_body={"confirm": True},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["service"] == "rerank"
    assert arbitrary.status_code == 422
    assert len(coordinator.calls) == 1
    assert coordinator.calls[0][0:2] == ("restart", "rerank")
    assert coordinator.calls[0][2]


def test_unconfigured_retrieval_compose_refuses_operations(tmp_path):
    client, coordinator = make_client(tmp_path, configured=False)
    client.setup_admin()

    response = client.post(
        "/api/control/v1/retrieval-operations/embed/stop",
        json_body={"confirm": True},
        headers={"X-CSRF-Token": client.csrf_token or ""},
    )

    assert response.status_code == 409
    assert coordinator.calls == []
