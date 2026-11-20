"""Turning chunks into vectors, in the background (lessons 6.4 and 6.5).

Embedding is slow on a CPU (about 1.6 chunks a second for nomic-embed-text here), so:
- only chunks without a vector from the current model are embedded;
- each batch is committed on its own, so a crash loses one batch, not an hour;
- a run stops after a time budget, so one job stays well inside its 10-minute lease and
  the queue's 5-minute visibility timeout. The job then queues itself again (jobs.py).
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from atlas.llm import EmbeddingProvider
from atlas.models import Chunk, Document, Repo

# Well under the queue's 5-minute visibility timeout, with room for one slow batch.
SECONDS_PER_RUN = 180


@dataclass(frozen=True)
class EmbedSummary:
    embedded: int
    remaining: int


def embedding_input(path: str, symbol: str | None, text: str) -> str:
    """What gets embedded: the chunk, headed by where it lives.

    A method's body often never says which class or file it's in; the header puts
    "engine.py" and "Engine.run" into the vector, so questions using those words find it.
    """
    header = f"{path}\n{symbol}" if symbol else path
    return f"{header}\n\n{text}"


def embed_pending(
    session_factory: sessionmaker[Session],
    embedder: EmbeddingProvider,
    repo_id: int,
    batch_size: int = 16,
    seconds: float = SECONDS_PER_RUN,
    clock: Callable[[], float] = time.monotonic,
) -> EmbedSummary:
    """Embed the repository's chunks that need it, until done or out of time."""
    needs_embedding = (Document.repo_id == repo_id) & or_(
        Chunk.embedding.is_(None), Chunk.embedding_model != embedder.model
    )
    started = clock()
    embedded = 0
    while True:
        # At least one batch per run, whatever the budget: a run that could stop before
        # doing anything would requeue itself forever without progress.
        out_of_time = embedded > 0 and clock() - started >= seconds
        if out_of_time:
            break
        with session_factory() as session, session.begin():
            batch = session.execute(
                select(Chunk, Document.path)
                .join(Chunk.document)
                .where(needs_embedding)
                .order_by(Chunk.id)
                .limit(batch_size)
            ).all()
            if not batch:
                break
            texts = [embedding_input(path, chunk.symbol, chunk.text) for chunk, path in batch]
            vectors = embedder.embed(texts, "document")
            for (chunk, _), vector in zip(batch, vectors, strict=True):
                chunk.embedding = vector
                chunk.embedding_model = embedder.model
            session.execute(
                update(Repo).where(Repo.id == repo_id).values(embedded_at=datetime.now(UTC))
            )
        embedded += len(batch)

    with session_factory() as session:
        remaining = session.scalar(
            select(func.count()).select_from(Chunk).join(Chunk.document).where(needs_embedding)
        )
    return EmbedSummary(embedded=embedded, remaining=remaining or 0)
