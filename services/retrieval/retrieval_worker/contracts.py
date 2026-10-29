from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr

MAX_BODY_BYTES = 256 * 1024
MAX_EMBEDDING_BATCH_SIZE = 8
MAX_INPUT_TOKENS = 2048
EXPECTED_DIMENSION = 1024
MAX_RERANKING_PAIRS = 20
MAX_PAIR_TOKENS = 2048


class MissingArtifactError(Exception):
    """A pinned model artifact is absent from the offline cache."""


class QueueFullError(Exception):
    """All active and queued inference capacity is occupied."""


class InputTooLongError(Exception):
    """A request exceeds the model's token bound."""


class ServiceError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


class EmbeddingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    model: StrictStr
    input: StrictStr | list[StrictStr]
    input_type: Literal["query", "document"]
    encoding_format: Literal["float"] = "float"


class EmbeddingData(BaseModel):
    object: Literal["embedding"] = "embedding"
    index: int
    embedding: list[float]


class TokenUsage(BaseModel):
    prompt_tokens: int
    total_tokens: int


class EmbeddingResponse(BaseModel):
    object: Literal["list"] = "list"
    data: list[EmbeddingData]
    model: str
    usage: TokenUsage


class RerankingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    model: StrictStr
    query: StrictStr
    documents: list[StrictStr]
    top_n: StrictInt | None = None


class RerankingResult(BaseModel):
    index: int
    relevance_score: float


class RerankingResponse(BaseModel):
    model: str
    revision: str
    results: list[RerankingResult]
    usage: TokenUsage
