from __future__ import annotations

import subprocess

from conftest import AsgiTestClient, load_dashboard_module

from app.api import create_app


def _completed(stdout: str = "", returncode: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_log_tail_limit_is_enforced():
    logs = load_dashboard_module("logs")
    calls = []

    def run(args, **_kwargs):
        calls.append(args)
        if args[1] == "ps":
            return _completed("a" * 64 + "\n")
        if args[1] == "inspect":
            return _completed("[]")
        if args[1] == "logs":
            return _completed("line one\nline two\n")
        raise AssertionError(f"unexpected Docker command: {args[1]}")

    result = logs.DockerLogReader(docker_path="docker", run=run, max_tail=40).read(tail=10000)

    log_call = next(args for args in calls if args[1] == "logs")
    assert log_call[log_call.index("--tail") + 1] == "40"
    assert result["lines"] == ["line one", "line two"]


def test_credentials_env_values_and_paths_are_redacted():
    logs = load_dashboard_module("logs")

    def run(args, **_kwargs):
        if args[1] == "ps":
            return _completed("a" * 64 + "\n")
        if args[1] == "inspect":
            return _completed('["API_TOKEN=secret-token-value","DATABASE_PASSWORD=hidden-password-value"]')
        if args[1] == "logs":
            return _completed(
                "connecting with secret-token-value\n"
                "DATABASE_PASSWORD=hidden-password-value\n"
                "loaded C:\\Users\\operator\\model-cache\\weights.bin\n"
                "opened /root/.cache/huggingface/tokenizer.json\n"
            )
        raise AssertionError(f"unexpected Docker command: {args[1]}")

    result = logs.DockerLogReader(docker_path="docker", run=run).read()

    rendered = "\n".join(result["lines"])
    assert "secret-token-value" not in rendered
    assert "hidden-password-value" not in rendered
    assert "C:\\Users\\operator" not in rendered
    assert "/root/.cache" not in rendered
    assert "[redacted]" in rendered


def test_prompt_like_content_is_not_stored_or_returned(tmp_path, monkeypatch):
    logs = load_dashboard_module("logs")
    monkeypatch.setenv("DASHBOARD_DATA_DIR", str(tmp_path))

    def run(args, **_kwargs):
        if args[1] == "ps":
            return _completed("a" * 64 + "\n")
        if args[1] == "inspect":
            return _completed("[]")
        if args[1] == "logs":
            return _completed(
                'server ready\nrequest prompt: SECRET_PROMPT_BODY\n'
                '{"prompt":"SECRET_JSON_PROMPT"}\nrequest completed\n'
            )
        raise AssertionError(f"unexpected Docker command: {args[1]}")

    result = logs.DockerLogReader(docker_path="docker", run=run).read()

    assert "SECRET_PROMPT_BODY" not in str(result)
    assert "SECRET_JSON_PROMPT" not in str(result)
    assert "SECRET_PROMPT_BODY" not in " ".join(path.read_text(errors="ignore") for path in tmp_path.rglob("*.*"))
    assert result["lines"] == ["[redacted content-bearing log output]"]


def test_docker_unavailable_returns_sanitized_category():
    logs = load_dashboard_module("logs")

    def fail(*_args, **_kwargs):
        raise OSError("private docker pipe details")

    result = logs.DockerLogReader(docker_path="docker", run=fail).read()

    assert result["availability"] == "unavailable"
    assert result["error_category"] == "docker_unavailable"
    assert "private docker pipe details" not in str(result)


def test_logs_route_requires_admin_session(tmp_path):
    logs = load_dashboard_module("logs")

    class StubLogReader:
        def read(self, *, tail=100):
            return {"availability": "available", "lines": ["ready"], "error_category": None}

    client = AsgiTestClient(
        create_app(
            metadata_path=tmp_path / "metadata.sqlite3",
            frontend_dist=tmp_path / "missing-dist",
            operation_coordinator=object(),
            job_runner=object(),
            log_reader=StubLogReader(),
        )
    )

    assert client.get("/api/control/v1/logs").status_code == 401
    client.setup_admin()
    response = client.get("/api/control/v1/logs?tail=25")

    assert response.status_code == 200
    assert response.json()["lines"] == ["ready"]
    assert logs.MAX_LOG_TAIL >= 25
