"""The Atlas web API.

Run it with:
    uv run uvicorn atlas.api.app:create_app --factory --reload
"""

import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from http import HTTPStatus

import httpx
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from atlas import __version__
from atlas.api.deps import SessionDep
from atlas.api.routes import auth, issues, notes, repos, search, users
from atlas.api.schemas import ErrorResponse
from atlas.config import ApiSettings

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

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Runs once at startup: one engine (and its connection pool) for the whole process.
        engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        # expire_on_commit=False: objects stay readable after commit without a surprise reload,
        # which in async code would be an implicit (and forbidden) database call.
        app.state.session_factory = async_sessionmaker(engine, expire_on_commit=False)
        yield
        # Runs once at shutdown: close pooled connections cleanly (lesson 2.1's graceful stop).
        await engine.dispose()

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
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    # Added after request_id, so it wraps it: CORS headers are on every response, errors included.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
    )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException) -> JSONResponse:
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
        logger.exception(
            "unhandled error, request_id=%s", getattr(request.state, "request_id", None)
        )
        body = error_body("internal_server_error", "Something went wrong on our side", request)
        return JSONResponse(body, status_code=500)

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
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "The database is unreachable"
            ) from None
        return {"status": "ready"}

    for module in (auth, users, repos, issues, notes, search):
        app.include_router(module.router)

    if settings.web_dir is not None:
        # The ready-made web interface (web/ in the course repository), served as plain files.
        app.mount("/app", StaticFiles(directory=settings.web_dir, html=True), name="web")

    return app
