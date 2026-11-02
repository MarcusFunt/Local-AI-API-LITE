from __future__ import annotations

import math
import os

from .contracts import MissingArtifactError
from .models import (
    EMBEDDING_DIMENSION,
    EMBEDDING_MODEL_ID,
    EMBEDDING_REVISION,
    QUERY_INSTRUCTION,
)


def format_embedding_input(text: str, *, input_type: str) -> str:
    if input_type == "query":
        return f"{QUERY_INSTRUCTION}\nQuery: {text}"
    if input_type == "document":
        return text
    raise ValueError("unsupported embedding input type")


def create_embedding_adapter():
    """Load the fixed Qwen snapshot from the mounted offline cache on CPU."""
    try:
        import torch
        from huggingface_hub import snapshot_download
        from huggingface_hub.errors import LocalEntryNotFoundError
        from torch.nn import functional
        from transformers import AutoModel, AutoTokenizer
    except ImportError:
        raise RuntimeError("Required model runtime is unavailable") from None

    cache_dir = os.environ.get("HF_HUB_CACHE", "/models/hub")
    try:
        snapshot = snapshot_download(
            repo_id=EMBEDDING_MODEL_ID,
            revision=EMBEDDING_REVISION,
            cache_dir=cache_dir,
            local_files_only=True,
        )
    except (LocalEntryNotFoundError, FileNotFoundError):
        raise MissingArtifactError() from None
    except OSError:
        raise MissingArtifactError() from None

    torch.set_num_threads(2)
    try:
        tokenizer = AutoTokenizer.from_pretrained(snapshot, local_files_only=True)
        tokenizer.padding_side = "right"
        model = AutoModel.from_pretrained(
            snapshot,
            local_files_only=True,
            trust_remote_code=False,
            torch_dtype=torch.float32,
            low_cpu_mem_usage=True,
        )
    except OSError:
        raise MissingArtifactError() from None

    if getattr(model.config, "hidden_size", None) != EMBEDDING_DIMENSION:
        raise RuntimeError("Pinned embedding model has an unexpected dimension")
    model.to("cpu")
    model.eval()

    class QwenEmbeddingAdapter:
        def _format(self, text: str, *, input_type: str) -> str:
            return format_embedding_input(text, input_type=input_type)

        def count_tokens(self, text: str, *, input_type: str) -> int:
            formatted = self._format(text, input_type=input_type)
            return len(tokenizer(formatted, add_special_tokens=True)["input_ids"])

        def embed(self, inputs: list[str], *, input_type: str) -> list[list[float]]:
            formatted = [self._format(text, input_type=input_type) for text in inputs]
            tokens = tokenizer(
                formatted,
                padding=True,
                truncation=False,
                return_tensors="pt",
            )
            with torch.inference_mode():
                hidden = model(**tokens).last_hidden_state
                last_token_indices = tokens["attention_mask"].sum(dim=1) - 1
                rows = torch.arange(hidden.shape[0], device=hidden.device)
                vectors = hidden[rows, last_token_indices]
                vectors = functional.normalize(vectors.float(), p=2, dim=1)
            return vectors.cpu().tolist()

    adapter = QwenEmbeddingAdapter()
    probe = adapter.embed(["retrieval worker readiness probe"], input_type="document")
    if len(probe) != 1 or len(probe[0]) != EMBEDDING_DIMENSION:
        raise RuntimeError("Embedding readiness probe failed")
    if not all(math.isfinite(float(value)) for value in probe[0]):
        raise RuntimeError("Embedding readiness probe failed")
    norm = math.sqrt(sum(float(value) ** 2 for value in probe[0]))
    if not math.isfinite(norm) or abs(norm - 1.0) > 1e-4:
        raise RuntimeError("Embedding readiness probe failed")
    return adapter
