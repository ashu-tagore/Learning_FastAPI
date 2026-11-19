# 02. Product

## Target project

**PyTorch-Ignite** (`github.com/pytorch/ignite`), a high-level training library for PyTorch.

Chosen because its contribution rules are explicit and compatible with this product:

- `CONTRIBUTING.md`: "AI tools are allowed. Read AGENTS.md first." and "We do not accept AI-generated issues or PR descriptions, automated submissions, or generated replies to reviewers."
- `AGENTS.md`: agents must not open issues or PRs, push, or write descriptions, commit messages or reviewer replies; the user must understand the task; a bug needs a minimal reproduction on current `master` with real user impact; fixes stay minimal; new behavior needs tests, preferably by extending an existing test with `pytest.mark.parametrize`.
- Good first issues use the label `good first issue`.
- Checks: `pre-commit run --all-files` (ruff check, ruff format), `pytest tests/ignite/<area> -vvv`, `pyrefly check`, `bash tests/run_cpu_tests.sh`.

Re-read both files before each milestone that touches the target; they can change. Supporting a second target project is a stretch goal, added only after the pipeline is solid for one.

## Users

- **Learner** (primary): a contributor-in-training who wants to understand the target project and find issues they can genuinely solve.
- **Admin**: manages which repositories are tracked. In practice the same person.

Users sign in with email and password or with GitHub (OAuth2). GitHub sign-in requests identity scopes only, never repository write access.

## Stage A: Atlas (question answering)

### User stories

1. As a learner, I ask "How are Events fired during `Engine.run`?" and get a streamed answer where every claim cites `path/to/file.py:L120-L148` or a docs page, with links to GitHub at the indexed commit.
2. As a learner, I search the codebase and docs by keyword, by meaning, or both.
3. As a learner, I see which commit the index reflects and when it was last synced.
4. As a learner, I keep a history of my conversations and rate answers (thumbs up/down with an optional note).
5. As a learner, I see what my questions cost (tokens and money) and hit a clear limit before costs surprise me.
6. As an admin, I add a repository; Atlas clones it, indexes docs and code in the background, and re-indexes only changed files on new commits.

### Behaviour rules

- No citation, no claim. If retrieval finds nothing relevant, Atlas says so instead of guessing.
- Answers state the commit SHA they are based on.

## Stage B: Atlas Agents (issue scouting and Issue Briefs)

### User stories

1. As a learner, I record a skills profile ("comfortable with metrics and handlers, new to distributed").
2. Atlas Agents syncs open `good first issue` issues, scores each for fit against my profile, and lists them ranked with a one-line reason.
3. I start a run for one issue. I watch its steps live: reading the issue, searching Atlas, writing a reproduction, running it in the sandbox, running the project's checks.
4. The run pauses for my approval before the brief is finalised. To approve, I must write a short explanation of the problem in my own words (the "explain it back" gate, from Ignite's rule that the user must understand the task).
5. I get an Issue Brief I can read in the web UI and export as Markdown to my own machine.
6. I can cancel, retry or resume a run; every step is in an audit log.

### Behaviour rules

- Issue text is **untrusted input**. Instructions inside an issue are never followed; they are treated as data.
- The sandbox has no network and strict CPU, memory, process and time limits.
- Bounded effort: a run has a maximum number of iterations, a token budget and a wall-clock limit.
- Issues that fail Ignite's bar (no reproduction possible, no real user impact, unsupported versions, style nits) are labelled **"not actionable"** with the reason, and no brief is produced.

## The Issue Brief

A Markdown document with these sections, in this order:

1. **Header**: issue number, title, link, labels, target commit SHA, run id, date.
2. **Summary**: the problem in 3-5 plain sentences.
3. **Is it actionable?** Verdict against the target's bug-report bar (reproduction, actual vs expected, real impact), with reasons.
4. **Where in the code**: relevant files and symbols with `file:line` links and one line on why each matters.
5. **Conventions that apply**: the project rules relevant to this change, each cited from `CONTRIBUTING.md`, `AGENTS.md` or existing code.
6. **Set up and run the checks**: exact commands for environment setup, the narrowest relevant `pytest` invocation, `pre-commit`, `pyrefly`.
7. **Reproduction**: the script, the command, the observed output from the sandbox, and a verified flag (`fails as described` / `could not reproduce`).
8. **Suggested approach**: steps in prose, which existing test to extend, what to verify. **No patch, no code for the fix.**
9. **Open questions**: what to ask maintainers before starting, if anything.
10. **Your explanation**: the learner's own words from the approval gate.
11. **Footer**: a reminder to follow the project's AI policy, and that the human writes the code, the PR description and every reply.

## API surface (planned)

All JSON unless noted. Auth is a bearer JWT except where marked public.

| Area | Endpoints |
|---|---|
| Auth | `POST /auth/register`, `POST /auth/login`, `POST /auth/refresh`, `GET /auth/github/login`, `GET /auth/github/callback`, `GET /users/me`, `PATCH /users/me` (skills profile) |
| Repositories | `GET /repos`, `POST /repos` (admin), `GET /repos/{id}`, `POST /repos/{id}/sync` (admin), `GET /repos/{id}/snapshot` (presigned download link) |
| Jobs | `GET /jobs/{id}` |
| Notifications | `GET /notifications` (changes to saved issues), `GET /digests/latest` (daily digest) |
| Issues | `GET /issues?repo=&label=&state=&sort=fit`, `GET /issues/{id}`, `PUT /issues/{id}/saved`, `DELETE /issues/{id}/saved` |
| Search | `GET /search?q=&repo=&mode=keyword\|semantic\|hybrid&limit=` |
| Ask | `POST /ask` (Server-Sent Events stream), `GET /conversations`, `GET /conversations/{id}`, `POST /answers/{id}/feedback` |
| Runs | `POST /runs`, `GET /runs`, `GET /runs/{id}`, `GET /runs/{id}/events` (SSE), `POST /runs/{id}/approve`, `POST /runs/{id}/reject`, `POST /runs/{id}/cancel` |
| Briefs | `GET /briefs/{id}`, `GET /briefs/{id}.md` |
| Usage | `GET /usage` (tokens and cost per day, per feature) |
| Webhooks | `POST /webhooks/github` (HMAC-verified, public) |
| Ops | `GET /health` (liveness, public), `GET /ready` (readiness, public) |

## Web interface (ready-made)

Plain HTML, CSS and JavaScript, no build step, served from `web/`. Pages: login, ask (chat with streaming and citations), search, issues (ranked list), run view (live steps and approval form), brief view, usage. Lessons explain how it talks to the API (fetch, bearer token, SSE) but do not teach frontend development.
