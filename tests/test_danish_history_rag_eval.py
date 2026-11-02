from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.danish_history_rag_eval import (
    AuthenticFtsIndex,
    SearchHit,
    audit_generated_answer,
    render_grounded_answer,
    retrieval_metrics,
)


@pytest.fixture
def tiny_authentic_index(tmp_path: Path) -> Path:
    db_path = tmp_path / "authentic.sqlite"
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        CREATE VIRTUAL TABLE search USING fts5(
            passage_id UNINDEXED, contents, sender, recipient, title
        );
        CREATE TABLE source (
            passage_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            source_url TEXT,
            year INTEGER,
            sender TEXT,
            recipient TEXT,
            title TEXT,
            raw_date TEXT,
            char_start INTEGER,
            char_end INTEGER
        );
        INSERT INTO search VALUES
            ('p-arrest', 'Arrestationen påvirkede ikke hans politiske stilling.', 'Anker', 'Frederik', 'Brev'),
            ('p-ship', 'Skibet sejlede fra København til Norge.', 'Sømand', 'Familie', 'Brev');
        INSERT INTO source VALUES
            ('p-arrest', 'doc-arrest', 'https://example.test/edition', 1814, 'Anker', 'Frederik', 'Brev', '1814', 0, 55),
            ('p-ship', 'doc-ship', 'https://example.test/edition', 1814, 'Sømand', 'Familie', 'Brev', 1814, 0, 45);
        """
    )
    connection.close()
    return db_path


def test_authentic_fts_search_returns_a_citable_source_passage(tiny_authentic_index: Path):
    with AuthenticFtsIndex(tiny_authentic_index) as index:
        results = index.search("Hvad siger Anker om arrestationen?", limit=2)

    assert results[0].passage_id == "p-arrest"
    assert results[0].document_id == "doc-arrest"
    assert results[0].source_url == "https://example.test/edition"
    assert "Arrestationen" in results[0].text


def test_retrieval_metrics_score_the_exact_known_target_passage():
    hits = [
        SearchHit("p-1", "doc-other", "source", "other text", None, None, None, None),
        SearchHit("p-2", "doc-target", "source", "target text", None, None, None, None),
        SearchHit("p-3", "doc-target", "source", "another chunk", None, None, None, None),
    ]

    result = retrieval_metrics(hits, {"p-2"})

    assert result == {
        "target_rank": 2,
        "reciprocal_rank": 0.5,
        "known_target_hit_at_1": 0.0,
        "known_target_hit_at_2": 1.0,
        "known_target_hit_at_5": 1.0,
        "known_target_hit_at_10": 1.0,
        "known_target_hit_at_20": 1.0,
    }


def test_answer_audit_requires_context_citation_and_exact_source_quote():
    contexts = {"p-arrest": "Arrestationen påvirkede ikke hans politiske stilling."}
    answer = {
        "answer": "Arrestationen ændrede ikke hans politiske stilling. [p-arrest]",
        "claims": [
            {
                "text": "Arrestationen ændrede ikke hans politiske stilling.",
                "citation_id": "p-arrest",
                "quote": "Arrestationen påvirkede ikke hans politiske stilling.",
            }
        ],
    }

    audit = audit_generated_answer(answer, contexts)

    assert audit["claim_count"] == 1
    assert audit["citation_integrity_pass"] is True
    assert audit["verbatim_quote_count"] == 1
    assert audit["quote_grounding_pass"] is True


def test_answer_audit_flags_unknown_citations_and_nonverbatim_quotes():
    answer = {
        "answer": "Søen var dyb. [missing]",
        "claims": [
            {
                "text": "Søen var dyb.",
                "citation_id": "missing",
                "quote": "Søen var meget dyb.",
            }
        ],
    }

    audit = audit_generated_answer(answer, {"p-arrest": "Arrestationen påvirkede ikke hans politiske stilling."})

    assert audit["citation_integrity_pass"] is False
    assert audit["verbatim_quote_count"] == 0
    assert audit["quote_grounding_pass"] is False


def test_grounding_gate_adds_citation_and_keeps_only_verbatim_supported_claims():
    answer = {
        "answer": "The author was politically unaffected by arrest.",
        "claims": [
            {
                "text": "Arrestationen påvirkede ikke hans politiske stilling.",
                "citation_id": "p-arrest",
                "quote": "Arrestationen påvirkede ikke hans politiske stilling.",
            },
            {
                "text": "He was arrested in Copenhagen.",
                "citation_id": "p-arrest",
                "quote": "He was arrested in Copenhagen.",
            },
        ],
    }

    rendered = render_grounded_answer(
        answer,
        {"p-arrest": "Arrestationen påvirkede ikke hans politiske stilling."},
        source_urls={"p-arrest": "https://example.test/edition"},
        abstention_text="Jeg kan ikke svare ud fra kilderne.",
    )

    assert rendered["answer"] == (
        "Arrestationen påvirkede ikke hans politiske stilling. "
        "[p-arrest](https://example.test/edition)\n"
        '> "Arrestationen påvirkede ikke hans politiske stilling."'
    )
    assert len(rendered["claims"]) == 1
    assert rendered["rejected_claim_count"] == 1
    assert rendered["rejected_claims"][0]["reason"] == "quote_not_verbatim"
    assert rendered["abstained"] is False


def test_grounding_gate_abstains_when_no_claim_has_a_verbatim_citation():
    rendered = render_grounded_answer(
        {"answer": "Unsupported statement.", "claims": []},
        {"p-arrest": "Arrestationen påvirkede ikke hans politiske stilling."},
        abstention_text="Jeg kan ikke svare ud fra kilderne.",
    )

    assert rendered["answer"] == "Jeg kan ikke svare ud fra kilderne."
    assert rendered["claims"] == []
    assert rendered["abstained"] is True


def test_grounding_gate_uses_a_short_exact_claim_as_quote_when_quote_field_is_missing():
    passage = "Arrestationen påvirkede ikke hans politiske stilling."
    rendered = render_grounded_answer(
        {
            "answer": "The author was politically unaffected by arrest.",
            "claims": [
                {"text": passage, "citation_id": "p-arrest", "quote": None}
            ],
        },
        {"p-arrest": passage},
        abstention_text="Jeg kan ikke svare ud fra kilderne.",
    )

    assert rendered["abstained"] is False
    assert rendered["claims"][0]["quote"] == passage
    assert "[p-arrest]" in rendered["answer"]
