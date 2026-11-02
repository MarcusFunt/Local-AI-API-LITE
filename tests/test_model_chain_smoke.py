from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "model_chain_smoke", ROOT / "scripts" / "model_chain_smoke.py"
)
assert SPEC is not None and SPEC.loader is not None
model_chain = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(model_chain)


class FakeModelClient:
    def __init__(self):
        self.calls = []

    def verify_services(self):
        self.calls.append(("verify_services",))
        return {name: {"alias": name, "ready": True} for name in model_chain.SERVICE_ORDER}

    def synthesize(self, text):
        self.calls.append(("synthesize", text))
        return b"RIFFquestionWAVE synthetic question audio" if text.startswith("When did") else b"RIFFanswerWAVE synthetic answer audio"

    def transcribe(self, audio):
        self.calls.append(("transcribe", audio))
        if audio == b"RIFFquestionWAVE synthetic question audio":
            return "question from first audio"
        if audio == b"RIFFanswerWAVE synthetic answer audio":
            return "answer from final audio"
        raise AssertionError("ASR received audio that TTS did not produce")

    def embed(self, texts, *, input_type):
        self.calls.append(("embed", list(texts), input_type))
        if input_type == "query":
            return [[1.0, 0.0]]
        vectors = {
            "weather report for Vinterlys": [0.99, 0.01],
            "The fictional Vinterlys observatory opened in 1842.": [0.8, 0.6],
            "A fictional lighthouse opened in 1842.": [0.0, 1.0],
        }
        return [vectors[text] for text in texts]

    def rerank(self, query, documents):
        self.calls.append(("rerank", query, list(documents)))
        # GTE promotes the answer passage from second dense position to first.
        return [1, 0, 2]

    def generate(self, query, passages):
        self.calls.append(("generate", query, list(passages)))
        return "In the fictional test corpus, it opened in 1842 [vinterlys-answer]."


def _passages():
    return [
        {"id": "vinterlys-weather", "text": "weather report for Vinterlys"},
        {"id": "vinterlys-answer", "text": "The fictional Vinterlys observatory opened in 1842."},
        {"id": "lighthouse", "text": "A fictional lighthouse opened in 1842."},
    ]


def test_model_chain_feeds_each_real_model_output_into_the_next_stage():
    client = FakeModelClient()

    result = model_chain.run_model_chain(
        client,
        question="When did the fictional Vinterlys observatory open?",
        passages=_passages(),
    )

    assert [call[0] for call in client.calls] == [
        "verify_services",
        "synthesize",
        "transcribe",
        "embed",
        "embed",
        "rerank",
        "generate",
        "synthesize",
        "transcribe",
    ]
    assert client.calls[1][1] == "When did the fictional Vinterlys observatory open?"
    first_audio = client.calls[2][1]
    assert first_audio == b"RIFFquestionWAVE synthetic question audio"
    assert client.calls[3][1] == ["question from first audio"]
    assert client.calls[4][1] == [passage["text"] for passage in _passages()]
    dense_order = client.calls[5][2]
    assert dense_order == [
        "weather report for Vinterlys",
        "The fictional Vinterlys observatory opened in 1842.",
        "A fictional lighthouse opened in 1842.",
    ]
    assert client.calls[6][1] == "question from first audio"
    assert client.calls[6][2][0]["id"] == "vinterlys-answer"
    assert client.calls[7][1] == "In the fictional test corpus, it opened in 1842 [vinterlys-answer]."
    assert client.calls[8][1] == b"RIFFanswerWAVE synthetic answer audio"
    assert result["status"] == "passed"
    assert result["evidence_ids"] == ["vinterlys-answer", "vinterlys-weather", "lighthouse"]
    assert "transcript" not in result
    assert "answer" not in result


def test_dense_ranking_rejects_mismatched_vector_counts_and_dimensions():
    with pytest.raises(model_chain.ModelChainError, match="embedding_response_invalid"):
        model_chain.rank_passages([1.0, 0.0], [[1.0]], _passages())


def test_dense_ranking_orders_passages_by_qwen_vector_similarity():
    passages = _passages()

    ranked = model_chain.rank_passages(
        [1.0, 0.0],
        [[0.99, 0.01], [0.8, 0.6], [0.0, 1.0]],
        passages,
    )

    assert [item["id"] for item in ranked] == [
        "vinterlys-weather",
        "vinterlys-answer",
        "lighthouse",
    ]
