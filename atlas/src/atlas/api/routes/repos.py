"""Repositories Atlas knows about. Anyone may list them; only administrators may add one."""

from typing import Annotated

from fastapi import APIRouter, Header, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select

from atlas.api import idempotency
from atlas.api.deps import AdminUser, SessionDep
from atlas.api.errors import ApiError
from atlas.api.schemas import RepoCreate, RepoOut
from atlas.models import Repo

router = APIRouter(prefix="/repos", tags=["repos"])


@router.get("", response_model=list[RepoOut])
async def list_repos(session: SessionDep) -> list[Repo]:
    return list(await session.scalars(select(Repo).order_by(Repo.full_name)))


@router.post("", status_code=status.HTTP_201_CREATED, response_model=RepoOut)
async def add_repo(
    body: RepoCreate,
    admin: AdminUser,
    session: SessionDep,
    request: Request,
    idempotency_key: Annotated[str | None, Header(max_length=100)] = None,
) -> JSONResponse:
    previous = await idempotency.replay(session, admin, idempotency_key, request.url.path)
    if previous is not None:
        return previous

    exists = (
        await session.scalar(select(Repo.id).where(Repo.full_name == body.full_name)) is not None
    )
    if exists:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "repo_already_tracked",
            f"{body.full_name} is already tracked",
        )

    repo = Repo(full_name=body.full_name)
    session.add(repo)
    await session.flush()
    payload = RepoOut.model_validate(repo).model_dump(mode="json")
    idempotency.remember(
        session, admin, idempotency_key, request.url.path, status.HTTP_201_CREATED, payload
    )
    await session.commit()
    return JSONResponse(payload, status_code=status.HTTP_201_CREATED)
