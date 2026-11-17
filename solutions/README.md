# Reference solution

The finished version of the API built in the **FastAPI Backend Path** tutorial.
Your own code in `../app` is untouched. Try each exercise yourself first, then compare.

## What maps to which chapter

| File | Chapters |
|---|---|
| `app/config.py`, `.env.example` | 3. Configuration |
| `app/database.py`, `app/models.py` | 5. Databases and the ORM, 9. Relationships |
| `app/schemas.py` | 4. Pydantic schemas |
| `app/routers/*.py` | 6. Routers, 10. Queries |
| `app/security.py`, `app/routers/auth.py` | 7. Passwords, 8. JWT auth |
| `app/routers/votes.py` | 9. Relationships (many-to-many) |
| `alembic/` | 11. Migrations |
| `app/main.py` (CORS) | 12. CORS and middleware |
| `tests/` | 13. Testing |
| `Dockerfile`, `docker-compose.yml` | 14. Docker and deployment |

## Run the tests (no database needed, they use in-memory SQLite)

```bash
cd solutions
uv sync
uv run pytest
```

## Run against Postgres

```bash
cp .env.example .env
uv run alembic upgrade head
uv run fastapi dev app/main.py
```

Open http://127.0.0.1:8000/docs, create a user, then click **Authorize** and log in.

## Run everything in Docker

```bash
cp .env.example .env
docker compose up --build
```
