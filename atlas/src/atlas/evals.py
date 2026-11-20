"""Measuring retrieval and answers against questions with known answers (lesson 6.8).

    cases = load_cases(Path("evals/ignite.jsonl"))
    report = run_eval(session, chat, embedder, "pytorch/ignite", cases)
    problems = below_thresholds(report["scores"], thresholds)

Each case is a question and the files a good answer must come from; a case with no
expected files is one Atlas should decline. Three kinds of checks:

- Retrieval: does search put an expected file near the top? Exact, cheap, no model.
- Citations: does the answer cite an expected file, and only sources that exist? Exact.
- Grounding: is every claim in the answer supported by what it cites? Only a reader can
  tell, so a model reads (the judge). The judge makes mistakes too, so its verdicts are
  compared with a person's on a fixed set of answers (hand_grades.jsonl).
"""

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from atlas import answer
from atlas.llm import ChatProvider, ChatRequest, EmbeddingProvider, LLMError, user
from atlas.storage import SearchMode, search_chunks

MODES: tuple[SearchMode, ...] = ("keyword", "semantic", "hybrid")
DEPTH = 10  # how far down the results retrieval checks look


@dataclass(frozen=True)
class Case:
    id: str
    question: str
    expected_paths: list[str]

    @property
    def answerable(self) -> bool:
        return bool(self.expected_paths)


def load_cases(path: Path) -> list[Case]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [Case(**json.loads(line)) for line in lines if line.strip()]


# ---------- Retrieval ----------


def first_relevant_rank(paths: list[str], expected: list[str]) -> int | None:
    """The position (from 1) of the first result from an expected file, if any."""
    for position, path in enumerate(paths, start=1):
        if path in expected:
            return position
    return None


def retrieval_scores(ranks: list[int | None]) -> dict[str, float]:
    """recall@k: the share of questions with a relevant result in the top k.
    MRR (mean reciprocal rank): the average of 1/rank, 0 when missing, so a relevant
    result at #1 counts fully, at #2 half, and one never found nothing."""
    count = len(ranks)
    found_in_5 = sum(1 for rank in ranks if rank is not None and rank <= 5)
    found_in_10 = sum(1 for rank in ranks if rank is not None and rank <= 10)
    reciprocal = sum(1 / rank for rank in ranks if rank is not None)
    return {
        "recall_at_5": round(found_in_5 / count, 3),
        "recall_at_10": round(found_in_10 / count, 3),
        "mrr": round(reciprocal / count, 3),
    }


def evaluate_retrieval(
    session: Session,
    repo: str,
    cases: list[Case],
    vectors: dict[str, list[float]],
    model: str,
) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, int | None]]]:
    """Scores per search mode over the answerable cases, and each case's rank per mode."""
    answerable = [case for case in cases if case.answerable]
    ranks: dict[str, dict[str, int | None]] = {case.id: {} for case in answerable}
    for mode in MODES:
        for case in answerable:
            hits = search_chunks(
                session, repo, case.question, mode, vectors[case.id], model=model, limit=DEPTH
            )
            paths = [hit.path for hit in hits]
            ranks[case.id][mode] = first_relevant_rank(paths, case.expected_paths)
    scores: dict[str, dict[str, float]] = {
        mode: retrieval_scores([ranks[case.id][mode] for case in answerable]) for mode in MODES
    }
    return scores, ranks


# ---------- Answers and the judge ----------

JUDGE_SYSTEM = (
    "You check answers about source code. You are given numbered sources and an answer "
    "that cites them. Decide whether every claim in the answer is supported by the sources. "
    "A claim is supported only if a source states it or shows it in code. Ignore style."
)


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supported: bool
    unsupported_claims: list[str] = Field(max_length=5)


def judge_answer(chat: ChatProvider, sources: list[answer.Source], text: str) -> Verdict | None:
    """The judge's verdict on one answer, or None if its reply wasn't a valid verdict."""
    blocks = "\n\n".join(f"[{s.number}] {s.location}\n{s.text}" for s in sources)
    schema = json.dumps(Verdict.model_json_schema())
    prompt = (
        f"Sources:\n\n{blocks}\n\nAnswer:\n{text}\n\n"
        f"Is every claim in the answer supported by the sources? "
        f"Reply with JSON matching this schema:\n{schema}"
    )
    request = ChatRequest(
        messages=[user(prompt)],
        system=JUDGE_SYSTEM,
        max_tokens=400,
        output_schema=Verdict,
        purpose="eval_judge",
    )
    result = chat.complete(request)
    try:
        return Verdict.model_validate_json(result.text)
    except ValidationError:
        return None


@dataclass
class AnswerOutcome:
    case_id: str
    answerable: bool
    answer: str
    refused: bool
    cited_paths: list[str]
    invalid_citations: list[int]
    cites_expected: bool
    supported: bool | None  # the judge's verdict; None when not judged or the judge failed
    unsupported_claims: list[str]
    seconds: float


def answer_case(
    session: Session,
    chat: ChatProvider,
    repo: str,
    case: Case,
    query_vector: list[float],
    model: str,
) -> AnswerOutcome:
    """Answer one question as POST /ask would, then check the answer."""
    started = time.monotonic()
    sources = answer.find_sources(session, repo, case.question, query_vector, model)
    text = answer.NOT_FOUND
    if sources:
        text = chat.complete(answer.answer_request(case.question, sources, history=[])).text
    seconds = time.monotonic() - started

    refused = answer.NOT_FOUND.rstrip(".") in text
    cited, invalid = answer.check_citations(text, sources)
    cited_paths = [source.path for source in cited]
    cites_expected = any(path in case.expected_paths for path in cited_paths)

    # Only answers that make claims are judged: a refusal has nothing to support.
    verdict = None
    should_judge = case.answerable and not refused and bool(cited)
    if should_judge:
        verdict = judge_answer(chat, cited, text)
    return AnswerOutcome(
        case_id=case.id,
        answerable=case.answerable,
        answer=text,
        refused=refused,
        cited_paths=cited_paths,
        invalid_citations=invalid,
        cites_expected=cites_expected,
        supported=verdict.supported if verdict else None,
        unsupported_claims=verdict.unsupported_claims if verdict else [],
        seconds=round(seconds, 1),
    )


def answer_scores(outcomes: list[AnswerOutcome]) -> dict[str, float]:
    answerable = [o for o in outcomes if o.answerable]
    unanswerable = [o for o in outcomes if not o.answerable]
    judged = [o for o in answerable if o.supported is not None]

    def share(count: int, total: int) -> float:
        return round(count / total, 3) if total else 0.0

    return {
        # Answerable questions answered from an expected file.
        "cites_expected": share(sum(o.cites_expected for o in answerable), len(answerable)),
        # Answers whose every [n] points at a real source.
        "valid_citations": share(sum(not o.invalid_citations for o in outcomes), len(outcomes)),
        # Answerable questions Atlas wrongly declined.
        "wrongly_refused": share(sum(o.refused for o in answerable), len(answerable)),
        # Unanswerable questions Atlas correctly declined.
        "correctly_refused": share(sum(o.refused for o in unanswerable), len(unanswerable)),
        # Judged answers whose every claim the judge found supported.
        "supported": share(sum(bool(o.supported) for o in judged), len(judged)),
        "judged": len(judged),
    }


# ---------- The whole run ----------


def run_eval(
    session: Session,
    chat: ChatProvider | None,
    embedder: EmbeddingProvider,
    repo: str,
    cases: list[Case],
) -> dict[str, Any]:
    """Retrieval for every case; answers too when a chat model is given."""
    vectors = dict(
        zip(
            [case.id for case in cases],
            embedder.embed([case.question for case in cases], "query"),
            strict=True,
        )
    )
    retrieval, ranks = evaluate_retrieval(session, repo, cases, vectors, embedder.model)
    scores: dict[str, Any] = {"retrieval": retrieval}
    report: dict[str, Any] = {"cases": len(cases), "scores": scores, "ranks": ranks}
    if chat is None:
        return report

    outcomes = []
    for case in cases:
        try:
            outcome = answer_case(session, chat, repo, case, vectors[case.id], embedder.model)
        except LLMError as error:
            raise LLMError(f"case {case.id}: {error}") from error
        outcomes.append(outcome)
    scores["answers"] = answer_scores(outcomes)
    report["answers"] = [asdict(outcome) for outcome in outcomes]
    return report


def below_thresholds(scores: dict[str, Any], thresholds: dict[str, float]) -> list[str]:
    """Each score under its threshold, as a sentence. Thresholds are named like
    "retrieval.hybrid.recall_at_5" or "answers.cites_expected"; for "wrongly_refused",
    lower is better, so it fails when *above* its threshold."""
    problems = []
    for name, limit in thresholds.items():
        value: Any = scores
        for part in name.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        if value is None:
            continue  # not measured in this run (retrieval only, say)
        lower_is_better = name.endswith("wrongly_refused")
        failed = value > limit if lower_is_better else value < limit
        if failed:
            direction = "above" if lower_is_better else "below"
            problems.append(f"{name} is {value}, {direction} the threshold of {limit}")
    return problems


def judge_agreement(chat: ChatProvider, grades_path: Path) -> dict[str, Any]:
    """How often the judge agrees with a person, on answers a person graded by hand.

    The answers are stored with the grades, so the judge sees exactly what the person saw.
    """
    rows = [json.loads(line) for line in grades_path.read_text().splitlines() if line.strip()]
    agreed = 0
    disagreements = []
    for row in rows:
        sources = [answer.Source(**source) for source in row["sources"]]
        verdict = judge_answer(chat, sources, row["answer"])
        judged = verdict.supported if verdict else None
        if judged == row["supported"]:
            agreed += 1
        else:
            disagreements.append({"id": row["id"], "person": row["supported"], "judge": judged})
    return {"graded": len(rows), "agreed": agreed, "disagreements": disagreements}
