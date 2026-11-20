"""Measuring retrieval and answers (lesson 6.8)."""

import json
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from atlas import evals, storage
from atlas.answer import NOT_FOUND, Source
from atlas.evals import Case, first_relevant_rank, load_cases, retrieval_scores
from atlas.llm.ollama import OllamaChat
from tests.fake_llm import FakeEmbeddings, FakeOllama
from tests.test_hybrid import embed_sample


def test_the_first_relevant_rank_counts_from_one() -> None:
    paths = ["a.py", "b.py", "c.py", "b.py"]

    assert first_relevant_rank(paths, ["b.py"]) == 2
    assert first_relevant_rank(paths, ["z.py", "c.py"]) == 3
    assert first_relevant_rank(paths, ["z.py"]) is None


def test_recall_and_mean_reciprocal_rank() -> None:
    # Found first, found second, found seventh, never found.
    scores = retrieval_scores([1, 2, 7, None])

    assert scores == {
        "recall_at_5": 0.5,  # two of four in the top 5
        "recall_at_10": 0.75,
        "mrr": round((1 + 1 / 2 + 1 / 7) / 4, 3),
    }


def test_scores_below_a_threshold_are_reported_and_unmeasured_ones_skipped() -> None:
    scores = {
        "retrieval": {"hybrid": {"recall_at_5": 0.6}},
        "answers": {"cites_expected": 0.9, "wrongly_refused": 0.2},
    }
    thresholds = {
        "retrieval.hybrid.recall_at_5": 0.7,
        "answers.cites_expected": 0.8,
        "answers.wrongly_refused": 0.1,  # lower is better
        "answers.supported": 0.5,  # not in this run
    }

    problems = evals.below_thresholds(scores, thresholds)

    assert problems == [
        "retrieval.hybrid.recall_at_5 is 0.6, below the threshold of 0.7",
        "answers.wrongly_refused is 0.2, above the threshold of 0.1",
    ]


def test_the_committed_cases_are_well_formed() -> None:
    # evals/ sits next to atlas/ in the course repository.
    cases = load_cases(Path(__file__).parents[2] / "evals" / "ignite.jsonl")

    assert len({case.id for case in cases}) == len(cases)
    assert sum(case.answerable for case in cases) >= 20
    assert any(not case.answerable for case in cases)


def test_the_judge_reads_sources_and_answer_and_its_verdict_is_validated() -> None:
    ollama = FakeOllama()
    chat = OllamaChat(model="judge", transport=ollama.transport())
    source = Source(1, "a.py", 1, 2, "f", "def f():\n    return 1", None)
    ollama.replies += [
        json.dumps({"supported": False, "unsupported_claims": ["f returns 2"]}),
        "not json at all",
    ]

    verdict = evals.judge_answer(chat, [source], "f returns 2 [1].")
    broken = evals.judge_answer(chat, [source], "f returns 2 [1].")

    assert verdict is not None
    assert verdict.unsupported_claims == ["f returns 2"]
    assert broken is None
    prompt = ollama.requests[0]["messages"][-1]["content"]
    assert "[1] a.py:1-2\ndef f():" in prompt
    assert "f returns 2 [1]." in prompt
    assert ollama.requests[0]["format"]["properties"]["supported"]["type"] == "boolean"


@pytest.fixture
def embedded(session: Session, sample_repo: Path) -> FakeEmbeddings:
    storage.index_folder(session, "pytorch/ignite", sample_repo)
    embedder = FakeEmbeddings()
    embed_sample(session, embedder)
    return embedder


def test_a_whole_run_checks_retrieval_citations_refusals_and_grounding(
    session: Session, embedded: FakeEmbeddings
) -> None:
    ollama = FakeOllama()
    chat = OllamaChat(model="fake-chat", transport=ollama.transport())
    cases = [
        Case("accuracy", "def accuracy return", ["ignite/metrics.py"]),
        Case("bread", "bake bread", []),
    ]
    # The answer to "accuracy", then the judge's verdict on it. "bread" needs no model.
    ollama.replies += [
        "It returns 1.0 [1].",
        json.dumps({"supported": True, "unsupported_claims": []}),
    ]

    report = evals.run_eval(session, chat, embedded, "pytorch/ignite", cases)

    assert report["scores"]["retrieval"]["hybrid"]["recall_at_5"] == 1.0
    assert report["scores"]["answers"] == {
        "cites_expected": 1.0,
        "valid_citations": 1.0,
        "wrongly_refused": 0.0,
        "correctly_refused": 1.0,
        "supported": 1.0,
        "judged": 1,
    }
    bread = report["answers"][1]
    assert (bread["answer"], bread["refused"]) == (NOT_FOUND, True)
    assert len(ollama.requests) == 2
