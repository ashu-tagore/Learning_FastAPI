# 06. Conventions

## Repository layout

```
.
├── README.md               what this repo is, how to start
├── CLAUDE.md               instructions for AI assistants
├── docs/context/           these documents
├── course/                 Quarto site source (lessons as .qmd)
├── atlas/                  reference implementation (uv project)
│   ├── src/atlas/          one package, grows by part
│   ├── migrations/         Alembic
│   └── tests/
├── web/                    ready-made web UI (no build step)
├── sandbox-images/         one Dockerfile per target repo
├── infra/                  Terraform
├── evals/                  evaluation cases, thresholds, hand grades (runner: `atlas eval`)
└── .github/workflows/      ci.yml, site.yml, later deploy.yml
```

Learners write their own code in their own repository, `my-atlas` (created in lesson 1.1), and compare with `atlas/` at the matching checkpoint tag.

## Code style

The code is written for human brains. A reader can hold about four things in working memory at once.

- **Readable conditionals.** Extract compound conditions into well-named variables.
  ```python
  is_open = issue.state == "open"
  is_beginner_friendly = "good first issue" in issue.labels
  if is_open and is_beginner_friendly:
      ...
  ```
- **Early returns** over nested `if`s; keep the happy path unindented.
- **Deep modules, not shallow ones.** A simple interface hiding real work. No `FooManagerFactory`, no layers that only forward calls. Add a layer when duplication or complexity demands it, not before.
- **Linear code over jumping between many tiny functions.** Split a function when it has two responsibilities, not because it is long.
- **Composition over inheritance.** Inheritance at most one level deep (for example Pydantic `PostBase` to `PostCreate`).
- **Minimal language features.** No metaclasses, clever decorators or dense comprehensions where a loop reads better.
- **Self-descriptive values.** `Literal["up", "remove"]`, not `0/1` flags; enums with readable values.
- **Comments explain why, not what.** Allowed: why this approach, why this tricky line, a short bird's-eye summary of a block. Not allowed: comments restating the next line.
- **No emojis** in code, comments, commit messages or lesson code samples.
- **Original code only.** Never copy code from other courses or projects; write it fresh.
- Python 3.13 for `atlas/`; type hints everywhere; `ruff` for lint and format; `mypy` for types.

## API rules

- Input is parsed at the edge into domain types (`Email`, `RepoFullName`, `IssueCursor`); routes don't re-check it. Request schemas use `REQUEST_BODY` (`extra="forbid"`).
- Routes raise `ApiError(status, code, message)`. Codes are a contract: never change one's meaning, add a new one instead. Every error, the framework's included, has the shape `{"error": {code, message, request_id, details?}}`.
- Logs go through `logging` (never `print`) as JSON lines via `atlas.logs`, and carry the request id. Never log passwords, tokens, keys, request or response bodies, or query strings.
- The user id comes from the token, never from the URL or body.

## Lesson template

Every lesson file is `course/part-P-<slug>/NN-<slug>.qmd`. After adding one, run `python3 course/_tools/generate_part_pages.py` to update the part page and sidebar (never edit those by hand):

```markdown
---
title: "4.8 Authentication with JWT"
description: "One sentence on what the learner can do after this lesson."
---

::: {.callout-note title="Analogy: <short name>"}
...intuition first...
:::

## Theory
## Diagram          (mermaid, when a picture beats words)
## In the project   (code with "why" comments; files touched)
## Common mistakes
## Check yourself   (3 questions; answers in collapsed callouts)
## Exercise         (guided; solution in a collapsed callout)
## Stretch          (harder; solution in a collapsed callout)
## What changed     (files, link to diff between checkpoints)
```

Review and practice lessons (the last lesson of Parts 1 to 8) use this template instead:

```markdown
## Recap            (the part's key ideas on one page, with links back)
## Mixed practice   (exercises from across the part, no step-by-step hints)
## Build it alone   (a small feature defined only by acceptance tests and a short spec)
## From earlier parts (quiz questions revisiting older parts, for spaced repetition)
## Self-check       (a rubric: what you should now be able to do without notes)
```

Writing rules for lessons:

- Plain, direct sentences. Define every term the first time it appears.
- Never use a concept a previous lesson didn't teach. If unavoidable, say "we'll cover this in X.Y" and keep it to one line.
- Every claim about a library, an API or the target project is verified against current docs or by running code.
- Show real output from running the code, not imagined output.

## Tests

- Every feature in `atlas/` has tests; CI must be green before merge.
- Tests run against a real Postgres (compose service in CI), never SQLite.
- Tests are independent: fixtures build their own data; order does not matter.
- Test the unhappy paths (401, 403, 404, 409, 422) and security properties (no secret ever in a response).
- LLM-dependent code is tested with a fake provider implementing the same interface; real-model checks live in `evals/`, not in unit tests.
- Fakes replace only what crosses the process boundary, and are as strict as the real service. Atlas's own code is never mocked.
- Every new test is seen failing once (red first, or break the code on purpose) before it's trusted.
- Coverage is reported in CI, not enforced; read the `Missing` column for blind spots. Async code needs `concurrency = ["greenlet", "thread"]` to be measured correctly.
- `tests/api_contract.json` pins paths, methods and status codes; refresh it with `UPDATE_CONTRACT=1` only for deliberate changes, and review the diff.

## Git

- Commit messages follow the existing history: `type: Past-tense sentence`, for example `feat: Added hybrid search endpoint`, `docs: Added curriculum`, `chore: Removed redundant code`, `fix: ...`, `test: ...`.
- One logical change per commit.
- Feature branches merge to `main` through pull requests. Context docs may go to `main` directly with the owner's approval.
- Never commit `.env`, keys or tokens. `.env.example` documents every variable with a fake value.

## Configuration

- All settings come from environment variables through one `Settings` class (pydantic-settings). Nothing reads `os.environ` directly. (Parts 1 to 3 predate lesson 4.2, so their checkpoints read `os.environ` in `cli.py`; from Checkpoint 4 everything goes through `atlas.config`.) `.env.example` documents every variable.
- Secrets have no defaults, so a missing secret fails at startup.

## Interaction with the target project

- Atlas never writes to GitHub. The GitHub token is read-only.
- Respect GitHub rate limits and use conditional requests.
- Re-read the target's `CONTRIBUTING.md` and `AGENTS.md` at each milestone that depends on them and update [02-product.md](02-product.md) if they changed.
