"""The Atlas web API.

Run it with:
    uv run uvicorn atlas.api.app:create_app --factory --reload
"""

import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from http import HTTPStatus

import httpx
import redis
import redis.asyncio
from fastapi import FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.exceptions import HTTPException as StarletteHTTPException

from atlas import __version__
from atlas.api.deps import SessionDep
from atlas.api.errors import ApiError
from atlas.api.limits import RateLimiter, ResponseCache
from atlas.api.routes import (
    ask,
    auth,
    digests,
    issues,
    jobs,
    notes,
    notifications,
    repos,
    search,
    users,
    webhooks,
)
from atlas.api.schemas import ErrorResponse
from atlas.config import ApiSettings
from atlas.llm.calls import make_chat, make_embeddings
from atlas.logs import configure_logging, current_request_id
from atlas.queue import RedisQueue
from atlas.snapshots import SnapshotStore, make_s3_client

logger = logging.getLogger("atlas.api")


def error_body(
    code: str, message: str, request: Request, details: object = None
) -> dict[str, object]:
    """Every error from the API has this one shape, so clients handle errors in one place."""
    error: dict[str, object] = {
        "code": code,
        "message": message,
        "request_id": getattr(request.state, "request_id", None),
    }
    if details is not None:
        error["details"] = details
    return {"error": error}


def status_code_name(status_code: int) -> str:
    # 404 -> "not_found": readable, and derived from the standard status phrases.
    return HTTPStatus(status_code).phrase.lower().replace(" ", "_").replace("-", "_")


def create_app(
    settings: ApiSettings | None = None,
    github_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """Build the application. Tests pass their own settings and a fake GitHub."""
    settings = settings or ApiSettings()  # type: ignore[call-arg]  # values come from the environment
    configure_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Runs once at startup: one engine (and its connection pool) for the whole process.
        engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        # expire_on_commit=False: objects stay readable after commit without a surprise reload,
        # which in async code would be an implicit (and forbidden) database call.
        app.state.session_factory = async_sessionmaker(engine, expire_on_commit=False)
        # Connects lazily, on the first send: the API starts even while Redis is down.
        redis_client = redis.Redis.from_url(settings.redis_url)
        app.state.queue = RedisQueue(redis_client)
        # Creating the client makes no network call; nor does signing a download link.
        app.state.snapshots = SnapshotStore(make_s3_client(settings), settings.snapshot_bucket)
        # The asyncio flavour of the same client library: rate limits and the cache are
        # checked on every request, so they mustn't block the event loop (lesson 5.5).
        async_redis = redis.asyncio.Redis.from_url(settings.redis_url)
        app.state.limiter = RateLimiter(async_redis)
        # Embeds search queries for semantic and hybrid search (lesson 6.6).
        app.state.embedder = make_embeddings(settings)
        # Writes answers for POST /ask (lesson 6.7). Makes no network call until used.
        app.state.chat = make_chat(settings)
        app.state.cache = ResponseCache(async_redis)
        yield
        # Runs once at shutdown: close pooled connections cleanly (lesson 2.1's graceful stop).
        await engine.dispose()
        redis_client.close()
        await async_redis.aclose()

    app = FastAPI(
        title="Atlas API",
        version=__version__,
        description="Search a codebase and its issues. Part of Zero to Production in FastAPI.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.github_transport = github_transport

    @app.middleware("http")
    async def request_id(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Reuse the caller's id if it sent one, so one request can be traced across services.
        request.state.request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        # Not reset afterwards: each request runs in its own copy of the context, and the
        # crash handler, which runs after this middleware has finished, still needs the id.
        current_request_id.set(request.state.request_id)
        started = time.perf_counter()
        status_code = 500  # what the client gets if call_next raises
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = request.state.request_id
            return response
        finally:
            # Path only, never the query string or body: they can hold tokens and passwords.
            logger.info(
                "request finished",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )

    # Added after request_id, so it wraps it: CORS headers are on every response, errors included.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
    )

    @app.exception_handler(ApiError)
    async def api_error(request: Request, error: ApiError) -> JSONResponse:
        """Errors routes raise on purpose: their own code and message."""
        body = error_body(error.code, error.message, request)
        return JSONResponse(body, status_code=error.status_code, headers=error.headers)

    # Starlette's class, not FastAPI's subclass: unknown routes (404) and wrong methods (405)
    # are raised by Starlette's router, and FastAPI's class wouldn't catch them (lesson 4.14).
    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, error: StarletteHTTPException) -> JSONResponse:
        """Errors raised by the framework itself: a code derived from the status."""
        body = error_body(status_code_name(error.status_code), str(error.detail), request)
        return JSONResponse(body, status_code=error.status_code, headers=error.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        details = [
            {"location": list(item["loc"]), "message": item["msg"]} for item in error.errors()
        ]
        body = error_body("validation_error", "The request is not valid", request, details)
        return JSONResponse(body, status_code=422)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
        # The full traceback goes to the server log; the client gets an id to quote, not internals.
        logger.exception("unhandled error")
        body = error_body("internal_server_error", "Something went wrong on our side", request)
        # This handler runs outside the request_id middleware, so it adds the header itself.
        request_id = getattr(request.state, "request_id", None)
        headers = {"X-Request-ID": request_id} if request_id else None
        return JSONResponse(body, status_code=500, headers=headers)

    @app.get("/health", tags=["operations"])
    async def health() -> dict[str, str]:
        """Liveness: the process is up and answering."""
        return {"status": "ok"}

    @app.get("/ready", tags=["operations"], responses={503: {"model": ErrorResponse}})
    async def ready(session: SessionDep) -> dict[str, str]:
        """Readiness: the process can reach its database, so it can do real work."""
        try:
            await session.execute(text("SELECT 1"))
        except OperationalError:
            # 503, not 500: nothing is broken in this process, it just can't serve yet.
            # Load balancers stop sending traffic on 503 and retry later (lesson 7.9).
            raise ApiError(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "database_unavailable",
                "The database is unreachable",
            ) from None
        return {"status": "ready"}

    for module in (
        auth,
        users,
        repos,
        issues,
        notes,
        search,
        jobs,
        webhooks,
        notifications,
        digests,
        ask,
    ):
        app.include_router(module.router)

    if settings.web_dir is not None:
        # The ready-made web interface (web/ in the course repository), served as plain files.
        app.mount("/app", StaticFiles(directory=settings.web_dir, html=True), name="web")

    return app
