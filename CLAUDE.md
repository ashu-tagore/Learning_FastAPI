# Instructions for AI assistants

This repo is **Zero to Production in FastAPI**: a course (Quarto site in `course/`) plus one growing reference project (`atlas/`). Before any work, read `docs/context/README.md` and the docs it links. They are the source of truth; if your task conflicts with them, stop and ask.

## Non-negotiables

- **Atlas never writes to GitHub** and the Issue Brief never contains fix code. See `docs/context/01-vision.md` and `02-product.md`.
- **Course order is build order.** A lesson may only use concepts earlier lessons taught. Reference code at a checkpoint must be what a learner could have written by then.
- **Lessons and code ship together.** Never describe code that doesn't exist or isn't tested.
- **Verify, don't recall.** Check library APIs, model ids and the target project's rules against current sources, and show real output from running code.
- When a decision changes, update the relevant context doc and add an entry to `docs/context/07-decisions.md`. When a milestone changes status, update `docs/context/05-roadmap.md`.

## Code style (summary; full rules in `docs/context/06-conventions.md`)

- Write for human working memory: extract complex conditions into named variables, prefer early returns, keep code linear.
- Deep modules with simple interfaces; no shallow wrapper layers; composition over inheritance.
- Comments explain why, never restate what. No emojis anywhere.
- Original code only.

## Lessons

Follow the template in `docs/context/06-conventions.md`: analogy, theory, diagram, project code, common mistakes, quiz, exercise, stretch, what changed. Plain, direct sentences; define every term on first use.

## Commands

```bash
cd atlas && docker compose up -d                           # local Postgres (Part 3 onward)
export TEST_DATABASE_URL=postgresql+psycopg://atlas:atlas-dev-password@localhost:5432/atlas_test
cd atlas && uv sync && uv run pytest                       # reference tests (DB tests skip without TEST_DATABASE_URL)
cd atlas && uv run atlas init-db                           # run migrations (needs DATABASE_URL)
cd atlas && uv run alembic revision --autogenerate -m "..."  # new migration; review it, rename the revision to the next number
cd atlas && SECRET_KEY=dev-only-key-at-least-32-characters-long WEB_DIR=../web uv run uvicorn atlas.api.app:create_app --factory --reload  # API + web UI at /app
cd atlas && uv run ruff check . && uv run ruff format --check . && uv run mypy src tests
python3 course/_tools/generate_part_pages.py               # after editing 04-curriculum.md or adding a lesson
quarto preview course                                      # course site locally (Quarto 1.6.42, as in CI)
```

Never edit `course/part-*/index.qmd` or `course/_sidebar.yml` by hand: they are generated from `docs/context/04-curriculum.md`.

## Git

Commit messages: `type: Past-tense sentence` (for example `feat: Added hybrid search endpoint`). Feature work merges to `main` through pull requests.
