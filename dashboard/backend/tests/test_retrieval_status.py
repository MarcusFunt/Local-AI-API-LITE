from __future__ import annotations

import json
import subprocess

from conftest import load_dashboard_module


def _completed(stdout: str, returncode: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _inspect_record(spec, *, health: str = "healthy", state: str = "running") -> dict:
    return {
        "Id": "abcdef123456",
        "Name": f"/{spec['project_name']}-{spec['service']}-1",
        "Image": "sha256:container-image-id",
        "State": {
            "Status": state,
            "ExitCode": 0,
            "StartedAt": "2026-10-31T10:00:00Z",
            "Health": {"Status": health, "Log": []},
        },
        "Config": {
            "Image": "local-ai-api-lite-retrieval:0.1.0",
            "Labels": {
                "com.docker.compose.project": spec["project_name"],
                "com.docker.compose.service": spec["service"],
            },
        },
        "RestartCount": 0,
        "NetworkSettings": {
            "Ports": {"8080/tcp": None},
            "Networks": {"ddu_inference_bus": {"Aliases": [spec["alias"]]}},
        },
        "Mounts": [
            {
                "Type": "bind",
                "Source": "C:/Users/test/.cache/retrieval",
                "Destination": "/models",
                "RW": False,
            }
        ],
    }


def _probe(spec, *, ready_status: int = 200, ready_state: str = "ready", model_id=None, revision=None):
    return {
        "health_status": 200,
        "ready_status": ready_status,
        "ready_state": ready_state,
        "models_status": 200,
        "models": [
            {
                "id": model_id or spec["alias"],
                "revision": revision or spec["revision"],
            }
        ],
        "metrics_status": 200,
        "metrics": {
            "accepted_total": 2,
            "completed_total": 2,
            "failed_total": 0,
            "timed_out_total": 0,
            "rejected_total": 0,
            "active_requests": 0,
            "queued_requests": 0,
            "outstanding_requests": 0,
            "max_outstanding": 2,
            "saturated": False,
            "stuck_inference": False,
        },
    }


def _reader_for(service: str, probe: dict | None = None, *, state: str = "running", health: str = "healthy"):
    retrieval_status = load_dashboard_module("retrieval_status")
    spec = retrieval_status.SERVICE_SPECS[service]

    def run(args, **_kwargs):
        if args[1] == "ps":
            return _completed("abcdef123456\n" if f"label=com.docker.compose.service={service}" in args else "")
        if args[1] == "inspect":
            return _completed(json.dumps([_inspect_record(spec, health=health, state=state)]))
        if args[1] == "stats":
            return _completed(json.dumps({"CPUPerc": "12.5%", "MemUsage": "1.5GiB / 4GiB"}))
        if args[1] == "exec":
            return _completed(json.dumps(probe or _probe(spec)))
        raise AssertionError(f"unexpected Docker command: {args[1]}")

    return retrieval_status.RetrievalServiceDockerReader(docker_path="docker", run=run)


def test_ready_retrieval_worker_reports_pinned_identity_and_resources():
    reader = _reader_for("embed")
    spec = load_dashboard_module("retrieval_status").SERVICE_SPECS["embed"]

    result = reader.read_service("embed")

    assert result["state"] == "Ready"
    assert result["availability"] == "available"
    assert result["alias"] == "ddu-embed"
    assert result["project_name"] == "local-ai-api-lite-retrieval"
    assert result["model_id"] == spec["alias"]
    assert result["model_revision"] == spec["revision"]
    assert result["expected_model_revision"] == spec["revision"]
    assert result["image_ref"] == "local-ai-api-lite-retrieval:0.1.0"
    assert result["container"]["cpu_percent"] == 12.5
    assert result["container"]["memory_used_bytes"] > 0
    assert result["last_successful_readiness_check_at"]
    assert result["last_error_category"] is None
    assert result["metrics"]["completed_total"] == 2


def test_worker_is_not_ready_when_live_model_identity_differs_from_the_pin():
    retrieval_status = load_dashboard_module("retrieval_status")
    spec = retrieval_status.SERVICE_SPECS["rerank"]
    reader = _reader_for("rerank", _probe(spec, model_id="unexpected-model"))

    result = reader.read_service("rerank")

    assert result["state"] == "Degraded"
    assert result["last_error_category"] == "model_identity_mismatch"
    assert result["model_id"] == "unexpected-model"


def test_loading_worker_is_starting_even_when_container_liveness_is_healthy():
    retrieval_status = load_dashboard_module("retrieval_status")
    spec = retrieval_status.SERVICE_SPECS["embed"]
    reader = _reader_for("embed", _probe(spec, ready_status=503, ready_state="loading"))

    result = reader.read_service("embed")

    assert result["state"] == "Starting/Loading"
    assert result["last_error_category"] == "model_loading"
    assert result["last_successful_readiness_check_at"] is None


def test_stopped_worker_is_reported_without_http_probe():
    reader = _reader_for("rerank", state="exited")

    result = reader.read_service("rerank")

    assert result["state"] == "Stopped"
    assert result["availability"] == "available"
    assert result["last_error_category"] is None


def test_docker_unavailable_is_unknown_not_stopped():
    retrieval_status = load_dashboard_module("retrieval_status")

    result = retrieval_status.RetrievalServiceDockerReader(docker_path=None).read_service("embed")

    assert result["state"] == "Unknown/Unavailable"
    assert result["availability"] == "unavailable"
    assert result["last_error_category"] == "docker_unavailable"


def test_probe_timeout_keeps_last_success_and_records_unknown_observation():
    reader = _reader_for("embed")
    first = reader.read_service("embed")
    reader._run = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        subprocess.TimeoutExpired(["docker", "ps"], timeout=1)
    )

    result = reader.read_service("embed")

    assert first["last_successful_readiness_check_at"]
    assert result["state"] == "Unknown/Unavailable"
    assert result["availability"] == "unavailable"
    assert result["last_error_category"] == "command_timeout"
    assert result["last_successful_readiness_check_at"] == first["last_successful_readiness_check_at"]
    assert result["observed_at"] is not None
    assert result["container"] is None
    assert result["metrics"] is None


def test_worker_with_wrong_image_is_degraded_without_http_probe():
    retrieval_status = load_dashboard_module("retrieval_status")
    spec = retrieval_status.SERVICE_SPECS["embed"]
    calls = []

    def run(args, **_kwargs):
        calls.append(args[1])
        if args[1] == "ps":
            return _completed("abcdef123456\n")
        if args[1] == "inspect":
            record = _inspect_record(spec)
            record["Config"]["Image"] = "untrusted/image:latest"
            return _completed(json.dumps([record]))
        if args[1] == "stats":
            return _completed(json.dumps({"CPUPerc": "1%", "MemUsage": "1GiB / 4GiB"}))
        raise AssertionError("wrong-image containers must not be probed")

    result = retrieval_status.RetrievalServiceDockerReader(run=run).read_service("embed")

    assert result["state"] == "Degraded"
    assert result["last_error_category"] == "image_identity_mismatch"
    assert "exec" not in calls


def test_http_liveness_failure_is_separate_from_model_readiness():
    retrieval_status = load_dashboard_module("retrieval_status")
    spec = retrieval_status.SERVICE_SPECS["rerank"]
    reader = _reader_for("rerank", {**_probe(spec), "health_status": 0})

    result = reader.read_service("rerank")

    assert result["state"] == "Degraded"
    assert result["last_error_category"] == "liveness_check_failed"
