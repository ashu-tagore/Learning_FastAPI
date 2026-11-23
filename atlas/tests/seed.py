"""A little of everything, so migrations can be tested on a database that has data (7.2).

    DATABASE_URL=... python -m tests.seed

A migration that works on an empty database can still fail on a real one: adding a
NOT NULL column without a default passes on empty tables and fails on any table with
rows. CI runs the newest migration again on this data (downgrade one step, upgrade) so
that failure happens in CI, not during a deploy.

Rows are built with the models, so the seed always matches the current schema; when a
table is added, add a row here.
"""

import os
import sys
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from atlas.models import (
    Chunk,
    Citation,
    Conversation,
    Document,
    Feedback,
    Issue,
    IssueNote,
    Job,
    Label,
    LlmCall,
    Message,
    Notification,
    Repo,
    User,
)


def seed(session: Session) -> None:
    now = datetime.now(UTC)
    repo = Repo(full_name="pytorch/ignite", indexed_at=now, indexed_commit="0" * 40)
    user = User(email="seed@example.com", password_hash="not-a-real-hash")
    session.add_all([repo, user])
    session.flush()

    document = Document(
        repo_id=repo.id, path="ignite/metrics.py", kind="code", content_hash="0" * 64, line_count=2
    )
    session.add(document)
    session.flush()
    session.add(
        Chunk(
            document_id=document.id,
            start_line=1,
            end_line=2,
            symbol="accuracy",
            text="def accuracy():\n    return 1.0",
        )
    )

    label = Label(repo_id=repo.id, name="good first issue")
    issue = Issue(
        repo_id=repo.id,
        number=1,
        title="Seed issue",
        url="https://github.com/pytorch/ignite/issues/1",
        author="seed",
        comments=0,
        created_at=now,
        labels=[label],
    )
    session.add(issue)
    session.add(IssueNote(user_id=user.id, repo_id=repo.id, issue_number=1, body="A note."))
    session.add(
        Notification(
            user_id=user.id,
            repo_full_name=repo.full_name,
            issue_number=1,
            title="Seed issue",
            change="closed",
            event_key="seed",
        )
    )
    session.add(Job(kind="sync_repo", repo_id=repo.id, status="succeeded"))

    call = LlmCall(
        provider="ollama",
        model="qwen3:1.7b",
        purpose="answer",
        input_tokens=10,
        output_tokens=5,
        cost_usd=Decimal(0),
        latency_ms=1.0,
        stop_reason="end",
    )
    conversation = Conversation(user_id=user.id, repo_full_name=repo.full_name, title="Seed")
    session.add_all([call, conversation])
    session.flush()
    question = Message(conversation_id=conversation.id, role="user", content="What?")
    answer = Message(
        conversation_id=conversation.id, role="assistant", content="This [1].", llm_call_id=call.id
    )
    session.add_all([question, answer])
    session.flush()
    session.add(
        Citation(
            message_id=answer.id,
            number=1,
            path="ignite/metrics.py",
            start_line=1,
            end_line=2,
            symbol="accuracy",
            commit="0" * 40,
        )
    )
    session.add(Feedback(message_id=answer.id, rating="up"))


if __name__ == "__main__":
    url = os.environ.get("DATABASE_URL")
    if url is None:
        sys.exit("set DATABASE_URL")
    with Session(create_engine(url)) as session, session.begin():
        seed(session)
    print("seeded one row of each kind")
