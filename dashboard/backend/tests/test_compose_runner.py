from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from conftest import load_dashboard_module

PROJECT_NAME = "local-ai-api-lite"
IMAGE_REF = "vllm/vllm-openai:v0.32.0@sha256:befccf0e7e4089aa23926bb6f887a85c0d46c2a99e6de5c5ffdf5cb7957c5b34"
CACHE_DESTINATION = "/root/.cache/huggingface"


def _rendered_config(cache_source: Path) -> dict:
    return {
        "name": PROJECT_NAME,
        "services": {
            "vllm": {
                "restart": "unless-stopped",
                "command": [
                    "RedHatAI/Qwen3.5-4B-quantized.w4a16",
                    "--revision",
                    "7a613872f394578b0b52b683ff4ac47516b4bcaf",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "8080",
                    "--served-model-name",
                    "ddu-narrator",
                    "--language-model-only",
                    "--max-model-len",
                    "4096",
                    "--max-num-seqs",
                    "2",
                    "--gpu-memory-utilization",
                    "0.75",
                    "--reasoning-parser",
                    "qwen3",
                    "--enable-per-request-metrics",
                ],
                "entrypoint": None,
                "environment": {
                    "HF_HOME": "/root/.cache/huggingface",
                    "HF_HUB_CACHE": "/root/.cache/huggingface/hub",
                    "HF_HUB_DISABLE_TELEMETRY": "1",
                    "VLLM_LOGGING_LEVEL": "INFO",
                },
                "gpus": [{"count": -1}],
                "image": IMAGE_REF,
                "shm_size": "4294967296",
                "stop_grace_period": "2m0s",
                "volumes": [
                    {
                        "type": "bind",
                        "source": str(cache_source),
                        "target": CACHE_DESTINATION,
                        "bind": {},
                    }
                ],
                "networks": {"inference": {"aliases": ["ddu-llm"]}},
                "healthcheck": {
                    "test": [
                        "CMD",
                        "python3",
                        "-c",
                        "import urllib.request; urllib.request.urlopen( 'http://127.0.0.1:8080/health', timeout=5)",
                    ],
                    "timeout": "10s",
                    "interval": "30s",
                    "retries": 5,
                    "start_period": "5m0s",
                },
            }
        },
        "networks": {
            "inference": {"name": "ddu_inference_bus", "ipam": {}, "external": True}
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
    command:
      - "${{MODEL_ID:?Set MODEL_ID in .env}}"
      - --revision
      - "${{MODEL_REVISION:?Set MODEL_REVISION in .env}}"
      - --host
      - 0.0.0.0
      - --port
      - "8080"
      - --served-model-name
      - "${{MODEL_ALIAS:-ddu-narrator}}"
      - --language-model-only
      - --max-model-len
      - "${{MAX_MODEL_LEN:-4096}}"
      - --max-num-seqs
      - "${{MAX_NUM_SEQS:-2}}"
      - --gpu-memory-utilization
      - "${{GPU_MEMORY_UTILIZATION:-0.75}}"
      - --reasoning-parser
      - "${{REASONING_PARSER:-qwen3}}"
      - --enable-per-request-metrics
    environment:
      HF_HOME: /root/.cache/huggingface
      HF_HUB_CACHE: /root/.cache/huggingface/hub
      HF_HUB_DISABLE_TELEMETRY: "1"
      VLLM_LOGGING_LEVEL: INFO
    volumes:
      - ${{HF_CACHE_DIR:?set HF_CACHE_DIR}}:{CACHE_DESTINATION}
    gpus: all
    shm_size: 4gb
    restart: unless-stopped
    stop_grace_period: 2m
    healthcheck:
      test:
        - CMD
        - python3
        - -c
        - "import urllib.request; urllib.request.urlopen( 'http://127.0.0.1:8080/health', timeout=5)"
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 5m
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


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("privileged", True),
        ("devices", [{"path_on_host": "/dev/nvidia0", "path_in_container": "/dev/nvidia0"}]),
        ("ports", [{"target": 8080, "published": "8080", "protocol": "tcp"}]),
        ("command", ["/bin/sh", "-c", "unexpected service command"]),
    ],
)
def test_rendered_configuration_rejects_unallowlisted_service_options(tmp_path, setting, value):
    compose_runner = load_dashboard_module("compose_runner")
    _root, cache_source = _project_root(tmp_path)
    rendered = _rendered_config(cache_source)
    rendered["services"]["vllm"][setting] = value

    with pytest.raises(compose_runner.ComposeRunnerError):
        compose_runner.ComposeRunner._validate_rendered_configuration(rendered)


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




def test_contract_check_uses_fixed_no_prompt_command_and_expected_model(tmp_path):
    compose_runner = load_dashboard_module("compose_runner")
    root, cache_source = _project_root(tmp_path)
    calls: list[list[str]] = []
    runtime = _runtime_container(cache_source)
    runtime["Config"]["Cmd"] = [
        "RedHatAI/Qwen3.5-4B-quantized.w4a16",
        "--revision",
        "7a613872f394578b0b52b683ff4ac47516b4bcaf",
        "--served-model-name",
        "ddu-narrator",
        "--reasoning-parser",
        "qwen3",
        "--max-model-len",
        "4096",
        "--max-num-seqs",
        "2",
        "--gpu-memory-utilization",
        "0.75",
    ]

    def run(args, **kwargs):
        calls.append(args)
        assert kwargs["shell"] is False
        if "config" in args:
            return subprocess.CompletedProcess(args, 0, json.dumps(_rendered_config(cache_source)), "")
        if "ps" in args:
            return subprocess.CompletedProcess(args, 0, "abcdef1234567890\n", "")
        if args[1] == "inspect":
            return subprocess.CompletedProcess(args, 0, json.dumps([runtime]), "")
        if args[1] == "exec":
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(f"unexpected Docker command: {args}")

    runner = compose_runner.ComposeRunner(root, run=run, docker_path="docker")

    assert runner.verify_contract("baseline-4k-2") is True
    execution = next(args for args in calls if len(args) > 1 and args[1] == "exec")
    assert execution[:5] == ["docker", "exec", "abcdef1234567890", "python3", "-c"]
    assert execution[-1] == "ddu-narrator"
    assert "chat/completions" not in execution[5]
