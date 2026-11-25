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
cd atlas && docker compose --profile app up -d --build     # the whole system from the production image (7.1)
cd atlas && docker build -t atlas .                        # the production image alone (7.1)
export TEST_DATABASE_URL=postgresql+psycopg://atlas:atlas-dev-password@localhost:5432/atlas_test
export TEST_REDIS_URL=redis://localhost:6379/15             # queue, cache, stream tests (Part 5 onward)
export TEST_S3_URL=http://localhost:8333                   # object storage tests (lesson 5.4; SeaweedFS from compose)
export TEST_SQS_URL=http://localhost:9324                  # SQS queue tests (lesson 7.5; ElasticMQ from compose)
cd atlas && uv sync && uv run pytest                       # reference tests (DB, Redis, S3 and SQS tests skip without their URLs)
cd atlas && uv run atlas init-db                           # run migrations (needs DATABASE_URL)
cd atlas && uv run alembic revision --autogenerate -m "..."  # new migration; review it, rename the revision to the next number
cd atlas && SECRET_KEY=dev-only-key-at-least-32-characters-long WEB_DIR=../web uv run uvicorn atlas.api.app:create_app --factory --reload --no-access-log  # API + web UI at /app; JSON request logs (lesson 4.15)
cd atlas && S3_ENDPOINT_URL=http://localhost:8333 AWS_ACCESS_KEY_ID=atlas AWS_SECRET_ACCESS_KEY=atlas-dev-secret-key uv run atlas worker  # background jobs (5.1, snapshots 5.4)
cd atlas && uv run atlas scheduler                         # queues syncs and snapshots (5.2); `atlas notifier` (5.6); `atlas digest` (5.7)
cd atlas && docker compose exec ollama ollama pull qwen3:1.7b      # local chat model (Part 6); also nomic-embed-text
cd atlas && uv run atlas llm "question" --stream             # ask the configured model (6.2); `atlas llm-usage` sums the cost log
cd atlas && uv run atlas fit --email ada@example.com --limit 5  # score issues against a skills profile (6.3)
cd atlas && uv run atlas find "stop training early" --repo pytorch/ignite --mode hybrid  # search (keyword|semantic|hybrid, 6.6); the worker's embed_repo job fills vectors (6.4)
cd atlas && uv run atlas embed --repo pytorch/ignite         # embed pending chunks without a worker (6.7)
cd atlas && uv run atlas ask "How do I stop training early?"  # answer with citations (6.7); POST /ask streams the same over SSE
cd atlas && uv run atlas eval --thresholds ../evals/thresholds.json --hand-grades ../evals/hand_grades.jsonl  # evaluation (6.8); --retrieval-only takes seconds
cd atlas && uv run ruff check . && uv run ruff format --check . && uv run mypy src tests
python3 course/_tools/generate_part_pages.py               # after editing 04-curriculum.md or adding a lesson
quarto preview course                                      # course site locally (Quarto 1.6.42, as in CI)
```

Never edit `course/part-*/index.qmd` or `course/_sidebar.yml` by hand: they are generated from `docs/context/04-curriculum.md`.

## Git

Commit messages: `type: Past-tense sentence` (for example `feat: Added hybrid search endpoint`). Feature work merges to `main` through pull requests.
