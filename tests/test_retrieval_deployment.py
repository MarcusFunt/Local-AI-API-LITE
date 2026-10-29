from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def read_retrieval_compose() -> dict:
    return yaml.safe_load((ROOT / "deploy/retrieval/compose.yaml").read_text(encoding="utf-8"))


def test_embedding_service_is_private_cpu_only_and_independent():
    compose = read_retrieval_compose()
    service = compose["services"]["embed"]

    assert service["image"] == "local-ai-api-lite-retrieval:0.1.0"
    assert service["command"] == ["python", "-m", "retrieval_worker.app", "--role", "embedding"]
    assert "ports" not in service
    assert "gpus" not in service
    assert "depends_on" not in service
    assert "restart" not in service
    assert service["networks"]["inference"]["aliases"] == ["ddu-embed"]
    assert compose["networks"]["inference"] == {
        "name": "ddu_inference_bus",
        "external": True,
    }
    assert service["deploy"]["resources"]["limits"] == {"cpus": "4.0", "memory": "5g"}


def test_embedding_cache_is_external_and_read_only_for_serving():
    compose = read_retrieval_compose()
    for name in ("embed", "rerank"):
        service = compose["services"][name]
        cache_mounts = [mount for mount in service["volumes"] if mount["target"] == "/models"]

        assert len(cache_mounts) == 1
        assert cache_mounts[0]["type"] == "bind"
        assert cache_mounts[0]["source"] == "${MODEL_CACHE_DIR:?Set MODEL_CACHE_DIR}"
        assert cache_mounts[0]["read_only"] is True
        assert service["environment"]["HF_HUB_OFFLINE"] == "1"
        assert service["environment"]["TRANSFORMERS_OFFLINE"] == "1"
        assert service["environment"]["HF_MODULES_CACHE"] == "/tmp/hf-modules"


def test_model_installer_is_explicit_write_access_and_not_on_inference_network():
    compose = read_retrieval_compose()
    installer = compose["services"]["model-installer"]

    assert installer["profiles"] == ["install"]
    assert installer["user"] == "${MODEL_CACHE_UID:-10001}:${MODEL_CACHE_GID:-10001}"
    assert "networks" not in installer
    assert installer["command"][-1] == "--plan"
    model_mount = next(mount for mount in installer["volumes"] if mount["target"] == "/models")
    assert model_mount["read_only"] is False


def test_retrieval_alias_does_not_replace_existing_llm_contract():
    retrieval_services = read_retrieval_compose()["services"]
    root_compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))

    assert retrieval_services["embed"]["networks"]["inference"]["aliases"] == ["ddu-embed"]
    assert retrieval_services["rerank"]["networks"]["inference"]["aliases"] == ["ddu-rerank"]
    assert root_compose["services"]["vllm"]["networks"]["inference"]["aliases"] == ["ddu-llm"]
    assert "ports" not in root_compose["services"]["vllm"]


def test_reranking_service_is_private_cpu_only_and_independent():
    compose = read_retrieval_compose()
    service = compose["services"]["rerank"]

    assert service["image"] == "local-ai-api-lite-retrieval:0.1.0"
    assert service["command"] == ["python", "-m", "retrieval_worker.app", "--role", "reranking"]
    assert "ports" not in service
    assert "gpus" not in service
    assert "depends_on" not in service
    assert "restart" not in service
    assert service["networks"]["inference"]["aliases"] == ["ddu-rerank"]
    assert service["deploy"]["resources"]["limits"] == {"cpus": "2.0", "memory": "4g"}


def test_retrieval_env_example_uses_an_external_cache_without_credentials():
    example = (ROOT / "deploy/retrieval/.env.example").read_text(encoding="utf-8")

    assert "MODEL_CACHE_DIR=" in example
    assert "HF_TOKEN" not in example
    assert "MODEL_ID=" not in example
    assert "MODEL_CACHE_UID=" in example
    assert "MODEL_CACHE_GID=" in example


def test_worker_runtime_is_exactly_pinned_and_every_dependency_has_a_hash():
    lock = (ROOT / "services/retrieval/requirements.lock").read_text(encoding="utf-8")
    required = {
        "fastapi": "0.162.0",
        "uvicorn": "0.54.0",
        "huggingface-hub": "0.36.2",
        "transformers": "4.57.6",
        "torch": "2.7.0+cpu",
    }

    for package, version in required.items():
        assert re.search(rf"(?m)^{re.escape(package)}=={re.escape(version)} \\", lock)

    lines = lock.splitlines()
    package_indexes = [
        index
        for index, line in enumerate(lines)
        if line.endswith("\\") and re.match(r"^[A-Za-z0-9_.-]+==", line)
    ]
    assert package_indexes
    for position, index in enumerate(package_indexes):
        end = package_indexes[position + 1] if position + 1 < len(package_indexes) else len(lines)
        block = "\n".join(lines[index:end])
        assert "--hash=sha256:" in block, lines[index]

    dockerfile = (ROOT / "services/retrieval/Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"(?m)^FROM python:3\.11\.18-slim-bookworm@sha256:[a-f0-9]{64}$", dockerfile)
    requirements = (ROOT / "services/retrieval/requirements.in").read_text(encoding="utf-8")
    assert "sentence-transformers==" not in requirements
    assert "sentence-transformers==" not in lock

    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    for excluded in (".git", ".venv", "model-cache", "hf-cache", ".env"):
        assert excluded in dockerignore
