"""Keyword search over an indexed codebase (the same query as `atlas find`)."""

from typing import Annotated, Literal

from fastapi import APIRouter, Query

from atlas.api.deps import SessionDep
from atlas.api.schemas import SearchHit, SearchPage
from atlas.storage import chunk_search_statement, hit_from_row

router = APIRouter(prefix="/search", tags=["search"])


@router.get("", response_model=SearchPage)
def search(
    session: SessionDep,
    q: Annotated[str, Query(min_length=1, max_length=200, description="Words to look for")],
    repo: str = "pytorch/ignite",
    kind: Literal["code", "doc"] | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    offset: Annotated[int, Query(ge=0, le=1000)] = 0,
) -> SearchPage:
    """Offset pagination: fine here, because nobody reads past the first few pages of results."""
    statement = chunk_search_statement(repo, q, kind=kind, limit=limit + 1, offset=offset)
    hits = [hit_from_row(row) for row in session.execute(statement)]

    has_next_page = len(hits) > limit
    page = hits[:limit]
    return SearchPage(
        items=[SearchHit(**vars(hit)) for hit in page],
        limit=limit,
        offset=offset,
        next_offset=offset + limit if has_next_page else None,
    )
