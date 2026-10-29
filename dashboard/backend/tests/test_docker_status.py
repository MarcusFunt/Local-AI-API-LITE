from __future__ import annotations

import json
import subprocess

from conftest import load_dashboard_module


def _completed(stdout: str, returncode: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _inspect_record(*, health: str = "healthy", state: str = "running") -> dict:
    logs = [
        {
            "Start": "2026-10-28T19:40:17Z",
            "End": "2026-10-28T19:40:18Z",
            "ExitCode": 0 if health == "healthy" else 1,
        }
    ]
    if health != "healthy":
        logs.insert(
            0,
            {
                "Start": "2026-10-28T19:39:47Z",
                "End": "2026-10-28T19:39:48Z",
                "ExitCode": 0,
            },
        )
    return {
        "Id": "abcdef123456",
        "Name": "/local-ai-api-lite-vllm-1",
        "State": {
            "Status": state,
            "Running": state == "running",
            "ExitCode": 0 if state != "exited" else 1,
            "Error": "",
            "StartedAt": "2026-10-28T17:26:57Z",
            "Health": {
                "Status": health,
                "Log": logs,
            },
        },
        "Config": {
            "Image": "vllm/vllm-openai:v0.32.0@sha256:befccf0e7e4089aa23926bb6f887a85c0d46c2a99e6de5c5ffdf5cb7957c5b34",
            "Cmd": [
                "RedHatAI/Qwen3.5-4B-quantized.w4a16",
                "--revision",
                "7a613872f394578b0b52b683ff4ac47516b4bcaf",
                "--served-model-name",
                "ddu-narrator",
                "--reasoning-parser",
                "qwen3",
                "--max-model-len",
                "8192",
                "--max-num-seqs",
                "4",
                "--gpu-memory-utilization",
                "0.75",
            ],
        },
        "RestartCount": 0,
        "NetworkSettings": {"Ports": {"8080/tcp": None}},
    }


def test_healthy_container_reports_readiness_runtime_profile_and_container_metrics():
    docker_status = load_dashboard_module("docker_status")
    calls = []

    def run(args, **_kwargs):
        calls.append(args)
        if args[1] == "ps":
            return _completed("abcdef123456\n")
        if args[1] == "inspect":
            return _completed(json.dumps([_inspect_record()]))
        if args[1] == "stats":
            return _completed(json.dumps({"CPUPerc": "1.14%", "MemUsage": "3.4GiB / 29GiB"}))
        raise AssertionError(f"unexpected Docker command: {args[1]}")

    result = docker_status.DockerReader(docker_path="docker", run=run).read()

    assert result["inference_state"] == "Ready"
    assert result["configuration"]["model_alias"] == "ddu-narrator"
    assert result["configuration"]["reasoning_parser"] == "qwen3"
    assert result["active_profile"] == "baseline-8k-4"
    assert result["container"]["cpu_percent"] == 1.14
    assert result["container"]["memory_used_bytes"] > 0
    assert result["container"]["host_published_ports"] == []
    assert all(command[1] in {"ps", "inspect", "stats"} for command in calls)


def test_docker_unavailable_is_unknown_not_stopped():
    docker_status = load_dashboard_module("docker_status")

    result = docker_status.DockerReader(docker_path=None, run=lambda *_args, **_kwargs: None).read()

    assert result["inference_state"] == "Unknown"
    assert result["availability"] == "unavailable"
    assert result["container"] is None


def test_running_container_without_a_healthy_probe_is_degraded():
    docker_status = load_dashboard_module("docker_status")

    def run(args, **_kwargs):
        if args[1] == "ps":
            return _completed("abcdef123456\n")
        if args[1] == "inspect":
            return _completed(json.dumps([_inspect_record(health="unhealthy")]))
        if args[1] == "stats":
            return _completed("")
        raise AssertionError(f"unexpected Docker command: {args[1]}")

    result = docker_status.DockerReader(docker_path="docker", run=run).read()

    assert result["inference_state"] == "Degraded"
    assert result["last_failure_reason"] == "Container healthcheck failed (exit code 1)"
    assert result.get("last_health_probe_at") == "2026-10-28T19:40:18Z"
    assert result.get("last_successful_health_probe_at") == "2026-10-28T19:39:48Z"


def test_successful_docker_query_with_no_container_reports_stopped():
    docker_status = load_dashboard_module("docker_status")
    result = docker_status.DockerReader(
        docker_path="docker",
        run=lambda *_args, **_kwargs: _completed(""),
    ).read()

    assert result["availability"] == "available"
    assert result["inference_state"] == "Stopped"
    assert result["container"] is None


def test_invalid_profile_registry_does_not_hide_a_healthy_container(monkeypatch, tmp_path):
    profiles = load_dashboard_module("profiles")
    docker_status = load_dashboard_module("docker_status")
    registry = tmp_path / "profiles.yaml"
    registry.write_text("version: 1\nprofiles: {}\n", encoding="utf-8")
    monkeypatch.setattr(profiles, "DEFAULT_PROFILES_PATH", registry)

    def run(args, **_kwargs):
        if args[1] == "ps":
            return _completed("abcdef123456\n")
        if args[1] == "inspect":
            return _completed(json.dumps([_inspect_record()]))
        if args[1] == "stats":
            return _completed("")
        raise AssertionError(f"unexpected Docker command: {args[1]}")

    result = docker_status.DockerReader(docker_path="docker", run=run).read()

    assert result["inference_state"] == "Ready"
    assert result["active_profile"] is None
