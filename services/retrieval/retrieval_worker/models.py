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

RERANKING_ALIAS = "ddu-rerank"
RERANKING_MODEL_ID = "Alibaba-NLP/gte-multilingual-reranker-base"
RERANKING_REVISION = "8215cf04918ba6f7b6a62bb44238ce2953d8831c"
RERANKING_CODE_ID = "Alibaba-NLP/new-impl"
RERANKING_CODE_REVISION = "40ced75c3017eb27626c9d4ea981bde21a2662f4"
RERANKING_CODE_HASHES = {
    "configuration.py": "3411088045ffb8a9a0aa9936eae275896b39983a2ee5b08f091b44e6289e4fe4",
    "modeling.py": "374670b416fcc82f081c9cd28b5fd61c2bd91bbe18eb4798fcc48a81f9c250a0",
}
RERANKING_MAX_PAIRS = 20
MAX_PAIR_TOKENS = 2048
RERANKING_METADATA = ModelMetadata(
    id=RERANKING_ALIAS,
    object="model",
    task="rerank",
    revision=RERANKING_REVISION,
    capabilities={
        "languages": ["multilingual", "Danish"],
        "max_pair_tokens": MAX_PAIR_TOKENS,
        "score_semantics": "raw_ranking_signal",
    },
    limits={
        "max_candidate_pairs": RERANKING_MAX_PAIRS,
        "max_pair_tokens": MAX_PAIR_TOKENS,
        "max_request_seconds": 60,
    },
)
