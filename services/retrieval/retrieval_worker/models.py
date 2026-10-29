from __future__ import annotations

from dataclasses import asdict, dataclass

EMBEDDING_ALIAS = "ddu-embed"
EMBEDDING_MODEL_ID = "Qwen/Qwen3-Embedding-0.6B"
EMBEDDING_REVISION = "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
EMBEDDING_DIMENSION = 1024
QUERY_INSTRUCTION_VERSION = "qwen3-question-to-passages-en-v1"
QUERY_INSTRUCTION = "Instruct: Given a question, retrieve relevant passages that answer the question"


@dataclass(frozen=True)
class ModelMetadata:
    id: str
    object: str
    task: str
    revision: str
    capabilities: dict[str, object]
    limits: dict[str, int]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


EMBEDDING_METADATA = ModelMetadata(
    id=EMBEDDING_ALIAS,
    object="model",
    task="embedding",
    revision=EMBEDDING_REVISION,
    capabilities={
        "dimensions": EMBEDDING_DIMENSION,
        "normalized": True,
        "dtype": "float32",
        "input_types": ["query", "document"],
        "query_instruction_version": QUERY_INSTRUCTION_VERSION,
    },
    limits={"max_batch_size": 8, "max_input_tokens": 2048, "max_request_seconds": 60},
)
