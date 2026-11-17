FROM python:3.13-slim

# uv installs dependencies from the lockfile much faster than pip.
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv

WORKDIR /code

# Copy only the dependency files first. Docker caches this layer,
# so editing app code doesn't trigger a full reinstall.
COPY pyproject.toml ./
RUN uv pip install --system -r pyproject.toml

COPY . .

# Apply pending migrations, then start the server.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
