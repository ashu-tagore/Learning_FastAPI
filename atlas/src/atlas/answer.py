"""Answering questions from the indexed code, with citations (lesson 6.7).

    sources = find_sources(session, "pytorch/ignite", question, query_vector, model)
    request = answer_request(question, sources, history=[])
    result = chat.complete(request)
    cited, invalid = check_citations(result.text, sources)

This is retrieval-augmented generation (RAG): search first, then ask the model to answer
from what the search found, citing it by number. The model never sees the whole codebase,
only the few chunks most likely to hold the answer.

The rule from the product spec: no citation, no claim. When nothing relevant is found,
Atlas says so without asking a model at all, so it can't guess.
"""

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.llm import ChatRequest, Message, user
from atlas.models import Chunk, Document, Repo
from atlas.storage import CANDIDATES, keyword_ranking, reciprocal_rank_fusion, semantic_ranking

# How many chunks the model reads. More gives it more chances to find the answer, and
# makes every answer slower and costlier: a small CPU model reads about 100 tokens a second.
SOURCES = 5
# Below this cosine similarity (with nomic-embed-text), the nearest chunk is about
# something else. Measured on lesson 6.8's questions: answerable ones scored 0.62 to 0.81,
# everyday ones ("the capital of France") 0.55 to 0.59. Technical questions the code can't
# answer (Kubernetes, 0.65) pass the gate; the prompt's "couldn't find" rule must catch them.
# A different embedding model needs this measured again.
MIN_SIMILARITY = 0.6
# Earlier exchanges sent with each question, so "and its arguments?" makes sense.
HISTORY_TURNS = 3
NOT_FOUND = "I couldn't find this in the indexed code."

SYSTEM = (
    "You answer questions about a software project's code and documentation, using only "
    "the numbered sources you are given. Never use outside knowledge. If the sources don't "
    f'answer the question, reply exactly: "{NOT_FOUND}"'
)

# Repeated after the sources, right before the model writes: a small model forgot the
# system prompt's citation rule after reading 1,300 tokens of code (lesson 6.7). The
# example shows only the format: an earlier example sentence ("EarlyStopping takes a
# patience argument [2].") turned up, word for word, in an answer about F1 scores (6.8).
CITATION_RULE = (
    "Answer in at most 150 words. End every sentence that uses a source with the source's "
    "number in square brackets, like this: [2]. Use only the sources above."
)

CITATION = re.compile(r"\[(\d+)\]")


@dataclass(frozen=True)
class Source:
    number: int  # how the model cites it: [1], [2], ...
    path: str
    start_line: int
    end_line: int
    symbol: str | None
    text: str
    commit: str | None  # None for a repository indexed from a folder, not synced from Git

    @property
    def location(self) -> str:
        return f"{self.path}:{self.start_line}-{self.end_line}"


@dataclass(frozen=True)
class Turn:
    """One earlier question and its answer."""

    question: str
    answer: str


def github_url(
    repo_full_name: str, commit: str | None, path: str, start_line: int, end_line: int
) -> str | None:
    """A link to the cited lines, at the commit the answer was based on."""
    if commit is None:
        return None
    return f"https://github.com/{repo_full_name}/blob/{commit}/{path}#L{start_line}-L{end_line}"


def find_sources(
    session: Session,
    repo_full_name: str,
    question: str,
    query_vector: list[float],
    model: str,
    limit: int = SOURCES,
) -> list[Source]:
    """The chunks most likely to answer the question, best first; none if nothing is close.

    Hybrid search (lesson 6.6), with one gate in front: if even the nearest chunk by meaning
    is far away, the question isn't about this code, and no source is better than a wrong one.
    """
    semantic = semantic_ranking(session, repo_full_name, query_vector, model, None, CANDIDATES)
    nothing_close = not semantic or semantic[0][1] < MIN_SIMILARITY
    if nothing_close:
        return []

    keyword = keyword_ranking(session, repo_full_name, question, None, CANDIDATES)
    fused = reciprocal_rank_fusion([[i for i, _ in keyword], [i for i, _ in semantic]])
    chunk_ids = [chunk_id for chunk_id, _ in fused[:limit]]

    commit = session.scalar(select(Repo.indexed_commit).where(Repo.full_name == repo_full_name))
    rows = session.execute(
        select(Chunk.id, Document.path, Chunk.start_line, Chunk.end_line, Chunk.symbol, Chunk.text)
        .join(Chunk.document)
        .where(Chunk.id.in_(chunk_ids))
    ).all()
    by_id = {row.id: row for row in rows}
    sources = []
    for number, chunk_id in enumerate(chunk_ids, start=1):
        row = by_id[chunk_id]
        source = Source(
            number, row.path, row.start_line, row.end_line, row.symbol, row.text, commit
        )
        sources.append(source)
    return sources


# "Explain this file" (lesson 6.9) reads the file's own chunks, in order, up to this many:
# about 360 lines, which a CPU model reads in roughly half a minute.
EXPLAIN_SOURCES = 6


def file_sources(
    session: Session, repo_full_name: str, path: str, limit: int = EXPLAIN_SOURCES
) -> tuple[list[Source], bool]:
    """A file's chunks in line order, numbered from 1, and whether the file had more.

    No search: the question names the file, so its chunks are the sources.
    """
    commit = session.scalar(select(Repo.indexed_commit).where(Repo.full_name == repo_full_name))
    rows = session.execute(
        select(Chunk.start_line, Chunk.end_line, Chunk.symbol, Chunk.text)
        .join(Chunk.document)
        .join(Document.repo)
        .where(Repo.full_name == repo_full_name, Document.path == path)
        .order_by(Chunk.start_line)
        .limit(limit + 1)  # one more than needed: if it comes back, the file is longer
    ).all()
    truncated = len(rows) > limit
    sources = [
        Source(number, path, row.start_line, row.end_line, row.symbol, row.text, commit)
        for number, row in enumerate(rows[:limit], start=1)
    ]
    return sources, truncated


def explain_question(path: str, truncated: bool) -> str:
    question = f"Explain what {path} does, part by part, for someone new to the project."
    if truncated:
        question += " The sources cover only the start of the file; say so."
    return question


def retrieval_text(question: str, history: list[Turn]) -> str:
    """What to search for. A follow-up like "what are its arguments?" names nothing; the
    previous question usually does, so the two are searched together."""
    if not history:
        return question
    return f"{history[-1].question}\n{question}"


def answer_request(question: str, sources: list[Source], history: list[Turn]) -> ChatRequest:
    """The model's input: earlier turns, then the sources, the question and the rules."""
    messages: list[Message] = []
    for turn in history[-HISTORY_TURNS:]:
        # Earlier answers without their sources: resending every turn's code would make
        # each question costlier than the last.
        messages.append(Message("user", turn.question))
        messages.append(Message("assistant", turn.answer))

    blocks = []
    for source in sources:
        heading = f"[{source.number}] {source.path} lines {source.start_line}-{source.end_line}"
        if source.symbol:
            heading += f" ({source.symbol})"
        blocks.append(f"{heading}\n{source.text}")
    prompt = "Sources:\n\n" + "\n\n".join(blocks) + f"\n\nQuestion: {question}\n\n{CITATION_RULE}"
    messages.append(user(prompt))
    return ChatRequest(messages=messages, system=SYSTEM, max_tokens=600, purpose="answer")


def check_citations(text: str, sources: list[Source]) -> tuple[list[Source], list[int]]:
    """The sources the answer cites, in order of first mention, and any numbers it made up.

    A model can cite [7] when it was given five sources; that citation points nowhere,
    and the caller should say so rather than drop it silently.
    """
    by_number = {source.number: source for source in sources}
    cited: list[Source] = []
    invalid: list[int] = []
    for match in CITATION.finditer(text):
        number = int(match.group(1))
        source = by_number.get(number)
        if source is None:
            if number not in invalid:
                invalid.append(number)
            continue
        if source not in cited:
            cited.append(source)
    return cited, invalid
