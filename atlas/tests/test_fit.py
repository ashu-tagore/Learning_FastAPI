"""Issue fit scoring with structured outputs validated by Pydantic (lesson 6.3)."""

from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from atlas import github, storage
from atlas.fit import FitError, FitScore, profile_hash, score_issue, score_user_issues
from atlas.llm.ollama import OllamaChat
from atlas.models import Issue, IssueFit, LlmCall, User
from tests.conftest import sign_up
from tests.fake_llm import FakeOllama

PROFILE = "Comfortable with Python and pytest; new to PyTorch; happy to write docs."
GOOD = '{"score": 4, "reasons": ["Docs only", "Small change"], "first_step": "Open engine.py"}'


@pytest.fixture
def ollama() -> FakeOllama:
    return FakeOllama()


@pytest.fixture
def chat(ollama: FakeOllama) -> OllamaChat:
    return OllamaChat("qwen3:1.7b", transport=ollama.transport())


def an_issue(number: int = 12, labels: tuple[str, ...] = ("good first issue",)) -> github.Issue:
    return github.Issue(
        number=number,
        title="Fix docstring typo in Engine.run",
        url=f"https://github.com/pytorch/ignite/issues/{number}",
        author="contributor",
        labels=labels,
        comments=1,
        created_at=datetime(2026, 11, 2, tzinfo=UTC),
    )


def test_a_valid_reply_becomes_a_fit_score(chat: OllamaChat, ollama: FakeOllama) -> None:
    ollama.replies.append(GOOD)

    fit, calls = score_issue(chat, PROFILE, an_issue())

    assert fit == FitScore(
        score=4, reasons=["Docs only", "Small change"], first_step="Open engine.py"
    )
    assert [call.purpose for call in calls] == ["fit_score"]
    sent = ollama.requests[0]
    assert PROFILE in sent["messages"][-1]["content"]
    assert "Fix docstring typo" in sent["messages"][-1]["content"]


def test_an_invalid_reply_is_retried_with_the_error(chat: OllamaChat, ollama: FakeOllama) -> None:
    # Valid JSON, wrong content: a score of 9 on a 1 to 5 scale, and no first step.
    ollama.replies.append('{"score": 9, "reasons": ["Easy"]}')
    ollama.replies.append(GOOD)

    fit, calls = score_issue(chat, PROFILE, an_issue())

    assert fit.score == 4
    assert len(calls) == 2
    retry = ollama.requests[1]["messages"]
    assert retry[-2] == {"role": "assistant", "content": '{"score": 9, "reasons": ["Easy"]}'}
    assert "less than or equal to 5" in retry[-1]["content"]
    assert "first_step" in retry[-1]["content"]


def test_two_invalid_replies_give_up_clearly(chat: OllamaChat, ollama: FakeOllama) -> None:
    ollama.replies.extend(["not json at all", '{"score": "high"}'])

    with pytest.raises(FitError, match="no valid fit score after 2 attempts"):
        score_issue(chat, PROFILE, an_issue())


def test_a_cut_off_reply_is_not_retried_as_if_it_were_wrong(
    chat: OllamaChat, ollama: FakeOllama
) -> None:
    ollama.replies.append('{"score": 4, "reasons": ["Docs')
    ollama.done_reason = "length"

    with pytest.raises(FitError, match="cut off"):
        score_issue(chat, PROFILE, an_issue())
    assert len(ollama.requests) == 1


def test_scores_are_stored_and_only_redone_when_the_profile_changes(
    session: Session, chat: OllamaChat, ollama: FakeOllama
) -> None:
    storage.save_issues(session, "pytorch/ignite", [an_issue(12), an_issue(7)])
    user = User(email="learner@example.com", password_hash="x", skills_profile=PROFILE)
    session.add(user)
    session.flush()
    ollama.replies.extend([GOOD, GOOD])

    first = score_user_issues(session, chat, user, "pytorch/ignite")
    again = score_user_issues(session, chat, user, "pytorch/ignite")
    user.skills_profile = PROFILE + " Also know NumPy."
    ollama.replies.extend([GOOD, GOOD])
    after_edit = score_user_issues(session, chat, user, "pytorch/ignite")

    assert (first.scored, again.scored, after_edit.scored) == (2, 0, 2)
    fits = session.scalars(select(IssueFit)).all()
    assert {fit.profile_hash for fit in fits} == {profile_hash(user.skills_profile)}
    assert len(session.scalars(select(LlmCall)).all()) == 4


def test_a_failed_issue_does_not_stop_the_others(
    session: Session, chat: OllamaChat, ollama: FakeOllama
) -> None:
    storage.save_issues(session, "pytorch/ignite", [an_issue(12), an_issue(7)])
    user = User(email="learner@example.com", password_hash="x", skills_profile=PROFILE)
    session.add(user)
    session.flush()
    ollama.replies.extend(["nonsense", "more nonsense", GOOD])  # issue 12 fails twice

    summary = score_user_issues(session, chat, user, "pytorch/ignite")

    assert (summary.scored, summary.failed) == (1, 1)


def test_an_empty_profile_scores_nothing(session: Session, chat: OllamaChat) -> None:
    storage.save_issues(session, "pytorch/ignite", [an_issue(12)])
    user = User(email="learner@example.com", password_hash="x", skills_profile="  ")
    session.add(user)
    session.flush()

    assert score_user_issues(session, chat, user, "pytorch/ignite").scored == 0


@pytest.mark.anyio
async def test_my_fits_come_best_first(
    client: httpx.AsyncClient, async_session: AsyncSession
) -> None:
    headers = await sign_up(client, "learner@example.com")
    await async_session.run_sync(
        lambda sync: storage.save_issues(sync, "pytorch/ignite", [an_issue(12), an_issue(7)])
    )
    user = await async_session.scalar(select(User).where(User.email == "learner@example.com"))
    issue_ids = {
        number: issue_id
        for number, issue_id in await async_session.execute(select(Issue.number, Issue.id))
    }
    assert user is not None
    for number, score in [(12, 2), (7, 5)]:
        async_session.add(
            IssueFit(
                user_id=user.id,
                issue_id=issue_ids[number],
                score=score,
                reasons=["r"],
                first_step="s",
                model="qwen3:1.7b",
                profile_hash=profile_hash(""),
            )
        )
    await async_session.commit()

    response = await client.get("/users/me/fits", headers=headers)

    assert [(f["issue"]["number"], f["score"]) for f in response.json()] == [(7, 5), (12, 2)]
