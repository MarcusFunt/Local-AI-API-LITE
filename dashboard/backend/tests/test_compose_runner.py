from __future__ import annotations

import json
import subprocess
from pathlib import Path

from conftest import load_dashboard_module

PROJECT_NAME = "local-ai-api-lite"
IMAGE_REF = "vllm/vllm-openai:v0.32.0@sha256:befccf0e7e4089aa23926bb6f887a85c0d46c2a99e6de5c5ffdf5cb7957c5b34"
CACHE_DESTINATION = "/root/.cache/huggingface"


def _rendered_config(cache_source: Path) -> dict:
    return {
        "name": PROJECT_NAME,
        "services": {
            "vllm": {
                "image": IMAGE_REF,
                "volumes": [
                    {"type": "bind", "source": str(cache_source), "target": CACHE_DESTINATION}
                ],
                "networks": {"inference": {"aliases": ["ddu-llm"]}},
            }
        },
        "networks": {
            "inference": {"name": "ddu_inference_bus", "external": True}
        },
    }


def _runtime_container(cache_source: Path, *, project: str = PROJECT_NAME, service: str = "vllm") -> dict:
    return {
        "Id": "abcdef1234567890",
        "Name": "/local-ai-api-lite-vllm-1",
        "Config": {
            "Image": IMAGE_REF,
            "Labels": {
                "com.docker.compose.project": project,
                "com.docker.compose.service": service,
            },
        },
        "State": {"Status": "running"},
        "NetworkSettings": {
            "Networks": {"ddu_inference_bus": {"Aliases": ["local-ai-api-lite-vllm-1", "ddu-llm"]}},
            "Ports": {"8080/tcp": None},
        },
        "Mounts": [
            {"Type": "bind", "Source": str(cache_source), "Destination": CACHE_DESTINATION}
        ],
    }


def _project_root(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "compose-root"
    root.mkdir()
    cache_source = tmp_path / "model-cache"
    cache_source.mkdir()
    (root / "compose.yaml").write_text(
        f"""name: local-ai-api-lite
services:
  vllm:
    image: {IMAGE_REF}
    volumes:
      - ${{HF_CACHE_DIR:?set HF_CACHE_DIR}}:{CACHE_DESTINATION}
    networks:
      inference:
        aliases: [ddu-llm]
networks:
  inference:
    external: true
    name: ddu_inference_bus
""",
        encoding="utf-8",
    )
    (root / ".env").write_text(f"HF_CACHE_DIR={cache_source}\n", encoding="utf-8")
    return root, cache_source


def test_runtime_project_matches_expected_labels_and_network(tmp_path):
    compose_runner = load_dashboard_module("compose_runner")
    root, cache_source = _project_root(tmp_path)
    commands: list[list[str]] = []

    def run(args, **_kwargs):
        commands.append(args)
        if args[-2:] == ["config", "--format"] or "config" in args:
            return subprocess.CompletedProcess(args, 0, json.dumps(_rendered_config(cache_source)), "")
        if "ps" in args:
            return subprocess.CompletedProcess(args, 0, "abcdef1234567890\n", "")
        if args[1] == "inspect":
            return subprocess.CompletedProcess(args, 0, json.dumps([_runtime_container(cache_source)]), "")
        raise AssertionError(f"unexpected Docker command: {args}")

    runner = compose_runner.ComposeRunner(root, run=run, docker_path="docker")

    result = runner.inspect_project()

    assert result["project_name"] == PROJECT_NAME
    assert result["service"] == "vllm"
    assert result["runtime_state"] == "running"
    assert result["network"] == "ddu_inference_bus"


def test_stop_and_start_use_fixed_project_and_service_arguments(tmp_path):
    compose_runner = load_dashboard_module("compose_runner")
    root, cache_source = _project_root(tmp_path)
    commands: list[list[str]] = []

    def run(args, **_kwargs):
        commands.append(args)
        if "config" in args:
            return subprocess.CompletedProcess(args, 0, json.dumps(_rendered_config(cache_source)), "")
        if "ps" in args:
            return subprocess.CompletedProcess(args, 0, "", "")
        if "stop" in args or "up" in args:
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(f"unexpected Docker command: {args}")

    runner = compose_runner.ComposeRunner(root, run=run, docker_path="docker")

    runner.stop_vllm()
    runner.start_vllm()

    stop_args = next(args for args in commands if "stop" in args)
    start_args = next(args for args in commands if "up" in args)
    assert stop_args[-2:] == ["stop", "vllm"]
    assert start_args[-4:] == ["up", "--detach", "--no-deps", "vllm"]
    assert all("--project-name" in args and PROJECT_NAME in args for args in (stop_args, start_args))
    assert all(str(root) in args for args in (stop_args, start_args))


def test_runner_never_calls_down_or_volume_commands(tmp_path):
    compose_runner = load_dashboard_module("compose_runner")
    root, cache_source = _project_root(tmp_path)
    commands: list[list[str]] = []

    def run(args, **_kwargs):
        commands.append(args)
        if "config" in args:
            return subprocess.CompletedProcess(args, 0, json.dumps(_rendered_config(cache_source)), "")
        if "ps" in args:
            return subprocess.CompletedProcess(args, 0, "", "")
        if "stop" in args or "up" in args:
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(f"unexpected Docker command: {args}")

    runner = compose_runner.ComposeRunner(root, run=run, docker_path="docker")
    runner.stop_vllm()
    runner.start_vllm()

    flattened = [argument.casefold() for args in commands for argument in args]
    assert "down" not in flattened
    assert "volume" not in flattened
    assert "rm" not in flattened


def test_runtime_label_or_network_mismatch_fails_closed(tmp_path):
    compose_runner = load_dashboard_module("compose_runner")
    root, cache_source = _project_root(tmp_path)
    commands: list[list[str]] = []

    def run(args, **_kwargs):
        commands.append(args)
        if "config" in args:
            return subprocess.CompletedProcess(args, 0, json.dumps(_rendered_config(cache_source)), "")
        if "ps" in args:
            return subprocess.CompletedProcess(args, 0, "abcdef1234567890\n", "")
        if args[1] == "inspect":
            record = _runtime_container(cache_source, project="unrelated-project")
            return subprocess.CompletedProcess(args, 0, json.dumps([record]), "")
        raise AssertionError(f"unexpected Docker command: {args}")

    runner = compose_runner.ComposeRunner(root, run=run, docker_path="docker")

    try:
        runner.inspect_project()
    except compose_runner.ComposeRunnerError as exc:
        assert "project" in str(exc).casefold() or "runtime" in str(exc).casefold()
    else:
        raise AssertionError("a mismatched runtime project must be rejected")


def test_old_service_must_release_network_alias_before_replacement_start(tmp_path):
    compose_runner = load_dashboard_module("compose_runner")
    root, _cache_source = _project_root(tmp_path)
    endpoint = {
        "Id": "abcdef1234567890",
        "NetworkSettings": {
            "Networks": {"ddu_inference_bus": {"Aliases": ["old-container", "ddu-llm"]}}
        },
    }
    calls: list[list[str]] = []

    def occupied(args, **_kwargs):
        calls.append(args)
        if args[1:3] == ["network", "inspect"]:
            payload = [{"Name": "ddu_inference_bus", "Containers": {endpoint["Id"]: {}}}]
            return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")
        if args[1] == "inspect":
            return subprocess.CompletedProcess(args, 0, json.dumps([endpoint]), "")
        raise AssertionError(f"unexpected Docker command: {args}")

    runner = compose_runner.ComposeRunner(root, run=occupied, docker_path="docker")

    assert runner.assert_alias_released() is False
    assert all("up" not in args for args in calls)

    def empty(args, **_kwargs):
        assert args[1:3] == ["network", "inspect"]
        return subprocess.CompletedProcess(
            args,
            0,
            json.dumps([{"Name": "ddu_inference_bus", "Containers": None}]),
            "",
        )

    assert compose_runner.ComposeRunner(root, run=empty).assert_alias_released() is True


def test_stopped_runtime_may_have_disconnected_network_alias(tmp_path):
    compose_runner = load_dashboard_module("compose_runner")
    root, cache_source = _project_root(tmp_path)

    def run(args, **_kwargs):
        if "config" in args:
            return subprocess.CompletedProcess(args, 0, json.dumps(_rendered_config(cache_source)), "")
        if "ps" in args:
            return subprocess.CompletedProcess(args, 0, "abcdef1234567890\n", "")
        if args[1] == "inspect":
            record = _runtime_container(cache_source)
            record["State"]["Status"] = "exited"
            record["NetworkSettings"]["Networks"] = {}
            return subprocess.CompletedProcess(args, 0, json.dumps([record]), "")
        raise AssertionError(f"unexpected Docker command: {args}")

    result = compose_runner.ComposeRunner(root, run=run).inspect_project()

    assert result["runtime_state"] == "exited"
    assert result["active_profile_id"] is None
