"""How well does an issue fit a learner? A model's judgment, as validated data (lesson 6.3).

    atlas fit --email ada@example.com --repo pytorch/ignite

The model is asked for JSON matching FitScore. The provider constrains its output to the
schema, and Pydantic then checks it again: constrained output is well-formed, not right.
A reply that fails the check is sent back once with the error, which small models often
fix; a second failure gives up on that issue instead of guessing.
"""

import hashlib
import json
import logging
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from atlas import github
from atlas.llm import ChatProvider, ChatRequest, ChatResult, Message, user
from atlas.llm.calls import record
from atlas.models import Issue, IssueFit, Repo, User

logger = logging.getLogger("atlas.fit")

ATTEMPTS = 2

SYSTEM = """You help newcomers to open source choose an issue to work on.
Given a learner's description of their skills and one GitHub issue, rate how well the
issue fits them:
5 = they could start today with what they already know;
3 = doable, with some learning on the way;
1 = needs skills they haven't mentioned.
Judge only from what they wrote and what the issue shows. Do not assume skills.
Give at most three short reasons, and one concrete first step they could take.
Reply with a single JSON object and nothing else."""


class FitScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: int = Field(ge=1, le=5)
    reasons: list[str] = Field(min_length=1, max_length=3)
    first_step: str = Field(min_length=1, max_length=300)


class FitError(Exception):
    """No usable score for an issue. Carries the calls it took, so their cost is logged."""

    def __init__(self, message: str, calls: list[ChatResult]) -> None:
        super().__init__(message)
        self.calls = calls


def describe(issue: github.Issue) -> str:
    labels = ", ".join(issue.labels) or "none"
    return f"Issue #{issue.number}: {issue.title}\nLabels: {labels}\nComments: {issue.comments}"


def score_issue(
    chat: ChatProvider, profile: str, issue: github.Issue
) -> tuple[FitScore, list[ChatResult]]:
    """Score one issue for one profile; return the score and every call it took."""
    schema = json.dumps(FitScore.model_json_schema())
    prompt = (
        f"The learner's skills:\n{profile}\n\n{describe(issue)}\n\n"
        f"Reply with JSON matching this schema:\n{schema}"
    )
    messages = [user(prompt)]
    calls: list[ChatResult] = []

    for _ in range(ATTEMPTS):
        result = chat.complete(
            ChatRequest(
                messages=messages,
                system=SYSTEM,
                max_tokens=400,
                output_schema=FitScore,
                purpose="fit_score",
            )
        )
        calls.append(result)
        # A cut-off or refused reply isn't the model being wrong; asking again for the
        # same thing with the same limit would just fail the same way.
        if result.stop_reason != "end":
            raise FitError(f"the reply was cut off or refused ({result.stop_reason})", calls)
        try:
            return FitScore.model_validate_json(result.text), calls
        except ValidationError as error:
            messages = [
                *messages,
                Message("assistant", result.text),
                user(f"That reply was not valid:\n{error}\nReply again with only the JSON object."),
            ]

    raise FitError(f"no valid fit score after {ATTEMPTS} attempts", calls)


def profile_hash(profile: str) -> str:
    """Identifies a version of a profile: a stored score is stale once the profile changes."""
    return hashlib.sha256(profile.strip().encode()).hexdigest()


@dataclass(frozen=True)
class FitSummary:
    scored: int
    failed: int
    unchanged: int


def score_user_issues(
    session: Session,
    chat: ChatProvider,
    learner: User,
    repo_full_name: str,
    limit: int | None = None,
) -> FitSummary:
    """Score the repository's open issues for a learner, skipping scores still current."""
    profile = learner.skills_profile.strip()
    if not profile:
        return FitSummary(scored=0, failed=0, unchanged=0)
    current = profile_hash(profile)

    up_to_date = set(
        session.scalars(
            select(IssueFit.issue_id).where(
                IssueFit.user_id == learner.id, IssueFit.profile_hash == current
            )
        )
    )
    issues = session.scalars(
        select(Issue)
        .join(Issue.repo)
        .where(Repo.full_name == repo_full_name)
        .options(selectinload(Issue.labels))
        .order_by(Issue.number.desc())
    ).all()
    to_score = [issue for issue in issues if issue.id not in up_to_date][:limit]

    scored = 0
    failed = 0
    for issue in to_score:
        try:
            fit, calls = score_issue(chat, profile, as_github_issue(issue))
        except FitError as error:
            logger.warning("fit scoring failed", extra={"issue": issue.number, "error": str(error)})
            for call in error.calls:
                record(session, call)
            failed += 1
            continue
        for call in calls:
            record(session, call)
        session.execute(
            delete(IssueFit).where(IssueFit.user_id == learner.id, IssueFit.issue_id == issue.id)
        )
        session.add(
            IssueFit(
                user_id=learner.id,
                issue_id=issue.id,
                score=fit.score,
                reasons=fit.reasons,
                first_step=fit.first_step,
                model=calls[-1].model,
                profile_hash=current,
            )
        )
        scored += 1
    session.flush()
    return FitSummary(scored=scored, failed=failed, unchanged=len(issues) - len(to_score))


def as_github_issue(issue: Issue) -> github.Issue:
    return github.Issue(
        number=issue.number,
        title=issue.title,
        url=issue.url,
        author=issue.author,
        labels=tuple(sorted(label.name for label in issue.labels)),
        comments=issue.comments,
        created_at=issue.created_at,
    )
