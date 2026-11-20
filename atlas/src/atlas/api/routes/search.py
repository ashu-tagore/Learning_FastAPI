"""Keyword search over an indexed codebase (the same query as `atlas find`)."""

import logging
from dataclasses import asdict
from functools import partial
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from atlas.api.deps import EmbedderDep, SessionDep
from atlas.api.limits import ResponseCache, get_cache, limit_per_client
from atlas.api.schemas import RepoFullName, SearchHit, SearchPage
from atlas.llm import LLMError
from atlas.models import Repo
from atlas.storage import SearchMode, chunk_search_statement, hit_from_row, search_chunks

router = APIRouter(prefix="/search", tags=["search"])
logger = logging.getLogger("atlas.api")


# Generous for a person, tight for a script hammering the database (lesson 5.5).
SEARCH_REQUESTS_PER_MINUTE = 60


@router.get(
    "",
    response_model=SearchPage,
    dependencies=[Depends(limit_per_client("search", SEARCH_REQUESTS_PER_MINUTE))],
)
async def search(
    session: SessionDep,
    cache: Annotated[ResponseCache, Depends(get_cache)],
    embedder: EmbedderDep,
    response: Response,
    q: Annotated[str, Query(min_length=1, max_length=200, description="Words to look for")],
    repo: Annotated[RepoFullName, Query()] = "pytorch/ignite",
    mode: SearchMode = "hybrid",
    kind: Literal["code", "doc"] | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    offset: Annotated[int, Query(ge=0, le=1000)] = 0,
) -> SearchPage:
    """Search by words, by meaning, or both (lesson 6.6).

    X-Search-Mode says which mode answered: when the embedding model is unreachable,
    semantic and hybrid searches fall back to keywords rather than fail.
    """
    query_vector: list[float] | None = None
    if mode != "keyword":
        try:
            # The embedding client is synchronous; a thread keeps it off the event loop.
            query_vector = (await run_in_threadpool(embedder.embed, [q], "query"))[0]
        except LLMError:
            logger.warning("embeddings unavailable; searching by keyword", exc_info=True)
            mode = "keyword"
    response.headers["X-Search-Mode"] = mode

    # The data's version: re-indexing or (for semantic results) re-embedding changes it,
    # and with it every cache key (lesson 5.5).
    versions = (
        await session.execute(
            select(Repo.indexed_at, Repo.embedded_at).where(Repo.full_name == repo)
        )
    ).first()
    run = partial(
        run_search, session, q, repo, mode, query_vector, embedder.model, kind, limit, offset
    )
    if versions is None or versions.indexed_at is None:
        response.headers["X-Cache"] = "skip"  # never indexed: nothing worth caching
        return await run()

    version = f"{repo}@{versions.indexed_at.isoformat()}/{versions.embedded_at}"
    params = {"q": q, "mode": mode, "kind": kind, "limit": limit, "offset": offset}
    key = cache.key("search", version, params)
    cached = await cache.get(key)
    if cached is not None:
        response.headers["X-Cache"] = "hit"
        return SearchPage.model_validate(cached)

    page = await run()
    await cache.set(key, page.model_dump(mode="json"))
    response.headers["X-Cache"] = "miss"
    return page


async def run_search(
    session: AsyncSession,
    q: str,
    repo: str,
    mode: SearchMode,
    query_vector: list[float] | None,
    model: str,
    kind: str | None,
    limit: int,
    offset: int,
) -> SearchPage:
    # One more than asked for: if it comes back, there is a next page.
    hits = await session.run_sync(
        lambda sync: search_chunks(
            sync,
            repo,
            q,
            mode,
            query_vector,
            model=model,
            kind=kind,
            limit=limit + 1,
            offset=offset,
        )
    )
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
    """One open connection; each message is a query, each reply the top five hits (lesson 4.18).

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
