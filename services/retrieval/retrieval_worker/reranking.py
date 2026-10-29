from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path

from .contracts import MissingArtifactError
from .models import (
    RERANKING_CODE_HASHES,
    RERANKING_CODE_ID,
    RERANKING_CODE_REVISION,
    RERANKING_MODEL_ID,
    RERANKING_REVISION,
)

MODEL_BATCH_SIZE = 2
EXPECTED_AUTO_MAP = {
    "AutoConfig": "Alibaba-NLP/new-impl--configuration.NewConfig",
    "AutoModelForSequenceClassification": (
        "Alibaba-NLP/new-impl--modeling.NewForSequenceClassification"
    ),
}


def verify_reviewed_code_snapshot(snapshot_path: str | Path) -> None:
    root = Path(snapshot_path)
    for filename, expected_digest in RERANKING_CODE_HASHES.items():
        code_file = root / filename
        try:
            actual_digest = hashlib.sha256(code_file.read_bytes()).hexdigest()
        except OSError:
            raise MissingArtifactError() from None
        if actual_digest != expected_digest:
            raise RuntimeError("Reviewed model implementation hash does not match")


def verify_model_code_reference(model_snapshot_path: str | Path) -> None:
    config_path = Path(model_snapshot_path) / "config.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise MissingArtifactError() from None
    auto_map = config.get("auto_map", {})
    if (
        not isinstance(auto_map, dict)
        or config.get("num_labels") != 1
        or any(auto_map.get(name) != expected for name, expected in EXPECTED_AUTO_MAP.items())
    ):
        raise RuntimeError("Pinned reranker references unexpected custom code")


def create_reranking_adapter():
    """Load the pinned GTE model and reviewed custom code from the offline cache."""
    try:
        import torch
        from huggingface_hub import snapshot_download
        from huggingface_hub.errors import LocalEntryNotFoundError
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
    except ImportError:
        raise RuntimeError("Required model runtime is unavailable") from None

    cache_dir = os.environ.get("HF_HUB_CACHE", "/models/hub")
    try:
        model_snapshot = snapshot_download(
            repo_id=RERANKING_MODEL_ID,
            revision=RERANKING_REVISION,
            cache_dir=cache_dir,
            local_files_only=True,
        )
        code_snapshot = snapshot_download(
            repo_id=RERANKING_CODE_ID,
            revision=RERANKING_CODE_REVISION,
            cache_dir=cache_dir,
            local_files_only=True,
        )
    except (LocalEntryNotFoundError, FileNotFoundError, OSError):
        raise MissingArtifactError() from None

    verify_reviewed_code_snapshot(code_snapshot)
    verify_model_code_reference(model_snapshot)
    torch.set_num_threads(2)
    try:
        tokenizer = AutoTokenizer.from_pretrained(
            model_snapshot,
            revision=RERANKING_REVISION,
            local_files_only=True,
        )
        tokenizer.padding_side = "right"
        model = AutoModelForSequenceClassification.from_pretrained(
            model_snapshot,
            revision=RERANKING_REVISION,
            code_revision=RERANKING_CODE_REVISION,
            local_files_only=True,
            trust_remote_code=True,
            torch_dtype=torch.float32,
        )
    except OSError:
        raise MissingArtifactError() from None

    model.to("cpu")
    model.eval()

    class GTERerankingAdapter:
        def count_pair_tokens(self, query: str, document: str) -> int:
            tokens = tokenizer(query, document, add_special_tokens=True, truncation=False)
            return len(tokens["input_ids"])

        def score_pairs(self, query: str, documents: list[str]) -> list[float]:
            scores: list[float] = []
            for start in range(0, len(documents), MODEL_BATCH_SIZE):
                chunk = documents[start : start + MODEL_BATCH_SIZE]
                inputs = tokenizer(
                    [(query, document) for document in chunk],
                    padding=True,
                    truncation=False,
                    return_tensors="pt",
                )
                with torch.inference_mode():
                    logits = model(**inputs, return_dict=True).logits.reshape(-1).float()
                scores.extend(float(score) for score in logits.cpu().tolist())
            return scores

    adapter = GTERerankingAdapter()
    probe = adapter.score_pairs("retrieval worker readiness probe", ["synthetic passage"])
    if len(probe) != 1 or not math.isfinite(probe[0]):
        raise RuntimeError("Reranking readiness probe failed")
    return adapter
