"""Repositories Atlas knows about. Anyone may list them; only administrators may add one."""

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select

from atlas.api import idempotency
from atlas.api.deps import AdminUser, SessionDep
from atlas.api.schemas import RepoCreate, RepoOut
from atlas.models import Repo

router = APIRouter(prefix="/repos", tags=["repos"])


@router.get("", response_model=list[RepoOut])
def list_repos(session: SessionDep) -> list[Repo]:
    return list(session.scalars(select(Repo).order_by(Repo.full_name)))


@router.post("", status_code=status.HTTP_201_CREATED, response_model=RepoOut)
def add_repo(
    body: RepoCreate,
    admin: AdminUser,
    session: SessionDep,
    request: Request,
    idempotency_key: Annotated[str | None, Header(max_length=100)] = None,
) -> JSONResponse:
    previous = idempotency.replay(session, admin, idempotency_key, request.url.path)
    if previous is not None:
        return previous

    exists = session.scalar(select(Repo.id).where(Repo.full_name == body.full_name)) is not None
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{body.full_name} is already tracked")

    repo = Repo(full_name=body.full_name)
    session.add(repo)
    session.flush()
    payload = RepoOut.model_validate(repo).model_dump(mode="json")
    idempotency.remember(
        session, admin, idempotency_key, request.url.path, status.HTTP_201_CREATED, payload
    )
    session.commit()
    return JSONResponse(payload, status_code=status.HTTP_201_CREATED)
