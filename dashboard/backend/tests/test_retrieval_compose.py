from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from conftest import load_dashboard_module

ROOT = Path(__file__).resolve().parents[3]


def make_project(tmp_path: Path) -> Path:
    root = tmp_path / "retrieval"
    root.mkdir()
    shutil.copyfile(ROOT / "deploy" / "retrieval" / "compose.yaml", root / "compose.yaml")
    (root / ".env").write_text(
        f"MODEL_CACHE_DIR={tmp_path.as_posix()}/model-cache\nMODEL_CACHE_UID=10001\nMODEL_CACHE_GID=10001\n",
        encoding="utf-8",
    )
    return root


def test_validates_existing_separate_retrieval_compose_source(tmp_path):
    module = load_dashboard_module("retrieval_compose")
    root = make_project(tmp_path)

    assert module.validate_retrieval_compose_root(root) == root.resolve()


def test_settings_store_keeps_narrator_and_retrieval_roots_independent(tmp_path):
    configuration = load_dashboard_module("configuration")
    narrator_root = tmp_path / "narrator"
    narrator_root.mkdir()
    shutil.copyfile(ROOT / "compose.yaml", narrator_root / "compose.yaml")
    (narrator_root / ".env").write_text(
        "MODEL_ID=unused\nMODEL_REVISION=unused\nHF_CACHE_DIR=C:/cache\n",
        encoding="utf-8",
    )
    retrieval_root = make_project(tmp_path)
    settings = configuration.LocalSettingsStore(tmp_path / "settings.json")

    settings.configure_compose_root(narrator_root, validate_configuration=lambda _path: None)
    settings.configure_retrieval_compose_root(retrieval_root)

    assert settings.load_compose_root() == narrator_root.resolve()
    assert settings.load_retrieval_compose_root() == retrieval_root.resolve()
    assert json.loads(settings.path.read_text(encoding="utf-8"))["version"] == 2


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("services", "embed", "ports"), ["8080:8080"]),
        (("services", "rerank", "privileged"), True),
        (("services", "embed", "gpus"), "all"),
        (("services", "embed", "networks", "inference", "aliases"), ["ddu-llm"]),
        (("services", "embed", "image"), "untrusted/image:latest"),
        (("services", "unexpected"), {}),
    ],
)
def test_rejects_unsafe_or_unexpected_compose_changes(tmp_path, path, value):
    module = load_dashboard_module("retrieval_compose")
    root = make_project(tmp_path)
    compose = yaml.safe_load((root / "compose.yaml").read_text(encoding="utf-8"))
    destination = compose
    for key in path[:-1]:
        destination = destination[key]
    destination[path[-1]] = value
    (root / "compose.yaml").write_text(yaml.safe_dump(compose), encoding="utf-8")

    with pytest.raises(module.RetrievalComposeError):
        module.validate_retrieval_compose_root(root)


def test_compose_runner_uses_fixed_project_and_service_arguments(tmp_path):
    module = load_dashboard_module("retrieval_compose")
    root = make_project(tmp_path)
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, json.dumps(_rendered_contract(root)), "")

    runner = module.RetrievalComposeRunner(root, run=run)
    runner.validate_project()

    args = calls[0][0]
    assert args[1:4] == ["compose", "--project-name", "local-ai-api-lite-retrieval"]
    assert args[args.index("--profile") + 1] == "install"
    assert args[-3:] == ["config", "--format", "json"]
    assert "ddu-llm" not in json.dumps(_rendered_contract(root))
    assert calls[0][1]["shell"] is False
    assert calls[0][1]["timeout"] <= 10


def test_runner_rejects_unsafe_resolved_compose_config_before_lifecycle(tmp_path):
    module = load_dashboard_module("retrieval_compose")
    root = make_project(tmp_path)
    rendered = _rendered_contract(root)
    rendered["services"]["embed"]["ports"] = ["8080:8080"]
    calls = []

    def run(args, **_kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, json.dumps(rendered), "")

    runner = module.RetrievalComposeRunner(root, run=run)
    with pytest.raises(module.RetrievalComposeError):
        runner.validate_project()

    assert len(calls) == 1
    assert calls[0][-3] == "config"


def test_start_launches_missing_worker_with_fixed_scoped_compose_command(tmp_path):
    module = load_dashboard_module("retrieval_compose")
    root = make_project(tmp_path)
    calls = []
    runner = module.RetrievalComposeRunner(root, run=lambda args, **_kwargs: calls.append(args) or subprocess.CompletedProcess(args, 0, "", ""))
    runner.validate_project = lambda: root
    runner.inspect_service = lambda _service: {"service": "embed", "runtime_state": "absent"}
    runner._wait_ready = lambda _root, service, _heartbeat: {"service": service, "runtime_state": "running"}

    assert runner.start("embed")["runtime_state"] == "running"
    assert calls == [
        [
            "docker", "compose", "--project-name", "local-ai-api-lite-retrieval",
            "--project-directory", str(root), "--file", str(root / "compose.yaml"),
            "--env-file", str(root / ".env"), "up", "--detach", "--no-deps", "embed",
        ]
    ]


def test_restart_missing_worker_fails_without_starting_it(tmp_path):
    module = load_dashboard_module("retrieval_compose")
    root = make_project(tmp_path)
    calls = []
    runner = module.RetrievalComposeRunner(root, run=lambda args, **_kwargs: calls.append(args) or subprocess.CompletedProcess(args, 0, "", ""))
    runner.validate_project = lambda: root
    runner.inspect_service = lambda _service: {"service": "rerank", "runtime_state": "absent"}

    with pytest.raises(module.RetrievalComposeError):
        runner.restart("rerank")

    assert calls == []


def test_stop_missing_worker_is_safe_noop(tmp_path):
    module = load_dashboard_module("retrieval_compose")
    root = make_project(tmp_path)
    calls = []
    runner = module.RetrievalComposeRunner(root, run=lambda args, **_kwargs: calls.append(args) or subprocess.CompletedProcess(args, 0, "", ""))
    runner.validate_project = lambda: root
    runner.inspect_service = lambda _service: {"service": "embed", "runtime_state": "absent"}

    assert runner.stop("embed") == {"service": "embed", "runtime_state": "absent"}
    assert calls == []


def _rendered_contract(root: Path) -> dict:
    source = yaml.safe_load((root / "compose.yaml").read_text(encoding="utf-8"))
    cache_path = (root.parent / "model-cache").resolve()
    services = {}
    for name in ("embed", "rerank"):
        raw = source["services"][name]
        services[name] = {
            "image": raw["image"],
            "build": {"context": str(root.parent.parent), "dockerfile": "services/retrieval/Dockerfile"},
            "command": raw["command"],
            "environment": raw["environment"],
            "expose": ["8080"],
            "read_only": True,
            "tmpfs": raw["tmpfs"],
            "user": "10001:10001",
            "init": True,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "stop_grace_period": "1m15s",
            "volumes": [{"type": "bind", "source": str(cache_path), "target": "/models", "read_only": True}],
            "networks": {"inference": {"name": "ddu_inference_bus", "external": True, "aliases": [raw["networks"]["inference"]["aliases"][0]]}},
            "deploy": {
                "resources": {
                    "limits": {
                        "cpus": int(float(raw["deploy"]["resources"]["limits"]["cpus"])),
                        "memory": {"5g": "5368709120", "4g": "4294967296"}[
                            raw["deploy"]["resources"]["limits"]["memory"]
                        ],
                    }
                }
            },
            "healthcheck": {"test": raw["healthcheck"]["test"], "interval": "30s", "timeout": "5s", "retries": 3, "start_period": "30s"},
            "logging": raw["logging"],
        }
    installer = source["services"]["model-installer"]
    services["model-installer"] = {
        "profiles": ["install"],
        "image": installer["image"],
        "build": {"context": str(root.parent.parent), "dockerfile": "services/retrieval/Dockerfile"},
        "entrypoint": installer["entrypoint"],
        "command": installer["command"],
        "environment": {"HF_HOME": "/models", "HF_HUB_CACHE": "/models/hub", "HF_HUB_OFFLINE": "0", "HF_HUB_DISABLE_TELEMETRY": "1", "TRANSFORMERS_OFFLINE": "0"},
        "read_only": True,
        "tmpfs": installer["tmpfs"],
        "user": "10001:10001",
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "volumes": [{"source": str(cache_path), "target": "/models"}],
    }
    return {
        "name": "local-ai-api-lite-retrieval",
        "services": services,
        "networks": {"inference": {"name": "ddu_inference_bus", "external": True}},
    }
