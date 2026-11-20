"""Everything Atlas stores in Postgres: indexing a codebase, searching it, and issues.

Functions take a Session and never commit: the caller decides where a transaction
starts and ends, so several operations can succeed or fail together.
"""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from sqlalchemy import Row, Select, case, delete, func, select, text
from sqlalchemy.orm import Session, selectinload

from atlas import github
from atlas.chunking import chunk_file
from atlas.events import IssueEvent
from atlas.models import Chunk, Document, Issue, Label, Repo, issue_labels, saved_issues
from atlas.search import DEFAULT_EXTENSIONS, is_in_skipped_folder, iter_source_files

DOC_EXTENSIONS = {".md", ".rst"}


@dataclass(frozen=True)
class IndexSummary:
    documents: int
    chunks: int
    skipped: int


@dataclass(frozen=True)
class Hit:
    path: str
    start_line: int
    end_line: int
    rank: float
    snippet: str
    symbol: str | None = None


def get_or_create_repo(session: Session, full_name: str) -> Repo:
    repo = session.scalar(select(Repo).where(Repo.full_name == full_name))
    if repo is None:
        repo = Repo(full_name=full_name)
        session.add(repo)
        # Flush sends the INSERT now (inside the open transaction) so repo.id is known.
        session.flush()
    return repo


def read_document(repo: Repo, root: Path, path: Path) -> Document | None:
    """A Document with its chunks for one file, or None if it can't be stored as text."""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None
    # Postgres text columns cannot hold the NUL character.
    if "\x00" in text:
        return None

    lines = text.splitlines()
    relative = path.relative_to(root).as_posix()
    document = Document(
        repo=repo,
        path=relative,
        kind="doc" if path.suffix in DOC_EXTENSIONS else "code",
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        line_count=len(lines),
    )
    # By structure: functions, methods, sections (lesson 6.5).
    document.chunks = [
        Chunk(start_line=p.start_line, end_line=p.end_line, symbol=p.symbol, text=p.text)
        for p in chunk_file(relative, text)
    ]
    return document


def index_folder(
    session: Session,
    repo_full_name: str,
    root: Path,
    *,
    extensions: tuple[str, ...] = DEFAULT_EXTENSIONS,
) -> IndexSummary:
    """Replace the stored copy of a codebase with the files under root."""
    repo = get_or_create_repo(session, repo_full_name)

    # Delete-then-insert inside the caller's transaction: other sessions keep seeing
    # the old index until the commit, then the whole new one at once.
    kept = stored_embeddings(session, repo.id, paths=None)
    session.execute(delete(Document).where(Document.repo_id == repo.id))

    document_count = 0
    chunk_count = 0
    skipped = 0
    for path in iter_source_files(root, extensions):
        document = read_document(repo, root, path)
        if document is None:
            skipped += 1
            continue
        reuse_embeddings(document, kept)
        session.add(document)
        document_count += 1
        chunk_count += len(document.chunks)

    repo.indexed_at = datetime.now(UTC)
    session.flush()
    return IndexSummary(documents=document_count, chunks=chunk_count, skipped=skipped)


# (path, chunk text) -> (vector, model that made it)
StoredEmbeddings = dict[tuple[str, str], tuple[list[float], str]]


def stored_embeddings(session: Session, repo_id: int, paths: list[str] | None) -> StoredEmbeddings:
    """The vectors of chunks about to be deleted, so unchanged ones can keep theirs (6.5)."""
    statement = (
        select(Document.path, Chunk.text, Chunk.embedding, Chunk.embedding_model)
        .join(Chunk.document)
        .where(Document.repo_id == repo_id, Chunk.embedding.is_not(None))
    )
    if paths is not None:
        statement = statement.where(Document.path.in_(paths))
    kept: StoredEmbeddings = {}
    for path, chunk_text, vector, model in session.execute(statement):
        if vector is None or model is None:
            continue
        kept[(path, chunk_text)] = (list(vector), model)
    return kept


def reuse_embeddings(document: Document, kept: StoredEmbeddings) -> None:
    """Give each new chunk the vector of an identical old chunk in the same file, if any.

    Embedding is the slow, costly step of indexing; a one-line change to a file shouldn't
    mean re-embedding every function in it.
    """
    for chunk in document.chunks:
        previous = kept.get((document.path, chunk.text))
        if previous is not None:
            chunk.embedding, chunk.embedding_model = previous


def update_index(
    session: Session,
    repo_full_name: str,
    root: Path,
    *,
    changed: list[str],
    deleted: list[str],
    extensions: tuple[str, ...] = DEFAULT_EXTENSIONS,
) -> IndexSummary:
    """Re-index only the files a commit changed (lesson 5.3); every other file is untouched."""
    repo = get_or_create_repo(session, repo_full_name)
    kept = stored_embeddings(session, repo.id, paths=changed)
    session.execute(
        delete(Document).where(Document.repo_id == repo.id, Document.path.in_([*changed, *deleted]))
    )

    document_count = 0
    chunk_count = 0
    skipped = 0
    for relative in changed:
        path = root / relative
        is_wanted = path.suffix in extensions and not is_in_skipped_folder(Path(relative))
        if not is_wanted:
            continue
        document = read_document(repo, root, path)
        if document is None:
            skipped += 1
            continue
        reuse_embeddings(document, kept)
        session.add(document)
        document_count += 1
        chunk_count += len(document.chunks)

    repo.indexed_at = datetime.now(UTC)
    session.flush()
    return IndexSummary(documents=document_count, chunks=chunk_count, skipped=skipped)


def chunk_search_statement(
    repo_full_name: str,
    query: str,
    *,
    kind: str | None = None,
    limit: int = 10,
    offset: int = 0,
) -> Select[str, int, int, float, str]:
    """The full-text search query, built once and run by the CLI and the web API alike."""
    # websearch_to_tsquery understands what people type into search boxes:
    # quoted phrases, "or", and -excluded words.
    ts_query = func.websearch_to_tsquery("english", query)
    rank = func.ts_rank(Chunk.tsv, ts_query)
    snippet = func.ts_headline(
        "english",
        Chunk.text,
        ts_query,
        "StartSel=[, StopSel=], MaxWords=14, MinWords=5, MaxFragments=1",
    )
    statement = (
        select(Document.path, Chunk.start_line, Chunk.end_line, rank, snippet)
        .join(Chunk.document)
        .join(Document.repo)
        .where(Repo.full_name == repo_full_name, Chunk.tsv.bool_op("@@")(ts_query))
        # Path and line break ties so results come out the same on every run.
        .order_by(rank.desc(), Document.path, Chunk.start_line)
        .limit(limit)
        .offset(offset)
    )
    if kind is not None:
        statement = statement.where(Document.kind == kind)
    return statement


def hit_from_row(row: Row[str, int, int, float, str]) -> Hit:
    path, start, end, score, text = row
    # The snippet can span lines; one line reads better in a terminal or a web page.
    return Hit(
        path=path, start_line=start, end_line=end, rank=float(score), snippet=" ".join(text.split())
    )


def find_chunks(session: Session, repo_full_name: str, query: str, limit: int = 10) -> list[Hit]:
    """Full-text search: chunks matching the query's words, best match first."""
    statement = chunk_search_statement(repo_full_name, query, limit=limit)
    return [hit_from_row(row) for row in session.execute(statement)]


# ---------- Semantic and hybrid search (lesson 6.6) ----------

SearchMode = Literal["keyword", "semantic", "hybrid"]
# Each ranking contributes this many candidates to the fusion: enough to cover the pages
# people read, few enough to keep both queries fast.
CANDIDATES = 50
# The constant from the paper that introduced reciprocal rank fusion (Cormack, Clarke and
# Buettcher, 2009); it damps the difference between the very top ranks.
RRF_K = 60

# (chunk id, score), best first
Ranking = list[tuple[int, float]]


def keyword_ranking(
    session: Session, repo_full_name: str, query: str, kind: str | None, limit: int
) -> Ranking:
    ts_query = func.websearch_to_tsquery("english", query)
    rank = func.ts_rank(Chunk.tsv, ts_query)
    statement = (
        select(Chunk.id, rank)
        .join(Chunk.document)
        .join(Document.repo)
        .where(Repo.full_name == repo_full_name, Chunk.tsv.bool_op("@@")(ts_query))
        .order_by(rank.desc(), Document.path, Chunk.start_line)
        .limit(limit)
    )
    if kind is not None:
        statement = statement.where(Document.kind == kind)
    return [(chunk_id, float(score)) for chunk_id, score in session.execute(statement)]


def semantic_ranking(
    session: Session,
    repo_full_name: str,
    query_vector: list[float],
    model: str,
    kind: str | None,
    limit: int,
) -> Ranking:
    """The chunks whose vectors are nearest the query's, by cosine distance."""
    # An HNSW search keeps at most hnsw.ef_search candidates (40 by default), so without
    # this a request for 50 silently returns 40 (lesson 6.6). SET LOCAL lasts only until
    # the end of this transaction. int() because SET can't take a bound parameter.
    candidates = max(int(limit), 40)
    session.execute(text(f"SET LOCAL hnsw.ef_search = {candidates}"))
    distance = Chunk.embedding.cosine_distance(query_vector)
    statement = (
        select(Chunk.id, distance)
        .join(Chunk.document)
        .join(Document.repo)
        # Vectors from another model live in another space: comparing them is meaningless.
        .where(Repo.full_name == repo_full_name, Chunk.embedding_model == model)
        .order_by(distance)
        .limit(limit)
    )
    if kind is not None:
        statement = statement.where(Document.kind == kind)
    # Reported as similarity (1 is identical), so that higher is better, as for keywords.
    return [(chunk_id, 1 - float(dist)) for chunk_id, dist in session.execute(statement)]


def reciprocal_rank_fusion(rankings: list[list[int]], k: int = RRF_K) -> Ranking:
    """Merge rankings by position alone: each item scores 1 / (k + rank) in each list.

    Keyword scores and cosine similarities are on unrelated scales, so adding them would
    let one dominate by accident. Positions are comparable: first is first.
    """
    scores: dict[int, float] = {}
    for ranking in rankings:
        for position, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1 / (k + position)
    # Ties (rare) are broken by id, so results are the same on every run.
    return sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))


def search_chunks(
    session: Session,
    repo_full_name: str,
    query: str,
    mode: SearchMode,
    query_vector: list[float] | None = None,
    *,
    model: str | None = None,
    kind: str | None = None,
    limit: int = 10,
    offset: int = 0,
) -> list[Hit]:
    """Search one repository by words, by meaning, or both (hybrid), best first."""
    wanted = max(CANDIDATES, offset + limit)
    keyword: Ranking = []
    semantic: Ranking = []
    if mode in ("keyword", "hybrid"):
        keyword = keyword_ranking(session, repo_full_name, query, kind, wanted)
    can_use_vectors = query_vector is not None and model is not None
    if mode in ("semantic", "hybrid") and can_use_vectors:
        assert query_vector is not None and model is not None  # for mypy
        semantic = semantic_ranking(session, repo_full_name, query_vector, model, kind, wanted)

    if mode == "keyword":
        ranked = keyword
    elif mode == "semantic":
        ranked = semantic
    else:
        ranked = reciprocal_rank_fusion([[i for i, _ in keyword], [i for i, _ in semantic]])
    return load_hits(session, query, ranked[offset : offset + limit])


def load_hits(session: Session, query: str, ranked: Ranking) -> list[Hit]:
    """The chunks of a ranking, in its order, with a highlighted snippet each."""
    if not ranked:
        return []
    ts_query = func.websearch_to_tsquery("english", query)
    snippet = func.ts_headline(
        "english",
        Chunk.text,
        ts_query,
        "StartSel=[, StopSel=], MaxWords=14, MinWords=5, MaxFragments=1",
    )
    rows = session.execute(
        select(Chunk.id, Document.path, Chunk.start_line, Chunk.end_line, Chunk.symbol, snippet)
        .join(Chunk.document)
        .where(Chunk.id.in_([chunk_id for chunk_id, _ in ranked]))
    ).all()
    by_id = {row[0]: row for row in rows}
    hits = []
    for chunk_id, score in ranked:
        _, path, start, end, symbol, text = by_id[chunk_id]
        hits.append(Hit(path, start, end, score, " ".join(text.split()), symbol))
    return hits


def save_issues(session: Session, repo_full_name: str, issues: list[github.Issue]) -> int:
    """Make the stored open issues match the fetched list; return how many are stored."""
    repo = get_or_create_repo(session, repo_full_name)
    stored_numbers = set(session.scalars(select(Issue.number).where(Issue.repo_id == repo.id)))
    no_longer_open = stored_numbers - {issue.number for issue in issues}
    apply_issue_changes(session, repo_full_name, issues, list(no_longer_open))
    return len(issues)


def apply_issue_changes(
    session: Session,
    repo_full_name: str,
    open_issues: list[github.Issue],
    closed_numbers: list[int],
    poll: str = "full",
) -> list[IssueEvent]:
    """Store new and updated open issues, remove closed ones, and say what changed.

    `poll` names the GitHub poll these changes came from (lesson 5.6). A retry of the same
    poll gives the same name, so the events it returns get the same keys.
    """
    repo = get_or_create_repo(session, repo_full_name)
    labels_by_name = {
        label.name: label
        for label in session.scalars(select(Label).where(Label.repo_id == repo.id))
    }
    stored_by_number = {
        issue.number: issue
        for issue in session.scalars(
            # selectinload: every stored issue's labels in one more query, not one each.
            select(Issue).where(Issue.repo_id == repo.id).options(selectinload(Issue.labels))
        )
    }
    savers_by_issue_id: dict[int, list[int]] = {}
    for issue_id, user_id in session.execute(
        select(saved_issues.c.issue_id, saved_issues.c.user_id)
        .join(Issue, Issue.id == saved_issues.c.issue_id)
        .where(Issue.repo_id == repo.id)
    ):
        savers_by_issue_id.setdefault(issue_id, []).append(user_id)

    events: list[IssueEvent] = []

    def record(row: Issue, change: str, labels: list[str]) -> None:
        events.append(
            IssueEvent(
                repo=repo_full_name,
                number=row.number,
                title=row.title,
                change=change,
                labels=labels,
                saved_by=sorted(savers_by_issue_id.get(row.id, [])),
                key=f"{repo_full_name}#{row.number}:{change}:{poll}",
            )
        )

    for number in closed_numbers:
        stored = stored_by_number.get(number)
        if stored is not None:
            # Recorded before the delete, while who saved it is still known.
            record(stored, "closed", sorted(label.name for label in stored.labels))
            session.delete(stored)

    for fetched in open_issues:
        row = stored_by_number.get(fetched.number)
        is_new = row is None
        old_labels = set() if row is None else {label.name for label in row.labels}
        if row is None:
            row = Issue(repo_id=repo.id, number=fetched.number)
            session.add(row)

        row.title = fetched.title
        row.url = fetched.url
        row.author = fetched.author
        row.comments = fetched.comments
        row.created_at = fetched.created_at
        row.labels = [
            _get_or_create_label(session, repo, labels_by_name, name) for name in fetched.labels
        ]

        new_labels = sorted(fetched.labels)
        if is_new:
            record(row, "opened", new_labels)
        elif old_labels != set(new_labels):
            record(row, "relabelled", new_labels)

    session.flush()
    return events


def _get_or_create_label(
    session: Session, repo: Repo, labels_by_name: dict[str, Label], name: str
) -> Label:
    label = labels_by_name.get(name)
    if label is None:
        label = Label(repo_id=repo.id, name=name)
        session.add(label)
        labels_by_name[name] = label
    return label


def label_counts(session: Session, repo_full_name: str) -> list[tuple[str, int]]:
    """How many stored open issues carry each label, most common first."""
    issue_count = func.count(issue_labels.c.issue_id)
    statement = (
        select(Label.name, issue_count)
        .join(issue_labels, issue_labels.c.label_id == Label.id)
        .join(Repo, Repo.id == Label.repo_id)
        .where(Repo.full_name == repo_full_name)
        .group_by(Label.name)
        .order_by(issue_count.desc(), Label.name)
    )
    return [(name, count) for name, count in session.execute(statement)]


def age_buckets(session: Session, repo_full_name: str, now: datetime) -> list[tuple[str, int]]:
    """Stored open issues grouped by how long they have been open."""
    age_in_days = func.extract("epoch", now - Issue.created_at) / 86400
    bucket = case(
        (age_in_days < 30, "under 30 days"),
        (age_in_days < 180, "30 to 180 days"),
        else_="over 180 days",
    )
    statement = (
        select(bucket, func.count())
        .join(Repo, Repo.id == Issue.repo_id)
        .where(Repo.full_name == repo_full_name)
        .group_by(bucket)
    )
    counts: dict[str, int] = {name: count for name, count in session.execute(statement)}
    order = ["under 30 days", "30 to 180 days", "over 180 days"]
    return [(name, counts.get(name, 0)) for name in order]
