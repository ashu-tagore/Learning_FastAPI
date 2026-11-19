# 05. Roadmap

How the work is sequenced. Each milestone produces lessons **and** reference code together, so the course never describes code that doesn't exist.

## Milestones

| # | Milestone | Delivers | Status |
|---|---|---|---|
| M0 | Foundation | Context docs, repo restructure, Quarto site skeleton on GitHub Pages, CI, `atlas/` package skeleton with tooling, lesson template | Done (site publishes once Pages is enabled) |
| M1 | Parts 0-1 | 15 lessons; `atlas` CLI v0 keyword search | Done (tag `checkpoint-01` pending: this environment cannot push tags) |
| M2 | Part 2 | 7 lessons; OS and network basics, GitHub client, concurrent issue fetch | Done (tag `checkpoint-02` pending: this environment cannot push tags) |
| M3 | Part 3 | 12 lessons; Postgres schema, query tuning, concurrency, SQL search | Done (tag `checkpoint-03` pending: this environment cannot push tags) |
| M4 | Part 4 | 16 lessons (12 ported from the original tutorial); GitHub sign-in; async API v1; web UI v1 | Done (tag `checkpoint-04` pending: this environment cannot push tags) |
| D1 | Part 4 depth pass | Part 4 grows to 20 lessons, failure-first and test-first; Part 7 designed in depth ([08-depth-pass.md](08-depth-pass.md)) | Part 4 done (20 lessons, 126 tests, 94% coverage); Part 7 design fixed, written in M7 |
| M5 | Part 5 | 7 lessons; worker, queue, webhooks, storage, cache, event streams | Not started |
| M6 | Part 6 | 9 lessons; providers, ingestion, hybrid search, `/ask`, evals | Not started |
| M7 | Part 7 | 14 lessons; Terraform, ECS deploy, CI/CD, database operations, observability, security, privacy, incident response | Not started |
| M8 | Part 8 | 10 lessons; agent runs, sandbox, Issue Briefs, web UI v2 | Not started |
| M9 | Part 9 | 6 lessons; distributed systems, design docs, outbox, unguided capstone, career material | Not started |

## Definition of done (every milestone)

- Every lesson in the part follows the template and renders on the site without warnings.
- Reference code for the part is in `atlas/`, with tests; CI is green.
- A checkpoint tag `checkpoint-NN` (part number, two digits) marks the state.
- This table is updated in the same pull request.
- Any changed decision is logged in `07-decisions.md`.

## M0 in detail

1. Context docs (this folder), `README.md`, `CLAUDE.md`. Pushed to `main`.
2. Tags preserving the past: `posts-api-v1` (the original app) and `posts-api-solution-v1` (the tutorial's reference solution).
3. Restructure: remove `app/` and `solutions/` from the working tree (their content returns, reshaped, in Part 4); add `course/`, `atlas/`, `web/`, `sandbox-images/`, `infra/`, `evals/`. (Learners keep their own code in a separate `my-atlas` repository; see D18.)
4. Quarto site: `course/_quarto.yml` with a docked left sidebar listing all parts and lessons, top navbar, right-hand "On this page", search, light and dark themes, code copy buttons; landing page; one fully written sample lesson (0.1) proving the template. Part overview pages and the sidebar are generated from `04-curriculum.md` by `course/_tools/generate_part_pages.py`; CI fails if they are stale.
5. GitHub Actions: `site.yml` renders Quarto and publishes to GitHub Pages; `ci.yml` runs ruff, mypy and pytest for `atlas/`.
6. `atlas/` uv project: Python 3.13, ruff, mypy, pytest, pre-commit config, empty package with a passing smoke test.

**Owner action needed for M0:** in the repo's Settings, Pages, set Source to "GitHub Actions". Rename the repository to "Zero to Production in FastAPI" (suggested slug `zero-to-production-fastapi`).

## Work order rationale

Course order is build order. Each lesson can only use what earlier lessons taught, and the reference code at a checkpoint is exactly what a learner could have written by then. Production topics are not front-loaded because a truly-zero learner can't use them yet; instead the learner is told to start contributing to PyTorch-Ignite by hand in parallel.

## Branching

- `main` holds published, reviewed work. Context-doc changes may go straight to `main` with the owner's approval.
- Milestone work happens on a feature branch and merges to `main` through a pull request.
