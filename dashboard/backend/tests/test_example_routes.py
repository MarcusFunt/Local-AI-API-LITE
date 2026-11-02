from __future__ import annotations

from conftest import AsgiTestClient, load_dashboard_module

from app.example_runner import ExampleDependencyUnavailable, ExampleDockerUnavailable
from app.examples import ExampleDependencyStatus, ExamplePublicStatus


class FakeExampleRunner:
    def __init__(self, *, chat="ready", voice="unavailable", error=None):
        self.status = ExamplePublicStatus(
            chat_dependency=ExampleDependencyStatus(state=chat, active_profile="baseline-8k-4"),
            voice_dependency=ExampleDependencyStatus(state=voice),
            missing_dependencies=[] if voice == "ready" else ["ddu-stt", "ddu-tts"],
        )
        self.error = error
        self.stop_state = "stopping"

    def list_status(self):
        return [self.status]

    def start(self, example_id):
        if self.error is not None:
            raise self.error
        return self.status.model_copy(update={"state": "starting"})

    def stop(self, example_id):
        return self.status.model_copy(update={"state": self.stop_state})

    def reconcile(self):
        return [self.status]


def _client(tmp_path, runner):
    api = load_dashboard_module("api")
    return AsgiTestClient(
        api.create_app(metadata_path=tmp_path / "metadata.sqlite3", example_runner=runner)
    )


def test_example_status_requires_an_administrator_session(tmp_path):
    client = _client(tmp_path, FakeExampleRunner())

    response = client.get("/api/control/v1/examples")

    assert response.status_code == 401


def test_start_and_stop_require_csrf_and_reject_extra_body_fields(tmp_path):
    client = _client(tmp_path, FakeExampleRunner())
    client.setup_admin()

    missing_token = client.post("/api/control/v1/examples/chat-voice/start")
    extra_field = client.post(
        "/api/control/v1/examples/chat-voice/start",
        json_body={"service": "other"},
        headers={"X-CSRF-Token": client.csrf_token},
    )
    stop_without_token = client.post("/api/control/v1/examples/chat-voice/stop")

    assert missing_token.status_code == 403
    assert extra_field.status_code == 422
    assert stop_without_token.status_code == 403


def test_speech_unavailable_status_still_allows_chat_example_start(tmp_path):
    runner = FakeExampleRunner(chat="ready", voice="unavailable")
    client = _client(tmp_path, runner)
    client.setup_admin()

    status = client.get("/api/control/v1/examples")
    started = client.post(
        "/api/control/v1/examples/chat-voice/start",
        headers={"X-CSRF-Token": client.csrf_token},
    )

    assert status.status_code == 200
    assert status.json()["examples"][0]["voice_dependency"]["state"] == "unavailable"
    assert started.status_code == 202
    assert started.json()["state"] == "starting"


def test_missing_narrator_returns_conflict(tmp_path):
    runner = FakeExampleRunner(chat="stopped", voice="ready", error=ExampleDependencyUnavailable())
    client = _client(tmp_path, runner)
    client.setup_admin()

    response = client.post(
        "/api/control/v1/examples/chat-voice/start",
        headers={"X-CSRF-Token": client.csrf_token},
    )

    assert response.status_code == 409
    assert "narrator" in response.json()["detail"].lower()


def test_docker_errors_are_sanitized_and_map_to_service_unavailable(tmp_path):
    runner = FakeExampleRunner(
        error=ExampleDockerUnavailable("C:/Users/operator/private/build-output")
    )
    client = _client(tmp_path, runner)
    client.setup_admin()

    response = client.post(
        "/api/control/v1/examples/chat-voice/start",
        headers={"X-CSRF-Token": client.csrf_token},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Docker is unavailable for the Chat + Voice example"}
    assert "private" not in response.text


def test_stop_returns_accepted_state_and_requires_csrf(tmp_path):
    client = _client(tmp_path, FakeExampleRunner())
    client.setup_admin()

    response = client.post(
        "/api/control/v1/examples/chat-voice/stop",
        headers={"X-CSRF-Token": client.csrf_token},
    )

    assert response.status_code == 202
    assert response.json()["state"] == "stopping"


def test_stop_returns_ok_after_docker_confirms_exit(tmp_path):
    runner = FakeExampleRunner()
    runner.stop_state = "stopped"
    client = _client(tmp_path, runner)
    client.setup_admin()

    response = client.post(
        "/api/control/v1/examples/chat-voice/stop",
        headers={"X-CSRF-Token": client.csrf_token},
    )

    assert response.status_code == 200
    assert response.json()["state"] == "stopped"
