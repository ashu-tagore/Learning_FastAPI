"""Keyword search over an indexed codebase (the same query as `atlas find`)."""

from dataclasses import asdict
from typing import Annotated, Literal

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from atlas.api.deps import SessionDep
from atlas.api.schemas import SearchHit, SearchPage
from atlas.storage import chunk_search_statement, hit_from_row

router = APIRouter(prefix="/search", tags=["search"])


@router.get("", response_model=SearchPage)
async def search(
    session: SessionDep,
    q: Annotated[str, Query(min_length=1, max_length=200, description="Words to look for")],
    repo: str = "pytorch/ignite",
    kind: Literal["code", "doc"] | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    offset: Annotated[int, Query(ge=0, le=1000)] = 0,
) -> SearchPage:
    """Offset pagination: fine here, because nobody reads past the first few pages of results."""
    statement = chunk_search_statement(repo, q, kind=kind, limit=limit + 1, offset=offset)
    hits = [hit_from_row(row) for row in await session.execute(statement)]

    has_next_page = len(hits) > limit
    page = hits[:limit]
    return SearchPage(
        items=[SearchHit(**vars(hit)) for hit in page],
        limit=limit,
        offset=offset,
        next_offset=offset + limit if has_next_page else None,
    )


@router.websocket("/live")
async def search_as_you_type(websocket: WebSocket, repo: str = "pytorch/ignite") -> None:
    """One open connection; each message is a query, each reply the top five hits (lesson 4.14).

    Compared with calling GET /search on every keystroke, the connection (and its TCP and
    TLS handshakes) is set up once, and the server could push updates without being asked.
    """
    await websocket.accept()
    # A session per message, not per connection: a connection may stay open for minutes,
    # and holding a database connection that long would starve everyone else of the pool.
    session_factory = websocket.app.state.session_factory
    try:
        while True:
            query = (await websocket.receive_text()).strip()
            is_blank = query == ""
            if is_blank:
                await websocket.send_json({"query": query, "items": []})
                continue
            async with session_factory() as session:
                rows = await session.execute(chunk_search_statement(repo, query, limit=5))
                hits = [asdict(hit_from_row(row)) for row in rows]
            await websocket.send_json({"query": query, "items": hits})
    except WebSocketDisconnect:
        # The browser tab closed or the user navigated away: nothing to clean up.
        return
