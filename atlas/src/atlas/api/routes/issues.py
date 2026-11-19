"""Open issues: listing with filters and cursor pagination, and saving them for later."""

import base64
import binascii
import json
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import selectinload

from atlas.api.deps import CurrentUser, SessionDep
from atlas.api.schemas import IssueOut, IssuePage, RepoFullName
from atlas.models import Issue, Label, Repo, saved_issues

router = APIRouter(prefix="/issues", tags=["issues"])


def issue_out(issue: Issue) -> IssueOut:
    return IssueOut(
        id=issue.id,
        repo=issue.repo.full_name,
        number=issue.number,
        title=issue.title,
        url=issue.url,
        author=issue.author,
        labels=sorted(label.name for label in issue.labels),
        comments=issue.comments,
        created_at=issue.created_at,
    )


def encode_cursor(last_number: int) -> str:
    # Opaque to clients: they pass it back unchanged, so its format can change later.
    raw = json.dumps({"before": last_number}).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cursor(cursor: str) -> int:
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")))
        return int(payload["before"])
    except (binascii.Error, ValueError, KeyError, TypeError, UnicodeEncodeError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid cursor") from None


@router.get("", response_model=IssuePage)
async def list_issues(
    session: SessionDep,
    repo: Annotated[RepoFullName, Query()] = "pytorch/ignite",
    label: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: str | None = None,
) -> IssuePage:
    """Newest first. Keyset pagination: each page continues below the last issue number seen."""
    statement = (
        select(Issue)
        .join(Issue.repo)
        .where(Repo.full_name == repo)
        .order_by(Issue.number.desc())
        # One more than asked for: if it exists, there is a next page.
        .limit(limit + 1)
        .options(selectinload(Issue.labels), selectinload(Issue.repo))
    )
    if label is not None:
        statement = statement.where(Issue.labels.any(Label.name == label))
    if cursor is not None:
        statement = statement.where(Issue.number < decode_cursor(cursor))

    issues = list(await session.scalars(statement))
    has_next_page = len(issues) > limit
    page = issues[:limit]
    next_cursor = encode_cursor(page[-1].number) if has_next_page else None
    return IssuePage(items=[issue_out(issue) for issue in page], next_cursor=next_cursor)


async def get_issue_or_404(session: SessionDep, issue_id: int) -> Issue:
    # Load what issue_out reads now: in async code a lazy load later would be an error.
    issue = await session.get(
        Issue, issue_id, options=[selectinload(Issue.labels), selectinload(Issue.repo)]
    )
    if issue is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Issue {issue_id} not found")
    return issue


@router.get("/{issue_id}", response_model=IssueOut)
async def read_issue(issue_id: int, session: SessionDep) -> IssueOut:
    return issue_out(await get_issue_or_404(session, issue_id))


@router.put("/{issue_id}/saved", status_code=status.HTTP_204_NO_CONTENT)
async def save_issue(issue_id: int, user: CurrentUser, session: SessionDep) -> Response:
    """PUT is idempotent: saving an already-saved issue changes nothing and still succeeds."""
    await get_issue_or_404(session, issue_id)
    await session.execute(
        insert(saved_issues)
        .values(user_id=user.id, issue_id=issue_id)
        .on_conflict_do_nothing(index_elements=["user_id", "issue_id"])
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{issue_id}/saved", status_code=status.HTTP_204_NO_CONTENT)
async def unsave_issue(issue_id: int, user: CurrentUser, session: SessionDep) -> Response:
    await session.execute(
        delete(saved_issues).where(
            saved_issues.c.user_id == user.id, saved_issues.c.issue_id == issue_id
        )
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
