from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def read_compose() -> dict:
    return yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))


def test_compose_uses_a_digest_pinned_vllm_image_and_pinned_model_revision():
    compose = read_compose()
    service = compose["services"]["vllm"]
    example = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert re.fullmatch(
        r"vllm/vllm-openai:v0\.32\.0@sha256:[0-9a-f]{64}", service["image"]
    )
    assert "MODEL_REVISION=7a613872f394578b0b52b683ff4ac47516b4bcaf" in example


def test_compose_keeps_the_model_api_off_host_and_on_the_private_alias():
    compose = read_compose()
    service = compose["services"]["vllm"]

    assert "ports" not in service
    assert service["networks"]["inference"]["aliases"] == ["ddu-llm"]
    assert compose["networks"]["inference"] == {
        "name": "ddu_inference_bus",
        "external": True,
    }
    assert "--port" in service["command"]
    assert "8080" in service["command"]
    assert "--enable-per-request-metrics" in service["command"]


def test_compose_reserves_the_gpu_and_mounts_only_an_external_model_cache():
    service = read_compose()["services"]["vllm"]

    assert service["gpus"] == "all"
    assert service["shm_size"] == "4gb"
    cache_mounts = [mount for mount in service["volumes"] if "/root/.cache/huggingface" in mount]
    assert len(cache_mounts) == 1
    assert cache_mounts[0].startswith("${HF_CACHE_DIR:")
    assert not cache_mounts[0].startswith("./")


def test_example_configuration_contains_no_tokens_and_uses_conservative_defaults():
    example = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "MODEL_ID=RedHatAI/Qwen3.5-4B-quantized.w4a16" in example
    assert "MODEL_ALIAS=ddu-narrator" in example
    assert "MAX_MODEL_LEN=4096" in example
    assert "MAX_NUM_SEQS=2" in example
    assert "GPU_MEMORY_UTILIZATION=0.75" in example
    assert "HF_TOKEN" not in example
    assert "HF_CACHE_DIR=" in example


def test_deployment_docs_explain_the_alias_health_semantics_and_rollback():
    deployment = (ROOT / "docs/DEPLOYMENT.md").read_text(encoding="utf-8").lower()
    contract = (ROOT / "docs/API_CONTRACT.md").read_text(encoding="utf-8").lower()

    assert "ddu-llm" in deployment
    assert "ddu_inference_bus" in deployment
    assert "health" in contract and "200" in contract
    assert "rollback" in deployment


def test_local_markdown_links_resolve():
    markdown = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
    link_pattern = re.compile(r"\[[^\]]+\]\(([^)]+)\)")

    for document in markdown:
        for destination in link_pattern.findall(document.read_text(encoding="utf-8")):
            if destination.startswith(("https://", "http://", "mailto:", "#")):
                continue
            target = destination.split("#", 1)[0]
            if target:
                assert (document.parent / target).exists(), f"broken link in {document.name}: {destination}"


def test_ci_runs_mock_tests_lints_compose_validation_and_secret_scanning():
    workflow = ROOT / ".github/workflows/ci.yml"
    assert workflow.exists()
    content = workflow.read_text(encoding="utf-8")

    assert "python -m pytest -q" in content
    assert "ruff check scripts tests" in content
    assert "yamllint" in content
    assert "shellcheck" in content
    assert "docker compose config --quiet" in content
    assert "gitleaks/gitleaks-action@v3" in content
