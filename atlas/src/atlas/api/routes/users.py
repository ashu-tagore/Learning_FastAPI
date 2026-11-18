"""The signed-in user's own account."""

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from atlas.api.deps import CurrentUser, SessionDep
from atlas.api.routes.issues import issue_out
from atlas.api.schemas import IssueOut, UserOut, UserUpdate
from atlas.models import Issue, User, saved_issues

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserOut)
async def read_me(user: CurrentUser) -> User:
    return user


@router.patch("/me", response_model=UserOut)
async def update_me(changes: UserUpdate, user: CurrentUser, session: SessionDep) -> User:
    # exclude_unset: only the fields the client actually sent, so PATCH changes just those.
    for field, value in changes.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    await session.commit()
    await session.refresh(user)
    return user


@router.get("/me/saved-issues", response_model=list[IssueOut])
async def list_saved_issues(user: CurrentUser, session: SessionDep) -> list[IssueOut]:
    statement = (
        select(Issue)
        .join(saved_issues, saved_issues.c.issue_id == Issue.id)
        .where(saved_issues.c.user_id == user.id)
        .order_by(saved_issues.c.saved_at.desc())
        # Load every issue's labels and repo in one extra query each, not one per issue (N+1).
        .options(selectinload(Issue.labels), selectinload(Issue.repo))
    )
    return [issue_out(issue) for issue in await session.scalars(statement)]
