from __future__ import annotations

import json
import subprocess

from conftest import load_dashboard_module


def _completed(stdout: str, returncode: int = 0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def _record(spec, *, container_state="running", health="healthy"):
    return {
        "Id": "abcdef123456",
        "Name": f"/local-ai-api-lite-speech-{spec['service']}-1",
        "Image": "sha256:speech-image-id",
        "State": {
            "Status": container_state,
            "ExitCode": 0,
            "StartedAt": "2026-10-31T10:00:00Z",
            "Health": {"Status": health, "Log": []},
        },
        "Config": {
            "Image": "local-ai-api-lite-speech:0.1.0",
            "Labels": {
                "com.docker.compose.project": spec["project_name"],
                "com.docker.compose.service": spec["service"],
            },
        },
        "RestartCount": 0,
        "HostConfig": {"Privileged": False, "DeviceRequests": None},
        "NetworkSettings": {
            "Ports": {"8080/tcp": None},
            "Networks": {"ddu_inference_bus": {"Aliases": [spec["alias"]]}},
        },
        "Mounts": [{"Type": "bind", "Destination": "/models", "RW": False}],
    }


def _probe(spec, *, ready_status=200, ready_state="ready", model_id=None, revision=None):
    return {
        "health_status": 200,
        "ready_status": ready_status,
        "ready_state": ready_state,
        "models_status": 200,
        "models": [{
            "id": model_id or spec["alias"],
            "model_id": spec["model_id"],
            "revision": revision or spec["revision"],
        }],
        "metrics_status": 200,
        "metrics": {
            "accepted_total": 4,
            "completed_total": 3,
            "failed_total": 1,
            "timed_out_total": 1,
            "rejected_total": 0,
            "active_requests": 0,
            "queued_requests": 0,
            "outstanding_requests": 0,
            "max_outstanding": 2,
            "saturated": False,
            "stuck_inference": False,
            "closed": False,
            "last_successful_inference_at": "2026-10-31T10:01:00Z",
        },
    }


def _reader(service, *, probe=None, stats=None, state="running"):
    module = load_dashboard_module("speech_status")
    spec = module.SERVICE_SPECS[service]

    def run(args, **_kwargs):
        if args[1] == "ps":
            return _completed("abcdef123456\n")
        if args[1] == "inspect":
            return _completed(json.dumps([_record(spec, container_state=state)]))
        if args[1] == "stats":
            return _completed(stats if stats is not None else json.dumps({"CPUPerc": "0.0%", "MemUsage": "0B / 4GiB"}))
        if args[1] == "exec":
            return _completed(json.dumps(probe or _probe(spec)))
        raise AssertionError(f"unexpected Docker command: {args[1]}")

    return module.SpeechServiceDockerReader(
        docker_path="docker",
        run=run,
        wall_clock=lambda: "2026-10-31T10:02:00Z",
    )


def test_ready_speech_service_exposes_revision_resources_checks_and_counters():
    reader = _reader("stt")

    result = reader.read_service("stt")

    assert result["state"] == "Ready"
    assert result["alias"] == "ddu-stt"
    assert result["model_revision"] == "536b0662742c02347bc0e980a01041f333bce120"
    assert result["container"]["state"] == "Running"
    assert result["container"]["cpu_percent"] == 0.0
    assert result["container"]["memory_used_bytes"] == 0
    assert result["last_successful_readiness_check_at"] == "2026-10-31T10:02:00Z"
    assert result["metrics"]["timed_out_total"] == 1
    assert result["metrics"]["last_successful_inference_at"] == "2026-10-31T10:01:00Z"
    assert result["observed_at"] == "2026-10-31T10:02:00Z"


def test_tts_worker_with_approved_bilingual_revision_is_ready():
    module = load_dashboard_module("speech_status")
    spec = module.SERVICE_SPECS["tts"]
    revision = "375a0fe641dea077c2a47b4e9a056d6da521eed3"
    reader = _reader("tts", probe=_probe(spec, revision=revision))

    result = reader.read_service("tts")

    assert result["state"] == "Ready"
    assert result["model_revision"] == revision
    assert result["expected_model_revision"] == revision
    assert result["model_repository"] == (
        "rhasspy/piper-voices · da: da_DK-talesyntese-medium; en: en_US-ljspeech-medium"
    )
    assert result["last_error_category"] is None


def test_speech_worker_revision_mismatch_is_degraded():
    module = load_dashboard_module("speech_status")
    spec = module.SERVICE_SPECS["tts"]
    reader = _reader("tts", probe=_probe(spec, revision="unexpected"))

    result = reader.read_service("tts")

    assert result["state"] == "Degraded"
    assert result["availability"] == "available"
    assert result["last_error_category"] == "model_identity_mismatch"


def test_docker_timeout_is_unknown_and_does_not_become_zero_or_healthy():
    module = load_dashboard_module("speech_status")

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["docker", "ps"], timeout=1)

    reader = module.SpeechServiceDockerReader(docker_path="docker", run=timeout)
    result = reader.read_service("stt")

    assert result["state"] == "Unknown/Unavailable"
    assert result["availability"] == "unavailable"
    assert result["last_error_category"] == "command_timeout"
    assert result["observed_at"] is not None
    assert result["container"] is None
    assert result["metrics"] is None


def test_speech_timeout_keeps_last_successful_readiness_time():
    reader = _reader("tts")
    first = reader.read_service("tts")
    reader._run = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        subprocess.TimeoutExpired(["docker", "ps"], timeout=1)
    )

    result = reader.read_service("tts")

    assert first["last_successful_readiness_check_at"] == "2026-10-31T10:02:00Z"
    assert result["state"] == "Unknown/Unavailable"
    assert result["last_successful_readiness_check_at"] == first["last_successful_readiness_check_at"]
    assert result["observed_at"] is not None


def test_missing_stats_remain_unavailable_while_zero_stats_remain_zero():
    reader = _reader("tts", stats="not-json")

    result = reader.read_service("tts")

    assert result["state"] == "Ready"
    assert result["container"]["cpu_percent"] is None
    assert result["container"]["memory_used_bytes"] is None
    assert result["container"]["memory_limit_bytes"] is None
