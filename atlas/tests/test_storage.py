from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from atlas import github, storage
from atlas.models import Chunk, Document, Issue, Label


def make_issue(number: int, labels: tuple[str, ...], days_old: int = 1) -> github.Issue:
    return github.Issue(
        number=number,
        title=f"Issue {number}",
        url=f"https://github.com/pytorch/ignite/issues/{number}",
        author="contributor",
        labels=labels,
        comments=0,
        created_at=datetime(2026, 11, 17, tzinfo=UTC) - timedelta(days=days_old),
    )


def test_split_into_chunks_covers_every_line_once() -> None:
    lines = [f"line {n}" for n in range(1, 6)]

    chunks = list(storage.split_into_chunks(lines, size=2))

    assert [(start, end) for start, end, _ in chunks] == [(1, 2), (3, 4), (5, 5)]
    assert chunks[2][2] == "line 5"


def test_index_stores_documents_and_chunks(session: Session, sample_repo: Path) -> None:
    summary = storage.index_folder(session, "pytorch/ignite", sample_repo, chunk_lines=2)

    paths = set(session.scalars(select(Document.path)))
    assert paths == {"README.md", "ignite/engine/engine.py", "ignite/metrics.py"}
    assert summary == storage.IndexSummary(documents=3, chunks=5, skipped=1)


def test_reindexing_replaces_instead_of_duplicating(session: Session, sample_repo: Path) -> None:
    storage.index_folder(session, "pytorch/ignite", sample_repo)
    (sample_repo / "ignite" / "metrics.py").unlink()

    storage.index_folder(session, "pytorch/ignite", sample_repo)

    assert session.scalar(select(func.count()).select_from(Document)) == 2


def test_documents_are_classified_as_code_or_doc(session: Session, sample_repo: Path) -> None:
    storage.index_folder(session, "pytorch/ignite", sample_repo)

    kinds = {path: kind for path, kind in session.execute(select(Document.path, Document.kind))}
    assert kinds["README.md"] == "doc"
    assert kinds["ignite/metrics.py"] == "code"


def test_find_ranks_matching_chunks_with_line_ranges(session: Session, sample_repo: Path) -> None:
    storage.index_folder(session, "pytorch/ignite", sample_repo, chunk_lines=2)

    hits = storage.find_chunks(session, "pytorch/ignite", "fire event")

    assert [(hit.path, hit.start_line, hit.end_line) for hit in hits] == [
        ("ignite/engine/engine.py", 3, 3)
    ]
    # The snippet highlights words as Postgres's parser sees the original text.
    assert "[event]" in hits[0].snippet


def test_find_uses_word_stems(session: Session, sample_repo: Path) -> None:
    storage.index_folder(session, "pytorch/ignite", sample_repo)

    # "hooks" in the query matches "hook" in README.md: both reduce to the stem "hook".
    hits = storage.find_chunks(session, "pytorch/ignite", "hooks")

    assert [hit.path for hit in hits] == ["README.md"]


def test_find_only_searches_the_named_repo(session: Session, sample_repo: Path) -> None:
    storage.index_folder(session, "pytorch/ignite", sample_repo)

    assert storage.find_chunks(session, "someone/else", "Engine") == []


def test_chunk_text_is_searchable_through_generated_column(
    session: Session, sample_repo: Path
) -> None:
    storage.index_folder(session, "pytorch/ignite", sample_repo)

    tsv = session.scalar(select(Chunk.tsv).join(Chunk.document).where(Document.path == "README.md"))
    assert tsv is not None and "'hook'" in tsv


def test_save_issues_inserts_updates_and_removes_closed(session: Session) -> None:
    storage.save_issues(session, "pytorch/ignite", [make_issue(1, ("bug",)), make_issue(2, ())])

    storage.save_issues(session, "pytorch/ignite", [make_issue(2, ("docs",))])

    stored = session.scalars(select(Issue)).all()
    assert [issue.number for issue in stored] == [2]
    assert [label.name for label in stored[0].labels] == ["docs"]


def test_labels_are_shared_not_duplicated(session: Session) -> None:
    issues = [make_issue(1, ("good first issue",)), make_issue(2, ("good first issue", "docs"))]

    storage.save_issues(session, "pytorch/ignite", issues)

    names = session.scalars(select(Label.name).order_by(Label.name)).all()
    assert names == ["docs", "good first issue"]


def test_label_counts_most_common_first(session: Session) -> None:
    issues = [
        make_issue(1, ("good first issue",)),
        make_issue(2, ("good first issue", "docs")),
        make_issue(3, ("help wanted",)),
    ]
    storage.save_issues(session, "pytorch/ignite", issues)

    assert storage.label_counts(session, "pytorch/ignite") == [
        ("good first issue", 2),
        ("docs", 1),
        ("help wanted", 1),
    ]


def test_age_buckets_include_empty_buckets(session: Session) -> None:
    issues = [make_issue(1, (), days_old=3), make_issue(2, (), days_old=400)]
    storage.save_issues(session, "pytorch/ignite", issues)

    now = datetime(2026, 11, 17, tzinfo=UTC)
    assert storage.age_buckets(session, "pytorch/ignite", now) == [
        ("under 30 days", 1),
        ("30 to 180 days", 0),
        ("over 180 days", 1),
    ]
